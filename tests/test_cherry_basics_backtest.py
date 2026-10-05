"""polylab.analysis.cherry_basics: episode model on a hand-built data/general (entry incl. window-start state,
leading NO side, TP before/after hold, time exit, resolution, fees, H1/H2 split, caps replay)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from polylab.analysis import cherry_basics as cb
from polylab.general import store
from polylab.settings import Paths

H = 3600
END = 1_780_000_000 - 1_780_000_000 % 3600


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "root").ensure()


def add(paths, cid, prices, *, winner, volume=30000.0, category="politics", closed_at=None, end=END, event=None):
    reg = store.registry(paths)
    reg.execute(
        "INSERT INTO gen_markets(condition_id, event_id, category, tags, outcomes, yes_token, no_token, neg_risk, "
        "created_at, start_date, end_date, end_ref, closed, closed_at, resolved_index, volume, in_core, first_seen, "
        "updated_at, source, hist_status) VALUES(?,?,?,'[]',?,?,?,0,?,?,?,?,1,?,?,?,0,0,0,'history_enum','done')",
        (cid, event or cid, category, json.dumps(["Yes", "No"]), cid + "y", cid + "n", end - 10 * 86400,
         end - 10 * 86400, end, end, closed_at or end + H, winner, volume))
    gid = reg.execute("SELECT id FROM gen_markets WHERE condition_id=?", (cid,)).fetchone()[0]
    reg.commit()
    reg.close()
    store.write_quotes(paths, [(gid, end - int(h * H), None, None, None, None, None, p, store.SRC_HISTORY)
                               for h, p in prices])


def cell(d, *, target=("abs", 0.95), hold=0.99, X=None, window=(48, 72), lo=0.90, hi=0.95, floor=10000.0):
    return cb.one_cell(d, (window, lo, hi, target, hold, X, floor), 1.0)


def test_episode_paths(paths):
    # A: in band at the window start (state from 80 h), TP 0.95 later -> sells at 0.95
    add(paths, "A", [(80, 0.91), (40, 0.97)], winner=0)
    # B: NO leads (YES 0.08 -> NO 0.92), drifts to resolution, NO wins
    add(paths, "B", [(60, 0.08), (30, 0.06)], winner=1)
    # C: enters at 50 h, collapses, loses at resolution (no stop)
    add(paths, "C", [(50, 0.91), (20, 0.40)], winner=1)
    # D: jumps straight to 0.9995 (bid >= 0.99) -> hold suppresses TP; resolves YES
    add(paths, "D", [(55, 0.91), (10, 0.9995)], winner=0)
    # E: never in band
    add(paths, "E", [(70, 0.70), (10, 0.75)], winner=0)
    # F: below the volume floor
    add(paths, "F", [(60, 0.91), (10, 0.99)], winner=0, volume=5000.0)
    d = cb.load(paths)
    assert d.n_markets == 5
    c = cell(d)
    got = {d.mk["gid"][i]: (r, round(float(p), 4)) for i, r, p in zip(c["_idx"], c["_reason"], c["_pnl"])}
    hs = cb.SPREAD_BASE[1][1] / 2
    fee = cb.CATEGORY_FEE["politics"]
    fill = 0.91 + hs
    sh = 1 / fill
    cost = 1 + fee * (1 - fill)
    assert got[1] == (0, round(sh * 0.95 * (1 - fee * 0.05) - cost, 4))           # A: TP at 0.95
    assert got[2][0] == 2 and got[2][1] == pytest.approx(round(1 / (0.92 + hs) - (1 + fee * (1 - 0.92 - hs)), 4),
                                                         abs=1e-4)                  # B: NO wins
    assert got[3] == (2, round(-cost, 4))                                          # C: total loss
    assert got[4][0] == 2 and got[4][1] > 0                                        # D: held to resolution
    assert 5 not in got                                                            # E: no entry
    # without hold, D takes profit at 0.95
    c2 = cell(d, hold=None)
    d_reason = dict(zip(d.mk["gid"][c2["_idx"]], c2["_reason"]))
    assert d_reason[4] == 0
    # time exit 24 h before end: C sells at the forward-filled 0.91 state? no: C's state at 24 h is 0.91 -> bid
    c3 = cell(d, target=("none", None), hold=None, X=24.0)
    r3 = dict(zip(d.mk["gid"][c3["_idx"]], zip(c3["_reason"], c3["_exit_ts"])))
    assert r3[3] == (1, END - 24 * H)
    # the time exit inside the entry window is not a grid cell
    assert not any(c["time_exit_h"] == 24.0 for c in cb.simulate(d, windows=((12, 36),), band_list=[(0.9, 0.95)],
                                                                  targets=(("none", None),), holds=(None,)))


def test_summary_split_caps_and_ci(paths):
    for i in range(12):
        add(paths, f"m{i}", [(60, 0.91), (30, 0.97)], winner=0, end=END + i * 86400 * 10)
    d = cb.load(paths)
    c = cell(d)
    split = END + 55 * 86400
    s = cb.summarize(d, c, split, categories=True)
    assert s["all"]["n"] == 12 and s["h1"]["n"] + s["h2"]["n"] == 12 and s["exits"]["tp"] == 12
    assert s["by_category"]["politics"]["all"]["n"] == 12 and s["all"]["roi"] > 0
    ci = cb.bootstrap_ci(d, c)
    assert ci[0] <= s["all"]["roi"] <= ci[1]
    pf = cb.portfolio(d, c, max_positions=1, max_open=5.0)
    assert pf["n"] == 12 and pf["pnl_usdc"] > 0
    assert np.all(c["_exit_ts"] >= c["_ts"])


def test_tp_only_when_net_positive(paths):
    # entry 0.94 + hs ≈ 0.948: a 0.95 sale cannot beat cost after two fees -> held to resolution instead
    add(paths, "G", [(60, 0.94), (40, 0.959), (10, 0.97)], winner=0)
    d = cb.load(paths)
    c = cell(d, lo=0.92, hi=0.96)
    assert int(c["_reason"][0]) == 0 and int(c["_exit_ts"][0]) == END - 10 * H   # sold later at a net-positive bid
    fill = 0.94 + cb.SPREAD_BASE[1][1] / 2
    b = cb.net_positive_bid(np.array([fill]), np.array([0.04]))[0]
    assert 0.95 < b < 0.96
    assert cb.net_positive_bid(np.array([0.9]), np.array([0.0]))[0] == pytest.approx(0.9)
