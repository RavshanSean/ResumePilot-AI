"""Batch 36: authenticated job status with strict account isolation."""
import pytest
from fastapi.testclient import TestClient
from app import main
from app.auth import connection

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path/'private'))
    with TestClient(main.app) as c:
        yield c

def register(client, email):
    response=client.post('/api/auth/register', json={'email':email,'password':'SecurePassword123!'})
    assert response.status_code == 201, response.text
    return response.json()

def test_auth_isolation_and_read_only(client):
    owner=register(client,'batch36owner@example.com')
    other=register(client,'batch36other@example.com')
    uid=owner['user']['id']
    with connection() as db:
        db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)', (uid,'secret-job','secret-fingerprint','processing',100))
        db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,lease_token,lease_until,attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (uid,'secret-job','leased','secret-token',200,1,100,100))
        before=tuple(db.execute('SELECT * FROM analysis_jobs').fetchone())
    url='/api/jobs/secret-job/status'
    assert client.get(url).status_code == 401
    assert client.get(url,headers={'Authorization':'Bearer '+other['access_token']}).status_code == 404
    r=client.get(url,headers={'Authorization':'Bearer '+owner['access_token']})
    assert r.status_code == 200, r.text
    assert r.json() == {'status':'processing'}
    for private in (uid,'secret-job','secret-token','secret-fingerprint'):
        assert private not in r.text
    with connection() as db:
        assert tuple(db.execute('SELECT * FROM analysis_jobs').fetchone()) == before

def test_status_mapping_and_missing(client):
    owner=register(client,'batch36states@example.com')
    uid=owner['user']['id']
    headers={'Authorization':'Bearer '+owner['access_token']}
    with connection() as db:
        for i,(job_state,batch_state,expected) in enumerate((('queued','processing','queued'),('completed','completed','completed'),('interrupted','interrupted','interrupted'))):
            rid=f'job-{i}'
            db.execute('INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,?,?)',(uid,rid,'hash',batch_state,100))
            db.execute('INSERT INTO analysis_jobs(user_id,request_id,state,created_at,updated_at) VALUES(?,?,?,?,?)',(uid,rid,job_state,100,100))
            db.commit()
            assert client.get(f'/api/jobs/{rid}/status',headers=headers).json() == {'status':expected}
    assert client.get('/api/jobs/unknown/status',headers=headers).status_code == 404
