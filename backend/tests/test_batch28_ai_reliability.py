"""AI reliability and privacy regressions, entirely offline."""
import asyncio
import json
import httpx
import pytest
from app import matcher

REQUIREMENTS = "Python\nFastAPI"
RESUME = "Python developer with FastAPI experience."

class FakeResponse:
    def __init__(self, data=None, failure=None):
        self.data = data
        self.failure = failure
    def raise_for_status(self):
        if self.failure:
            raise self.failure
    def json(self):
        return self.data

class FakeClient:
    response = None
    error = None
    def __init__(self, *args, **kwargs):
        assert kwargs.get('trust_env') is False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def post(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response

def run():
    return asyncio.run(matcher.match_resume(REQUIREMENTS, RESUME))

@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.setattr(matcher, 'AI_PROVIDER', 'ollama')
    monkeypatch.setattr(matcher, 'AI_TIMEOUT_SECONDS', 5.0)
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    FakeClient.error = None
    FakeClient.response = None

@pytest.mark.parametrize('error', [
    httpx.ConnectError('offline'),
    httpx.ReadTimeout('timed out'),
    httpx.HTTPStatusError('503', request=httpx.Request('POST', 'http://localhost/api/chat'), response=httpx.Response(503)),
])
def test_network_failures_are_safe(error):
    FakeClient.error = error
    with pytest.raises(matcher.MatchingError, match='unavailable'):
        run()

@pytest.mark.parametrize('data', [
    {}, {'message': {}}, {'message': {'content': '{not json'}},
    {'message': {'content': '[]'}},
])
def test_bad_provider_payload_is_safe(data):
    FakeClient.response = FakeResponse(data)
    with pytest.raises(matcher.MatchingError):
        run()

@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf')])
def test_invalid_timeout_fails_before_network(monkeypatch, timeout):
    monkeypatch.setattr(matcher, 'AI_TIMEOUT_SECONDS', timeout)
    with pytest.raises(matcher.MatchingError, match='timeout configuration'):
        run()

def test_unverified_evidence_is_not_returned():
    result = matcher._normalize_result({
        'requirements': [
            {'requirement': 'Python', 'status': 'uncertain', 'evidence': 'imaginary quote'},
            {'requirement': 'FastAPI', 'status': 'not_met', 'evidence': 'unverified quote'},
        ]
    }, ['Python', 'FastAPI'], RESUME)
    assert result['category'] == 'Needs Review'
    assert all(item['evidence'] == '' for item in result['requirements'])
