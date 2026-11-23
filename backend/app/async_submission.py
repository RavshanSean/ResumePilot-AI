"""Pure preflight checks for future asynchronous analysis submissions.

No credits, queue rows, uploads, or workers are touched by this module.
"""
from pathlib import Path
from fastapi import HTTPException
from app.temporary_storage import _validate, DEFAULT_TTL_SECONDS

MAX_FILES = 30
MAX_REQUIREMENTS = 20_000
MAX_REQUEST_ID = 100


def validate_submission(request_id: str, requirements: str, uploads: list[tuple[str, bytes]]):
    """Validate complete upload data before any credit reservation or staging."""
    if not isinstance(request_id, str) or not request_id or len(request_id) > MAX_REQUEST_ID:
        raise HTTPException(400, 'A valid analysis request ID is required.')
    if any(ord(c) < 32 or ord(c) == 127 for c in request_id):
        raise HTTPException(400, 'Invalid analysis request ID.')
    if not isinstance(requirements, str) or not requirements.strip() or len(requirements) > MAX_REQUIREMENTS:
        raise HTTPException(400, 'Enter essential job requirements (maximum 20,000 characters).')
    if not isinstance(uploads, (list, tuple)) or not 1 <= len(uploads) <= MAX_FILES:
        raise HTTPException(400, 'Upload between 1 and 30 resumes.')
    for item in uploads:
        if not isinstance(item, tuple) or len(item) != 2:
            raise HTTPException(400, 'Invalid upload payload.')
        filename, data = item
        try:
            _validate(filename, data, DEFAULT_TTL_SECONDS)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
    return len(uploads)
