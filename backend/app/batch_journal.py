"""Durable completed-response replay, scoped to each authenticated user."""
import hashlib
import json
import time
from fastapi import HTTPException
from app.auth import connection


def initialize_batch_journal():
    with connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_batches (
            user_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('processing','completed','interrupted')),
            response_json TEXT,
            created_at INTEGER NOT NULL,
            completed_at INTEGER,
            PRIMARY KEY(user_id, request_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")

        # Migrate Batch 16 journals without discarding completed results.
        sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='analysis_batches'"
        ).fetchone()[0]
        if "'interrupted'" not in sql:
            db.execute("""CREATE TABLE analysis_batches_new (
                user_id TEXT NOT NULL, request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('processing','completed','interrupted')),
                response_json TEXT, created_at INTEGER NOT NULL, completed_at INTEGER,
                PRIMARY KEY(user_id, request_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )""")
            db.execute("INSERT INTO analysis_batches_new SELECT * FROM analysis_batches")
            db.execute("DROP TABLE analysis_batches")
            db.execute("ALTER TABLE analysis_batches_new RENAME TO analysis_batches")


def fingerprint_batch(requirements, uploads):
    digest = hashlib.sha256()
    for item in [requirements.encode(), str(len(uploads)).encode()]:
        digest.update(len(item).to_bytes(8, 'big'))
        digest.update(item)
    for name, data in uploads:
        for item in (name.encode(), data):
            digest.update(len(item).to_bytes(8, 'big'))
            digest.update(item)
    return digest.hexdigest()


def begin_batch(user_id, request_id, fingerprint):
    if not request_id or len(request_id) > 100:
        raise HTTPException(400, 'Invalid batch request identifier.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(
            'SELECT fingerprint,state,response_json FROM analysis_batches WHERE user_id=? AND request_id=?',
            (user_id, request_id),
        ).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise HTTPException(409, 'Request ID reused with different inputs. Use a new request ID.')
            if row['state'] == 'completed':
                return json.loads(row['response_json'])
            if row['state'] == 'interrupted':
                raise HTTPException(409, 'This analysis was interrupted and its unused credits were returned. Start a new analysis with a new request ID.')
            raise HTTPException(409, 'This batch is processing or was interrupted. Contact support before retrying.')
        db.execute(
            "INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,'processing',?)",
            (user_id, request_id, fingerprint, int(time.time())),
        )
    return None


def complete_batch(user_id, request_id, response):
    payload = json.dumps(response, allow_nan=False)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        result = db.execute(
            "UPDATE analysis_batches SET state='completed',response_json=?,completed_at=? WHERE user_id=? AND request_id=? AND state='processing'",
            (payload, int(time.time()), user_id, request_id),
        )
        if result.rowcount != 1:
            raise RuntimeError('Batch journal was not in processing state.')


def abandon_unreserved_batch(user_id, request_id):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute(
            'SELECT 1 FROM credit_operations WHERE user_id=? AND substr(request_id,1,?)=? LIMIT 1',
            (user_id, len(request_id)+1, request_id+':'),
        ).fetchone()
        if existing is None:
            db.execute(
                "DELETE FROM analysis_batches WHERE user_id=? AND request_id=? AND state='processing'",
                (user_id, request_id),
            )


def claim_and_reserve(user_id, request_id, fingerprint, count):
    """Atomically claim a new batch and reserve all credits, or replay it."""
    from app.credits import ensure_account
    if not request_id or len(request_id) > 100:
        raise HTTPException(400, 'Invalid batch request identifier.')
    if not isinstance(count, int) or not 1 <= count <= 30:
        raise HTTPException(400, 'Upload between 1 and 30 resumes.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute(
            'SELECT fingerprint,state,response_json FROM analysis_batches WHERE user_id=? AND request_id=?',
            (user_id, request_id),
        ).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise HTTPException(409, 'Request ID reused with different inputs. Use a new request ID.')
            if row['state'] == 'completed':
                return json.loads(row['response_json'])
            if row['state'] == 'interrupted':
                raise HTTPException(409, 'This analysis was interrupted. Start a new analysis with a new request ID.')
            raise HTTPException(409, 'This analysis is already processing or may have been interrupted.')
        ensure_account(db, user_id)
        keys = [f'{request_id}:{index}' for index in range(count)]
        existing = db.execute(
            'SELECT 1 FROM credit_operations WHERE user_id=? AND request_id IN ('
            + ','.join('?' for _ in keys) + ') LIMIT 1',
            (user_id, *keys),
        ).fetchone()
        if existing:
            raise HTTPException(409, 'A credit reservation already exists for this request ID.')
        changed = db.execute(
            'UPDATE credit_accounts SET balance=balance-? WHERE user_id=? AND balance>=?',
            (count, user_id, count),
        )
        if changed.rowcount != 1:
            raise HTTPException(402, f'This batch requires {count} credits. Your available balance is insufficient.')
        now = int(time.time())
        db.executemany(
            "INSERT INTO credit_operations(user_id,request_id,state,created_at) VALUES(?,?,'reserved',?)",
            [(user_id, key, now) for key in keys],
        )
        db.execute(
            "INSERT INTO analysis_batches(user_id,request_id,fingerprint,state,created_at) VALUES(?,?,?,'processing',?)",
            (user_id, request_id, fingerprint, now),
        )
    return None


def recover_interrupted_batches(now=None, max_age_seconds=86400):
    """Controlled maintenance only: no old workers may still be running.

    Refund only still-reserved credits; preserve completed charges and results.
    Never automatically rerun uploads that are no longer available.
    """
    now = int(time.time()) if now is None else int(now)
    if max_age_seconds < 1:
        raise ValueError('Recovery age must be positive.')
    cutoff = now - max_age_seconds
    recovered = 0
    refunded = 0
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute(
            "SELECT user_id,request_id FROM analysis_batches WHERE state='processing' AND created_at<?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            user_id, request_id = row['user_id'], row['request_id']
            # Exact prefix comparison, not LIKE wildcard matching.
            reserved = db.execute(
                "SELECT request_id FROM credit_operations WHERE user_id=? AND substr(request_id,1,?)=? AND state='reserved'",
                (user_id, len(request_id)+1, request_id+':'),
            ).fetchall()
            for operation in reserved:
                changed = db.execute(
                    "UPDATE credit_operations SET state='released' WHERE user_id=? AND request_id=? AND state='reserved'",
                    (user_id, operation['request_id']),
                )
                if changed.rowcount == 1:
                    db.execute('UPDATE credit_accounts SET balance=balance+1 WHERE user_id=?', (user_id,))
                    refunded += 1
            db.execute(
                "UPDATE analysis_batches SET state='interrupted' WHERE user_id=? AND request_id=? AND state='processing'",
                (user_id, request_id),
            )
            recovered += 1
    return {'interrupted_batches': recovered, 'refunded_credits': refunded}
