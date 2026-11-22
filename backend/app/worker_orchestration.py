"""Batch 23: explicitly invoked, fail-closed development worker orchestration.

No scheduler, API endpoint, or background process starts automatically.
The caller supplies a trusted processor; no external AI provider is configured.
Recovery requires an operator to stop all old workers first.
"""
import time
from app.auth import connection
from app.job_queue import claim_staged_job, heartbeat
from app.background_worker import prepare_leased_job, WorkerPreparationError
from app.worker_settlement import settle_leased_batch, WorkerSettlementError
from app.batch_journal import recover_interrupted_batches


class WorkerOrchestrationError(RuntimeError):
    pass


def run_one_staged_job(processor, lease_seconds=300):
    """Claim and execute at most one staged job with an injected processor.

    Processor receives requirements, ordered uploads, and a heartbeat callback.
    It must return (response_dict, ordered_success_flags). Only settlement
    commits results. Errors leave reservations pending for controlled recovery.
    This is intentionally not an unattended retrying worker.
    """
    if not callable(processor):
        raise ValueError('Trusted processor callable required.')
    # Reject invalid configuration before claiming a queued job.
    # bool is an int subclass and must not be accepted as a lease duration.
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
        raise ValueError('Lease duration must be an integer from 1 to 86400 seconds.')
    lease = claim_staged_job(lease_seconds=lease_seconds)
    if lease is None:
        return None
    uid, rid, token = lease['user_id'], lease['request_id'], lease['lease_token']

    def renew():
        if not heartbeat(uid, rid, token, lease_seconds=lease_seconds):
            raise WorkerOrchestrationError('Worker lease lost; results cannot be committed.')
        return True

    try:
        prepared = prepare_leased_job(uid, rid, token)
        response, successes = processor(prepared['requirements'], prepared['uploads'], renew)
        # Recheck ownership immediately before the atomic fenced settlement.
        renew()
        settle_leased_batch(uid, rid, token, response, successes)
    except (WorkerPreparationError, WorkerSettlementError) as exc:
        raise WorkerOrchestrationError('Worker could not safely complete the job.') from exc
    # Other processor exceptions deliberately propagate; no automatic retries.
    return {'user_id': uid, 'request_id': rid, 'completed': True}


def inspect_recovery_candidates(now=None, max_age_seconds=86400):
    """Read-only list of stale processing batches. Does not mutate balances."""
    now = int(time.time()) if now is None else int(now)
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise ValueError('Recovery age must be a positive integer.')
    with connection() as db:
        rows = db.execute("""SELECT b.user_id,b.request_id,j.state AS job_state,
            j.lease_until,b.created_at FROM analysis_batches b
            JOIN analysis_jobs j ON j.user_id=b.user_id AND j.request_id=b.request_id
            WHERE b.state='processing' AND b.created_at<?
            ORDER BY b.created_at,b.user_id,b.request_id""", (now-max_age_seconds,)).fetchall()
    return [dict(row) for row in rows]


def recover_stopped_workers(*, workers_stopped=False, now=None, max_age_seconds=86400):
    """Operator-only crash reconciliation; NEVER call with live workers.

    Old leases are invalidated and still-reserved credits refunded atomically
    by the existing journal recovery. No job is automatically retried.
    """
    if workers_stopped is not True:
        raise WorkerOrchestrationError('Stop all old workers before recovery.')
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise ValueError('Recovery age must be a positive integer.')
    return recover_interrupted_batches(now=now, max_age_seconds=max_age_seconds)
