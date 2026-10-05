"""analysis.ou05 aggregates + publish_ou05 hook on a synthetic registry/shards (no network)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from polylab import db as dbmod
from polylab.analysis import ou05 as A
from polylab.ou05 import store
from polylab.publish import snapshot
from polylab.settings import Paths

NOW = 1791158400  # 2026-10-05T00:00:00Z
KICK = NOW - 86400


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path))
    return Paths(tmp_path).ensure()


def add_market(reg, cid, league, volume, resolved_over, kick=KICK):
    reg.execute("INSERT INTO ou05_markets(condition_id, game_key, league, home, away, question, over_token, "
                "under_token, created_at, game_start, closed, closed_at, resolved_over, volume, liquidity, first_seen, "
                "updated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, "g" + cid, league, "H", "A", "H vs. A: O/U 0.5", cid + "o", cid + "u", kick - 10 * 86400, kick,
                 1, kick + 7200, resolved_over, volume, volume / 2, kick, kick, "discover"))


def poll_row(cid, ts, bid, ask):
    r = {k: None for k in store.QUOTE_COLS}
    r.update(condition_id=cid, ts=ts, over_bid=bid, over_ask=ask, under_bid=round(1 - ask, 4),
             under_ask=round(1 - bid, 4), over_mid=(bid + ask) / 2, sum_ask=round(ask + 1 - bid, 4),
             sum_bid=round(bid + 1 - ask, 4), source="poll")
    return tuple(r[k] for k in store.QUOTE_COLS)


@pytest.fixture()
def data(paths):
    reg = store.registry(paths)
    add_market(reg, "0x" + "a" * 64, "epl", 50_000, 1)
    add_market(reg, "0x" + "b" * 64, "kor2", 50, 0)
    reg.commit()
    rows = []
    a, b = "0x" + "a" * 64, "0x" + "b" * 64
    # market a: 2-hour pre-kickoff spread 0.02 for 50 min, then 0.06 for 10 min (heartbeat rows every 10 min)
    for k in range(0, 50, 10):
        rows.append(poll_row(a, KICK - 7200 + 60 * k, 0.92, 0.94))
    rows.append(poll_row(a, KICK - 7200 + 3000, 0.90, 0.96))
    rows.append(poll_row(a, KICK - 3600, 0.92, 0.94))
    # a 50-minute hole before the next row: the row above stands for 60 s only
    rows.append(poll_row(a, KICK - 600, 0.92, 0.94))
    # market b: placeholder book 0.01/0.99 all day
    for k in range(0, 60, 10):
        rows.append(poll_row(b, KICK - 7200 + 60 * k, 0.01, 0.99))
    store.write_quotes(paths, rows)
    hist = []
    for cid, px in ((a, 0.93), (b, 0.95)):
        r = {k: None for k in store.QUOTE_COLS}
        r.update(condition_id=cid, ts=KICK - 3 * 86400, over_mid=px, under_mid=round(1 - px, 4), source="history")
        hist.append(tuple(r[k] for k in store.QUOTE_COLS))
    store.write_quotes(paths, hist)
    return a, b


def test_weights_cap_gaps():
    # 660 s (one skipped heartbeat run) still counts as unchanged up to the 600 s cap; > 780 s is a hole
    w = A.history_weights(np.array([0, 60, 720, 1320, 2200], dtype=float))
    assert list(w) == [60, 600, 600, 60, 60]


def test_wquantiles_time_weighted():
    q = A.wquantiles(np.array([1.02, 1.06]), np.array([3000.0, 600.0]), qs=(0.5, 0.9))
    assert q == {"p50": 1.02, "p90": 1.06}


def test_summary_bands_tiers_calibration(paths, data):
    a, b = data
    obj, markets = A.summary(paths, NOW)
    rows = {(r["tier"], r["band"]): r for r in obj["overround"]}
    major = rows[("major", "h1")]           # 1–6 h before kickoff
    assert major["markets"] == 1 and major["sum_ask"]["p50"] == pytest.approx(1.02)
    assert major["sum_ask"]["p90"] == pytest.approx(1.06) and major["spread"]["p50"] == pytest.approx(0.02)
    assert major["sum_bid"]["p50"] == pytest.approx(0.98)
    # 3000 s at spread .02 + 600 s at .06; the row at exactly 60 min before (15–60 min band) precedes a 50-minute
    # hole and stands for 60 s only, as does the last row (0–15 min band)
    assert major["minutes"] == 60 and rows[("major", "m15")]["minutes"] == 1 and rows[("major", "m0")]["minutes"] == 1
    assert rows[("vol_lt_1k", "h1")]["sum_ask"]["p50"] == pytest.approx(1.98)
    cal = {(r["tier"], r["band"]): r for r in obj["calibration"]}
    # pre-kickoff 1–6h: poll mid (spread <= .10) for a; b's 0.01/0.99 book is not a price -> history only elsewhere
    assert cal[("all", "h1")]["n"] == 1 and cal[("all", "h1")]["n_poll"] == 1
    assert cal[("all", "d1")]["n"] == 2 and cal[("all", "d1")]["over_rate"] == 0.5
    assert obj["scope"]["poll_rows"] == 14 and obj["scope"]["history_rows"] == 2
    assert {d["phase"] for d in obj["distribution"]["series"]} >= {"pre", "last24h"}


def test_run_and_publish_hook(paths, data, tmp_path):
    res = A.run(paths, now=NOW, export=True)
    assert res["browser_markets"] == 2 and any(p.endswith("ou05_markets.parquet") for p in res["parquet"])
    files = A.cached_files(paths)
    assert "latest/ou05/summary.json" in files and f"latest/ou05/markets/{data[0]}.json" in files
    m = json.loads(files[f"latest/ou05/markets/{data[0]}.json"].read_bytes())
    assert m["columns"][0] == "at" and m["points"] and m["resolved_over"] is True

    class FakeStorage:
        def __init__(self):
            self.paths = []

        def upload(self, path, body, content_type="application/json"):
            self.paths.append(path)

    st = FakeStorage()
    w1 = snapshot.publish_ou05(paths, st)
    assert "latest/ou05/summary.json" in w1 and len(st.paths) == len(w1)
    assert snapshot.publish_ou05(paths, st) == []                         # nothing changed: nothing uploaded
    A.run(paths, now=NOW + 3600, export=False)
    w3 = snapshot.publish_ou05(paths, st)
    assert sorted(w3) == ["latest/ou05/markets_index.json", "latest/ou05/summary.json"]   # markets/* unchanged


def test_publish_hook_noop_without_cache(paths):
    assert snapshot.publish_ou05(paths, None) == []
    assert not (store.ou05_dir(paths) / "registry.db").exists()
    assert dbmod.year_month(NOW) == "2026-10"
