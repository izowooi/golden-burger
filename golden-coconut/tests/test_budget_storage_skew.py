from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from polybot.api.transport import (
    CycleBudget,
    DeadlineExceeded,
    receipt_skew_seconds,
)
from polybot.db.repository import (
    GIB,
    classify_storage_guard,
    inspect_storage,
    slot_start_utc,
)


class Clock:
    def __init__(self, value=0):
        self.value = value

    def __call__(self):
        return self.value


def test_cooperative_stop_margin_and_hard_deadline():
    clock = Clock(0)
    budget = CycleBudget(0, 225, 30, 240, monotonic=clock)
    clock.value = 194.9
    budget.ensure_can_start_request("phase")
    clock.value = 195
    with pytest.raises(DeadlineExceeded, match="cooperative"):
        budget.ensure_can_start_request("phase")
    clock.value = 241
    with pytest.raises(DeadlineExceeded, match="hard"):
        budget.assert_within_hard_deadline()


def test_atomic_five_minute_slot_floor():
    value = datetime(2026, 8, 27, 12, 7, 59, tzinfo=timezone.utc)
    assert slot_start_utc(value).isoformat() == "2026-08-27T12:05:00+00:00"
    with pytest.raises(ValueError):
        slot_start_utc(value, 10)


def test_receipt_skew_boundary():
    assert receipt_skew_seconds(
        ["2026-08-27T00:00:00Z", "2026-08-27T00:01:30Z"]
    ) == 90
    assert receipt_skew_seconds(
        ["2026-08-27T00:00:00Z", "2026-08-27T00:01:31Z"]
    ) == 91


def test_storage_guard_free_warn_and_stop():
    assert classify_storage_guard(
        total_bytes=1000 * GIB,
        used_bytes=500 * GIB,
        free_bytes=500 * GIB,
        min_free_gib=150,
        warn_used_ratio=0.70,
        stop_used_ratio=0.80,
    )[0] == "OK"
    assert classify_storage_guard(
        total_bytes=1000 * GIB,
        used_bytes=750 * GIB,
        free_bytes=250 * GIB,
        min_free_gib=150,
        warn_used_ratio=0.70,
        stop_used_ratio=0.80,
    )[0] == "WARN"
    assert classify_storage_guard(
        total_bytes=1000 * GIB,
        used_bytes=790 * GIB,
        free_bytes=149 * GIB,
        min_free_gib=150,
        warn_used_ratio=0.70,
        stop_used_ratio=0.80,
    )[0] == "STOP"


def test_inspect_storage_uses_frozen_limits(config):
    usage = SimpleNamespace(total=1000 * GIB, used=750 * GIB, free=250 * GIB)
    metric = inspect_storage(
        config.db_path,
        config.trading.storage,
        disk_usage=lambda path: usage,
    )
    assert metric["guard_state"] == "WARN"
