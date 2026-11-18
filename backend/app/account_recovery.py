"""Single-use email verification and password recovery."""

import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import (
    connection,
    current_user,
    hash_password,
    normalized_email,
    token_digest,
)

router = APIRouter(prefix="/api/auth", tags=["account-recovery"])

RESET_TTL = 15 * 60
VERIFY_TTL = 24 * 60 * 60
REQUEST_COOLDOWN = 60
GENERIC_MESSAGE = (
    "If the account exists, an email will be sent "
    "when email delivery is configured."
)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class ResetRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=1024)


def initialize_recovery():
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS account_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                purpose TEXT NOT NULL
                    CHECK(purpose IN ('reset','verify')),
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
                    ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS account_tokens_user_idx
            ON account_tokens(user_id,purpose)
        """)


def cleanup_expired_tokens(now=None):
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        return db.execute(
            "DELETE FROM account_tokens WHERE expires_at<=?",
            (now,),
        ).rowcount


def send_account_email(address, purpose, token):
    """Send using explicitly configured SMTP, never print secrets."""
    host = os.getenv("RESUMEPILOT_SMTP_HOST", "").strip()
    sender = os.getenv("RESUMEPILOT_SMTP_FROM", "").strip()
    username = os.getenv("RESUMEPILOT_SMTP_USER", "").strip()
    password = os.getenv("RESUMEPILOT_SMTP_PASSWORD", "")
    port = int(os.getenv("RESUMEPILOT_SMTP_PORT", "465"))

    if not (host and sender and username and password):
        raise RuntimeError("SMTP delivery is not configured.")

    if port != 465:
        raise RuntimeError(
            "Only SMTP over implicit TLS on port 465 is supported."
        )

    base = os.getenv(
        "RESUMEPILOT_PUBLIC_URL",
        "http://localhost:5173",
    ).rstrip("/")

    # A fixed route and URL-encoded URL-safe token.
    if purpose == "reset":
        subject = "ResumePilot AI password reset"
        link = f"{base}/reset-password?token={token}"
        description = "Reset your password"
    else:
        subject = "Verify your ResumePilot AI email"
        link = f"{base}/verify-email?token={token}"
        description = "Verify your email"

    message = EmailMessage()
    message["From"] = sender
    message["To"] = address
    message["Subject"] = subject
    message.set_content(
        f"{description}: {link}\\n"
        "If you did not request this, ignore this email.\\n"
        "Never share this link."
    )

    with smtplib.SMTP_SSL(
        host, port, timeout=10,
        context=ssl.create_default_context(),
    ) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def issue_token(user_id, email, purpose):
    """Issue at most one token per purpose and cooldown period."""
    now = int(time.time())
    ttl = RESET_TTL if purpose == "reset" else VERIFY_TTL
    token = secrets.token_urlsafe(32)
    digest = token_digest(token)

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute(
            """SELECT created_at FROM account_tokens
               WHERE user_id=? AND purpose=?
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, purpose),
        ).fetchone()

        if (
            latest is not None
            and now - latest["created_at"] < REQUEST_COOLDOWN
        ):
            return False

        db.execute(
            "DELETE FROM account_tokens WHERE user_id=? AND purpose=?",
            (user_id, purpose),
        )
        db.execute(
            """INSERT INTO account_tokens
               (token_hash,user_id,purpose,created_at,expires_at)
               VALUES(?,?,?,?,?)""",
            (digest, user_id, purpose, now, now + ttl),
        )

    try:
        send_account_email(email, purpose, token)
    except Exception:
        # Invalidate undelivered tokens without exposing them.
        with connection() as db:
            db.execute(
                "DELETE FROM account_tokens WHERE token_hash=?",
                (digest,),
            )
        return False

    return True


@router.post("/forgot-password")
def forgot_password(payload: EmailRequest):
    email = normalized_email(payload.email)

    with connection() as db:
        user = db.execute(
            "SELECT id,email FROM users WHERE email=?",
            (email,),
        ).fetchone()

    if user is not None:
        issue_token(user["id"], user["email"], "reset")

    # Same response for existing and nonexistent accounts.
    return {"message": GENERIC_MESSAGE}


@router.post("/reset-password")
def reset_password(payload: ResetRequest):
    new_hash = hash_password(payload.new_password)
    now = int(time.time())
    digest = token_digest(payload.token)

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        token = db.execute(
            """SELECT user_id FROM account_tokens
               WHERE token_hash=? AND purpose='reset'
               AND expires_at>?""",
            (digest, now),
        ).fetchone()

        if token is None:
            raise HTTPException(400, "Invalid or expired reset token.")

        user_id = token["user_id"]
        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (new_hash, user_id),
        )
        db.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (user_id,),
        )
        db.execute(
            "DELETE FROM account_tokens WHERE user_id=?",
            (user_id,),
        )
        db.execute(
            "DELETE FROM auth_login_attempts WHERE email=("
            "SELECT email FROM users WHERE id=?)",
            (user_id,),
        )

    return {
        "success": True,
        "message": "Password reset. Sign in with your new password.",
    }


@router.post("/request-verification")
def request_verification(user=Depends(current_user)):
    with connection() as db:
        row = db.execute(
            "SELECT email_verified FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()

    if row is None:
        raise HTTPException(401, "Invalid account.")

    if row["email_verified"]:
        return {"message": "Email already verified."}

    issued = issue_token(user["id"], user["email"], "verify")

    return {
        "message": (
            "Verification requested."
            if issued
            else "Verification email unavailable or recently requested."
        )
    }


@router.post("/verify-email")
def verify_email(payload: TokenRequest):
    now = int(time.time())
    digest = token_digest(payload.token)

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        token = db.execute(
            """SELECT user_id FROM account_tokens
               WHERE token_hash=? AND purpose='verify'
               AND expires_at>?""",
            (digest, now),
        ).fetchone()

        if token is None:
            raise HTTPException(
                400, "Invalid or expired verification token."
            )

        db.execute(
            "UPDATE users SET email_verified=1 WHERE id=?",
            (token["user_id"],),
        )
        db.execute(
            """DELETE FROM account_tokens
               WHERE user_id=? AND purpose='verify'""",
            (token["user_id"],),
        )

    return {"success": True, "email_verified": True}


@router.get("/verification-status")
def verification_status(user=Depends(current_user)):
    with connection() as db:
        row = db.execute(
            "SELECT email_verified FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()

    return {"email_verified": bool(row["email_verified"])}
