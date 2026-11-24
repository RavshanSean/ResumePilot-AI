import os
import time
import pytest
from app.auth import connection, initialize_database
from app.temporary_storage import initialize_temporary_storage, store_resume
from app.batch_journal import initialize_batch_journal
from app.job_queue import initialize_job_queue, initialize_job_inputs
from app.storage_recovery import reconcile_orphans


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path / 'test.sqlite'))
    root = tmp_path / 'private'
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(root))
    initialize_database()
    initialize_batch_journal()
    initialize_job_queue()
    initialize_temporary_storage()
    initialize_job_inputs()
    with connection() as db:
        db.execute('INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)', ('owner','owner@example.com','unused',0))
    fid = store_resume('owner','cv.pdf',b'%PDF-1.4')
    return root, root / (fid + '.upload')


def age(path):
    old = time.time() - 172800
    os.utime(path, (old, old))


def test_maintenance_confirmation_required(storage):
    with pytest.raises(ValueError):
        reconcile_orphans()


def test_dry_run_and_cleanup_preserve_owned_recent_and_unknown_files(storage):
    root, owned = storage
    orphan = root / ('a'*48 + '.upload')
    recent = root / ('b'*48 + '.upload')
    unknown = root / 'unrelated.txt'
    for path in (orphan, recent, unknown):
        path.write_bytes(b'test')
    for path in (owned, orphan, unknown):
        age(path)
    assert reconcile_orphans(maintenance_confirmed=True) == {'found':1,'removed':0,'failed':0}
    assert orphan.exists()
    assert reconcile_orphans(maintenance_confirmed=True, dry_run=False) == {'found':1,'removed':1,'failed':0}
    assert not orphan.exists()
    assert owned.exists() and recent.exists() and unknown.exists()
    assert reconcile_orphans(maintenance_confirmed=True, dry_run=False)['removed'] == 0


def test_symlink_is_not_followed(storage):
    root, owned = storage
    link = root / ('c'*48 + '.upload')
    link.symlink_to(owned)
    assert reconcile_orphans(maintenance_confirmed=True, dry_run=False)['removed'] == 0
    assert link.is_symlink() and owned.exists()
