import json
from dataclasses import dataclass
from typing import Any

from app.content_blocks import TEXT_BLOCK_TYPES, text_from_content_blocks
from app.core.config import settings
from app.models.media_extraction import MediaExtraction
from app.models.raw_item import RawItem
from app.services.llm import LLMClient

TRANSLATION_CHUNK_MAX_CHARS = 12_000
TRANSLATION_CHUNK_MAX_BLOCKS = 60
TRANSLATION_CONTEXT_MAX_CHARS = 4_000


@dataclass(slots=True)
class TranslationData:
    source_language: str
    target_language: str
    translated_title: str
    translated_text: str
    translated_content_blocks: list[dict[str, Any]]
    translated_media_extractions: list[dict[str, object]]
    translation_status: str
    translation_model: str | None


def detect_language(text: str) -> str:
    visible_chars = [char for char in text if not char.isspace()]
    if not visible_chars:
        return "unknown"
    cjk_chars = sum("\u4e00" <= char <= "\u9fff" for char in visible_chars)
    return "zh-CN" if cjk_chars / len(visible_chars) >= 0.1 else "en"


def _chunk_text_blocks(
    text_blocks: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    if not text_blocks:
        return [[]]
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_chars = 0
    for block in text_blocks:
        block_chars = len(str(block.get("text") or "")) + 80
        if current and (
            len(current) >= TRANSLATION_CHUNK_MAX_BLOCKS
            or current_chars + block_chars > TRANSLATION_CHUNK_MAX_CHARS
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(block)
        current_chars += block_chars
    if current:
        chunks.append(current)
    return chunks


def _document_outline(text_blocks: list[dict[str, object]]) -> str:
    headings = [
        str(block.get("text") or "").strip()
        for block in text_blocks
        if block.get("type") == "heading" and str(block.get("text") or "").strip()
    ]
    return "\n".join(headings)[:TRANSLATION_CONTEXT_MAX_CHARS]


def _neighbor_text(blocks: list[dict[str, object]], *, from_end: bool) -> str:
    selected = blocks[-2:] if from_end else blocks[:2]
    return "\n".join(str(block.get("text") or "") for block in selected)[
        : TRANSLATION_CONTEXT_MAX_CHARS // 2
    ]


async def build_translation(
    raw_item: RawItem,
    *,
    media_extractions: list[MediaExtraction],
    glossary: list[dict[str, object]] | None = None,
    rules: list[str] | None = None,
) -> TranslationData:
    source_text = text_from_content_blocks(raw_item.content_blocks)
    source_title = raw_item.native_title
    source_language = raw_item.language or detect_language(source_text)
    target_language = "zh-CN"
    blocks = [dict(block) for block in raw_item.content_blocks]

    structured_text = json.dumps(
        [extraction.structured_data for extraction in media_extractions],
        ensure_ascii=False,
    )
    structured_requires_translation = bool(
        media_extractions and detect_language(structured_text) != "zh-CN"
    )
    if not source_text.strip() and not structured_requires_translation:
        return TranslationData(
            source_language=source_language,
            target_language=target_language,
            translated_title=source_title or "",
            translated_text="",
            translated_content_blocks=blocks,
            translated_media_extractions=[],
            translation_status="not_required",
            translation_model=None,
        )
    if source_language.startswith("zh") and not structured_requires_translation:
        return TranslationData(
            source_language=source_language,
            target_language=target_language,
            translated_title=source_title or "",
            translated_text=source_text,
            translated_content_blocks=blocks,
            translated_media_extractions=[
                {
                    "extraction_id": extraction.id,
                    "translated_data": extraction.structured_data,
                }
                for extraction in media_extractions
            ],
            translation_status="not_required",
            translation_model=None,
        )

    text_blocks = [
        {
            "index": index,
            "type": block.get("type"),
            "text": (
                "\n".join(str(item) for item in block.get("items", []))
                if block.get("type") == "list"
                else block.get("text")
            ),
        }
        for index, block in enumerate(blocks)
        if block.get("type") in TEXT_BLOCK_TYPES and (block.get("text") or block.get("items"))
    ]
    chunks = _chunk_text_blocks(text_blocks)
    outline = _document_outline(text_blocks)
    source_media_extractions = [
        {
            "extraction_id": extraction.id,
            "structured_data": extraction.structured_data,
        }
        for extraction in media_extractions
    ]
    client = LLMClient()
    translated_title = ""
    translated_result_blocks = []
    translated_result_extractions = []
    previous_translation_tail = ""
    for chunk_index, chunk in enumerate(chunks):
        result = await client.translate(
            title=source_title,
            text_blocks=chunk,
            source_language=source_language,
            target_language=target_language,
            glossary=glossary,
            knowledge_rules=rules,
            media_extractions=(source_media_extractions if chunk_index == 0 else []),
            document_context={
                "document_outline": outline,
                "chunk_number": chunk_index + 1,
                "total_chunks": len(chunks),
                "previous_source_tail": (
                    _neighbor_text(chunks[chunk_index - 1], from_end=True)
                    if chunk_index > 0
                    else ""
                ),
                "next_source_head": (
                    _neighbor_text(chunks[chunk_index + 1], from_end=False)
                    if chunk_index + 1 < len(chunks)
                    else ""
                ),
                "preferred_translated_title": translated_title,
                "previous_translation_tail": previous_translation_tail,
            },
        )
        if not translated_title:
            translated_title = result.translated_title
        translated_result_blocks.extend(result.translated_blocks)
        translated_result_extractions.extend(result.translated_media_extractions)
        previous_translation_tail = "\n".join(
            block.text for block in result.translated_blocks[-2:]
        )[-TRANSLATION_CONTEXT_MAX_CHARS // 2 :]

    translations = {block.index: block.text for block in translated_result_blocks}
    translated_blocks: list[dict[str, Any]] = []
    translated_text_parts: list[str] = []
    for index, block in enumerate(blocks):
        translated_block = dict(block)
        if index in translations:
            if block.get("type") == "list":
                translated_block["items"] = [
                    line for line in translations[index].splitlines() if line.strip()
                ]
            else:
                translated_block["text"] = translations[index]
            translated_text_parts.append(translations[index])
        translated_blocks.append(translated_block)

    return TranslationData(
        source_language=source_language,
        target_language=target_language,
        translated_title=translated_title or source_title or "",
        translated_text="\n\n".join(translated_text_parts),
        translated_content_blocks=translated_blocks,
        translated_media_extractions=[
            extraction.model_dump(mode="json") for extraction in translated_result_extractions
        ],
        translation_status="translated",
        translation_model=settings.model_name,
    )
