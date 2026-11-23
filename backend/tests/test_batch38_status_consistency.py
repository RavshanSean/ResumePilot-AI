"""Batch 38: status mapping precedence and isolation regression coverage."""
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

@pytest.mark.parametrize('job_state,batch_state,expected', [
    ('queued', 'processing', 'queued'),
    ('leased', 'processing', 'processing'),
    ('completed', 'completed', 'completed'),
    ('interrupted', 'interrupted', 'interrupted'),
    ('leased', 'completed', 'completed'),
    ('queued', 'interrupted', 'interrupted'),
    ('interrupted', 'completed', 'completed'),
    ('completed', 'interrupted', 'interrupted'),
])
def test_status_mapping_and_terminal_batch_precedence(client, job_state, batch_state, expected):
    owner = register(client, 'batch38@example.com')
    uid = owner['user']['id']
    with connection() as db:
        db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                   (uid, 'job38', 'private-fingerprint', batch_state, 100))
        db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (uid, 'job38', job_state, 'private-token' if job_state == 'leased' else None,
                    200 if job_state == 'leased' else None, 1, 100, 100))
    headers = {'Authorization': 'Bearer ' + owner['access_token']}
    response = client.get('/api/jobs/job38/status', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {'status': expected}
    assert 'private-' not in response.text
    assert uid not in response.text

def test_missing_job_and_foreign_job_remain_identical(client):
    owner = register(client, 'batch38owner@example.com')
    other = register(client, 'batch38other@example.com')
    with connection() as db:
        db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                   (owner['user']['id'], 'job38', 'private', 'processing', 100))
        db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                   (owner['user']['id'], 'job38', 'queued', 100, 100))
    headers = {'Authorization': 'Bearer ' + other['access_token']}
    foreign = client.get('/api/jobs/job38/status', headers=headers)
    missing = client.get('/api/jobs/missing/status', headers=headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {'detail': 'Job not found.'}
