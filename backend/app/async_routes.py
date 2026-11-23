"""Opt-in async submission; no worker starts from an HTTP request."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from app.auth import current_user, connection
from app.async_submission import validate_submission
from app.batch_journal import claim_and_reserve, fingerprint_batch
from app.job_queue import stage_job_inputs
from app.temporary_storage import MAX_UPLOAD_BYTES

router = APIRouter(prefix='/api/jobs', tags=['jobs'])


def rollback_failed_submission(user_id: str, request_id: str):
    """Release only this still-queued, unstaged submission's reserved credits.

    Never touch an active lease or a staged job. All updates are atomic.
    """
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('''SELECT j.state, b.state AS batch_state
            FROM analysis_jobs j JOIN analysis_batches b
            ON j.user_id=b.user_id AND j.request_id=b.request_id
            WHERE j.user_id=? AND j.request_id=?''', (user_id, request_id)).fetchone()
        if not job or job['state'] != 'queued' or job['batch_state'] != 'processing':
            return False
        staged = db.execute('SELECT 1 FROM analysis_job_requirements WHERE user_id=? AND request_id=?',
                            (user_id, request_id)).fetchone()
        if staged:
            return False
        keys = db.execute('''SELECT request_id FROM credit_operations
            WHERE user_id=? AND substr(request_id,1,?)=? AND state='reserved' ''',
            (user_id, len(request_id)+1, request_id+':')).fetchall()
        for row in keys:
            db.execute("UPDATE credit_operations SET state='released' WHERE user_id=? AND request_id=? AND state='reserved'",
                       (user_id, row['request_id']))
        if keys:
            db.execute('UPDATE credit_accounts SET balance=balance+? WHERE user_id=?', (len(keys), user_id))
        db.execute('DELETE FROM analysis_batches WHERE user_id=? AND request_id=?', (user_id, request_id))
        return True


@router.post('/submit', status_code=202)
async def submit_job(
    files: list[UploadFile] = File(...),
    requirements: str = Form(''),
    request_id: str = Form(''),
    user=Depends(current_user),
):
    if len(files) > 30:
        raise HTTPException(400, 'Upload between 1 and 30 resumes.')
    uploads = []
    try:
        for file in files:
            uploads.append((file.filename or '', await file.read(MAX_UPLOAD_BYTES + 1)))
    finally:
        for file in files:
            await file.close()
    validate_submission(request_id, requirements, uploads)
    user_id = user['id']
    fingerprint = fingerprint_batch(requirements, uploads)
    # Existing request IDs never reserve a second time. Completed synchronous
    # batches cannot be silently converted to async submissions.
    with connection() as db:
        existing = db.execute('SELECT 1 FROM analysis_batches WHERE user_id=? AND request_id=?',
                              (user_id, request_id)).fetchone()
    if existing:
        raise HTTPException(409, 'Request ID already used. Start a new analysis with a new request ID.')
    replay = claim_and_reserve(user_id, request_id, fingerprint, len(uploads))
    if replay is not None:
        raise HTTPException(409, 'Request ID already completed.')
    try:
        stage_job_inputs(user_id, request_id, requirements, uploads)
    except BaseException:
        rollback_failed_submission(user_id, request_id)
        raise
    return {'request_id': request_id, 'status': 'queued'}
