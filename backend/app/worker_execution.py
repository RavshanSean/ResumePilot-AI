"""Single-job processing foundation; results are not yet committed."""
from app.job_queue import claim_staged_job, heartbeat
from app.worker_inputs import load_worker_inputs


def execute_one(processor, lease_seconds=60):
    """Return a fenced provisional result; never finalize credits or jobs.

    Processor must be bounded and side-effect-free. Failures leave the lease
    for controlled recovery. Do not use with paid providers yet.
    """
    if not callable(processor):
        raise TypeError('Processor must be callable.')
    claim = claim_staged_job(lease_seconds=lease_seconds)
    if claim is None:
        return None
    requirements, uploads = load_worker_inputs(claim, lease_seconds)
    result = processor(requirements, uploads)
    if not heartbeat(claim['user_id'], claim['request_id'], claim['lease_token'], lease_seconds):
        raise RuntimeError('Worker lease lost during processing.')
    return claim, result
