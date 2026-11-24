"""Lease-fenced input loading; no AI calls or background loop."""
from app.job_queue import heartbeat, read_staged_job


def load_worker_inputs(claim, lease_seconds=60):
    uid, rid, token = (claim[k] for k in ('user_id', 'request_id', 'lease_token'))
    if not heartbeat(uid, rid, token, lease_seconds):
        raise RuntimeError('Worker lease unavailable.')
    inputs = read_staged_job(uid, rid)
    if not heartbeat(uid, rid, token, lease_seconds):
        raise RuntimeError('Worker lease lost while loading inputs.')
    return inputs
