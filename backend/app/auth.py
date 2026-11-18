"""Account and session foundation. No card or resume storage."""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/auth", tags=["accounts"])

SESSION_SECONDS = 60 * 60 * 24
MAX_PASSWORD_BYTES = 1024
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 15 * 60


def database_path():
    return os.getenv(
        "RESUMEPILOT_DATABASE",
        str(Path(__file__).resolve().parents[1] / "resumepilot.db"),
    )


@contextmanager
def connection():
    path = database_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")

    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_database():
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS sessions_user_idx
            ON sessions(user_id)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS auth_login_attempts (
                email TEXT PRIMARY KEY,
                failed_count INTEGER NOT NULL DEFAULT 0,
                window_start INTEGER NOT NULL,
                locked_until INTEGER NOT NULL DEFAULT 0
            )
        """)


def cleanup_expired_sessions(now=None):
    """Remove expired sessions without touching active sessions."""
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        result = db.execute(
            "DELETE FROM sessions WHERE expires_at<=?",
            (now,),
        )
        return result.rowcount



class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=12, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)



def normalized_email(email):
    email = email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Enter a valid email address.")
    return email


def hash_password(password, salt=None):
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise HTTPException(422, "Password is too long.")

    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", raw, salt, 600000
    )
    return salt.hex() + ":" + digest.hex()


def verify_password(password, stored):
    try:
        salt_hex, expected = stored.split(":", 1)
        actual = hash_password(
            password, bytes.fromhex(salt_hex)
        ).split(":", 1)[1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db, user_id):
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_SECONDS

    db.execute(
        """INSERT INTO sessions(token_hash,user_id,expires_at)
           VALUES(?,?,?)""",
        (token_digest(token), user_id, expires_at),
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": SESSION_SECONDS,
    }


def current_user(authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:]
    if not token or len(token) > 256:
        raise HTTPException(401, "Invalid session.")

    with connection() as db:
        row = db.execute(
            """SELECT users.id, users.email
               FROM sessions
               JOIN users ON users.id=sessions.user_id
               WHERE sessions.token_hash=?
               AND sessions.expires_at>?""",
            (token_digest(token), int(time.time())),
        ).fetchone()

    if row is None:
        raise HTTPException(401, "Invalid or expired session.")

    return {"id": row["id"], "email": row["email"]}


@router.post("/register", status_code=201)
def register(credentials: Credentials):
    email = normalized_email(credentials.email)
    user_id = secrets.token_hex(16)
    password_hash = hash_password(credentials.password)

    try:
        with connection() as db:
            db.execute(
                """INSERT INTO users(id,email,password_hash,created_at)
                   VALUES(?,?,?,?)""",
                (user_id, email, password_hash, int(time.time())),
            )
            session = issue_session(db, user_id)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Account already exists.")

    return {
        "user": {"id": user_id, "email": email},
        **session,
    }


@router.post("/login")
def login(credentials: Credentials):
    email = normalized_email(credentials.email)
    now = int(time.time())
    session = None
    locked = False

    # Serialize attempts so simultaneous requests cannot
    # bypass the failure threshold.
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")

        attempt = db.execute(
            """SELECT failed_count, window_start, locked_until
               FROM auth_login_attempts WHERE email=?""",
            (email,),
        ).fetchone()

        if attempt and attempt["locked_until"] > now:
            locked = True
        else:
            row = db.execute(
                "SELECT id,password_hash FROM users WHERE email=?",
                (email,),
            ).fetchone()

            valid = (
                row is not None
                and verify_password(
                    credentials.password, row["password_hash"]
                )
            )

            if valid:
                db.execute(
                    "DELETE FROM auth_login_attempts WHERE email=?",
                    (email,),
                )
                session = issue_session(db, row["id"])
            else:
                if (
                    attempt is None
                    or now - attempt["window_start"]
                    >= LOGIN_WINDOW_SECONDS
                ):
                    count = 1
                    window_start = now
                else:
                    count = attempt["failed_count"] + 1
                    window_start = attempt["window_start"]

                locked_until = (
                    now + LOGIN_LOCK_SECONDS
                    if count >= LOGIN_MAX_FAILURES
                    else 0
                )

                db.execute(
                    """INSERT INTO auth_login_attempts
                       (email,failed_count,window_start,locked_until)
                       VALUES(?,?,?,?)
                       ON CONFLICT(email) DO UPDATE SET
                         failed_count=excluded.failed_count,
                         window_start=excluded.window_start,
                         locked_until=excluded.locked_until""",
                    (email, count, window_start, locked_until),
                )

    if locked:
        raise HTTPException(
            429,
            "Too many login attempts. Try again in 15 minutes.",
            headers={"Retry-After": str(LOGIN_LOCK_SECONDS)},
        )

    if session is None:
        raise HTTPException(401, "Invalid email or password.")

    return session


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.post("/logout")
def logout(
    authorization: str | None = Header(None),
    user=Depends(current_user),
):
    token = authorization[7:]

    with connection() as db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash=? AND user_id=?",
            (token_digest(token), user["id"]),
        )

    return {"success": True}



@router.post("/logout-all")
def logout_all(user=Depends(current_user)):
    """Revoke every session belonging to the authenticated user."""
    with connection() as db:
        result = db.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (user["id"],),
        )

    return {"success": True, "sessions_revoked": result.rowcount}


@router.post("/change-password")
def change_password(
    payload: PasswordChange,
    user=Depends(current_user),
):
    """Verify current password, rotate password, revoke all sessions."""
    if payload.current_password == payload.new_password:
        raise HTTPException(
            400, "New password must differ from current password."
        )

    # Validate password length before modifying the database.
    new_hash = hash_password(payload.new_password)

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT password_hash FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()

        if row is None or not verify_password(
            payload.current_password, row["password_hash"]
        ):
            raise HTTPException(401, "Current password is incorrect.")

        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (new_hash, user["id"]),
        )
        db.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (user["id"],),
        )
        session = issue_session(db, user["id"])

    return {
        "success": True,
        "message": "Password changed. All previous sessions revoked.",
        **session,
    }
