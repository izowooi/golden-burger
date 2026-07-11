"""Main bot orchestrator with Conviction Ladder strategy."""
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
    """Polymarket automated trading bot with Conviction Ladder strategy.

    Orchestrates the trading cycle:
    0. 스캔 대상 시장 스냅샷 저장 (모멘텀 게이트 데이터)
    1. HOLDING 포지션 청산 체크 (EXPIRED 처리 포함)
    2. 전략 스캔 → 후보 목록
    3. 후보별 재진입 체크 → execute_buy
    4. 오래된 스냅샷 정리

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
            strategy_name="golden-date",
        )
        self.history = HistoryClient() if config.trading.history_backfill else None

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"YES-Only: {config.trading.yes_only_mode}, "
            f"History Backfill: {config.trading.history_backfill}"
        )

    def _log_strategy_config(self):
        """사이클 시작 시 전략 설정 로깅."""
        trading = self.config.trading
        ladder = trading.ladder
        gate = trading.momentum_gate

        logger.info(
            f"Conviction Ladder 전략 - "
            f"진입: {ladder.entry_hours_min}h < 해결시간 <= {ladder.h3}h (사다리 3단), "
            f"모멘텀 게이트: 최근 {gate.lookback_hours}h favorite 변화 >= {gate.min_change:+.3f}"
        )
        for band_no, (max_hours, band_min, band_max) in enumerate(ladder.rungs(), start=1):
            logger.info(
                f"  사다리 {band_no}단: 해결까지 ~{max_hours:.0f}h → 확률 [{band_min:.2f}, {band_max:.2f}]"
            )
        logger.info(
            f"손익 설정 - 손절: {trading.stop_loss_percent:.0%}, "
            f"익절: {trading.take_profit_percent:.0%} (목표가 0.99 캡), "
            f"트레일링: {trading.trailing_stop.percent:.0%} "
            f"(enabled={trading.trailing_stop.enabled}), "
            f"시간 청산: 해결 {trading.exit_hours}h 전, "
            f"재진입 쿨다운: {trading.reentry_cooldown_hours:.0f}h"
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
            repo, self.clob, self.config.trading,
            simulation_mode=self.config.simulation_mode,
        )

        stats = {
            "snapshots_saved": 0,
            "checked_holdings": 0,
            "sold": 0,
            "buy_candidates": 0,
            "bought": 0,
        }

        try:
            self._log_strategy_config()

            # Gamma 전체 sweep 1회 - Phase 0과 Phase 2가 공유 (banana의 2회 sweep 낭비 수정)
            markets = self.gamma.get_all_tradable_markets(
                min_liquidity=self.config.trading.min_liquidity
            )

            # Phase 0: Save market snapshots (모멘텀 게이트 데이터 축적)
            logger.info("=== Phase 0: 마켓 스냅샷 저장 ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)

            # Phase 1: Check and sell holdings (EXPIRED 처리 포함)
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

            # Phase 3: Execute buys (재진입 쿨다운 체크 포함)
            logger.info("=== Phase 3: 매수 실행 ===")
            for candidate in candidates:
                can_enter, reason = repo.can_reenter(
                    candidate["condition_id"],
                    self.config.trading.reentry_cooldown_hours,
                )
                if not can_enter:
                    logger.info(
                        f"재진입 조건 미충족 skip: {candidate['condition_id']} ({reason})"
                    )
                    continue

                if trader.execute_buy(candidate):
                    stats["bought"] += 1

            # Phase 4: Cleanup old snapshots (retention: lookback의 3배, 최소 7일)
            logger.info("=== Phase 4: 오래된 스냅샷 정리 ===")
            retention_days = max(
                MIN_SNAPSHOT_RETENTION_DAYS,
                math.ceil(self.config.trading.momentum_gate.lookback_hours * 3 / 24),
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
        audit = RunAudit.start(self.config, strategy_name="golden-date")

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
            trading = self.config.trading

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
                        "max_price": t.max_price,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "buy_amount_usdc": trading.buy_amount_usdc,
                    "min_liquidity": trading.min_liquidity,
                    "min_volume_24h": trading.min_volume_24h,
                    "max_positions": trading.max_positions,
                    "take_profit_percent": trading.take_profit_percent,
                    "stop_loss_percent": trading.stop_loss_percent,
                    "trailing_stop_enabled": trading.trailing_stop.enabled,
                    "trailing_stop_percent": trading.trailing_stop.percent,
                    "entry_hours_min": trading.ladder.entry_hours_min,
                    "ladder_rungs": trading.ladder.rungs(),
                    "momentum_lookback_hours": trading.momentum_gate.lookback_hours,
                    "momentum_min_change": trading.momentum_gate.min_change,
                    "exit_hours": trading.exit_hours,
                    "reentry_cooldown_hours": trading.reentry_cooldown_hours,
                    "history_backfill": trading.history_backfill,
                    "yes_only_mode": trading.yes_only_mode,
                },
            }
        finally:
            session.close()
