"""Transactional development credit ledger.

No public credit-granting endpoint. Stripe integration is deferred.
"""

import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException

from app.auth import connection, current_user

router = APIRouter(prefix="/api/credits", tags=["credits"])
TRIAL_CREDITS = 5


def initialize_credits():
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS credit_accounts (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 5 CHECK(balance >= 0),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS credit_operations (
                user_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(
                    state IN ('reserved', 'completed', 'released')
                ),
                created_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, request_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)


def ensure_account(db, user_id):
    db.execute(
        """INSERT OR IGNORE INTO credit_accounts(user_id, balance)
           SELECT id, ? FROM users WHERE id=?""",
        (TRIAL_CREDITS, user_id),
    )


def balance(user_id):
    with connection() as db:
        ensure_account(db, user_id)
        row = db.execute(
            "SELECT balance FROM credit_accounts WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(401, "Account unavailable.")
        return row["balance"]


def reserve(user_id, request_id):
    """Atomically deduct one available credit and reserve a unique request."""
    if not request_id or len(request_id) > 128:
        raise HTTPException(400, "Invalid request identifier.")

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        ensure_account(db, user_id)

        existing = db.execute(
            """SELECT state FROM credit_operations
               WHERE user_id=? AND request_id=?""",
            (user_id, request_id),
        ).fetchone()

        if existing:
            raise HTTPException(
                409, "This analysis request has already been submitted."
            )

        changed = db.execute(
            """UPDATE credit_accounts
               SET balance=balance-1
               WHERE user_id=? AND balance>=1""",
            (user_id,),
        )

        if changed.rowcount != 1:
            raise HTTPException(
                402, "Insufficient credits. No analysis was charged."
            )

        db.execute(
            """INSERT INTO credit_operations
               (user_id,request_id,state,created_at)
               VALUES(?,?,?,?)""",
            (user_id, request_id, "reserved", int(time.time())),
        )




def reserve_batch(user_id, request_id, count):
    """Reserve an entire upload atomically before AI work begins."""
    if not request_id or len(request_id) > 100:
        raise HTTPException(400, "Invalid batch request identifier.")
    if not isinstance(count, int) or not 1 <= count <= 30:
        raise HTTPException(400, "Upload between 1 and 30 resumes.")

    keys = [f"{request_id}:{index}" for index in range(count)]

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        ensure_account(db, user_id)

        # Check all request IDs before touching the balance.
        existing = db.execute(
            """SELECT request_id FROM credit_operations
               WHERE user_id=? AND request_id IN (
               """ + ",".join("?" for _ in keys) + ")",
            (user_id, *keys),
        ).fetchone()

        if existing:
            raise HTTPException(
                409, "This batch was already submitted. "
                     "Start a new analysis to try again."
            )

        changed = db.execute(
            """UPDATE credit_accounts
               SET balance=balance-?
               WHERE user_id=? AND balance>=?""",
            (count, user_id, count),
        )

        if changed.rowcount != 1:
            raise HTTPException(
                402,
                f"This batch requires {count} credits. "
                "Your available balance is insufficient."
            )

        db.executemany(
            """INSERT INTO credit_operations
               (user_id,request_id,state,created_at)
               VALUES(?,?,'reserved',?)""",
            [(user_id, key, int(time.time())) for key in keys],
        )

    return keys


def finish(user_id, request_id, successful):
    """Complete a reservation, or return its credit exactly once."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute(
            """UPDATE credit_operations SET state=?
               WHERE user_id=? AND request_id=? AND state='reserved'""",
            (
                "completed" if successful else "released",
                user_id,
                request_id,
            ),
        )

        if changed.rowcount != 1:
            return False

        if not successful:
            db.execute(
                """UPDATE credit_accounts SET balance=balance+1
                   WHERE user_id=?""",
                (user_id,),
            )

        return True


@router.get("/balance")
def get_balance(user=Depends(current_user)):
    return {
        "balance": balance(user["id"]),
        "unit": "successful_ai_resume_analysis",
    }


# Development recovery window. Long-running production jobs will need
# durable worker leases and reconciliation before this can be relied on.
RECOVERY_SECONDS = 24 * 60 * 60


def recover_stale_reservations(now=None):
    """Refund stale reservations once, preserving completed charges.

    Call only during controlled startup when no older workers are active.
    """
    now = int(time.time()) if now is None else int(now)
    cutoff = now - RECOVERY_SECONDS

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT user_id, request_id FROM credit_operations
               WHERE state='reserved' AND created_at<?""",
            (cutoff,),
        ).fetchall()

        for row in rows:
            changed = db.execute(
                """UPDATE credit_operations SET state='released'
                   WHERE user_id=? AND request_id=?
                   AND state='reserved'""",
                (row["user_id"], row["request_id"]),
            )

            if changed.rowcount == 1:
                db.execute(
                    """UPDATE credit_accounts SET balance=balance+1
                       WHERE user_id=?""",
                    (row["user_id"],),
                )

        return len(rows)
