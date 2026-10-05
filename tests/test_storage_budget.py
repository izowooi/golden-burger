"""Storage budget (ops/storage.py), its health/attention/report wiring, and the raw WS archive knob."""

from __future__ import annotations

import json
import os
import time

import pytest

from polylab.collector import stream
from polylab.ops import health, storage
from polylab.settings import Paths

DAY = 86400
NOW = 1_791_158_400        # 2026-10-05T00:00:00Z


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "root").ensure()


def write(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * n)


def test_raw_dated_dirs_and_samples_growth(paths):
    for i in range(1, 8):
        day = time.strftime("%Y-%m-%d", time.gmtime(NOW - i * DAY))
        write(paths.raw_dir / day / "market-00.jsonl.gz", 2_000_000_000 if i == 1 else 1_000_000_000)
    write(paths.data / "core.db", 1_000_000_000)
    base = storage.measure(paths)
    samples = [{"ts": NOW - 7 * DAY, "sizes": base}]
    (paths.state / "storage_samples.json").write_text(json.dumps(samples))
    write(paths.data / "core.db", 1_700_000_000)                    # +0.7 GB in 7 days
    st = storage.status(paths, NOW)
    raw = st["areas"]["raw"]
    assert raw["method"] == "raw_dated_dirs" and raw["gb_per_day"] == pytest.approx(8 / 7, abs=1e-3)
    assert st["areas"]["core"]["method"] == "samples" and st["areas"]["core"]["gb_30d"] == pytest.approx(3.0)
    assert st["projected_30d_gb"] == pytest.approx(round(8 / 7 * 30 + 3.0, 2), abs=0.05)
    assert st["level"] == "ok"
    # a fresh sample was recorded, at most hourly
    assert len(storage.load_samples(paths)) == 2
    storage.status(paths, NOW + 60)
    assert len(storage.load_samples(paths)) == 2


def test_budget_levels_and_health_check():
    assert storage.budget_level(None) is None and storage.budget_level(40) == "ok"
    assert storage.budget_level(60) == "warn" and storage.budget_level(120) == "critical"
    h = {"now": NOW, "collector": {}, "jobs": [],
         "storage": {"projected_30d_gb": 120.0, "level": "critical", "budget_warn_gb": 50, "budget_crit_gb": 100,
                     "areas": {"raw": {"gb_30d": 100.0}, "core": {"gb_30d": 20.0}}}}
    [p] = [p for p in health.checks(h) if p["key"] == "storage_budget"]
    assert p["level"] == "critical" and "raw 100GB" in p["message"]
    h["storage"]["level"] = "ok"
    assert not [p for p in health.checks(h) if p["key"] == "storage_budget"]


def test_attention_and_report_lines():
    from polylab.autopilot import attention
    from polylab.reports.render import storage_lines
    st = {"projected_30d_gb": 70.0, "level": "warn", "budget_warn_gb": 50, "budget_crit_gb": 100, "total_gb_now": 9.1,
          "areas": {"raw": {"gb_30d": 60.0}, "core": {"gb_30d": 10.0}}, "unmeasured_areas": ["ou05"]}
    report = {"health": {"now": NOW, "collector": {}, "jobs": [], "storage": st}, "name": "x"}
    items = attention._health_items(report, "reports/daily/x.md")
    [it] = [i for i in items if i["id"] == "storage_budget"]
    assert it["severity"] == "warn" and it["category"] == "risk"
    lines = storage_lines(st)
    assert "70GB/월" in lines[0] and "raw 60" in lines[0] and "ou05" in lines[1]
    assert storage_lines(None) == []


def test_compact_lists_only_quiet_closed_month_shards(paths):
    old = paths.data / "books" / "2026-08.db"
    cur = paths.data / "books" / "2026-10.db"
    busy = paths.data / "ou05" / "2026-09.db"
    for p in (old, cur, busy):
        import sqlite3
        p.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(p).execute("CREATE TABLE t(x)").connection.close()
    os.utime(old, (NOW - 3 * DAY, NOW - 3 * DAY))
    os.utime(busy, (NOW - 3600, NOW - 3600))
    names = [p.name for p in storage.closed_shards(paths, NOW)]
    assert names == ["2026-08.db"]
    assert storage.compact(paths, NOW, apply=False)[0]["shard"] == "books/2026-08.db"
    assert "mb_after" in storage.compact(paths, NOW, apply=True)[0]


def test_raw_archive_modes():
    pc = {"event_type": "price_change", "price_changes": [{"asset_id": "a"}]}
    bk = {"event_type": "book", "asset_id": "a"}
    text = json.dumps([pc, bk])
    assert stream.archive_text(text, [pc, bk], "full") == text
    assert stream.archive_text(text, [pc, bk], "off") is None
    assert json.loads(stream.archive_text(text, [pc, bk], "lean")) == bk
    assert stream.archive_text(json.dumps(pc), [pc], "lean") is None
    assert stream.archive_text(json.dumps(bk), [bk], "lean") == json.dumps(bk)


def test_raw_mode_env(monkeypatch):
    monkeypatch.delenv("POLYLAB_RAW_MARKET", raising=False)
    assert stream.raw_market_mode() == "lean"
    monkeypatch.setenv("POLYLAB_RAW_MARKET", "FULL")
    assert stream.raw_market_mode() == "full"
    monkeypatch.setenv("POLYLAB_RAW_MARKET", "bogus")
    assert stream.raw_market_mode() == "lean"
