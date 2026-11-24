import pytest
from app import worker_execution as worker


def test_empty_queue(monkeypatch):
    monkeypatch.setattr(worker, 'claim_staged_job', lambda **kw: None)
    assert worker.execute_one(lambda *args: pytest.fail('Unexpected processing')) is None


@pytest.mark.parametrize('valid', [True, False])
def test_execution_fencing(monkeypatch, valid):
    claim = dict(user_id='u', request_id='r', lease_token='t')
    monkeypatch.setattr(worker, 'claim_staged_job', lambda **kw: claim)
    monkeypatch.setattr(worker, 'load_worker_inputs', lambda *args: ('Python', [('cv.pdf', b'data')]))
    monkeypatch.setattr(worker, 'heartbeat', lambda *args: valid)
    def processor(requirements, uploads):
        assert requirements == 'Python'
        assert uploads == [('cv.pdf', b'data')]
        return {'score': 80}
    if valid:
        assert worker.execute_one(processor) == (claim, {'score': 80})
    else:
        with pytest.raises(RuntimeError, match='lease lost'):
            worker.execute_one(processor)


def test_loading_failure_prevents_processing(monkeypatch):
    monkeypatch.setattr(worker, 'claim_staged_job', lambda **kw: {})
    def fail(*args):
        raise RuntimeError('Worker lease unavailable.')
    monkeypatch.setattr(worker, 'load_worker_inputs', fail)
    with pytest.raises(RuntimeError, match='lease unavailable'):
        worker.execute_one(lambda *args: pytest.fail('Unexpected processing'))
