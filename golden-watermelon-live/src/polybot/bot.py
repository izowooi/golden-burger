"""Golden Watermelon live trading-cycle orchestration."""

from __future__ import annotations

import logging

from polybot_observability import RunAudit, log_reconciliation_continuity
from polybot_observability import SQLiteMaintenanceRequirements

from .api.clob_client import ClobClientWrapper, PreSubmissionContractError
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
            strategy_name="golden-watermelon-live",
        )
        logger.info(
            "Golden Watermelon Live bot initialized - job=%s simulation=%s lifecycle=%s "
            "match_result_yes_only=%s source=%s preregistration=%s",
            config.job_name,
            config.simulation_mode,
            config.trading.lifecycle_mode,
            config.trading.yes_only_mode,
            config.trading.strategy_source_digest[:12],
            config.trading.preregistration_sha256[:12],
        )

    def _log_strategy_config(self) -> None:
        trading = self.config.trading
        entry = trading.entry
        archive = trading.archive
        logger.info(
            "Golden Watermelon Live exact $5 result-YES VWAP [%.3f, %.3f], "
            "in-play age [%.1f, %.1f]h, hold-to-resolution",
            entry.prob_min,
            entry.prob_max,
            entry.hours_min,
            entry.hours_max,
        )
        logger.info(
            "execution - FOK BUY + bid-triggered FOK emergency stop %.2f; "
            "stop-limit floor %.2f spread<=%.2f loss<=%.0f%%; "
            "no TP/time-exit; $%.2f positions=%s event=%s new_per_cycle=%s "
            "emergency_sells_per_cycle=%s drawdown_entry_guard=-$%.2f",
            entry.stop_price,
            entry.stop_price - entry.max_stop_slippage,
            entry.max_stop_spread,
            entry.max_stop_loss_fraction * 100,
            trading.buy_amount_usdc,
            trading.max_positions,
            trading.max_event_positions,
            trading.max_new_positions_per_cycle,
            trading.max_emergency_sells_per_cycle,
            trading.experiment_capital_usdc * trading.max_drawdown_stop,
        )
        logger.info(
            "server envelope - live soccer tag=100350; exact-$5 CLOB depth gate; "
            "in_play_hours<=%.0f retention=%sd",
            archive.hours_max,
            archive.retention_days,
        )

    def run_cycle(self, *, order_reconciliation: dict | None = None) -> dict:
        trading = self.config.trading
        order_reconciliation = order_reconciliation or {}
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
            "entry_blocked_candidates": 0,
            "orphan_buy_recovery": {
                "checked": 0,
                "recovered": 0,
                "evidence_gaps": 0,
                "identity_gaps": 0,
                "duplicate_token_submissions": 0,
            },
        }
        try:
            self._log_strategy_config()
            markets = scanner.fetch_markets()
            trader.set_cycle_markets(markets)

            logger.info("=== Phase 0: exact-book sports archive ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)
            sweep = self.gamma.last_sweep_attestation
            if not isinstance(sweep, dict):
                sweep = {}
            exclusion_counts = sweep.get("exclusion_counts") or {}
            drift_excluded = sum(
                int(count)
                for reason, count in exclusion_counts.items()
                if ":status=drift" in str(reason)
            )
            stats["universe_health"] = {
                "raw_market_count": int(sweep.get("raw_market_count", 0)),
                "qualified_market_count": int(
                    sweep.get("qualified_market_count", 0)
                ),
                "drift_excluded_count": drift_excluded,
                "metadata_drift_suspected": drift_excluded > 0,
            }
            lifecycle_mode = trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning("archive_only: 주문 및 포지션 mutation을 건너뜁니다")
            else:
                logger.info("=== Phase 1: own-order reconciliation / resolution ===")
                stats["orphan_buy_recovery"] = trader.recover_orphan_buys()
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
                sell_walks = {}
                batch_sell_books = getattr(
                    self.clob, "get_sell_book_walks", None
                )
                if holdings and callable(batch_sell_books):
                    sell_requests = {}
                    for trade in holdings:
                        try:
                            sell_requests[str(trade.token_id)] = (
                                trader.signable_sell_shares(trade)
                            )
                        except (TypeError, ValueError):
                            logger.error(
                                "holding share envelope is invalid; scoped single "
                                "fallback will fail closed - trade=%s",
                                trade.id,
                            )
                    sell_walks = batch_sell_books(sell_requests)
                for trade in holdings:
                    token_id = str(trade.token_id)
                    if token_id in sell_walks:
                        sold = trader.execute_sell(
                            trade,
                            prefetched_walk=sell_walks[token_id],
                            book_prefetched=True,
                        )
                    else:
                        sold = trader.execute_sell(trade)
                    if sold:
                        stats["sold"] += 1
                        updated = repo.get_by_id(trade.id)
                        if updated is not None:
                            repo.append_trade_to_csv(
                                updated, self.config.db_path.parent
                            )
                stats["resolved"] = max(
                    0, repo.get_stats()["resolved"] - resolved_before
                )
                stats["emergency_sell_guard"] = {
                    "submitted": trader.emergency_sell_submissions,
                    "blocked": trader.emergency_sell_guard_blocks,
                    "per_cycle_limit": trading.max_emergency_sells_per_cycle,
                }

            if lifecycle_mode == "active":
                logger.info("=== Phase 2: frozen exact-VWAP arm scan ===")
                candidates = scanner.scan_buy_candidates(markets)
                stats["buy_candidates"] = len(candidates)
                state_before_entry = repo.get_stats()
                economic_guard = repo.get_economic_pnl_guard()
                realized_pnl = float(
                    economic_guard.get("confirmed_sell_pnl") or 0.0
                )
                settlement_pnl = float(
                    economic_guard.get("proven_resolution_pnl") or 0.0
                )
                economic_pnl = float(economic_guard.get("economic_pnl") or 0.0)
                economic_evidence_gaps = int(
                    economic_guard.get("evidence_gaps") or 0
                )
                drawdown_limit = (
                    trading.experiment_capital_usdc * trading.max_drawdown_stop
                )
                drawdown_triggered = economic_pnl <= -drawdown_limit + 1e-9
                stats["drawdown_guard"] = {
                    "triggered": drawdown_triggered,
                    "economic_pnl": economic_pnl,
                    "confirmed_sell_pnl": realized_pnl,
                    "proven_resolution_pnl": settlement_pnl,
                    "recorded_realized_pnl": float(
                        economic_guard.get("recorded_realized_pnl") or 0.0
                    ),
                    "recorded_settlement_pnl": float(
                        economic_guard.get("recorded_settlement_pnl") or 0.0
                    ),
                    "execution_adjustment_pnl": float(
                        economic_guard.get("execution_adjustment_pnl") or 0.0
                    ),
                    "invalidated_settlement_pnl": float(
                        economic_guard.get("invalidated_settlement_pnl") or 0.0
                    ),
                    "execution_override_count": int(
                        economic_guard.get("execution_override_count") or 0
                    ),
                    "evidence_gaps": economic_evidence_gaps,
                    "loss_limit_usdc": drawdown_limit,
                }
                if (
                    stats["drawdown_guard"]["execution_override_count"]
                    or economic_evidence_gaps
                ):
                    logger.warning(
                        "economic P&L guard used execution-ledger truth - "
                        "economic=$%.4f recorded_realized=$%.4f "
                        "recorded_settlement=$%.4f execution_adjustment=$%.4f "
                        "invalidated_settlement=$%.4f overrides=%s gaps=%s",
                        economic_pnl,
                        stats["drawdown_guard"]["recorded_realized_pnl"],
                        stats["drawdown_guard"]["recorded_settlement_pnl"],
                        stats["drawdown_guard"]["execution_adjustment_pnl"],
                        stats["drawdown_guard"]["invalidated_settlement_pnl"],
                        stats["drawdown_guard"]["execution_override_count"],
                        economic_evidence_gaps,
                    )
                capacity = repo.get_entry_capacity_state()
                open_buy_evidence_gaps = repo.get_open_buy_evidence_gap_count()
                blocking_reasons = []
                degraded_reasons = []
                if state_before_entry["pending_buy"]:
                    blocking_reasons.append("pending_buy_unresolved")
                if state_before_entry["pending_sell"]:
                    blocking_reasons.append("pending_sell_unresolved")
                if state_before_entry["quarantined"]:
                    blocking_reasons.append("quarantined_position")
                if int(order_reconciliation.get("unresolved_sell_outcomes", 0)):
                    blocking_reasons.append("unresolved_sell_outcome")
                if int(order_reconciliation.get("reconciliation_sell_gaps", 0)):
                    blocking_reasons.append("sell_reconciliation_gap")
                if capacity["total_reserved"] >= trading.max_positions:
                    blocking_reasons.append("max_capacity_reserved")
                if capacity["untracked_buy_reservations"]:
                    blocking_reasons.append("untracked_buy_exposure")
                if open_buy_evidence_gaps:
                    blocking_reasons.append("open_buy_fill_or_fee_evidence_gap")
                if int(order_reconciliation.get("unresolved_buy_outcomes", 0)):
                    blocking_reasons.append("unresolved_buy_outcome")
                if int(order_reconciliation.get("reconciliation_buy_gaps", 0)):
                    blocking_reasons.append("buy_reconciliation_gap")
                if int(order_reconciliation.get("errors", 0)):
                    blocking_reasons.append("order_reconciliation_error")
                if drawdown_triggered:
                    blocking_reasons.append("economic_drawdown_limit_reached")
                if economic_evidence_gaps:
                    blocking_reasons.append("economic_pnl_execution_evidence_gap")
                if stats["universe_health"]["metadata_drift_suspected"]:
                    blocking_reasons.append("league_identity_metadata_drift")
                entry_guard = {
                    "blocked": bool(blocking_reasons),
                    "blocking_reasons": blocking_reasons,
                    "degraded_reasons": degraded_reasons,
                    "open_positions": capacity["open_positions"],
                    "untracked_buy_reservations": capacity[
                        "untracked_buy_reservations"
                    ],
                    "total_reserved": capacity["total_reserved"],
                    "open_buy_evidence_gaps": open_buy_evidence_gaps,
                    "max_positions": trading.max_positions,
                    "capacity_remaining": max(
                        0, trading.max_positions - capacity["total_reserved"]
                    ),
                    "pending_buy": state_before_entry["pending_buy"],
                    "pending_sell": state_before_entry["pending_sell"],
                    "quarantined": state_before_entry["quarantined"],
                    "unresolved_buy_outcomes": int(
                        order_reconciliation.get("unresolved_buy_outcomes", 0)
                    ),
                    "unresolved_sell_outcomes": int(
                        order_reconciliation.get("unresolved_sell_outcomes", 0)
                    ),
                    "reconciliation_buy_gaps": int(
                        order_reconciliation.get("reconciliation_buy_gaps", 0)
                    ),
                    "reconciliation_sell_gaps": int(
                        order_reconciliation.get("reconciliation_sell_gaps", 0)
                    ),
                    "reconciliation_errors": int(
                        order_reconciliation.get("errors", 0)
                    ),
                    "economic_pnl": economic_pnl,
                    "drawdown_loss_limit_usdc": drawdown_limit,
                }
                stats["entry_guard"] = entry_guard
                if blocking_reasons:
                    stats["entry_blocked_candidates"] = len(candidates)
                    block_reason = ",".join(blocking_reasons)
                    for candidate in candidates:
                        episode_id = candidate.get("entry_episode_id")
                        if (
                            not isinstance(episode_id, bool)
                            and isinstance(episode_id, int)
                        ):
                            repo.mark_entry_episode_execution(
                                episode_id,
                                state="BLOCKED_GUARD",
                                reason=block_reason,
                            )
                    logger.warning(
                        "entry guard가 신규 BUY를 차단합니다 - reasons=%s "
                        "candidates=%s reserved=%s/%s",
                        ",".join(blocking_reasons),
                        len(candidates),
                        capacity["total_reserved"],
                        trading.max_positions,
                    )
                else:
                    logger.info("=== Phase 3: fresh-book FOK BUY execution ===")
                    for candidate in candidates[
                        : trading.max_new_positions_per_cycle
                    ]:
                        episode_id = candidate.get("entry_episode_id")
                        try:
                            trade_id = trader.execute_buy(candidate)
                        except Exception as error:
                            if (
                                not isinstance(episode_id, bool)
                                and isinstance(episode_id, int)
                            ):
                                retryable_pre_submission = isinstance(
                                    error, PreSubmissionContractError
                                )
                                repo.mark_entry_episode_execution(
                                    episode_id,
                                    state=(
                                        "PRE_SUBMISSION_CONTRACT_ERROR"
                                        if retryable_pre_submission
                                        else "EXECUTION_EXCEPTION"
                                    ),
                                    reason=type(error).__name__,
                                )
                            raise
                        if trade_id is not None:
                            stats["bought"] += 1
                        elif (
                            not isinstance(episode_id, bool)
                            and isinstance(episode_id, int)
                        ):
                            repo.mark_entry_episode_execution(
                                episode_id,
                                state="NOT_EXECUTED",
                                reason=(
                                    trader.last_entry_outcome_reason
                                    or "unspecified_fail_closed_rejection"
                                ),
                            )
            else:
                logger.warning("%s: 신규 진입을 건너뜁니다", lifecycle_mode)

            logger.info("=== Phase 4: archive retention cleanup ===")
            repo.cleanup_old_snapshots(
                days=self.config.trading.archive.retention_days
            )
            db_stats = repo.get_stats()
            stats["open_states"] = {
                "pending_buy": db_stats["pending_buy"],
                "holding": db_stats["holding"],
                "pending_sell": db_stats["pending_sell"],
                "quarantined": db_stats["quarantined"],
                "total": (
                    db_stats["pending_buy"]
                    + db_stats["holding"]
                    + db_stats["pending_sell"]
                    + db_stats["quarantined"]
                ),
            }
            logger.info(
                "cycle complete - snapshots=%s checked=%s sells=%s resolved=%s "
                "candidates=%s buys=%s open=%s/%s (pending_buy=%s holding=%s "
                "pending_sell=%s quarantined=%s) recorded_trade_pnl=$%.4f "
                "economic_guard_pnl=%s",
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
                db_stats["quarantined"],
                db_stats["total_pnl"],
                (
                    f"${stats['drawdown_guard']['economic_pnl']:.4f}"
                    if "drawdown_guard" in stats
                    else "not_evaluated"
                ),
            )
            return stats
        finally:
            session.close()

    def run(self) -> None:
        logger.info("트레이딩 사이클 시작 - %s", self.config.job_name)
        audit = RunAudit.start(self.config, strategy_name="golden-watermelon-live")
        try:
            self.gamma.sweep_attestations.clear()
            reconciliation = self.clob.reconcile_order_ledger()
            log_reconciliation_continuity(reconciliation, logger=logger)
            stats = self.run_cycle(order_reconciliation=reconciliation)
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
            return {
                "strategy": "Golden Watermelon Live In-Play Match Result",
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "lifecycle_mode": trading.lifecycle_mode,
                "db_path": str(self.config.db_path),
                "statistics": repo.get_stats(),
                "economic_pnl_guard": repo.get_economic_pnl_guard(),
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
                    "min_liquidity": trading.min_liquidity,
                    "min_volume_24h": trading.min_volume_24h,
                    "min_cumulative_volume": trading.min_cumulative_volume,
                    "max_positions": trading.max_positions,
                    "max_event_positions": trading.max_event_positions,
                    "max_new_positions_per_cycle": trading.max_new_positions_per_cycle,
                    "max_emergency_sells_per_cycle": (
                        trading.max_emergency_sells_per_cycle
                    ),
                    "experiment_capital_usdc": trading.experiment_capital_usdc,
                    "max_drawdown_stop": trading.max_drawdown_stop,
                    "reentry_cooldown_hours": trading.reentry_cooldown_hours,
                    "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
                    "min_order_size": trading.min_order_size,
                    "min_order_buffer_shares": trading.min_order_buffer_shares,
                    "yes_only_mode": trading.yes_only_mode,
                    "entry": {
                        "prob_min": trading.entry.prob_min,
                        "prob_max": trading.entry.prob_max,
                        "stop_price": trading.entry.stop_price,
                        "max_stop_slippage": trading.entry.max_stop_slippage,
                        "max_stop_spread": trading.entry.max_stop_spread,
                        "max_stop_loss_fraction": (
                            trading.entry.max_stop_loss_fraction
                        ),
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
