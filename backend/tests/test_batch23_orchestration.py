"""Explicit worker orchestration and controlled recovery regression tests."""
import pytest
from fastapi.testclient import TestClient
from app import main, credits
from app.auth import connection
from app.batch_journal import claim_and_reserve, fingerprint_batch
from app.job_queue import stage_job_inputs
from app.worker_orchestration import (run_one_staged_job, inspect_recovery_candidates,
                                      recover_stopped_workers, WorkerOrchestrationError)


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv('RESUMEPILOT_DATABASE', str(tmp_path/'db.sqlite'))
    monkeypatch.setenv('RESUMEPILOT_TEMP_STORAGE', str(tmp_path/'uploads'))
    with TestClient(main.app) as client:
        response = client.post('/api/auth/register', json={'email':'batch23@example.com','password':'SecurePassword123!'})
        assert response.status_code == 201, response.text
        uid = response.json()['user']['id']
        uploads = [('one.pdf',b'first'),('two.docx',b'second')]
        claim_and_reserve(uid,'job',fingerprint_batch('Python',uploads),2)
        stage_job_inputs(uid,'job','Python',uploads)
        yield uid, uploads


def test_explicit_processor_settles_atomically(job):
    uid, uploads = job
    def processor(requirements, received, renew):
        assert requirements == 'Python' and received == uploads
        assert renew()
        return {'candidates':[], 'errors':[]}, [True,False]
    assert run_one_staged_job(processor)['completed']
    assert credits.balance(uid) == 4
    with connection() as db:
        assert [r[0] for r in db.execute('SELECT state FROM credit_operations ORDER BY request_id')] == ['completed','released']
        assert db.execute('SELECT state FROM analysis_jobs').fetchone()[0] == 'completed'
    assert run_one_staged_job(processor) is None


def test_processor_exception_leaves_credits_reserved(job):
    uid,_ = job
    def broken(*args):
        raise RuntimeError('simulated crash')
    with pytest.raises(RuntimeError, match='simulated crash'):
        run_one_staged_job(broken)
    assert credits.balance(uid) == 3
    with connection() as db:
        assert db.execute("SELECT COUNT(*) FROM credit_operations WHERE state='reserved'").fetchone()[0] == 2
        assert db.execute('SELECT state FROM analysis_jobs').fetchone()[0] == 'leased'


def test_expired_lease_cannot_commit(job):
    uid,_ = job
    def expire(requirements, uploads, renew):
        with connection() as db:
            db.execute('UPDATE analysis_jobs SET lease_until=0')
        return {'candidates':[], 'errors':[]}, [True,True]
    with pytest.raises(WorkerOrchestrationError):
        run_one_staged_job(expire)
    assert credits.balance(uid) == 3
    with connection() as db:
        assert db.execute('SELECT state FROM analysis_batches').fetchone()[0] == 'processing'


def test_no_unattended_retries_after_crash(job):
    def broken(*args):
        raise RuntimeError('crash')
    with pytest.raises(RuntimeError): run_one_staged_job(broken)
    assert run_one_staged_job(lambda *args: ({'candidates':[],'errors':[]},[True,True])) is None


def test_recovery_requires_explicit_workers_stopped(job):
    with pytest.raises(WorkerOrchestrationError): recover_stopped_workers()
    with pytest.raises(WorkerOrchestrationError): recover_stopped_workers(workers_stopped=1)


def test_read_only_recovery_inspection(job):
    uid,_ = job
    with connection() as db:
        db.execute('UPDATE analysis_batches SET created_at=100')
    rows = inspect_recovery_candidates(now=86502)
    assert len(rows) == 1 and rows[0]['user_id'] == uid
    assert credits.balance(uid) == 3


def test_stopped_worker_recovery_refunds_once(job):
    uid,_ = job
    with connection() as db:
        db.execute('UPDATE analysis_batches SET created_at=100')
    result = recover_stopped_workers(workers_stopped=True,now=86502)
    assert result == {'interrupted_batches':1,'refunded_credits':2}
    assert credits.balance(uid) == 5
    assert recover_stopped_workers(workers_stopped=True,now=86502)['refunded_credits'] == 0
    with connection() as db:
        assert db.execute('SELECT state FROM analysis_jobs').fetchone()[0] == 'interrupted'


def test_invalid_processor_and_recovery_age(job):
    with pytest.raises(ValueError): run_one_staged_job(None)
    with pytest.raises(ValueError): inspect_recovery_candidates(max_age_seconds=0)
    with pytest.raises(ValueError): recover_stopped_workers(workers_stopped=True,max_age_seconds=0)
