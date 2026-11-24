import pytest
from app import worker_inputs


def test_load_checks_lease_before_and_after(monkeypatch):
    calls = []
    def heartbeat(*args):
        calls.append('lease')
        return True
    def read(*args):
        calls.append('read')
        return ('Python', [('cv.pdf', b'pdf')])
    monkeypatch.setattr(worker_inputs, 'heartbeat', heartbeat)
    monkeypatch.setattr(worker_inputs, 'read_staged_job', read)
    assert worker_inputs.load_worker_inputs(dict(user_id='u', request_id='r', lease_token='t'))[0] == 'Python'
    assert calls == ['lease', 'read', 'lease']


@pytest.mark.parametrize('checks', [[False], [True, False]])
def test_lost_lease_fails_closed(monkeypatch, checks):
    values = iter(checks)
    reads = []
    monkeypatch.setattr(worker_inputs, 'heartbeat', lambda *a: next(values))
    monkeypatch.setattr(worker_inputs, 'read_staged_job', lambda *a: reads.append(a))
    with pytest.raises(RuntimeError, match='lease'):
        worker_inputs.load_worker_inputs(dict(user_id='u', request_id='r', lease_token='t'))
    assert len(reads) == len(checks)-1
