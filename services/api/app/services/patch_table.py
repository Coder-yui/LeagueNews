from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.services.media_ocr import (
    OCRProcessingError,
    OCRResult,
    prepare_image,
    resolve_public_media_path,
)

PatchPreviewKind = Literal["preview", "full_preview"]

SECTION_TYPES = {
    ("CHAMPION", "BUFF"): "champion_buff",
    ("CHAMPION", "NERF"): "champion_nerf",
    ("CHAMPION", "ADJUST"): "champion_adjustment",
    ("SYSTEM", "BUFF"): "system_buff",
    ("SYSTEM", "NERF"): "system_nerf",
    ("SYSTEM", "ADJUST"): "system_adjustment",
    ("ITEM", "BUFF"): "item_buff",
    ("ITEM", "NERF"): "item_nerf",
    ("ITEM", "ADJUST"): "item_adjustment",
    ("RUNE", "BUFF"): "rune_buff",
    ("RUNE", "NERF"): "rune_nerf",
    ("RUNE", "ADJUST"): "rune_adjustment",
}


@dataclass(slots=True)
class PatchTableResult:
    preview_kind: PatchPreviewKind
    divider_x: int | None
    structure_confidence: float
    sections: list[dict[str, Any]]
    warnings: list[str]
    boundaries: list[int]

    def model_dump(self) -> dict[str, Any]:
        return {
            "preview_kind": self.preview_kind,
            "divider_x": self.divider_x,
            "structure_confidence": self.structure_confidence,
            "sections": self.sections,
            "warnings": self.warnings,
            "boundaries": self.boundaries,
        }


def parse_patch_table(
    storage_path: str,
    ocr: OCRResult,
    parameters: dict[str, Any] | None = None,
    *,
    title_hint: str | None = None,
) -> PatchTableResult:
    config = parameters or {}
    image_path = resolve_public_media_path(storage_path)
    with Image.open(image_path) as source:
        prepared = prepare_image(source, config)
    gray = cv2.cvtColor(np.asarray(prepared), cv2.COLOR_RGB2GRAY)
    height, width = gray.shape

    divider_x, divider_confidence = _infer_divider(ocr.lines, width, config)
    expects_full = "full preview" in (title_hint or "").casefold()
    preview_kind: PatchPreviewKind = (
        "full_preview" if divider_x is not None or expects_full else "preview"
    )
    warnings: list[str] = []
    if preview_kind == "full_preview" and divider_x is None:
        warnings.append("标题表明这是 Full Preview，但未检测到稳定的左右列分隔")

    left_edge = divider_x if divider_x is not None else width
    boundaries = _detect_left_boundaries(gray, left_edge, config)
    section_lines = _section_lines(ocr.lines)
    records = _build_records(
        ocr.lines,
        boundaries,
        divider_x,
        width,
        height,
        section_lines,
        preview_kind,
    )
    sections = _group_sections(records)

    if not records:
        warnings.append("未从表格左列提取到任何目标单元格")
    if preview_kind == "full_preview":
        missing_changes = [record["target"] for record in records if not record["raw_changes"]]
        if missing_changes:
            warnings.append(
                "以下 Full Preview 目标没有匹配到右侧改动："
                + "、".join(missing_changes[:10])
            )

    boundary_confidence = min(1.0, len(boundaries) / max(4, len(records) + 1))
    section_confidence = min(1.0, len(sections) / 3) if sections else 0.0
    if preview_kind == "full_preview":
        paired = sum(bool(record["raw_changes"]) for record in records)
        pairing_confidence = paired / len(records) if records else 0.0
        structure_confidence = (
            0.35 * divider_confidence
            + 0.25 * boundary_confidence
            + 0.3 * pairing_confidence
            + 0.1 * section_confidence
        )
    else:
        structure_confidence = (
            0.55 * boundary_confidence
            + 0.35 * section_confidence
            + (0.1 if records else 0.0)
        )
    if expects_full and divider_x is None:
        structure_confidence = min(structure_confidence, 0.35)

    return PatchTableResult(
        preview_kind=preview_kind,
        divider_x=divider_x,
        structure_confidence=round(structure_confidence, 6),
        sections=sections,
        warnings=warnings,
        boundaries=boundaries,
    )


