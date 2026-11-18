"""Resilience, cleanup, and privacy regression tests."""

import logging
import threading

import pymupdf
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app import main
from app.matcher import MatchingError

client = TestClient(main.app)
REQUIREMENTS = "Python"


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    async def match_resume(requirements, resume_text):
        return {
            "category": "Needs Review",
            "summary": "Human review required.",
            "requirements": [
                {
                    "requirement": "Python",
                    "status": "uncertain",
                    "evidence": "",
                }
            ],
            "supported_count": 0,
            "total_requirements": 1,
        }

    monkeypatch.setattr(main, "match_resume", match_resume)


def upload(files, requirements=REQUIREMENTS):
    return client.post(
        "/api/analyze",
        data={"requirements": requirements},
        files=files,
    )


def test_read_failure_isolated_and_uploads_closed(monkeypatch, caplog):
    original_read = UploadFile.read
    original_close = UploadFile.close
    closed = []

    async def read(file, size=-1):
        if file.filename == "private-name.pdf":
            raise OSError("private internal details")
        return await original_read(file, size)

    async def close(file):
        closed.append(file.filename)
        await original_close(file)

    monkeypatch.setattr(UploadFile, "read", read)
    monkeypatch.setattr(UploadFile, "close", close)
    monkeypatch.setattr(
        main,
        "parse_resume",
        lambda name, data: "Synthetic evidence",
    )

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = upload([
            ("files", ("private-name.pdf", b"data")),
            ("files", ("valid.pdf", b"data")),
        ])

    assert response.status_code == 200
    result = response.json()

    assert len(result["candidates"]) == 1
    assert len(result["errors"]) == 1
    assert result["candidates"][0]["id"] == "1"
    assert result["candidates"][0]["ai_completed"] is True
    assert result["errors"][0]["message"] == (
        "This resume could not be processed. Please try again."
    )

    assert {"private-name.pdf", "valid.pdf"} <= set(closed)
    assert "private internal details" not in response.text
    assert "private-name.pdf" not in caplog.text
    assert "Synthetic evidence" not in caplog.text
    assert "candidates=1 errors=1" in caplog.text


def test_parser_runs_off_event_loop(monkeypatch):
    def parse(name, data):
        assert threading.current_thread().name == "AnyIO worker thread"
        return "Synthetic evidence"

    monkeypatch.setattr(main, "parse_resume", parse)

    result = upload([
        ("files", ("resume.pdf", b"data"))
    ]).json()

    assert result["candidates"][0]["evidence"] == "Synthetic evidence"
    assert result["candidates"][0]["human_review_required"] is True


def test_password_protected_pdf_reports_per_file_error():
    with pymupdf.open() as document:
        document.new_page().insert_text(
            (72, 72), "Private synthetic resume"
        )
        data = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )

    response = upload([
        ("files", ("protected.pdf", data))
    ])

    assert response.status_code == 200
    assert not response.json()["candidates"]
    assert "unencrypted" in response.json()["errors"][0]["message"]


def test_ai_failure_does_not_discard_valid_resume(monkeypatch):
    monkeypatch.setattr(
        main,
        "parse_resume",
        lambda name, data: "Synthetic Python evidence",
    )

    async def unavailable(requirements, resume_text):
        raise MatchingError("Private provider failure")

    monkeypatch.setattr(main, "match_resume", unavailable)

    response = upload([
        ("files", ("candidate.pdf", b"data"))
    ])

    assert response.status_code == 200
    result = response.json()

    assert not result["errors"]
    assert len(result["candidates"]) == 1

    candidate = result["candidates"][0]
    assert candidate["category"] == "Needs Review"
    assert candidate["ai_completed"] is False
    assert candidate["human_review_required"] is True
    assert "Private provider failure" not in response.text


def test_unexpected_ai_error_isolated_per_candidate(monkeypatch):
    monkeypatch.setattr(
        main,
        "parse_resume",
        lambda name, data: "Synthetic evidence",
    )

    async def broken_ai(requirements, resume_text):
        raise RuntimeError("private model exception")

    monkeypatch.setattr(main, "match_resume", broken_ai)

    response = upload([
        ("files", ("candidate.pdf", b"data"))
    ])

    assert response.status_code == 200
    result = response.json()

    assert not result["candidates"]
    assert len(result["errors"]) == 1
    assert "private model exception" not in response.text
    assert result["errors"][0]["message"] == (
        "This resume could not be processed. Please try again."
    )


def test_empty_requirements_rejected_before_ai():
    response = upload([
        ("files", ("candidate.pdf", b"data"))
    ], requirements="   ")

    assert response.status_code == 400
    assert "requirements" in response.json()["detail"].lower()
