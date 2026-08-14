from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
import app.services.notification_dispatcher as dispatcher
from app.core.config import settings
from app.core.database import Base
from app.connectors.baidu_tieba import BaiduTiebaConnectorCollectionError
from app.connectors.weibo import WeiboConnectorConfigurationError
from app.connectors.x_twitter import XConnectorConfigurationError
from app.models.connector_run import ConnectorRun
from app.models.normalized_item import NormalizedItem
from app.models.notification import NotificationOutbox
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun
from app.services.feishu import FeishuBotClient, FeishuDeliveryError
from app.services.notification_dispatcher import validate_dispatcher_configuration
from app.services.notifications import (
    classify_collection_error,
    enqueue_collection_failure,
    enqueue_featured_message,
    enqueue_notification,
    enqueue_pipeline_failure,
    render_notification_body,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _published_item(db: Session, *, score: float) -> NormalizedItem:
    source = Source(name=f"Featured source {score}", connector_type="x_twitter")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        external_id=f"featured-{score}",
        native_title="Native headline",
        author_name="Author",
        canonical_url="https://x.com/example/status/1",
        content_blocks=[{"type": "paragraph", "text": "source evidence"}],
        published_at=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title="Normalized headline",
        normalized_text="Normalized body",
        summary="A useful summary",
        products=["lol_pc"],
        message_type="game_announcement",
        topics=["balance_gameplay"],
        importance_score=score,
        translated_title="中文标题",
        translated_text="中文正文",
        translated_content_blocks=[],
        translation_status="translated",
        analysis_model="test",
    )
    db.add(item)
    db.commit()
    return item


