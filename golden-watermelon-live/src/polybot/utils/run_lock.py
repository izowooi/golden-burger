"""Non-blocking per-runtime lock for sub-minute-safe Jenkins scheduling."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Iterator


@contextmanager
def exclusive_job_run_lock(lock_path: Path) -> Iterator[bool]:
    """Yield false when a prior cycle still owns this runtime.

    The lock is advisory and process-scoped.  A crashed process releases it
    automatically, so there is no stale-lock deletion procedure and no DB
    mutation merely because two Jenkins triggers overlap.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
