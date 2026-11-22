"""Account-scoped, read-only job status API. Does not expose resumes or leases."""
from fastapi import APIRouter, Depends, HTTPException
from app.auth import connection, current_user

router = APIRouter(prefix='/api/jobs', tags=['jobs'])

@router.get('/{request_id}/status')
def get_job_status(request_id: str, user=Depends(current_user)):
    if not request_id or len(request_id) > 100:
        raise HTTPException(404, 'Job not found.')
    with connection() as db:
        row = db.execute('''SELECT j.state, b.state AS batch_state
            FROM analysis_jobs j JOIN analysis_batches b
            ON j.user_id=b.user_id AND j.request_id=b.request_id
            WHERE j.user_id=? AND j.request_id=?''',
            (user['id'], request_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Job not found.')
    state = row['state']
    if row['batch_state'] == 'completed':
        state = 'completed'
    elif row['batch_state'] == 'interrupted':
        state = 'interrupted'
    elif state == 'leased':
        state = 'processing'
    return {'status': state}