def render_patch_table_overlay(
    storage_path: str,
    table: PatchTableResult,
    parameters: dict[str, Any],
    output_path: Path,
) -> None:
    source_path = resolve_public_media_path(storage_path)
    with Image.open(source_path) as source:
        overlay = prepare_image(source, parameters).convert("RGB")
    draw = ImageDraw.Draw(overlay)
    width, height = overlay.size
    line_width = max(2, int(width / 650))

    if table.divider_x is not None:
        draw.line(
            [(table.divider_x, 0), (table.divider_x, height)],
            fill=(40, 210, 255),
            width=line_width,
        )
    for boundary in table.boundaries:
        draw.line([(0, boundary), (width, boundary)], fill=(255, 185, 35), width=1)
    record_index = 0
    for section in table.sections:
        for record in section["records"]:
            x0, y0, x1, y1 = record["bbox"]
            draw.rectangle((x0, y0, x1, y1), outline=(55, 235, 125), width=line_width)
            draw.text(
                (4, min(height - 12, y0 + 2)),
                str(record_index),
                fill=(55, 235, 125),
            )
            record_index += 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(output_path, format="JPEG", quality=92)


def _infer_divider(
    lines: list[dict[str, Any]],
    width: int,
    parameters: dict[str, Any],
) -> tuple[int | None, float]:
    override = parameters.get("divider_x_ratio")
    if override is not None:
        return round(float(override) * width), 1.0

    minimum = width * 0.12
    maximum = width * 0.35
    candidates: list[float] = []
    for line in lines:
        if _section_type(str(line.get("text", ""))) is not None:
            continue
        bounds = _box_bounds(line.get("box"))
        if bounds is None:
            continue
        x0 = bounds[0]
        if minimum <= x0 <= maximum:
            candidates.append(x0)
    if not candidates:
        return None, 0.0

    tolerance = max(4.0, width * 0.008)
    best_cluster: list[float] = []
    for center in candidates:
        cluster = [value for value in candidates if abs(value - center) <= tolerance]
        if len(cluster) > len(best_cluster) or (
            len(cluster) == len(best_cluster)
            and cluster
            and np.median(cluster) < np.median(best_cluster)
        ):
            best_cluster = cluster
    minimum_support = max(3, round(len(lines) * 0.07))
    if len(best_cluster) < minimum_support:
        return None, len(best_cluster) / minimum_support
    return round(float(np.median(best_cluster))), min(1.0, len(best_cluster) / 8)


def _detect_left_boundaries(
    gray: np.ndarray,
    left_edge: int,
    parameters: dict[str, Any],
) -> list[int]:
    height, width = gray.shape
    margin = max(2, round(width * 0.003))
    region = gray[:, margin : max(margin + 1, left_edge - margin)]
    brightness = int(parameters.get("line_brightness", 105))
    coverage = float(parameters.get("line_coverage", 0.82))
    row_scores = (region >= brightness).mean(axis=1)
    candidate_rows = np.flatnonzero(row_scores >= coverage)
    groups: list[list[int]] = []
    for raw_y in candidate_rows:
        y = int(raw_y)
        if not groups or y > groups[-1][-1] + 1:
            groups.append([y])
        else:
            groups[-1].append(y)
    boundaries = [
        max(group, key=lambda y: row_scores[y])
        for group in groups
        if group[-1] - group[0] <= max(8, round(height * 0.02))
    ]
    minimum_gap = max(5, round(height * 0.006))
    return [
        boundary
        for index, boundary in enumerate(boundaries)
        if index == 0 or boundary - boundaries[index - 1] >= minimum_gap
    ]


def _section_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = []
    for line in lines:
        section_type = _section_type(str(line.get("text", "")))
        bounds = _box_bounds(line.get("box"))
        if section_type and bounds:
            sections.append(
                {
                    "section_type": section_type,
                    "section_label": str(line["text"]),
                    "center_y": (bounds[1] + bounds[3]) / 2,
                    "bounds": bounds,
                }
            )
    return sorted(sections, key=lambda section: section["center_y"])


def _build_records(
    lines: list[dict[str, Any]],
    boundaries: list[int],
    divider_x: int | None,
    width: int,
    height: int,
    section_lines: list[dict[str, Any]],
    preview_kind: PatchPreviewKind,
) -> list[dict[str, Any]]:
    if len(boundaries) < 2:
        return []
    records: list[dict[str, Any]] = []
    divider_tolerance = max(4, round(width * 0.008))
    indexed_lines = [
        (line, bounds)
        for line in lines
        if (bounds := _box_bounds(line.get("box"))) is not None
        and _section_type(str(line.get("text", ""))) is None
        and not _is_section_decoration(line, bounds, section_lines)
    ]

    for y0, y1 in zip(boundaries, boundaries[1:], strict=False):
        if y1 - y0 < max(7, round(height * 0.008)):
            continue
        midpoint = (y0 + y1) / 2
        current_section = _latest_section(section_lines, midpoint)
        if current_section is None:
            continue
        cell_lines = [
            (line, bounds)
            for line, bounds in indexed_lines
            if y0 <= (bounds[1] + bounds[3]) / 2 < y1
        ]
        if divider_x is None:
            target_lines = cell_lines
            change_lines: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
        else:
            target_lines = [
                item for item in cell_lines if item[1][0] < divider_x - divider_tolerance
            ]
            change_lines = [
                item for item in cell_lines if item[1][0] >= divider_x - divider_tolerance
            ]
        target = _join_target_lines(target_lines)
        if not target:
            continue
        raw_changes = (
            _group_visual_lines(change_lines, height)
            if preview_kind == "full_preview"
            else []
        )
        confidence_values = [
            float(line["confidence"])
            for line, _ in target_lines + change_lines
            if line.get("confidence") is not None
        ]
        records.append(
            {
                "section_type": current_section["section_type"],
                "section_label": current_section["section_label"],
                "target": target,
                "raw_changes": raw_changes,
                "bbox": [0, y0, width - 1, y1],
                "ocr_confidence": (
                    sum(confidence_values) / len(confidence_values)
                    if confidence_values
                    else 0.0
                ),
            }
        )
    return records


