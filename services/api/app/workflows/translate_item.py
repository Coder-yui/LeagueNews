from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.content_blocks import TEXT_BLOCK_TYPES, text_from_content_blocks
from app.core.config import settings
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.llm import LLMClient

@dataclass(slots=True)
class TranslationData:
    source_language: str
    target_language: str
    translated_title: str
    translated_text: str
    translated_content_blocks: list[dict[str, Any]]
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
    glossary: list[dict[str, object]] | None = None,
) -> TranslationData:
    source_text = text_from_content_blocks(raw_item.content_blocks)
    source_language = raw_item.language or detect_language(source_text)
    target_language = "zh-CN"
    blocks = [dict(block) for block in raw_item.content_blocks]

    if source_language.startswith("zh"):
        return TranslationData(
            source_language=source_language,
            target_language=target_language,
            translated_title=canonical_title,
            translated_text=source_text,
            translated_content_blocks=blocks,
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
    if not text_blocks:
        return TranslationData(
            source_language=source_language,
            target_language=target_language,
            translated_title=canonical_title,
            translated_text="",
            translated_content_blocks=blocks,
            translation_status="not_required",
            translation_model=None,
        )

    result = await LLMClient().translate(
        title=raw_item.display_title,
        text_blocks=text_blocks,
        source_language=source_language,
        target_language=target_language,
        glossary=glossary,
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
        translation_status="translated",
        translation_model=settings.model_name,
    )


async def translate_normalized_item(db: Session, item: NormalizedItem) -> NormalizedItem:
    translation = await build_translation(item.raw_item, canonical_title=item.normalized_title)
    item.source_language = translation.source_language
    item.target_language = translation.target_language
    item.translated_title = translation.translated_title
    item.translated_text = translation.translated_text
    item.translated_content_blocks = translation.translated_content_blocks
    item.translation_status = translation.translation_status
    item.translation_model = translation.translation_model
    if translation.translation_status == "translated":
        item.normalized_title = translation.translated_title
        for link in item.event_links:
            if link.is_primary:
                link.event.title = translation.translated_title
    db.commit()
    db.refresh(item)
    return item
