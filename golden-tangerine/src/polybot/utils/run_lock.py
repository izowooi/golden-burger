"""Nonblocking process lock scoped to one Tangerine runtime database."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
from typing import Iterator


class RunLockUnavailable(RuntimeError):
    """Another process already owns this database's cycle lock."""


@contextmanager
def db_run_lock(db_path: str | Path) -> Iterator[Path]:
    """Hold a sibling inode lock without holding SQLite across network I/O."""
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
