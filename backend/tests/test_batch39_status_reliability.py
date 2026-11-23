"""Batch 39: polling stability, session revocation, and read-only status checks."""
import pytest
from fastapi.testclient import TestClient
from app import main
from app.auth import connection

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path / 'private'))
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(main.app) as client:
        response = client.post('/api/auth/register', json={
            'email': 'batch39@example.com', 'password': 'SecurePassword123!'
        })
        assert response.status_code == 201, response.text
        account = response.json()
        uid = account['user']['id']
        with connection() as db:
            db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                       (uid, 'batch39-job', 'private-fingerprint', 'processing', 100))
            db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (uid, 'batch39-job', 'leased', 'private-lease-token', 200, 1, 100, 100))
        yield client, account

def headers(account):
    return {'Authorization': 'Bearer ' + account['access_token']}

def snapshot():
    with connection() as db:
        return {
            'jobs': [tuple(r) for r in db.execute('SELECT * FROM analysis_jobs ORDER BY user_id,request_id')],
            'batches': [tuple(r) for r in db.execute('SELECT * FROM analysis_batches ORDER BY user_id,request_id')],
            'credits': [tuple(r) for r in db.execute('SELECT * FROM credit_accounts ORDER BY user_id')],
        }

def test_repeated_polling_is_private_and_read_only(setup):
    client, account = setup
    before = snapshot()
    for _ in range(12):
        response = client.get('/api/jobs/batch39-job/status', headers=headers(account))
        assert response.status_code == 200, response.text
        assert response.json() == {'status': 'processing'}
        for secret in ('private-lease-token', 'private-fingerprint', account['user']['id'], 'batch39-job'):
            assert secret not in response.text
    assert snapshot() == before

def test_polling_reflects_committed_state_changes(setup):
    client, account = setup
    url = '/api/jobs/batch39-job/status'
    assert client.get(url, headers=headers(account)).json() == {'status': 'processing'}
    with connection() as db:
        db.execute("UPDATE analysis_batches SET state='completed' WHERE request_id='batch39-job'")
    assert client.get(url, headers=headers(account)).json() == {'status': 'completed'}
    with connection() as db:
        db.execute("UPDATE analysis_batches SET state='interrupted' WHERE request_id='batch39-job'")
    assert client.get(url, headers=headers(account)).json() == {'status': 'interrupted'}

def test_revoked_session_cannot_poll(setup):
    client, account = setup
    url = '/api/jobs/batch39-job/status'
    auth = headers(account)
    assert client.get(url, headers=auth).status_code == 200
    assert client.post('/api/auth/logout', headers=auth).status_code == 200
    assert client.get(url, headers=auth).status_code == 401

def test_expired_session_cannot_poll(setup):
    client, account = setup
    auth = headers(account)
    with connection() as db:
        db.execute('UPDATE sessions SET expires_at=0 WHERE user_id=?', (account['user']['id'],))
    assert client.get('/api/jobs/batch39-job/status', headers=auth).status_code == 401
