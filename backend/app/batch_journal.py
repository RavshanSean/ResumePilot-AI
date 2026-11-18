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
            state TEXT NOT NULL CHECK(state IN ('processing','completed')),
            response_json TEXT,
            created_at INTEGER NOT NULL,
            completed_at INTEGER,
            PRIMARY KEY(user_id, request_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")


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
