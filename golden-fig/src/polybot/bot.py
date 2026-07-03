"""Main bot orchestrator with Hope Crusher strategy."""
import logging
from .config import BotConfig
from .api.gamma_client import GammaClient
from .api.clob_client import ClobClientWrapper
from .api.history_client import HistoryClient
from .strategy.scanner import MarketScanner
from .strategy.trader import Trader
from .db.models import init_database
from .db.repository import TradeRepository

logger = logging.getLogger(__name__)

# 스냅샷 보존 일수: 전략 lookback(24h)의 3배 이상, 최소 7일
SNAPSHOT_RETENTION_DAYS = 7


class PolymarketBot:
    """Polymarket automated trading bot with Hope Crusher strategy.

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
        self.Session = init_database(str(config.db_path))

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(config.api, config.simulation_mode)
        self.history = HistoryClient()

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
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
        trader = Trader(repo, self.clob, self.config.trading)

        stats = {
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
                f"Hope Crusher 전략 - "
                f"YES {strategy.yes_min:.0%}~{strategy.yes_max:.0%} 롱샷의 NO 매수, "
                f"진입: {time_cfg.entry_hours_min}h <= 해결시간 <= {time_cfg.entry_hours_max}h, "
                f"청산: 해결 {time_cfg.exit_hours}h 전"
            )
            logger.info(
                f"손익 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                f"익절: {self.config.trading.take_profit_percent:.0%} (목표가 0.99 캡), "
                f"재진입 쿨다운: {self.config.trading.reentry_cooldown_hours}h"
            )

            # Gamma 전체 sweep 1회 - Phase 0과 2가 공유
            markets = scanner.fetch_markets()

            # Phase 0: Save market snapshots
            logger.info("=== Phase 0: 마켓 스냅샷 저장 ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)

            # Phase 1: Check and sell holdings
            logger.info("=== Phase 1: 보유 포지션 매도 확인 ===")
            holdings = repo.get_holding_trades()
            stats["checked_holdings"] = len(holdings)

            for trade in holdings:
                if trader.execute_sell(trade):
                    stats["sold"] += 1
                    updated_trade = repo.get_by_id(trade.id)
                    if updated_trade:
                        repo.append_trade_to_csv(updated_trade, self.config.db_path.parent)

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

        try:
            stats = self.run_cycle()
            logger.info(f"사이클 성공적으로 완료: {stats}")
        except Exception as e:
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
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "yes_min": self.config.trading.strategy.yes_min,
                    "yes_max": self.config.trading.strategy.yes_max,
                    "yes_rise_block_24h": self.config.trading.strategy.yes_rise_block_24h,
                    "yes_spike_block_6h": self.config.trading.strategy.yes_spike_block_6h,
                    "buy_amount_usdc": self.config.trading.buy_amount_usdc,
                    "min_liquidity": self.config.trading.min_liquidity,
                    "min_volume_24h": self.config.trading.min_volume_24h,
                    "max_positions": self.config.trading.max_positions,
                    "take_profit_percent": self.config.trading.take_profit_percent,
                    "stop_loss_percent": self.config.trading.stop_loss_percent,
                    "reentry_cooldown_hours": self.config.trading.reentry_cooldown_hours,
                    "history_backfill": self.config.trading.history_backfill,
                    "entry_hours_min": self.config.trading.time_based.entry_hours_min,
                    "entry_hours_max": self.config.trading.time_based.entry_hours_max,
                    "exit_hours": self.config.trading.time_based.exit_hours,
                },
            }
        finally:
            session.close()
