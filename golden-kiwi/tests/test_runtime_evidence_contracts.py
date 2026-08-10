"""Run/cohort isolation, cooldown, and single-writer evidence contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from polybot.bot import exclusive_job_run_lock
from polybot.config import (
    ApiConfig,
    BotConfig,
    MicroCascadeEntryConfig,
    TradingConfig,
)
from polybot.db.models import (
    ExperimentState,
    MarketSnapshot,
    MarketSweep,
    TradeStatus,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot_observability import RunAudit


def bot_config(db_path, *, trading=None):
    return BotConfig(
        trading=trading or TradingConfig(),
        api=ApiConfig(private_key="", funder_address=""),
        db_path=db_path,
        simulation_mode=True,
        job_name="kiwi-sim-b-3x2",
    )


def add_snapshot_and_sweep(
    Session,
    *,
    run_id,
    sweep_id,
    timestamp,
    probability,
):
    session = Session()
    session.add(
        MarketSnapshot(
            condition_id="condition-1",
            probability=probability,
            liquidity=30_000,
            volume_24h=12_000,
            best_bid=probability - 0.005,
            best_ask=probability + 0.005,
            spread=0.01,
            run_id=run_id,
            timestamp=timestamp,
        )
    )
    session.add(
        MarketSweep(
            sweep_id=sweep_id,
            schema_version=2,
            run_id=run_id,
            started_at=timestamp - timedelta(seconds=1),
            completed_at=timestamp,
            cursor_complete=1,
            pages=1,
            raw_market_count=1,
            unique_condition_count=1,
            qualified_market_count=1,
            excluded_condition_count=0,
            exclusion_counts_json="{}",
            missing_condition_id_count=0,
            duplicate_raw_count=0,
            min_liquidity=20_000,
            min_volume=10_000,
            max_pages=53,
            max_markets=5_330,
            max_elapsed_seconds=120,
            elapsed_seconds=1,
            membership_digest_sha256="0" * 64,
            snapshot_eligible_count=1,
            snapshotted_market_count=1,
            membership_detail_stored=0,
        )
    )
    session.commit()
    session.close()


def test_lineage_uses_only_successful_cursor_complete_same_cohort_rows(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "lineage.db"
    Session = init_database(str(db_path))
    primary = bot_config(db_path)
    base = datetime(2026, 7, 30, 12, 0)

    success = RunAudit.start(primary, strategy_name="golden-kiwi")
    add_snapshot_and_sweep(
        Session,
        run_id=success.run_id,
        sweep_id="success",
        timestamp=base,
        probability=0.40,
    )
    success.succeed()

    # A monorepo commit change is provenance only and must not split lineage.
    monkeypatch.setenv("GIT_COMMIT", "b" * 40)
    later_commit = RunAudit.start(primary, strategy_name="golden-kiwi")
    add_snapshot_and_sweep(
        Session,
        run_id=later_commit.run_id,
        sweep_id="later-commit",
        timestamp=base + timedelta(minutes=3),
        probability=0.403,
    )
    later_commit.succeed()

    failed = RunAudit.start(primary, strategy_name="golden-kiwi")
    add_snapshot_and_sweep(
        Session,
        run_id=failed.run_id,
        sweep_id="failed",
        timestamp=base + timedelta(minutes=5),
        probability=0.405,
    )
    failed.fail(RuntimeError("expected test failure"))

    other_arm = TradingConfig(
        entry=replace(
            MicroCascadeEntryConfig(),
            confirmation_steps=5,
            min_cumulative_move=0.02,
        )
    )
    cross_cohort = RunAudit.start(
        bot_config(db_path, trading=other_arm),
        strategy_name="golden-kiwi",
    )
    add_snapshot_and_sweep(
        Session,
        run_id=cross_cohort.run_id,
        sweep_id="cross-cohort",
        timestamp=base + timedelta(minutes=10),
        probability=0.41,
    )
    cross_cohort.succeed()

    foreign_strategy = RunAudit.start(
        primary,
        strategy_name="foreign-strategy",
    )
    add_snapshot_and_sweep(
        Session,
        run_id=foreign_strategy.run_id,
        sweep_id="foreign-strategy",
        timestamp=base + timedelta(minutes=12),
        probability=0.415,
    )
    foreign_strategy.succeed()

    current = RunAudit.start(primary, strategy_name="golden-kiwi")
    add_snapshot_and_sweep(
        Session,
        run_id=current.run_id,
        sweep_id="current",
        timestamp=base + timedelta(minutes=15),
        probability=0.42,
    )
    session = Session()
    repository = TradeRepository(session)
    rows = repository.get_entry_lineage_snapshots(
        "condition-1",
        base - timedelta(minutes=1),
        current.run_id,
    )
    assert [row.run_id for row in rows] == [
        success.run_id,
        later_commit.run_id,
        current.run_id,
    ]
    session.close()
    current.fail(RuntimeError("test cleanup"))


def test_event_cooldown_starts_at_signal_not_close(tmp_path):
    Session = init_database(str(tmp_path / "cooldown.db"))
    session = Session()
    repository = TradeRepository(session)
    now = datetime(2026, 7, 30, 12, 0)
    repository.create_trade(
        condition_id="condition-1",
        event_id="event-1",
        token_id="token-1",
        outcome="Yes",
        status=TradeStatus.COMPLETED,
        signal_timestamp_at_entry=now - timedelta(hours=5),
        buy_timestamp=now - timedelta(hours=5),
        sell_timestamp=now - timedelta(hours=4),
    )

    assert repository.can_reenter("condition-1", 6, now) == (
        False,
        "signal_cooldown",
    )
    assert repository.can_enter_event("event-1", 6, now) == (
        False,
        "event_signal_cooldown",
    )
    after_signal_window = now + timedelta(hours=1, seconds=1)
    assert repository.can_reenter("condition-1", 6, after_signal_window) == (
        True,
        "ok",
    )
    assert repository.can_enter_event("event-1", 6, after_signal_window) == (
        True,
        "ok",
    )
    session.close()


def test_failed_entry_run_is_quarantined_and_removed_from_economics(tmp_path):
    db_path = tmp_path / "failed-run.db"
    Session = init_database(str(db_path))
    config = bot_config(db_path)
    audit = RunAudit.start(config, strategy_name="golden-kiwi")
    session = Session()
    repository = TradeRepository(session)
    trade = repository.create_trade(
        condition_id="condition-1",
        event_id="event-1",
        token_id="token-1",
        outcome="Yes",
        status=TradeStatus.COMPLETED,
        entry_run_id=audit.run_id,
        hypothetical_pnl=5.0,
    )
    trade_id = trade.id
    session.close()
    audit.fail(RuntimeError("cycle failed after entry"))

    session = Session()
    repository = TradeRepository(session)
    assert repository.invalidate_non_successful_run_evidence() == 1
    refreshed = repository.get_by_id(trade_id)
    assert refreshed.status == TradeStatus.QUARANTINED
    assert refreshed.hypothetical_pnl is None
    assert refreshed.promotion_eligible == 0
    assert repository.get_stats()["research_economic_pnl"] == 0
    session.close()


def test_drawdown_kill_switch_is_first_trip_permanent_across_sessions(tmp_path):
    db_path = tmp_path / "drawdown-latch.db"
    Session = init_database(str(db_path))
    tripped_at = datetime(2026, 7, 30, 12, 0)
    source = RunAudit.start(
        bot_config(db_path),
        strategy_name="golden-kiwi",
    )
    source.succeed()

    session = Session()
    repository = TradeRepository(session)
    first = repository.latch_drawdown_kill_switch(
        economic_pnl=-20.0,
        loss_limit_usdc=20.0,
        experiment_capital_usdc=100.0,
        max_drawdown_stop=0.20,
        run_id=source.run_id,
        tripped_at=tripped_at,
    )
    session.close()

    session = Session()
    repository = TradeRepository(session)
    recovered_attempt = repository.latch_drawdown_kill_switch(
        economic_pnl=-25.0,
        loss_limit_usdc=20.0,
        experiment_capital_usdc=100.0,
        max_drawdown_stop=0.20,
        run_id="later-run",
        tripped_at=tripped_at + timedelta(days=1),
    )
    assert recovered_attempt == first
    assert recovered_attempt["tripped_run_id"] == source.run_id
    assert recovered_attempt["economic_pnl"] == -20.0
    stats = repository.get_stats()
    assert stats["drawdown_kill_switch_tripped"] is True
    assert stats["drawdown_kill_switch"] == first
    session.close()


def test_corrupt_drawdown_latch_fails_closed_instead_of_becoming_off(tmp_path):
    Session = init_database(str(tmp_path / "corrupt-latch.db"))
    session = Session()
    session.add(
        ExperimentState(
            key="drawdown_kill_switch",
            value_json='{"tripped":false}',
        )
    )
    session.commit()
    repository = TradeRepository(session)
    with pytest.raises(RuntimeError, match="fail closed"):
        repository.get_drawdown_kill_switch()
    with pytest.raises(RuntimeError, match="fail closed"):
        repository.get_stats()
    session.close()


def test_per_job_lock_rejects_overlapping_process_writer(tmp_path):
    db_path = tmp_path / "data" / "kiwi-sim-b-3x2" / "trades_sim.db"
    with exclusive_job_run_lock(db_path):
        with pytest.raises(RuntimeError, match="이미 실행 중"):
            with exclusive_job_run_lock(db_path):
                pass
