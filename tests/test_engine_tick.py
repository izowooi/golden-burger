from __future__ import annotations

import json

import pytest
import yaml

from polylab import db, registry, settings
from polylab.engine.tick import run, sanitize
from polylab.execution.clob import Clob
from test_execution_flows import FakeClient
from test_strategy_fixtures import T0, Env, level_book


def write_variant(d, vid, family, mode, sports, params=None, account=None, limits=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.yaml").write_text(yaml.safe_dump({
        "id": vid, "family": family, "hypothesis": "t", "account": account, "mode": mode, "sports": sports,
        "stake_usdc": 5, "params": params or {}, "bounds": {},
        "limits": limits or {"max_positions": 5, "max_open_usdc": 50, "daily_loss_stop_usdc": 20,
                             "max_new_per_cycle": 5}}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.delenv("POLYLAB_KILL", raising=False)
    e = Env(tmp_path / "root")
    e.us_game("g", "nfl", T0)
    e.core.execute("UPDATE markets SET fee_schedule='{\"feesEnabled\": false}'")
    e.core.commit()
    return e


def positions(env, vid):
    conn = db.strategy(env.paths, vid)
    return [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY opened_at")]


def test_paper_entry_then_stop_exit(env, tmp_path):
    reg = tmp_path / "reg"
    write_variant(reg, "wm-paper", "watermelon", "paper", ["nfl"], {"hours_max": 6})
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    s = run(env.paths, registry_dir=reg, poll=False, now=now)
    assert s["ok"], s
    [p] = positions(env, "wm-paper")
    assert p["status"] == "open" and p["mode"] == "paper" and p["entry_price"] == pytest.approx(0.95)
    assert json.loads(p["exit_rules"])["stop_price"] == 0.65
    # second tick: same token not re-entered, no exit while bid above stop
    s = run(env.paths, registry_dir=reg, poll=False, now=now + 60)
    level_book(env, "g-h", now + 110, 0.94, 0.95)
    assert len(positions(env, "wm-paper")) == 1
    level_book(env, "g-h", now + 120, 0.60, 0.62)
    s = run(env.paths, registry_dir=reg, poll=False, now=now + 120)
    [p] = positions(env, "wm-paper")
    assert p["status"] == "closed" and p["exit_reason"] == "stop_loss"
    assert p["realized_pnl"] == pytest.approx(p["shares"] * 0 + p["proceeds_usdc"] - p["cost_usdc"])
    assert p["realized_pnl"] < 0
    core = db.core(env.paths)
    runs = core.execute("SELECT ok, summary FROM job_runs WHERE job='polylab-tick'").fetchall()
    assert len(runs) == 3 and all(r["ok"] == 1 for r in runs)


def test_resolution_settles_paper_position(env, tmp_path):
    reg = tmp_path / "reg"
    write_variant(reg, "wm-paper", "watermelon", "paper", ["nfl"], {"hours_max": 6})
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    run(env.paths, registry_dir=reg, poll=False, now=now)
    env.resolve("c-g", 0, now + 600)
    run(env.paths, registry_dir=reg, poll=False, now=now + 700)
    [p] = positions(env, "wm-paper")
    assert p["status"] == "resolved" and p["exit_reason"] == "resolution_win" and p["realized_pnl"] > 0


def test_kill_switch_blocks_entries_only(env, tmp_path):
    reg = tmp_path / "reg"
    write_variant(reg, "wm-paper", "watermelon", "paper", ["nfl"], {"hours_max": 6})
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    (env.paths.state / "KILL").write_text("stop")
    s = run(env.paths, registry_dir=reg, poll=False, now=now)
    assert s["kill_switch"] and positions(env, "wm-paper") == []
    (env.paths.state / "KILL").unlink()
    run(env.paths, registry_dir=reg, poll=False, now=now)
    assert len(positions(env, "wm-paper")) == 1


def test_kill_env(env, tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    write_variant(reg, "wm-paper", "watermelon", "paper", ["nfl"], {"hours_max": 6})
    level_book(env, "g-h", T0 + 3590, 0.94, 0.95)
    monkeypatch.setenv("POLYLAB_KILL", "1")
    run(env.paths, registry_dir=reg, poll=False, now=T0 + 3600)
    assert positions(env, "wm-paper") == []


def test_live_without_credentials_fails_closed_others_continue(env, tmp_path):
    reg = tmp_path / "reg"
    write_variant(reg, "wm-live", "watermelon", "live", ["nfl"], {"hours_max": 6}, account="nobody")
    write_variant(reg, "wm-paper", "watermelon", "paper", ["nfl"], {"hours_max": 6})
    level_book(env, "g-h", T0 + 3590, 0.94, 0.95)
    s = run(env.paths, registry_dir=reg, poll=False, now=T0 + 3600)
    res = {r["variant_id"]: r for r in s["variants"]}
    assert not res["wm-live"]["ok"] and "credentials missing" in res["wm-live"]["error"]
    assert res["wm-paper"]["ok"] and res["wm-paper"]["entries"] == 1
    assert positions(env, "wm-live") == []


def test_dry_run_writes_no_positions(env, tmp_path):
    reg = tmp_path / "reg"
    write_variant(reg, "wm-paper", "watermelon", "paper", ["nfl"], {"hours_max": 6})
    level_book(env, "g-h", T0 + 3590, 0.94, 0.95)
    s = run(env.paths, registry_dir=reg, poll=False, now=T0 + 3600, dry_run=True)
    assert positions(env, "wm-paper") == []
    conn = db.strategy(env.paths, "wm-paper")
    assert conn.execute("SELECT COUNT(*) FROM decisions WHERE action='enter'").fetchone()[0] == 1


def test_live_with_fake_clob_and_paper_all(env, tmp_path):
    reg = tmp_path / "reg"
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "accounts.env").write_text(
        "POLYBOT_CAT__POLYMARKET_PRIVATE_KEY=0xabc\nPOLYBOT_CAT__POLYMARKET_FUNDER_ADDRESS=0xdef\n")
    write_variant(reg, "wm-live", "watermelon", "live", ["nfl"], {"hours_max": 6}, account="cat")
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    client = FakeClient(book={"g-h": {"bids": [{"price": "0.94", "size": "500"}],
                                      "asks": [{"price": "0.95", "size": "500"}]}},
                        fd={"r": 0.0, "e": 1, "to": True})
    s = run(env.paths, registry_dir=reg, poll=False, now=now, clob_factory=lambda c: Clob(client))
    assert s["ok"], s
    [p] = positions(env, "wm-live")
    assert p["mode"] == "live" and p["status"] == "pending" and len(client.posted) == 1
    client.trades["t1"]["status"] = "CONFIRMED"
    run(env.paths, registry_dir=reg, poll=False, now=now + 60, clob_factory=lambda c: Clob(client))
    [p] = positions(env, "wm-live")
    assert p["status"] == "open" and p["entry_fee_usdc"] == 0.0
    # --paper-all never touches live exposure or the client
    s = run(env.paths, registry_dir=reg, poll=False, now=now + 120, paper_all=True,
            clob_factory=lambda c: pytest.fail("paper must not build a client"))
    live_rows = [q for q in positions(env, "wm-live") if q["mode"] == "live"]
    assert len(live_rows) == 1 and live_rows[0]["status"] == "open"
    assert s["ok"] and len(client.posted) == 1


def test_sanitize_strips_addresses_and_queries():
    msg = "GET https://data-api.polymarket.com/v2/positions?user=0x" + "a" * 40 + " failed key 0x" + "b" * 64
    out = sanitize(msg)
    assert "aaaa" not in out and "bbbb" not in out and "user=" not in out


def test_repo_registry_loads_eight_variants():
    vs = registry.load_all()
    ids = {v.id for v in vs}
    assert {"watermelon-cat", "watermelon-dog", "apricot-eco", "apricot-fruit", "plum-king", "plum-queen",
            "cherry-blue", "cherry-yellow"} <= ids
    for v in vs:
        if v.id in ids:
            assert v.stake_usdc == 5.0 and v.mode in ("live", "paper")
            assert set(v.limits) >= {"max_positions", "max_open_usdc", "daily_loss_stop_usdc", "max_new_per_cycle"}


def test_live_stop_uses_fresh_clob_book_when_stored_book_is_stale(env, tmp_path):
    reg = tmp_path / "reg"
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "accounts.env").write_text(
        "POLYBOT_CAT__POLYMARKET_PRIVATE_KEY=0xabc\nPOLYBOT_CAT__POLYMARKET_FUNDER_ADDRESS=0xdef\n")
    write_variant(reg, "wm-live", "watermelon", "live", ["nfl"], {"hours_max": 6}, account="cat")
    now = T0 + 3600
    level_book(env, "g-h", now - 10, 0.94, 0.95)
    # real CLOB /book shape: string levels, bids ascending, asks descending
    client = FakeClient(book={"g-h": {"bids": [{"price": "0.93", "size": "5"}, {"price": "0.94", "size": "500"}],
                                      "asks": [{"price": "0.99", "size": "9"}, {"price": "0.95", "size": "500"}]}},
                        fd={"r": 0.05, "e": 1, "to": True})
    run(env.paths, registry_dir=reg, poll=False, now=now, clob_factory=lambda c: Clob(client))
    client.trades["t1"]["status"] = "CONFIRMED"
    run(env.paths, registry_dir=reg, poll=False, now=now + 60, clob_factory=lambda c: Clob(client))
    [p] = positions(env, "wm-live")
    assert p["status"] == "open" and p["entry_fee_usdc"] > 0     # 5% sports taker fee applied
    # collector stalls (stored book goes stale); the live book collapses below the stop
    client.books["g-h"] = {"bids": [{"price": "0.60", "size": "500"}], "asks": [{"price": "0.62", "size": "500"}]}
    run(env.paths, registry_dir=reg, poll=False, now=now + 1200, clob_factory=lambda c: Clob(client))
    assert len(client.posted) == 2 and client.posted[1].args.side == "SELL"
    [p] = positions(env, "wm-live")
    assert p["status"] == "closing" and p["exit_reason"] == "stop_loss"
    conn = db.strategy(env.paths, "wm-live")
    assert "maker_address" not in json.dumps([dict(r) for r in conn.execute("SELECT response FROM orders")])
