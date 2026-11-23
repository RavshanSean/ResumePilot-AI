"""Batch 48: actual SQLite + private storage submission integration tests.

Uses an isolated temporary database, actual authenticated HTTP requests, and
real credit reservation / file staging. No worker is started.
"""
import io
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import async_routes
from app.auth import connection
from app.credits import balance
from app.temporary_storage import store_resume


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'integration.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_ENABLE_ASYNC_SUBMISSION', '1')
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(app) as client:
        identities = []
        for i in range(2):
            response = client.post('/api/auth/register', json={
                'email': f'integration{i}@example.com',
                'password': 'SecurePassword123!',
            })
            assert response.status_code == 201, response.text
            body = response.json()
            identities.append((body['user']['id'], {'Authorization': 'Bearer ' + body['access_token']}))
        yield client, identities


def submit(client, headers, request_id='integration', content=b'%PDF-1.4', filename='cv.pdf'):
    return client.post('/api/jobs/submit', headers=headers,
        data={'requirements': 'Python and security', 'request_id': request_id},
        files={'files': (filename, io.BytesIO(content), 'application/pdf')})


def count(db, table, user_id, request_id):
    return db.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id=? AND request_id=?',
                      (user_id, request_id)).fetchone()[0]


def test_successful_submission_stages_real_private_file(environment):
    client, ((uid, headers), _) = environment
    before = balance(uid)
    response = submit(client, headers)
    assert response.status_code == 202, response.text
    assert response.json() == {'request_id': 'integration', 'status': 'queued'}
    assert balance(uid) == before - 1
    with connection() as db:
        assert count(db, 'analysis_jobs', uid, 'integration') == 1
        assert count(db, 'analysis_job_inputs', uid, 'integration') == 1
        assert count(db, 'analysis_job_requirements', uid, 'integration') == 1
    status = client.get('/api/jobs/integration/status', headers=headers)
    assert status.status_code == 200
    assert status.json() == {'status': 'queued'}


def test_duplicate_id_does_not_charge_again(environment):
    client, ((uid, headers), _) = environment
    assert submit(client, headers).status_code == 202
    after = balance(uid)
    assert submit(client, headers).status_code == 409
    assert balance(uid) == after
    with connection() as db:
        assert count(db, 'analysis_job_inputs', uid, 'integration') == 1


def test_invalid_upload_does_not_reserve(environment):
    client, ((uid, headers), _) = environment
    before = balance(uid)
    assert submit(client, headers, filename='cv.txt').status_code == 400
    assert balance(uid) == before
    with connection() as db:
        assert count(db, 'analysis_batches', uid, 'integration') == 0


def test_failed_staging_refunds_and_removes_batch(environment):
    client, ((uid, headers), _) = environment
    before = balance(uid)
    with patch.object(async_routes, 'stage_job_inputs', side_effect=OSError('simulated disk failure')):
        with pytest.raises(OSError, match='simulated disk failure'):
            submit(client, headers)
    assert balance(uid) == before
    with connection() as db:
        assert count(db, 'analysis_batches', uid, 'integration') == 0
        assert count(db, 'analysis_jobs', uid, 'integration') == 0
        assert db.execute("SELECT state FROM credit_operations WHERE user_id=? AND request_id=?",
                          (uid, 'integration:0')).fetchone()['state'] == 'released'


def test_cross_user_status_is_not_visible(environment):
    client, ((uid, headers), (_, other_headers)) = environment
    assert submit(client, headers).status_code == 202
    response = client.get('/api/jobs/integration/status', headers=other_headers)
    assert response.status_code == 404
    assert balance(uid) == 4


def test_concurrent_duplicate_only_one_reservation(environment):
    client, ((uid, headers), _) = environment
    before = balance(uid)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, client, headers) for _ in range(2)]
        responses = [f.result() for f in futures]
    assert sorted(r.status_code for r in responses) == [202, 409]
    assert balance(uid) == before - 1
    with connection() as db:
        assert count(db, 'analysis_job_inputs', uid, 'integration') == 1
