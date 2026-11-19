import os
import logging
import pytest
from fastapi.testclient import TestClient
from app import main
from app.privacy_logging import audit_event
from app.temporary_storage import store_resume, read_resume, storage_root

@pytest.fixture
def account(tmp_path,monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE',str(tmp_path/'private'))
    with TestClient(main.app) as client:
        response=client.post('/api/auth/register',json={'email':'privacy25@example.com','password':'SecurePassword123!'})
        assert response.status_code==201
        yield response.json()['user']['id'],tmp_path,monkeypatch

def test_audit_allowlist(caplog):
    with caplog.at_level(logging.INFO,logger='resumepilot.audit'):
        audit_event('resume_batch_completed',files=2,errors=0)
    assert 'event=resume_batch_completed' in caplog.text
    for kwargs in ({'filename':'secret.pdf'},{'files':'secret'},{'files':True},{'files':-1}):
        with pytest.raises(ValueError):
            audit_event('resume_batch_completed',**kwargs)
    with pytest.raises(ValueError):
        audit_event('private resume content')

def test_read_rejects_world_readable_file(account):
    uid,_,_=account
    fid=store_resume(uid,'private.pdf',b'private')
    path=storage_root()/(fid+'.upload')
    path.chmod(0o644)
    with pytest.raises(OSError,match='permissions'):
        read_resume(uid,fid)
    path.chmod(0o600)
    assert read_resume(uid,fid)[1]==b'private'

def test_read_rejects_hardlinks(account):
    uid,root,_=account
    fid=store_resume(uid,'private.pdf',b'private')
    os.link(storage_root()/(fid+'.upload'),root/'hardlink')
    with pytest.raises(OSError,match='Unsafe'):
        read_resume(uid,fid)

def test_root_rejects_symlinked_parent(account):
    _,root,monkeypatch=account
    real=root/'real'
    real.mkdir()
    link=root/'link'
    link.symlink_to(real,target_is_directory=True)
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE',str(link/'uploads'))
    with pytest.raises(RuntimeError,match='symlink'):
        storage_root()
