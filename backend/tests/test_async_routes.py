"""Async submission API route smoke tests; no worker execution."""
from app.main import app


def test_async_submission_route_is_registered():
    from fastapi.testclient import TestClient

    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert "/api/jobs/submit" in paths
    assert "post" in paths["/api/jobs/submit"]


def test_async_submission_does_not_replace_sync_endpoint():
    assert any(getattr(route, 'path', None) == '/api/analyze' for route in app.routes)
