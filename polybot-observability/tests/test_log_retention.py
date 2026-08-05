from datetime import date

import pytest

from polybot_observability import DEFAULT_LOG_RETENTION_DAYS, prune_daily_logs


def test_daily_log_pruning_is_bounded_and_file_name_safe(tmp_path):
    old = tmp_path / "20260531.log"
    boundary = tmp_path / "20260601.log"
    current = tmp_path / "20260731.log"
    unrelated = tmp_path / "jenkins.log"
    malformed = tmp_path / "20261340.log"
    for path in (old, boundary, current, unrelated, malformed):
        path.write_text(path.name, encoding="utf-8")
    symlink = tmp_path / "20260530.log"
    symlink.symlink_to(unrelated)

    removed = prune_daily_logs(
        tmp_path,
        retention_days=DEFAULT_LOG_RETENTION_DAYS,
        today=date(2026, 7, 31),
    )

    assert removed == (old,)
    assert not old.exists()
    assert boundary.exists()
    assert current.exists()
    assert unrelated.read_text(encoding="utf-8") == "jenkins.log"
    assert malformed.exists()
    assert symlink.is_symlink()


@pytest.mark.parametrize("retention_days", [True, 0, -1, 1.5])
def test_daily_log_pruning_rejects_invalid_retention(retention_days):
    with pytest.raises(ValueError, match="integer >= 1"):
        prune_daily_logs("unused", retention_days=retention_days)
