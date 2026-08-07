import re
from dataclasses import asdict, dataclass

from app.content_blocks import text_from_content_blocks
from app.models.raw_item import RawItem

_MEANINGFUL_CHARACTER = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
MIN_AUTOMATIC_TEXT_CHARACTERS = 12


@dataclass(frozen=True, slots=True)
class EvidenceGate:
    decision: str
    requires_manual_review: bool
    reason_code: str
    evidence_sources: tuple[str, ...]
    meaningful_text_characters: int
    designer_patch_extraction_count: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["evidence_sources"] = list(self.evidence_sources)
        return value


def evaluate_evidence_gate(
    raw_item: RawItem,
    *,
    designer_patch_images: bool,
    designer_patch_extraction_count: int = 0,
) -> EvidenceGate:
    # display_title may synthesize an account name and duplicate the first body
    # line. Only source-authored title/body text may satisfy the evidence gate.
    title = (raw_item.native_title or "").strip()
    source_text = text_from_content_blocks(raw_item.content_blocks).strip()
    combined = f"{title}\n{source_text}"
    meaningful_count = len(_MEANINGFUL_CHARACTER.findall(combined))
    evidence_sources = []
    if title:
        evidence_sources.append("source_title")
    if source_text:
        evidence_sources.append("source_text")
    if designer_patch_extraction_count:
        evidence_sources.append("designer_patch_changes")
    has_usable_text = meaningful_count >= MIN_AUTOMATIC_TEXT_CHARACTERS
    if not has_usable_text and not designer_patch_images:
        return EvidenceGate(
            decision="insufficient_evidence",
            requires_manual_review=False,
            reason_code="source_text_too_short",
            evidence_sources=tuple(evidence_sources),
            meaningful_text_characters=meaningful_count,
            designer_patch_extraction_count=designer_patch_extraction_count,
            reason="消息可用正文不足，无法支撑事实抽取",
        )
    return EvidenceGate(
        decision="process",
        requires_manual_review=False,
        reason_code="usable_source_evidence",
        evidence_sources=tuple(evidence_sources),
        meaningful_text_characters=meaningful_count,
        designer_patch_extraction_count=designer_patch_extraction_count,
        reason=(
            "已有确认的设计师版本改动结构"
            if designer_patch_extraction_count
            else (
                "进入设计师版本改动图片提取"
                if designer_patch_images
                else "原文文本足以支撑后续处理"
            )
        ),
    )
