"""Nonblocking process lock scoped to one bot database."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any


class DatabaseRunLock:
    """Advisory lock preventing overlapping runs against the same SQLite DB.

    The lock file is persistent, but ownership is the kernel-held ``flock`` on
    the open file descriptor.  Stale metadata can therefore never keep a run
    blocked after its owning process exits.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.path = self.db_path.with_name(f"{self.db_path.name}.run.lock")
        self._handle: IO[str] | None = None
        self.acquired = False
        self.owner: dict[str, Any] = {}

    def __enter__(self) -> "DatabaseRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            try:
                value = json.loads(handle.read(4096) or "{}")
                self.owner = value if isinstance(value, dict) else {}
            except (json.JSONDecodeError, OSError):
                self.owner = {}
            handle.close()
            return self

        self._handle = handle
        self.acquired = True
        self.owner = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(self.db_path),
        }
        handle.seek(0)
        handle.truncate()
        json.dump(self.owner, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
            self.acquired = False
