"""Batch 20 staged manifest and privacy integration tests."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import main
from app.auth import connection
from app.batch_journal import claim_and_reserve,fingerprint_batch,complete_batch
from app.job_queue import stage_job_inputs,read_staged_job,claim_staged_job
from app.temporary_storage import cleanup_expired,storage_root

@pytest.fixture
def users(tmp_path,monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE',str(tmp_path/'private'))
    with TestClient(main.app) as client:
        result=[]
        for i in range(2):
            response=client.post('/api/auth/register',json={'email':f'batch20{i}@example.com','password':'SecurePassword123!'})
            assert response.status_code==201,response.text
            result.append(response.json()['user']['id'])
        yield result

def prepare(user,key='batch',uploads=None,requirements='Python',count=None):
    uploads=uploads if uploads is not None else [('one.pdf',b'first'),('two.docx',b'second')]
    claim_and_reserve(user,key,fingerprint_batch(requirements,uploads),len(uploads) if count is None else count)
    return uploads

def test_staging_order_and_private_roundtrip(users):
    owner,other=users
    uploads=prepare(owner)
    ids=stage_job_inputs(owner,'batch','Python',uploads)
    assert len(ids)==2 and ids[0]!=ids[1]
    assert read_staged_job(owner,'batch')[1]==uploads
    with pytest.raises(FileNotFoundError): read_staged_job(other,'batch')
    with connection() as db:
        assert db.execute('SELECT count(*) FROM analysis_job_inputs').fetchone()[0]==2

def test_manifest_is_immutable_and_fingerprint_checked(users):
    owner,_=users
    uploads=prepare(owner)
    stage_job_inputs(owner,'batch','Python',uploads)
    with pytest.raises(HTTPException) as error:
        stage_job_inputs(owner,'batch','Python',uploads)
    assert error.value.status_code==409
    with pytest.raises(HTTPException): stage_job_inputs(owner,'batch','Different',uploads)
    with connection() as db:
        assert db.execute('SELECT count(*) FROM temporary_resume_files').fetchone()[0]==2

def test_mismatched_reservations_fail_before_writing(users):
    owner,_=users
    uploads=prepare(owner)
    with connection() as db:
        db.execute("UPDATE credit_operations SET state='released' WHERE user_id=? AND request_id='batch:1'",(owner,))
    with pytest.raises(HTTPException): stage_job_inputs(owner,'batch','Python',uploads)
    with connection() as db:
        assert db.execute('SELECT count(*) FROM temporary_resume_files').fetchone()[0]==0

def test_claim_only_staged_and_no_duplicate_claim(users):
    owner,_=users
    uploads=prepare(owner)
    assert claim_staged_job() is None
    stage_job_inputs(owner,'batch','Python',uploads)
    lease=claim_staged_job()
    assert lease['user_id']==owner
    assert claim_staged_job() is None

def test_expired_staged_inputs_cannot_be_claimed(users):
    owner,_=users
    uploads=prepare(owner)
    stage_job_inputs(owner,'batch','Python',uploads,ttl_seconds=1,now=100)
    assert claim_staged_job(now=101) is None
    with pytest.raises(FileNotFoundError): read_staged_job(owner,'batch',now=101)

def test_invalid_input_rejected_without_side_effects(users):
    owner,_=users
    uploads=prepare(owner)
    with pytest.raises(ValueError): stage_job_inputs(owner,'batch','Python',[('evil.txt',b'abc')])
    with connection() as db:
        assert db.execute('SELECT count(*) FROM temporary_resume_files').fetchone()[0]==0

def test_completed_batch_cannot_be_staged(users):
    owner,_=users
    uploads=prepare(owner)
    complete_batch(owner,'batch',{'candidates':[]})
    with pytest.raises(HTTPException): stage_job_inputs(owner,'batch','Python',uploads)

def test_cleanup_expired_staged_files_restricts_foreign_key(users):
    owner,_=users
    uploads=prepare(owner)
    stage_job_inputs(owner,'batch','Python',uploads,ttl_seconds=1,now=100)
    assert cleanup_expired(now=101)==0
    # Retention cleanup preserves referenced files until manifests are reconciled.
    with connection() as db:
        assert db.execute('SELECT count(*) FROM analysis_job_inputs').fetchone()[0]==2
