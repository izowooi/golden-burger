"""Final Five trading-cycle orchestration."""

from __future__ import annotations

import logging

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
            strategy_name="golden-papaya",
        )
        logger.info(
            "Final Five bot 초기화 - job=%s simulation=%s lifecycle=%s yes_only=%s",
            config.job_name,
            config.simulation_mode,
            config.trading.lifecycle_mode,
            config.trading.yes_only_mode,
        )

    def _log_strategy_config(self) -> None:
        trading = self.config.trading
        entry = trading.entry
        archive = trading.archive
        logger.info(
            "Final Five 전략 - prior YES < %.2f -> current YES [%.2f, %.2f], "
            "hours [%.1f, %.1f], absolute stop YES <= %.2f",
            entry.prob_min,
            entry.prob_min,
            entry.prob_max,
            entry.hours_min,
            entry.hours_max,
            entry.stop_price,
        )
        logger.info(
            "실행 - fresh ask <= %.2f BUY, midpoint stop trigger + fresh bid SELL; "
            "TP/trailing/time-exit 없음; $%.2f, positions=%s, event=%s",
            entry.prob_max,
            trading.buy_amount_usdc,
            trading.max_positions,
            trading.max_event_positions,
        )
        logger.info(
            "research archive - strict binary YES >= %.2f, hours <= %.0f, retention=%sd",
            archive.prob_min,
            archive.hours_max,
            archive.retention_days,
        )
        logger.info(
            "crossing lineage - current-run snapshot required, max prior gap=%.1fmin",
            trading.max_snapshot_gap_minutes,
        )

    def run_cycle(self) -> dict:
        session = self.Session()
        repo = TradeRepository(session)
        scanner = MarketScanner(self.gamma, self.config.trading, repo)
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

            logger.info("=== Phase 0: Final Five research archive ===")
            stats["snapshots_saved"] = scanner.save_market_snapshots(markets)
            lifecycle_mode = self.config.trading.lifecycle_mode

            if lifecycle_mode == "archive_only":
                logger.warning("archive_only: 주문 및 포지션 mutation을 건너뜁니다")
            else:
                logger.info("=== Phase 1: absolute-stop / resolution evidence 확인 ===")
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
                logger.info("=== Phase 2: threshold-crossing scan ===")
                candidates = scanner.scan_buy_candidates(markets)
                stats["buy_candidates"] = len(candidates)
                logger.info("=== Phase 3: fresh-ask BUY execution ===")
                for candidate in candidates:
                    if trader.execute_buy(candidate) is not None:
                        stats["bought"] += 1
            else:
                logger.warning("%s: 신규 진입을 건너뜁니다", lifecycle_mode)

            logger.info("=== Phase 4: archive retention cleanup ===")
            repo.cleanup_old_snapshots(
                days=self.config.trading.archive.retention_days
            )
            db_stats = repo.get_stats()
            logger.info(
                "사이클 완료 - snapshots=%s checked=%s stop_sells=%s resolved=%s "
                "candidates=%s buys=%s holding=%s realized_pnl=$%.4f",
                stats["snapshots_saved"],
                stats["checked_holdings"],
                stats["sold"],
                stats["resolved"],
                stats["buy_candidates"],
                stats["bought"],
                db_stats["holding"],
                db_stats["total_pnl"],
            )
            return stats
        finally:
            session.close()

    def run(self) -> None:
        logger.info("트레이딩 사이클 시작 - %s", self.config.job_name)
        audit = RunAudit.start(self.config, strategy_name="golden-papaya")
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
            return {
                "strategy": "Final Five",
                "job_name": self.config.job_name,
                "simulation_mode": self.config.simulation_mode,
                "lifecycle_mode": trading.lifecycle_mode,
                "db_path": str(self.config.db_path),
                "statistics": repo.get_stats(),
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
                    "max_positions": trading.max_positions,
                    "max_event_positions": trading.max_event_positions,
                    "reentry_cooldown_hours": trading.reentry_cooldown_hours,
                    "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
                    "min_order_size": trading.min_order_size,
                    "min_order_buffer_shares": trading.min_order_buffer_shares,
                    "yes_only_mode": trading.yes_only_mode,
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
