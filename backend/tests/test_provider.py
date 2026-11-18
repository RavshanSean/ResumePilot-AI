"""OpenAI provider integration and cost guard tests."""

import asyncio
import json

import httpx
import pytest

from app import matcher


RESUME = "Experienced Python engineer."
REQUIREMENTS = "Python"


def run():
    return asyncio.run(matcher.match_resume(REQUIREMENTS, RESUME))


def test_openai_provider_uses_json_and_limits_output(monkeypatch):
    monkeypatch.setattr(matcher, "AI_PROVIDER", "openai")
    monkeypatch.setattr(matcher, "_openai_requests", 0)
    monkeypatch.setattr(
        matcher, "OPENAI_MAX_REQUESTS_PER_PROCESS", 2
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "requirements": [{
                                "requirement": "Python",
                                "status": "supported",
                                "evidence": "Python",
                            }]
                        })
                    }
                }]
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json, headers):
            assert url == matcher.OPENAI_URL
            assert json["max_completion_tokens"] == 1200
            assert json["response_format"]["type"] == "json_object"
            assert headers["Authorization"] == "Bearer synthetic-test-key"
            return Response()

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    assert run()["category"] == "Review First"
    assert matcher._openai_requests == 1

    assert run()["category"] == "Review First"
    assert matcher._openai_requests == 2

    with pytest.raises(matcher.MatchingError, match="allowance"):
        run()


def test_missing_openai_key_fails_without_request(monkeypatch):
    monkeypatch.setattr(matcher, "AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(matcher.MatchingError, match="API key"):
        run()


def test_invalid_provider_fails_closed(monkeypatch):
    monkeypatch.setattr(matcher, "AI_PROVIDER", "invalid")

    with pytest.raises(matcher.MatchingError, match="provider"):
        run()


def test_openai_http_error_is_safe(monkeypatch):
    monkeypatch.setattr(matcher, "AI_PROVIDER", "openai")
    monkeypatch.setattr(matcher, "_openai_requests", 0)
    monkeypatch.setattr(
        matcher, "OPENAI_MAX_REQUESTS_PER_PROCESS", 10
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json, headers):
            raise httpx.ConnectError("private network details")

    monkeypatch.setattr(httpx, "AsyncClient", Client)

    with pytest.raises(matcher.MatchingError) as exc:
        run()

    assert "private network details" not in str(exc.value)