def test_featured_enqueue_reuses_authoritative_rule_and_is_idempotent(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_featured_push_enabled", True)
    item = _published_item(db, score=0.74)

    assert enqueue_featured_message(db, item) is False
    assert db.scalar(select(NotificationOutbox.id)) is None

    item.importance_score = 0.75
    assert enqueue_featured_message(db, item) is True
    db.commit()

    item.current_revision += 1
    item.summary = "Updated summary"
    db.commit()
    assert enqueue_featured_message(db, item) is False
    notifications = list(db.scalars(select(NotificationOutbox)))
    assert len(notifications) == 1
    assert notifications[0].dedupe_key == f"featured:{item.id}"
    assert notifications[0].payload["products"] == ["lol_pc"]
    assert notifications[0].payload["url"].endswith(f"/messages/{item.id}")


def test_collection_error_classification_uses_connector_boundaries() -> None:
    assert classify_collection_error(XConnectorConfigurationError("cookie missing")) == "authentication"
    assert classify_collection_error(
        WeiboConnectorConfigurationError("browser profile is not logged in")
    ) == "authentication"
    assert classify_collection_error(
        BaiduTiebaConnectorCollectionError("response changed")
    ) == "collection_failed"


def test_collection_alert_cooldown_is_per_source_and_error_kind(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", True)
    source = Source(name="Alert source", connector_type="x_twitter")
    other_source = Source(name="Other alert source", connector_type="x_twitter")
    db.add_all([source, other_source])
    db.flush()
    run = ConnectorRun(
        source_id=source.id,
        connector_type=source.connector_type,
        status="failed",
        error_message="cookie expired",
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    db.commit()

    occurred_at = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    assert enqueue_collection_failure(
        db,
        source=source,
        connector_run=run,
        error=XConnectorConfigurationError("cookie expired"),
        consecutive_failures=1,
        occurred_at=occurred_at,
    ) is True
    db.commit()
    assert enqueue_collection_failure(
        db,
        source=source,
        connector_run=run,
        error=XConnectorConfigurationError("cookie expired"),
        consecutive_failures=2,
        occurred_at=occurred_at + timedelta(minutes=30),
    ) is False
    assert enqueue_collection_failure(
        db,
        source=other_source,
        connector_run=None,
        error=XConnectorConfigurationError("cookie expired"),
        consecutive_failures=1,
        occurred_at=occurred_at + timedelta(minutes=30),
    ) is True
    assert db.scalar(select(NotificationOutbox).where(NotificationOutbox.kind == "collection_failure"))


def test_pipeline_failure_payload_is_alert_only_and_stage_specific(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", True)
    source = Source(name="Pipeline source", connector_type="manual")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        native_title="Raw title",
        canonical_url="https://example.com/raw",
        content_blocks=[{"type": "paragraph", "text": "raw"}],
    )
    db.add(raw)
    db.flush()
    job = PipelineJob(
        raw_item_id=raw.id,
        status="failed",
        current_stage="image_ocr",
        error_message="OCR provider failed",
        completed_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    assert enqueue_pipeline_failure(db, job=job, raw_item=raw) is True
    db.commit()
    notification = db.scalar(select(NotificationOutbox))
    assert notification is not None
    assert notification.target == "alert"
    assert notification.payload["stage"] == "image_ocr"
    card = render_notification_body(notification)
    assert card["card"]["header"]["title"]["content"] == "🚨 LeagueNews 处理失败"
    assert "OCR" in card["card"]["elements"][0]["text"]["content"]


def test_pipeline_failure_prefers_processing_run_stage_for_normal_processing(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", True)
    source = Source(name="Processing stage source", connector_type="manual")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        native_title="Raw title",
        canonical_url="https://example.com/raw",
        content_blocks=[{"type": "paragraph", "text": "raw"}],
    )
    db.add(raw)
    db.flush()
    processing_run = ProcessingRun(
        raw_item_id=raw.id,
        workflow_type="item",
        status="failed",
        current_stage="message_analysis",
        execution_mode="automatic",
    )
    db.add(processing_run)
    db.flush()
    job = PipelineJob(
        raw_item_id=raw.id,
        processing_run_id=processing_run.id,
        status="failed",
        current_stage="importance",
        error_message="message analysis failed",
        completed_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    assert enqueue_pipeline_failure(db, job=job, raw_item=raw) is True
    notification = db.scalar(select(NotificationOutbox))
    assert notification is not None
    assert notification.payload["stage"] == "message_analysis"


def test_event_aggregation_failure_keeps_pipeline_job_stage(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", True)
    source = Source(name="Event stage source", connector_type="manual")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        native_title="Raw title",
        canonical_url="https://example.com/raw",
        content_blocks=[{"type": "paragraph", "text": "raw"}],
    )
    db.add(raw)
    db.flush()
    processing_run = ProcessingRun(
        raw_item_id=raw.id,
        workflow_type="item",
        status="failed",
        current_stage="importance",
        execution_mode="automatic",
    )
    db.add(processing_run)
    db.flush()
    job = PipelineJob(
        raw_item_id=raw.id,
        processing_run_id=processing_run.id,
        status="failed",
        current_stage="event_aggregation",
        error_message="event aggregation failed",
        completed_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    assert enqueue_pipeline_failure(db, job=job, raw_item=raw) is True
    notification = db.scalar(select(NotificationOutbox))
    assert notification is not None
    assert notification.payload["stage"] == "event_aggregation"


@pytest.mark.parametrize(
    ("stage", "label"),
    (
        ("image_ocr", "OCR"),
        ("translation", "翻译"),
        ("message_analysis", "消息分析"),
        ("importance", "重要性计算"),
        ("event_aggregation", "事件聚合"),
    ),
)
def test_pipeline_failure_card_labels_cover_all_failure_stages(
    stage: str,
    label: str,
) -> None:
    notification = NotificationOutbox(
        target="alert",
        kind="pipeline_failure",
        dedupe_key=f"pipeline:render:{stage}",
        payload={"stage": stage, "error_summary": "failure"},
    )
    card = render_notification_body(notification)
    assert label in card["card"]["elements"][0]["text"]["content"]


def test_claim_lease_recovery_and_target_isolation(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    featured = enqueue_notification(
        db,
        target="featured",
        kind="featured_message",
        dedupe_key="featured:lease-test",
        payload={"title": "test"},
    )
    alert = enqueue_notification(
        db,
        target="alert",
        kind="pipeline_failure",
        dedupe_key="pipeline:lease-test",
        payload={"stage": "translation"},
    )
    assert featured and alert
    db.commit()

    claimed = dispatcher.claim_next_notification(db, enabled_targets={"featured"})
    assert claimed is not None
    assert claimed.target == "featured"
    first_token = claimed.lease_token
    claimed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    recovered = dispatcher.claim_next_notification(db, enabled_targets={"featured"})
    assert recovered is not None
    assert recovered.lease_token != first_token
    assert dispatcher.claim_next_notification(db, enabled_targets={"alert"}) is not None


def test_dispatcher_boundary_rejects_missing_enabled_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_featured_push_enabled", True)
    monkeypatch.setattr(settings, "feishu_featured_webhook_url", "")
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", False)

    with pytest.raises(ValueError, match="FEISHU_FEATURED_WEBHOOK_URL"):
        validate_dispatcher_configuration()


@pytest.mark.anyio
async def test_dispatcher_uses_separate_bots_and_does_not_recurse(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_featured_push_enabled", True)
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", True)
    monkeypatch.setattr(settings, "feishu_featured_webhook_url", "https://featured.invalid/hook")
    monkeypatch.setattr(settings, "feishu_alert_webhook_url", "https://alert.invalid/hook")
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    sent: list[str] = []

    class FakeClient:
        def __init__(self, *, webhook_url: str, secret: str = "") -> None:
            self.webhook_url = webhook_url

        async def send(self, _body: dict[str, object]) -> None:
            sent.append(self.webhook_url)

    monkeypatch.setattr(dispatcher, "SessionLocal", factory)
    monkeypatch.setattr(dispatcher, "FeishuBotClient", FakeClient)
    enqueue_notification(
        db,
        target="featured",
        kind="featured_message",
        dedupe_key="featured:bot-test",
        payload={"title": "test"},
    )
    enqueue_notification(
        db,
        target="alert",
        kind="pipeline_failure",
        dedupe_key="pipeline:bot-test",
        payload={"stage": "translation"},
    )
    db.commit()

    assert await dispatcher.process_next_notification() is True
    assert await dispatcher.process_next_notification() is True
    assert sent == ["https://featured.invalid/hook", "https://alert.invalid/hook"]
    with factory() as check:
        rows = list(check.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id)))
    assert [row.status for row in rows] == ["sent", "sent"]
    assert db.scalar(select(NotificationOutbox).where(NotificationOutbox.status == "sending")) is None


@pytest.mark.anyio
async def test_dispatcher_network_failure_is_retryable_without_recursive_alert(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_alert_push_enabled", True)
    monkeypatch.setattr(settings, "feishu_alert_webhook_url", "https://alert.invalid/hook")
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    class FailingClient:
        def __init__(self, **_: object) -> None:
            pass

        async def send(self, _body: dict[str, object]) -> None:
            raise FeishuDeliveryError("temporary upstream failure")

    monkeypatch.setattr(dispatcher, "SessionLocal", factory)
    monkeypatch.setattr(dispatcher, "FeishuBotClient", FailingClient)
    enqueue_notification(
        db,
        target="alert",
        kind="pipeline_failure",
        dedupe_key="pipeline:retry-test",
        payload={"stage": "translation"},
    )
    db.commit()

    assert await dispatcher.process_next_notification() is True
    with factory() as check:
        rows = list(check.scalars(select(NotificationOutbox)))
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].attempts == 1
    assert rows[0].next_attempt_at is not None
    assert rows[0].last_error == "temporary upstream failure"


def test_feishu_signature_does_not_expose_webhook_secret() -> None:
    signature = FeishuBotClient.signature("1710000000", "bot-secret")
    assert signature
    assert "bot-secret" not in signature


def test_feishu_signature_matches_official_deterministic_vector() -> None:
    assert (
        FeishuBotClient.signature("1599360473", "demo")
        == "l1N0gAcBjdwBvGm1xMjOF0XSyaLRpR7tuO5dHfhAYc8="
    )
