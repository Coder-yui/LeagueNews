import asyncio
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_blocks import text_from_content_blocks
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.ocr_lab import OCRProfile
from app.models.raw_item import RawItem
from app.services.llm import PatchPreviewExtraction
from app.services.media_ocr import run_ocr
from app.services.patch_table import ensure_table_confidence, parse_patch_table

PATCH_TASK = "patch_preview"
PATCH_SCHEMA_VERSION = "v2"
PATCH_OCR_REVIEW_SCHEMA_VERSION = "v2-ocr-review"
DETERMINISTIC_STRUCTURE_VERSION = "patch-preview-deterministic-v1"
PATCH_SECTION_DEFINITIONS = {
    "champion_buff": ("英雄增强", "champion"),
    "champion_nerf": ("英雄削弱", "champion"),
    "champion_adjustment": ("英雄调整", "champion"),
    "system_buff": ("系统增强", "system"),
    "system_nerf": ("系统削弱", "system"),
    "system_adjustment": ("系统调整", "system"),
}


def is_patch_preview(raw_item: RawItem) -> bool:
    if not raw_item.media_assets:
        return False
    text = (
        f"{raw_item.display_title or ''}\n"
        f"{text_from_content_blocks(raw_item.content_blocks)}"
    ).casefold()
    preview_language = any(
        marker in text
        for marker in ("preview", "micropatch", "hotfix")
    )
    source = raw_item.source
    is_phroxzon = bool(
        source
        and source.connector_type == "x_twitter"
        and (source.external_key or "").casefold() == "riotphroxzon"
    )
    return preview_language and is_phroxzon


async def extract_patch_preview(
    db: Session,
    *,
    raw_item: RawItem,
    media_asset: MediaAsset,
    glossary: list[dict[str, object]] | None = None,
    force: bool = False,
    structure: bool = True,
    enforce_confidence: bool = True,
) -> MediaExtraction:
    schema_version = PATCH_SCHEMA_VERSION if structure else PATCH_OCR_REVIEW_SCHEMA_VERSION
    if not force:
        existing = db.scalar(
            select(MediaExtraction).where(
                MediaExtraction.media_asset_id == media_asset.id,
                MediaExtraction.task_type == PATCH_TASK,
                MediaExtraction.schema_version == schema_version,
                MediaExtraction.status == "processed",
            ).order_by(MediaExtraction.created_at.desc()).limit(1)
        )
        if existing:
            return existing
    if not media_asset.storage_path:
        raise RuntimeError(f"media asset {media_asset.id} has no local storage_path")

    active_profile = db.scalar(
        select(OCRProfile)
        .where(OCRProfile.is_active.is_(True))
        .order_by(OCRProfile.updated_at.desc())
        .limit(1)
    )
    ocr_parameters = dict(active_profile.parameters) if active_profile else {}
    ocr = await asyncio.to_thread(run_ocr, media_asset.storage_path, ocr_parameters)
    table = await asyncio.to_thread(
        parse_patch_table,
        media_asset.storage_path,
        ocr,
        ocr_parameters,
        title_hint=raw_item.display_title,
    )
    if enforce_confidence:
        ensure_table_confidence(table)
    structured_data: dict[str, object] = {}
    if structure:
        structured = build_patch_preview(
            title=raw_item.display_title,
            table_data=table.model_dump(),
        )
        structured_data = structured.model_dump(mode="json")
    media_asset.sha256 = ocr.sha256
    media_asset.width = ocr.width
    media_asset.height = ocr.height
    media_asset.ocr_text = ocr.raw_text
    extraction = MediaExtraction(
        media_asset_id=media_asset.id,
        task_type=PATCH_TASK,
        provider=(
            f"patch-table+rapidocr+{DETERMINISTIC_STRUCTURE_VERSION}"
            if structure
            else "patch-table+rapidocr"
        ),
        ocr_engine=ocr.engine,
        structuring_model=DETERMINISTIC_STRUCTURE_VERSION if structure else "",
        schema_version=schema_version,
        status="processed",
        raw_ocr_text=ocr.raw_text,
        ocr_lines=ocr.lines,
        structured_data=structured_data,
        processing_config={
            "ocr_profile_id": active_profile.id if active_profile else None,
            "ocr_profile_name": active_profile.name if active_profile else "rapidocr-default",
            "parameters": ocr_parameters,
            "processed_width": ocr.processed_width,
            "processed_height": ocr.processed_height,
            "table_data": table.model_dump(),
            "structure_confidence": table.structure_confidence,
        },
        confidence=min(ocr.confidence, table.structure_confidence),
    )
    db.add(extraction)
    db.flush()
    return extraction


