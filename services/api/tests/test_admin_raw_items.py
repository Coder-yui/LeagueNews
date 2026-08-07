from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem
from app.models.source import Source


def test_raw_item_list_exposes_admin_pipeline_projection() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Admin Source", connector_type="manual")
        db.add(source)
        db.flush()
        db.add(
            RawItem(
                source_id=source.id,
                native_title="Admin projection item",
                content_blocks=[{"type": "paragraph", "text": "Evidence"}],
            )
        )
        db.commit()

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/api/v1/raw-items")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["source_name"] == "Admin Source"
    assert payload["source_connector_type"] == "manual"
    assert payload["processing_status"] == "pending"
    assert payload["current_pipeline_stage"] is None
    assert payload["current_pipeline_job_status"] is None
    assert payload["processing_runs"] == []


def test_raw_item_list_projects_repaired_media_path() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Media Source", connector_type="manual")
        raw = RawItem(
            source=source,
            native_title="Repaired media",
            content_blocks=[
                {
                    "id": "b0001",
                    "type": "image",
                    "source_url": "https://cdn.example.com/image.jpg",
                }
            ],
        )
        db.add_all(
            [
                source,
                raw,
                MediaAsset(
                    raw_item=raw,
                    block_index=0,
                    source_url="https://cdn.example.com/image.jpg",
                    storage_path="/api/v1/media-assets/files/manual/image.jpg",
                ),
            ]
        )
        db.commit()

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/api/v1/raw-items")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["content_blocks"][0]["storage_path"] == (
        "/api/v1/media-assets/files/manual/image.jpg"
    )


def test_raw_item_admin_queries_exclude_superseded_revisions() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Versioned Source", connector_type="manual")
        db.add(source)
        db.flush()
        old = RawItem(
            source_id=source.id,
            external_id="same-item",
            native_title="Old revision",
            content_blocks=[{"type": "paragraph", "text": "Old"}],
            revision=1,
        )
        db.add(old)
        db.flush()
        latest = RawItem(
            source_id=source.id,
            external_id="same-item",
            native_title="Latest revision",
            content_blocks=[{"type": "paragraph", "text": "Latest"}],
            revision=2,
            supersedes_raw_item_id=old.id,
        )
        db.add(latest)
        db.commit()
        old_id = old.id
        latest_id = latest.id

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        simple = client.get("/api/v1/raw-items")
        admin = client.get(
            "/api/v1/raw-items/admin-page?process_status=all"
        )
        process_old = client.post(f"/api/v1/raw-items/{old_id}/process")
    finally:
        app.dependency_overrides.clear()

    assert simple.status_code == 200
    assert [item["id"] for item in simple.json()] == [latest_id]
    assert admin.status_code == 200
    assert [item["id"] for item in admin.json()["items"]] == [latest_id]
    assert admin.json()["total"] == 1
    assert admin.json()["total_items"] == 1
    assert process_old.status_code == 409
    assert process_old.json()["detail"] == (
        "raw item has been superseded by a newer revision"
    )


def test_raw_item_admin_page_reports_total_and_paginates_beyond_100() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Paged Source", connector_type="manual")
        db.add(source)
        db.flush()
        db.add_all(
            [
                RawItem(
                    source_id=source.id,
                    native_title=f"Item {index:03d}",
                    content_blocks=[{"type": "paragraph", "text": str(index)}],
                )
                for index in range(105)
            ]
        )
        db.commit()

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        first = client.get(
            "/api/v1/raw-items/admin-page?process_status=all&limit=100&sort=desc"
        )
        second = client.get(
            "/api/v1/raw-items/admin-page"
            "?process_status=all&limit=100&offset=100&sort=desc"
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json()["total"] == 105
    assert first.json()["total_items"] == 105
    assert len(first.json()["items"]) == 100
    assert second.status_code == 200
    assert len(second.json()["items"]) == 5
    assert first.json()["source_options"] == [{"id": 1, "name": "Paged Source"}]
