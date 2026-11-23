"""Preflight must fail without making any database or storage changes."""
import pytest
from fastapi import HTTPException
from app.async_submission import validate_submission

GOOD = [('resume.pdf', b'%PDF-1.4')]


def test_valid_submission_returns_count():
    assert validate_submission('request-1', 'Python and security', GOOD) == 1
    assert validate_submission('request-2', 'Python', GOOD * 30) == 30


@pytest.mark.parametrize('request_id', ['', 'x' * 101, 'bad\nidentifier'])
def test_rejects_bad_request_id(request_id):
    with pytest.raises(HTTPException) as exc:
        validate_submission(request_id, 'Python', GOOD)
    assert exc.value.status_code == 400


@pytest.mark.parametrize('requirements', ['', '  ', 'x' * 20001])
def test_rejects_bad_requirements(requirements):
    with pytest.raises(HTTPException) as exc:
        validate_submission('request', requirements, GOOD)
    assert exc.value.status_code == 400


@pytest.mark.parametrize('uploads', [[], GOOD * 31, [('resume.txt', b'data')],
    [('../resume.pdf', b'data')], [('resume.pdf', b'')], [('resume.pdf', b'x' * (10 * 1024 * 1024 + 1))]])
def test_rejects_invalid_uploads(uploads):
    with pytest.raises(HTTPException) as exc:
        validate_submission('request', 'Python', uploads)
    assert exc.value.status_code == 400


def test_validation_does_not_mutate_inputs():
    uploads = list(GOOD)
    assert validate_submission('request', 'Python', uploads) == 1
    assert uploads == GOOD
