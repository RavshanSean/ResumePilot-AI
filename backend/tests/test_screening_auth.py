"""Authenticated screening integration tests."""

import io

import pymupdf
from fastapi.testclient import TestClient

from app import auth, main


def pdf_bytes():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Python FastAPI experience")
    content = document.tobytes()
    document.close()
    return content


def test_anonymous_screening_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE",
        str(tmp_path / "accounts.db"),
    )
    monkeypatch.delenv("RESUMEPILOT_TEST_BYPASS_AUTH", raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/analyze",
            data={"requirements": "Python"},
            files={
                "files": (
                    "resume.pdf",
                    io.BytesIO(pdf_bytes()),
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 401


def test_authenticated_screening_works(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE",
        str(tmp_path / "accounts.db"),
    )
    monkeypatch.delenv("RESUMEPILOT_TEST_BYPASS_AUTH", raising=False)

    async def fake_match(requirements, resume_text):
        return {
            "category": "Review First",
            "summary": "Evidence found. Human review required.",
            "requirements": [{
                "requirement": "Python",
                "status": "supported",
                "evidence": "Python",
            }],
            "supported_count": 1,
            "total_requirements": 1,
        }

    monkeypatch.setattr(main, "match_resume", fake_match)

    with TestClient(main.app) as client:
        registration = client.post(
            "/api/auth/register",
            json={
                "email": "recruiter@example.com",
                "password": "SecurePassword123!",
            },
        )
        assert registration.status_code == 201

        token = registration.json()["access_token"]

        response = client.post(
            "/api/analyze",
            headers={"Authorization": f"Bearer {token}"},
            data={"requirements": "Python"},
            files={
                "files": (
                    "resume.pdf",
                    io.BytesIO(pdf_bytes()),
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 200
        assert response.json()["candidates"][0]["category"] == "Review First"

        client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

        denied = client.post(
            "/api/analyze",
            headers={"Authorization": f"Bearer {token}"},
            data={"requirements": "Python"},
            files={
                "files": (
                    "resume.pdf",
                    io.BytesIO(pdf_bytes()),
                    "application/pdf",
                )
            },
        )

        assert denied.status_code == 401


def test_invalid_token_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE",
        str(tmp_path / "accounts.db"),
    )
    monkeypatch.delenv("RESUMEPILOT_TEST_BYPASS_AUTH", raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/analyze",
            headers={"Authorization": "Bearer invalid-token"},
            data={"requirements": "Python"},
            files={
                "files": (
                    "resume.pdf",
                    io.BytesIO(pdf_bytes()),
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 401
