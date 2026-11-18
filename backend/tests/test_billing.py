"""Test-mode billing tests, no Stripe network calls."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import billing, credits, main


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE", str(tmp_path / "billing.db")
    )
    monkeypatch.delenv("RESUMEPILOT_TEST_BYPASS_AUTH", raising=False)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "email": "billing@example.com",
                "password": "SecurePassword123!",
            },
        )
        assert response.status_code == 201
        data = response.json()
        yield client, data["user"]["id"], {
            "Authorization": "Bearer " + data["access_token"]
        }


def create_order(user_id, order_id="order-one"):
    with billing.connection() as db:
        db.execute(
            """INSERT INTO billing_orders
               (id,user_id,package,credits,amount,currency,
                stripe_session,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                order_id, user_id, "starter", 100, 199,
                "usd", "cs_test_order_one", int(time.time()),
            ),
        )


def paid_event(event_id="evt_one", amount=199):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_order_one",
            "client_reference_id": "order-one",
            "metadata": {"order_id": "order-one"},
            "payment_status": "paid",
            "amount_total": amount,
            "currency": "usd",
        }},
    }


def test_packages_are_public_but_checkout_disabled(account):
    client, _, headers = account
    response = client.get("/api/billing/packages")
    assert response.status_code == 200
    assert len(response.json()["packages"]) == 3
    assert response.json()["checkout_enabled"] is False

    response = client.post(
        "/api/billing/checkout",
        json={"package": "starter"},
        headers=headers,
    )
    assert response.status_code == 503


def test_paid_event_grants_credits_once(account):
    _, user_id, _ = account
    create_order(user_id)

    assert credits.balance(user_id) == 5
    assert billing.apply_paid_event(paid_event()) is True
    assert credits.balance(user_id) == 105

    assert billing.apply_paid_event(paid_event()) is False
    assert billing.apply_paid_event(paid_event("evt_other")) is False
    assert credits.balance(user_id) == 105


def test_wrong_amount_cannot_grant_credits(account):
    _, user_id, _ = account
    create_order(user_id)

    with pytest.raises(HTTPException) as error:
        billing.apply_paid_event(paid_event(amount=1))

    assert error.value.status_code == 409
    assert credits.balance(user_id) == 5


def test_unpaid_event_cannot_grant_credits(account):
    _, user_id, _ = account
    create_order(user_id)
    event = paid_event()
    event["data"]["object"]["payment_status"] = "unpaid"

    assert billing.apply_paid_event(event) is False
    assert credits.balance(user_id) == 5


def test_webhook_signature_validation():
    secret = "whsec_testsecret"
    timestamp = int(time.time())
    body = b'{"id":"evt_test"}'
    digest = hmac.new(
        secret.encode(),
        str(timestamp).encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    header = f"t={timestamp},v1={digest}"

    billing.verify_signature(body, header, secret, now=timestamp)

    with pytest.raises(HTTPException):
        billing.verify_signature(
            body + b"x", header, secret, now=timestamp
        )

    with pytest.raises(HTTPException):
        billing.verify_signature(
            body, header, secret, now=timestamp + 301
        )


def test_webhook_is_disabled_without_configuration(account):
    client, _, _ = account
    response = client.post(
        "/api/billing/webhook",
        content=json.dumps(paid_event()),
    )
    assert response.status_code == 503
