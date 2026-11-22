"""Batch 34: monitoring boundaries and privacy/credit regression checks."""
import pytest
from fastapi.testclient import TestClient
from app import main
from app.auth import connection
from app.worker_monitoring import inspect_worker_health


def test_monitoring_boundary_states_and_no_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path / 'private'))
    with TestClient(main.app) as client:
        response = client.post('/api/auth/register', json={
            'email': 'batch34@example.com', 'password': 'SecurePassword123!'
        })
        assert response.status_code == 201, response.text
        uid = response.json()['user']['id']
        with connection() as db:
            for rid, state, until, created in (
                ('queued-private', 'queued', None, 100),
                ('leased-expired', 'leased', 200, 100),
                ('leased-active', 'leased', 201, 100),
                ('completed-private', 'completed', None, 100),
                ('interrupted-private', 'interrupted', None, 100),
            ):
                batch_state = 'completed' if state == 'completed' else 'interrupted' if state == 'interrupted' else 'processing'
                db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                           (uid, rid, 'fingerprint-private', batch_state, created))
                db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                           (uid, rid, state, 'PRIVATE-LEASE' if state == 'leased' else None, until, 1, created, created))
            before_jobs = [tuple(row) for row in db.execute('SELECT * FROM analysis_jobs ORDER BY request_id')]
            before_batches = [tuple(row) for row in db.execute('SELECT * FROM analysis_batches ORDER BY request_id')]
            before_credits = [tuple(row) for row in db.execute('SELECT * FROM credit_accounts ORDER BY user_id')]
        report = inspect_worker_health(now=200)
        assert report['jobs'] == {'queued': 1, 'leased': 2, 'completed': 1, 'interrupted': 1}
        assert report['expired_leases'] == 1
        assert report['stale_processing_batches_24h'] == 0
        serialized = repr(report)
        for private in (uid, 'PRIVATE-LEASE', 'fingerprint-private', 'leased-expired', 'queued-private'):
            assert private not in serialized
        assert inspect_worker_health(now=86500)['stale_processing_batches_24h'] == 3
        with connection() as db:
            assert [tuple(row) for row in db.execute('SELECT * FROM analysis_jobs ORDER BY request_id')] == before_jobs
            assert [tuple(row) for row in db.execute('SELECT * FROM analysis_batches ORDER BY request_id')] == before_batches
            assert [tuple(row) for row in db.execute('SELECT * FROM credit_accounts ORDER BY user_id')] == before_credits


@pytest.mark.parametrize('invalid', [True, False, 1.5, '200', None if False else []])
def test_monitoring_rejects_invalid_time(invalid):
    with pytest.raises(ValueError):
        inspect_worker_health(now=invalid)
