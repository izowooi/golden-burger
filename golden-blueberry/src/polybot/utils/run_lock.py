"""Process-local-host exclusion for one Blueberry runtime database."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator


class RunLockUnavailable(RuntimeError):
    """Raised when another process already owns the database run lock."""


@contextmanager
def db_run_lock(db_path: str | Path) -> Iterator[Path]:
    """Acquire a nonblocking, process-safe lock scoped to ``db_path``.

    The lock is a sibling inode rather than a SQLite write transaction, so it
    excludes overlapping bot cycles without holding the database writer lock
    across network I/O.  ``flock`` is released automatically if the process
    exits, including after a crash.
    """

    database = Path(db_path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database.with_name(f"{database.name}.run.lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(descriptor, "a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RunLockUnavailable(
                f"another cycle owns the run lock for {database}"
            ) from error
        yield lock_path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
