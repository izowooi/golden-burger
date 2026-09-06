"""Plan-only persistence and fake POST bridge; never real network or wallet I/O."""
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from polybot.plans import PlanStore, PlanStoreError

LION, WOLF = "guava-live-lion-a-v1", "guava-live-wolf-b-v1"
FINGERPRINT = "c" * 64  # Opaque test namespace, never derived from a private key.
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def case(tmp_path, monkeypatch):
    from polybot.config import load_config
    from polybot_observability import ExecutionLedger, RunAudit
    monkeypatch.setenv("GIT_COMMIT", "a" * 40)  # RunAudit must not invoke git.
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    for target, name in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
        monkeypatch.setattr(target, name, forbidden)
    config = replace(load_config(ROOT / "config.yaml", job_name=LION, environment={}), root=tmp_path)
    audit = RunAudit.start(config, strategy_name="golden-guava")
    ledger = ExecutionLedger(config.db_path, strategy_name="golden-guava")
    store = PlanStore(config.db_path, job_name=LION, account_fingerprint=FINGERPRINT)
    try:
        yield store, config, audit, ledger
    finally:
        store.close()
        audit.succeed()


def plan(store, audit, **changes):
    args = dict(run_id=audit.run_id, decision_id="buy-1", side="BUY", parent_buy_decision_id=None,
                event_id="event-1", condition_id="condition-1", token_id="token-1",
                requested_quantity="5", requested_limit_price="0.5", sport_family="soccer",
                league_code="EPL", hypothesis_id="UNSELECTED_TEST_ONLY",
                evidence_ref="receipt:request-1/book-1", evidence_sha256="b" * 64,
                observed_at="2026-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z")
    args.update(changes)
    return store.record_plan(**args)


def bind(case):
    store, config, audit, _ = case
    return store.bind_run(audit.run_id, config.public_snapshot())


def test_missing_database_never_created(tmp_path):
    missing = tmp_path / "missing" / "trades.db"
    with pytest.raises(PlanStoreError):
        PlanStore(missing, job_name=LION, account_fingerprint=FINGERPRINT)
    assert not missing.parent.exists()


def test_bind_stores_distinct_guava_shared_and_snapshot_hashes(case):
    store, config, audit, _ = case
    bound = bind(case)
    assert bound["guava_config_hash"] == config.config_hash
    assert bound["shared_config_hash"] == audit.config_hash != config.config_hash
    assert bound["strategy_source_digest"] == config.strategy_source_digest
    assert bound == bind(case)
    with sqlite3.connect(config.db_path) as con:
        shared = json.loads(con.execute("SELECT config_json FROM strategy_configs").fetchone()[0])
        assert shared["trading"]["max_tokens_per_cycle"] == "<redacted>"
        assert json.loads(bound["public_snapshot_json"])["trading"]["max_tokens_per_cycle"] == config.trading.max_tokens_per_cycle


