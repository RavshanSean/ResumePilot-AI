"""SQLite durable job metadata and fenced worker leases.

Development foundation only. Staged input manifests are optional; existing
synchronous requests continue to run without them. No background worker is enabled.
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


def initialize_job_inputs():
    """One immutable ordered manifest per batch; no resume contents in SQLite."""
    with connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_job_inputs (
            user_id TEXT NOT NULL, request_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position>=0),
            file_id TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,request_id,position),
            FOREIGN KEY(user_id,request_id) REFERENCES analysis_jobs(user_id,request_id) ON DELETE CASCADE,
            FOREIGN KEY(file_id) REFERENCES temporary_resume_files(file_id) ON DELETE RESTRICT
        )""")
        db.execute('CREATE INDEX IF NOT EXISTS analysis_job_inputs_job_idx ON analysis_job_inputs(user_id,request_id)')
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_job_requirements (
            user_id TEXT NOT NULL, request_id TEXT NOT NULL,
            requirements TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,request_id),
            FOREIGN KEY(user_id,request_id) REFERENCES analysis_jobs(user_id,request_id) ON DELETE CASCADE
        )""")


def stage_job_inputs(user_id, request_id, requirements, uploads, ttl_seconds=3600, now=None):
    """Attach immutable private upload IDs to an existing queued batch.

    Requires pre-existing atomic batch+credit reservation. Never charges here.
    Files are staged first; a single transaction publishes the complete manifest.
    If staging fails, best-effort cleanup removes files created by this call.
    No automatic worker processing is activated.
    """
    from app.batch_journal import fingerprint_batch
    from app.temporary_storage import store_resume, delete_resume, _validate
    from fastapi import HTTPException
    if not isinstance(requirements,str) or not requirements.strip() or len(requirements)>20000:
        raise ValueError('Invalid job requirements.')
    if not isinstance(uploads,(list,tuple)) or not 1<=len(uploads)<=30:
        raise ValueError('Upload between 1 and 30 resumes.')
    for filename,data in uploads:
        _validate(filename,data,ttl_seconds)
    digest=fingerprint_batch(requirements,uploads)
    now=int(time.time()) if now is None else int(now)
    # Early check avoids unnecessary disk writes. The final check inside the
    # transaction also handles races with other staging or completion.
    def verify(db):
        row=db.execute("""SELECT b.fingerprint,b.state AS batch_state,j.state AS job_state
            FROM analysis_batches b JOIN analysis_jobs j
            ON b.user_id=j.user_id AND b.request_id=j.request_id
            WHERE b.user_id=? AND b.request_id=?""",(user_id,request_id)).fetchone()
        if row is None or row['batch_state']!='processing' or row['job_state']!='queued' or row['fingerprint']!=digest:
            raise HTTPException(409,'Batch is unavailable, completed, or has different inputs.')
        if db.execute('SELECT 1 FROM analysis_job_requirements WHERE user_id=? AND request_id=?',
                      (user_id,request_id)).fetchone():
            raise HTTPException(409,'Job inputs already staged.')
        operations=db.execute("""SELECT COUNT(*) AS n FROM credit_operations
            WHERE user_id=? AND substr(request_id,1,?)=? AND state='reserved'""",
            (user_id,len(request_id)+1,request_id+':')).fetchone()['n']
        if operations!=len(uploads):
            raise HTTPException(409,'Batch reservations do not match the uploads.')
    with connection() as db:
        verify(db)
    created=[]
    try:
        for filename,data in uploads:
            created.append(store_resume(user_id,filename,data,ttl_seconds=ttl_seconds,now=now))
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            verify(db)
            db.execute('INSERT INTO analysis_job_requirements(user_id,request_id,requirements,created_at) VALUES(?,?,?,?)',
                       (user_id,request_id,requirements,now))
            db.executemany('INSERT INTO analysis_job_inputs(user_id,request_id,position,file_id,created_at) VALUES(?,?,?,?,?)',
                           [(user_id,request_id,i,fid,now) for i,fid in enumerate(created)])
    except BaseException:
        for fid in created:
            try:
                delete_resume(user_id,fid)
            except OSError:
                pass
        raise
    return tuple(created)


def read_staged_job(user_id,request_id,now=None):
    """Return requirements and ordered verified resume bytes for the owning user.

    Does not confer a lease or authorize processing. Worker must separately
    validate its lease token before doing any work.
    """
    from app.temporary_storage import read_resume
    with connection() as db:
        req=db.execute('SELECT requirements FROM analysis_job_requirements WHERE user_id=? AND request_id=?',
                       (user_id,request_id)).fetchone()
        rows=db.execute('SELECT file_id FROM analysis_job_inputs WHERE user_id=? AND request_id=? ORDER BY position',
                        (user_id,request_id)).fetchall()
    if req is None or not rows:
        raise FileNotFoundError('Staged job inputs unavailable.')
    return req['requirements'],[read_resume(user_id,row['file_id'],now=now) for row in rows]


def claim_staged_job(lease_seconds=60,now=None):
    """Claim ONLY queued jobs whose manifest exists and whose files are unexpired.

    This claims metadata only; actual processing is intentionally disabled.
    """
    _validate_lease(lease_seconds)
    now=int(time.time()) if now is None else int(now)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("""SELECT j.user_id,j.request_id FROM analysis_jobs j
            JOIN analysis_job_requirements r ON r.user_id=j.user_id AND r.request_id=j.request_id
            JOIN analysis_batches b ON b.user_id=j.user_id AND b.request_id=j.request_id
            WHERE j.state='queued' AND b.state='processing'
            AND EXISTS(SELECT 1 FROM analysis_job_inputs i WHERE i.user_id=j.user_id AND i.request_id=j.request_id)
            AND NOT EXISTS(SELECT 1 FROM analysis_job_inputs i
                LEFT JOIN temporary_resume_files f ON f.file_id=i.file_id
                WHERE i.user_id=j.user_id AND i.request_id=j.request_id
                AND (f.file_id IS NULL OR f.expires_at<=?))
            ORDER BY j.created_at,j.user_id,j.request_id LIMIT 1""",(now,)).fetchone()
        if row is None:
            return None
        token=secrets.token_urlsafe(32)
        db.execute("""UPDATE analysis_jobs SET state='leased',lease_token=?,lease_until=?,
            attempts=attempts+1,updated_at=? WHERE user_id=? AND request_id=? AND state='queued'""",
            (token,now+lease_seconds,now,row['user_id'],row['request_id']))
        return {'user_id':row['user_id'],'request_id':row['request_id'],
                'lease_token':token,'lease_until':now+lease_seconds}
