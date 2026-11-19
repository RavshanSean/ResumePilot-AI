"""Opt-in terminal retention and orphan cleanup tests."""
import os
import pytest
from fastapi.testclient import TestClient
from app import main, credits
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch
from app.job_queue import stage_job_inputs, claim_staged_job
from app.worker_settlement import settle_leased_batch
from app.temporary_storage import storage_root, read_resume
from app.retention_maintenance import (
    inspect_terminal_staging, cleanup_terminal_staging,
    reconcile_orphans, RetentionMaintenanceError,
)


@pytest.fixture
def setup_job(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE',str(tmp_path/'private'))
    with TestClient(main.app) as client:
        r=client.post('/api/auth/register',json={'email':'retention24@example.com','password':'SecurePassword123!'})
        assert r.status_code==201, r.text
        uid=r.json()['user']['id']
        uploads=[('resume.pdf',b'private bytes')]
        claim_and_reserve(uid,'job24',fingerprint_batch('Python',uploads),1)
        ids=stage_job_inputs(uid,'job24','Python',uploads)
        yield uid,ids[0]


def terminal(uid):
    lease=claim_staged_job()
    assert lease and lease['user_id']==uid
    settle_leased_batch(uid,'job24',lease['lease_token'],
                        {'candidates':[],'errors':[]},[True])


def test_requires_explicit_offline_confirmation(setup_job):
    with pytest.raises(RetentionMaintenanceError):
        cleanup_terminal_staging()
    with pytest.raises(RetentionMaintenanceError):
        reconcile_orphans()


def test_active_job_not_deleted(setup_job):
    uid,fid=setup_job
    assert cleanup_terminal_staging(workers_stopped=True,grace_seconds=0)=={'jobs':0,'files':0}
    assert read_resume(uid,fid)[1]==b'private bytes'


def test_completed_cleanup_preserves_result_and_credit(setup_job):
    uid,fid=setup_job
    terminal(uid)
    before=credits.balance(uid)
    assert inspect_terminal_staging(grace_seconds=0)['completed']['files']==1
    assert cleanup_terminal_staging(workers_stopped=True,grace_seconds=0)=={'jobs':1,'files':1}
    assert credits.balance(uid)==before
    with connection() as db:
        assert db.execute("SELECT state FROM analysis_batches WHERE user_id=?",(uid,)).fetchone()[0]=='completed'
        assert db.execute("SELECT COUNT(*) FROM analysis_job_inputs").fetchone()[0]==0
    assert not (storage_root()/(fid+'.upload')).exists()
    assert cleanup_terminal_staging(workers_stopped=True,grace_seconds=0)=={'jobs':0,'files':0}


def test_interrupted_requires_both_terminal_states(setup_job):
    uid,fid=setup_job
    with connection() as db:
        db.execute("UPDATE analysis_jobs SET state='interrupted' WHERE user_id=?",(uid,))
    assert cleanup_terminal_staging(workers_stopped=True,grace_seconds=0)['jobs']==0
    with connection() as db:
        db.execute("UPDATE analysis_batches SET state='interrupted' WHERE user_id=?",(uid,))
    assert cleanup_terminal_staging(workers_stopped=True,grace_seconds=0)['files']==1


def test_orphan_reconciliation_preserves_tracked_and_recent(setup_job):
    uid,fid=setup_job
    root=storage_root()
    old=root/('a'*48+'.upload')
    recent=root/('b'*48+'.upload')
    old.write_bytes(b'orphan')
    recent.write_bytes(b'fresh')
    os.utime(old,(100,100))
    os.utime(recent,(10000,10000))
    assert reconcile_orphans(workers_stopped=True,now=11000,min_age_seconds=3600)==1
    assert not old.exists()
    assert recent.exists()
    assert (root/(fid+'.upload')).exists()


def test_orphan_symlink_not_followed(setup_job,tmp_path):
    root=storage_root()
    outside=tmp_path/'outside'
    outside.write_text('safe')
    link=root/('c'*48+'.upload')
    link.symlink_to(outside)
    assert reconcile_orphans(workers_stopped=True,now=9999999999)==0
    assert outside.read_text()=='safe'


def test_invalid_limits_rejected(setup_job):
    with pytest.raises(ValueError):
        cleanup_terminal_staging(workers_stopped=True,limit=0)
    with pytest.raises(ValueError):
        reconcile_orphans(workers_stopped=True,min_age_seconds=1)
