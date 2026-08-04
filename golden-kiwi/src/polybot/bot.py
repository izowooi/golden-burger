"""Micro-Cascade research-cycle orchestration."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import logging
import os
from pathlib import Path

from polybot_observability import RunAudit, log_reconciliation_continuity
from polybot_observability import SQLiteMaintenanceRequirements
from sqlalchemy import text

from .api.clob_client import ClobClientWrapper
from .api.gamma_client import GammaClient
from .config import BotConfig, ExperimentCollectionConfig
from .db.models import init_database
from .db.repository import TradeRepository
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader


logger = logging.getLogger(__name__)


@contextmanager
def exclusive_job_run_lock(db_path: Path):
    """Enforce one writer per canonical job across overlapping processes."""
    lock_path = (
        Path(db_path).expanduser().resolve().parent
        / ".golden-kiwi-run.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                f"동일 Golden Kiwi job이 이미 실행 중입니다: {lock_path.parent.name}"
            ) from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class PolymarketBot:
    """Archive every cycle; trade only under the resolved lifecycle mode."""

    def __init__(self, config: BotConfig):
        if not config.simulation_mode:
            raise RuntimeError(
                "Golden Kiwi is research/simulation-only; live bot startup "
                "is disabled before any CLOB client is created"
            )
        self.config = config
        # Schema migration/maintenance is a write too, so it shares the exact
        # same per-job lock as the trading cycle.
        with exclusive_job_run_lock(config.db_path):
            self.Session = init_database(
                str(config.db_path),
                SQLiteMaintenanceRequirements(
                    # Kiwi needs every real 5-minute observation for its entire
                    # 60-day lineage/review horizon. Cold rollup would destroy the
                    # staircase evidence even though the experiment lasts 30 days.
                    full_cadence_hours=float(
                        config.trading.archive.retention_days
                    )
                    * 24.0,
                    retention_days=float(config.trading.archive.retention_days),
                ),
            )
            # ExecutionLedger schema/bootstrap writes happen in the wrapper
            # constructor, so that construction belongs under the same
            # single-writer lock as database migration.
            self.gamma = GammaClient()
            self.clob = ClobClientWrapper(
                config.api,
                config.simulation_mode,
                audit_db_path=config.db_path,
                strategy_name="golden-kiwi",
            )
            if config.experiment.enabled:
                session = self.Session()
                try:
                    if (
                        config.experiment.window_start is None
                        or config.experiment.window_end is None
                        or config.experiment.expected_offset_minute is None
                    ):
                        raise RuntimeError(
                            "enabled experiment collection contract가 불완전합니다"
                        )
                    TradeRepository(session).ensure_experiment_contract(
                        canonical_job=config.job_name,
                        arm=config.trading.arm_name,
                        window_start=config.experiment.window_start,
                        window_end=config.experiment.window_end,
                        expected_cadence_minutes=(
                            config.experiment.expected_cadence_minutes
                        ),
                        expected_offset_minute=(
                            config.experiment.expected_offset_minute
                        ),
                        preregistration_sha256=(
                            config.experiment.preregistration_sha256
                        ),
                        analyzer_version=config.experiment.analyzer_version,
                    )
                finally:
                    session.close()
        logger.info(
            "Micro-Cascade bot 초기화 - job=%s simulation=%s lifecycle=%s "
            "arm=%s source_cohort=%s git_commit=provenance_only",
            config.job_name,
            config.simulation_mode,
            config.trading.lifecycle_mode,
            config.trading.arm_name,
            config.trading.strategy_source_digest[:12],
        )

    def _log_strategy_config(self) -> None:
        trading = self.config.trading
        entry = trading.entry
        archive = trading.archive
        logger.info(
            "Micro-Cascade arm %s - %s strictly-positive steps, cumulative "
            "[%.3f, %.3f], each step <= %.3f, gaps %.0f~%.0fmin",
            trading.arm_name,
            entry.confirmation_steps,
            entry.min_cumulative_move,
            entry.max_cumulative_move,
            entry.max_step_move,
            entry.min_snapshot_gap_minutes,
            entry.max_snapshot_gap_minutes,
        )
        logger.info(
            "universe - strict binary YES %.2f~%.2f, resolution >= %.1fh, "
            "exact excluded tags=%s",
            entry.prob_min,
            entry.prob_max,
            entry.min_hours_to_resolution,
            trading.excluded_categories,
        )
        logger.info(
            "실행 - fresh ask <= %.2f, spread <= %.3f, depth >= %.2fx; "
            "$%.2f, effective liquidity=$%.0f, volume24h=$%.0f",
            entry.prob_max,
            trading.max_spread,
            trading.depth_safety_multiple,
            trading.buy_amount_usdc,
            trading.effective_min_liquidity,
            trading.effective_min_volume_24h,
        )
        logger.info(
            "리스크 - simulation-only, hold=%.0fmin time exit, open notional "
            "<= $%.2f, positions=%s, event=%s, new/cycle=%s, cooldown=%.0fh",
            entry.hold_minutes,
            trading.max_open_notional_usdc,
            trading.max_positions,
            trading.max_event_positions,
            trading.max_new_positions_per_cycle,
            trading.reentry_cooldown_hours,
        )
        logger.info(
            "research archive - strict binary YES %.2f~%.2f, retention=%sd",
            archive.prob_min,
            archive.prob_max,
            archive.retention_days,
        )
        logger.info(
            "lineage - current-run snapshot required, %s persisted moves, "
            "each gap %.1f~%.1fmin",
            entry.confirmation_steps,
            entry.min_snapshot_gap_minutes,
            entry.max_snapshot_gap_minutes,
        )
        experiment = self.config.experiment
        if experiment.enabled:
            logger.info(
                "promotion collection - UTC [%s, %s), cadence=%sm offset=%s "
                "analyzer=v%s prereg=%s",
                experiment.window_start,
                experiment.window_end,
                experiment.expected_cadence_minutes,
                experiment.expected_offset_minute,
                experiment.analyzer_version,
                experiment.preregistration_sha256,
            )
        else:
            logger.warning(
                "promotion collection env가 없어 smoke/archive mode로 "
                "기록합니다; 이 run은 promotion population에 들어가지 않습니다"
            )

    def run_cycle(self) -> dict:
        session = self.Session()
        repo = TradeRepository(session)
        experiment = getattr(
            self.config,
            "experiment",
            ExperimentCollectionConfig(),
        )
        scanner = MarketScanner(
            self.gamma,
            self.config.trading,
            repo,
            experiment=experiment,
            job_name=getattr(self.config, "job_name", "default"),
        )
        trader = Trader(
            repo,
            self.clob,
            self.config.trading,
            gamma_client=self.gamma,
            simulation_mode=self.config.simulation_mode,
        )
        stats = {
            "lifecycle_mode": self.config.trading.lifecycle_mode,
            "snapshots_saved": 0,
            "pending_buys_checked": 0,
            "pending_buys_activated": 0,
            "pending_sells_checked": 0,
            "checked_holdings": 0,
            "sold": 0,
            "resolved": 0,
            "buy_candidates": 0,
            "bought": 0,
            "signal_decisions_appended": 0,
            "followup_observations_appended": 0,
            "drawdown_blocked": False,
        }
        try:
            self._log_strategy_config()
            drawdown_blocked = trader.evaluate_drawdown_stop()
            stats["drawdown_blocked"] = drawdown_blocked
            markets = scanner.fetch_markets()

            logger.info("=== Phase 0: Micro-Cascade research archive ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)
            stats[
                "followup_observations_appended"
            ] = repo.append_due_followup_observations(
                markets,
                observed_at=datetime.now(timezone.utc),
                fetch_market=self.gamma.get_market_by_condition_id,
            )
            lifecycle_mode = self.config.trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning("archive_only: 주문 및 포지션 mutation을 건너뜁니다")
            else:
                logger.info("=== Phase 1: exact BUY/SELL fill 및 exit 확인 ===")
                pending_buys = repo.get_pending_buy_trades()
                stats["pending_buys_checked"] = len(pending_buys)
                for pending_trade in pending_buys:
                    if trader.reconcile_pending_buy(pending_trade):
                        stats["pending_buys_activated"] += 1
                pending_sells = repo.get_pending_sell_trades()
                stats["pending_sells_checked"] = len(pending_sells)
                for pending_trade in pending_sells:
                    if trader.reconcile_pending_sell(pending_trade):
                        stats["sold"] += 1
                holdings = repo.get_holding_trades()
                stats["checked_holdings"] = len(holdings)
                resolved_before = repo.get_stats()["resolved"]
                for trade in holdings:
                    if trader.execute_sell(trade):
                        stats["sold"] += 1
                stats["resolved"] = max(
                    0, repo.get_stats()["resolved"] - resolved_before
                )

            if lifecycle_mode == "active":
                logger.info("=== Phase 2: persisted staircase scan ===")
                candidates = scanner.scan_buy_candidates(
                    markets,
                    drawdown_blocked=drawdown_blocked,
                )
                stats["buy_candidates"] = len(candidates)
                logger.info("=== Phase 3: fresh-ask BUY execution ===")
                attempt_order = 0
                for candidate in candidates:
                    if (
                        stats["bought"]
                        >= self.config.trading.max_new_positions_per_cycle
                    ):
                        logger.info(
                            "cycle 신규 포지션 한도 %s 도달",
                            self.config.trading.max_new_positions_per_cycle,
                        )
                        break
                    attempt_order += 1
                    candidate["_fresh_attempt_order"] = attempt_order
                    if trader.execute_buy(candidate) is not None:
                        stats["bought"] += 1
                for row in scanner.last_signal_funnel:
                    evidence = trader.last_attempt_evidence_by_condition.get(
                        str(row["condition_id"])
                    )
                    if evidence is not None:
                        row.update(evidence)
                    elif row["event_selected"]:
                        row["fresh_fail_reason"] = (
                            "not_attempted_after_cycle_selection"
                            if stats["bought"]
                            else "not_attempted"
                        )
                repo.append_signal_decisions(scanner.last_signal_funnel)
                stats["signal_decisions_appended"] = len(
                    scanner.last_signal_funnel
                )
            else:
                logger.warning("%s: 신규 진입을 건너뜁니다", lifecycle_mode)

            logger.info("=== Phase 4: archive retention cleanup ===")
            repo.cleanup_old_snapshots(
                days=self.config.trading.archive.retention_days
            )
            db_stats = repo.get_stats()
            logger.info(
                "사이클 완료 - snapshots=%s checked=%s time_exits=%s resolved=%s "
                "candidates=%s buys=%s holding=%s research_economic_pnl=$%.4f "
                "drawdown_latched=%s",
                stats["snapshots_saved"],
                stats["checked_holdings"],
                stats["sold"],
                stats["resolved"],
                stats["buy_candidates"],
                stats["bought"],
                db_stats["holding"],
                db_stats["research_economic_pnl"],
                db_stats.get("drawdown_kill_switch_tripped", False),
            )
            return stats
        finally:
            session.close()

    def run(self) -> None:
        logger.info("트레이딩 사이클 시작 - %s", self.config.job_name)
        with exclusive_job_run_lock(self.config.db_path):
            audit = RunAudit.start(self.config, strategy_name="golden-kiwi")
            try:
                recovery_session = self.Session()
                try:
                    recovery_repo = TradeRepository(recovery_session)
                    recovery_repo.reconcile_staged_drawdown_kill_switch(
                        current_detection_run_id=audit.run_id
                    )
                    invalidated = recovery_repo.invalidate_non_successful_run_evidence(
                        exclude_run_id=audit.run_id
                    )
                    recovery_repo.export_unmaterialized_successful_exits(
                        self.config.db_path.parent
                    )
                finally:
                    recovery_session.close()
                if invalidated:
                    logger.warning(
                        "이전 FAILED/stale RUNNING run evidence %s건을 "
                        "격리·재개방했습니다",
                        invalidated,
                    )
                self.gamma.sweep_attestations.clear()
                reconciliation = self.clob.reconcile_order_ledger()
                log_reconciliation_continuity(reconciliation, logger=logger)
                stats = self.run_cycle()
                stats["market_sweeps"] = self.gamma.get_sweep_summaries()
                stats["order_reconciliation"] = reconciliation
                audit.succeed(stats)
                latch_session = self.Session()
                try:
                    TradeRepository(
                        latch_session
                    ).finalize_staged_drawdown_kill_switch(audit.run_id)
                except Exception as latch_error:
                    # The detector run is already durably SUCCESS.  Leaving the
                    # append-by-key pending row intact is safe: startup recovery
                    # deterministically finalizes it before the next cycle.
                    logger.warning(
                        "SUCCESS drawdown latch 확정은 다음 cycle에 재시도합니다: %s",
                        type(latch_error).__name__,
                    )
                finally:
                    latch_session.close()
                export_session = self.Session()
                try:
                    TradeRepository(
                        export_session
                    ).export_unmaterialized_successful_exits(
                        self.config.db_path.parent
                    )
                except Exception as export_error:
                    logger.warning(
                        "SUCCESS run CSV materialization은 다음 cycle에 재시도합니다: %s",
                        type(export_error).__name__,
                    )
                finally:
                    export_session.close()
                logger.info("사이클 성공: %s", stats)
            except Exception as error:
                audit.fail(error)
                recovery_session = self.Session()
                try:
                    recovery_repo = TradeRepository(recovery_session)
                    recovery_repo.invalidate_non_successful_run_evidence(
                        only_run_id=audit.run_id
                    )
                    status = recovery_session.execute(
                        text(
                            "SELECT status FROM run_audits WHERE run_id = :run_id"
                        ),
                        {"run_id": audit.run_id},
                    ).scalar_one_or_none()
                    if status == "FAILED":
                        recovery_repo.discard_staged_drawdown_kill_switch(
                            audit.run_id
                        )
                finally:
                    recovery_session.close()
                logger.exception("사이클 실패: %s", error)
                raise

    def get_status(self) -> dict:
        session = self.Session()
        repo = TradeRepository(session)
        try:
            trading = self.config.trading
            holdings = repo.get_holding_trades()
            return {
                "strategy": "Micro-Cascade",
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "lifecycle_mode": trading.lifecycle_mode,
                "db_path": str(self.config.db_path),
                "experiment": {
                    "enabled": self.config.experiment.enabled,
                    "window_start": self.config.experiment.window_start,
                    "window_end": self.config.experiment.window_end,
                    "expected_cadence_minutes": (
                        self.config.experiment.expected_cadence_minutes
                    ),
                    "expected_offset_minute": (
                        self.config.experiment.expected_offset_minute
                    ),
                    "preregistration_sha256": (
                        self.config.experiment.preregistration_sha256
                    ),
                    "analyzer_version": (
                        self.config.experiment.analyzer_version
                    ),
                },
                "statistics": repo.get_stats(),
                "holdings": [
                    {
                        "id": trade.id,
                        "condition_id": trade.condition_id,
                        "event_id": trade.event_id,
                        "question": trade.question,
                        "buy_price": trade.buy_price,
                        "yes_price_at_buy": trade.yes_price_at_buy,
                        "confirmation_steps": trade.confirmation_steps_at_entry,
                        "cumulative_move": trade.cumulative_move_at_entry,
                        "hold_minutes_target": trade.hold_minutes_target_at_entry,
                        "buy_timestamp": (
                            trade.buy_timestamp.isoformat()
                            if trade.buy_timestamp else None
                        ),
                    }
                    for trade in holdings
                ],
                "config": {
                    "buy_amount_usdc": trading.buy_amount_usdc,
                    "min_liquidity": trading.min_liquidity,
                    "effective_min_liquidity": trading.effective_min_liquidity,
                    "min_volume_24h": trading.min_volume_24h,
                    "effective_min_volume_24h": (
                        trading.effective_min_volume_24h
                    ),
                    "max_positions": trading.max_positions,
                    "max_event_positions": trading.max_event_positions,
                    "max_open_notional_usdc": trading.max_open_notional_usdc,
                    "max_new_positions_per_cycle": (
                        trading.max_new_positions_per_cycle
                    ),
                    "reentry_cooldown_hours": trading.reentry_cooldown_hours,
                    "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
                    "arm": trading.arm_name,
                    "min_order_size": trading.min_order_size,
                    "min_order_buffer_shares": trading.min_order_buffer_shares,
                    "yes_only_mode": trading.yes_only_mode,
                    "entry": {
                        "confirmation_steps": (
                            trading.entry.confirmation_steps
                        ),
                        "min_cumulative_move": (
                            trading.entry.min_cumulative_move
                        ),
                        "min_step_move": trading.entry.min_step_move,
                        "max_step_move": trading.entry.max_step_move,
                        "max_cumulative_move": (
                            trading.entry.max_cumulative_move
                        ),
                        "min_snapshot_gap_minutes": (
                            trading.entry.min_snapshot_gap_minutes
                        ),
                        "max_snapshot_gap_minutes": (
                            trading.entry.max_snapshot_gap_minutes
                        ),
                        "prob_min": trading.entry.prob_min,
                        "prob_max": trading.entry.prob_max,
                        "min_hours_to_resolution": (
                            trading.entry.min_hours_to_resolution
                        ),
                        "hold_minutes": trading.entry.hold_minutes,
                    },
                    "archive": {
                        "prob_min": trading.archive.prob_min,
                        "prob_max": trading.archive.prob_max,
                        "retention_days": trading.archive.retention_days,
                    },
                    "excluded_categories_exact": list(
                        trading.excluded_categories
                    ),
                },
            }
        finally:
            session.close()
