"""Atomic fenced settlement regression tests."""
import pytest
from fastapi.testclient import TestClient
from app import main, credits
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch, claim_and_reserve as replay_claim
from app.job_queue import stage_job_inputs, claim_staged_job
from app.worker_settlement import settle_leased_batch, WorkerSettlementError


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path/'private'))
    with TestClient(main.app) as client:
        r=client.post('/api/auth/register',json={'email':'settle22@example.com','password':'SecurePassword123!'})
        assert r.status_code==201,r.text
        uid=r.json()['user']['id']
        uploads=[('a.pdf',b'one'),('b.docx',b'two')]
        digest=fingerprint_batch('Python',uploads)
        claim_and_reserve(uid,'batch',digest,2)
        stage_job_inputs(uid,'batch','Python',uploads)
        lease=claim_staged_job()
        yield uid,lease['lease_token'],digest


def response():
    return {'candidates':[], 'errors':[], 'notice':'Human review required.'}


def states(uid):
    with connection() as db:
        return ([r[0] for r in db.execute("SELECT state FROM credit_operations WHERE user_id=? ORDER BY request_id",(uid,))],
                db.execute("SELECT state FROM analysis_batches WHERE user_id=?",(uid,)).fetchone()[0],
                db.execute("SELECT state FROM analysis_jobs WHERE user_id=?",(uid,)).fetchone()[0])


def test_atomic_mixed_success_refund_and_replay(job):
    uid,token,digest=job
    assert credits.balance(uid)==3
    assert settle_leased_batch(uid,'batch',token,response(),[True,False])
    assert credits.balance(uid)==4
    assert states(uid)==(['completed','released'],'completed','completed')
    assert replay_claim(uid,'batch',digest,2)==response()


def test_wrong_token_cannot_charge_or_complete(job):
    uid,_,_=job
    with pytest.raises(WorkerSettlementError): settle_leased_batch(uid,'batch','wrong',response(),[True,True])
    assert states(uid)==(['reserved','reserved'],'processing','leased')
    assert credits.balance(uid)==3


def test_expired_lease_cannot_settle(job):
    uid,token,_=job
    with connection() as db:
        db.execute('UPDATE analysis_jobs SET lease_until=0 WHERE user_id=?',(uid,))
    with pytest.raises(WorkerSettlementError): settle_leased_batch(uid,'batch',token,response(),[True,True])
    assert states(uid)==(['reserved','reserved'],'processing','leased')


def test_missing_reservation_rolls_back_all_changes(job):
    uid,token,_=job
    with connection() as db:
        db.execute("UPDATE credit_operations SET state='released' WHERE user_id=? AND request_id='batch:1'",(uid,))
    with pytest.raises(WorkerSettlementError): settle_leased_batch(uid,'batch',token,response(),[False,True])
    assert states(uid)==(['reserved','released'],'processing','leased')
    assert credits.balance(uid)==3


def test_duplicate_completion_cannot_refund_twice(job):
    uid,token,_=job
    settle_leased_batch(uid,'batch',token,response(),[False,False])
    assert credits.balance(uid)==5
    with pytest.raises(WorkerSettlementError): settle_leased_batch(uid,'batch',token,response(),[False,False])
    assert credits.balance(uid)==5


def test_bad_outcome_counts_and_types_rejected(job):
    uid,token,_=job
    for outcomes in ([True],[True,1],[],[True,True,False]):
        with pytest.raises(WorkerSettlementError): settle_leased_batch(uid,'batch',token,response(),outcomes)
    assert states(uid)==(['reserved','reserved'],'processing','leased')


def test_invalid_response_rejected_without_mutations(job):
    uid,token,_=job
    with pytest.raises(WorkerSettlementError): settle_leased_batch(uid,'batch',token,{'candidates':[], 'errors':[], 'bad':float('nan')},[True,True])
    assert states(uid)==(['reserved','reserved'],'processing','leased')
