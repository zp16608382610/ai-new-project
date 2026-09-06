"""Basic health-check tests for the backend API."""
from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_health_returns_200() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}