"""Database-scoped cycle lock contract."""

from __future__ import annotations

import pytest

from polybot.utils.run_lock import RunLockUnavailable, db_run_lock


def test_same_database_lock_is_nonblocking_and_different_database_is_independent(
    tmp_path,
):
    first = tmp_path / "arm-a" / "trades.db"
    second = tmp_path / "arm-b" / "trades.db"
    with db_run_lock(first) as lock_path:
        assert lock_path.name == "trades.db.run.lock"
        with pytest.raises(RunLockUnavailable, match="another cycle"):
            with db_run_lock(first):
                pass
        with db_run_lock(second):
            pass


def test_lock_is_released_after_context_exit(tmp_path):
    database = tmp_path / "trades.db"
    with db_run_lock(database):
        pass
    with db_run_lock(database):
        pass
