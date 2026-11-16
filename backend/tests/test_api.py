
"""API tests for ResumePilot AI."""

import io

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient

from app import main
from app.matcher import MatchingError

client = TestClient(main.app)

REQUIREMENTS = "Python\nFastAPI"


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    async def match_resume(requirements, resume_text):
        return {
            "category": "Needs Review",
            "summary": "One requirement is uncertain. Human review required.",
            "requirements": [
                {
                    "requirement": "Python",
                    "status": "supported",
                    "evidence": "Python",
                },
                {
                    "requirement": "FastAPI",
                    "status": "uncertain",
                    "evidence": "",
                },
            ],
            "supported_count": 1,
            "total_requirements": 2,
            "human_review_required": True,
        }

    monkeypatch.setattr(main, "match_resume", match_resume)


def pdf(text):
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


def docx(text):
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def upload(filename, content, requirements=REQUIREMENTS):
    return client.post(
        "/api/analyze",
        data={"requirements": requirements},
        files=[("files", (filename, content))],
    )


def test_health_reports_ai_integration():
    result = client.get("/api/health").json()

    assert result["status"] == "ok"
    assert result["ai_enabled"] is True
    assert result["analysis_mode"] == "ai"
    assert result["human_review_required"] is True
    assert result["persistence_enabled"] is False


def test_docx_extracts_evidence_and_returns_assessment():
    response = upload(
        "candidate.docx",
        docx("Built Python APIs for five years."),
    )

    assert response.status_code == 200
    result = response.json()

    assert result["mode"] == "ai"
    assert not result["errors"]

    candidate = result["candidates"][0]

    assert candidate["id"] == "0"
    assert candidate["filename"] == "candidate.docx"
    assert "Built Python APIs" in candidate["evidence"]
    assert candidate["status"] == "Needs Review"
    assert candidate["category"] == "Needs Review"
    assert candidate["ai_completed"] is True
    assert candidate["demo"] is False
    assert candidate["human_review_required"] is True


def test_pdf_and_invalid_file_are_isolated():
    response = client.post(
        "/api/analyze",
        data={"requirements": REQUIREMENTS},
        files=[
            ("files", ("valid.pdf", pdf("Python engineer"))),
            ("files", ("bad.pdf", b"broken")),
        ],
    )

    assert response.status_code == 200

    result = response.json()

    assert len(result["candidates"]) == 1
    assert len(result["errors"]) == 1
    assert result["candidates"][0]["id"] == "0"
    assert "Python engineer" in result["candidates"][0]["evidence"]


@pytest.mark.parametrize(
    "filename,content",
    [
        ("file.txt", b"text"),
        ("empty.pdf", b""),
    ],
)
def test_rejects_invalid_upload(filename, content):
    result = upload(filename, content).json()

    assert not result["candidates"]
    assert len(result["errors"]) == 1


def test_batch_limit():
    response = client.post(
        "/api/analyze",
        data={"requirements": REQUIREMENTS},
        files=[
            ("files", (f"{number}.pdf", b"a"))
            for number in range(31)
        ],
    )

    assert response.status_code == 400


def test_unreadable_pdf():
    with pymupdf.open() as document:
        document.new_page()
        data = document.tobytes()

    result = upload("scan.pdf", data).json()

    assert not result["candidates"]
    assert len(result["errors"]) == 1
    assert "OCR" in result["errors"][0]["message"]


def test_requirements_length_limit():
    response = upload(
        "candidate.pdf",
        b"x",
        requirements="x" * 20001,
    )

    assert response.status_code == 400


def test_empty_requirements_rejected():
    response = upload(
        "candidate.pdf",
        pdf("Python engineer"),
        requirements=" ",
    )

    assert response.status_code == 400


def test_successful_green_priority(monkeypatch):
    async def green_match(requirements, resume_text):
        return {
            "category": "Review First",
            "summary": "All essential requirements have evidence.",
            "requirements": [
                {
                    "requirement": "Python",
                    "status": "supported",
                    "evidence": "Python",
                },
                {
                    "requirement": "FastAPI",
                    "status": "supported",
                    "evidence": "FastAPI",
                },
            ],
            "supported_count": 2,
            "total_requirements": 2,
            "human_review_required": True,
        }

    monkeypatch.setattr(main, "match_resume", green_match)

    result = upload(
        "qualified.pdf",
        pdf("Python and FastAPI engineer"),
    ).json()

    candidate = result["candidates"][0]

    assert candidate["category"] == "Review First"
    assert candidate["status"] == "Review First"
    assert candidate["supported_count"] == 2
    assert candidate["ai_completed"] is True


def test_ollama_failure_preserves_candidate(monkeypatch):
    async def unavailable(requirements, resume_text):
        raise MatchingError("Ollama unavailable")

    monkeypatch.setattr(main, "match_resume", unavailable)

    result = upload(
        "candidate.pdf",
        pdf("Python engineer"),
    ).json()

    assert not result["errors"]
    assert len(result["candidates"]) == 1

    candidate = result["candidates"][0]

    assert candidate["status"] == "Needs Review"
    assert candidate["ai_completed"] is False
    assert candidate["supported_count"] == 0
    assert all(
        requirement["status"] == "uncertain"
        for requirement in candidate["requirements"]
    )
