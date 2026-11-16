
"""Tests for ResumePilot AI's evidence-based matching engine."""

import asyncio
import json

import httpx
import pytest

from app import matcher


REQUIREMENTS = "Python\nFastAPI\nPostgreSQL"

RESUME = (
    "Software engineer with Python experience. "
    "Built production APIs using FastAPI. "
    "Managed PostgreSQL databases."
)


def run_match(requirements=REQUIREMENTS, resume=RESUME):
    return asyncio.run(matcher.match_resume(requirements, resume))


def mock_ollama(monkeypatch, assessments):
    """Replace Ollama HTTP calls with a deterministic test response."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "content": json.dumps({
                        "requirements": assessments
                    })
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            assert url.endswith("/api/chat")
            assert json["stream"] is False
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def assessment(requirement, status, evidence=""):
    return {
        "requirement": requirement,
        "status": status,
        "evidence": evidence,
    }


def test_all_essentials_supported_go_green(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "supported", "Python experience"),
        assessment("FastAPI", "supported", "FastAPI"),
        assessment("PostgreSQL", "supported", "PostgreSQL databases"),
    ])

    result = run_match()

    assert result["category"] == "Review First"
    assert result["supported_count"] == 3
    assert result["total_requirements"] == 3
    assert result["human_review_required"] is True


def test_missing_requirement_goes_red(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "supported", "Python experience"),
        assessment("FastAPI", "supported", "FastAPI"),
        assessment("PostgreSQL", "uncertain"),
    ])

    result = run_match()

    assert result["category"] == "Needs Review"
    assert result["supported_count"] == 2
    assert result["requirements"][2]["status"] == "uncertain"


def test_explicitly_unmet_requirement_goes_red(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "supported", "Python experience"),
        assessment("FastAPI", "not_met"),
        assessment("PostgreSQL", "supported", "PostgreSQL databases"),
    ])

    result = run_match()

    assert result["category"] == "Needs Review"
    assert result["human_review_required"] is True


def test_invented_evidence_is_not_accepted(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "supported", "Python experience"),
        assessment("FastAPI", "supported", "Invented certification"),
        assessment("PostgreSQL", "supported", "PostgreSQL databases"),
    ])

    result = run_match()

    assert result["category"] == "Needs Review"
    assert result["requirements"][1]["status"] == "uncertain"
    assert result["requirements"][1]["evidence"] == ""


def test_missing_evidence_is_not_supported(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "supported", ""),
        assessment("FastAPI", "supported", "FastAPI"),
        assessment("PostgreSQL", "supported", "PostgreSQL databases"),
    ])

    result = run_match()

    assert result["category"] == "Needs Review"
    assert result["supported_count"] == 2


def test_incomplete_ai_response_fails_safely(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "supported", "Python experience"),
    ])

    with pytest.raises(matcher.MatchingError):
        run_match()


def test_reordered_requirements_fail_safely(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("FastAPI", "supported", "FastAPI"),
        assessment("Python", "supported", "Python experience"),
        assessment("PostgreSQL", "supported", "PostgreSQL databases"),
    ])

    with pytest.raises(matcher.MatchingError):
        run_match()


def test_invalid_status_fails_safely(monkeypatch):
    mock_ollama(monkeypatch, [
        assessment("Python", "excellent", "Python experience"),
        assessment("FastAPI", "supported", "FastAPI"),
        assessment("PostgreSQL", "supported", "PostgreSQL databases"),
    ])

    with pytest.raises(matcher.MatchingError):
        run_match()


def test_empty_requirements_are_rejected():
    with pytest.raises(matcher.MatchingError):
        run_match(requirements="   ")


def test_fallback_never_goes_green():
    result = matcher.fallback_result(REQUIREMENTS)

    assert result["category"] == "Needs Review"
    assert result["supported_count"] == 0
    assert result["human_review_required"] is True
    assert all(
        item["status"] == "uncertain"
        for item in result["requirements"]
    )


def test_numbered_requirements_are_parsed():
    result = matcher._extract_requirements(
        "1. Python\n2. FastAPI\n3. PostgreSQL"
    )

    assert result == ["Python", "FastAPI", "PostgreSQL"]


def test_duplicate_requirements_are_removed():
    result = matcher._extract_requirements(
        "Python\nFastAPI\nPython"
    )

    assert result == ["Python", "FastAPI"]
