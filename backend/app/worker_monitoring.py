"""Read-only, aggregate worker diagnostics. No resume data or account identifiers."""
import time
from app.auth import connection

STATES = ('queued', 'leased', 'completed', 'interrupted')


def inspect_worker_health(now=None):
    """Return aggregate queue counts; never expose IDs, lease tokens or uploads."""
    if now is None:
        now = int(time.time())
    elif type(now) is not int:
        raise ValueError('now must be an integer timestamp.')
    with connection() as db:
        rows = db.execute('SELECT state, COUNT(*) AS n FROM analysis_jobs GROUP BY state').fetchall()
        expired = db.execute("SELECT COUNT(*) FROM analysis_jobs WHERE state='leased' AND lease_until<=?", (now,)).fetchone()[0]
        stale = db.execute("SELECT COUNT(*) FROM analysis_batches WHERE state='processing' AND created_at<=?", (now-86400,)).fetchone()[0]
    counts = {state: 0 for state in STATES}
    for row in rows:
        if row['state'] in counts:
            counts[row['state']] = row['n']
    return {'jobs': counts, 'expired_leases': expired, 'stale_processing_batches_24h': stale}
