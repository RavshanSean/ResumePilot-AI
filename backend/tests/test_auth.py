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



def test_login_lockout_after_repeated_failures(client):
    client.post("/api/auth/register", json=credentials())

    wrong = {
        **credentials(),
        "password": "Incorrect-Password-123!",
    }

    for _ in range(auth.LOGIN_MAX_FAILURES):
        response = client.post("/api/auth/login", json=wrong)
        assert response.status_code == 401

    blocked = client.post(
        "/api/auth/login", json=credentials()
    )

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_login_recovers_after_lock_expires(client):
    client.post("/api/auth/register", json=credentials())

    wrong = {
        **credentials(),
        "password": "Incorrect-Password-123!",
    }

    for _ in range(auth.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login", json=wrong)

    with auth.connection() as db:
        db.execute(
            """UPDATE auth_login_attempts
               SET locked_until=1, window_start=1
               WHERE email=?""",
            ("person@example.com",),
        )

    response = client.post(
        "/api/auth/login", json=credentials()
    )

    assert response.status_code == 200

    with auth.connection() as db:
        row = db.execute(
            "SELECT * FROM auth_login_attempts WHERE email=?",
            ("person@example.com",),
        ).fetchone()

    assert row is None


def test_successful_login_resets_failure_counter(client):
    client.post("/api/auth/register", json=credentials())

    wrong = {
        **credentials(),
        "password": "Incorrect-Password-123!",
    }

    for _ in range(3):
        assert client.post(
            "/api/auth/login", json=wrong
        ).status_code == 401

    assert client.post(
        "/api/auth/login", json=credentials()
    ).status_code == 200

    with auth.connection() as db:
        row = db.execute(
            "SELECT * FROM auth_login_attempts WHERE email=?",
            ("person@example.com",),
        ).fetchone()

    assert row is None


def test_login_lockout_isolated_by_email(client):
    client.post(
        "/api/auth/register",
        json=credentials("first@example.com"),
    )
    client.post(
        "/api/auth/register",
        json=credentials("second@example.com"),
    )

    wrong = {
        **credentials("first@example.com"),
        "password": "Incorrect-Password-123!",
    }

    for _ in range(auth.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login", json=wrong)

    assert client.post(
        "/api/auth/login",
        json=credentials("first@example.com"),
    ).status_code == 429

    assert client.post(
        "/api/auth/login",
        json=credentials("second@example.com"),
    ).status_code == 200


def test_unknown_email_is_rate_limited(client):
    unknown = credentials("unknown@example.com")

    for _ in range(auth.LOGIN_MAX_FAILURES):
        assert client.post(
            "/api/auth/login", json=unknown
        ).status_code == 401

    assert client.post(
        "/api/auth/login", json=unknown
    ).status_code == 429



def test_source_limit_across_different_emails(client):
    for i in range(auth.SOURCE_MAX_FAILURES):
        response = client.post(
            "/api/auth/login",
            json=credentials(f"unknown{i}@example.com"),
        )
        assert response.status_code == 401

    blocked = client.post(
        "/api/auth/login",
        json=credentials("another@example.com"),
    )
    assert blocked.status_code == 429


def test_source_limit_persists_in_database(client):
    for i in range(auth.SOURCE_MAX_FAILURES):
        client.post(
            "/api/auth/login",
            json=credentials(f"attempt{i}@example.com"),
        )

    with auth.connection() as db:
        row = db.execute(
            "SELECT failed_count,locked_until FROM auth_source_attempts"
        ).fetchone()

    assert row["failed_count"] == auth.SOURCE_MAX_FAILURES
    assert row["locked_until"] > 0


def test_source_lock_expires(client):
    for i in range(auth.SOURCE_MAX_FAILURES):
        client.post(
            "/api/auth/login",
            json=credentials(f"attempt{i}@example.com"),
        )

    with auth.connection() as db:
        db.execute(
            """UPDATE auth_source_attempts
               SET locked_until=1,window_start=1"""
        )

    response = client.post(
        "/api/auth/login",
        json=credentials("fresh@example.com"),
    )
    assert response.status_code == 401


def test_success_does_not_erase_source_failure_history(client):
    client.post("/api/auth/register", json=credentials())

    client.post(
        "/api/auth/login",
        json=credentials("unknown@example.com"),
    )

    assert client.post(
        "/api/auth/login", json=credentials()
    ).status_code == 200

    with auth.connection() as db:
        row = db.execute(
            "SELECT failed_count FROM auth_source_attempts"
        ).fetchone()

    assert row["failed_count"] == 1
