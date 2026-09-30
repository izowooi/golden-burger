from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from polylab.analysis.backtest import active_steps, backtest, main, summarize
from test_strategy_fixtures import T0, Env, level_book


def wm_variant(**params):
    return SimpleNamespace(id="wm-bt", family="watermelon", sports=["nfl"], mode="live", stake_usdc=5.0,
                           params={"hours_max": 6, **params}, limits={"max_positions": 5, "max_new_per_cycle": 5},
                           bounds={}, account=None)


def _history(env):
    env.us_game("g", "nfl", T0, status="ended", ended_at=T0 + 4 * 3600)
    env.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    for m in range(0, 180):
        t = T0 + m * 60
        env.bar("g-h", t, 0.80 if m < 60 else 0.94, source="history")
        env.bar("g-a", t, 0.20 if m < 60 else 0.06, source="history")
    env.resolve("c-g", 0, T0 + 5 * 3600)


def test_backtest_synthetic_book_entry_and_resolution(tmp_path):
    env = Env(tmp_path / "root")
    _history(env)
    out = backtest(env.paths, wm_variant(), T0 - 3600, T0 + 24 * 3600, step=60, spread=0.01)
    s = out["summary"]
    assert s["n"] == 1 and s["wins"] == 1 and s["fee_unknown_trades"] == 0
    [t] = out["trades"]
    assert t["exit_reason"] == "resolution_win" and t["entry_price"] == pytest.approx(0.945)
    assert t["pnl"] == pytest.approx(5 / 0.945 - 5, rel=1e-6)
    assert t["opened_at"] == T0 + 3600 and not out["books"]
    again = backtest(env.paths, wm_variant(), T0 - 3600, T0 + 24 * 3600, step=60, spread=0.01)
    assert json.dumps(again, sort_keys=True) == json.dumps(out, sort_keys=True)


def test_backtest_stop_and_param_override(tmp_path, monkeypatch):
    env = Env(tmp_path / "root")
    env.us_game("g", "nfl", T0, status="ended", ended_at=T0 + 4 * 3600)
    for m in range(0, 180):
        env.bar("g-h", T0 + m * 60, 0.94 if m < 30 else 0.55)
        env.bar("g-a", T0 + m * 60, 0.06 if m < 30 else 0.45)
    env.resolve("c-g", 1, T0 + 5 * 3600)
    out = backtest(env.paths, wm_variant(), T0, T0 + 86400, step=60)
    [t] = out["trades"]
    assert t["exit_reason"] == "stop_loss" and t["pnl"] < 0
    assert out["summary"]["fee_unknown_trades"] == 1          # no stored schedule: flagged, not zeroed
    none = backtest(env.paths, wm_variant(prob_min=0.95), T0, T0 + 86400, step=60)
    assert none["summary"]["n"] == 0
    # CLI + --params merge
    monkeypatch.setenv("POLYLAB_ROOT", str(env.paths.root))
    reg = tmp_path / "wm-bt.yaml"
    reg.write_text(json.dumps({"id": "wm-bt", "family": "watermelon", "mode": "paper", "sports": ["nfl"],
                               "stake_usdc": 5, "params": {"hours_max": 6}}))
    out_file = tmp_path / "bt.json"
    assert main(["--variant", "wm-bt", "--variant-file", str(reg), "--from", "2026-09-01", "--to", "2026-10-01",
                 "--params", '{"prob_min": 0.95}', "--out", str(out_file)]) == 0
    assert json.loads(out_file.read_text())["params"]["prob_min"] == 0.95


def test_backtest_uses_stored_books_when_present(tmp_path):
    env = Env(tmp_path / "root")
    env.us_game("g", "nfl", T0, status="ended", ended_at=T0 + 4 * 3600)
    env.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    level_book(env, "g-h", T0 + 600, 0.955, 0.96)
    env.resolve("c-g", 0, T0 + 5 * 3600)
    out = backtest(env.paths, wm_variant(), T0, T0 + 86400, step=60)
    assert out["books"] and out["trades"][0]["entry_price"] == pytest.approx(0.96)


def test_active_steps_skip_dead_time(tmp_path):
    env = Env(tmp_path / "root")
    env.us_game("g", "nfl", T0 + 86400)
    steps = active_steps(env.core, wm_variant(), T0, T0 + 3 * 86400, 60)
    assert steps[0] == T0 + 86400 and len(steps) == 7 * 60
    assert summarize([])["n"] == 0
