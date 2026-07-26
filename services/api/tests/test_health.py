from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_http_errors_use_unified_response_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    payload = response.json()
    assert payload["detail"] == "Not Found"
    assert payload["error"] == {
        "code": "not_found",
        "message": "Not Found",
        "retryable": False,
    }
