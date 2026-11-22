"""Batch 32: private-file cleanup must respect remaining manifest references."""
import pytest
import sqlite3
from fastapi.testclient import TestClient
from app import main
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch
from app.job_queue import stage_job_inputs
from app.retention_maintenance import cleanup_terminal_staging
from app.temporary_storage import read_resume, storage_root


def test_cleanup_does_not_unlink_shared_manifest_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RESUMEPILOT_DATABASE", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("RESUMEPILOT_TEMP_STORAGE", str(tmp_path / "private"))
    with TestClient(main.app) as client:
        result = client.post("/api/auth/register", json={
            "email": "batch32@example.com", "password": "SecurePassword123!"
        })
        assert result.status_code == 201
        uid = result.json()["user"]["id"]
        uploads = [("resume.pdf", b"private test bytes")]
        for rid in ("first", "second"):
            claim_and_reserve(uid, rid, fingerprint_batch("Python", uploads), 1)
            stage_job_inputs(uid, rid, "Python", uploads)
        with connection() as db:
            first = db.execute(
                "SELECT file_id FROM analysis_job_inputs WHERE request_id='first'"
            ).fetchone()[0]
            second = db.execute(
                "SELECT file_id FROM analysis_job_inputs WHERE request_id='second'"
            ).fetchone()[0]
            # The database must reject a cross-job shared reference.
            # Each stored file can belong to only one job manifest.
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE analysis_job_inputs SET file_id=? WHERE request_id='second'",
                    (first,),
                )
            db.execute(
                "UPDATE analysis_jobs SET state='completed' WHERE request_id='first'"
            )
            db.execute(
                "UPDATE analysis_batches SET state='completed' WHERE request_id='first'"
            )
        # Completed first job is cleaned; queued second job remains untouched.
        result = cleanup_terminal_staging(workers_stopped=True, grace_seconds=0)
        assert result == {"jobs": 1, "files": 1}
        assert not (storage_root() / (first + ".upload")).exists()
        assert read_resume(uid, second)[1] == b"private test bytes"
        assert (storage_root() / (second + ".upload")).exists()
        with connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM analysis_job_inputs WHERE request_id='second'"
            ).fetchone()[0] == 1
