"""Main bot orchestrator with Fear Spike Fade strategy."""
import logging
from polybot_observability import RunAudit, log_reconciliation_continuity
from polybot_observability import SQLiteMaintenanceRequirements
from .config import BotConfig
from .api.gamma_client import GammaClient
from .api.clob_client import ClobClientWrapper
from .api.history_client import HistoryClient
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader
from .db.models import init_database
from .db.repository import TradeRepository

logger = logging.getLogger(__name__)

# 스냅샷 보존 일수: 전략 lookback(7d)의 3배, 최소 7일
SNAPSHOT_RETENTION_DAYS = 21


class PolymarketBot:
    """Polymarket automated trading bot with Fear Spike Fade strategy.

    Orchestrates the trading cycle:
    0. Save market snapshots (YES 가격 기준)
    1. Check existing positions for sell conditions (EXPIRED 처리 포함)
    2. Scan markets for buy candidates (항상 NO 토큰)
    3. Execute buys (재진입 쿨다운 체크 포함)
    4. Cleanup old snapshots
    """

    def __init__(self, config: BotConfig):
        """Initialize bot with configuration.

        Args:
            config: Bot configuration
        """
        self.config = config

        # Initialize database
        base_window_days = float(config.trading.strategy.base_window_days)
        self.Session = init_database(
            str(config.db_path),
            SQLiteMaintenanceRequirements(
                full_cadence_hours=base_window_days * 24.0,
                retention_days=base_window_days,
            ),
        )

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(
            config.api,
            config.simulation_mode,
            audit_db_path=config.db_path,
            strategy_name="golden-orange",
        )
        self.history = HistoryClient()

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"Lifecycle: {config.trading.lifecycle_mode}, "
            f"Backfill: {config.trading.history_backfill}"
        )

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
            repo,
            self.clob,
            self.config.trading,
            mode="sim" if self.config.simulation_mode else "live",
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
            strategy = self.config.trading.strategy
            time_cfg = self.config.trading.time_based
            logger.info(
                f"Fear Spike Fade 전략 - "
                f"base(7d 중앙값) <= {strategy.base_max:.0%} tail 시장의 공포 스파이크"
                f"(+{strategy.jump_min:.2f}, YES <= {strategy.yes_max:.0%})를 NO 매수로 페이드, "
                f"스파이크 {strategy.spike_wait_minutes:.0f}분 경과 + "
                f"{strategy.stall_window_minutes:.0f}분 스톨 + volume x{strategy.vol_mult_min:.1f} 확인, "
                f"진입: 해결까지 >= {time_cfg.entry_hours_min}h"
            )
            logger.info(
                f"청산 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                f"retrace 익절: YES <= base + {strategy.retrace_ratio:.0%}x(peak-base), "
                f"보조 익절: {self.config.trading.take_profit_percent:+.0%} (목표가 0.99 캡), "
                f"max_holding: {strategy.max_holding_hours:.0f}h, "
                f"time exit: 해결 {time_cfg.exit_hours}h 전, "
                f"재진입 쿨다운: {self.config.trading.reentry_cooldown_hours}h"
            )

            # Gamma 전체 sweep 1회 - Phase 0과 2가 공유
            markets = scanner.fetch_markets()

            # Phase 0: Save market snapshots
            logger.info("=== Phase 0: 마켓 스냅샷 저장 ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)

            lifecycle_mode = self.config.trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning(
                    "=== Phase 1 건너뜀: archive_only 모드에서는 주문을 생성하지 않습니다 ==="
                )
            else:
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

            if lifecycle_mode == "active":
                # Phase 2: Scan for buy candidates
                logger.info("=== Phase 2: 매수 후보 스캔 ===")
                candidates = scanner.scan_buy_candidates(markets)
                stats["buy_candidates"] = len(candidates)

                # Phase 3: Execute buys (재진입 정책: HOLDING 또는 쿨다운이면 skip)
                logger.info("=== Phase 3: 매수 실행 ===")
                for candidate in candidates:
                    condition_id = candidate["condition_id"]

                    if repo.has_holding(condition_id):
                        logger.info(f"이미 보유 중인 시장 skip: {condition_id}")
                        continue

                    if repo.is_in_reentry_cooldown(
                        condition_id, self.config.trading.reentry_cooldown_hours
                    ):
                        logger.info(f"재진입 쿨다운 중 skip: {condition_id}")
                        continue

                    if trader.execute_buy(candidate):
                        stats["bought"] += 1
            else:
                logger.warning(
                    "=== Phase 2/3 건너뜀: "
                    f"{lifecycle_mode} 모드에서 신규 진입이 차단됩니다 ==="
                )

            # Phase 4: Cleanup old snapshots
            logger.info("=== Phase 4: 오래된 스냅샷 정리 ===")
            repo.cleanup_old_snapshots(days=SNAPSHOT_RETENTION_DAYS)

            # Log statistics
            db_stats = repo.get_stats()
            logger.info(f"=== 사이클 완료 ===")
            logger.info(f"스냅샷 저장: {stats['snapshots_saved']}개")
            logger.info(f"보유 포지션 확인: {stats['checked_holdings']}개")
            logger.info(f"매도: {stats['sold']}건")
            logger.info(f"매수 후보: {stats['buy_candidates']}개")
            logger.info(f"매수: {stats['bought']}건")
            logger.info(f"총 포지션: {db_stats['holding']}개")
            logger.info(f"총 P&L: ${db_stats['total_pnl']:.4f}")
            if db_stats["expired"] > 0:
                logger.warning(f"수동 redeem 필요(EXPIRED): {db_stats['expired']}건")

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")
        audit = RunAudit.start(self.config, strategy_name="golden-orange")

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
                        "yes_price_at_buy": t.yes_price_at_buy,
                        "base_price_at_buy": t.base_price_at_buy,
                        "spike_peak_at_buy": t.spike_peak_at_buy,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "lifecycle_mode": self.config.trading.lifecycle_mode,
                    "base_window_days": self.config.trading.strategy.base_window_days,
                    "base_exclude_recent_hours": self.config.trading.strategy.base_exclude_recent_hours,
                    "base_max": self.config.trading.strategy.base_max,
                    "jump_min": self.config.trading.strategy.jump_min,
                    "yes_max": self.config.trading.strategy.yes_max,
                    "spike_wait_minutes": self.config.trading.strategy.spike_wait_minutes,
                    "stall_window_minutes": self.config.trading.strategy.stall_window_minutes,
                    "vol_mult_min": self.config.trading.strategy.vol_mult_min,
                    "retrace_ratio": self.config.trading.strategy.retrace_ratio,
                    "max_holding_hours": self.config.trading.strategy.max_holding_hours,
                    "buy_amount_usdc": self.config.trading.buy_amount_usdc,
                    "min_liquidity": self.config.trading.min_liquidity,
                    "min_volume_24h": self.config.trading.min_volume_24h,
                    "max_positions": self.config.trading.max_positions,
                    "take_profit_percent": self.config.trading.take_profit_percent,
                    "stop_loss_percent": self.config.trading.stop_loss_percent,
                    "reentry_cooldown_hours": self.config.trading.reentry_cooldown_hours,
                    "history_backfill": self.config.trading.history_backfill,
                    "entry_hours_min": self.config.trading.time_based.entry_hours_min,
                    "exit_hours": self.config.trading.time_based.exit_hours,
                },
            }
        finally:
            session.close()
