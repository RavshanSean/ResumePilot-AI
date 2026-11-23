"""Batch 49: real SQLite and private-file failure recovery regression tests.

All tests use isolated temporary accounts and never enable a worker.
"""
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import async_routes, job_queue, temporary_storage
from app.auth import connection
from app.credits import balance


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'recovery.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path / 'private'))
    monkeypatch.setenv('RESUMEPILOT_ENABLE_ASYNC_SUBMISSION', '1')
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(app) as client:
        response = client.post('/api/auth/register', json={
            'email': 'recovery@example.com', 'password': 'SecurePassword123!',
        })
        assert response.status_code == 201, response.text
        body = response.json()
        yield client, body['user']['id'], {'Authorization': 'Bearer ' + body['access_token']}, tmp_path


def submit(client, headers, request_id, count=2):
    return client.post('/api/jobs/submit', headers=headers,
        data={'requirements': 'Python', 'request_id': request_id},
        files=[('files', (f'{i}.pdf', io.BytesIO(b'%PDF-1.4'), 'application/pdf'))
               for i in range(count)])


def rows(uid, request_id):
    with connection() as db:
        batch = db.execute('SELECT COUNT(*) FROM analysis_batches WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()[0]
        job = db.execute('SELECT COUNT(*) FROM analysis_jobs WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()[0]
        manifest = db.execute('SELECT COUNT(*) FROM analysis_job_inputs WHERE user_id=? AND request_id=?', (uid, request_id)).fetchone()[0]
        files = db.execute('SELECT COUNT(*) FROM temporary_resume_files WHERE user_id=?', (uid,)).fetchone()[0]
    return batch, job, manifest, files


def test_second_private_write_failure_removes_first_file_and_refunds(environment):
    client, uid, headers, tmp_path = environment
    before = balance(uid)
    original = temporary_storage.store_resume
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('simulated second-file failure')
        return original(*args, **kwargs)

    with patch.object(temporary_storage, 'store_resume', side_effect=fail_second):
        with pytest.raises(OSError, match='simulated second-file failure'):
            submit(client, headers, 'second-fails')
    assert calls == 2
    assert balance(uid) == before
    assert rows(uid, 'second-fails') == (0, 0, 0, 0)
    assert list((tmp_path / 'private').glob('*.upload')) == []
    with connection() as db:
        states = [r['state'] for r in db.execute(
            "SELECT state FROM credit_operations WHERE user_id=? AND request_id IN (?,?) ORDER BY request_id",
            (uid, 'second-fails:0', 'second-fails:1'))]
    assert states == ['released', 'released']


def test_manifest_transaction_failure_cleans_all_staged_files(environment):
    client, uid, headers, tmp_path = environment
    before = balance(uid)
    original = job_queue.connection
    # Fail only the final manifest publication, after both private files exist.
    # The real connection and SQLite schema remain in use.
    class ManifestFailure(RuntimeError):
        pass

    def fail_manifest(*args, **kwargs):
        raise ManifestFailure('manifest publication unavailable')

    # stage_job_inputs calls its module's connection for preflight and publication;
    # inject failure on the second call, leaving store_resume's real DB untouched.
    calls = 0
    def staged_connection():
        nonlocal calls
        calls += 1
        if calls == 2:
            return fail_manifest()
        return original()

    with patch.object(job_queue, 'connection', side_effect=staged_connection):
        with pytest.raises(ManifestFailure):
            submit(client, headers, 'manifest-fails')
    assert balance(uid) == before
    assert rows(uid, 'manifest-fails') == (0, 0, 0, 0)
    assert list((tmp_path / 'private').glob('*.upload')) == []


def test_released_reservation_prevents_reuse_of_failed_id(environment):
    client, uid, headers, _ = environment
    before = balance(uid)
    with patch.object(async_routes, 'stage_job_inputs', side_effect=OSError('staging failed')):
        with pytest.raises(OSError):
            submit(client, headers, 'failed-id', count=1)
    assert balance(uid) == before
    response = submit(client, headers, 'failed-id', count=1)
    assert response.status_code == 409
    assert balance(uid) == before
    assert rows(uid, 'failed-id') == (0, 0, 0, 0)


def test_staged_submission_cannot_be_rolled_back(environment):
    client, uid, headers, _ = environment
    before = balance(uid)
    response = submit(client, headers, 'staged', count=1)
    assert response.status_code == 202, response.text
    assert async_routes.rollback_failed_submission(uid, 'staged') is False
    assert balance(uid) == before - 1
    assert rows(uid, 'staged') == (1, 1, 1, 1)
