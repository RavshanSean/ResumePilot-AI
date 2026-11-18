"""Account recovery and verification security tests."""

import pytest
from fastapi.testclient import TestClient

from app import auth, main, account_recovery as recovery


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE", str(tmp_path / "recovery.db")
    )
    sent = []

    def fake_send(email, purpose, token):
        sent.append((email, purpose, token))

    monkeypatch.setattr(recovery, "send_account_email", fake_send)

    # Initialize the isolated test database explicitly.
    # Do not depend on application lifespan startup.
    auth.initialize_database()
    auth.initialize_email_verification()
    recovery.initialize_recovery()

    with TestClient(main.app) as client:
        yield client, sent


def register(client, email="person@example.com"):
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "Original-Password-123!",
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def headers(token):
    return {"Authorization": "Bearer " + token}


def test_password_reset_revokes_sessions(setup):
    client, sent = setup
    old = register(client)
    second = client.post(
        "/api/auth/login",
        json={
            "email": "person@example.com",
            "password": "Original-Password-123!",
        },
    ).json()["access_token"]

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": "person@example.com"},
    )
    assert response.status_code == 200
    assert "token" not in response.text
    token = sent[-1][2]

    result = client.post(
        "/api/auth/reset-password",
        json={
            "token": token,
            "new_password": "Updated-Password-456!",
        },
    )
    assert result.status_code == 200

    for session in (old, second):
        assert client.get(
            "/api/auth/me", headers=headers(session)
        ).status_code == 401

    assert client.post(
        "/api/auth/login",
        json={
            "email": "person@example.com",
            "password": "Updated-Password-456!",
        },
    ).status_code == 200


def test_reset_token_is_single_use(setup):
    client, sent = setup
    register(client)
    client.post(
        "/api/auth/forgot-password",
        json={"email": "person@example.com"},
    )
    payload = {
        "token": sent[-1][2],
        "new_password": "Updated-Password-456!",
    }
    assert client.post(
        "/api/auth/reset-password", json=payload
    ).status_code == 200
    assert client.post(
        "/api/auth/reset-password", json=payload
    ).status_code == 400


def test_reset_token_expiration(setup):
    client, sent = setup
    register(client)
    client.post(
        "/api/auth/forgot-password",
        json={"email": "person@example.com"},
    )
    with auth.connection() as db:
        db.execute(
            "UPDATE account_tokens SET expires_at=1"
        )
    assert client.post(
        "/api/auth/reset-password",
        json={
            "token": sent[-1][2],
            "new_password": "Updated-Password-456!",
        },
    ).status_code == 400


def test_forgot_password_does_not_reveal_accounts(setup):
    client, sent = setup
    register(client)
    known = client.post(
        "/api/auth/forgot-password",
        json={"email": "person@example.com"},
    )
    unknown = client.post(
        "/api/auth/forgot-password",
        json={"email": "nobody@example.com"},
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(sent) == 1


def test_email_verification_single_use(setup):
    client, sent = setup
    token = register(client)
    assert client.get(
        "/api/auth/verification-status",
        headers=headers(token),
    ).json()["email_verified"] is False

    response = client.post(
        "/api/auth/request-verification",
        headers=headers(token),
    )
    assert response.status_code == 200
    assert len(sent) == 1

    verification = sent[-1][2]
    assert client.post(
        "/api/auth/verify-email",
        json={"token": verification},
    ).json()["email_verified"] is True

    assert client.post(
        "/api/auth/verify-email",
        json={"token": verification},
    ).status_code == 400

    assert client.get(
        "/api/auth/verification-status",
        headers=headers(token),
    ).json()["email_verified"] is True


def test_verification_requires_login(setup):
    client, sent = setup
    assert client.post(
        "/api/auth/request-verification"
    ).status_code == 401


def test_reset_cooldown_prevents_repeated_emails(setup):
    client, sent = setup
    register(client)
    for _ in range(3):
        client.post(
            "/api/auth/forgot-password",
            json={"email": "person@example.com"},
        )
    assert len(sent) == 1


def test_delivery_failure_does_not_leave_valid_token(
    setup, monkeypatch
):
    client, sent = setup
    register(client)

    def fail_delivery(*args):
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(
        recovery, "send_account_email", fail_delivery
    )

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": "person@example.com"},
    )
    assert response.status_code == 200

    with auth.connection() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM account_tokens"
        ).fetchone()[0]

    assert count == 0


def test_reset_token_cannot_verify_email(setup):
    client, sent = setup
    register(client)
    client.post(
        "/api/auth/forgot-password",
        json={"email": "person@example.com"},
    )
    assert client.post(
        "/api/auth/verify-email",
        json={"token": sent[-1][2]},
    ).status_code == 400
