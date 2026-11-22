"""Batch 24: opt-in offline retention cleanup for terminal staged jobs.

Never run while any worker or submission process can write to the storage root.
No automatic scheduler or HTTP endpoint. No refunds or credit changes.
"""
import os
import stat
import time
from app.auth import connection
from app.temporary_storage import storage_root, _path


class RetentionMaintenanceError(RuntimeError):
    pass


def inspect_terminal_staging(now=None, grace_seconds=3600):
    """Read-only counts; no filenames or resume contents exposed."""
    now = int(time.time()) if now is None else int(now)
    if type(grace_seconds) is not int or grace_seconds < 0:
        raise ValueError('Invalid grace period.')
    with connection() as db:
        rows = db.execute("""SELECT j.state, COUNT(DISTINCT j.user_id || ':' || j.request_id) AS jobs,
            COUNT(i.file_id) AS files
            FROM analysis_jobs j
            JOIN analysis_batches b ON b.user_id=j.user_id AND b.request_id=j.request_id
            JOIN analysis_job_inputs i ON i.user_id=j.user_id AND i.request_id=j.request_id
            WHERE j.state IN ('completed','interrupted')
              AND b.state IN ('completed','interrupted')
              AND j.updated_at<=?
            GROUP BY j.state""", (now-grace_seconds,)).fetchall()
    return {r['state']: {'jobs': r['jobs'], 'files': r['files']} for r in rows}


def cleanup_terminal_staging(*, workers_stopped=False, now=None, grace_seconds=3600, limit=100):
    """Detach terminal manifests and remove their private files, offline only.

    Metadata deletion and manifest removal are transactional. A crash after DB
    commit may leave unreferenced files; reconcile_orphans can remove them.
    """
    if workers_stopped is not True:
        raise RetentionMaintenanceError('Stop workers and submissions before cleanup.')
    if type(grace_seconds) is not int or grace_seconds < 0:
        raise ValueError('Invalid grace period.')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Limit must be 1 to 1000.')
    now = int(time.time()) if now is None else int(now)
    root = storage_root()
    ids = []
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        jobs = db.execute("""SELECT j.user_id,j.request_id
            FROM analysis_jobs j JOIN analysis_batches b
            ON b.user_id=j.user_id AND b.request_id=j.request_id
            WHERE j.state IN ('completed','interrupted')
            AND b.state IN ('completed','interrupted')
            AND j.updated_at<=?
            AND EXISTS(SELECT 1 FROM analysis_job_inputs i
              WHERE i.user_id=j.user_id AND i.request_id=j.request_id)
            ORDER BY j.updated_at,j.user_id,j.request_id LIMIT ?""",
            (now-grace_seconds,limit)).fetchall()
        for job in jobs:
            uid, rid = job['user_id'], job['request_id']
            files = db.execute("""SELECT file_id FROM analysis_job_inputs
                WHERE user_id=? AND request_id=?""", (uid,rid)).fetchall()
            db.execute('DELETE FROM analysis_job_inputs WHERE user_id=? AND request_id=?',(uid,rid))
            db.execute('DELETE FROM analysis_job_requirements WHERE user_id=? AND request_id=?',(uid,rid))
            for f in files:
                # Do not remove a file still referenced by another manifest.
                changed = db.execute("""DELETE FROM temporary_resume_files WHERE file_id=?
                    AND NOT EXISTS(SELECT 1 FROM analysis_job_inputs WHERE file_id=?)""",
                    (f['file_id'],f['file_id']))
                if changed.rowcount == 1:
                    ids.append(f['file_id'])
    for file_id in set(ids):
        _path(root,file_id).unlink(missing_ok=True)
    return {'jobs':len(jobs),'files':len(set(ids))}


def reconcile_orphans(*, workers_stopped=False, min_age_seconds=86400, limit=100, now=None):
    """Delete only old, untracked opaque .upload files in the private root.

    Requires complete write quiescence; never follows symlinks or traverses dirs.
    """
    if workers_stopped is not True:
        raise RetentionMaintenanceError('Stop workers and submissions before reconciliation.')
    if type(min_age_seconds) is not int or min_age_seconds < 3600:
        raise ValueError('Orphan age must be at least one hour.')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Limit must be 1 to 1000.')
    now = time.time() if now is None else float(now)
    root = storage_root()
    removed = 0
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for entry in sorted(root.iterdir()):
            if removed >= limit:
                break
            name = entry.name
            if not name.endswith('.upload'):
                continue
            fid = name[:-7]
            if len(fid) != 48 or any(c not in '0123456789abcdef' for c in fid):
                continue
            info = entry.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            if info.st_mtime > now-min_age_seconds:
                continue
            exists = db.execute('SELECT 1 FROM temporary_resume_files WHERE file_id=?',(fid,)).fetchone()
            if exists:
                continue
            entry.unlink()
            removed += 1
    return removed
