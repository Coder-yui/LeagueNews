import json

from sqlalchemy.orm import Session

from app.content_blocks import text_from_content_blocks
from app.core.config import settings
from app.models.event_item import EventItem
from app.models.news_event import NewsEvent
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.llm import LLMClient
from app.workflows.translate_item import build_translation
from app.workflows.understand_media import extraction_context, understand_patch_media


async def analyze_raw_item(db: Session, raw_item: RawItem) -> NewsEvent:
    """Stable workflow: load raw item -> analyze -> validate/store event -> mark processed."""
    media_extractions = await understand_patch_media(db, raw_item)
    source_text = text_from_content_blocks(raw_item.content_blocks)
    analysis_content = source_text
    if media_extractions:
        analysis_content += "\n\n[图片版本改动结构化提取]\n" + json.dumps(
            extraction_context(media_extractions), ensure_ascii=False
        )
    result = await LLMClient().analyze(
        title=raw_item.display_title, content=analysis_content
    )
    translation = await build_translation(raw_item, canonical_title=result.title)
    display_title = (
        translation.translated_title
        if translation.translation_status == "translated"
        else result.title
    )
    normalized_item = NormalizedItem(
        raw_item_id=raw_item.id,
        normalized_title=display_title,
        normalized_text=source_text,
        summary=result.summary,
        category=result.category,
        entities=result.entities,
        importance_score=result.importance_score,
        credibility=result.credibility,
        language=raw_item.language,
        source_language=translation.source_language,
        target_language=translation.target_language,
        translated_title=translation.translated_title,
        translated_text=translation.translated_text,
        translated_content_blocks=translation.translated_content_blocks,
        translation_status=translation.translation_status,
        translation_model=translation.translation_model,
        analysis_model=settings.model_name,
        analysis_version="v2",
    )
    db.add(normalized_item)
    db.flush()
    event = NewsEvent(
        title=display_title,
        summary=result.summary,
        category=result.category,
        entities=result.entities,
        importance_score=result.importance_score,
        credibility=result.credibility,
        occurred_at=raw_item.published_at,
    )
    db.add(event)
    db.flush()
    db.add(
        EventItem(
            event_id=event.id,
            normalized_item_id=normalized_item.id,
            relation_type="primary",
            is_primary=True,
        )
    )
    db.commit()
    db.refresh(event)
    return event
