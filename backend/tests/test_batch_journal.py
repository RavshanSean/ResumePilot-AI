"""Batch journal regression tests."""
import io
import pymupdf
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app import main, credits
from app.batch_journal import fingerprint_batch, begin_batch, complete_batch

@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path/'batch.db'))
    monkeypatch.delenv('RESUMEPILOT_TEST_BYPASS_AUTH', raising=False)
    with TestClient(main.app) as client:
        response = client.post('/api/auth/register',json={'email':'batch@example.com','password':'SecurePassword123!'})
        assert response.status_code == 201, response.text
        body = response.json()
        yield client,body['user']['id'],{'Authorization':'Bearer '+body['access_token']}

def pdf(text):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72,72),text)
        return doc.tobytes()

def test_fingerprint_changes_with_content():
    first=fingerprint_batch('Python',[('a.pdf',b'one')])
    assert first==fingerprint_batch('Python',[('a.pdf',b'one')])
    assert first!=fingerprint_batch('Python',[('a.pdf',b'two')])
    assert first!=fingerprint_batch('Java',[('a.pdf',b'one')])

def test_replay(account):
    _,uid,_=account
    digest=fingerprint_batch('Python',[('a.pdf',b'data')])
    assert begin_batch(uid,'one',digest) is None
    result={'candidates':[{'id':'0'}],'errors':[]}
    complete_batch(uid,'one',result)
    assert begin_batch(uid,'one',digest)==result
    assert credits.balance(uid)==5

def test_duplicate_processing(account):
    _,uid,_=account
    digest=fingerprint_batch('Python',[('a.pdf',b'data')])
    begin_batch(uid,'one',digest)
    with pytest.raises(HTTPException) as error:
        begin_batch(uid,'one',digest)
    assert error.value.status_code==409

def test_different_payload_conflicts(account):
    _,uid,_=account
    begin_batch(uid,'one',fingerprint_batch('Python',[('a.pdf',b'data')]))
    with pytest.raises(HTTPException) as error:
        begin_batch(uid,'one',fingerprint_batch('Java',[('a.pdf',b'data')]))
    assert error.value.status_code==409

def test_api_retry_only_charges_once(account,monkeypatch):
    client,uid,headers=account
    calls=[]
    async def matcher(requirements,resume_text):
        calls.append(resume_text)
        return {'category':'Review First','summary':'Evidence found','requirements':[], 'supported_count':1,'total_requirements':1}
    monkeypatch.setattr(main,'match_resume',matcher)
    data=pdf('Python engineer')
    def submit(requirements='Python'):
        return client.post('/api/analyze',headers=headers,data={'requirements':requirements,'request_id':'stable'},files={'files':('candidate.pdf',io.BytesIO(data),'application/pdf')})
    first=submit()
    assert first.status_code==200,first.text
    second=submit()
    assert second.status_code==200,second.text
    assert first.json()==second.json()
    assert len(calls)==1
    assert credits.balance(uid)==4
    assert submit('Java').status_code==409
    assert credits.balance(uid)==4

def test_insufficient_credit_claim_is_released(account):
    client,uid,headers=account
    data=pdf('Python engineer')
    result=client.post('/api/analyze',headers=headers,data={'requirements':'Python','request_id':'six'},files=[('files',(f'{i}.pdf',data,'application/pdf')) for i in range(6)])
    assert result.status_code==402,result.text
    assert credits.balance(uid)==5
    with credits.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM analysis_batches WHERE request_id='six'").fetchone()[0]==0
