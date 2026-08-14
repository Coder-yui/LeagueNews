from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.connectors.baidu_tieba import (
    BaiduTiebaConnectorCollectionError,
    BaiduTiebaConnectorConfigurationError,
)
from app.connectors.weibo import WeiboConnectorCollectionError, WeiboConnectorConfigurationError
from app.connectors.x_twitter import XConnectorCollectionError, XConnectorConfigurationError
from app.core.config import settings
from app.domain.importance import is_featured_message
from app.models.connector_run import ConnectorRun
from app.models.normalized_item import NormalizedItem
from app.models.notification import NotificationOutbox
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun


logger = logging.getLogger(__name__)

COLLECTION_FAILURE_KINDS = {
    "authentication",
    "configuration",
    "rate_limited",
    "upstream_rejected",
    "network",
    "parse",
    "collection_failed",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None, *, fallback: datetime | None = None) -> str:
    resolved = value or fallback or _utc_now()
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=UTC)
    return resolved.astimezone(UTC).isoformat()


def _clip(value: object, limit: int, *, fallback: str = "-") -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        return fallback
    return text[:limit] + ("…" if len(text) > limit else "")


def _public_message_url(normalized_item_id: int) -> str | None:
    origin = settings.public_origin.strip().rstrip("/")
    return f"{origin}/messages/{normalized_item_id}" if origin else None


