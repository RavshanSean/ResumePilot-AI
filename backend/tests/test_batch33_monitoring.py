"""Batch 33: aggregate diagnostics must be read-only and privacy-safe."""
import pytest
from fastapi.testclient import TestClient
from app import main
from app.auth import connection
from app.worker_monitoring import inspect_worker_health


def test_monitoring_counts_and_privacy(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path/'private'))
    with TestClient(main.app) as client:
        response = client.post('/api/auth/register', json={'email':'batch33@example.com','password':'SecurePassword123!'})
        assert response.status_code == 201
        uid = response.json()['user']['id']
        with connection() as db:
            db.execute("INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)", (uid,'private-request','abc','processing',10))
            db.execute("INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (uid,'private-request','leased','SECRET-TOKEN',100,1,10,10))
        before = None
        with connection() as db:
            before = tuple(db.execute('SELECT state,lease_token,lease_until FROM analysis_jobs').fetchone())
        report = inspect_worker_health(now=100)
        assert report['jobs']['leased'] == 1
        assert report['expired_leases'] == 1
        assert report['stale_processing_batches_24h'] == 0
        assert 'SECRET-TOKEN' not in repr(report)
        assert uid not in repr(report)
        assert 'private-request' not in repr(report)
        with connection() as db:
            assert tuple(db.execute('SELECT state,lease_token,lease_until FROM analysis_jobs').fetchone()) == before
        assert inspect_worker_health(now=86411)['stale_processing_batches_24h'] == 1


def test_monitoring_rejects_noninteger_time():
    with pytest.raises(ValueError):
        inspect_worker_health(now=True)
