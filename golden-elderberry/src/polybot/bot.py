"""Main bot orchestrator with panic fade strategy."""
import logging
from polybot_observability import RunAudit
from .config import BotConfig
from .api.gamma_client import GammaClient
from .api.clob_client import ClobClientWrapper
from .api.history_client import HistoryClient
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader
from .db.models import init_database
from .db.repository import TradeRepository

logger = logging.getLogger(__name__)

# 스냅샷 보존 기간: 전략 lookback의 3배, 최소 7일
SNAPSHOT_RETENTION_MIN_DAYS = 7.0


class PolymarketBot:
    """Polymarket automated trading bot with panic fade strategy.

    Orchestrates the trading cycle:
    0. Save market snapshots (YES 가격 기준)
    1. Check existing positions for sell conditions (EXPIRED 처리 포함)
    2. Scan markets for panic fade candidates
    3. Execute buys for qualifying candidates (재진입 쿨다운 체크)
    4. Cleanup old snapshots

    Gamma 전체 sweep은 사이클당 1회만 수행하고 Phase 0과 2가 결과를 공유한다.
    """

    def __init__(self, config: BotConfig):
        """Initialize bot with configuration.

        Args:
            config: Bot configuration
        """
        self.config = config

        # Initialize database
        self.Session = init_database(str(config.db_path))

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(
            config.api,
            config.simulation_mode,
            audit_db_path=config.db_path,
            strategy_name="golden-elderberry",
        )
        self.history = HistoryClient()

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"Lifecycle: {config.trading.lifecycle_mode}, "
            f"Backfill: {config.trading.history_backfill}"
        )

    def _snapshot_retention_days(self) -> float:
        """스냅샷 보존 일수 = 전략 lookback의 3배, 최소 7일."""
        lookback_days = self.config.trading.strategy.ref_window_hours / 24.0
        return max(lookback_days * 3, SNAPSHOT_RETENTION_MIN_DAYS)

    def run_cycle(self) -> dict:
        """Execute one trading cycle.

        Returns:
            Dictionary with cycle statistics:
            - snapshots_saved: int
            - checked_holdings: int
            - sold: int
            - buy_candidates: int
            - bought: int
        """
        session = self.Session()
        repo = TradeRepository(session)

        scanner = MarketScanner(
            self.gamma, self.config.trading, repo, history_client=self.history
        )
        trader = Trader(
            repo, self.clob, self.config.trading,
            simulation_mode=self.config.simulation_mode,
        )

        stats = {
            "lifecycle_mode": self.config.trading.lifecycle_mode,
            "snapshots_saved": 0,
            "checked_holdings": 0,
            "sold": 0,
            "buy_candidates": 0,
            "bought": 0,
        }

        try:
            # Log strategy configuration at cycle start
            s = self.config.trading.strategy
            logger.info(
                f"Panic Fade 전략 - "
                f"ref: 최근 {s.ref_window_hours:.0f}h(최근 {s.ref_exclude_recent_hours:.0f}h 제외) "
                f"최고가 >= {s.ref_min:.0%}, 낙폭 >= {s.drop_min:.0%}, "
                f"진입 밴드 [{s.current_min:.0%}, {s.current_max:.0%}], "
                f"안정화 {s.stab_window_minutes:.0f}분 std <= {s.stab_max_std}"
            )
            logger.info(
                f"손익 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                f"익절: {self.config.trading.take_profit_percent:.0%} (0.99 캡), "
                f"최대 보유: {s.max_holding_hours:.0f}h, "
                f"time exit: 해결 {self.config.trading.time_based.exit_hours}h 전"
            )

            # Gamma 전체 sweep (1회) - Phase 0과 2가 공유
            markets = scanner.fetch_markets()

            # Phase 0: Save market snapshots
            logger.info("=== Phase 0: 마켓 스냅샷 저장 ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)

            lifecycle_mode = self.config.trading.lifecycle_mode
            if lifecycle_mode != "archive_only":
                # Phase 1: Check and sell holdings
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
            else:
                logger.info("Lifecycle archive_only: Phase 1 매도 확인 생략")

            if lifecycle_mode == "active":
                # Phase 2: Scan for buy candidates
                logger.info("=== Phase 2: 매수 후보 스캔 ===")
                candidates = scanner.scan_buy_candidates(markets)
                stats["buy_candidates"] = len(candidates)

                # Phase 3: Execute buys (재진입 쿨다운 체크)
                logger.info("=== Phase 3: 매수 실행 ===")
                for candidate in candidates:
                    blocked, reason = repo.is_reentry_blocked(
                        candidate["condition_id"],
                        self.config.trading.reentry_cooldown_hours,
                    )
                    if blocked:
                        logger.info(
                            f"재진입 차단 ({reason}) skip: {candidate['condition_id']}"
                        )
                        continue

                    if trader.execute_buy(candidate):
                        stats["bought"] += 1
            else:
                logger.info(
                    f"Lifecycle {lifecycle_mode}: Phase 2 스캔과 Phase 3 매수 생략"
                )

            # Phase 4: Cleanup old snapshots
            logger.info("=== Phase 4: 오래된 스냅샷 정리 ===")
            repo.cleanup_old_snapshots(days=self._snapshot_retention_days())

            # Log statistics
            db_stats = repo.get_stats()
            logger.info(f"=== 사이클 완료 ===")
            logger.info(f"스냅샷 저장: {stats['snapshots_saved']}개")
            logger.info(f"보유 포지션 확인: {stats['checked_holdings']}개")
            logger.info(f"매도: {stats['sold']}건")
            logger.info(f"매수 후보: {stats['buy_candidates']}개")
            logger.info(f"매수: {stats['bought']}건")
            logger.info(f"총 포지션: {db_stats['holding']}개")
            if db_stats["expired"] > 0:
                logger.warning(f"EXPIRED 포지션 {db_stats['expired']}개 - 수동 redeem 필요")
            logger.info(f"총 P&L: ${db_stats['total_pnl']:.4f}")

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")
        audit = RunAudit.start(self.config, strategy_name="golden-elderberry")

        try:
            # A long-lived process may call run() repeatedly; attest only this run.
            self.gamma.sweep_attestations.clear()
            reconciliation = self.clob.reconcile_order_ledger()
            if reconciliation.get("errors", 0):
                raise RuntimeError(
                    "미완료 CLOB 주문 대사에 실패해 새 trading cycle을 중단합니다: "
                    f"{reconciliation['errors']}건"
                )
            stats = self.run_cycle()
            stats["market_sweeps"] = self.gamma.get_sweep_summaries()
            stats["order_reconciliation"] = reconciliation
            audit.succeed(stats)
            logger.info(f"사이클 성공적으로 완료: {stats}")
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
            holdings = repo.get_holding_trades()

            return {
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "lifecycle_mode": self.config.trading.lifecycle_mode,
                "db_path": str(self.config.db_path),
                "statistics": stats,
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
                        "ref_price_at_buy": t.ref_price_at_buy,
                        "drop_at_buy": t.drop_at_buy,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "lifecycle_mode": self.config.trading.lifecycle_mode,
                    "buy_amount_usdc": self.config.trading.buy_amount_usdc,
                    "min_liquidity": self.config.trading.min_liquidity,
                    "min_volume_24h": self.config.trading.min_volume_24h,
                    "max_positions": self.config.trading.max_positions,
                    "take_profit_percent": self.config.trading.take_profit_percent,
                    "stop_loss_percent": self.config.trading.stop_loss_percent,
                    "reentry_cooldown_hours": self.config.trading.reentry_cooldown_hours,
                    "history_backfill": self.config.trading.history_backfill,
                    "ref_window_hours": self.config.trading.strategy.ref_window_hours,
                    "ref_exclude_recent_hours": self.config.trading.strategy.ref_exclude_recent_hours,
                    "ref_min": self.config.trading.strategy.ref_min,
                    "drop_min": self.config.trading.strategy.drop_min,
                    "current_min": self.config.trading.strategy.current_min,
                    "current_max": self.config.trading.strategy.current_max,
                    "stab_window_minutes": self.config.trading.strategy.stab_window_minutes,
                    "stab_max_std": self.config.trading.strategy.stab_max_std,
                    "max_holding_hours": self.config.trading.strategy.max_holding_hours,
                    "entry_hours_min": self.config.trading.time_based.entry_hours_min,
                    "exit_hours": self.config.trading.time_based.exit_hours,
                },
            }
        finally:
            session.close()
