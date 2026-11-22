"""Batch 35: read-only diagnostics and queue recovery invariants."""
import pytest
from fastapi.testclient import TestClient
from app import main
from app.auth import connection
from app.job_queue import heartbeat, finish_job, expire_leases_for_maintenance
from app.worker_monitoring import inspect_worker_health

@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path / 'private'))
    with TestClient(main.app) as client:
        result = client.post('/api/auth/register', json={'email':'batch35@example.com','password':'SecurePassword123!'})
        assert result.status_code == 201, result.text
        uid = result.json()['user']['id']
        with connection() as db:
            db.execute("INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)", (uid,'job35','private-fingerprint','processing',100))
            db.execute("INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (uid,'job35','leased','private-token',200,1,100,100))
        yield uid

def test_expired_worker_cannot_renew_or_complete(queue):
    uid = queue
    assert heartbeat(uid,'job35','private-token',now=200) is False
    assert finish_job(uid,'job35','private-token',now=200) is False
    assert heartbeat(uid,'job35','wrong-token',now=199) is False
    with connection() as db:
        row = db.execute("SELECT state,lease_token,lease_until FROM analysis_jobs WHERE request_id='job35'").fetchone()
        assert tuple(row) == ('leased','private-token',200)

def test_expiration_is_idempotent_and_does_not_change_credits(queue):
    uid = queue
    with connection() as db:
        before = db.execute('SELECT user_id,balance FROM credit_accounts ORDER BY user_id').fetchall()
    assert expire_leases_for_maintenance(now=200) == 1
    assert expire_leases_for_maintenance(now=200) == 0
    assert finish_job(uid,'job35','private-token',now=199) is False
    with connection() as db:
        assert db.execute("SELECT state FROM analysis_jobs WHERE request_id='job35'").fetchone()[0] == 'interrupted'
        assert db.execute('SELECT user_id,balance FROM credit_accounts ORDER BY user_id').fetchall() == before

def test_monitoring_is_read_only_and_private(queue):
    uid = queue
    with connection() as db:
        before = tuple(db.execute("SELECT * FROM analysis_jobs WHERE request_id='job35'").fetchone())
    report = inspect_worker_health(now=200)
    assert report['jobs']['leased'] == 1
    assert report['expired_leases'] == 1
    for secret in (uid, 'job35', 'private-token', 'private-fingerprint'):
        assert secret not in repr(report)
    with connection() as db:
        assert tuple(db.execute("SELECT * FROM analysis_jobs WHERE request_id='job35'").fetchone()) == before
