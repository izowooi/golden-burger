"""Main bot orchestrator with Shock Follow strategy."""
import logging
from polybot_observability import RunAudit, log_reconciliation_continuity
from polybot_observability import SQLiteMaintenanceRequirements
import math
from .config import BotConfig
from .api.gamma_client import GammaClient
from .api.clob_client import ClobClientWrapper
from .api.history_client import HistoryClient
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader
from .db.models import init_database
from .db.repository import TradeRepository

logger = logging.getLogger(__name__)

# 스냅샷 보존 최소 일수 (retention = max(7일, 전략 lookback x 3))
MIN_SNAPSHOT_RETENTION_DAYS = 7


class PolymarketBot:
    """Polymarket automated trading bot with Shock Follow strategy.

    Orchestrates the trading cycle:
    0. Save market snapshots (jump/volume 판정용, YES 가격 기준)
    1. Check existing positions for sell conditions (EXPIRED 처리 포함)
    2. Scan markets for buy candidates (Phase 0과 gamma sweep 결과 공유)
    3. Execute buys for qualifying candidates (재진입 쿨다운 체크)
    4. Cleanup old snapshots
    """

    def __init__(self, config: BotConfig):
        """Initialize bot with configuration.

        Args:
            config: Bot configuration
        """
        self.config = config

        # Initialize database
        shock = config.trading.shock
        self.Session = init_database(
            str(config.db_path),
            SQLiteMaintenanceRequirements(
                full_cadence_hours=max(
                    24.0,
                    float(shock.jump_window_hours),
                    float(shock.death_window_hours),
                )
            ),
        )

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(
            config.api,
            config.simulation_mode,
            audit_db_path=config.db_path,
            strategy_name="golden-lime",
        )
        self.history = HistoryClient() if config.trading.history_backfill else None

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"Lifecycle: {config.trading.lifecycle_mode}, "
            f"Backfill: {config.trading.history_backfill}"
        )

    def _snapshot_retention_days(self) -> int:
        """스냅샷 보존 일수: 전략 lookback(24h volume 윈도우)의 3배, 최소 7일."""
        lookback_hours = max(self.config.trading.shock.jump_window_hours, 24.0)
        return max(MIN_SNAPSHOT_RETENTION_DAYS, math.ceil(lookback_hours * 3 / 24))

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

        scanner = MarketScanner(self.gamma, self.config.trading, repo, self.history)
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
            shock = self.config.trading.shock
            logger.info(
                f"Shock Follow 전략 - "
                f"점프: {shock.jump_window_hours:.0f}h 내 최저가 대비 >= +{shock.jump_min:.2f}, "
                f"기준가 [{shock.base_min:.2f}, {shock.base_max:.2f}], 현재가 <= {shock.current_max:.2f}, "
                f"고점 유지 {shock.hold_window_minutes:.0f}분/되돌림 <= {shock.max_pullback:.2f}, "
                f"거래량 x{shock.vol_mult_min:.1f}"
            )
            logger.info(
                f"손익 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                f"익절: {self.config.trading.take_profit_percent:.0%} (0.99 캡), "
                f"트레일링: {self.config.trading.trailing_stop.percent:.0%}, "
                f"모멘텀 사망: {shock.death_window_hours:.0f}h, "
                f"시간 청산: 해결 {self.config.trading.time_based.exit_hours}h 전"
            )

            # Gamma 전체 sweep은 1회만 수행하고 Phase 0과 2가 결과를 공유한다
            markets = self.gamma.get_all_tradable_markets(
                min_liquidity=self.config.trading.min_liquidity
            )

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

                # Phase 3: Execute buys (재진입 쿨다운 체크)
                logger.info("=== Phase 3: 매수 실행 ===")
                for candidate in candidates:
                    can_enter, reason = repo.can_enter(
                        candidate["condition_id"],
                        self.config.trading.reentry_cooldown_hours,
                    )
                    if not can_enter:
                        logger.info(
                            f"재진입 불가 skip: {candidate['condition_id']} ({reason})"
                        )
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
                logger.warning(f"수동 redeem 필요 (EXPIRED): {db_stats['expired']}건")
            logger.info(f"총 P&L: ${db_stats['total_pnl']:.4f}")

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")
        audit = RunAudit.start(self.config, strategy_name="golden-lime")

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
            shock = self.config.trading.shock

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
                        "jump_size_at_buy": t.jump_size_at_buy,
                        "max_price": t.max_price,
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
                    "trailing_stop_enabled": self.config.trading.trailing_stop.enabled,
                    "trailing_stop_percent": self.config.trading.trailing_stop.percent,
                    "entry_hours_min": self.config.trading.time_based.entry_hours_min,
                    "exit_hours": self.config.trading.time_based.exit_hours,
                    "reentry_cooldown_hours": self.config.trading.reentry_cooldown_hours,
                    "history_backfill": self.config.trading.history_backfill,
                    "jump_window_hours": shock.jump_window_hours,
                    "jump_min": shock.jump_min,
                    "base_min": shock.base_min,
                    "base_max": shock.base_max,
                    "current_max": shock.current_max,
                    "hold_window_minutes": shock.hold_window_minutes,
                    "max_pullback": shock.max_pullback,
                    "vol_mult_min": shock.vol_mult_min,
                    "death_window_hours": shock.death_window_hours,
                },
            }
        finally:
            session.close()
