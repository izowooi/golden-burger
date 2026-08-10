"""Primary raw-signal evidence, follow-up, and drawdown atomicity contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from polybot.config import (
    ANALYZER_SCHEMA_VERSION,
    ApiConfig,
    BotConfig,
    MicroCascadeEntryConfig,
    PREREGISTERED_WINDOW_END,
    PREREGISTERED_WINDOW_START,
    PREREGISTRATION_SHA256,
    TradingConfig,
)
from polybot.api.gamma_client import GammaConditionMismatchError
from polybot.db.models import (
    ExperimentState,
    MicroCascadeExperimentContract,
    MicroCascadeFollowupObservation,
    MicroCascadeSignalDecision,
    TradeStatus,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot.strategy.trader import Trader
from polybot_observability import RunAudit


UTC = timezone.utc
WINDOW_START = PREREGISTERED_WINDOW_START
WINDOW_END = PREREGISTERED_WINDOW_END


def _config(db_path, *, job="kiwi-sim-b-3x2", trading=None):
    return BotConfig(
        trading=trading or TradingConfig(),
        api=ApiConfig(private_key="", funder_address=""),
        db_path=db_path,
        simulation_mode=True,
        job_name=job,
    )


def _decision(run_id: str, observed_at: datetime, *, suffix="1"):
    naive = observed_at.astimezone(UTC).replace(tzinfo=None)
    return {
        "run_id": run_id,
        "condition_id": f"condition-{suffix}",
        "event_id": f"event-{suffix}",
        "token_id": f"token-{suffix}",
        "arm": "B",
        "canonical_job": "kiwi-sim-b-3x2",
        "collection_eligible": 1,
        "scan_evaluated_at": naive,
        "trend_snapshot_ids_json": "[1,2,3,4]",
        "trend_snapshot_timestamps_json": "[]",
        "trend_prices_json": "[0.40,0.407,0.414,0.42]",
        "trend_gap_minutes_json": "[5,5,5]",
        "entry_snapshot_id": 4,
        "snapshot_probability": 0.42,
        "snapshot_best_bid": 0.415,
        "snapshot_best_ask": 0.425,
        "snapshot_spread": 0.01,
        "snapshot_liquidity": 30_000.0,
        "snapshot_volume_24h": 12_000.0,
        "market_end_date": naive + timedelta(hours=8),
        "event_sibling_count": 1,
        "event_rank": 1,
        "event_selected": 1,
        "global_rank": 1,
        "cooldown_allowed": 1,
        "cooldown_reason": "ok",
        "position_count": 0,
        "open_notional_usdc": 0.0,
        "drawdown_tripped": 0,
        "raw_selected": 1,
        "fresh_attempt_order": 1,
        "fresh_attempted": 1,
        "fresh_observed_at": naive,
        "fresh_best_bid": 0.415,
        "fresh_best_ask": 0.425,
        "fresh_spread": 0.01,
        "fresh_depth_shares": 100.0,
        "fresh_depth_limit_price": 0.43,
        "fresh_gate_passed": 0,
        "fresh_fail_reason": "fresh_staircase_cumulative_move_below_min",
        "execution_selected": 0,
        "trade_id": None,
    }


def _raw_market(condition_id: str, observed_at: datetime, *, bid=0.44):
    return {
        "conditionId": condition_id,
        "outcomes": ["Yes", "No"],
        "outcomePrices": [bid + 0.005, 1 - bid - 0.005],
        "clobTokenIds": ["yes-token", "no-token"],
        "negRisk": False,
        "bestBid": bid,
        "bestAsk": bid + 0.01,
        "liquidity": 50.0,  # deliberately below the main sweep's $1k floor
        "volume24hr": 0.0,
        "closed": True,  # deliberately absent from the tradable sweep
        "_gammaObservedAt": observed_at.isoformat(),
        "updatedAt": observed_at.isoformat(),
    }


def _append_source_decision(Session, config, *, observed_at, succeed=True):
    audit = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    rows = repository.append_signal_decisions(
        [_decision(audit.run_id, observed_at)]
    )
    decision_id = rows[0].id
    session.close()
    if succeed:
        audit.succeed()
    else:
        audit.fail(RuntimeError("intentional source failure"))
    return decision_id, audit.run_id


def test_contract_and_primary_tables_are_immutable_append_only(tmp_path):
    Session = init_database(str(tmp_path / "immutable.db"))
    session = Session()
    repository = TradeRepository(session)
    contract = repository.ensure_experiment_contract(
        canonical_job="kiwi-sim-b-3x2",
        arm="B",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_cadence_minutes=5,
        expected_offset_minute=1,
        preregistration_sha256=PREREGISTRATION_SHA256,
        analyzer_version=ANALYZER_SCHEMA_VERSION,
    )
    assert contract.schema_version == 2
    assert repository.ensure_experiment_contract(
        canonical_job="kiwi-sim-b-3x2",
        arm="B",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_cadence_minutes=5,
        expected_offset_minute=1,
        preregistration_sha256=PREREGISTRATION_SHA256,
        analyzer_version=ANALYZER_SCHEMA_VERSION,
    ).canonical_job == contract.canonical_job
    with pytest.raises(RuntimeError, match="contract"):
        repository.ensure_experiment_contract(
            canonical_job="kiwi-sim-a-3x1",
            arm="A",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            expected_cadence_minutes=5,
            expected_offset_minute=0,
            preregistration_sha256=PREREGISTRATION_SHA256,
            analyzer_version=ANALYZER_SCHEMA_VERSION,
        )
    with pytest.raises(DatabaseError, match="append-only"):
        session.execute(
            text(
                "UPDATE micro_cascade_experiment_contracts "
                "SET arm = 'A' WHERE canonical_job = 'kiwi-sim-b-3x2'"
            )
        )
        session.commit()
    session.rollback()
    assert (
        session.query(func.count(MicroCascadeExperimentContract.canonical_job))
        .scalar()
        == 1
    )
    session.close()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"arm": "A"}, "매핑"),
        (
            {"window_start": WINDOW_START.replace(tzinfo=None)},
            "timezone-aware",
        ),
        (
            {"window_end": WINDOW_START + timedelta(days=30, seconds=1)},
            "30일",
        ),
        ({"expected_cadence_minutes": 10}, "5분"),
        ({"expected_offset_minute": 5}, "0~4분"),
        ({"expected_offset_minute": 2}, "1분으로 고정"),
        ({"expected_offset_minute": "1"}, "정수"),
        ({"preregistration_sha256": "not-a-hash"}, "SHA-256"),
        ({"analyzer_version": 1}, "v3"),
    ],
)
def test_experiment_contract_rejects_noncanonical_values(
    tmp_path,
    override,
    message,
):
    Session = init_database(str(tmp_path / "invalid-contract.db"))
    session = Session()
    values = {
        "canonical_job": "kiwi-sim-b-3x2",
        "arm": "B",
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "expected_cadence_minutes": 5,
        "expected_offset_minute": 1,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "analyzer_version": ANALYZER_SCHEMA_VERSION,
    }
    values.update(override)
    with pytest.raises(ValueError, match=message):
        TradeRepository(session).ensure_experiment_contract(**values)
    assert session.query(MicroCascadeExperimentContract).count() == 0
    session.close()


def test_signal_decision_unique_and_failed_source_remains_but_is_not_due(tmp_path):
    db_path = tmp_path / "source-status.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    source = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    payload = _decision(source.run_id, WINDOW_START)
    repository.append_signal_decisions([payload])
    with pytest.raises(IntegrityError):
        repository.append_signal_decisions([payload])
    source.fail(RuntimeError("fail after append"))

    observer = RunAudit.start(config, strategy_name="golden-kiwi")
    assert TradeRepository(session).append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=61),
        fetch_market=lambda condition: _raw_market(
            condition, WINDOW_START + timedelta(minutes=61)
        ),
    ) == 0
    observer.succeed()
    assert session.query(MicroCascadeSignalDecision).count() == 1
    assert session.query(MicroCascadeFollowupObservation).count() == 0
    session.close()


@pytest.mark.parametrize("minutes", [59, 76])
def test_followup_is_not_appended_outside_predeclared_window(
    tmp_path,
    minutes,
):
    db_path = tmp_path / f"outside-window-{minutes}.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    _append_source_decision(Session, config, observed_at=WINDOW_START)
    observer = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    assert TradeRepository(session).append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=minutes),
        fetch_market=lambda condition: _raw_market(
            condition,
            WINDOW_START + timedelta(minutes=minutes),
        ),
    ) == 0
    observer.succeed()
    assert session.query(MicroCascadeFollowupObservation).count() == 0
    session.close()


def test_followup_retries_absence_and_uses_independent_closed_market_lookup(
    tmp_path,
):
    db_path = tmp_path / "followup.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    decision_id, _ = _append_source_decision(
        Session,
        config,
        observed_at=WINDOW_START,
    )

    missing = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    assert repository.append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=61),
        fetch_market=lambda _condition: None,
    ) == 1
    session.close()
    missing.succeed()

    failed_quote = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    assert repository.append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=63),
        fetch_market=lambda condition: _raw_market(
            condition,
            WINDOW_START + timedelta(minutes=63),
            bid=0.43,
        ),
    ) == 1
    session.close()
    failed_quote.fail(RuntimeError("quote run failed after append"))

    valid = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    assert repository.append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=65),
        fetch_market=lambda condition: _raw_market(
            condition,
            WINDOW_START + timedelta(minutes=65),
            bid=0.44,
        ),
    ) == 1
    session.close()
    valid.succeed()

    later = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    assert repository.append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=70),
        fetch_market=lambda condition: _raw_market(
            condition,
            WINDOW_START + timedelta(minutes=70),
            bid=0.45,
        ),
    ) == 0
    later.succeed()

    rows = (
        session.query(MicroCascadeFollowupObservation)
        .filter(
            MicroCascadeFollowupObservation.decision_id == decision_id
        )
        .order_by(MicroCascadeFollowupObservation.observed_at)
        .all()
    )
    assert len(rows) == 3
    assert rows[0].valid_quote == 0
    assert rows[0].source_reason == (
        "market_missing_from_gamma_condition_lookup"
    )
    assert rows[1].valid_quote == 1  # retained but observing run is FAILED
    assert rows[2].valid_quote == 1
    assert rows[2].market_seen == 1
    assert rows[2].liquidity == 50.0
    assert rows[2].best_bid == pytest.approx(0.44)
    session.close()


@pytest.mark.parametrize(
    ("fetch_market", "expected_reason", "market_seen"),
    [
        (
            lambda _condition: {
                **_raw_market("different-condition", WINDOW_START),
                "conditionId": "different-condition",
            },
            "gamma_condition_id_mismatch",
            1,
        ),
        (
            lambda _condition: (_ for _ in ()).throw(
                GammaConditionMismatchError("wrong condition")
            ),
            "gamma_condition_lookup_error:GammaConditionMismatchError",
            0,
        ),
        (
            lambda _condition: (_ for _ in ()).throw(
                __import__("requests").exceptions.Timeout("timed out")
            ),
            "gamma_condition_lookup_error:Timeout",
            0,
        ),
    ],
)
def test_followup_censors_condition_mismatch_and_lookup_errors(
    tmp_path,
    fetch_market,
    expected_reason,
    market_seen,
):
    db_path = tmp_path / f"censored-{expected_reason.split(':')[-1]}.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    _append_source_decision(Session, config, observed_at=WINDOW_START)
    observer = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()

    assert TradeRepository(session).append_due_followup_observations(
        [],
        observed_at=WINDOW_START + timedelta(minutes=61),
        fetch_market=fetch_market,
    ) == 1
    row = session.query(MicroCascadeFollowupObservation).one()
    assert row.valid_quote == 0
    assert row.source_available == 0
    assert row.market_seen == market_seen
    assert row.source_reason == expected_reason
    assert row.best_bid is None
    session.close()
    observer.succeed()


def _terminal_run(
    Session,
    config,
    *,
    pnl,
    at,
    status=TradeStatus.COMPLETED,
    succeed=True,
    suffix="1",
):
    audit = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    TradeRepository(session).create_trade(
        condition_id=f"condition-{suffix}",
        event_id=f"event-{suffix}",
        token_id=f"token-{suffix}",
        outcome="Yes",
        status=status,
        entry_run_id=audit.run_id,
        exit_run_id=audit.run_id if status == TradeStatus.COMPLETED else None,
        sell_timestamp=at if status == TradeStatus.COMPLETED else None,
        hypothetical_pnl=pnl,
    )
    session.close()
    if succeed:
        audit.succeed()
    else:
        audit.fail(RuntimeError("intentional terminal failure"))
    return audit.run_id


def test_drawdown_first_crossing_is_candidate_independent_and_two_phase(
    tmp_path,
):
    db_path = tmp_path / "drawdown-two-phase.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    _terminal_run(
        Session,
        config,
        pnl=-10.0,
        at=WINDOW_START,
        suffix="1",
    )
    crossing_run_id = _terminal_run(
        Session,
        config,
        pnl=-10.0,
        at=WINDOW_START + timedelta(minutes=5),
        suffix="2",
    )
    _terminal_run(
        Session,
        config,
        pnl=50.0,
        at=WINDOW_START + timedelta(minutes=10),
        suffix="3",
    )

    detector = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    trader = Trader(
        repository,
        SimpleNamespace(simulation_mode=True),
        config.trading,
        simulation_mode=True,
    )
    # No candidate or market scan is involved: the historical first crossing
    # is detected at cycle start even though later P&L recovered.
    assert trader.evaluate_drawdown_stop() is True
    pending_key = f"drawdown_kill_switch_pending:{detector.run_id}"
    assert session.get(ExperimentState, pending_key) is not None
    assert repository.get_drawdown_kill_switch() is None
    detector.fail(RuntimeError("cycle failed after detection"))
    assert repository.discard_staged_drawdown_kill_switch(detector.run_id)
    assert repository.get_drawdown_kill_switch() is None
    session.close()

    restarted = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    trader = Trader(
        repository,
        SimpleNamespace(simulation_mode=True),
        config.trading,
        simulation_mode=True,
    )
    assert trader.evaluate_drawdown_stop() is True
    session.close()
    restarted.succeed()

    session = Session()
    repository = TradeRepository(session)
    state = repository.finalize_staged_drawdown_kill_switch(
        restarted.run_id
    )
    assert state is not None
    assert state["economic_pnl"] == pytest.approx(-20.0)
    assert state["tripped_run_id"] == crossing_run_id
    session.close()

    session = Session()
    recovered = TradeRepository(session).get_drawdown_kill_switch()
    assert recovered == state
    session.close()


def test_strict_drawdown_ignores_wrong_job_cohort_failed_and_nonterminal(
    tmp_path,
):
    db_path = tmp_path / "strict-path.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    _terminal_run(
        Session,
        config,
        pnl=-5.0,
        at=WINDOW_START,
        suffix="valid",
    )
    _terminal_run(
        Session,
        _config(db_path, job="another-job"),
        pnl=-100.0,
        at=WINDOW_START + timedelta(minutes=1),
        suffix="wrong-job",
    )
    other_arm = TradingConfig(
        entry=replace(
            MicroCascadeEntryConfig(),
            confirmation_steps=5,
            min_cumulative_move=0.02,
        )
    )
    _terminal_run(
        Session,
        _config(db_path, trading=other_arm),
        pnl=-100.0,
        at=WINDOW_START + timedelta(minutes=2),
        suffix="wrong-cohort",
    )
    _terminal_run(
        Session,
        config,
        pnl=-100.0,
        at=WINDOW_START + timedelta(minutes=3),
        succeed=False,
        suffix="failed",
    )
    _terminal_run(
        Session,
        config,
        pnl=-100.0,
        at=WINDOW_START + timedelta(minutes=4),
        status=TradeStatus.HOLDING,
        suffix="holding",
    )

    detector = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    result = TradeRepository(session).strict_terminal_economic_path(
        current_run_id_value=detector.run_id,
        loss_limit_usdc=20.0,
    )
    assert result.economic_pnl == pytest.approx(-5.0)
    assert result.tripped is False
    assert result.terminal_trade_count == 1
    session.close()
    detector.fail(RuntimeError("test cleanup"))


@pytest.mark.parametrize("bad_pnl", [float("nan"), float("inf"), None])
def test_strict_drawdown_fails_closed_on_nonfinite_terminal_pnl(
    tmp_path,
    bad_pnl,
):
    db_path = tmp_path / f"bad-pnl-{bad_pnl}.db"
    Session = init_database(str(db_path))
    config = _config(db_path)
    _terminal_run(
        Session,
        config,
        pnl=bad_pnl,
        at=WINDOW_START,
        suffix="bad",
    )
    detector = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    with pytest.raises(RuntimeError, match="P&L"):
        TradeRepository(session).strict_terminal_economic_path(
            current_run_id_value=detector.run_id,
            loss_limit_usdc=20.0,
        )
    session.close()
    detector.fail(RuntimeError("test cleanup"))
