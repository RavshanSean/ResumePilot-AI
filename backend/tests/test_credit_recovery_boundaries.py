"""Recovery boundary regression tests; no production data or worker."""
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch
from app.async_routes import rollback_failed_submission
from app.credits import balance
from test_async_failure_recovery import environment


def test_rollback_preserves_unrelated_reservations(environment):
    _, uid, _, _ = environment
    digest = fingerprint_batch('Python', [('cv.pdf', b'%PDF-1.4')])
    before = balance(uid)
    claim_and_reserve(uid, 'job', digest, 1)
    claim_and_reserve(uid, 'job-other', digest, 1)
    assert rollback_failed_submission(uid, 'job') is True
    assert rollback_failed_submission(uid, 'job') is False
    assert balance(uid) == before - 1
    with connection() as db:
        assert db.execute("SELECT state FROM credit_operations WHERE user_id=? AND request_id=?", (uid, 'job-other:0')).fetchone()[0] == 'reserved'


def test_nested_request_id_recovery_boundary(environment):
    _, uid, _, _ = environment
    digest = fingerprint_batch('Python', [('cv.pdf', b'%PDF-1.4')])
    before = balance(uid)
    claim_and_reserve(uid, 'parent', digest, 1)
    claim_and_reserve(uid, 'parent:child', digest, 1)
    assert rollback_failed_submission(uid, 'parent') is True
    assert balance(uid) == before - 1
    with connection() as db:
        assert db.execute("SELECT state FROM credit_operations WHERE user_id=? AND request_id=?", (uid, 'parent:child:0')).fetchone()[0] == 'reserved'


def test_stale_recovery_preserves_nested_recent_batch(environment):
    from app.batch_journal import recover_interrupted_batches
    import time
    _, uid, _, _ = environment
    digest = fingerprint_batch('Python', [('cv.pdf', b'%PDF-1.4')])
    before = balance(uid)
    claim_and_reserve(uid, 'old', digest, 1)
    claim_and_reserve(uid, 'old:child', digest, 1)
    now = int(time.time())
    with connection() as db:
        db.execute("UPDATE analysis_batches SET created_at=? WHERE user_id=? AND request_id=?", (now-7200, uid, 'old'))
    assert recover_interrupted_batches(now=now, max_age_seconds=3600) == {'interrupted_batches': 1, 'refunded_credits': 1}
    assert balance(uid) == before - 1
    with connection() as db:
        assert db.execute("SELECT state FROM credit_operations WHERE user_id=? AND request_id=?", (uid, 'old:child:0')).fetchone()[0] == 'reserved'
    assert recover_interrupted_batches(now=now, max_age_seconds=3600)['refunded_credits'] == 0
