"""Authentication and account isolation regression tests."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import auth, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "RESUMEPILOT_DATABASE",
        str(tmp_path / "test.db"),
    )
    auth.initialize_database()

    with TestClient(main.app) as test_client:
        yield test_client


def credentials(email="person@example.com"):
    return {
        "email": email,
        "password": "Correct-Horse-Battery-123!",
    }


def test_register_login_me_logout(client):
    response = client.post(
        "/api/auth/register", json=credentials()
    )
    assert response.status_code == 201

    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get(
        "/api/auth/me", headers=headers
    ).json()["email"] == "person@example.com"

    login = client.post(
        "/api/auth/login", json=credentials()
    )
    assert login.status_code == 200
    assert login.json()["access_token"] != token

    assert client.post(
        "/api/auth/logout", headers=headers
    ).json()["success"] is True

    assert client.get(
        "/api/auth/me", headers=headers
    ).status_code == 401


def test_duplicate_and_invalid_password(client):
    assert client.post(
        "/api/auth/register", json=credentials()
    ).status_code == 201

    assert client.post(
        "/api/auth/register", json=credentials()
    ).status_code == 409

    wrong = {
        **credentials(),
        "password": "Incorrect-Password-123",
    }
    assert client.post(
        "/api/auth/login", json=wrong
    ).status_code == 401

    assert client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "short"},
    ).status_code == 422


def test_accounts_are_isolated(client):
    first = client.post(
        "/api/auth/register",
        json=credentials("first@example.com"),
    ).json()

    second = client.post(
        "/api/auth/register",
        json=credentials("second@example.com"),
    ).json()

    assert first["user"]["id"] != second["user"]["id"]

    for account in (first, second):
        result = client.get(
            "/api/auth/me",
            headers={
                "Authorization":
                f"Bearer {account['access_token']}"
            },
        ).json()
        assert result["id"] == account["user"]["id"]


def test_password_and_session_not_stored_in_plaintext(client):
    registered = client.post(
        "/api/auth/register", json=credentials()
    ).json()

    with auth.connection() as db:
        row = db.execute(
            "SELECT password_hash FROM users"
        ).fetchone()
        session = db.execute(
            "SELECT token_hash FROM sessions"
        ).fetchone()

    assert "Correct-Horse" not in row["password_hash"]
    assert registered["access_token"] != session["token_hash"]


def test_expired_session_rejected(client):
    registered = client.post(
        "/api/auth/register", json=credentials()
    ).json()

    with auth.connection() as db:
        db.execute("UPDATE sessions SET expires_at=1")

    assert client.get(
        "/api/auth/me",
        headers={
            "Authorization":
            f"Bearer {registered['access_token']}"
        },
    ).status_code == 401


def test_no_authentication_returns_401(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 401



def test_change_password_revokes_existing_sessions(client):
    first = client.post(
        "/api/auth/register", json=credentials()
    ).json()

    second = client.post(
        "/api/auth/login", json=credentials()
    ).json()

    headers = {
        "Authorization": "Bearer " + first["access_token"]
    }

    response = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": credentials()["password"],
            "new_password": "Another-Secure-Password-456!",
        },
    )

    assert response.status_code == 200
    new_token = response.json()["access_token"]

    for token in (
        first["access_token"],
        second["access_token"],
    ):
        assert client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer " + token},
        ).status_code == 401

    assert client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer " + new_token},
    ).status_code == 200

    assert client.post(
        "/api/auth/login", json=credentials()
    ).status_code == 401

    updated = {
        **credentials(),
        "password": "Another-Secure-Password-456!",
    }
    assert client.post(
        "/api/auth/login", json=updated
    ).status_code == 200


def test_wrong_current_password_does_not_revoke_session(client):
    registered = client.post(
        "/api/auth/register", json=credentials()
    ).json()

    headers = {
        "Authorization": "Bearer " + registered["access_token"]
    }

    response = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "Wrong-Password-123!",
            "new_password": "Another-Secure-Password-456!",
        },
    )

    assert response.status_code == 401
    assert client.get(
        "/api/auth/me", headers=headers
    ).status_code == 200


def test_same_password_change_rejected(client):
    registered = client.post(
        "/api/auth/register", json=credentials()
    ).json()

    response = client.post(
        "/api/auth/change-password",
        headers={
            "Authorization": "Bearer " + registered["access_token"]
        },
        json={
            "current_password": credentials()["password"],
            "new_password": credentials()["password"],
        },
    )

    assert response.status_code == 400


def test_logout_all_revokes_only_own_sessions(client):
    first = client.post(
        "/api/auth/register",
        json=credentials("first@example.com"),
    ).json()

    second = client.post(
        "/api/auth/register",
        json=credentials("second@example.com"),
    ).json()

    first_headers = {
        "Authorization": "Bearer " + first["access_token"]
    }
    second_headers = {
        "Authorization": "Bearer " + second["access_token"]
    }

    response = client.post(
        "/api/auth/logout-all", headers=first_headers
    )

    assert response.status_code == 200
    assert response.json()["sessions_revoked"] >= 1

    assert client.get(
        "/api/auth/me", headers=first_headers
    ).status_code == 401

    assert client.get(
        "/api/auth/me", headers=second_headers
    ).status_code == 200


def test_logout_all_requires_authentication(client):
    assert client.post(
        "/api/auth/logout-all"
    ).status_code == 401

    assert client.post(
        "/api/auth/change-password",
        json={
            "current_password": "Current-Password-123!",
            "new_password": "Another-Password-456!",
        },
    ).status_code == 401


def test_expired_session_cleanup_preserves_active(client):
    registered = client.post(
        "/api/auth/register", json=credentials()
    ).json()

    with auth.connection() as db:
        db.execute(
            """INSERT INTO sessions
               (token_hash,user_id,expires_at)
               SELECT ?,user_id,1 FROM sessions LIMIT 1""",
            ("expired-test-token-hash",),
        )

    assert auth.cleanup_expired_sessions() == 1
    assert auth.cleanup_expired_sessions() == 0

    assert client.get(
        "/api/auth/me",
        headers={
            "Authorization": "Bearer " + registered["access_token"]
        },
    ).status_code == 200
