from pathlib import Path

from PIL import Image, ImageDraw

from app.services.media_ocr import OCRResult
from app.services.patch_table import parse_patch_table


def _line(
    text: str,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    confidence: float = 0.99,
) -> dict[str, object]:
    return {
        "text": text,
        "confidence": confidence,
        "box": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
    }


def test_merged_left_cell_owns_all_right_side_change_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image_path = tmp_path / "table.png"
    image = Image.new("RGB", (400, 300), (55, 55, 55))
    draw = ImageDraw.Draw(image)
    for y in (40, 80, 240, 280):
        draw.line((0, y, 399, y), fill=(180, 180, 180), width=2)
    for y in (120, 160, 200):
        draw.line((80, y, 399, y), fill=(180, 180, 180), width=2)
    image.save(image_path)
    monkeypatch.setattr(
        "app.services.patch_table.resolve_public_media_path",
        lambda _: image_path,
    )

    lines = [
        _line("1", 4, 21, 28, 35, 0.8),
        _line("CHAMPION ADJUSTMENTS", 30, 20, 196, 36),
        _line("Alpha", 4, 50, 50, 70),
        _line("A change", 82, 50, 180, 70),
        _line("Bel'Veth", 4, 145, 65, 165),
        _line("Base Stats", 82, 90, 170, 110),
        _line("Q change", 82, 130, 180, 150),
        _line("R change", 82, 210, 180, 230),
    ]
    ocr = OCRResult(
        raw_text="\n".join(str(line["text"]) for line in lines),
        lines=lines,
        confidence=0.99,
        sha256="test",
        width=400,
        height=300,
        processed_width=400,
        processed_height=300,
        engine="test",
    )

    result = parse_patch_table(
        "/ignored.png",
        ocr,
        {"line_brightness": 105, "line_coverage": 0.82},
        title_hint="Patch Full Preview",
    )

    records = result.sections[0]["records"]
    assert result.preview_kind == "full_preview"
    assert result.divider_x == 82
    assert len(records) == 2
    assert records[0]["target"] == "Alpha"
    assert records[0]["raw_changes"] == ["A change"]
    assert records[1]["target"] == "Bel'Veth"
    assert records[1]["raw_changes"] == ["Base Stats", "Q change", "R change"]
    assert result.structure_confidence >= 0.65
