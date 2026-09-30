from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from polylab import db, settings
from polylab.execution import redeem
from polylab.execution.accounts import summarize_positions
from polylab.execution.ledger import StrategyLedger
from test_strategy_fixtures import T0, Env

FIX = Path(__file__).parent / "fixtures" / "polymarket_api"


def test_summarize_v2_positions_fixture():
    rows = json.loads((FIX / "data_v2_positions_redeemable.json").read_text())["data"]
    s = summarize_positions(rows)
    assert s["redeemable_count"] == sum(1 for r in rows if r["status"] == "REDEEMABLE")
    assert s["redeemable_usdc"] == pytest.approx(sum(r["current_value"] for r in rows if r["status"] == "REDEEMABLE"))
    assert redeem.redeemable_conditions(rows) == s["redeemable_conditions"]


def test_redeemable_conditions_excludes_losers():
    rows = [{"status": "REDEEMABLE", "redeemable": True, "condition_id": "a", "current_size": 5},
            {"status": "REDEEMABLE_LOST", "redeemable": True, "condition_id": "b", "current_size": 5},
            {"status": "REDEEMABLE", "redeemable": True, "condition_id": "c", "current_size": 0}]
    assert redeem.redeemable_conditions(rows) == ["a"]


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "accounts.env").write_text("POLYBOT_CAT__POLYMARKET_PRIVATE_KEY=0xabc\n"
                                    "POLYBOT_CAT__POLYMARKET_FUNDER_ADDRESS=0xdef\n")
    monkeypatch.setattr(settings, "SECRETS_DIR", d)
    return d


def test_redeem_dry_run_and_execute_updates_ledger(tmp_path, secrets):
    env = Env(tmp_path / "root")
    led = StrategyLedger(db.strategy(env.paths, "wm"), "wm")
    led.conn.execute("INSERT INTO positions(position_id, mode, param_version, stake_usdc, condition_id, token_id, "
                     "opened_at, status, settlement, redeemed) VALUES('p','live',1,5,'cond1','t',?, 'resolved',"
                     "'resolution',0)", (T0,))
    led.conn.commit()
    fetch = lambda user: [{"status": "REDEEMABLE", "redeemable": True, "condition_id": "cond1", "current_size": 5}]
    out = redeem.redeem_account("cat", execute=False, positions_fetcher=fetch, paths=env.paths, variant_id="wm")
    assert out["conditions"] == ["cond1"] and out["done"] == []

    calls = []

    class FakeSecure:
        def redeem_positions(self, condition_id):
            calls.append(condition_id)
            return SimpleNamespace(wait=lambda: SimpleNamespace(transaction_hash="0x" + "1" * 64))

    out = redeem.redeem_account("cat", execute=True, positions_fetcher=fetch, paths=env.paths, variant_id="wm",
                                client_factory=lambda c, a: FakeSecure())
    assert out["done"] == ["cond1"] and calls == ["cond1"]
    conn = db.strategy(env.paths, "wm")
    assert conn.execute("SELECT redeemed FROM positions").fetchone()[0] == 1
    assert [r[0] for r in conn.execute("SELECT status FROM redemptions ORDER BY id")] == ["dry_run", "done"]


def test_builder_key_cache_is_private(tmp_path, secrets):
    pytest.importorskip("polymarket")
    from polymarket import BuilderApiKey
    redeem.save_builder_key("cat", BuilderApiKey(key="k", secret="s", passphrase="p"))
    p = redeem.builder_key_path("cat")
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert redeem.load_builder_key("cat").key == "k"
    os.chmod(p, 0o644)
    with pytest.raises(PermissionError):
        redeem.load_builder_key("cat")
