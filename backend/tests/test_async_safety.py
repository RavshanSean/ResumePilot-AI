"""Batch 47: disabled-by-default and isolated credit rollback invariants."""
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import current_user
from app import async_routes


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('RESUMEPILOT_ENABLE_ASYNC_SUBMISSION', raising=False)
    app.dependency_overrides[current_user] = lambda: {'id': 'owner'}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_async_disabled_by_default_without_reserving(client):
    with patch.object(async_routes, 'claim_and_reserve', side_effect=AssertionError('charged')):
        response = client.post('/api/jobs/submit', data={'requirements': 'Python', 'request_id': 'x'},
                               files={'files': ('cv.pdf', b'%PDF-1.4', 'application/pdf')})
    assert response.status_code == 503


def test_disabled_route_does_not_call_staging(client):
    with patch.object(async_routes, 'stage_job_inputs', side_effect=AssertionError('staged')):
        response = client.post('/api/jobs/submit', data={'requirements': 'Python', 'request_id': 'x'},
                               files={'files': ('cv.pdf', b'%PDF-1.4', 'application/pdf')})
    assert response.status_code == 503


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.sqlite'
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db
    with connect() as db:
        db.executescript('''
            CREATE TABLE credit_accounts(user_id TEXT PRIMARY KEY,balance INTEGER NOT NULL);
            CREATE TABLE credit_operations(user_id TEXT,request_id TEXT,state TEXT,PRIMARY KEY(user_id,request_id));
            CREATE TABLE analysis_batches(user_id TEXT,request_id TEXT,state TEXT,PRIMARY KEY(user_id,request_id));
            CREATE TABLE analysis_jobs(user_id TEXT,request_id TEXT,state TEXT,PRIMARY KEY(user_id,request_id),
                FOREIGN KEY(user_id,request_id) REFERENCES analysis_batches(user_id,request_id) ON DELETE CASCADE);
            CREATE TABLE analysis_job_requirements(user_id TEXT,request_id TEXT,PRIMARY KEY(user_id,request_id));
            INSERT INTO credit_accounts VALUES('owner',7);
            INSERT INTO credit_accounts VALUES('other',3);
            INSERT INTO analysis_batches VALUES('owner','job','processing');
            INSERT INTO analysis_jobs VALUES('owner','job','queued');
            INSERT INTO credit_operations VALUES('owner','job:0','reserved');
            INSERT INTO credit_operations VALUES('owner','job:1','reserved');
            INSERT INTO credit_operations VALUES('owner','job-other:0','reserved');
            INSERT INTO credit_operations VALUES('other','job:0','reserved');
        ''')
    @contextmanager
    def connection():
        db = connect()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    monkeypatch.setattr(async_routes, 'connection', connection)
    return connect


def test_rollback_refunds_only_exact_owner_job(ledger):
    assert async_routes.rollback_failed_submission('owner', 'job') is True
    assert async_routes.rollback_failed_submission('owner', 'job') is False
    with ledger() as db:
        assert db.execute("SELECT balance FROM credit_accounts WHERE user_id='owner'").fetchone()[0] == 9
        assert db.execute("SELECT balance FROM credit_accounts WHERE user_id='other'").fetchone()[0] == 3
        assert db.execute("SELECT state FROM credit_operations WHERE user_id='owner' AND request_id='job:0'").fetchone()[0] == 'released'
        assert db.execute("SELECT state FROM credit_operations WHERE user_id='owner' AND request_id='job-other:0'").fetchone()[0] == 'reserved'
        assert db.execute("SELECT state FROM credit_operations WHERE user_id='other' AND request_id='job:0'").fetchone()[0] == 'reserved'


@pytest.mark.parametrize('blocker', ['leased', 'staged', 'completed'])
def test_rollback_refuses_active_staged_or_completed_jobs(ledger, blocker):
    with ledger() as db:
        if blocker == 'leased':
            db.execute("UPDATE analysis_jobs SET state='leased' WHERE user_id='owner' AND request_id='job'")
        elif blocker == 'staged':
            db.execute("INSERT INTO analysis_job_requirements VALUES('owner','job')")
        else:
            db.execute("UPDATE analysis_batches SET state='completed' WHERE user_id='owner' AND request_id='job'")
        db.commit()
    assert async_routes.rollback_failed_submission('owner', 'job') is False
    with ledger() as db:
        assert db.execute("SELECT balance FROM credit_accounts WHERE user_id='owner'").fetchone()[0] == 7
        assert db.execute("SELECT COUNT(*) FROM credit_operations WHERE user_id='owner' AND state='reserved'").fetchone()[0] == 3
