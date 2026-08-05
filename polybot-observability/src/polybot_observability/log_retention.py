"""Bound local daily bot logs without touching unrelated files."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path


DEFAULT_LOG_RETENTION_DAYS = 60
_DAILY_LOG_NAME = re.compile(r"^(?P<day>\d{8})\.log$")


def prune_daily_logs(
    log_dir: str | Path,
    *,
    retention_days: int = DEFAULT_LOG_RETENTION_DAYS,
    today: date | datetime | None = None,
) -> tuple[Path, ...]:
    """Delete only ``YYYYMMDD.log`` files older than full retention days.

    The current partial day plus the preceding ``retention_days`` full calendar
    days are retained. Symlinks and non-matching files are never touched.
    """

    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days < 1
    ):
        raise ValueError("retention_days must be an integer >= 1")
    reference = today or date.today()
    if isinstance(reference, datetime):
        reference = reference.date()
    cutoff = reference - timedelta(days=retention_days)
    directory = Path(log_dir)
    if not directory.is_dir():
        return ()

    removed: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        match = _DAILY_LOG_NAME.fullmatch(path.name)
        if match is None:
            continue
        try:
            log_day = datetime.strptime(match.group("day"), "%Y%m%d").date()
        except ValueError:
            continue
        if log_day >= cutoff:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(path)
    return tuple(removed)
