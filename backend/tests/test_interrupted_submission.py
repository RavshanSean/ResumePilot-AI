"""Recovery of interrupted reservations using the real database."""
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch, recover_interrupted_batches
from app.credits import balance
from app.async_routes import rollback_failed_submission
from test_async_failure_recovery import environment, submit, rows


def test_interrupted_unstaged_reservation_refunds_once(environment):
    client, uid, headers, _ = environment
    before = balance(uid)
    claim_and_reserve(uid, 'interrupted', fingerprint_batch('Python', [('a.pdf', b'pdf')]), 1)
    assert balance(uid) == before - 1
    assert rollback_failed_submission(uid, 'interrupted') is True
    assert rollback_failed_submission(uid, 'interrupted') is False
    assert balance(uid) == before
    assert rows(uid, 'interrupted') == (0, 0, 0, 0)


def test_maintenance_refunds_stale_reservation_once(environment):
    client, uid, headers, _ = environment
    before = balance(uid)
    claim_and_reserve(uid, 'stale', fingerprint_batch('Python', [('a.pdf', b'pdf')]), 1)
    with connection() as db:
        db.execute('UPDATE analysis_batches SET created_at=1 WHERE user_id=? AND request_id=?', (uid, 'stale'))
    assert recover_interrupted_batches(now=100000)['refunded_credits'] == 1
    assert recover_interrupted_batches(now=100000)['refunded_credits'] == 0
    assert balance(uid) == before
    assert client.get('/api/jobs/stale/status', headers=headers).json() == {'status': 'interrupted'}


def test_recent_staged_job_survives_stale_recovery(environment):
    client, uid, headers, _ = environment
    before = balance(uid)
    assert submit(client, headers, 'fresh', count=1).status_code == 202
    assert recover_interrupted_batches(now=100000)['refunded_credits'] == 0
    assert balance(uid) == before - 1
    assert rows(uid, 'fresh') == (1, 1, 1, 1)
