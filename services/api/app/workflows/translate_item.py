import json
from dataclasses import dataclass
from typing import Any

from app.content_blocks import TEXT_BLOCK_TYPES, text_from_content_blocks
from app.core.config import settings
from app.models.media_extraction import MediaExtraction
from app.models.raw_item import RawItem
from app.services.llm import LLMClient

@dataclass(slots=True)
class TranslationData:
    source_language: str
    target_language: str
    translated_title: str
    translated_text: str
    translated_content_blocks: list[dict[str, Any]]
    translated_summary: str
    translated_entities: list[dict[str, str]]
    translated_media_extractions: list[dict[str, object]]
    translation_status: str
    translation_model: str | None


def detect_language(text: str) -> str:
    visible_chars = [char for char in text if not char.isspace()]
    if not visible_chars:
        return "unknown"
    cjk_chars = sum("\u4e00" <= char <= "\u9fff" for char in visible_chars)
    return "zh-CN" if cjk_chars / len(visible_chars) >= 0.1 else "en"


async def build_translation(
    raw_item: RawItem,
    *,
    canonical_title: str,
    summary: str,
    entities: list[dict[str, str]],
    media_extractions: list[MediaExtraction],
    glossary: list[dict[str, object]] | None = None,
) -> TranslationData:
    source_text = text_from_content_blocks(raw_item.content_blocks)
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
    if source_language.startswith("zh") and not structured_requires_translation:
        return TranslationData(
            source_language=source_language,
            target_language=target_language,
            translated_title=canonical_title,
            translated_text=source_text,
            translated_content_blocks=blocks,
            translated_summary=summary,
            translated_entities=entities,
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
        if block.get("type") in TEXT_BLOCK_TYPES
        and (block.get("text") or block.get("items"))
    ]
    result = await LLMClient().translate(
        title=canonical_title,
        text_blocks=text_blocks,
        source_language=source_language,
        target_language=target_language,
        glossary=glossary,
        summary=summary,
        entities=entities,
        media_extractions=[
            {
                "extraction_id": extraction.id,
                "structured_data": extraction.structured_data,
            }
            for extraction in media_extractions
        ],
    )
    translations = {block.index: block.text for block in result.translated_blocks}
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
        translated_title=result.translated_title or canonical_title,
        translated_text="\n\n".join(translated_text_parts),
        translated_content_blocks=translated_blocks,
        translated_summary=result.translated_summary,
        translated_entities=[
            entity.model_dump(mode="json") for entity in result.translated_entities
        ],
        translated_media_extractions=[
            extraction.model_dump(mode="json")
            for extraction in result.translated_media_extractions
        ],
        translation_status="translated",
        translation_model=settings.model_name,
    )
