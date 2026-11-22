
import sqlite3
from fastapi.testclient import TestClient
from app import main
from app.auth import connection

def test_fresh_startup_initializes_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path/'fresh.sqlite'))
    with TestClient(main.app) as client:
        r=client.post('/api/auth/register',json={'email':'batch26@example.com','password':'SecurePassword123!'})
        assert r.status_code==201
        token=r.json()['access_token']
        status=client.get('/api/auth/verification-status',headers={'Authorization':'Bearer '+token})
        assert status.status_code==200
        assert status.json()=={'email_verified':False}
        with connection() as db:
            assert db.execute("SELECT 1 FROM sqlite_master WHERE name='account_tokens'").fetchone()

def test_legacy_user_preserved(tmp_path, monkeypatch):
    path=tmp_path/'legacy.sqlite'
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(path))
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE users (id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,created_at INTEGER NOT NULL)')
        db.execute("INSERT INTO users VALUES ('legacy','legacy@example.com','existing-hash',1)")
    with TestClient(main.app):
        with connection() as db:
            row=db.execute("SELECT email_verified,password_hash FROM users WHERE id='legacy'").fetchone()
            assert row['email_verified']==0
            assert row['password_hash']=='existing-hash'

def test_restart_keeps_verification(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE',str(tmp_path/'restart.sqlite'))
    with TestClient(main.app) as client:
        r=client.post('/api/auth/register',json={'email':'restart26@example.com','password':'SecurePassword123!'})
        assert r.status_code==201
        token=r.json()['access_token']
        with connection() as db:
            db.execute("UPDATE users SET email_verified=1 WHERE email='restart26@example.com'")
    with TestClient(main.app) as client:
        r=client.get('/api/auth/verification-status',headers={'Authorization':'Bearer '+token})
        assert r.status_code==200
        assert r.json()=={'email_verified':True}
