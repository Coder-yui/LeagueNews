from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from app.domain.message_taxonomy import MessageType, Product, Topic
from app.mcp.tools._common import mcp_db_session
from app.services.event_read import event_associations_for_messages
from app.services.published_items import search_published_items, get_published_item


def news_list_projection(
    payload: dict[str, Any],
    *,
    related_event_ids: list[int] | None = None,
    include_related_event_ids: bool = True,
) -> dict[str, Any]:
    """Keep search results compact while retaining agent triage fields."""
    projection = {
        "id": payload["id"],
        "title": payload["title"],
        "summary": payload["summary"],
        "products": payload["products"],
        "message_type": payload["message_type"],
        "topics": payload["topics"],
        "importance_score": payload["importance_score"],
        "source_name": payload["source_name"],
        "source_reliability_score": payload["source_reliability_score"],
        "author": payload["author"],
        "published_at": payload["published_at"],
        "source_url": payload["source_url"],
    }
    if include_related_event_ids:
        projection["related_event_ids"] = related_event_ids or []
    return projection


def news_detail_projection(
    payload: dict[str, Any],
    *,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Expose the full public message projection without internal paths/metadata."""
    media = [
        {
            "block_index": extraction["block_index"],
            "url": extraction["storage_path"] or extraction["source_url"],
            "source_url": extraction["source_url"],
            "mime_type": extraction["mime_type"],
            "confidence": extraction["confidence"],
            "original_data": extraction["original_data"],
            "translated_data": extraction["translated_data"],
        }
        for extraction in payload["media_extractions"]
    ]
    return {
        "id": payload["id"],
        "title": payload["title"],
        "summary": payload["summary"],
        "products": payload["products"],
        "message_type": payload["message_type"],
        "topics": payload["topics"],
        "content_form": payload["content_form"],
        "entities": payload["entities"],
        "importance_score": payload["importance_score"],
        "importance_dimensions": payload["importance_dimensions"],
        "importance_policy_version": payload["importance_policy_version"],
        "source": {
            "id": payload["source_id"],
            "name": payload["source_name"],
            "reliability_score": payload["source_reliability_score"],
            "base_url": payload["source_base_url"],
        },
        "author": payload["author"],
        "published_at": payload["published_at"],
        "source_url": payload["source_url"],
        "original_title": payload["original_title"],
        "original_content_blocks": payload["original_content_blocks"],
        "source_language": payload["source_language"],
        "translated_title": payload["translated_title"],
        "translated_content_blocks": payload["translated_content_blocks"],
        "translation_status": payload["translation_status"],
        "media": media,
        "events": events or [],
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="search_news",
        description=(
            "Search published LeagueNews messages by words, product, message type, topic, "
            "importance, or time. Results are compact triage summaries; call get_news_item "
            "when you need the full public message and evidence. This tool only reads "
            "published current message projections."
        ),
        structured_output=True,
    )
    def search_news(
        query: str | None = None,
        product: Product | None = None,
        message_type: MessageType | None = None,
        topic: Topic | None = None,
        min_importance: Annotated[float | None, Field(ge=0, le=1)] = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort_by: Literal["time", "importance", "priority"] = "time",
        sort: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        with mcp_db_session() as db:
            result = search_published_items(
                db,
                query=query,
                product=product,
                message_type=message_type,
                topic=topic,
                min_importance=min_importance,
                since=since,
                until=until,
                sort_by=sort_by,
                sort=sort,
                limit=limit,
                offset=offset,
            )
            associations = event_associations_for_messages(
                db,
                [payload["id"] for payload in result.items],
            )
            items = [
                news_list_projection(
                    payload,
                    related_event_ids=[event["id"] for event in associations.get(payload["id"], [])],
                )
                for payload in result.items
            ]
        return {
            "items": items,
            "total": result.total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < result.total,
        }

    @mcp.tool(
        name="get_news_item",
        description=(
            "Read one published LeagueNews message by id. Returns the full public message "
            "projection, including source, bilingual content, safe published media, and "
            "current event associations. It does not expose review, pipeline, raw LLM, "
            "private storage, or secret data."
        ),
        structured_output=True,
    )
    def get_news_item(message_id: Annotated[int, Field(gt=0)]) -> dict[str, Any]:
        with mcp_db_session() as db:
            payload = get_published_item(db, message_id)
            if payload is None:
                raise ValueError("published news item not found")
            associations = event_associations_for_messages(db, [message_id])
        return news_detail_projection(payload, events=associations.get(message_id, []))