def _insert_ignore(
    db: Session,
    *,
    target: str,
    kind: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> bool:
    values = {
        "target": target,
        "kind": kind,
        "dedupe_key": dedupe_key,
        "payload": payload,
        "status": "pending",
        "attempts": 0,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(NotificationOutbox).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(NotificationOutbox).values(**values)
    else:
        statement = insert(NotificationOutbox).values(**values)
    if dialect in {"postgresql", "sqlite"}:
        statement = statement.on_conflict_do_nothing(index_elements=["dedupe_key"])
    result = db.execute(statement)
    return bool(result.rowcount)


def enqueue_notification(
    db: Session,
    *,
    target: str,
    kind: str,
    dedupe_key: str,
    payload: dict[str, Any],
) -> bool:
    """Add one outbox record without allowing notification persistence to break business work."""
    try:
        with db.begin_nested():
            return _insert_ignore(
                db,
                target=target,
                kind=kind,
                dedupe_key=dedupe_key,
                payload=payload,
            )
    except Exception:
        # The caller owns the outer business transaction. A failed side-channel write is
        # deliberately observable only through logs; it must not roll back publication,
        # collection state, or a failed pipeline job.
        logger.exception(
            "notification outbox enqueue failed target=%s kind=%s",
            target,
            kind,
        )
        return False


def _featured_payload(item: NormalizedItem) -> dict[str, Any]:
    raw_item = item.raw_item
    source = raw_item.source if raw_item is not None else None
    return {
        "normalized_item_id": item.id,
        "title": _clip(item.translated_title or item.normalized_title, 300),
        "summary": _clip(item.summary, 1000),
        "source_name": _clip(source.name if source else None, 120),
        "connector_type": _clip(source.connector_type if source else None, 60),
        "products": [str(product) for product in (item.products or [])[:3]],
        "message_type": _clip(item.message_type, 80),
        "importance_score": round(float(item.importance_score), 4),
        "published_at": _iso(raw_item.published_at if raw_item else None, fallback=item.created_at),
        "author_name": _clip(raw_item.author_name if raw_item else None, 120, fallback=""),
        "topics": [str(topic) for topic in (item.topics or [])[:5]],
        "url": _public_message_url(item.id),
    }


def enqueue_featured_message(db: Session, item: NormalizedItem) -> bool:
    if not settings.feishu_featured_push_enabled:
        return False
    if item.publication_status != "published" or not is_featured_message(item.importance_score):
        return False
    return enqueue_notification(
        db,
        target="featured",
        kind="featured_message",
        dedupe_key=f"featured:{item.id}",
        payload=_featured_payload(item),
    )


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def classify_collection_error(error: BaseException) -> str:
    """Classify using connector exception boundaries, with only small message hints."""
    chain = _exception_chain(error)
    if any(isinstance(value, XConnectorConfigurationError) for value in chain):
        message = " ".join(str(value).lower() for value in chain)
        return "authentication" if any(
            marker in message for marker in ("cookie", "auth", "login", "ct0", "token")
        ) else "configuration"
    if any(isinstance(value, WeiboConnectorConfigurationError) for value in chain):
        message = " ".join(str(value).lower() for value in chain)
        return "authentication" if any(
            marker in message for marker in ("login", "profile", "cookie", "登录")
        ) else "configuration"
    if any(isinstance(value, BaiduTiebaConnectorConfigurationError) for value in chain):
        return "configuration"
    if any(isinstance(value, XConnectorCollectionError) for value in chain):
        message = " ".join(str(value).lower() for value in chain)
        if "rate-limited" in message or "rate limited" in message:
            return "rate_limited"
        if "blocked" in message or "rejected" in message:
            return "upstream_rejected"
        return "collection_failed"
    if any(isinstance(value, WeiboConnectorCollectionError) for value in chain):
        message = " ".join(str(value).lower() for value in chain)
        return "upstream_rejected" if "reject" in message or "登录" in message else "collection_failed"
    if any(isinstance(value, BaiduTiebaConnectorCollectionError) for value in chain):
        return "collection_failed"
    return "collection_failed"


def enqueue_collection_failure(
    db: Session,
    *,
    source: Source,
    connector_run: ConnectorRun | None,
    error: BaseException | str,
    consecutive_failures: int | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    if not settings.feishu_alert_push_enabled:
        return False
    error_kind = classify_collection_error(error) if isinstance(error, BaseException) else "collection_failed"
    if error_kind not in COLLECTION_FAILURE_KINDS:
        error_kind = "collection_failed"
    now = occurred_at or _utc_now()
    cooldown_seconds = settings.feishu_alert_cooldown_minutes * 60
    prefix = f"collection_failure:{source.id}:{error_kind}:"
    recent = db.scalar(
        select(NotificationOutbox.id)
        .where(
            NotificationOutbox.target == "alert",
            NotificationOutbox.kind == "collection_failure",
            NotificationOutbox.dedupe_key.like(f"{prefix}%"),
            NotificationOutbox.created_at >= now - timedelta(seconds=cooldown_seconds),
            NotificationOutbox.status.in_(["pending", "sending", "failed", "sent"]),
        )
        .limit(1)
    )
    if recent is not None:
        return False
    bucket = int(now.timestamp()) // max(cooldown_seconds, 1)
    failures = consecutive_failures
    if failures is None:
        schedule = source.collection_schedule
        failures = (schedule.consecutive_failures + 1) if schedule is not None else 1
    payload = {
        "source_id": source.id,
        "source_name": _clip(source.name, 120),
        "connector_type": _clip(source.connector_type, 60),
        "error_kind": error_kind,
        "consecutive_failures": failures,
        "error_summary": _clip(
            connector_run.error_message if connector_run and connector_run.error_message else error,
            1200,
        ),
        "connector_run_id": connector_run.id if connector_run else None,
        "occurred_at": _iso(
            connector_run.finished_at if connector_run else None,
            fallback=now,
        ),
    }
    return enqueue_notification(
        db,
        target="alert",
        kind="collection_failure",
        dedupe_key=f"{prefix}{bucket}",
        payload=payload,
    )


def enqueue_pipeline_failure(
    db: Session,
    *,
    job: PipelineJob,
    raw_item: RawItem | None,
) -> bool:
    if not settings.feishu_alert_push_enabled:
        return False
    source = raw_item.source if raw_item is not None else None
    stage = job.current_stage
    if stage != "event_aggregation" and job.processing_run_id is not None:
        processing_run = db.get(ProcessingRun, job.processing_run_id)
        if processing_run is not None:
            stage = processing_run.current_stage
    payload = {
        "stage": stage,
        "raw_item_id": job.raw_item_id,
        "pipeline_job_id": job.id,
        "processing_run_id": job.processing_run_id,
        "source_name": _clip(source.name if source else None, 120),
        "source_url": _clip(raw_item.canonical_url if raw_item else None, 500, fallback=""),
        "error_summary": _clip(job.error_message, 1200),
        "last_checkpoint_id": job.last_checkpoint_id,
        "occurred_at": _iso(job.completed_at),
    }
    return enqueue_notification(
        db,
        target="alert",
        kind="pipeline_failure",
        dedupe_key=f"pipeline_failure:{job.id}",
        payload=payload,
    )


STAGE_LABELS = {
    "relevance": "相关性",
    "image_ocr": "OCR",
    "translation": "翻译",
    "message_analysis": "消息分析",
    "importance": "重要性计算",
    "event_aggregation": "事件聚合",
}


ERROR_KIND_LABELS = {
    "authentication": "认证 / 登录状态",
    "configuration": "配置",
    "rate_limited": "限流",
    "upstream_rejected": "上游拒绝 / blocked",
    "network": "网络",
    "parse": "解析",
    "collection_failed": "采集失败",
}


def render_notification_body(notification: NotificationOutbox) -> dict[str, Any]:
    payload = notification.payload
    if notification.kind == "featured_message":
        return _render_featured(payload)
    if notification.kind == "collection_failure":
        return _render_collection_failure(payload)
    if notification.kind == "pipeline_failure":
        return _render_pipeline_failure(payload)
    raise ValueError(f"unsupported notification kind: {notification.kind}")


def _render_featured(payload: dict[str, Any]) -> dict[str, Any]:
    title = _clip(payload.get("title"), 300)
    summary = _clip(payload.get("summary"), 1000)
    details = [
        f"来源：{_clip(payload.get('source_name'), 120)}",
        f"产品：{_clip(', '.join(payload.get('products', [])) if payload.get('products') else payload.get('connector_type'), 120)}",
        f"消息类型：{_clip(payload.get('message_type'), 100)}",
        f"重要性：{float(payload.get('importance_score', 0)):.2f}",
        f"发布时间：{_clip(payload.get('published_at'), 80)}",
    ]
    author = payload.get("author_name")
    if author:
        details.append(f"作者：{_clip(author, 120)}")
    topics = payload.get("topics")
    if topics:
        details.append(f"Topics：{_clip(', '.join(str(value) for value in topics), 200)}")
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**{title}**\n\n{summary}"}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(details)}},
    ]
    if payload.get("url"):
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看完整消息"},
                        "type": "primary",
                        "url": payload["url"],
                    }
                ],
            }
        )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "🔥 LeagueNews 精选"},
            },
            "elements": elements,
        },
    }


