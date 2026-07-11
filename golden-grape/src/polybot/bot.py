"""Main bot orchestrator with the Cascade Rider strategy."""
import logging
from polybot_observability import RunAudit
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

# 스냅샷 보존: 전략 lookback의 3배, 최소 7일
MIN_SNAPSHOT_RETENTION_DAYS = 7


class PolymarketBot:
    """Polymarket automated trading bot with the Cascade Rider strategy.

    Orchestrates the trading cycle:
    0. Save market snapshots (드리프트 계산용, YES 가격 기준)
    1. Check existing positions for sell conditions (EXPIRED 처리 포함)
    2. Scan markets for buy candidates (드리프트 + 일관성 + 거래량 가속)
    3. Execute buys (재진입 쿨다운 체크)
    4. Cleanup old snapshots

    Gamma 전체 sweep은 1회만 수행하고 Phase 0과 2가 결과를 공유한다.
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
            strategy_name="golden-grape",
        )
        self.history = HistoryClient() if config.trading.history_backfill else None

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

        scanner = MarketScanner(self.gamma, self.config.trading, repo, self.history)
        trader = Trader(
            repo, self.clob, self.config.trading,
            mode="sim" if self.config.simulation_mode else "live",
        )

        stats = {
            "snapshots_saved": 0,
            "checked_holdings": 0,
            "sold": 0,
            "buy_candidates": 0,
            "bought": 0,
        }

        try:
            # Log strategy configuration at cycle start
            cascade = self.config.trading.cascade
            logger.info(
                f"Cascade Rider 전략 - "
                f"가격: {cascade.prob_min:.0%}~{cascade.prob_max:.0%}, "
                f"{cascade.drift_lookback_hours}h 드리프트: "
                f"{cascade.drift_min:+.0%}~{cascade.drift_max:+.0%}, "
                f"일관성 >= {cascade.consistency_min:.0%}, "
                f"거래량 가속 >= {cascade.vol_accel_min:.1f}x, "
                f"진입: 해결 {self.config.trading.entry_hours_min}h 이상 전"
            )
            logger.info(
                f"손익 설정 - 손절: {self.config.trading.stop_loss_percent:.0%}, "
                f"익절: {self.config.trading.take_profit_percent:.0%} (0.99 캡), "
                f"드리프트 소멸: {cascade.death_window_hours}h, "
                f"트레일링: {self.config.trading.trailing_stop.percent:.0%}, "
                f"시간 청산: 해결 {self.config.trading.exit_hours}h 전"
            )

            # Gamma 전체 sweep 1회 (Phase 0과 2가 공유)
            markets = self.gamma.get_all_tradable_markets(
                min_liquidity=self.config.trading.min_liquidity
            )

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

            # Phase 3: Execute buys (재진입 쿨다운 체크)
            logger.info("=== Phase 3: 매수 실행 ===")
            for candidate in candidates:
                block = repo.get_reentry_block(
                    candidate["condition_id"],
                    self.config.trading.reentry_cooldown_hours,
                )
                if block:
                    logger.info(
                        f"재진입 차단({block}) skip: {candidate['condition_id']}"
                    )
                    continue

                if trader.execute_buy(candidate):
                    stats["bought"] += 1

            # Phase 4: Cleanup old snapshots
            logger.info("=== Phase 4: 오래된 스냅샷 정리 ===")
            retention_days = max(
                MIN_SNAPSHOT_RETENTION_DAYS,
                math.ceil(cascade.drift_lookback_hours * 3 / 24),
            )
            repo.cleanup_old_snapshots(days=retention_days)

            # Log statistics
            db_stats = repo.get_stats()
            logger.info(f"=== 사이클 완료 ===")
            logger.info(f"스냅샷 저장: {stats['snapshots_saved']}개")
            logger.info(f"보유 포지션 확인: {stats['checked_holdings']}개")
            logger.info(f"매도: {stats['sold']}건")
            logger.info(f"매수 후보: {stats['buy_candidates']}개")
            logger.info(f"매수: {stats['bought']}건")
            logger.info(f"총 포지션: {db_stats['holding']}개")
            logger.info(f"EXPIRED(수동 redeem 필요): {db_stats['expired']}개")
            logger.info(f"총 P&L: ${db_stats['total_pnl']:.4f}")

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")
        audit = RunAudit.start(self.config, strategy_name="golden-grape")

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
                        "drift_at_buy": t.drift_at_buy,
                        "consistency_at_buy": t.consistency_at_buy,
                        "vol_accel_at_buy": t.vol_accel_at_buy,
                        "max_price": t.max_price,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "buy_amount_usdc": self.config.trading.buy_amount_usdc,
                    "min_liquidity": self.config.trading.min_liquidity,
                    "min_volume_24h": self.config.trading.min_volume_24h,
                    "max_positions": self.config.trading.max_positions,
                    "take_profit_percent": self.config.trading.take_profit_percent,
                    "stop_loss_percent": self.config.trading.stop_loss_percent,
                    "trailing_stop_enabled": self.config.trading.trailing_stop.enabled,
                    "trailing_stop_percent": self.config.trading.trailing_stop.percent,
                    "entry_hours_min": self.config.trading.entry_hours_min,
                    "exit_hours": self.config.trading.exit_hours,
                    "reentry_cooldown_hours": self.config.trading.reentry_cooldown_hours,
                    "history_backfill": self.config.trading.history_backfill,
                    "prob_min": self.config.trading.cascade.prob_min,
                    "prob_max": self.config.trading.cascade.prob_max,
                    "drift_lookback_hours": self.config.trading.cascade.drift_lookback_hours,
                    "drift_min": self.config.trading.cascade.drift_min,
                    "drift_max": self.config.trading.cascade.drift_max,
                    "bucket_hours": self.config.trading.cascade.bucket_hours,
                    "consistency_min": self.config.trading.cascade.consistency_min,
                    "vol_accel_min": self.config.trading.cascade.vol_accel_min,
                    "death_window_hours": self.config.trading.cascade.death_window_hours,
                },
            }
        finally:
            session.close()