def _group_sections(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for record in records:
        if (
            not sections
            or sections[-1]["section_type"] != record["section_type"]
            or sections[-1]["label"] != record["section_label"]
        ):
            sections.append(
                {
                    "section_type": record["section_type"],
                    "label": record["section_label"],
                    "records": [],
                }
            )
        clean_record = dict(record)
        clean_record.pop("section_type")
        clean_record.pop("section_label")
        sections[-1]["records"].append(clean_record)
    return sections


def _latest_section(
    sections: list[dict[str, Any]],
    center_y: float,
) -> dict[str, Any] | None:
    latest = None
    for section in sections:
        if section["center_y"] >= center_y:
            break
        latest = section
    return latest


def _is_section_decoration(
    line: dict[str, Any],
    bounds: tuple[float, float, float, float],
    sections: list[dict[str, Any]],
) -> bool:
    text = str(line.get("text", "")).strip()
    if len(text) > 3:
        return False
    x0, y0, x1, y1 = bounds
    width = x1 - x0
    height = max(1.0, y1 - y0)
    for section in sections:
        section_x0, section_y0, _, section_y1 = section["bounds"]
        section_height = max(1.0, section_y1 - section_y0)
        vertical_overlap = max(0.0, min(y1, section_y1) - max(y0, section_y0))
        overlap_ratio = vertical_overlap / min(height, section_height)
        horizontal_gap = section_x0 - x1
        if (
            x0 < section_x0
            and width <= section_height * 1.5
            and overlap_ratio >= 0.55
            and -section_height * 0.25 <= horizontal_gap <= section_height
        ):
            return True
    return False


def _section_type(text: str) -> str | None:
    normalized = " ".join(text.upper().replace("’", "'").split())
    for markers, section_type in SECTION_TYPES.items():
        if all(marker in normalized for marker in markers):
            return section_type
    return None


def _join_target_lines(
    lines: list[tuple[dict[str, Any], tuple[float, float, float, float]]],
) -> str:
    ordered = sorted(lines, key=lambda item: (item[1][1], item[1][0]))
    return " ".join(str(line["text"]).strip() for line, _ in ordered if str(line["text"]).strip())


def _group_visual_lines(
    lines: list[tuple[dict[str, Any], tuple[float, float, float, float]]],
    height: int,
) -> list[str]:
    ordered = sorted(lines, key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0]))
    groups: list[list[tuple[dict[str, Any], tuple[float, float, float, float]]]] = []
    tolerance = max(4.0, height * 0.004)
    centers: list[float] = []
    for item in ordered:
        center = (item[1][1] + item[1][3]) / 2
        if not groups or abs(center - centers[-1]) > tolerance:
            groups.append([item])
            centers.append(center)
        else:
            groups[-1].append(item)
            centers[-1] = sum(
                (entry[1][1] + entry[1][3]) / 2 for entry in groups[-1]
            ) / len(groups[-1])
    return [
        "  ".join(
            str(line["text"]).strip()
            for line, _ in sorted(group, key=lambda item: item[1][0])
            if str(line["text"]).strip()
        )
        for group in groups
    ]


def _box_bounds(box: object) -> tuple[float, float, float, float] | None:
    if not isinstance(box, list) or len(box) < 4:
        return None
    points = [
        point
        for point in box
        if isinstance(point, list)
        and len(point) >= 2
        and isinstance(point[0], (int, float))
        and isinstance(point[1], (int, float))
    ]
    if len(points) < 4:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def ensure_table_confidence(
    table: PatchTableResult,
    *,
    minimum: float = 0.65,
) -> None:
    if table.structure_confidence < minimum:
        raise OCRProcessingError(
            "版本图片表格结构置信度过低："
            f"{table.structure_confidence:.2%} < {minimum:.0%}。"
            "请先在 OCR 测试台调整结构参数并人工确认。"
        )
