"""Worker input preparation does not perform AI or charge credits."""
import pytest
from fastapi.testclient import TestClient
from app import main, credits
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch
from app.job_queue import stage_job_inputs, claim_staged_job
from app.background_worker import prepare_leased_job, WorkerPreparationError

@pytest.fixture
def staged(tmp_path,monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE',str(tmp_path/'uploads'))
    with TestClient(main.app) as client:
        r=client.post('/api/auth/register',json={'email':'worker21@example.com','password':'SecurePassword123!'})
        assert r.status_code==201
        uid=r.json()['user']['id']
        uploads=[('a.pdf',b'hello'),('b.docx',b'world')]
        claim_and_reserve(uid,'job',fingerprint_batch('Python',uploads),2)
        stage_job_inputs(uid,'job','Python',uploads)
        yield uid,uploads

def test_preparation_requires_active_lease(staged):
    uid,_=staged
    with pytest.raises(WorkerPreparationError): prepare_leased_job(uid,'job','wrong')

def test_preparation_returns_ordered_verified_inputs_without_charging(staged):
    uid,uploads=staged
    before=credits.balance(uid)
    lease=claim_staged_job()
    result=prepare_leased_job(uid,'job',lease['lease_token'])
    assert result['requirements']=='Python'
    assert result['uploads']==uploads
    assert credits.balance(uid)==before
    with connection() as db:
        assert db.execute("SELECT count(*) FROM credit_operations WHERE state='reserved'").fetchone()[0]==2

def test_wrong_token_rejected(staged):
    uid,_=staged
    claim_staged_job()
    with pytest.raises(WorkerPreparationError): prepare_leased_job(uid,'job','not-owner')

def test_expired_lease_rejected(staged):
    uid,_=staged
    lease=claim_staged_job()
    with connection() as db:
        db.execute("UPDATE analysis_jobs SET lease_until=0 WHERE user_id=?",(uid,))
    with pytest.raises(WorkerPreparationError): prepare_leased_job(uid,'job',lease['lease_token'])

def test_tampered_staged_bytes_rejected(staged):
    uid,_=staged
    lease=claim_staged_job()
    with connection() as db:
        fid=db.execute('SELECT file_id FROM analysis_job_inputs WHERE user_id=? ORDER BY position LIMIT 1',(uid,)).fetchone()[0]
    from app.temporary_storage import storage_root
    (storage_root()/(fid+'.upload')).write_bytes(b'altered')
    with pytest.raises(WorkerPreparationError): prepare_leased_job(uid,'job',lease['lease_token'])
