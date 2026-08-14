from __future__ import annotations

import asyncio
import logging
import os
import secrets
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.notification import NotificationOutbox
from app.services.feishu import FeishuBotClient
from app.services.notifications import render_notification_body


logger = logging.getLogger(__name__)
RETRY_DELAYS_SECONDS = (30, 120, 600, 1800)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def validate_dispatcher_configuration() -> None:
    missing: list[str] = []
    if settings.feishu_featured_push_enabled and not settings.feishu_featured_webhook_url.strip():
        missing.append("FEISHU_FEATURED_WEBHOOK_URL")
    if settings.feishu_alert_push_enabled and not settings.feishu_alert_webhook_url.strip():
        missing.append("FEISHU_ALERT_WEBHOOK_URL")
    if missing:
        raise ValueError(
            "Feishu notification dispatcher configuration is incomplete; missing: "
            + ", ".join(missing)
        )


def _enabled_targets() -> set[str]:
    targets: set[str] = set()
    if settings.feishu_featured_push_enabled and settings.feishu_featured_webhook_url.strip():
        targets.add("featured")
    if settings.feishu_alert_push_enabled and settings.feishu_alert_webhook_url.strip():
        targets.add("alert")
    return targets


def claim_next_notification(
    db: Session,
    *,
    worker_id: str | None = None,
    enabled_targets: set[str] | None = None,
) -> NotificationOutbox | None:
    enabled_targets = _enabled_targets() if enabled_targets is None else enabled_targets
    if not enabled_targets:
        return None
    now = datetime.now(UTC)
    notification = db.scalar(
        select(NotificationOutbox)
        .where(
            NotificationOutbox.target.in_(enabled_targets),
            or_(
                (
                    NotificationOutbox.status.in_(["pending", "failed"])
                    & (NotificationOutbox.next_attempt_at <= now)
                ),
                (
                    (NotificationOutbox.status == "sending")
                    & (
                        NotificationOutbox.lease_expires_at.is_(None)
                        | (NotificationOutbox.lease_expires_at <= now)
                    )
                ),
            ),
        )
        .order_by(NotificationOutbox.created_at, NotificationOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if notification is None:
        return None
    notification.status = "sending"
    notification.attempts += 1
    notification.lease_token = secrets.token_hex(24)
    notification.lease_expires_at = now + timedelta(seconds=settings.feishu_notification_lease_seconds)
    notification.last_error = None
    db.commit()
    db.refresh(notification)
    return notification


def mark_notification_sent(db: Session, *, notification_id: int, lease_token: str) -> bool:
    result = db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == notification_id,
            NotificationOutbox.status == "sending",
            NotificationOutbox.lease_token == lease_token,
        )
        .values(
            status="sent",
            sent_at=datetime.now(UTC),
            lease_token=None,
            lease_expires_at=None,
            last_error=None,
        )
    )
    db.commit()
    return bool(result.rowcount)


def mark_notification_failed(
    db: Session,
    *,
    notification_id: int,
    lease_token: str,
    error: str,
    attempts: int,
) -> bool:
    delay = RETRY_DELAYS_SECONDS[min(max(attempts - 1, 0), len(RETRY_DELAYS_SECONDS) - 1)]
    result = db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == notification_id,
            NotificationOutbox.status == "sending",
            NotificationOutbox.lease_token == lease_token,
        )
        .values(
            status="failed",
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
            last_error=error[:2000],
            lease_token=None,
            lease_expires_at=None,
        )
    )
    db.commit()
    return bool(result.rowcount)


def _client_for(notification: NotificationOutbox) -> FeishuBotClient:
    if notification.target == "featured":
        return FeishuBotClient(
            webhook_url=settings.feishu_featured_webhook_url,
            secret=settings.feishu_featured_secret,
        )
    if notification.target == "alert":
        return FeishuBotClient(
            webhook_url=settings.feishu_alert_webhook_url,
            secret=settings.feishu_alert_secret,
        )
    raise ValueError(f"unsupported notification target: {notification.target}")


async def process_next_notification() -> bool:
    validate_dispatcher_configuration()
    with SessionLocal() as db:
        notification = claim_next_notification(db, worker_id=_worker_id())
        if notification is None:
            return False
        lease_token = notification.lease_token
        if not lease_token:
            return True
        try:
            body = render_notification_body(notification)
            await _client_for(notification).send(body)
        except Exception as exc:
            message = str(exc)[:2000]
            logger.warning(
                "Feishu notification delivery failed id=%s target=%s kind=%s attempt=%s: %s",
                notification.id,
                notification.target,
                notification.kind,
                notification.attempts,
                message,
            )
            mark_notification_failed(
                db,
                notification_id=notification.id,
                lease_token=lease_token,
                error=message,
                attempts=notification.attempts,
            )
        else:
            mark_notification_sent(
                db,
                notification_id=notification.id,
                lease_token=lease_token,
            )
        return True


async def notification_dispatcher_loop() -> None:
    while True:
        processed = await process_next_notification()
        if not processed:
            await asyncio.sleep(settings.feishu_notification_poll_seconds)
