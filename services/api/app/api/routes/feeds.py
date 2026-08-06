from email.utils import format_datetime
from html import escape

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.event import Event
from app.models.intelligence import Digest

router = APIRouter()


def _rss(title: str, description: str, items: list[dict[str, str]]) -> Response:
    entries = "".join(
        "<item>"
        f"<title>{escape(item['title'])}</title>"
        f"<guid isPermaLink=\"false\">{escape(item['guid'])}</guid>"
        f"<link>{escape(item['link'])}</link>"
        f"<description>{escape(item['description'])}</description>"
        f"<pubDate>{escape(item['published'])}</pubDate>"
        f"<atom:updated>{escape(item['updated'])}</atom:updated>"
        "</item>"
        for item in items
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{escape(title)}</title><link>https://leaguenews.me</link>"
        f"<description>{escape(description)}</description>{entries}</channel></rss>"
    )
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/events.xml")
def event_feed(
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Response:
    events = db.scalars(
        select(Event)
        .where(Event.status == "active")
        .order_by(
            Event.importance_score.desc(),
            func.coalesce(Event.last_published_at, Event.created_at).desc(),
        )
        .limit(limit)
    )
    return _rss(
        "LeagueNews 事件更新",
        "仅包含已发布事件",
        [
            {
                "title": event.title,
                "guid": f"event:{event.id}",
                "link": f"https://leaguenews.me/events/{event.id}",
                "description": event.summary,
                "published": format_datetime(
                    event.first_published_at or event.created_at
                ),
                "updated": (event.updated_at.isoformat()),
            }
            for event in events
        ],
    )


@router.get("/digests.xml")
def digest_feed(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Response:
    digests = db.scalars(
        select(Digest)
        .where(Digest.status == "published")
        .order_by(Digest.published_at.desc())
        .limit(limit)
    )
    return _rss(
        "LeagueNews 日报与周报",
        "已发布的事件情报摘要",
        [
            {
                "title": digest.title,
                "guid": f"digest:{digest.id}",
                "link": f"https://leaguenews.me/digests/{digest.id}",
                "description": digest.body,
                "published": format_datetime(digest.published_at),
                "updated": digest.updated_at.isoformat(),
            }
            for digest in digests
        ],
    )