def test_plan_is_idempotent_and_does_not_touch_order_rows(case):
    store, config, audit, _ = case
    bind(case)
    first = plan(store, audit)
    assert plan(store, audit, requested_quantity=Decimal("5.00")) == first
    assert store.get_plan("buy-1", "BUY") == first
    with pytest.raises(PlanStoreError, match="conflict"):
        plan(store, audit, requested_limit_price="0.6")
    with sqlite3.connect(config.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM order_submissions").fetchone()[0] == 0
    view = store.bindings()
    assert view["expected_submission_ids"] == frozenset()
    assert view["unbound_plans"] == (("buy-1", "BUY"),)


def test_parent_required_and_all_market_ids_exact(case):
    store, _, audit, _ = case
    bind(case)
    with pytest.raises(PlanStoreError):
        plan(store, audit, decision_id="sell-1", side="SELL", parent_buy_decision_id="buy-1")
    plan(store, audit)
    for key in ("event_id", "condition_id", "token_id"):
        with pytest.raises(PlanStoreError):
            plan(store, audit, decision_id="sell-1", side="SELL", parent_buy_decision_id="buy-1", **{key: "different"})
    child = plan(store, audit, decision_id="sell-1", side="SELL", parent_buy_decision_id="buy-1", requested_quantity="10.005001")
    assert child["parent_buy_decision_id"] == "buy-1"


@pytest.mark.parametrize("change", [{"requested_quantity": "NaN"}, {"requested_quantity": True},
    {"requested_quantity": "0"}, {"requested_quantity": "4.99"}, {"requested_quantity": "100.01"}, {"requested_quantity": "5.001"},
    {"requested_limit_price": "Infinity"}, {"requested_limit_price": "1"}, {"requested_limit_price": "0"},
    {"evidence_sha256": "wrong"}, {"sport_family": "esports"}, {"parent_buy_decision_id": "manual"}])
def test_plan_domains_fail_before_any_insert(case, change):
    store, _, audit, _ = case
    bind(case)
    with pytest.raises(PlanStoreError):
        plan(store, audit, **change)
    assert store.bindings()["unbound_plans"] == ()


def test_sell_micro_precision(case):
    store, _, audit, _ = case
    bind(case); plan(store, audit)
    with pytest.raises(PlanStoreError):
        plan(store, audit, decision_id="s1", side="SELL", parent_buy_decision_id="buy-1", requested_quantity="1.0000001")


def test_new_plan_cannot_claim_future_observation_or_start_expired(case, monkeypatch):
    from polybot import plans
    store, _, audit, _ = case
    bind(case)
    monkeypatch.setattr(plans, "_now", lambda: "2026-09-06T08:00:00.000000Z")
    for observed, until in (("2026-09-06T08:00:01Z", "2026-09-06T08:01:00Z"),
                            ("2026-09-06T07:59:00Z", "2026-09-06T08:00:00Z")):
        with pytest.raises(PlanStoreError, match="new plan"):
            plan(store, audit, observed_at=observed, valid_until=until)
    assert store.bindings()["unbound_plans"] == ()


def test_run_must_be_bound_running_and_matching_identity(case):
    store, config, audit, _ = case
    with pytest.raises(PlanStoreError):
        plan(store, audit)
    wrong = config.public_snapshot(); wrong["job_name"] = WOLF
    with pytest.raises(PlanStoreError):
        store.bind_run(audit.run_id, wrong)
    bind(case); plan(store, audit)
    audit.succeed()
    with pytest.raises(PlanStoreError):
        store.bind_run(audit.run_id, config.public_snapshot())
    with pytest.raises(PlanStoreError):
        plan(store, audit, decision_id="after-finish")
    assert store.get_plan("buy-1", "BUY")["run_id"] == audit.run_id


def public_hash(snapshot):
    spec = dict(job_name=snapshot["job_name"], jenkins_job=snapshot["jenkins_job"], mode="live", shard=None, arm=snapshot["arm"])
    payload = dict(strategy_name="golden-guava", spec=spec, trading=snapshot["trading"], strategy_source_digest=snapshot["strategy_source_digest"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def test_config_tampering_and_redaction_rebind_conflict(case):
    store, config, audit, _ = case
    wrong = config.public_snapshot(); wrong["trading"]["max_positions"] += 1
    with pytest.raises(PlanStoreError):
        store.bind_run(audit.run_id, wrong)
    wrong["config_hash"] = public_hash(wrong)
    with pytest.raises(PlanStoreError, match="resolved"):
        store.bind_run(audit.run_id, wrong)
    bind(case)
    masked = config.public_snapshot(); masked["trading"]["max_tokens_per_cycle"] += 1
    masked["config_hash"] = public_hash(masked)
    with pytest.raises(PlanStoreError, match="conflict"):
        store.bind_run(audit.run_id, masked)


def test_credentials_rejected_without_echo_or_storage(case):
    store, config, audit, _ = case
    bad = config.public_snapshot(); bad["trading"]["private_key"] = "DO_NOT_PRINT_THIS"
    with pytest.raises(PlanStoreError) as error:
        store.bind_run(audit.run_id, bad)
    assert "DO_NOT_PRINT_THIS" not in str(error.value)
    bind(case)
    with pytest.raises(PlanStoreError):
        plan(store, audit, evidence_ref="postgres://user:DO_NOT_PRINT_THIS@host/db")
    with sqlite3.connect(config.db_path) as con:
        assert "DO_NOT_PRINT_THIS" not in " ".join(con.iterdump())


@pytest.mark.parametrize("table", ["guava_plan_identity", "guava_plan_runs", "guava_position_plans"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "REPLACE"])
def test_all_plan_tables_are_immutable_even_replace_without_recursive_triggers(case, table, operation):
    store, config, audit, _ = case
    bind(case); plan(store, audit)
    with sqlite3.connect(config.db_path) as con:
        con.execute("PRAGMA recursive_triggers=OFF")
        column = con.execute(f"PRAGMA table_info({table})").fetchone()[1]
        query = (f"UPDATE {table} SET {column}={column}" if operation == "UPDATE" else
                 f"DELETE FROM {table}" if operation == "DELETE" else f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute(query)


def test_runtime_pin_cannot_change(case):
    _, config, _, _ = case
    with pytest.raises(PlanStoreError):
        PlanStore(config.db_path, job_name=WOLF, account_fingerprint=FINGERPRINT)
    with pytest.raises(PlanStoreError):
        PlanStore(config.db_path, job_name="polybot-lion", account_fingerprint=FINGERPRINT)
    with pytest.raises(PlanStoreError):
        PlanStore(config.db_path, job_name=LION, account_fingerprint="d" * 64)


@pytest.mark.parametrize("kind", ["research", "foreign"])
def test_reject_non_live_or_foreign_db_without_plan_schema(tmp_path, monkeypatch, kind):
    from polybot_observability import RunAudit, ExecutionLedger
    from types import SimpleNamespace
    monkeypatch.setenv("GIT_COMMIT", "a" * 40)
    path = tmp_path / "bad.db"
    strategy = "other" if kind == "foreign" else "golden-guava"
    audit = RunAudit.start(SimpleNamespace(db_path=path, job_name=LION, simulation_mode=kind == "research", trading={}), strategy_name=strategy)
    try:
        ExecutionLedger(path, strategy_name=strategy)
        with pytest.raises(PlanStoreError):
            PlanStore(path, job_name=LION, account_fingerprint=FINGERPRINT)
        with sqlite3.connect(path) as con:
            assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='guava_plan_identity'").fetchone()
    finally:
        audit.succeed()


def test_orphan_is_not_filtered_from_binding_inventory(case):
    store, _, audit, ledger = case
    bind(case); plan(store, audit)
    ledger.record_intent(token_id="token-1", side="BUY", requested_price=.5, requested_size=10, simulation=False)
    with pytest.raises(PlanStoreError, match="orphan"):
        store.bindings()


def test_fake_broker_post_restart_bridge_and_explicit_parent(case):
    from polybot.execution import Broker
    from polybot.budget import Budget
    from test_execution import FakeSDK, confirmed
    store, config, audit, ledger = case
    bind(case); before = plan(store, audit)
    sdk = FakeSDK(); broker = Broker(ledger, config.db_path, Budget(), sdk_client=sdk)
    try:
        def committed_before_post(_):
            view = store.bindings()
            assert ("buy-1", "BUY") in view["submission_by_plan"]
            assert store.get_plan("buy-1", "BUY")["created_at"] == before["created_at"]
        sdk.callback = committed_before_post
        buy = broker.buy_fok("token-1", 5, .5, dict(event_id="event-1", condition_id="condition-1", decision_id="buy-1"))
        assert buy["order_id"]
        confirmed(sdk, buy["order_id"]); broker.reconcile([buy["submission_id"]])
        plan(store, audit, decision_id="sell-1", side="SELL", parent_buy_decision_id="buy-1", requested_quantity="10.005")
        sdk.callback = lambda _: (_ for _ in ()).throw(TimeoutError("ambiguous fake POST"))
        sell = broker.sell_fok("token-1", "10.005", .5, dict(event_id="event-1", condition_id="condition-1", decision_id="sell-1"))
        assert sell["submission_outcome_unknown"]
        store.close()
        reopened = PlanStore(config.db_path, job_name=LION, account_fingerprint=FINGERPRINT)
        try:
            view = reopened.bindings()
            assert view["expected_submission_ids"] == frozenset([buy["submission_id"], sell["submission_id"]])
            assert view["sell_to_buy"] == {sell["submission_id"]: buy["submission_id"]}
            assert view["unbound_plans"] == ()
            from polybot.position_state import Limits, reduce_positions
            limits = Limits(20, Decimal(100), 2, Decimal(10), 2, Decimal(10), Decimal(5))
            state = reduce_positions(broker.execution_inventory(), sell_to_buy=view["sell_to_buy"],
                                     expected_submission_ids=view["expected_submission_ids"], inventory_complete=True, limits=limits)
            assert state.active_position_count == 1 and state.notional_usdc == 5
            assert state.lots[0].sell_reserved_shares == 10
        finally:
            reopened.close()
        assert len(sdk.posts) == 2
    finally:
        broker.close()


def test_expired_future_and_non_utc_plans_cannot_execute_but_remain_readable(case):
    store, _, audit, _ = case
    bind(case)
    now = datetime.now(timezone.utc)
    original = plan(store, audit, observed_at=(now - timedelta(seconds=10)).isoformat(), valid_until=(now + timedelta(seconds=10)).isoformat())
    assert store.load_for_execution("buy-1", "BUY", run_id=audit.run_id, now=now)["run_id"] == audit.run_id
    for at in (now - timedelta(seconds=11), now + timedelta(seconds=10)):
        with pytest.raises(PlanStoreError, match="validity"):
            store.load_for_execution("buy-1", "BUY", run_id=audit.run_id, now=at)
    assert store.get_plan("buy-1", "BUY") == original
    assert store.bindings()["unbound_plans"] == (("buy-1", "BUY"),)
    for observed, until in (("2026-01-01", "2026-01-02"), ("2026-01-01T00:00:00+09:00", "2026-01-02T00:00:00Z"),
                            ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")):
        with pytest.raises(PlanStoreError):
            plan(store, audit, decision_id="invalid-time", observed_at=observed, valid_until=until)


def test_within_ttl_retry_uses_registered_compatible_run_without_rewriting_origin(case):
    from polybot_observability import RunAudit
    from polybot.execution import Broker
    from polybot.budget import Budget
    from test_execution import FakeSDK
    store, config, origin, ledger = case
    bind(case); original = plan(store, origin)
    origin.succeed()
    attempt = RunAudit.start(config, strategy_name="golden-guava")
    broker = Broker(ledger, config.db_path, Budget(), sdk_client=FakeSDK())
    try:
        with pytest.raises(PlanStoreError):
            store.load_for_execution("buy-1", "BUY", run_id=attempt.run_id, now=datetime.now(timezone.utc))
        store.bind_run(attempt.run_id, config.public_snapshot())
        retry = store.load_for_execution("buy-1", "BUY", run_id=attempt.run_id, now=datetime.now(timezone.utc))
        assert retry["run_id"] == origin.run_id and retry["attempt_run_id"] == attempt.run_id
        assert retry["order_authorized"] is False
        result = broker.buy_fok("token-1", 5, .5, dict(event_id="event-1", condition_id="condition-1", decision_id="buy-1"))
        assert store.bindings()["submission_by_plan"][("buy-1", "BUY")] == result["submission_id"]
        assert store.get_plan("buy-1", "BUY") == original
        with pytest.raises(PlanStoreError, match="envelope"):
            store.load_for_execution("buy-1", "BUY", run_id=attempt.run_id, now=datetime.now(timezone.utc))
    finally:
        broker.close(); attempt.succeed()


@pytest.mark.parametrize("changed", ["requested_quantity", "requested_limit_price"])
def test_binding_rejects_actual_envelope_different_from_preexisting_plan(case, changed):
    from polybot.execution import Broker, BrokerSettings
    from polybot.budget import Budget
    from test_execution import FakeSDK
    store, config, audit, ledger = case
    bind(case); plan(store, audit)
    broker = Broker(ledger, config.db_path, Budget(), sdk_client=FakeSDK(), settings=BrokerSettings(max_buy_notional=Decimal(10)))
    try:
        # Deliberately bypass the future planner guard, but never alter an envelope.
        quantity, limit = (10, .5) if changed == "requested_quantity" else (5, .6)
        broker.buy_fok("token-1", quantity, limit, dict(event_id="event-1", condition_id="condition-1", decision_id="buy-1"))
        with pytest.raises(PlanStoreError, match="requested amount"):
            store.bindings()
    finally:
        broker.close()


def test_existing_unplanned_envelope_cannot_be_backfilled(case):
    from polybot.execution import Broker
    from polybot.budget import Budget
    from test_execution import FakeSDK
    store, config, audit, ledger = case
    bind(case)
    broker = Broker(ledger, config.db_path, Budget(), sdk_client=FakeSDK())
    try:
        broker.buy_fok("token-1", 5, .5, dict(event_id="event-1", condition_id="condition-1", decision_id="buy-1"))
        with pytest.raises(PlanStoreError, match="backfill"):
            plan(store, audit)
        with pytest.raises(PlanStoreError, match="pre-POST plan"):
            store.bindings()
    finally:
        broker.close()


def test_incompatible_registered_retry_source_is_rejected(case):
    from polybot_observability import RunAudit
    store, config, origin, _ = case
    bind(case); plan(store, origin); origin.succeed()
    attempt = RunAudit.start(config, strategy_name="golden-guava")
    try:
        snapshot = config.public_snapshot(); snapshot["strategy_source_digest"] = "e" * 64
        snapshot["config_hash"] = public_hash(snapshot)
        store.bind_run(attempt.run_id, snapshot)
        with pytest.raises(PlanStoreError, match="incompatible attempt"):
            store.load_for_execution("buy-1", "BUY", run_id=attempt.run_id, now=datetime.now(timezone.utc))
    finally:
        attempt.succeed()


def test_plan_expiry_does_not_erase_existing_economic_bindings(case):
    from polybot.execution import Broker
    from polybot.budget import Budget
    from test_execution import FakeSDK
    store, config, audit, ledger = case
    bind(case)
    # A plan valid at creation remains attributable after its TTL. Expiry cannot
    # erase a real order's economic evidence.
    now = datetime.now(timezone.utc)
    plan(store, audit, observed_at=(now-timedelta(seconds=1)).isoformat(),
         valid_until=(now+timedelta(seconds=60)).isoformat())
    broker = Broker(ledger, config.db_path, Budget(), sdk_client=FakeSDK())
    try:
        result = broker.buy_fok("token-1", 5, .5, dict(event_id="event-1", condition_id="condition-1", decision_id="buy-1"))
        with pytest.raises(PlanStoreError, match="validity"):
            store.load_for_execution("buy-1", "BUY", run_id=audit.run_id, now=now+timedelta(seconds=61))
        assert store.bindings()["expected_submission_ids"] == frozenset([result["submission_id"]])
    finally:
        broker.close()


def test_fingerprint_not_accepted_inside_public_snapshot(case):
    store, config, audit, _ = case
    p = config.public_snapshot(); p["account_fingerprint"] = FINGERPRINT
    with pytest.raises(PlanStoreError, match="account identity is separate"):
        store.bind_run(audit.run_id, p)


def test_import_is_inert_and_cannot_load_broker_or_live_sdk():
    import os
    import subprocess
    import sys
    code = """
import builtins
original = builtins.__import__
def guard(name,*args,**kwargs):
    if name.startswith(('polybot.execution','py_clob','eth_account','requests','httpx')):
        raise AssertionError('execution dependency during PlanStore import')
    return original(name,*args,**kwargs)
builtins.__import__=guard
import sqlite3
sqlite3.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('DB opened at import'))
import polybot.plans
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_wolf_identity_positive_and_fingerprint_required(tmp_path, monkeypatch):
    from polybot.config import load_config
    from polybot_observability import RunAudit, ExecutionLedger
    monkeypatch.setenv("GIT_COMMIT", "a" * 40)
    cfg = replace(load_config(ROOT / "config.yaml", job_name=WOLF, environment={}), root=tmp_path)
    audit = RunAudit.start(cfg, strategy_name="golden-guava")
    try:
        ExecutionLedger(cfg.db_path, strategy_name="golden-guava")
        for fingerprint in ("", "not-a-hash", True):
            with pytest.raises(PlanStoreError):
                PlanStore(cfg.db_path, job_name=WOLF, account_fingerprint=fingerprint)
        store = PlanStore(cfg.db_path, job_name=WOLF, account_fingerprint=FINGERPRINT)
        try:
            assert store.bind_run(audit.run_id, cfg.public_snapshot())["guava_config_hash"] == cfg.config_hash
        finally:
            store.close()
    finally:
        audit.succeed()
