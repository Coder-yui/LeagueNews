from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from app.domain.event_categories import EventCategory
from app.domain.event_types import CredibilityLevel, EventFamily, EventLifecycle
from app.domain.message_taxonomy import Product
from app.mcp.tools._common import mcp_db_session
from app.services.event_read import get_event_detail, search_events


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="search_events",
        description=(
            "Search persisted LeagueNews events, which aggregate related published messages. "
            "Use this for event progress, lifecycle, credibility, importance, heat, roster "
            "changes, matches, releases, and other multi-message developments; use "
            "search_news for individual messages. Results are event-card summaries."
        ),
        structured_output=True,
    )
    def search_events_tool(
        query: str | None = None,
        product: Product | None = None,
        category: EventCategory | None = None,
        event_family: EventFamily | None = None,
        lifecycle: EventLifecycle | None = None,
        credibility: CredibilityLevel | None = None,
        importance: Literal["low", "medium", "high", "critical"] | None = None,
        heat: Literal["cold", "emerging", "active", "hot", "surging"] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort_by: Literal["time", "importance", "heat"] = "time",
        sort: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        with mcp_db_session() as db:
            result = search_events(
                db,
                query=query,
                product=product,
                category=category,
                event_family=event_family,
                lifecycle=lifecycle,
                credibility=credibility,
                importance=importance,
                heat=heat,
                since=since,
                until=until,
                sort_by=sort_by,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        return {
            "items": result.items,
            "total": result.total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(result.items) < result.total,
        }

    @mcp.tool(
        name="get_event",
        description=(
            "Read one current public LeagueNews event by id. The event must have at least one "
            "current published message mention; stale or withdrawn event projections are not "
            "returned. Returns lifecycle, importance, credibility, heat, supporting evidence, "
            "related published messages, sources, and timeline updates."
        ),
        structured_output=True,
    )
    def get_event(event_id: Annotated[int, Field(gt=0)]) -> dict[str, Any]:
        with mcp_db_session() as db:
            payload = get_event_detail(db, event_id)
        if payload is None:
            raise ValueError("event not found")
        return payload
