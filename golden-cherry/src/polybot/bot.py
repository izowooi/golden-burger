"""Main bot orchestrator with resolution momentum strategy."""
import logging
from polybot_observability import RunAudit, log_reconciliation_continuity
from polybot_observability import SQLiteMaintenanceRequirements
from .config import BotConfig
from .api.gamma_client import GammaClient
from .api.clob_client import ClobClientWrapper
from .strategy.scanner import MarketScanner, format_entry_window
from .strategy.trader import Trader
from .db.models import init_database
from .db.repository import TradeRepository
from .utils.process_lock import DatabaseRunLock

logger = logging.getLogger(__name__)


class PolymarketBot:
    """Polymarket automated trading bot with resolution momentum strategy.

    Orchestrates the trading cycle:
    1. Check existing positions for sell conditions
    2. Scan markets for buy candidates (time-based filter)
    3. Execute buys for qualifying candidates
    """

    def __init__(self, config: BotConfig):
        """Initialize bot with configuration.

        Args:
            config: Bot configuration
        """
        self.config = config

        # Initialize database
        self.Session = init_database(
            str(config.db_path), SQLiteMaintenanceRequirements()
        )

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(
            config.api,
            config.simulation_mode,
            audit_db_path=config.db_path,
            strategy_name="golden-cherry",
        )

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"Time-based: {config.trading.time_based.enabled}, "
            f"YES-Only: {config.trading.yes_only_mode}, "
            f"Lifecycle: {config.trading.lifecycle_mode}"
        )

    def run_cycle(self) -> dict:
        """Execute one trading cycle.

        Returns:
            Dictionary with cycle statistics:
            - lifecycle_mode: str
            - checked_holdings: int
            - sold: int
            - buy_candidates: int
            - bought: int
        """
        session = self.Session()
        repo = TradeRepository(session)

        # Scanner for finding candidates
        scanner = MarketScanner(self.gamma, self.config.trading, repo)
        trader = Trader(
            repo,
            self.clob,
            self.config.trading,
            gamma_client=self.gamma,
        )

        stats = {
            "lifecycle_mode": self.config.trading.lifecycle_mode,
            "checked_holdings": 0,
            "sold": 0,
            "buy_candidates": 0,
            "bought": 0,
            "legacy_buys_reclassified": 0,
            "pending_buys_checked": 0,
            "pending_buys_activated": 0,
            "pending_sells_checked": 0,
            "entry_guard": None,
        }

        try:
            # Log strategy configuration at cycle start
            time_cfg = self.config.trading.time_based
            if time_cfg.enabled:
                time_exit_text = (
                    f"해결 {time_cfg.exit_hours}h 전"
                    if time_cfg.exit_hours > 0
                    else "비활성화"
                )
                logger.info(
                    f"Resolution Momentum 전략 - "
                    f"진입 기준시각: {format_entry_window(time_cfg.entry_hours_min, time_cfg.entry_hours_max)}, "
                    f"확률: {self.config.trading.buy_threshold:.0%} ~ {self.config.trading.sell_threshold:.0%}, "
                    f"시간 청산: {time_exit_text}"
                )
                game_cfg = self.config.trading.game_start
                logger.info(
                    "시간 기준 - 비스포츠=endDate, 스포츠=%s, 인플레이 허용=%s, "
                    "gameStartTime 누락 스포츠 차단=%s",
                    "gameStartTime" if game_cfg.enabled else "endDate",
                    game_cfg.allow_in_play,
                    game_cfg.reject_sports_without_game_start,
                )
                logger.info(
                    f"손익 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                    f"익절: {self.config.trading.take_profit_percent:.0%}, "
                    f"트레일링: {self.config.trading.trailing_stop.percent:.0%}"
                )
                logger.info(
                    "노출 한도 - 건당 $%.2f(하드캡 $%.2f), open $%.2f, "
                    "포지션 %d개, cycle 신규 %d개, 유동성 최소 $%.0f",
                    self.config.trading.buy_amount_usdc,
                    self.config.trading.max_buy_amount_usdc,
                    self.config.trading.max_open_notional_usdc,
                    self.config.trading.max_positions,
                    self.config.trading.max_new_positions_per_cycle,
                    self.config.trading.effective_min_liquidity,
                )
                logger.info(
                    "exact zero-fill BUY TTL - %s분",
                    self.config.trading.pending_buy_ttl_minutes,
                )
            else:
                logger.info("시간 기반 필터 비활성화 (확률 조건만 사용)")

            lifecycle_mode = self.config.trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning(
                    "=== Phase 1 건너뜀: archive_only 모드에서는 주문을 생성하지 않습니다 ==="
                )
            else:
                logger.info("=== Phase 0: exact BUY/SELL fill 대사 ===")
                # Narrow migration for legacy rows that were marked HOLDING on
                # GTC acceptance even though their exact BUY order is still
                # LIVE without full-fill proof.
                holdings = repo.get_holding_trades()
                for trade in holdings:
                    if trader.reclassify_unconfirmed_live_buy(trade):
                        stats["legacy_buys_reclassified"] += 1

                pending_buys = repo.get_pending_buy_trades()
                stats["pending_buys_checked"] = len(pending_buys)
                for trade in pending_buys:
                    if trader.reconcile_pending_buy(trade):
                        stats["pending_buys_activated"] += 1

                pending_sells = repo.get_pending_sell_trades()
                stats["pending_sells_checked"] = len(pending_sells)
                for trade in pending_sells:
                    if trader.reconcile_pending_sell(trade):
                        stats["sold"] += 1
                        updated_trade = repo.get_by_id(trade.id)
                        if updated_trade:
                            repo.append_trade_to_csv(
                                updated_trade, self.config.db_path.parent
                            )

                # Phase 1: Check only fill-confirmed holdings for sell signals.
                logger.info("=== Phase 1: 보유 포지션 매도 확인 ===")
                holdings = repo.get_holding_trades()
                stats["checked_holdings"] = len(holdings)

                if holdings:
                    with self.clob.midpoint_snapshot(
                        trade.token_id for trade in holdings
                    ):
                        for trade in holdings:
                            if trader.execute_sell(trade):
                                stats["sold"] += 1
                                updated_trade = repo.get_by_id(trade.id)
                                if updated_trade:
                                    repo.append_trade_to_csv(
                                        updated_trade, self.config.db_path.parent
                                    )

            entry_guard = None
            if lifecycle_mode == "active":
                entry_guard = trader.get_entry_guard()
                stats["entry_guard"] = entry_guard
                logger.info(
                    "exact-economic entry guard - allowed=%s economic=$%.2f "
                    "(SELL=$%.2f + resolution=$%.2f) floor=$%.2f "
                    "unknown_buy=%d incomplete_fee=%d resolution_gap=%d blockers=%s",
                    entry_guard["entry_allowed"],
                    entry_guard["exact_economic_pnl_usdc"],
                    entry_guard["exact_confirmed_sell_pnl_usdc"],
                    entry_guard["exact_proven_resolution_settlement_usdc"],
                    entry_guard["drawdown_floor_usdc"],
                    entry_guard["unknown_buy_evidence_count"],
                    entry_guard["incomplete_fee_evidence_count"],
                    entry_guard["resolution_evidence_gap_count"],
                    ",".join(entry_guard["blockers"]) or "none",
                )

            if lifecycle_mode == "active" and entry_guard["entry_allowed"]:
                # Phase 2: Scan for buy candidates
                logger.info("=== Phase 2: 매수 후보 스캔 ===")
                candidates = scanner.scan_buy_candidates()
                stats["buy_candidates"] = len(candidates)

                # Phase 3: Execute buys
                logger.info("=== Phase 3: 매수 실행 ===")
                for candidate in candidates:
                    # Skip if already traded
                    if repo.is_already_traded(candidate["condition_id"]):
                        logger.info(f"이미 거래한 시장 skip: {candidate['condition_id']}")
                        continue

                    if trader.execute_buy(candidate):
                        stats["bought"] += 1
            elif lifecycle_mode != "active":
                logger.warning(
                    "=== Phase 2/3 건너뜀: "
                    f"{lifecycle_mode} 모드에서 신규 진입이 차단됩니다 ==="
                )
            else:
                logger.error(
                    "=== Phase 2/3 건너뜀: exact-economic entry guard가 "
                    "신규 진입을 차단했습니다. 기존 청산/대사는 완료되었습니다 ==="
                )

            # Log statistics
            db_stats = repo.get_stats()
            stats["exposure"] = {
                key: db_stats[key]
                for key in (
                    "managed_open_position_count",
                    "managed_open_notional_usdc",
                    "untracked_buy_reservation_count",
                    "untracked_buy_reservation_notional_usdc",
                    "untracked_buy_unknown_outcome_count",
                    "untracked_buy_reconciliation_count",
                    "reserved_position_count",
                    "reserved_open_notional_usdc",
                )
            }
            logger.info(f"=== 사이클 완료 ===")
            logger.info(f"보유 포지션 확인: {stats['checked_holdings']}개")
            logger.info(f"매도: {stats['sold']}건")
            logger.info(f"매수 후보: {stats['buy_candidates']}개")
            logger.info(f"매수: {stats['bought']}건")
            logger.info(
                "총 capacity 예약: %s개/$%.2f (managed=%s개/$%.2f, "
                "미추적 BUY=%s개/$%.2f; PENDING_BUY=%s, HOLDING=%s, "
                "PENDING_SELL=%s, QUARANTINED=%s)",
                db_stats["reserved_position_count"],
                db_stats["reserved_open_notional_usdc"],
                db_stats["managed_open_position_count"],
                db_stats["managed_open_notional_usdc"],
                db_stats["untracked_buy_reservation_count"],
                db_stats["untracked_buy_reservation_notional_usdc"],
                db_stats["pending_buy"],
                db_stats["holding"],
                db_stats["pending_sell"],
                db_stats["quarantined"],
            )
            logger.info(
                "해결 증거: %s개, settlement assumption=$%.4f",
                db_stats.get("resolved", 0),
                db_stats.get("settlement_pnl_assumption", 0.0),
            )
            logger.info(f"확정 SELL realized P&L: ${db_stats['total_pnl']:.4f}")
            if db_stats.get("unproven_pnl_count", 0):
                logger.warning(
                    "legacy/미확정 realized_pnl: %s개 합계 $%.4f "
                    "(확정 SELL 통계에서 제외)",
                    db_stats["unproven_pnl_count"],
                    db_stats.get("unproven_pnl", 0.0),
                )

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        with DatabaseRunLock(self.config.db_path) as run_lock:
            if not run_lock.acquired:
                stats = {
                    "skipped": True,
                    "skip_reason": "db_process_lock_busy",
                    "job_name": self.config.job_name,
                    "db_path": str(self.config.db_path),
                    "lock_path": str(run_lock.path),
                    "lock_owner_pid": run_lock.owner.get("pid"),
                    "lock_owner_acquired_at": run_lock.owner.get("acquired_at"),
                }
                logger.warning(
                    "중복 run 안전 skip - job=%s db=%s owner_pid=%s "
                    "owner_acquired_at=%s",
                    self.config.job_name,
                    self.config.db_path,
                    stats["lock_owner_pid"],
                    stats["lock_owner_acquired_at"],
                )
                return stats

            logger.info(
                "DB process lock 획득 - job=%s db=%s lock=%s pid=%s",
                self.config.job_name,
                self.config.db_path,
                run_lock.path,
                run_lock.owner.get("pid"),
            )
            logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")
            audit = RunAudit.start(self.config, strategy_name="golden-cherry")

            try:
                # A long-lived process may call run() repeatedly; attest only this run.
                self.gamma.sweep_attestations.clear()
                reconciliation = self.clob.reconcile_order_ledger()
                log_reconciliation_continuity(reconciliation, logger=logger)
                stats = self.run_cycle()
                stats["market_sweeps"] = self.gamma.get_sweep_summaries()
                stats["order_reconciliation"] = reconciliation
                audit.succeed(stats)
                logger.info(f"사이클 성공적으로 완료: {stats}")
                return stats
            except Exception as e:
                audit.fail(e)
                logger.exception(f"사이클 실패: {e}")
                raise

    def get_status(self) -> dict:
        """Get current bot status and statistics.

        Returns:
            Status dictionary
        """
        session = self.Session()
        repo = TradeRepository(session)

        try:
            stats = repo.get_stats()
            entry_guard = repo.get_entry_guard(
                self.config.trading.entry_drawdown_floor_usdc,
                simulation_mode=self.config.simulation_mode,
            )
            holdings = repo.get_holding_trades()

            return {
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "lifecycle_mode": self.config.trading.lifecycle_mode,
                "db_path": str(self.config.db_path),
                "statistics": stats,
                "entry_guard": entry_guard,
                "holdings": [
                    {
                        "id": t.id,
                        "condition_id": t.condition_id,
                        "question": t.question[:50] + "..." if len(t.question) > 50 else t.question,
                        "outcome": t.outcome,
                        "buy_price": t.buy_price,
                        "buy_amount": t.buy_amount,
                        "buy_timestamp": t.buy_timestamp.isoformat() if t.buy_timestamp else None,
                        "entry_reason": t.entry_reason,
                        "max_price": t.max_price,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "buy_threshold": self.config.trading.buy_threshold,
                    "sell_threshold": self.config.trading.sell_threshold,
                    "buy_amount_usdc": self.config.trading.buy_amount_usdc,
                    "max_buy_amount_usdc": self.config.trading.max_buy_amount_usdc,
                    "min_liquidity": self.config.trading.min_liquidity,
                    "effective_min_liquidity": self.config.trading.effective_min_liquidity,
                    "max_order_liquidity_ratio": self.config.trading.max_order_liquidity_ratio,
                    "max_positions": self.config.trading.max_positions,
                    "max_open_notional_usdc": self.config.trading.max_open_notional_usdc,
                    "max_new_positions_per_cycle": self.config.trading.max_new_positions_per_cycle,
                    "entry_drawdown_floor_usdc": self.config.trading.entry_drawdown_floor_usdc,
                    "take_profit_percent": self.config.trading.take_profit_percent,
                    "stop_loss_percent": self.config.trading.stop_loss_percent,
                    "trailing_stop_enabled": self.config.trading.trailing_stop.enabled,
                    "trailing_stop_percent": self.config.trading.trailing_stop.percent,
                    "time_based_enabled": self.config.trading.time_based.enabled,
                    "entry_hours_max": self.config.trading.time_based.entry_hours_max,
                    "entry_hours_min": self.config.trading.time_based.entry_hours_min,
                    "exit_hours": self.config.trading.time_based.exit_hours,
                    "game_start_filter_enabled": self.config.trading.game_start.enabled,
                    "allow_in_play": self.config.trading.game_start.allow_in_play,
                    "reject_sports_without_game_start": self.config.trading.game_start.reject_sports_without_game_start,
                    "lifecycle_mode": self.config.trading.lifecycle_mode,
                },
            }
        finally:
            session.close()