def _render_collection_failure(payload: dict[str, Any]) -> dict[str, Any]:
    details = [
        f"信源：{_clip(payload.get('source_name'), 120)}",
        f"connector_type：{_clip(payload.get('connector_type'), 60)}",
        f"source_id：{payload.get('source_id', '-')}",
        f"错误类别：{ERROR_KIND_LABELS.get(payload.get('error_kind'), payload.get('error_kind', '-'))}",
        f"连续失败：{payload.get('consecutive_failures', '-')}",
        f"ConnectorRun ID：{payload.get('connector_run_id') or '-'}",
        f"发生时间：{_clip(payload.get('occurred_at'), 80)}",
    ]
    return _render_alert_card("🚨 LeagueNews 采集失败", details, payload.get("error_summary"))


def _render_pipeline_failure(payload: dict[str, Any]) -> dict[str, Any]:
    details = [
        f"阶段：{STAGE_LABELS.get(payload.get('stage'), payload.get('stage', '-'))}",
        f"RawItem ID：{payload.get('raw_item_id', '-')}",
        f"PipelineJob ID：{payload.get('pipeline_job_id', '-')}",
        f"ProcessingRun ID：{payload.get('processing_run_id') or '-'}",
        f"来源：{_clip(payload.get('source_name'), 120)}",
        f"Checkpoint ID：{payload.get('last_checkpoint_id') or '-'}",
        f"发生时间：{_clip(payload.get('occurred_at'), 80)}",
    ]
    return _render_alert_card("🚨 LeagueNews 处理失败", details, payload.get("error_summary"))


def _render_alert_card(title: str, details: list[str], error_summary: object) -> dict[str, Any]:
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(details)}},
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**错误摘要**\n{_clip(error_summary, 1200)}",
            },
        },
    ]
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        },
    }