async def understand_patch_media(
    db: Session,
    raw_item: RawItem,
    *,
    glossary: list[dict[str, object]] | None = None,
    force: bool = False,
    structure: bool = True,
    enforce_confidence: bool = True,
) -> list[MediaExtraction]:
    if not is_patch_preview(raw_item):
        return []
    return [
        await extract_patch_preview(
            db,
            raw_item=raw_item,
            media_asset=media_asset,
            glossary=glossary,
            force=force,
            structure=structure,
            enforce_confidence=enforce_confidence,
        )
        for media_asset in raw_item.media_assets
        if media_asset.mime_type is None or media_asset.mime_type.startswith("image/")
    ]


def structure_patch_extraction(
    extraction: MediaExtraction,
    *,
    title: str | None,
) -> MediaExtraction:
    table_data = extraction.processing_config.get("table_data")
    if not isinstance(table_data, dict):
        raise ValueError(f"media extraction {extraction.id} has no patch table data")
    structured = build_patch_preview(
        title=title,
        table_data=table_data,
    )
    extraction.structured_data = structured.model_dump(mode="json")
    extraction.structuring_model = DETERMINISTIC_STRUCTURE_VERSION
    extraction.provider = f"patch-table+rapidocr+{DETERMINISTIC_STRUCTURE_VERSION}"
    extraction.schema_version = PATCH_SCHEMA_VERSION
    return extraction


def build_patch_preview(
    *,
    title: str | None,
    table_data: dict[str, object],
) -> PatchPreviewExtraction:
    raw_sections = table_data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("版本图片没有可结构化的分组")
    sections: list[dict[str, object]] = []
    for section_index, raw_section in enumerate(raw_sections):
        if not isinstance(raw_section, dict):
            raise ValueError(f"版本图片分组 {section_index + 1} 格式无效")
        section_type = _resolve_patch_section_type(raw_section)
        label, target_type = PATCH_SECTION_DEFINITIONS[section_type]
        raw_records = raw_section.get("records")
        if not isinstance(raw_records, list) or not raw_records:
            raise ValueError(f"{label}分组没有对象")
        entries: list[dict[str, object]] = []
        for record_index, raw_record in enumerate(raw_records):
            if not isinstance(raw_record, dict):
                raise ValueError(f"{label}第 {record_index + 1} 项格式无效")
            target = str(raw_record.get("target") or "").strip()
            if not target:
                raise ValueError(f"{label}第 {record_index + 1} 项缺少对象名称")
            raw_changes = raw_record.get("raw_changes")
            if not isinstance(raw_changes, list):
                raw_changes = []
            changes = [
                str(raw_change).strip()
                for raw_change in raw_changes
                if str(raw_change).strip()
            ]
            entries.append(
                {
                    "target": target,
                    "target_type": target_type,
                    "changes": changes,
                }
            )
        sections.append(
            {
                "section_type": section_type,
                "label": label,
                "entries": entries,
            }
        )
    title_lines = [
        line.strip()
        for line in (title or "Patch Preview").replace("\u00a0", " ").splitlines()
        if line.strip()
    ]
    title_text = title_lines[0] if title_lines else "Patch Preview"
    patch_match = re.search(r"\b(\d{1,2}\.\d{1,2})\b", title_text)
    return PatchPreviewExtraction.model_validate(
        {
            "document_type": "patch_preview",
            "preview_kind": table_data.get("preview_kind", "preview"),
            "patch": patch_match.group(1) if patch_match else None,
            "title": title_text,
            "sections": sections,
            "warnings": list(table_data.get("warnings") or []),
        }
    )


def _resolve_patch_section_type(section: dict[str, object]) -> str:
    section_type = str(section.get("section_type") or "")
    if section_type in PATCH_SECTION_DEFINITIONS:
        return section_type
    label = re.sub(r"[^A-Z]", "", str(section.get("label") or "").upper())
    scope = "champion" if "CHAMPION" in label else "system" if "SYSTEM" in label else ""
    direction = (
        "adjustment"
        if "ADJUST" in label
        else "buff"
        if "BUFF" in label
        else "nerf"
        if "NERF" in label
        else ""
    )
    inferred = f"{scope}_{direction}"
    if inferred not in PATCH_SECTION_DEFINITIONS:
        raise ValueError(
            "版本图片只允许 champion/system 的 buff、nerf、adjustment 六类分组，"
            f"当前无法识别：{section.get('label') or section_type}"
        )
    return inferred
def extraction_context(extractions: list[MediaExtraction]) -> list[dict[str, Any]]:
    return [extraction.structured_data for extraction in extractions]
