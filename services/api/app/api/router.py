from fastapi import APIRouter

from app.api.routes import (
    connectors,
    collection_schedules,
    digests,
    events,
    feeds,
    event_workflows,
    health,
    imports,
    knowledge,
    media_assets,
    mcp,
    normalized_items,
    ocr_lab,
    pipeline_corrections,
    raw_items,
    sources,
    workflows,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(raw_items.router, prefix="/raw-items", tags=["raw_items"])
api_router.include_router(
    normalized_items.router, prefix="/normalized-items", tags=["normalized_items"]
)
api_router.include_router(media_assets.router, prefix="/media-assets", tags=["media_assets"])
api_router.include_router(imports.router, prefix="/imports", tags=["imports"])
api_router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
api_router.include_router(
    collection_schedules.router,
    prefix="/collection-schedules",
    tags=["collection_schedules"],
)
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(digests.router, prefix="/digests", tags=["digests"])
api_router.include_router(feeds.router, prefix="/feeds", tags=["feeds"])
api_router.include_router(mcp.router, prefix="/mcp", tags=["mcp"])
api_router.include_router(
    event_workflows.router,
    prefix="/event-workflows",
    tags=["event_workflows"],
)
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(
    pipeline_corrections.router,
    prefix="/pipeline",
    tags=["pipeline"],
)
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
api_router.include_router(ocr_lab.router, prefix="/ocr-lab", tags=["ocr_lab"])
