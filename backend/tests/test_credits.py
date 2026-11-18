"""Credit accounting and authenticated analysis tests."""

import io
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import credits, main


def pdf():
    with pymupdf.open() as document:
        document.new_page().insert_text(
            (72, 72), "Python experience"
        )
        return document.tobytes()


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE", str(tmp_path / "credits.db")
    )
    monkeypatch.delenv("RESUMEPILOT_TEST_BYPASS_AUTH", raising=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "email": "credits@example.com",
                "password": "SecurePassword123!",
            },
        )
        assert response.status_code == 201

        data = response.json()
        headers = {
            "Authorization": f"Bearer {data['access_token']}"
        }
        yield client, data["user"]["id"], headers


def test_trial_balance_and_no_public_grant(account):
    client, user_id, headers = account
    assert client.get(
        "/api/credits/balance", headers=headers
    ).json()["balance"] == 5

    assert client.get("/api/credits/balance").status_code == 401
    assert client.post(
        "/api/credits/grant", headers=headers
    ).status_code == 404


def test_reservation_and_refund_are_idempotent(account):
    _, user_id, _ = account

    credits.reserve(user_id, "one")
    assert credits.balance(user_id) == 4

    with pytest.raises(HTTPException) as error:
        credits.reserve(user_id, "one")
    assert error.value.status_code == 409

    assert credits.finish(user_id, "one", False) is True
    assert credits.finish(user_id, "one", False) is False
    assert credits.balance(user_id) == 5

    credits.reserve(user_id, "two")
    assert credits.finish(user_id, "two", True) is True
    assert credits.finish(user_id, "two", False) is False
    assert credits.balance(user_id) == 4


def test_insufficient_credits(account):
    _, user_id, _ = account

    for index in range(5):
        credits.reserve(user_id, f"request-{index}")
        credits.finish(user_id, f"request-{index}", True)

    assert credits.balance(user_id) == 0

    with pytest.raises(HTTPException) as error:
        credits.reserve(user_id, "sixth")

    assert error.value.status_code == 402
    assert credits.balance(user_id) == 0


def test_concurrent_reservations_never_overspend(account):
    _, user_id, _ = account

    def attempt(index):
        try:
            credits.reserve(user_id, f"concurrent-{index}")
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(12)))

    assert sum(results) == 5
    assert credits.balance(user_id) == 0


def test_accounts_have_separate_balances(account):
    client, first_id, _ = account

    response = client.post(
        "/api/auth/register",
        json={
            "email": "other@example.com",
            "password": "SecurePassword123!",
        },
    )
    assert response.status_code == 201
    second_id = response.json()["user"]["id"]

    credits.reserve(first_id, "first-job")
    credits.finish(first_id, "first-job", True)

    assert credits.balance(first_id) == 4
    assert credits.balance(second_id) == 5


def test_successful_ai_charges_once(account, monkeypatch):
    client, user_id, headers = account

    async def fake_match(requirements, resume_text):
        return {
            "category": "Review First",
            "summary": "Evidence found.",
            "requirements": [{
                "requirement": "Python",
                "status": "supported",
                "evidence": "Python",
            }],
            "supported_count": 1,
            "total_requirements": 1,
        }

    monkeypatch.setattr(main, "match_resume", fake_match)

    def analyze():
        return client.post(
            "/api/analyze",
            headers=headers,
            data={
                "requirements": "Python",
                "request_id": "successful-job",
            },
            files={
                "files": ("resume.pdf", io.BytesIO(pdf()), "application/pdf")
            },
        )

    first = analyze()
    assert first.status_code == 200
    assert first.json()["candidates"][0]["ai_completed"] is True
    assert credits.balance(user_id) == 4

    repeated = analyze()
    assert repeated.status_code == 409
    assert credits.balance(user_id) == 4


def test_failed_ai_does_not_charge(account, monkeypatch):
    client, user_id, headers = account

    async def unavailable(requirements, resume_text):
        raise main.MatchingError("AI unavailable")

    monkeypatch.setattr(main, "match_resume", unavailable)

    response = client.post(
        "/api/analyze",
        headers=headers,
        data={
            "requirements": "Python",
            "request_id": "failed-job",
        },
        files={
            "files": ("resume.pdf", io.BytesIO(pdf()), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert response.json()["candidates"][0]["ai_completed"] is False
    assert credits.balance(user_id) == 5



def test_stale_reservation_is_refunded_once(account):
    _, user_id, _ = account

    credits.reserve(user_id, "stale-request")
    assert credits.balance(user_id) == 4

    with credits.connection() as db:
        db.execute(
            """UPDATE credit_operations SET created_at=?
               WHERE user_id=? AND request_id=?""",
            (100, user_id, "stale-request"),
        )

    assert credits.recover_stale_reservations(
        now=100 + credits.RECOVERY_SECONDS + 1
    ) == 1

    assert credits.balance(user_id) == 5

    assert credits.recover_stale_reservations(
        now=100 + credits.RECOVERY_SECONDS + 1
    ) == 0

    assert credits.balance(user_id) == 5


def test_recent_reservation_is_not_refunded(account):
    _, user_id, _ = account

    credits.reserve(user_id, "active-request")
    assert credits.balance(user_id) == 4

    assert credits.recover_stale_reservations() == 0
    assert credits.balance(user_id) == 4


def test_completed_charge_is_never_refunded_by_recovery(account):
    _, user_id, _ = account

    credits.reserve(user_id, "completed-request")
    credits.finish(user_id, "completed-request", True)

    with credits.connection() as db:
        db.execute(
            """UPDATE credit_operations SET created_at=?
               WHERE user_id=? AND request_id=?""",
            (100, user_id, "completed-request"),
        )

    assert credits.recover_stale_reservations(
        now=100 + credits.RECOVERY_SECONDS + 1
    ) == 0

    assert credits.balance(user_id) == 4
