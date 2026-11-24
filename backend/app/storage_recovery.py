"""Offline reconciliation. Stop all uploaders and workers before calling."""
import re
import stat
import time
from app.auth import connection
from app.temporary_storage import storage_root


def reconcile_orphans(*, maintenance_confirmed=False, dry_run=True, min_age_seconds=86400):
    if maintenance_confirmed is not True:
        raise ValueError('Stop all uploaders and workers before maintenance.')
    if type(min_age_seconds) is not int or min_age_seconds < 3600:
        raise ValueError('Minimum orphan age is one hour.')
    root = storage_root()
    cutoff = time.time() - min_age_seconds
    found = removed = failed = 0
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for path in root.iterdir():
            if not re.fullmatch(r'[0-9a-f]{48}\.upload', path.name):
                continue
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mtime > cutoff:
                    continue
                if db.execute('SELECT 1 FROM temporary_resume_files WHERE file_id=?', (path.stem,)).fetchone():
                    continue
                if db.execute('SELECT 1 FROM analysis_job_inputs WHERE file_id=?', (path.stem,)).fetchone():
                    continue
                found += 1
                if not dry_run:
                    path.unlink()
                    removed += 1
            except OSError:
                failed += 1
    return {'found': found, 'removed': removed, 'failed': failed}
