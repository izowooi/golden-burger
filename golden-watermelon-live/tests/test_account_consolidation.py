"""Disposable real SQLite + fake orders. No wallet, remote, Jenkins or live DB."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from multiprocessing import Process, Queue
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest
from polybot_observability import ExecutionLedger
from polybot import config as configs
from polybot.account import (AccountGuardError, account_paths, account_session,
                             prepare_account, read_database)
from polybot.account_runner import run_account
from polybot.bot import PolymarketBot
from polybot.db.models import init_database, Trade, TradeStatus
from polybot.utils.run_lock import exclusive_job_run_lock


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no actual network in account tests")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def make_config(root, runtime):
    spec = configs.RUNTIME_SPECS[runtime]
    trading = configs.TradingConfig(sport_family=spec.sport_family)
    return configs.BotConfig(trading, configs.ApiConfig("fixture-unused", "0x"+"2"*40, 3),
                             root/"data"/runtime/"trades.db", False, runtime, spec.jenkins_job)


@pytest.fixture
def account(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(configs, "SOURCE_PROJECT_ROOT", root)
    monkeypatch.delenv("POLYBOT_ACCOUNT_DISABLE_BUYS", raising=False)
    names = configs.ACCOUNT_RUNTIMES["polybot-cat"]
    soccer, mlb = [make_config(root, name) for name in names[:2]]
    sessions = init_database(str(soccer.db_path))
    sessions.kw["bind"].dispose()
    ExecutionLedger(soccer.db_path, strategy_name="golden-watermelon-live")
    return soccer, mlb


def holdings(config, count):
    seed_identity(config)
    sessions = init_database(str(config.db_path))
    with sessions() as session:
        for i in range(count):
            session.add(Trade(condition_id=f"condition-{i}", event_id=f"event-{i}", token_id=f"token-{i}",
                              outcome="Yes", status=TradeStatus.HOLDING, buy_order_id=f"filled-{i}",
                              buy_confirmed_size=10, buy_confirmed_vwap=.5, buy_confirmed_fee_usdc=0,
                              sport_family=config.trading.sport_family, mode="live"))
        session.commit()
    sessions.kw["bind"].dispose()


def intent(config, *, token="pending", ago=0, failed=False):
    seed_identity(config)
    ledger = ExecutionLedger(config.db_path, strategy_name="golden-watermelon-live")
    sid = ledger.record_intent(token_id=token, side="BUY", requested_price=.5, requested_size=10, simulation=False)
    with closing(sqlite3.connect(config.db_path)) as con, con:
        stamp = (datetime.now(timezone.utc)-timedelta(seconds=ago)).isoformat(timespec="microseconds")
        con.execute("UPDATE order_submissions SET submitted_at=? WHERE submission_id=?", (stamp, sid))
        if failed:
            con.execute("UPDATE order_submissions SET response_status='FAILED' WHERE submission_id=?", (sid,))
    return sid


def seed_identity(config):
    with closing(sqlite3.connect(config.db_path)) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS run_audits(job_name TEXT,strategy_name TEXT,finished_at TEXT,cycle_stats_json TEXT)")
        if con.execute("SELECT 1 FROM run_audits").fetchone() is None:
            con.execute("INSERT INTO run_audits(job_name,strategy_name) VALUES (?,?)", (config.job_name, "golden-watermelon-live"))


def test_prepare_creates_only_new_mlb_and_preserves_soccer_bytes(account):
    soccer, mlb = account
    before = hashlib.sha256(soccer.db_path.read_bytes()).hexdigest()
    result = prepare_account(soccer)
    assert result["status"] == "PREPARED_NO_ORDERS" and mlb.db_path.exists()
    assert hashlib.sha256(soccer.db_path.read_bytes()).hexdigest() == before
    assert read_database(mlb.db_path, mlb.job_name)["slots"] == 0
    assert not list(soccer.db_path.parents[1].rglob("control.db"))
    before_mlb = hashlib.sha256(mlb.db_path.read_bytes()).hexdigest()
    assert prepare_account(soccer)["new_runtimes"] == []
    assert hashlib.sha256(mlb.db_path.read_bytes()).hexdigest() == before_mlb


def test_missing_peer_blocks_buy_but_own_management_lock_remains_valid(account):
    soccer, mlb = account
    with account_session(soccer) as guard:
        guard.assert_active(soccer)
        with pytest.raises(AccountGuardError):
            guard.check_buy_budget()
    assert not mlb.db_path.exists()


def test_aggregate_twenty_not_twenty_each_and_peer_reads_do_not_write(account):
    soccer, mlb = account
    prepare_account(soccer)
    holdings(soccer, 10)
    holdings(mlb, 10)
    before = {c.job_name: hashlib.sha256(c.db_path.read_bytes()).hexdigest() for c in account}
    for config in account:
        with account_session(config) as guard:
            with pytest.raises(AccountGuardError, match="20/5"):
                guard.approve_buy(5, SimpleNamespace(get_collateral_balance=lambda: 100))
    assert {c.job_name: hashlib.sha256(c.db_path.read_bytes()).hexdigest() for c in account} == before


def test_peer_untracked_intent_reserves_slot_and_cash_after_restart(account):
    soccer, mlb = account
    prepare_account(soccer)
    holdings(soccer, 19)
    intent(mlb, ago=120)
    assert read_database(mlb.db_path, mlb.job_name)["slots"] == 1
    with account_session(soccer) as guard:
        with pytest.raises(AccountGuardError, match="20/5"):
            guard.check_buy_budget()
    assert read_database(mlb.db_path, mlb.job_name)["reserved_cash"] == 5


def test_combined_buy_five_rolling_seconds_even_failed_attempts(account):
    soccer, mlb = account
    prepare_account(soccer)
    for i in range(3):
        intent(soccer, token=f"s-{i}", failed=True)
    for i in range(2):
        intent(mlb, token=f"m-{i}", failed=True)
    with account_session(soccer) as guard:
        with pytest.raises(AccountGuardError, match="20/5"):
            guard.check_buy_budget()
    for config in account:
        with closing(sqlite3.connect(config.db_path)) as con, con:
            con.execute("UPDATE order_submissions SET submitted_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=61)).isoformat(),))
    with account_session(mlb) as guard:
        assert guard.check_buy_budget()["attempts"] == 0


def test_pending_cash_reserve_and_no_new_database_claims(account):
    soccer, mlb = account
    prepare_account(soccer)
    intent(soccer, ago=120)
    with account_session(mlb) as guard:
        with pytest.raises(AccountGuardError, match="pending cash"):
            guard.approve_buy(5, SimpleNamespace(get_collateral_balance=lambda: Decimal(9)))
        assert guard.cash_failed
    for config in account:
        with closing(sqlite3.connect(config.db_path)) as con:
            assert not con.execute("SELECT 1 FROM sqlite_master WHERE name IN ('claims','cycles','metadata')").fetchall()


def test_capacity_nineteen_admits_and_twenty_reports_actual_combined_counter(account):
    soccer, mlb = account
    prepare_account(soccer)
    holdings(soccer, 19)
    with account_session(mlb) as guard:
        assert guard.approve_buy(5, SimpleNamespace(get_collateral_balance=lambda: 100))["slots"] == 19
    holdings(mlb, 1)
    with account_session(soccer) as guard:
        with pytest.raises(AccountGuardError) as captured:
            guard.check_buy_budget()
    assert captured.value.code == "account_max_positions"
    assert captured.value.evidence["total_reserved"] == 20
    assert captured.value.evidence["max_positions"] == 20
    assert captured.value.evidence["buy_attempts_last_60s"] == 0


def test_rate_limit_is_distinct_from_position_limit(account):
    soccer, mlb = account
    prepare_account(soccer)
    for i in range(5):
        intent(soccer if i < 3 else mlb, token=f"attempt-{i}", failed=True)
    with account_session(mlb) as guard:
        with pytest.raises(AccountGuardError) as captured:
            guard.check_buy_budget()
    assert captured.value.code == "account_buy_rate_limit"
    assert captured.value.evidence["total_reserved"] == 0
    assert captured.value.evidence["buy_attempts_last_60s"] == 5


def test_canceled_orphan_unknown_matched_size_keeps_twentieth_slot(account):
    soccer, mlb = account
    prepare_account(soccer)
    holdings(soccer, 19)
    sid = intent(mlb, ago=120)
    with closing(sqlite3.connect(mlb.db_path)) as con, con:
        con.execute("UPDATE order_submissions SET order_id='orphan-order',success=1,"
                    "response_status='CANCELED',latest_order_status='CANCELED',"
                    "needs_reconciliation=0,latest_size_matched=NULL WHERE submission_id=?", (sid,))
    assert read_database(mlb.db_path, mlb.job_name)["slots"] == 1
    with account_session(soccer) as guard:
        with pytest.raises(AccountGuardError) as captured:
            guard.check_buy_budget()
    assert captured.value.code == "account_max_positions"
    assert captured.value.evidence["total_reserved"] == 20
    with closing(sqlite3.connect(mlb.db_path)) as con, con:
        con.execute("UPDATE order_submissions SET latest_size_matched=0 WHERE submission_id=?", (sid,))
    with account_session(soccer) as guard:
        assert guard.check_buy_budget()["slots"] == 19


def test_cash_failure_in_successful_peer_run_blocks_buy_only(account):
    soccer, mlb = account
    prepare_account(soccer)
    with closing(sqlite3.connect(soccer.db_path)) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS run_audits (job_name TEXT,strategy_name TEXT,finished_at TEXT,cycle_stats_json TEXT)")
        con.execute("INSERT INTO run_audits(job_name,strategy_name,finished_at,cycle_stats_json) VALUES (?,?,?,?)",
                    (soccer.job_name, "golden-watermelon-live", datetime.now(timezone.utc).isoformat(), '{"account_cash_failed":true}'))
    with account_session(mlb) as guard:
        guard.assert_active(mlb)
        with pytest.raises(AccountGuardError, match="20/5"):
            guard.check_buy_budget()


def test_malformed_peer_and_wrong_runtime_fail_closed(account):
    soccer, mlb = account
    prepare_account(soccer)
    with closing(sqlite3.connect(mlb.db_path)) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS run_audits (job_name TEXT,strategy_name TEXT,finished_at TEXT,cycle_stats_json TEXT)")
        con.execute("INSERT INTO run_audits(job_name,strategy_name) VALUES ('watermelon-live-bear-mlb-96-1m-v3a','golden-watermelon-live')")
    with account_session(soccer) as guard:
        guard.assert_active(soccer)
        with pytest.raises(AccountGuardError):
            guard.check_buy_budget()
    with pytest.raises(AccountGuardError, match="foreign"):
        with account_session(mlb):
            pass


def test_registered_bot_cannot_bypass_account_session(account):
    soccer, mlb = account
    with pytest.raises(AccountGuardError, match="account_session"):
        PolymarketBot(soccer)


def _attempt_lock(path, queue):
    with exclusive_job_run_lock(Path(path)) as ok:
        queue.put(ok)


def test_child_account_lock_serializes_other_profile_and_releases_after_exit(account):
    soccer, mlb = account
    prepare_account(soccer)
    _, lock = account_paths("polybot-cat")
    queue = Queue()
    with account_session(soccer):
        process = Process(target=_attempt_lock, args=(str(lock), queue))
        process.start(); process.join(5)
        assert process.exitcode == 0 and queue.get(timeout=1) is False
    with account_session(mlb) as guard:
        assert guard.active


def test_runner_sequential_env_isolation_failure_still_runs_other_management(account, monkeypatch):
    soccer, mlb = account
    prepare_account(soccer)
    monkeypatch.setattr(configs, "load_config", lambda *a, **k: soccer)
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MAX", "4")
    monkeypatch.setenv("POLYBOT_SPORT_FAMILY", "soccer")
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", "close_only")
    calls = []
    def child(cmd, **kwargs):
        assert "timeout" not in kwargs
        runtime = cmd[cmd.index("--job")+1]
        cfg = soccer if runtime == soccer.job_name else mlb
        # Parent does not hold the child lock (no parent/child deadlock).
        with account_session(cfg):
            pass
        calls.append((runtime, kwargs["env"]))
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)
    assert run_account("polybot-cat", run_process=child) == 1
    assert {r for r, _ in calls} == set(configs.ACCOUNT_RUNTIMES["polybot-cat"])
    assert calls[1][1]["POLYBOT_ACCOUNT_DISABLE_BUYS"] == "1"
    for runtime, env in calls:
        assert env["POLYBOT_ENTRY_HOURS_MAX"] == {"soccer":"4","mlb":"8","nfl":"6"}[configs.RUNTIME_SPECS[runtime].sport_family]
        assert env["POLYBOT_LIFECYCLE_MODE"] == "close_only"
    import os
    assert os.environ["POLYBOT_ENTRY_HOURS_MAX"] == "4"


def test_new_mlb_runtime_and_policy_do_not_rewrite_old_six():
    assert len(configs.RUNTIME_SPECS) == 10
    for account, jobs in configs.ACCOUNT_RUNTIMES.items():
        assert "v2h" in jobs[0] and "mlb" in jobs[1]
        assert configs.RUNTIME_SPECS[jobs[1]].jenkins_job == account
    assert configs.SPORT_POLICIES["catdog_mlb"] is not configs.SPORT_POLICIES["mlb"]
    for name in ("soccer", "mlb", "nhl", "catdog_mlb"):
        policy = configs.SPORT_POLICIES[name]
        assert (policy.stop_price, policy.max_entry_drawdown, policy.max_positions) == (.70, .30, 20)


def test_exact_no_order_proof_does_not_leave_duplicate_cash_reservation(account):
    soccer, mlb = account
    prepare_account(soccer)
    sid = intent(soccer, ago=120)
    ledger = ExecutionLedger(soccer.db_path, strategy_name="golden-watermelon-live")
    ledger.resolve_uncertain_submission(sid, resolution="NO_ORDER_CREATED", reason="fixture exact no POST proof")
    state = read_database(soccer.db_path, soccer.job_name)
    assert state["slots"] == 0 and state["reserved_cash"] == 0


@pytest.mark.parametrize("account_name", ["polybot-cat", "polybot-dog"])
@pytest.mark.parametrize("lifecycle", ["active", "close_only"])
def test_real_config_profiles_keep_soccer_values_and_mlb_hours_without_env_leak(tmp_path, monkeypatch, account_name, lifecycle):
    import os
    yaml_path = Path(configs.__file__).resolve().parents[2] / "config.yaml"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOB_NAME", account_name)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "fixture-unused")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x"+"2"*40)
    monkeypatch.setenv("POLYBOT_LIFECYCLE_MODE", lifecycle)
    monkeypatch.setenv("POLYBOT_SPORT_FAMILY", "soccer")
    monkeypatch.setenv("POLYBOT_ENTRY_HOURS_MAX", "4")
    monkeypatch.setenv("POLYBOT_ARCHIVE_HOURS_MAX", "4")
    monkeypatch.setenv("POLYBOT_ENTRY_PROB_MIN", ".96" if account_name.endswith("cat") else ".99")
    baseline = dict(os.environ)
    loaded = []
    for runtime in configs.ACCOUNT_RUNTIMES[account_name]:
        child_env = configs.profile_environment(runtime, baseline)
        with monkeypatch.context() as child:
            for k, v in child_env.items():
                if k.startswith("POLYBOT_"):
                    child.setenv(k, v)
            loaded.append(configs.load_config(str(yaml_path), runtime, simulation_mode=False))
    assert dict(os.environ) == baseline
    soccer, mlb = loaded
    assert soccer.trading.entry.hours_max == soccer.trading.archive.hours_max == 4
    assert mlb.trading.entry.hours_max == mlb.trading.archive.hours_max == 8
    assert soccer.db_path != mlb.db_path and "v2h" in str(soccer.db_path)
    for cfg in loaded:
        assert cfg.trading.lifecycle_mode == lifecycle
        assert (cfg.trading.entry.stop_price, cfg.trading.entry.max_entry_drawdown) == (.70, .30)
        assert cfg.trading.max_positions == 20 and cfg.trading.max_new_positions_per_cycle == 5
        assert cfg.trading.experiment_entry_end_utc == "2026-09-15T12:00:00Z"
        assert cfg.trading.experiment_followup_end_utc == "2026-09-22T12:00:00Z"
    assert soccer.trading.preregistration_sha256 != mlb.trading.preregistration_sha256


def test_single_runtime_cli_uses_account_guard_not_only_account_runner(account, monkeypatch):
    from contextlib import nullcontext
    from polybot import main
    import sys
    soccer, mlb = account
    prepare_account(soccer)
    guards = []
    class FakeBot:
        def __init__(self, config, *, cycle_budget, account_guard):
            account_guard.assert_active(config)
            guards.append(account_guard)
        def run(self):
            with pytest.raises(AccountGuardError, match="already running"):
                with account_session(mlb):
                    pass
        def close(self):
            return ()
    monkeypatch.setattr(main, "_load", lambda *a, **kw: soccer)
    monkeypatch.setattr(main, "PolymarketBot", FakeBot)
    monkeypatch.setattr(main, "setup_logger", lambda *a, **kw: None)
    monkeypatch.setattr(main, "enforced_cycle_deadline", lambda: nullcontext(object()))
    monkeypatch.setattr(sys, "argv", ["polybot", "run", "--live", "--job", soccer.job_name])
    main.main()
    assert len(guards) == 1 and not guards[0].active


def test_runner_order_rotates_without_changing_profile_policy(account, monkeypatch):
    from polybot import account_runner
    soccer, mlb = account
    monkeypatch.setattr(configs, "load_config", lambda *a, **kw: soccer)
    observed = []
    def child(cmd, **kwargs):
        observed.append(cmd[cmd.index("--job")+1])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(account_runner.time, "time", lambda: 120)
    assert run_account("polybot-cat", run_process=child) == 0
    monkeypatch.setattr(account_runner.time, "time", lambda: 180)
    assert run_account("polybot-cat", run_process=child) == 0
    assert observed == [soccer.job_name, mlb.job_name, mlb.job_name, soccer.job_name]


def test_peer_guard_failure_does_not_skip_existing_position_management(monkeypatch, tmp_path):
    from .test_lifecycle_mode import _build_bot
    trade = SimpleNamespace(id=1, token_id="owned-soccer-token")
    bot, scanner, trader, repo, session, gamma = _build_bot(monkeypatch, tmp_path, "active", [trade])
    def missing_peer():
        raise AccountGuardError("peer database unavailable")
    bot.account_guard = SimpleNamespace(check_buy_budget=missing_peer, cash_failed=False)
    trader.execute_sell.return_value = False
    scanner.scan_buy_candidates.side_effect = None
    scanner.scan_buy_candidates.return_value = []
    stats = bot.run_cycle()
    trader.execute_sell.assert_called_once_with(trade)
    assert stats["checked_holdings"] == 1
    assert "account_state_unavailable" in stats["entry_guard"]["blocking_reasons"]
    assert stats["account_guard"]["error_code"] == "account_state_unavailable"
    trader.execute_buy.assert_not_called()


def test_unapproved_nfl_runtime_is_not_an_account_runner_member():
    assert configs.account_for_runtime("watermelon-live-cat-nfl-96-1m-v5") is None
    assert configs.account_for_runtime("watermelon-live-dog-nfl-99-1m-v5") is None


def test_profile_environment_can_close_only_mlb_without_stopping_soccer():
    inherited = {
        "POLYBOT_LIFECYCLE_MODE": "active",
        "POLYBOT_CLOSE_ONLY_SPORT_FAMILIES": "mlb",
    }
    soccer = configs.profile_environment(
        "watermelon-live-cat-96-1m-v2h", inherited
    )
    mlb = configs.profile_environment(
        "watermelon-live-cat-mlb-96-1m-v4", inherited
    )
    assert soccer["POLYBOT_LIFECYCLE_MODE"] == "active"
    assert mlb["POLYBOT_LIFECYCLE_MODE"] == "close_only"
