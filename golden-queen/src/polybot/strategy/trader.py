"""Order execution and evidence-safe settlement handling for Crown Momentum."""

from __future__ import annotations

from datetime import datetime
import logging
import math
import re
from typing import Optional

from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)

from ..api.clob_client import ClobClientWrapper
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.models import STRATEGY_NAME, TradeStatus
from ..db.repository import ExactFillEvidence, TradeRepository
from .filters import get_proven_resolution
from .signals import evaluate_entry, evaluate_exit
from .timing import evaluate_entry_clock


logger = logging.getLogger(__name__)

_ZERO_BALANCE_PATTERN = re.compile(
    r"not enough balance.*balance:\s*0(?:\D|$)", re.IGNORECASE
)
_BALANCE_ALLOWANCE_PATTERN = re.compile(
    r"not enough balance\s*/\s*allowance", re.IGNORECASE
)
_AVAILABLE_BALANCE_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*order amount:\s*(\d+)", re.IGNORECASE
)
_CLOB_QUANTITY_SCALE = 1_000_000
_FILL_SIZE_TOLERANCE = 1e-6


def is_zero_balance_error(result: dict) -> bool:
    return bool(_ZERO_BALANCE_PATTERN.search(str(result.get("error", ""))))


def is_balance_allowance_error(result: dict) -> bool:
    return bool(_BALANCE_ALLOWANCE_PATTERN.search(str(result.get("error", ""))))


def available_shares_from_error(result: dict) -> Optional[float]:
    match = _AVAILABLE_BALANCE_PATTERN.search(str(result.get("error", "")))
    if match is None:
        return None
    return int(match.group(1)) / _CLOB_QUANTITY_SCALE


def _valid_book_price(value) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or not 0 < price < 1:
        return None
    return price


