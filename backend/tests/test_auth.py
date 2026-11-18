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
