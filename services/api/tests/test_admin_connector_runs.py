from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.connector_run import ConnectorRun
from app.models.source import Source


def test_connector_run_page_defaults_to_failed_and_supports_status_filter() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="采集测试来源", connector_type="manual")
        db.add(source)
        db.flush()
        db.add_all(
            [
                ConnectorRun(
                    source_id=source.id,
                    connector_type="manual",
                    status="completed",
                ),
                ConnectorRun(
                    source_id=source.id,
                    connector_type="manual",
                    status="failed",
                    error_message="测试失败",
                ),
            ]
        )
        db.commit()

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        default_response = client.get("/api/v1/connectors/runs/page")
        all_response = client.get("/api/v1/connectors/runs/page?status=all")
        completed_response = client.get(
            "/api/v1/connectors/runs/page?status=completed"
        )
    finally:
        app.dependency_overrides.clear()

    assert default_response.status_code == 200
    assert default_response.json()["total"] == 1
    assert default_response.json()["items"][0]["status"] == "failed"
    assert all_response.status_code == 200
    assert all_response.json()["total"] == 2
    assert completed_response.status_code == 200
    assert completed_response.json()["total"] == 1
    assert completed_response.json()["items"][0]["status"] == "completed"