class Trader:
    """Execute evidence-bound YES entries and immutable absolute exits."""

    def __init__(
        self,
        repo: TradeRepository,
        clob_client: ClobClientWrapper,
        config: TradingConfig,
        gamma_client: Optional[GammaClient] = None,
        simulation_mode: Optional[bool] = None,
    ):
        self.repo = repo
        self.clob = clob_client
        self.config = config
        self.gamma = gamma_client
        if simulation_mode is None:
            simulation_mode = bool(getattr(clob_client, "simulation_mode", False))
        self.mode = "sim" if simulation_mode else "live"
        self.buying_disabled = False

    def _fresh_book(self, token_id: str) -> Optional[tuple[float, float, float]]:
        """Return validated fresh bid/ask/spread, or fail closed."""
        try:
            best_bid = _valid_book_price(self.clob.get_best_bid(token_id))
            best_ask = _valid_book_price(self.clob.get_best_ask(token_id))
        except Exception as error:
            logger.warning(
                "fresh order book 조회 실패 - token=%s error=%s", token_id, error
            )
            return None
        if best_bid is None or best_ask is None or best_bid > best_ask + 1e-9:
            logger.warning(
                "invalid fresh order book - token=%s bid=%s ask=%s",
                token_id,
                best_bid,
                best_ask,
            )
            return None
        return best_bid, best_ask, best_ask - best_bid

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Revalidate the crossing, then submit a BUY at the fresh best ask."""
        if self.buying_disabled:
            return None
        condition_id = str(candidate["condition_id"])
        token_id = str(candidate["token_id"])
        if candidate.get("outcome") != "Yes":
            logger.error("Crown Momentum가 YES 이외 후보를 거부했습니다: %s", condition_id)
            return None
        entry_snapshot_id = candidate.get("entry_snapshot_id")
        if (
            isinstance(entry_snapshot_id, bool)
            or not isinstance(entry_snapshot_id, int)
            or entry_snapshot_id <= 0
        ):
            logger.warning(
                "current-run entry snapshot 증거 없는 후보를 fail-closed 처리합니다 - "
                "condition=%s snapshot=%s",
                condition_id,
                entry_snapshot_id,
            )
            return None
        prior_snapshot_id = candidate.get("prior_snapshot_id")
        if (
            isinstance(prior_snapshot_id, bool)
            or not isinstance(prior_snapshot_id, int)
            or prior_snapshot_id <= 0
            or prior_snapshot_id >= entry_snapshot_id
        ):
            logger.warning(
                "직전 persisted snapshot 증거 없는 후보를 fail-closed 처리합니다 - "
                "condition=%s prior_snapshot=%s entry_snapshot=%s",
                condition_id,
                prior_snapshot_id,
                entry_snapshot_id,
            )
            return None

        can_enter, reason = self.repo.can_reenter(
            condition_id, self.config.reentry_cooldown_hours
        )
        if not can_enter:
            logger.info("재진입 skip - condition=%s reason=%s", condition_id, reason)
            return None
        if self.repo.get_position_count() >= self.config.max_positions:
            logger.info("최대 포지션 수 %s 도달", self.config.max_positions)
            return None
        raw_event_id = candidate.get("event_id")
        if raw_event_id is None or not str(raw_event_id).strip():
            logger.warning(
                "event_id 없는 진입 후보를 fail-closed 처리합니다 - condition=%s",
                condition_id,
            )
            return None
        event_id = str(raw_event_id).strip()
        if (
            self.repo.get_event_position_count(event_id)
            >= self.config.max_event_positions
        ):
            logger.info(
                "event 포지션 한도 도달 - event=%s limit=%s",
                event_id,
                self.config.max_event_positions,
            )
            return None
        open_notional = self.repo.get_open_notional_usdc()
        if (
            open_notional + self.config.buy_amount_usdc
            > self.config.max_open_notional_usdc + 1e-9
        ):
            logger.info(
                "open notional 한도 도달 - current=$%.2f next=$%.2f limit=$%.2f",
                open_notional,
                self.config.buy_amount_usdc,
                self.config.max_open_notional_usdc,
            )
            return None

        clock_market = {
            "endDate": candidate.get("end_date"),
            "gameStartTime": candidate.get("game_start_time"),
            "sportsMarketType": candidate.get("sports_market_type"),
        }
        if candidate.get("is_sports"):
            # The scanner already classified this candidate from authoritative
            # Gamma tags/fields. Preserve that identity during the fresh clock
            # check even when Gamma omitted gameStartTime/sportsMarketType and
            # Queen is intentionally falling back to endDate.
            clock_market["tags"] = [{"slug": "sports"}]
        clock = evaluate_entry_clock(clock_market, self.config.sports)
        if not clock.valid:
            logger.info(
                "entry clock 재검증 실패 - condition=%s reason=%s",
                condition_id,
                clock.reason,
            )
            return None
        if bool(candidate.get("is_sports")) != clock.is_sports:
            logger.warning(
                "scanner/execution sports clock 불일치 - condition=%s",
                condition_id,
            )
            return None

        try:
            current_yes = _valid_book_price(self.clob.get_midpoint(token_id))
        except Exception as error:
            logger.warning(
                "entry midpoint 조회 실패 - condition=%s error=%s", condition_id, error
            )
            return None
        decision = evaluate_entry(
            candidate.get("prior_yes_price"),
            current_yes,
            clock.hours_left,
            self.config.entry,
            phase=clock.phase,
        )
        if not decision.should_enter:
            logger.info(
                "entry 재검증 실패 - condition=%s reason=%s",
                condition_id,
                decision.reason,
            )
            return None

        try:
            book = self.clob.get_buy_book_depth(
                token_id,
                ask_limit_price=self.config.entry.prob_max,
                max_price_window=self.config.depth_price_window,
            )
        except Exception as error:
            logger.warning(
                "fresh BUY depth 조회 실패 - condition=%s error=%s",
                condition_id,
                error,
            )
            return None
        best_bid = _valid_book_price(book.best_bid)
        best_ask = _valid_book_price(book.best_ask)
        depth_limit = _valid_book_price(book.ask_limit_price)
        try:
            spread = float(book.spread)
            depth_shares = float(book.ask_depth_shares)
        except (TypeError, ValueError):
            spread = float("nan")
            depth_shares = float("nan")
        if (
            best_bid is None
            or best_ask is None
            or depth_limit is None
            or best_bid > best_ask + 1e-9
            or not math.isfinite(spread)
            or spread < 0
            or not math.isclose(
                spread,
                best_ask - best_bid,
                rel_tol=0,
                abs_tol=1e-6,
            )
            or not math.isfinite(depth_shares)
            or depth_shares < 0
        ):
            logger.warning(
                "invalid fresh BUY depth - condition=%s bid=%s ask=%s "
                "spread=%s depth=%s limit=%s",
                condition_id,
                best_bid,
                best_ask,
                spread,
                depth_shares,
                depth_limit,
            )
            return None
        if spread > self.config.max_spread + 1e-9:
            logger.info(
                "fresh spread 상한 초과 - condition=%s spread=%.4f limit=%.4f",
                condition_id,
                spread,
                self.config.max_spread,
            )
            return None
        # Crossing is triggered by YES midpoint/Gamma price.  Executability is
        # independently capped by the fresh ask; the ask need not equal signal.
        if best_ask > self.config.entry.prob_max + 1e-9:
            logger.info(
                "fresh ask 상한 초과 - condition=%s ask=%.4f limit=%.4f",
                condition_id,
                best_ask,
                self.config.entry.prob_max,
            )
            return None
        if depth_limit < best_ask - 1e-9:
            logger.warning(
                "fresh depth limit이 best ask보다 낮습니다 - "
                "condition=%s ask=%.4f depth_limit=%.4f",
                condition_id,
                best_ask,
                depth_limit,
            )
            return None

        buy_shares = self.config.buy_amount_usdc / depth_limit
        required = self.config.min_order_size + self.config.min_order_buffer_shares
        if buy_shares + 1e-9 < required:
            logger.warning(
                "min-order buffer 미달 - condition=%s shares=%.6f required=%.6f",
                condition_id,
                buy_shares,
                required,
            )
            return None
        required_depth = buy_shares * self.config.depth_safety_multiple
        if depth_shares + 1e-9 < required_depth:
            logger.info(
                "fresh ask depth 부족 - condition=%s depth=%.6f required=%.6f "
                "limit=%.4f",
                condition_id,
                depth_shares,
                required_depth,
                depth_limit,
            )
            return None

        logger.info(
            "Crown Momentum 매수: '%s' signal=%.2f%% ask=%.2f%% "
            "limit=%.2f%% depth=%.2f shares=%.4f",
            str(candidate.get("question") or "")[:60],
            current_yes * 100,
            best_ask * 100,
            depth_limit * 100,
            depth_shares,
            buy_shares,
        )
        result = self.clob.place_limit_order(
            token_id=token_id,
            price=depth_limit,
            size=buy_shares,
            side="BUY",
        )
        if not (result.get("success") or result.get("orderID")):
            if is_balance_allowance_error(result):
                self.buying_disabled = True
                logger.warning(
                    "collateral 잔고/allowance 부족으로 이번 cycle의 남은 매수를 중단합니다"
                )
            else:
                logger.error("매수 주문 실패: %s", result)
            return None

        trade = self.repo.create_trade(
            condition_id=condition_id,
            market_slug=candidate.get("market_slug", ""),
            question=candidate.get("question", ""),
            event_id=event_id,
            event_slug=candidate.get("event_slug"),
            outcome="Yes",
            token_id=token_id,
            buy_price=depth_limit,
            buy_amount=self.config.buy_amount_usdc,
            buy_shares=buy_shares,
            buy_order_id=result.get("orderID"),
            buy_timestamp=datetime.utcnow(),
            buy_probability=current_yes,
            status=(
                TradeStatus.HOLDING
                if self.mode == "sim"
                else TradeStatus.PENDING_BUY
            ),
            entry_reason=decision.reason,
            strategy_name=STRATEGY_NAME,
            mode=self.mode,
            market_end_date=candidate.get("end_date"),
            hours_until_resolution_at_buy=candidate.get(
                "hours_until_resolution"
            ),
            liquidity_at_buy=candidate.get("liquidity"),
            volume_24h_at_buy=candidate.get("volume_24h"),
            market_tags=candidate.get("market_tags", ""),
            prior_yes_price_at_entry=candidate.get("prior_yes_price"),
            yes_price_at_buy=current_yes,
            stop_price_at_entry=self.config.entry.stop_price,
            take_profit_price_at_entry=self.config.entry.take_profit_price,
            entry_prob_min_at_buy=self.config.entry.prob_min,
            entry_prob_max_at_buy=self.config.entry.prob_max,
            entry_hours_min_at_buy=self.config.entry.hours_min,
            entry_hours_max_at_buy=self.config.entry.hours_max,
            entry_time_reference=clock.reference,
            entry_deadline_at_buy=clock.deadline,
            hours_until_entry_deadline_at_buy=clock.hours_left,
            market_game_start_time=clock.game_start_time,
            minutes_until_game_start_at_buy=clock.minutes_until_game_start,
            sports_market_type=clock.sports_market_type,
            sports_phase_at_buy=(
                clock.phase if clock.is_sports else "not_sports"
            ),
            prior_snapshot_id_at_entry=prior_snapshot_id,
            entry_snapshot_id=entry_snapshot_id,
            best_bid_at_buy=best_bid,
            best_ask_at_buy=best_ask,
            spread_at_buy=spread,
            book_depth_shares_at_buy=depth_shares,
            depth_limit_price_at_buy=depth_limit,
        )
        logger.info(
            "매수 주문 접수: Trade #%s Order=%s", trade.id, result.get("orderID")
        )
        return trade.id

    def _record_proven_resolution(
        self,
        trade,
        market: dict,
        fill_evidence: Optional[ExactFillEvidence] = None,
    ) -> bool:
        proof = get_proven_resolution(market)
        if proof is None:
            return False
        observed_at = datetime.utcnow()
        payout = float(proof["yes_payout"])
        if fill_evidence is not None:
            confirmed_size = fill_evidence.confirmed_size
            confirmed_vwap = fill_evidence.confirmed_vwap
            confirmed_fee = fill_evidence.confirmed_fee_usdc
            assumption = (payout - confirmed_vwap) * confirmed_size
            if fill_evidence.fee_complete and confirmed_fee is not None:
                assumption -= confirmed_fee
                assumption_basis = "confirmed_buy_fill_net_known_buy_fee"
            else:
                assumption_basis = "confirmed_buy_fill_gross_fee_unproven"
            resolution_evidence = (
                f"{proof['evidence']}+execution_ledger_exact_confirmed_buy"
            )
        else:
            confirmed_size = getattr(trade, "buy_shares", None)
            confirmed_vwap = getattr(trade, "buy_price", None)
            confirmed_fee = None
            assumption = None
            if confirmed_vwap is not None and confirmed_size is not None:
                assumption = (payout - confirmed_vwap) * confirmed_size
            assumption_basis = "simulation_requested_order_assumption"
            resolution_evidence = f"{proof['evidence']}+simulation_order"
        # Preserve the Gamma catalog evidence as well as the trade-local proof.
        self.repo.save_market_catalog(trade.condition_id, market, commit=True)
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.RESOLVED,
            exit_reason="resolved_with_payout_evidence",
            yes_price_at_exit=payout,
            resolution_outcome=proof["outcome"],
            resolution_value=payout,
            resolution_status=proof["status"],
            resolution_observed_at=observed_at,
            resolution_source_updated_at=market.get("updatedAt"),
            resolution_evidence=resolution_evidence,
            resolution_confirmed_buy_size=confirmed_size,
            resolution_confirmed_buy_vwap=confirmed_vwap,
            resolution_confirmed_buy_fee_usdc=confirmed_fee,
            settlement_pnl_assumption=assumption,
            settlement_assumption_basis=assumption_basis,
            # Deliberately no synthetic SELL and no realized P&L.
            sell_price=None,
            sell_shares=None,
            sell_order_id=None,
            sell_timestamp=None,
            sell_probability=None,
            realized_pnl=None,
        )
        logger.warning(
            "Gamma payout 증거로 RESOLVED 기록: Trade #%s outcome=%s YES payout=%.2f "
            "(settlement assumption=%s, realized_pnl=NULL)",
            trade.id,
            proof["outcome"],
            payout,
            assumption,
        )
        return True

    def _handle_midpoint_unavailable(self, trade, error) -> bool:
        if self.gamma is None:
            logger.warning(
                "midpoint unavailable and Gamma client not injected - trade=%s error=%s",
                trade.id,
                error,
            )
            return False
        try:
            market = self.gamma.get_market_by_condition_id(trade.condition_id)
        except Exception as gamma_error:
            logger.warning(
                "Gamma resolution lookup 실패 - condition=%s error=%s",
                trade.condition_id,
                gamma_error,
            )
            return False
        proof = get_proven_resolution(market) if market else None
        if proof is not None:
            if self.mode == "sim" or str(getattr(trade, "buy_order_id", "")).startswith(
                "SIM_"
            ):
                self._record_proven_resolution(trade, market)
                return False
            evidence = self.repo.get_exact_buy_fill_evidence(
                getattr(trade, "buy_order_id", None)
            )
            if evidence.state == "confirmed":
                self._record_proven_resolution(trade, market, fill_evidence=evidence)
                return False
            if evidence.state == "terminal_zero_fill":
                self.repo.update_trade(
                    trade.id,
                    status=TradeStatus.UNFILLED,
                    exit_reason="resolution_terminal_zero_fill",
                    realized_pnl=None,
                )
                logger.warning(
                    "resolved market의 terminal zero-fill 증명으로 UNFILLED: "
                    "Trade #%s order=%s status=%s",
                    trade.id,
                    evidence.order_id,
                    evidence.order_status,
                )
                return False
            logger.warning(
                "resolved payout은 확인했지만 exact CONFIRMED BUY fill 증거가 "
                "없어 HOLDING 유지: Trade #%s state=%s detail=%s",
                trade.id,
                evidence.state,
                evidence.detail,
            )
            return False
        logger.warning(
            "midpoint unavailable; closed+final payout 증거 없음 - condition=%s error=%s",
            trade.condition_id,
            error,
        )
        return False

    def _place_sell_with_balance_retry(
        self,
        *,
        token_id: str,
        price: float,
        requested_size: float,
    ) -> tuple[dict, float]:
        """Submit the exact proven holding size.

        Queen deliberately does not retry a smaller SELL after a balance
        error.  A partial retry would leave an unmodelled residual position
        and make exact BUY/SELL fill accounting impossible.
        """
        result = self.clob.place_limit_order(
            token_id=token_id, price=price, size=requested_size, side="SELL"
        )
        available = available_shares_from_error(result)
        if (
            not (result.get("success") or result.get("orderID"))
            and available is not None
            and 0 < available < requested_size
        ):
            logger.warning(
                "부분 token 잔고를 감지했지만 exact-size 원칙으로 SELL 재시도를 "
                "보류합니다 - requested=%.6f available=%.6f",
                requested_size,
                available,
            )
        return result, requested_size

    @staticmethod
    def _actual_fill_ready(evidence: ExactFillEvidence) -> bool:
        return (
            evidence.has_reconciled_full_fill
            and evidence.fee_complete
            and evidence.confirmed_size is not None
            and evidence.confirmed_vwap is not None
            and evidence.confirmed_fee_usdc is not None
        )

    def reconcile_pending_buy(self, trade) -> bool:
        """Activate a live position only after an exact full BUY fill."""
        if self.mode == "sim":
            logger.error(
                "simulation trade가 PENDING_BUY에 남아 있습니다 - trade=%s",
                trade.id,
            )
            return False
        evidence = self.repo.get_exact_buy_fill_evidence(
            getattr(trade, "buy_order_id", None)
        )
        if evidence.state == "terminal_zero_fill":
            self.repo.update_trade(
                trade.id,
                status=TradeStatus.UNFILLED,
                exit_reason="buy_terminal_zero_fill",
                realized_pnl=None,
            )
            logger.warning(
                "exact terminal zero-fill BUY 증거로 UNFILLED: Trade #%s order=%s",
                trade.id,
                evidence.order_id,
            )
            return False
        if (
            not evidence.has_reconciled_full_fill
            or evidence.confirmed_size is None
            or evidence.confirmed_vwap is None
        ):
            logger.info(
                "BUY full-fill 대사 대기: Trade #%s state=%s full=%s detail=%s",
                trade.id,
                evidence.state,
                evidence.has_reconciled_full_fill,
                evidence.detail,
            )
            return False
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.HOLDING,
            buy_price=evidence.confirmed_vwap,
            buy_shares=evidence.confirmed_size,
            buy_confirmed_size=evidence.confirmed_size,
            buy_confirmed_vwap=evidence.confirmed_vwap,
            buy_confirmed_fee_usdc=(
                evidence.confirmed_fee_usdc if evidence.fee_complete else None
            ),
        )
        logger.info(
            "exact full BUY fill로 HOLDING 활성화: Trade #%s size=%.6f vwap=%.4f",
            trade.id,
            evidence.confirmed_size,
            evidence.confirmed_vwap,
        )
        return True

    def reconcile_pending_sell(self, trade) -> bool:
        """Finalize one live exit only from exact, full BUY/SELL fill proof."""
        if self.mode == "sim":
            logger.error(
                "simulation trade가 PENDING_SELL에 남아 있습니다 - trade=%s",
                trade.id,
            )
            return False
        sell_evidence = self.repo.get_exact_sell_fill_evidence(
            getattr(trade, "sell_order_id", None)
        )
        if sell_evidence.state == "terminal_zero_fill":
            # The venue proved this exact SELL never filled.  Re-arm the
            # position without fabricating a close; the execution ledger keeps
            # the immutable failed order history.
            pending_reason = str(getattr(trade, "exit_reason", "") or "")
            base_reason = pending_reason.removesuffix("_pending_confirmed_fill")
            self.repo.update_trade(
                trade.id,
                status=TradeStatus.HOLDING,
                exit_reason=f"{base_reason or 'exit'}_sell_terminal_zero_fill",
                sell_price=None,
                sell_shares=None,
                sell_order_id=None,
                sell_timestamp=None,
                sell_probability=None,
                realized_pnl=None,
                hypothetical_pnl=None,
                pnl_basis=None,
                yes_price_at_exit=None,
                best_bid_at_exit=None,
                best_ask_at_exit=None,
                spread_at_exit=None,
                sell_confirmed_size=None,
                sell_confirmed_vwap=None,
                sell_confirmed_fee_usdc=None,
                sell_fill_matched_at=None,
            )
            logger.warning(
                "exact terminal zero-fill SELL 증거로 HOLDING 복귀: Trade #%s order=%s",
                trade.id,
                sell_evidence.order_id,
            )
            return False
        if not self._actual_fill_ready(sell_evidence):
            logger.warning(
                "SELL full-fill/fee 대사 미완료로 PENDING_SELL 유지: "
                "Trade #%s state=%s full=%s fee=%s detail=%s",
                trade.id,
                sell_evidence.state,
                sell_evidence.has_reconciled_full_fill,
                sell_evidence.fee_complete,
                sell_evidence.detail,
            )
            return False

        buy_evidence = self.repo.get_exact_buy_fill_evidence(
            getattr(trade, "buy_order_id", None)
        )
        if not self._actual_fill_ready(buy_evidence):
            logger.error(
                "SELL은 full fill이지만 BUY full-fill/fee 증거가 없어 "
                "PENDING_SELL 유지: Trade #%s state=%s full=%s fee=%s detail=%s",
                trade.id,
                buy_evidence.state,
                buy_evidence.has_reconciled_full_fill,
                buy_evidence.fee_complete,
                buy_evidence.detail,
            )
            return False
        if not math.isclose(
            sell_evidence.confirmed_size,
            buy_evidence.confirmed_size,
            rel_tol=1e-9,
            abs_tol=_FILL_SIZE_TOLERANCE,
        ):
            logger.error(
                "BUY/SELL confirmed size 불일치로 PENDING_SELL 유지: "
                "Trade #%s buy=%.6f sell=%.6f",
                trade.id,
                buy_evidence.confirmed_size,
                sell_evidence.confirmed_size,
            )
            return False

        size = sell_evidence.confirmed_size
        realized_pnl = (
            (sell_evidence.confirmed_vwap - buy_evidence.confirmed_vwap) * size
            - buy_evidence.confirmed_fee_usdc
            - sell_evidence.confirmed_fee_usdc
        )
        pending_reason = str(getattr(trade, "exit_reason", "") or "")
        base_reason = pending_reason.removesuffix("_pending_confirmed_fill")
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.COMPLETED,
            exit_reason=f"{base_reason or 'exit'}_confirmed_fill",
            sell_price=sell_evidence.confirmed_vwap,
            sell_shares=size,
            realized_pnl=realized_pnl,
            hypothetical_pnl=None,
            pnl_basis="exact_reconciled_buy_sell_confirmed_fills_net_known_fees",
            buy_confirmed_size=buy_evidence.confirmed_size,
            buy_confirmed_vwap=buy_evidence.confirmed_vwap,
            buy_confirmed_fee_usdc=buy_evidence.confirmed_fee_usdc,
            sell_confirmed_size=size,
            sell_confirmed_vwap=sell_evidence.confirmed_vwap,
            sell_confirmed_fee_usdc=sell_evidence.confirmed_fee_usdc,
            sell_fill_matched_at=sell_evidence.matched_at,
        )
        logger.info(
            "confirmed %s SELL 완료: Trade #%s size=%.6f vwap=%.4f actual P&L=$%.4f",
            base_reason or "exit",
            trade.id,
            size,
            sell_evidence.confirmed_vwap,
            realized_pnl,
        )
        return True

    def execute_sell(self, trade) -> bool:
        """Apply immutable target/stop signals and submit at a fresh best bid."""
        try:
            current_yes = _valid_book_price(self.clob.get_midpoint(trade.token_id))
        except Exception as error:
            return self._handle_midpoint_unavailable(trade, error)
        if current_yes is None:
            return self._handle_midpoint_unavailable(trade, "midpoint unavailable")

        immutable_stop = getattr(trade, "stop_price_at_entry", None)
        stop_price = (
            immutable_stop
            if immutable_stop is not None
            else self.config.entry.stop_price
        )
        immutable_target = getattr(trade, "take_profit_price_at_entry", None)
        take_profit_price = (
            immutable_target
            if immutable_target is not None
            else self.config.entry.take_profit_price
        )
        reason = evaluate_exit(
            current_yes,
            stop_price,
            take_profit_price,
        )
        if reason is None:
            logger.debug(
                "보유 유지: condition=%s YES=%.2f%% stop=%.2f%% target=%.2f%%",
                trade.condition_id,
                current_yes * 100,
                stop_price * 100,
                take_profit_price * 100,
            )
            return False

        book = self._fresh_book(trade.token_id)
        if book is None:
            return False
        best_bid, best_ask, spread = book
        if (
            reason == "take_profit"
            and best_bid < take_profit_price - 1e-9
        ):
            logger.info(
                "take-profit 신호는 충족했지만 fresh bid가 목표 미달 - "
                "Trade #%s signal=%.4f bid=%.4f target=%.4f",
                trade.id,
                current_yes,
                best_bid,
                take_profit_price,
            )
            return False
        # Do not re-trigger from bid. The signal was already decided by midpoint.
        logger.info(
            "%s 충족: Trade #%s signal=%.2f%% bid=%.2f%% shares=%.6f",
            reason,
            trade.id,
            current_yes * 100,
            best_bid * 100,
            trade.buy_shares,
        )
        result, sell_shares = self._place_sell_with_balance_retry(
            token_id=trade.token_id,
            price=best_bid,
            requested_size=trade.buy_shares,
        )
        if result.get("success") or result.get("orderID"):
            common = {
                "sell_price": best_bid,
                "sell_shares": sell_shares,
                "sell_order_id": result.get("orderID"),
                "sell_timestamp": datetime.utcnow(),
                "sell_probability": current_yes,
                "yes_price_at_exit": current_yes,
                "best_bid_at_exit": best_bid,
                "best_ask_at_exit": best_ask,
                "spread_at_exit": spread,
                "sell_confirmed_size": None,
                "sell_confirmed_vwap": None,
                "sell_confirmed_fee_usdc": None,
                "sell_fill_matched_at": None,
            }
            if self.mode == "sim":
                hypothetical_pnl = (best_bid - trade.buy_price) * sell_shares
                self.repo.update_trade(
                    trade.id,
                    **common,
                    status=TradeStatus.COMPLETED,
                    exit_reason=f"{reason}_simulation_hypothetical",
                    realized_pnl=None,
                    hypothetical_pnl=hypothetical_pnl,
                    pnl_basis="simulation_hypothetical_best_bid_fees_excluded",
                )
                logger.info(
                    "simulation %s SELL: Trade #%s bid=%.4f "
                    "hypothetical P&L=$%.4f",
                    reason,
                    trade.id,
                    best_bid,
                    hypothetical_pnl,
                )
                return True

            self.repo.update_trade(
                trade.id,
                **common,
                status=TradeStatus.PENDING_SELL,
                exit_reason=f"{reason}_pending_confirmed_fill",
                realized_pnl=None,
                hypothetical_pnl=None,
                pnl_basis=None,
            )
            logger.info(
                "%s SELL 접수, confirmed fill 대기: Trade #%s order=%s "
                "bid=%.4f size=%.6f",
                reason,
                trade.id,
                result.get("orderID"),
                best_bid,
                sell_shares,
            )
            return False
        if is_zero_balance_error(result):
            self._mark_unfilled(trade)
            return False
        logger.error("매도 주문 실패: %s", result)
        return False

    def _mark_unfilled(self, trade) -> None:
        if trade.buy_order_id and not str(trade.buy_order_id).startswith("SIM"):
            try:
                self.clob.cancel_order(trade.buy_order_id)
            except SubmissionEvidenceError as error:
                if isinstance(error.__cause__, ClobResponseUnavailableError):
                    self.repo.update_trade(
                        trade.id,
                        status=TradeStatus.QUARANTINED,
                        exit_reason="zero_balance_order_unavailable",
                        realized_pnl=None,
                    )
                    logger.warning(
                        "zero-balance 주문 증거 소실로 QUARANTINED: Trade #%s",
                        trade.id,
                    )
                    return
                logger.error(
                    "zero-fill 취소 증명 실패로 HOLDING 유지: Trade #%s error=%s",
                    trade.id,
                    type(error).__name__,
                )
                return
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.UNFILLED,
            exit_reason="buy_unfilled",
            realized_pnl=None,
        )
        logger.warning("매수 zero-fill 증명으로 UNFILLED: Trade #%s", trade.id)

    def check_and_sell_holdings(self) -> int:
        count = 0
        for trade in self.repo.get_holding_trades():
            if self.execute_sell(trade):
                count += 1
        return count
