"""Batch 17 atomic reservation and controlled recovery regression tests."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import main, credits
from app.batch_journal import claim_and_reserve, recover_interrupted_batches, complete_batch

@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'batch17.db'))
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(main.app) as client:
        response = client.post('/api/auth/register', json={'email':'recovery@example.com','password':'SecurePassword123!'})
        assert response.status_code == 201, response.text
        yield response.json()['user']['id']

def test_atomic_claim_reserves_all_credits(account):
    assert claim_and_reserve(account, 'batch', 'digest', 3) is None
    assert credits.balance(account) == 2
    with credits.connection() as db:
        assert db.execute("SELECT count(*) FROM credit_operations WHERE user_id=? AND state='reserved'",(account,)).fetchone()[0] == 3
        assert db.execute("SELECT state FROM analysis_batches WHERE user_id=? AND request_id='batch'",(account,)).fetchone()[0] == 'processing'

def test_insufficient_credit_does_not_create_journal(account):
    with pytest.raises(HTTPException) as error:
        claim_and_reserve(account, 'large', 'digest', 6)
    assert error.value.status_code == 402
    with credits.connection() as db:
        assert db.execute('SELECT count(*) FROM analysis_batches').fetchone()[0] == 0
    assert credits.balance(account) == 5

def test_recovery_refunds_only_reserved_credits_once(account):
    claim_and_reserve(account, 'partial', 'digest', 3)
    credits.finish(account, 'partial:0', True)
    credits.finish(account, 'partial:1', False)
    with credits.connection() as db:
        db.execute("UPDATE analysis_batches SET created_at=100 WHERE user_id=?",(account,))
    assert credits.balance(account) == 3
    result = recover_interrupted_batches(now=100+86401)
    assert result == {'interrupted_batches': 1, 'refunded_credits': 1}
    assert credits.balance(account) == 4
    assert recover_interrupted_batches(now=100+86401) == {'interrupted_batches': 0, 'refunded_credits': 0}
    assert credits.balance(account) == 4
    with pytest.raises(HTTPException) as error:
        claim_and_reserve(account, 'partial', 'digest', 3)
    assert error.value.status_code == 409

def test_completed_batch_never_recovered(account):
    claim_and_reserve(account, 'done', 'digest', 1)
    credits.finish(account, 'done:0', True)
    complete_batch(account, 'done', {'candidates': [], 'errors': []})
    with credits.connection() as db:
        db.execute("UPDATE analysis_batches SET created_at=100 WHERE user_id=?",(account,))
    assert recover_interrupted_batches(now=100+86401)['interrupted_batches'] == 0
    assert claim_and_reserve(account, 'done', 'digest', 1) == {'candidates': [], 'errors': []}
    assert credits.balance(account) == 4

def test_recent_processing_not_recovered(account):
    claim_and_reserve(account, 'recent', 'digest', 2)
    assert recover_interrupted_batches()['interrupted_batches'] == 0
    assert credits.balance(account) == 3

def test_different_fingerprint_cannot_replay(account):
    claim_and_reserve(account, 'key', 'first', 1)
    with pytest.raises(HTTPException) as error:
        claim_and_reserve(account, 'key', 'second', 1)
    assert error.value.status_code == 409
    assert credits.balance(account) == 4
