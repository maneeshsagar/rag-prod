"""The health endpoint must always answer, even when dependencies are down."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_every_dependency():
    response = TestClient(app).get("/health")

    assert response.status_code in (200, 503)
    body = response.json()
    assert set(body["checks"]) == {"postgres", "redis", "qdrant", "object_storage", "api_keys"}
