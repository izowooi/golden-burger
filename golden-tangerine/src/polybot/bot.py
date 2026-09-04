"""Sports Resolution Hold Live trading-cycle orchestration."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from polybot_observability import RunAudit, log_reconciliation_continuity
from polybot_observability import SQLiteMaintenanceRequirements

from .api.clob_client import ClobClientWrapper
from .api.gamma_client import GammaClient
from .config import BotConfig
from .db.models import init_database
from .db.repository import TradeRepository
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader


logger = logging.getLogger(__name__)


class PolymarketBot:
    """Archive every cycle; trade only under the resolved lifecycle mode."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.Session = init_database(
            str(config.db_path),
            SQLiteMaintenanceRequirements(
                full_cadence_hours=float(
                    config.trading.max_snapshot_gap_minutes
                )
                / 60.0,
                retention_days=float(config.trading.archive.retention_days),
            ),
        )
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(
            config.api,
            config.simulation_mode,
            audit_db_path=config.db_path,
            strategy_name="golden-tangerine",
        )
        logger.info(
            "Golden Tangerine bot initialized - job=%s simulation=%s lifecycle=%s "
            "all_outcomes=%s source=%s preregistration=%s",
            config.job_name,
            config.simulation_mode,
            config.trading.lifecycle_mode,
            not config.trading.yes_only_mode,
            config.trading.strategy_source_digest[:12],
            config.trading.preregistration_sha256[:12],
        )

    def _log_strategy_config(self) -> None:
        trading = self.config.trading
        entry = trading.entry
        archive = trading.archive
        logger.info(
            "Golden Tangerine configured $%.2f outcome VWAP [%.2f, %.2f], "
            "hours (%.1f, %.1f], hold-to-resolution",
            trading.buy_amount_usdc,
            entry.prob_min,
            entry.prob_max,
            entry.hours_min,
            entry.hours_max,
        )
        logger.info(
            "execution - FOK BUY only; no SELL/TP/stop/time-exit; "
            "$%.2f positions=%s event=%s new_per_cycle=%s",
            trading.buy_amount_usdc,
            trading.max_positions,
            trading.max_event_positions,
            trading.max_new_positions_per_cycle,
        )
        logger.info(
            "sports server envelope - liquidity>=%.0f cumulative_volume>=%.0f "
            "hours<=%.0f retention=%sd",
            trading.min_liquidity,
            trading.min_cumulative_volume,
            archive.hours_max,
            archive.retention_days,
        )

    @staticmethod
    def _execute_candidate_queue(repo, trader, candidates, cycle_limit: int) -> dict:
        """Continue after proven no-POST failures; stop after reserved POST capacity."""
        bought = 0
        post_reservations = 0
        for candidate in candidates:
            episode_id = candidate.get("entry_episode_id")
            if post_reservations >= cycle_limit:
                if isinstance(episode_id, int) and not isinstance(episode_id, bool):
                    repo.mark_entry_episode_execution(
                        episode_id,
                        state="CYCLE_POST_CAP_PROVEN_NO_POST",
                        reason="earlier_candidate_reserved_cycle_post_capacity",
                        proven_no_post=True,
                        post_may_have_occurred=False,
                    )
                continue
            trade_id = trader.execute_buy(candidate)
            if trade_id is not None:
                bought += 1
            if trade_id is not None or trader.last_entry_may_have_reached_venue:
                post_reservations += 1
        return {"bought": bought, "post_reservations": post_reservations}

    def run_cycle(self) -> dict:
        trading = self.config.trading
        session = self.Session()
        repo = TradeRepository(session)
        scanner = MarketScanner(
            self.gamma,
            self.config.trading,
            repo,
            clob_client=self.clob,
        )
        trader = Trader(
            repo,
            self.clob,
            self.config.trading,
            gamma_client=self.gamma,
            simulation_mode=self.config.simulation_mode,
        )
        cycle_id = str(uuid4())
        cycle_started = time.monotonic()
        repo.append_cycle_runtime_event(
            cycle_id=cycle_id,
            phase="cycle",
            status="STARTED",
            elapsed_seconds=0.0,
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
        }
        try:
            self._log_strategy_config()
            markets = scanner.fetch_markets()

            logger.info("=== Phase 0: exact-book sports archive ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)
            repo.append_cycle_runtime_event(
                cycle_id=cycle_id,
                phase="archive",
                status="COMPLETED",
                elapsed_seconds=time.monotonic() - cycle_started,
                detail={"snapshots_saved": stats["snapshots_saved"]},
            )
            lifecycle_mode = trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning("archive_only: 주문 및 포지션 mutation을 건너뜁니다")
            else:
                logger.info("=== Phase 1: own-order reconciliation / resolution ===")
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
                        completed = repo.get_by_id(pending_trade.id)
                        if completed is not None:
                            repo.append_trade_to_csv(
                                completed, self.config.db_path.parent
                            )
                holdings = repo.get_holding_trades()
                stats["checked_holdings"] = len(holdings)
                resolved_before = repo.get_stats()["resolved"]
                if holdings:
                    with self.clob.midpoint_snapshot(
                        trade.token_id for trade in holdings
                    ):
                        for trade in holdings:
                            if trader.execute_sell(trade):
                                stats["sold"] += 1
                                updated = repo.get_by_id(trade.id)
                                if updated is not None:
                                    repo.append_trade_to_csv(
                                        updated, self.config.db_path.parent
                                    )
                stats["resolved"] = max(
                    0, repo.get_stats()["resolved"] - resolved_before
                )

            if lifecycle_mode == "active":
                logger.info("=== Phase 2: frozen exact-VWAP arm scan ===")
                candidates = scanner.scan_buy_candidates(markets)
                stats["buy_candidates"] = len(candidates)
                logger.info("=== Phase 3: fresh-book FOK BUY execution ===")
                execution = self._execute_candidate_queue(
                    repo,
                    trader,
                    candidates,
                    trading.max_new_positions_per_cycle,
                )
                stats["bought"] += execution["bought"]
                stats["cycle_post_reservations"] = execution["post_reservations"]
            else:
                logger.warning("%s: 신규 진입을 건너뜁니다", lifecycle_mode)

            logger.info("=== Phase 4: archive retention cleanup ===")
            repo.cleanup_old_snapshots(
                days=self.config.trading.archive.retention_days
            )
            db_stats = repo.get_stats()
            capacity = repo.get_entry_capacity_state(
                base_notional_usdc=trading.buy_amount_usdc
            )
            stats["open_states"] = {
                "pending_buy": db_stats["pending_buy"],
                "holding": db_stats["holding"],
                "pending_sell": db_stats["pending_sell"],
                "quarantined": db_stats["quarantined"],
                "untracked_buy_reservations": capacity["untracked_buy_reservations"],
                "prepost_crash_reservations": capacity["prepost_crash_reservations"],
                "total": capacity["total_reserved"],
                "notional_usdc": capacity["total_notional_usdc"],
            }
            elapsed = time.monotonic() - cycle_started
            stats["cycle_runtime"] = {
                "cycle_id": cycle_id,
                "elapsed_seconds": elapsed,
                "warning_seconds": trading.cycle_runtime_warning_seconds,
                "warning_exceeded": elapsed > trading.cycle_runtime_warning_seconds,
                "hard_kill_enabled": False,
            }
            repo.append_cycle_runtime_event(
                cycle_id=cycle_id,
                phase="cycle",
                status="SUCCEEDED",
                elapsed_seconds=elapsed,
                detail=stats["cycle_runtime"],
            )
            if stats["cycle_runtime"]["warning_exceeded"]:
                logger.warning(
                    "cycle runtime warning exceeded; process was not killed - elapsed=%.3fs",
                    elapsed,
                )
            logger.info(
                "cycle complete - snapshots=%s checked=%s sells=%s resolved=%s "
                "candidates=%s buys=%s open=%s/%s (pending_buy=%s holding=%s "
                "pending_sell=%s) realized_pnl=$%.4f",
                stats["snapshots_saved"],
                stats["checked_holdings"],
                stats["sold"],
                stats["resolved"],
                stats["buy_candidates"],
                stats["bought"],
                stats["open_states"]["total"],
                self.config.trading.max_positions,
                db_stats["pending_buy"],
                db_stats["holding"],
                db_stats["pending_sell"],
                db_stats["total_pnl"],
            )
            return stats
        except Exception as error:
            repo.rollback()
            try:
                repo.append_cycle_runtime_event(
                    cycle_id=cycle_id,
                    phase="cycle",
                    status="FAILED",
                    elapsed_seconds=time.monotonic() - cycle_started,
                    detail={"error_type": type(error).__name__},
                )
            except Exception:
                logger.exception("failed to persist cycle runtime failure telemetry")
            raise
        finally:
            session.close()

    def run(self) -> None:
        logger.info("트레이딩 사이클 시작 - %s", self.config.job_name)
        audit = RunAudit.start(self.config, strategy_name="golden-tangerine")
        try:
            self.gamma.sweep_attestations.clear()
            reconciliation = self.clob.reconcile_order_ledger()
            log_reconciliation_continuity(reconciliation, logger=logger)
            stats = self.run_cycle()
            stats["market_sweeps"] = self.gamma.get_sweep_summaries()
            stats["order_reconciliation"] = reconciliation
            audit.succeed(stats)
            logger.info("사이클 성공: %s", stats)
        except Exception as error:
            audit.fail(error)
            logger.exception("사이클 실패: %s", error)
            raise

    def get_status(self) -> dict:
        session = self.Session()
        repo = TradeRepository(session)
        try:
            trading = self.config.trading
            holdings = repo.get_holding_trades()
            capacity = repo.get_entry_capacity_state(
                base_notional_usdc=trading.buy_amount_usdc
            )
            return {
                "strategy": "Golden Tangerine Sports Resolution Hold Live",
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "lifecycle_mode": trading.lifecycle_mode,
                "db_path": str(self.config.db_path),
                "statistics": repo.get_stats(),
                "entry_capacity": capacity,
                "exact_economic_loss": repo.get_exact_economic_loss_state(),
                "holdings": [
                    {
                        "id": trade.id,
                        "condition_id": trade.condition_id,
                        "event_id": trade.event_id,
                        "question": trade.question,
                        "buy_price": trade.buy_price,
                        "yes_price_at_buy": trade.yes_price_at_buy,
                        "stop_price": trade.stop_price_at_entry,
                        "buy_timestamp": (
                            trade.buy_timestamp.isoformat()
                            if trade.buy_timestamp else None
                        ),
                    }
                    for trade in holdings
                ],
                "config": {
                    "buy_amount_usdc": trading.buy_amount_usdc,
                    "max_open_notional_usdc": trading.max_open_notional_usdc,
                    "max_cumulative_exact_loss_usdc": (
                        trading.max_cumulative_exact_loss_usdc
                    ),
                    "min_liquidity": trading.min_liquidity,
                    "min_volume_24h": trading.min_volume_24h,
                    "min_cumulative_volume": trading.min_cumulative_volume,
                    "max_positions": trading.max_positions,
                    "max_event_positions": trading.max_event_positions,
                    "max_new_positions_per_cycle": trading.max_new_positions_per_cycle,
                    "reentry_cooldown_hours": trading.reentry_cooldown_hours,
                    "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
                    "min_order_size": trading.min_order_size,
                    "min_order_buffer_shares": trading.min_order_buffer_shares,
                    "yes_only_mode": trading.yes_only_mode,
                    "exclude_esports": trading.exclude_esports,
                    "cycle_runtime_warning_seconds": (
                        trading.cycle_runtime_warning_seconds
                    ),
                    "entry": {
                        "prob_min": trading.entry.prob_min,
                        "prob_max": trading.entry.prob_max,
                        "stop_price": trading.entry.stop_price,
                        "hours_min": trading.entry.hours_min,
                        "hours_max": trading.entry.hours_max,
                    },
                    "archive": {
                        "prob_min": trading.archive.prob_min,
                        "hours_max": trading.archive.hours_max,
                        "retention_days": trading.archive.retention_days,
                    },
                },
            }
        finally:
            session.close()
