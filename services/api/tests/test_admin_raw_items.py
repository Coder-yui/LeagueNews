from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
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
