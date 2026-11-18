"""Batch 21: safe, read-only worker preparation.

No AI, credit mutations, lease claims or automatic execution. A later batch
will add atomic fenced results and credit settlement before activating workers.
"""
import hashlib
import time
from app.auth import connection
from app.job_queue import read_staged_job
from app.batch_journal import fingerprint_batch


class WorkerPreparationError(RuntimeError):
    pass


def prepare_leased_job(user_id, request_id, lease_token, now=None):
    """Verify live lease and staged bytes, returning private inputs to worker code.

    This is a preparation primitive, NOT a runnable worker. Callers must
    recheck fencing before any eventual result or charge commit.
    """
    now = int(time.time()) if now is None else int(now)
    if not isinstance(lease_token, str) or not lease_token:
        raise WorkerPreparationError('Valid lease token required.')
    with connection() as db:
        row = db.execute("""SELECT b.fingerprint,b.state AS batch_state,
            j.state AS job_state,j.lease_until,j.lease_token
            FROM analysis_jobs j JOIN analysis_batches b
            ON b.user_id=j.user_id AND b.request_id=j.request_id
            WHERE j.user_id=? AND j.request_id=?""",
            (user_id,request_id)).fetchone()
        if (row is None or row['batch_state'] != 'processing'
                or row['job_state'] != 'leased'
                or row['lease_token'] != lease_token
                or row['lease_until'] <= now):
            raise WorkerPreparationError('Job lease unavailable or expired.')
        expected = row['fingerprint']
    try:
        requirements, uploads = read_staged_job(user_id, request_id, now=now)
    except (FileNotFoundError, OSError) as exc:
        raise WorkerPreparationError('Staged inputs missing or invalid.') from exc
    if fingerprint_batch(requirements, uploads) != expected:
        raise WorkerPreparationError('Staged inputs do not match batch fingerprint.')
    # Recheck after disk I/O; lease can be lost while files are read.
    with connection() as db:
        live = db.execute("""SELECT 1 FROM analysis_jobs j JOIN analysis_batches b
            ON b.user_id=j.user_id AND b.request_id=j.request_id
            WHERE j.user_id=? AND j.request_id=? AND j.state='leased'
            AND j.lease_token=? AND j.lease_until>? AND b.state='processing'""",
            (user_id,request_id,lease_token,int(time.time()) if now is None else now)).fetchone()
    if live is None:
        raise WorkerPreparationError('Job lease lost during input verification.')
    return {'user_id':user_id,'request_id':request_id,
            'requirements':requirements,'uploads':uploads}
