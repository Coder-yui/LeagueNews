import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance
from rapidocr import RapidOCR

from app.core.config import settings


class OCRProcessingError(RuntimeError):
    pass


@dataclass(slots=True)
class OCRResult:
    raw_text: str
    lines: list[dict[str, Any]]
    confidence: float
    sha256: str
    width: int
    height: int
    processed_width: int
    processed_height: int
    engine: str


@lru_cache
def get_ocr_engine() -> RapidOCR:
    return RapidOCR()


def resolve_public_media_path(storage_path: str) -> Path:
    path = urlparse(storage_path).path
    media_root = settings.resolved_media_root.resolve()
    api_prefixes = {
        "/api/v1/media-assets/files/": "private",
        "/api/v1/media-assets/published/": "published",
    }
    candidate: Path | None = None
    for prefix, visibility in api_prefixes.items():
        if path.startswith(prefix):
            parts = Path(path[len(prefix) :]).parts
            if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
                raise OCRProcessingError(f"invalid media path: {storage_path}")
            candidate = media_root / visibility / parts[0] / parts[1]
            break
    if candidate is None and path.startswith("/media/"):
        candidate = media_root / path.removeprefix("/media/")
    if candidate is None:
        candidate = media_root / path.lstrip("/")
    candidate = candidate.resolve()
    if not candidate.is_relative_to(media_root):
        raise OCRProcessingError("media path escapes the public directory")
    if not candidate.is_file():
        raise OCRProcessingError(f"media file not found: {storage_path}")
    return candidate


def run_ocr(
    storage_path: str,
    parameters: dict[str, Any] | None = None,
) -> OCRResult:
    image_path = resolve_public_media_path(storage_path)
    image_bytes = image_path.read_bytes()
    with Image.open(image_path) as image:
        width, height = image.size
        processed = prepare_image(image, parameters or {})

    call_options = _rapidocr_call_options(parameters or {})
    result = get_ocr_engine()(np.asarray(processed), **call_options)
    texts = list(result.txts) if result.txts is not None else []
    scores = [float(score) for score in result.scores] if result.scores is not None else []
    boxes = (
        [box.tolist() if hasattr(box, "tolist") else box for box in result.boxes]
        if result.boxes is not None
        else []
    )
    if not texts:
        raise OCRProcessingError(f"OCR returned no text for {storage_path}")

    lines = [
        {
            "index": index,
            "text": text,
            "confidence": scores[index] if index < len(scores) else None,
            "box": boxes[index] if index < len(boxes) else None,
        }
        for index, text in enumerate(texts)
    ]
    return OCRResult(
        raw_text="\n".join(texts),
        lines=lines,
        confidence=sum(scores) / len(scores) if scores else 0.0,
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        width=width,
        height=height,
        processed_width=processed.width,
        processed_height=processed.height,
        engine=f"rapidocr-{version('rapidocr')}",
    )


def render_ocr_overlay(
    storage_path: str,
    result: OCRResult,
    parameters: dict[str, Any],
    output_path: Path,
) -> None:
    source_path = resolve_public_media_path(storage_path)
    with Image.open(source_path) as image:
        overlay = prepare_image(image, parameters).convert("RGB")
    draw = ImageDraw.Draw(overlay)
    for line in result.lines:
        box = line.get("box")
        if not isinstance(box, list) or len(box) < 4:
            continue
        points = [
            (float(point[0]), float(point[1]))
            for point in box
            if isinstance(point, list) and len(point) >= 2
        ]
        if len(points) < 4:
            continue
        draw.line(points + [points[0]], fill=(255, 55, 55), width=max(2, int(overlay.width / 700)))
        x, y = points[0]
        draw.text((x + 2, max(0, y - 12)), str(line["index"]), fill=(255, 30, 30))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(output_path, format="JPEG", quality=92)


def prepare_image(image: Image.Image, parameters: dict[str, Any]) -> Image.Image:
    prepared = image.convert("RGB")
    scale = float(parameters.get("scale", 1.0))
    if scale != 1.0:
        prepared = prepared.resize(
            (round(prepared.width * scale), round(prepared.height * scale)),
            Image.Resampling.LANCZOS,
        )
    if bool(parameters.get("grayscale", False)):
        prepared = prepared.convert("L").convert("RGB")
    contrast = float(parameters.get("contrast", 1.0))
    if contrast != 1.0:
        prepared = ImageEnhance.Contrast(prepared).enhance(contrast)
    sharpness = float(parameters.get("sharpness", 1.0))
    if sharpness != 1.0:
        prepared = ImageEnhance.Sharpness(prepared).enhance(sharpness)
    return prepared


def _rapidocr_call_options(parameters: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {"use_cls": bool(parameters.get("use_cls", True))}
    for name in ("text_score", "box_thresh", "unclip_ratio"):
        value = parameters.get(name)
        if value is not None:
            options[name] = float(value)
    return options
