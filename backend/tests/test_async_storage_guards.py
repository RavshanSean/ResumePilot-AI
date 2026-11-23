"""Batch 50: real private-file ownership, integrity and recovery guards."""
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import job_queue, temporary_storage
from app.auth import connection
from app.credits import balance


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'storage.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path / 'private'))
    monkeypatch.setenv('RESUMEPILOT_ENABLE_ASYNC_SUBMISSION', '1')
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(app) as client:
        accounts = []
        for i in range(2):
            response = client.post('/api/auth/register', json={
                'email': f'storage{i}@example.com', 'password': 'SecurePassword123!',
            })
            assert response.status_code == 201, response.text
            body = response.json()
            accounts.append((body['user']['id'], {'Authorization': 'Bearer ' + body['access_token']}))
        yield client, accounts, tmp_path


def submit(client, headers, request_id='storage-job'):
    return client.post('/api/jobs/submit', headers=headers,
        data={'requirements': 'Python security', 'request_id': request_id},
        files={'files': ('cv.pdf', io.BytesIO(b'%PDF-1.4 test resume'), 'application/pdf')})


def test_owner_can_read_staged_data_but_other_user_cannot(environment):
    client, ((owner, headers), (other, _)), _ = environment
    response = submit(client, headers)
    assert response.status_code == 202, response.text
    requirements, files = job_queue.read_staged_job(owner, 'storage-job')
    assert requirements == 'Python security'
    assert files == [('cv.pdf', b'%PDF-1.4 test resume')]
    with pytest.raises(FileNotFoundError):
        job_queue.read_staged_job(other, 'storage-job')
    with connection() as db:
        fid = db.execute('SELECT file_id FROM analysis_job_inputs WHERE user_id=? AND request_id=?',
                         (owner, 'storage-job')).fetchone()['file_id']
    with pytest.raises(FileNotFoundError):
        temporary_storage.read_resume(other, fid)


def test_tampered_private_file_is_rejected(environment):
    client, ((owner, headers), _), tmp_path = environment
    assert submit(client, headers).status_code == 202
    with connection() as db:
        fid = db.execute('SELECT file_id FROM analysis_job_inputs WHERE user_id=?',
                         (owner,)).fetchone()['file_id']
    path = tmp_path / 'private' / (fid + '.upload')
    path.write_bytes(b'%PDF-1.4 altered bytes')
    with pytest.raises(OSError, match='integrity'):
        temporary_storage.read_resume(owner, fid)


def test_expired_staged_file_cannot_be_read(environment):
    client, ((owner, headers), _), _ = environment
    assert submit(client, headers).status_code == 202
    with connection() as db:
        row = db.execute('SELECT f.file_id,f.expires_at FROM temporary_resume_files f WHERE f.user_id=?',
                         (owner,)).fetchone()
    with pytest.raises(FileNotFoundError):
        temporary_storage.read_resume(owner, row['file_id'], now=row['expires_at'])
    with pytest.raises(FileNotFoundError):
        job_queue.read_staged_job(owner, 'storage-job', now=row['expires_at'])


def test_failed_staging_does_not_leave_queued_manifest_or_charge(environment):
    client, ((owner, headers), _), tmp_path = environment
    before = balance(owner)
    original = temporary_storage.store_resume
    def fail_after_first_write(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('failure after file write')
    # A crash after store_resume returns but before the caller records its ID
    # can leave an orphan. Verify rollback still refunds credits, and document
    # the orphan risk rather than asserting an unsafe cleanup guarantee.
    with patch.object(temporary_storage, 'store_resume', side_effect=fail_after_first_write):
        with pytest.raises(OSError, match='failure after file write'):
            submit(client, headers, 'orphan-risk')
    assert balance(owner) == before
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM analysis_batches WHERE user_id=? AND request_id=?',
                          (owner, 'orphan-risk')).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM analysis_job_inputs WHERE user_id=? AND request_id=?',
                          (owner, 'orphan-risk')).fetchone()[0] == 0
    # The orphan is a known limitation for a later maintenance/reconciliation batch.
    assert len(list((tmp_path / 'private').glob('*.upload'))) == 1
