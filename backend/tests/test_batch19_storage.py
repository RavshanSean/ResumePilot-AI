"""Temporary storage privacy, integrity and retention regression tests."""
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import main
from app.temporary_storage import (store_resume,read_resume,cleanup_expired,storage_root,
    MAX_UPLOAD_BYTES)
from app.auth import connection

@pytest.fixture
def accounts(tmp_path,monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE',str(tmp_path/'private'))
    with TestClient(main.app) as client:
        users=[]
        for i in range(2):
            r=client.post('/api/auth/register',json={'email':f'storage{i}@example.com','password':'SecurePassword123!'})
            assert r.status_code==201,r.text
            users.append(r.json()['user']['id'])
        yield users,tmp_path

def test_roundtrip_and_private_permissions(accounts):
    (user,_),root=accounts
    file_id=store_resume(user,'resume.pdf',b'private resume',now=100)
    assert len(file_id)==48
    assert read_resume(user,file_id,now=101)==('resume.pdf',b'private resume')
    assert storage_root().stat().st_mode & 0o777 == 0o700
    assert (root/'private'/(file_id+'.upload')).stat().st_mode & 0o777 == 0o600

def test_cross_account_and_expiration(accounts):
    (owner,other),_=accounts
    file_id=store_resume(owner,'a.docx',b'abc',ttl_seconds=5,now=100)
    with pytest.raises(FileNotFoundError): read_resume(other,file_id,now=101)
    with pytest.raises(FileNotFoundError): read_resume(owner,file_id,now=105)
    assert read_resume(owner,file_id,now=104)[1]==b'abc'

def test_cleanup_is_bounded_and_idempotent(accounts):
    (owner,_),root=accounts
    ids=[store_resume(owner,f'{i}.pdf',b'abc',ttl_seconds=5,now=100) for i in range(3)]
    assert cleanup_expired(now=105,limit=2)==2
    assert cleanup_expired(now=105,limit=2)==1
    assert cleanup_expired(now=105)==0
    assert not any((root/'private'/(i+'.upload')).exists() for i in ids)
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM temporary_resume_files').fetchone()[0]==0

def test_rejects_unsafe_names_sizes_and_ttl(accounts):
    (owner,_),_=accounts
    for name in ('../evil.pdf','/tmp/evil.pdf','a.txt','', 'a\\b.pdf'):
        with pytest.raises(ValueError): store_resume(owner,name,b'x')
    with pytest.raises(ValueError): store_resume(owner,'ok.pdf',b'')
    with pytest.raises(ValueError): store_resume(owner,'ok.pdf',b'x'*(MAX_UPLOAD_BYTES+1))
    with pytest.raises(ValueError): store_resume(owner,'ok.pdf',b'x',ttl_seconds=0)

def test_integrity_check(accounts):
    (owner,_),root=accounts
    fid=store_resume(owner,'a.pdf',b'original',now=100)
    (root/'private'/(fid+'.upload')).write_bytes(b'changed!')
    with pytest.raises(OSError,match='integrity'):
        read_resume(owner,fid,now=101)

def test_cleanup_does_not_follow_symlinks(accounts):
    (owner,_),root=accounts
    fid=store_resume(owner,'a.pdf',b'data',ttl_seconds=5,now=100)
    outside=root/'outside.txt'
    outside.write_text('keep')
    (root/'private'/(fid+'.upload')).unlink()
    (root/'private'/(fid+'.upload')).symlink_to(outside)
    assert cleanup_expired(now=105)==1
    assert outside.read_text()=='keep'

def test_metadata_is_account_scoped(accounts):
    (owner,other),_=accounts
    fid=store_resume(owner,'private.pdf',b'data')
    with connection() as db:
        row=db.execute('SELECT user_id,filename FROM temporary_resume_files WHERE file_id=?',(fid,)).fetchone()
        assert row['user_id']==owner
        assert row['filename']=='private.pdf'
    with pytest.raises(FileNotFoundError): read_resume(other,fid)
