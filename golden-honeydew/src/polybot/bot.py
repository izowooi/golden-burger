"""Main bot orchestrator with the Night Watch strategy."""
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

# 스냅샷 retention: 전략 lookback의 3배, 최소 60일 (§5 Phase 4).
# 60일인 이유: 이 DB는 월간 회고(docs/retro/)의 보조 가격 아카이브를 겸한다
# (주 아카이브는 nectarine). TP/SL 반사실 재생에 한 달치 시계열이 필요하다.
MIN_RETENTION_DAYS = 60


class PolymarketBot:
    """Polymarket automated trading bot with the Night Watch strategy.

    Orchestrates the trading cycle:
    0. Save market snapshots (median 계산용, Gamma sweep은 사이클당 1회)
    1. Check existing positions for sell conditions (EXPIRED 처리 포함)
    2. Scan markets for buy candidates (한산 시간대 + dislocation)
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
        self.Session = init_database(
            str(config.db_path),
            SQLiteMaintenanceRequirements(
                full_cadence_hours=float(
                    config.trading.signal.median_lookback_hours
                )
            ),
        )

        # Initialize API clients
        self.gamma = GammaClient()
        self.clob = ClobClientWrapper(
            config.api,
            config.simulation_mode,
            audit_db_path=config.db_path,
            strategy_name="golden-honeydew",
        )
        self.history = HistoryClient()

        logger.info(
            f"Bot 초기화 완료 - Job: {config.job_name}, "
            f"Simulation: {config.simulation_mode}, "
            f"Lifecycle: {config.trading.lifecycle_mode}, "
            f"Quiet hours (UTC): {config.trading.quiet.hours_utc}, "
            f"주말 포함: {config.trading.quiet.weekends}"
        )

    def _retention_days(self) -> int:
        """스냅샷 보관 일수: lookback * 3 (최소 60일)."""
        lookback_days = self.config.trading.signal.median_lookback_hours / 24
        return max(MIN_RETENTION_DAYS, math.ceil(lookback_days * 3))

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
            trading = self.config.trading
            logger.info(
                f"Night Watch 전략 - "
                f"quiet: UTC {trading.quiet.hours_utc} (주말: {trading.quiet.weekends}), "
                f"편차: >= {trading.signal.dev_min:.2f} (median {trading.signal.median_lookback_hours}h), "
                f"매수가: {trading.signal.entry_prob_min:.0%} ~ {trading.signal.entry_prob_max:.0%}"
            )
            logger.info(
                f"손익 설정 - 손절: {trading.stop_loss_percent:.0%}, "
                f"익절: {trading.take_profit_percent:.0%}, "
                f"최대 보유: {trading.time_based.max_holding_hours}h, "
                f"해결 {trading.time_based.exit_hours}h 전 청산"
            )

            # Gamma 전체 sweep은 1회만 수행하고 Phase 0과 2가 결과를 공유 (§5)
            markets = scanner.fetch_markets()

            # Phase 0: Save market snapshots (median 계산용)
            logger.info("=== Phase 0: 마켓 스냅샷 저장 ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)

            lifecycle_mode = self.config.trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning(
                    "=== Phase 1 건너뜀: archive_only 모드에서는 주문을 생성하지 않습니다 ==="
                )
            else:
                # Phase 1: Check and sell holdings (EXPIRED 처리 포함)
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
                    can_enter, reason = repo.can_reenter(
                        candidate["condition_id"],
                        self.config.trading.reentry_cooldown_hours,
                    )
                    if not can_enter:
                        logger.info(
                            f"재진입 불가 skip: {candidate['condition_id']} "
                            f"(사유: {reason})"
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
            repo.cleanup_old_snapshots(days=self._retention_days())

            # Log statistics
            db_stats = repo.get_stats()
            logger.info(f"=== 사이클 완료 ===")
            logger.info(f"스냅샷 저장: {stats['snapshots_saved']}개")
            logger.info(f"보유 포지션 확인: {stats['checked_holdings']}개")
            logger.info(f"매도: {stats['sold']}건")
            logger.info(f"매수 후보: {stats['buy_candidates']}개")
            logger.info(f"매수: {stats['bought']}건")
            logger.info(f"총 포지션: {db_stats['holding']}개")
            logger.info(f"EXPIRED (수동 redeem 필요): {db_stats['expired']}개")
            logger.info(f"총 P&L: ${db_stats['total_pnl']:.4f}")

            return stats

        finally:
            session.close()

    def run(self):
        """Run a single trading cycle (for Jenkins)."""
        logger.info(f"트레이딩 사이클 시작 - {self.config.job_name}")
        audit = RunAudit.start(self.config, strategy_name="golden-honeydew")

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
                        "deviation_at_buy": t.deviation_at_buy,
                        "median_at_buy": t.median_at_buy,
                        "market_end_date": t.market_end_date.isoformat() if t.market_end_date else None,
                    }
                    for t in holdings
                ],
                "config": {
                    "lifecycle_mode": self.config.trading.lifecycle_mode,
                    "quiet_hours_utc": self.config.trading.quiet.hours_utc,
                    "quiet_weekends": self.config.trading.quiet.weekends,
                    "median_lookback_hours": self.config.trading.signal.median_lookback_hours,
                    "dev_min": self.config.trading.signal.dev_min,
                    "vol_spike_block": self.config.trading.signal.vol_spike_block,
                    "entry_prob_min": self.config.trading.signal.entry_prob_min,
                    "entry_prob_max": self.config.trading.signal.entry_prob_max,
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
                    "max_holding_hours": self.config.trading.time_based.max_holding_hours,
                },
            }
        finally:
            session.close()
