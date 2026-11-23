"""Batch 40: bounded polling, owner isolation and read-only behavior."""
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
        accounts = []
        for email in ('batch40owner@example.com', 'batch40other@example.com'):
            response = client.post('/api/auth/register', json={
                'email': email, 'password': 'SecurePassword123!'
            })
            assert response.status_code == 201, response.text
            accounts.append(response.json())
        owner, other = accounts
        with connection() as db:
            for index in range(20):
                request_id = f'job40-{index}'
                db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                           (owner['user']['id'], request_id, 'private-fingerprint', 'processing', 100))
                db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                           (owner['user']['id'], request_id, 'queued', 100, 100))
        yield client, owner, other


def auth(account):
    return {'Authorization': 'Bearer ' + account['access_token']}


def snapshot():
    with connection() as db:
        return {
            'jobs': [tuple(row) for row in db.execute('SELECT * FROM analysis_jobs ORDER BY user_id,request_id')],
            'batches': [tuple(row) for row in db.execute('SELECT * FROM analysis_batches ORDER BY user_id,request_id')],
            'credits': [tuple(row) for row in db.execute('SELECT * FROM credit_accounts ORDER BY user_id')],
        }


def test_polling_many_jobs_does_not_mutate_data(setup):
    client, owner, _ = setup
    before = snapshot()
    for _ in range(3):
        for index in range(20):
            response = client.get(f'/api/jobs/job40-{index}/status', headers=auth(owner))
            assert response.status_code == 200, response.text
            assert response.json() == {'status': 'queued'}
            assert 'private-fingerprint' not in response.text
            assert owner['user']['id'] not in response.text
    assert snapshot() == before


def test_cross_account_polling_does_not_disclose_jobs(setup):
    client, _, other = setup
    before = snapshot()
    for index in range(20):
        forbidden = client.get(f'/api/jobs/job40-{index}/status', headers=auth(other))
        missing = client.get(f'/api/jobs/missing40-{index}/status', headers=auth(other))
        assert forbidden.status_code == missing.status_code == 404
        assert forbidden.json() == missing.json() == {'detail': 'Job not found.'}
    assert snapshot() == before


def test_status_queries_use_owner_and_request_id_filters(setup, monkeypatch):
    # Assert the query plan can use a key lookup, rather than asserting wall-clock
    # timings that vary across CI machines.
    from app.auth import connection
    with connection() as db:
        plan = db.execute('''EXPLAIN QUERY PLAN SELECT j.state, b.state AS batch_state
            FROM analysis_jobs j JOIN analysis_batches b
            ON j.user_id=b.user_id AND j.request_id=b.request_id
            WHERE j.user_id=? AND j.request_id=?''',
            ('owner', 'job40-0')).fetchall()
    detail = ' '.join(str(row['detail']) for row in plan).upper()
    assert 'SEARCH' in detail
    assert 'SCAN J' not in detail
    assert 'SCAN B' not in detail
