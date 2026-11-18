"""Batch 22: fenced atomic worker result and credit settlement.

Internal primitive only. No automatic worker, HTTP endpoint, or AI calls.
The caller must validate the actual AI outcomes before providing successes.
"""
import json
import time
from app.auth import connection


class WorkerSettlementError(RuntimeError):
    pass


def settle_leased_batch(user_id, request_id, lease_token, response, successes, now=None):
    """Commit final response, per-file credit settlement and job completion atomically.

    successes is an ordered list of exact bools, one per staged resume.
    True means a successful AI analysis; False refunds its reserved credit.
    Only a currently valid, fenced lease can settle. No implicit retry.
    """
    if not isinstance(lease_token, str) or not lease_token:
        raise WorkerSettlementError('Lease token required.')
    if not isinstance(successes, (list, tuple)) or not successes or len(successes) > 30 or any(type(x) is not bool for x in successes):
        raise WorkerSettlementError('Expected an ordered list of AI success flags.')
    if not isinstance(response, dict) or not isinstance(response.get('candidates'), list) or not isinstance(response.get('errors'), list):
        raise WorkerSettlementError('Invalid batch response.')
    try:
        payload = json.dumps(response, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkerSettlementError('Response must be valid JSON.') from exc
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("""SELECT j.state AS job_state,j.lease_token,j.lease_until,
            b.state AS batch_state FROM analysis_jobs j JOIN analysis_batches b
            ON j.user_id=b.user_id AND j.request_id=b.request_id
            WHERE j.user_id=? AND j.request_id=?""", (user_id,request_id)).fetchone()
        if (row is None or row['job_state'] != 'leased' or row['batch_state'] != 'processing'
                or row['lease_token'] != lease_token or row['lease_until'] <= now):
            raise WorkerSettlementError('Job lease unavailable or expired.')
        manifest = db.execute('SELECT position FROM analysis_job_inputs WHERE user_id=? AND request_id=? ORDER BY position',
                              (user_id,request_id)).fetchall()
        if len(manifest) != len(successes) or [r['position'] for r in manifest] != list(range(len(successes))):
            raise WorkerSettlementError('Staged manifest does not match outcomes.')
        for index, success in enumerate(successes):
            operation = f'{request_id}:{index}'
            changed = db.execute("""UPDATE credit_operations SET state=?
                WHERE user_id=? AND request_id=? AND state='reserved'""",
                ('completed' if success else 'released',user_id,operation))
            if changed.rowcount != 1:
                raise WorkerSettlementError('Credit reservation missing or already settled.')
            if not success:
                changed = db.execute('UPDATE credit_accounts SET balance=balance+1 WHERE user_id=?', (user_id,))
                if changed.rowcount != 1:
                    raise WorkerSettlementError('Credit account unavailable.')
        changed = db.execute("""UPDATE analysis_batches SET state='completed',response_json=?,completed_at=?
            WHERE user_id=? AND request_id=? AND state='processing'""",
            (payload,now,user_id,request_id))
        if changed.rowcount != 1:
            raise WorkerSettlementError('Batch no longer processing.')
        changed = db.execute("""UPDATE analysis_jobs SET state='completed',lease_token=NULL,
            lease_until=NULL,updated_at=? WHERE user_id=? AND request_id=? AND state='leased'
            AND lease_token=? AND lease_until>?""",
            (now,user_id,request_id,lease_token,now))
        if changed.rowcount != 1:
            raise WorkerSettlementError('Lease lost before completion.')
    return True
