"""Private local temporary resume storage (development foundation only).

Not wired into HTTP uploads or worker processing. Data is NOT encrypted at rest.
Use a private dedicated filesystem; do not serve the directory as static files.
"""
import hashlib
import os
from pathlib import Path
import secrets
import stat
import time
from app.auth import connection, database_path

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 24 * 3600


def storage_root():
    value = os.getenv('RESUMEPILOT_TEMP_STORAGE')
    if value:
        root = Path(value).expanduser().absolute()
    else:
        root = Path(database_path()).expanduser().absolute().parent / 'private_uploads'
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            raise RuntimeError('Temporary storage path must not contain a symlink.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('Temporary storage root is unsafe.')
    root.chmod(0o700)
    return root


def initialize_temporary_storage():
    with connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS temporary_resume_files (
            file_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_count INTEGER NOT NULL CHECK(byte_count>0),
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")
        db.execute('CREATE INDEX IF NOT EXISTS temporary_resume_expiry_idx ON temporary_resume_files(expires_at)')


def _validate(filename, data, ttl_seconds):
    if not isinstance(filename, str) or not filename or len(filename) > 255 or '\x00' in filename:
        raise ValueError('Invalid filename.')
    if Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('Filename must not contain a path.')
    if Path(filename).suffix.lower() not in ('.pdf', '.docx'):
        raise ValueError('Only PDF and DOCX resumes are supported.')
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD_BYTES:
        raise ValueError('Resume must be 1 byte to 10 MB.')
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError('Invalid retention duration.')


def _path(root, file_id):
    if not isinstance(file_id, str) or len(file_id) != 48 or any(c not in '0123456789abcdef' for c in file_id):
        raise ValueError('Invalid file identifier.')
    return root / (file_id + '.upload')


def store_resume(user_id, filename, data, ttl_seconds=DEFAULT_TTL_SECONDS, now=None):
    """Create a private file and scoped metadata; return opaque ID, never a path."""
    _validate(filename, data, ttl_seconds)
    now = int(time.time()) if now is None else int(now)
    root = storage_root()
    file_id = secrets.token_hex(24)
    target = _path(root, file_id)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        with connection() as db:
            db.execute('INSERT INTO temporary_resume_files(file_id,user_id,filename,sha256,byte_count,created_at,expires_at) VALUES(?,?,?,?,?,?,?)',
                       (file_id,user_id,filename,hashlib.sha256(data).hexdigest(),len(data),now,now+ttl_seconds))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return file_id


def read_resume(user_id, file_id, now=None):
    """Return (original filename, bytes) only to owning account before expiry."""
    now = int(time.time()) if now is None else int(now)
    with connection() as db:
        row = db.execute('SELECT filename,sha256,byte_count FROM temporary_resume_files WHERE file_id=? AND user_id=? AND expires_at>?',
                         (file_id,user_id,now)).fetchone()
    if row is None:
        raise FileNotFoundError('Resume unavailable or expired.')
    target = _path(storage_root(),file_id)
    flags = os.O_RDONLY
    if hasattr(os,'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(target,flags)
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError('Unsafe resume file.')
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise OSError('Unsafe resume file permissions.')
        data = stream.read(MAX_UPLOAD_BYTES+1)
    if len(data) != row['byte_count'] or hashlib.sha256(data).hexdigest() != row['sha256']:
        raise OSError('Stored resume failed integrity verification.')
    return row['filename'],data


def cleanup_expired(now=None, limit=100):
    """Delete expired files and metadata. Call only during controlled maintenance."""
    now = int(time.time()) if now is None else int(now)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Cleanup limit must be between 1 and 1000.')
    root = storage_root()
    removed = 0
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT f.file_id FROM temporary_resume_files f WHERE f.expires_at<=? AND NOT EXISTS(SELECT 1 FROM analysis_job_inputs i WHERE i.file_id=f.file_id) ORDER BY f.expires_at,f.file_id LIMIT ?', (now,limit)).fetchall()
        for row in rows:
            target = _path(root,row['file_id'])
            # Unlinking a symlink removes the link, never its target.
            target.unlink(missing_ok=True)
            db.execute('DELETE FROM temporary_resume_files WHERE file_id=? AND expires_at<=?', (row['file_id'],now))
            removed += 1
    return removed


def delete_resume(user_id, file_id):
    """Idempotently remove one owned staged file and its metadata.

    Never accept a filesystem path from callers.
    """
    root = storage_root()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT 1 FROM temporary_resume_files WHERE user_id=? AND file_id=?',
                         (user_id, file_id)).fetchone()
        if row is None:
            return False
        # DB references must be removed before unlink. Failed unlink leaves an
        # orphan that a separate controlled maintenance pass can reconcile.
        db.execute('DELETE FROM temporary_resume_files WHERE user_id=? AND file_id=?',
                   (user_id, file_id))
    _path(root, file_id).unlink(missing_ok=True)
    return True
