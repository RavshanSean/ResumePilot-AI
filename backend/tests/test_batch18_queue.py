"""Batch 18 persistent queue and worker lease tests."""
import pytest
from fastapi.testclient import TestClient
from app import main, credits
from app.batch_journal import claim_and_reserve, complete_batch, recover_interrupted_batches
from app.job_queue import claim_job, heartbeat, finish_job, expire_leases_for_maintenance

@pytest.fixture
def user(tmp_path,monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'queue.db'))
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH',raising=False)
    with TestClient(main.app) as client:
        r=client.post('/api/auth/register',json={'email':'queue@example.com','password':'SecurePassword123!'})
        assert r.status_code==201,r.text
        yield r.json()['user']['id']

def test_queue_created_with_atomic_reservation(user):
    claim_and_reserve(user,'job','hash',2)
    with credits.connection() as db:
        row=db.execute("SELECT state,attempts FROM analysis_jobs WHERE user_id=? AND request_id='job'",(user,)).fetchone()
        assert tuple(row)==('queued',0)
    assert credits.balance(user)==3

def test_claim_is_exclusive_and_persistent(user):
    claim_and_reserve(user,'job','hash',1)
    first=claim_job(now=1000)
    assert first['user_id']==user
    assert first['lease_until']==1060
    assert claim_job(now=1001) is None
    with credits.connection() as db:
        assert db.execute("SELECT attempts FROM analysis_jobs WHERE user_id=?",(user,)).fetchone()[0]==1

def test_heartbeat_and_fencing(user):
    claim_and_reserve(user,'job','hash',1)
    lease=claim_job(now=1000)
    assert not heartbeat(user,'job','wrong',now=1001)
    assert heartbeat(user,'job',lease['lease_token'],now=1050)
    assert not finish_job(user,'job','wrong',now=1051)
    assert finish_job(user,'job',lease['lease_token'],now=1051)
    assert not finish_job(user,'job',lease['lease_token'],now=1052)

def test_expired_token_cannot_renew_or_finish(user):
    claim_and_reserve(user,'job','hash',1)
    lease=claim_job(lease_seconds=5,now=1000)
    assert not heartbeat(user,'job',lease['lease_token'],now=1005)
    assert not finish_job(user,'job',lease['lease_token'],now=1005)
    assert expire_leases_for_maintenance(now=1005)==1
    assert expire_leases_for_maintenance(now=1005)==0
    assert claim_job(now=1006) is None
    assert credits.balance(user)==4

def test_synchronous_completion_updates_queue(user):
    claim_and_reserve(user,'job','hash',1)
    credits.finish(user,'job:0',True)
    complete_batch(user,'job',{'candidates':[]})
    with credits.connection() as db:
        assert db.execute("SELECT state FROM analysis_jobs WHERE user_id=?",(user,)).fetchone()[0]=='completed'
    assert claim_job() is None

def test_controlled_recovery_marks_queue_interrupted(user):
    claim_and_reserve(user,'job','hash',2)
    with credits.connection() as db:
        db.execute("UPDATE analysis_batches SET created_at=100 WHERE user_id=?",(user,))
    assert recover_interrupted_batches(now=86502)=={'interrupted_batches':1,'refunded_credits':2}
    with credits.connection() as db:
        assert db.execute("SELECT state FROM analysis_jobs WHERE user_id=?",(user,)).fetchone()[0]=='interrupted'
    assert credits.balance(user)==5

def test_invalid_lease_duration(user):
    with pytest.raises(ValueError):
        claim_job(lease_seconds=0)
