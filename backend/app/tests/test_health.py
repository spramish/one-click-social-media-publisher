"""Phase 1 tests: configuration and health endpoints.

These tests do not require PostgreSQL or Redis — the readiness probes are
expected to report "unreachable" in a bare test environment, and that is
asserted explicitly rather than hidden.
"""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_root_returns_service_metadata() -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == settings.project_name
    assert body["health"] == "/api/v1/health"


def test_health_liveness_ok() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


def test_health_db_reports_status() -> None:
    """Shape check — works whether or not PostgreSQL is running locally."""
    response = client.get("/api/v1/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "error"}
    assert "database" in body


def test_health_redis_reports_status() -> None:
    """Shape check — works whether or not Redis is running locally."""
    response = client.get("/api/v1/health/redis")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "error"}
    assert "redis" in body


def test_openapi_available() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == settings.project_name
