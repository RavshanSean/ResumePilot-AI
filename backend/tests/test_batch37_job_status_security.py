"""Batch 37: status endpoint authentication, isolation and safe output."""
import pytest
from fastapi.testclient import TestClient
from app import main
from app.auth import connection

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path / 'private'))
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(main.app) as c:
        yield c

def register(client, email):
    r = client.post('/api/auth/register', json={'email': email, 'password': 'SecurePassword123!'})
    assert r.status_code == 201, r.text
    return r.json()

def seed(uid, rid, state='leased', batch_state='processing'):
    with connection() as db:
        db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                   (uid, rid, 'PRIVATE-FINGERPRINT', batch_state, 100))
        db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (uid, rid, state, 'PRIVATE-LEASE' if state == 'leased' else None, 200 if state == 'leased' else None, 1, 100, 100))

def test_no_session_and_invalid_session_are_rejected(client):
    owner = register(client, 'batch37auth@example.com')
    seed(owner['user']['id'], 'job37')
    url = '/api/jobs/job37/status'
    assert client.get(url).status_code == 401
    assert client.get(url, headers={'Authorization': 'Bearer invalid'}).status_code == 401
    assert client.get(url, headers={'Authorization': 'Basic invalid'}).status_code == 401

def test_foreign_and_missing_jobs_are_indistinguishable(client):
    owner = register(client, 'batch37owner@example.com')
    stranger = register(client, 'batch37stranger@example.com')
    seed(owner['user']['id'], 'job37')
    headers = {'Authorization': 'Bearer ' + stranger['access_token']}
    foreign = client.get('/api/jobs/job37/status', headers=headers)
    missing = client.get('/api/jobs/never-created/status', headers=headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {'detail': 'Job not found.'}

def test_status_is_read_only_and_has_allowlisted_fields(client):
    owner = register(client, 'batch37private@example.com')
    uid = owner['user']['id']
    seed(uid, 'job37')
    headers = {'Authorization': 'Bearer ' + owner['access_token']}
    with connection() as db:
        before_job = tuple(db.execute('SELECT * FROM analysis_jobs WHERE user_id=?', (uid,)).fetchone())
        before_batch = tuple(db.execute('SELECT * FROM analysis_batches WHERE user_id=?', (uid,)).fetchone())
        before_credits = [tuple(row) for row in db.execute('SELECT * FROM credit_accounts ORDER BY user_id')]
    for _ in range(3):
        r = client.get('/api/jobs/job37/status', headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {'status': 'processing'}
        assert set(r.json()) == {'status'}
        for secret in (uid, 'job37', 'PRIVATE-LEASE', 'PRIVATE-FINGERPRINT'):
            assert secret not in r.text
    with connection() as db:
        assert tuple(db.execute('SELECT * FROM analysis_jobs WHERE user_id=?', (uid,)).fetchone()) == before_job
        assert tuple(db.execute('SELECT * FROM analysis_batches WHERE user_id=?', (uid,)).fetchone()) == before_batch
        assert [tuple(row) for row in db.execute('SELECT * FROM credit_accounts ORDER BY user_id')] == before_credits

def test_oversized_request_id_returns_generic_not_found(client):
    owner = register(client, 'batch37length@example.com')
    headers = {'Authorization': 'Bearer ' + owner['access_token']}
    r = client.get('/api/jobs/' + ('x' * 101) + '/status', headers=headers)
    assert r.status_code == 404
    assert r.json() == {'detail': 'Job not found.'}
