import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.routes.digests import digest_payload
from app.api.routes.events import _detail_payload, _event_statement, _summary_payload
from app.core.database import get_db
from app.core.config import settings
from app.models.event import Event
from app.models.intelligence import Claim, Digest, EventClaim
from app.models.normalized_item import NormalizedItem

router = APIRouter()
PROTOCOL_VERSION = "2025-11-25"
TOOLS = [
    ("list_events", "List published events", {"limit": {"type": "integer"}}),
    ("get_event", "Get a published event", {"event_id": {"type": "integer"}}),
    (
        "get_event_timeline",
        "Get event revisions and traceable claims",
        {"event_id": {"type": "integer"}},
    ),
    ("search_events", "Search published events", {"query": {"type": "string"}}),
    ("list_digests", "List published daily or weekly digests", {}),
    ("get_digest", "Get a published digest", {"digest_id": {"type": "integer"}}),
]


def _tool_definitions() -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": properties,
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }
        for name, description, properties in TOOLS
    ]


def _call_tool(db: Session, name: str, arguments: dict[str, Any]) -> object:
    if name == "list_events":
        limit = max(1, min(100, int(arguments.get("limit", 20))))
        events = db.scalars(
            _event_statement()
            .where(Event.status == "active")
            .order_by(Event.updated_at.desc())
            .limit(limit)
        )
        return {"events": [_summary_payload(event) for event in events]}
    if name in {"get_event", "get_event_timeline"}:
        event_id = int(arguments["event_id"])
        event = db.scalar(
            _event_statement().where(
                Event.id == event_id, Event.status == "active"
            )
        )
        if event is None:
            raise ValueError("event not found")
        payload = _detail_payload(event)
        if name == "get_event_timeline":
            claims = db.execute(
                select(Claim, EventClaim.relation)
                .join(EventClaim, EventClaim.claim_id == Claim.id)
                .join(
                    NormalizedItem,
                    NormalizedItem.id == Claim.normalized_item_id,
                )
                .where(
                    EventClaim.event_id == event_id,
                    Claim.status == "active",
                    NormalizedItem.publication_status == "published",
                )
                .order_by(Claim.effective_at, Claim.id)
            ).all()
            payload["claims"] = [
                {
                    "id": claim.id,
                    "subject": claim.subject,
                    "predicate": claim.predicate,
                    "object": claim.object_value,
                    "stance": claim.stance,
                    "evidence": claim.evidence,
                    "normalized_item_id": claim.normalized_item_id,
                    "provenance": claim.provenance,
                    "event_relation": relation,
                }
                for claim, relation in claims
            ]
        return payload
    if name == "search_events":
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        events = db.scalars(
            _event_statement()
            .where(
                Event.status == "active",
                or_(Event.title.ilike(f"%{query}%"), Event.summary.ilike(f"%{query}%")),
            )
            .order_by(Event.updated_at.desc())
            .limit(20)
        )
        return {"events": [_summary_payload(event) for event in events]}
    if name in {"list_digests", "get_digest"}:
        statement = select(Digest).where(Digest.status == "published")
        if name == "get_digest":
            statement = statement.where(Digest.id == int(arguments["digest_id"]))
            digest = db.scalar(statement)
            if digest is None:
                raise ValueError("digest not found")
            return digest_payload(digest)
        return {
            "digests": [
                digest_payload(value)
                for value in db.scalars(
                    statement.order_by(Digest.cutoff_at.desc()).limit(20)
                )
            ]
        }
    raise ValueError(f"unknown tool: {name}")


@router.post("", response_model=None)
async def mcp_endpoint(
    request: Request, db: Session = Depends(get_db)
) -> Response | dict[str, object]:
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins:
        raise HTTPException(status_code=403, detail="invalid Origin")
    requested_version = request.headers.get("mcp-protocol-version")
    if requested_version and requested_version != PROTOCOL_VERSION:
        raise HTTPException(status_code=400, detail="unsupported MCP protocol version")
    payload = await request.json()
    request_id = payload.get("id")
    method = payload.get("method")
    if request_id is None:
        return Response(status_code=202)
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "leaguenews-readonly",
                    "version": "1.0.0",
                    "description": "Read-only published LeagueNews intelligence",
                },
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": _tool_definitions()},
        }
    if method == "tools/call":
        try:
            params = payload.get("params") or {}
            result = _call_tool(
                db, str(params.get("name")), dict(params.get("arguments") or {})
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        serialized = json.loads(json.dumps(result, default=str))
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(serialized, ensure_ascii=False),
                    }
                ],
                "structuredContent": serialized,
                "isError": False,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }
