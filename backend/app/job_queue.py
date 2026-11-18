"""SQLite durable job metadata and fenced worker leases.

Development foundation only. Uploaded resume bytes are NOT persisted here;
workers must not automatically process these jobs yet.
"""
import secrets
import time
from app.auth import connection

MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 3600


def initialize_job_queue():
    with connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_jobs (
            user_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('queued','leased','completed','interrupted')),
            lease_token TEXT,
            lease_until INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,request_id),
            FOREIGN KEY(user_id,request_id) REFERENCES analysis_batches(user_id,request_id) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS analysis_jobs_claim_idx ON analysis_jobs(state,created_at)")


def enqueue_in_transaction(db, user_id, request_id, now=None):
    now = int(time.time()) if now is None else int(now)
    db.execute("""INSERT INTO analysis_jobs(user_id,request_id,state,created_at,updated_at)
        VALUES(?,?,'queued',?,?)""", (user_id, request_id, now, now))


def _validate_lease(seconds):
    if not isinstance(seconds, int) or not MIN_LEASE_SECONDS <= seconds <= MAX_LEASE_SECONDS:
        raise ValueError('Lease duration must be an integer from 5 to 3600 seconds.')


def claim_job(lease_seconds=60, now=None):
    """Claim one queued job atomically; return a fencing token or None.

    Expired leases are NOT automatically reassigned: uploads are not durable.
    """
    _validate_lease(lease_seconds)
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("""SELECT user_id,request_id FROM analysis_jobs
            WHERE state='queued' ORDER BY created_at,user_id,request_id LIMIT 1""").fetchone()
        if row is None:
            return None
        token = secrets.token_urlsafe(32)
        db.execute("""UPDATE analysis_jobs SET state='leased',lease_token=?,lease_until=?,
            attempts=attempts+1,updated_at=? WHERE user_id=? AND request_id=? AND state='queued'""",
            (token,now+lease_seconds,now,row['user_id'],row['request_id']))
        return {'user_id':row['user_id'],'request_id':row['request_id'],
                'lease_token':token,'lease_until':now+lease_seconds}


def heartbeat(user_id,request_id,lease_token,lease_seconds=60,now=None):
    _validate_lease(lease_seconds)
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("""UPDATE analysis_jobs SET lease_until=?,updated_at=?
            WHERE user_id=? AND request_id=? AND state='leased'
            AND lease_token=? AND lease_until>?""",
            (now+lease_seconds,now,user_id,request_id,lease_token,now))
        return changed.rowcount == 1


def finish_job(user_id,request_id,lease_token,now=None):
    """Fence late workers. This changes queue metadata, not credit charges."""
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("""UPDATE analysis_jobs SET state='completed',lease_token=NULL,
            lease_until=NULL,updated_at=? WHERE user_id=? AND request_id=?
            AND state='leased' AND lease_token=? AND lease_until>?""",
            (now,user_id,request_id,lease_token,now))
        return changed.rowcount == 1


def expire_leases_for_maintenance(now=None):
    """Mark expired leased jobs interrupted, never silently rerun or refund.

    Must only be used in controlled maintenance when workers are stopped.
    Credit reconciliation remains the responsibility of batch recovery.
    """
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        result = db.execute("""UPDATE analysis_jobs SET state='interrupted',
            lease_token=NULL,lease_until=NULL,updated_at=?
            WHERE state='leased' AND lease_until<=?""", (now,now))
        return result.rowcount
