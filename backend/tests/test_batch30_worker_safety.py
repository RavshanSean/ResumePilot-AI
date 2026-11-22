"""Batch 30: fail-closed worker orchestration regression tests."""
import pytest
from app.worker_orchestration import run_one_staged_job

@pytest.mark.parametrize("duration", [0, -1, True, False, 1.5, "300", None, 86401])
def test_invalid_lease_duration_rejected_before_claim(monkeypatch, duration):
    def must_not_claim(**kwargs):
        raise AssertionError("invalid duration must not claim a job")
    monkeypatch.setattr("app.worker_orchestration.claim_staged_job", must_not_claim)
    with pytest.raises(ValueError, match="Lease duration"):
        run_one_staged_job(lambda *_: None, lease_seconds=duration)

def test_valid_duration_can_find_no_job(monkeypatch):
    seen = []
    def no_job(**kwargs):
        seen.append(kwargs["lease_seconds"])
        return None
    monkeypatch.setattr("app.worker_orchestration.claim_staged_job", no_job)
    assert run_one_staged_job(lambda *_: None, lease_seconds=300) is None
    assert seen == [300]
