"""Order execution and evidence-safe settlement handling for Sports Resolution Hold Live."""

from __future__ import annotations

from datetime import datetime, timezone
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
from .scanner import get_hours_until_resolution, parse_end_date


logger = logging.getLogger(__name__)

_ZERO_BALANCE_PATTERN = re.compile(
    r"not enough balance.*balance:\s*0(?:\D|$)", re.IGNORECASE
)
_BALANCE_ALLOWANCE_PATTERN = re.compile(
    r"not enough balance\s*/\s*allowance", re.IGNORECASE
)
# CLOB 잔고 거절은 두 가지 형식으로 온다. 실측(2026-07-28 Jenkins 로그):
#   (1) balance: N, order amount: M
#   (2) balance: N, sum of active orders: X, sum of matched orders: Y,
#       order amount (inc. fees): Z
# (2)에서 X가 N과 같으면 잔고 전액이 **자기 자신의 미체결 주문**에 묶인 것이다.
# 이 경우 수량을 줄여도 절대 팔리지 않는다 — 기존 주문을 먼저 취소해야 한다.
_AVAILABLE_BALANCE_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*(?:sum of active orders:\s*\d+.*?)?"
    r"order amount(?:\s*\(inc\. fees\))?:\s*(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_ACTIVE_ORDERS_PATTERN = re.compile(
    r"balance:\s*(\d+)\s*,\s*sum of active orders:\s*(\d+)", re.IGNORECASE
)


def locked_in_own_orders(result: dict) -> bool:
    """잔고 전액이 자기 미체결 주문에 묶였는지. 수량 축소로는 해결되지 않는다."""
    match = _ACTIVE_ORDERS_PATTERN.search(str(result.get("error", "")))
    if match is None:
        return False
    balance, active = int(match.group(1)), int(match.group(2))
    return balance > 0 and active >= balance
_CLOB_QUANTITY_SCALE = 1_000_000
_SELL_BALANCE_SAFETY_FACTOR = 0.99
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

# ── 매도 실패 진단 ────────────────────────────────────────────────────
# 매도 거절 중 상당수는 재시도해도 성공하지 않는다. 그런데 실패 분기가 trade
# 상태를 바꾸지 않으므로 HOLDING으로 남아 매 사이클 같은 주문을 반복 제출한다.
# golden-cherry 실측(2026-07-11~28): 실패한 SELL 제출 73,238건 / 401 token
# = 토큰당 평균 182.6회, 최대 4,002회(17일간 사실상 매 사이클).
# 원인은 둘이었다 — (1) 부분 체결로 실제 잔고 < DB 수량인데 줄이지 않음,
# (2) 잔고와 정확히 같은 수량을 제출(거래소가 반올림 여유를 요구).
_SELL_GONE_PATTERNS = ("invalid token id", "orderbook id does not exist")


def classify_sell_failure(
    result: dict, requested_size: float, min_order_size: float = 5.0
) -> str:
    """매도 실패를 재시도 가능성 기준으로 분류한다 (로그 집계용).

    market_gone / dust_unsellable 은 영구적이라 재시도가 무의미하다.
    partial_balance / balance_edge 는 수량을 줄이면 체결될 수 있다.
    """
    message = str(result.get("error", "")).lower()
    if any(pattern in message for pattern in _SELL_GONE_PATTERNS):
        return "market_gone"
    if "not enough balance" not in message:
        if "not ready" in message or "request exception" in message:
            return "transient"
        return "other"
    if locked_in_own_orders(result):
        return "locked_in_own_orders"
    available = available_shares_from_error(result)
    if available is None:
        return "balance_unparsed"
    if available <= 0:
        return "zero_balance"
    if available < min_order_size:
        return "dust_unsellable"
    if available < requested_size:
        return "partial_balance"
    return "balance_edge"



def _valid_book_price(value) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or not 0 < price < 1:
        return None
    return price


class Trader:
    """Execute exact-book binary-outcome entries and hold to resolution."""

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
        """Revalidate the exact $5 walk, then submit a FOK BUY."""
        if self.buying_disabled:
            return None
        condition_id = str(candidate["condition_id"])
        token_id = str(candidate["token_id"])
        outcome = str(candidate.get("outcome") or "").strip()
        if not outcome:
            logger.error("aligned outcome identity missing: %s", condition_id)
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

        now = datetime.now(timezone.utc)
        experiment_start = parse_end_date(self.config.experiment_start_utc)
        experiment_end = parse_end_date(self.config.experiment_entry_end_utc)
        if (
            experiment_start is None
            or experiment_end is None
            or not (experiment_start <= now < experiment_end)
        ):
            logger.info("frozen entry period is closed - condition=%s", condition_id)
            return None
        hours_left = get_hours_until_resolution(candidate.get("end_date"), now=now)
        if hours_left is None or not 0 < hours_left <= 6 + 1e-9:
            logger.info(
                "six-hour window revalidation failed - condition=%s hours=%s",
                condition_id,
                hours_left,
            )
            return None
        try:
            walk = self.clob.get_buy_book_walk(
                token_id, notional_usdc=self.config.buy_amount_usdc
            )
        except Exception as error:
            logger.warning(
                "fresh exact-book walk failed - condition=%s error=%s",
                condition_id,
                type(error).__name__,
            )
            return None
        if not (
            self.config.entry.prob_min - 1e-9
            <= walk.vwap
            <= self.config.entry.prob_max + 1e-9
        ):
            logger.info(
                "fresh exact VWAP left arm band - condition=%s vwap=%.4f band=%.2f-%.2f",
                condition_id,
                walk.vwap,
                self.config.entry.prob_min,
                self.config.entry.prob_max,
            )
            return None
        required = self.config.min_order_size + self.config.min_order_buffer_shares
        if walk.shares + 1e-9 < required:
            logger.warning(
                "min-order buffer 미달 - condition=%s shares=%.6f required=%.6f",
                condition_id,
                walk.shares,
                required,
            )
            return None
        if not 0 < walk.limit_price < 1:
            logger.warning(
                "FOK limit price is not orderable - condition=%s price=%s",
                condition_id,
                walk.limit_price,
            )
            return None

        logger.info(
            "Golden Tangerine FOK BUY: '%s' outcome=%s exact_vwap=%.2f%% "
            "best_ask=%.2f%% limit=%.2f%% shares=%.4f",
            str(candidate.get("question") or "")[:60],
            outcome,
            walk.vwap * 100,
            walk.best_ask * 100,
            walk.limit_price * 100,
            walk.shares,
        )
        result = self.clob.place_limit_order(
            token_id=token_id,
            price=walk.limit_price,
            size=walk.shares,
            side="BUY",
            order_type="FOK",
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
            outcome=outcome,
            token_id=token_id,
            buy_price=walk.vwap,
            buy_amount=self.config.buy_amount_usdc,
            buy_shares=walk.shares,
            buy_order_id=result.get("orderID"),
            buy_timestamp=datetime.utcnow(),
            buy_probability=walk.vwap,
            status=(
                TradeStatus.HOLDING
                if self.mode == "sim"
                else TradeStatus.PENDING_BUY
            ),
            entry_reason="first_observed_exact_5_usdc_band_fok",
            strategy_name=STRATEGY_NAME,
            mode=self.mode,
            market_end_date=candidate.get("end_date"),
            hours_until_resolution_at_buy=hours_left,
            liquidity_at_buy=candidate.get("liquidity"),
            volume_24h_at_buy=candidate.get("volume_24h"),
            market_tags=candidate.get("market_tags", ""),
            prior_yes_price_at_entry=None,
            yes_price_at_buy=candidate.get("yes_probability"),
            stop_price_at_entry=self.config.entry.stop_price,
            entry_prob_min_at_buy=self.config.entry.prob_min,
            entry_prob_max_at_buy=self.config.entry.prob_max,
            entry_hours_min_at_buy=self.config.entry.hours_min,
            entry_hours_max_at_buy=self.config.entry.hours_max,
            prior_snapshot_id_at_entry=None,
            entry_snapshot_id=entry_snapshot_id,
            best_bid_at_buy=walk.best_bid,
            best_ask_at_buy=walk.best_ask,
            spread_at_buy=walk.spread,
        )
        logger.info(
            "매수 주문 접수: Trade #%s Order=%s", trade.id, result.get("orderID")
        )
        episode_id = candidate.get("entry_episode_id")
        if not isinstance(episode_id, bool) and isinstance(episode_id, int):
            self.repo.link_entry_episode_trade(episode_id, trade.id)
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
        payouts = proof.get("payouts_by_outcome") or {}
        if trade.outcome not in payouts:
            logger.error(
                "resolution payout lacks selected outcome - trade=%s outcome=%s",
                trade.id,
                trade.outcome,
            )
            return False
        payout = float(payouts[trade.outcome])
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
            # Legacy column name: stores payout of the first listed outcome.
            yes_price_at_exit=proof["first_outcome_payout"],
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
            "Gamma payout 증거로 RESOLVED 기록: Trade #%s selected=%s winner=%s payout=%.2f "
            "(settlement assumption=%s, realized_pnl=NULL)",
            trade.id,
            trade.outcome,
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
        result = self.clob.place_limit_order(
            token_id=token_id, price=price, size=requested_size, side="SELL"
        )
        if result.get("success") or result.get("orderID"):
            return result, requested_size
        available = available_shares_from_error(result)
        if available is None or available <= 0 or available >= requested_size:
            return result, requested_size
        retry_size = (
            math.floor(available * _SELL_BALANCE_SAFETY_FACTOR * _CLOB_QUANTITY_SCALE)
            / _CLOB_QUANTITY_SCALE
        )
        if retry_size < self.config.min_order_size:
            logger.warning(
                "부분 잔고가 최소 SELL 수량보다 작아 보류 - available=%.6f",
                available,
            )
            return result, requested_size
        logger.warning(
            "가용 token 잔고 99%%로 SELL 1회 재시도 - requested=%.6f available=%.6f retry=%.6f",
            requested_size,
            available,
            retry_size,
        )
        retry = self.clob.place_limit_order(
            token_id=token_id, price=price, size=retry_size, side="SELL"
        )
        return retry, retry_size

    @staticmethod
    def _actual_fill_ready(evidence: ExactFillEvidence) -> bool:
        return (
            evidence.has_reconciled_full_fill
            and evidence.fee_complete
            and evidence.confirmed_size is not None
            and evidence.confirmed_vwap is not None
            and evidence.confirmed_fee_usdc is not None
        )

    @staticmethod
    def _pending_buy_age_minutes(
        trade, now: Optional[datetime] = None
    ) -> Optional[float]:
        placed_at = getattr(trade, "buy_timestamp", None)
        if not isinstance(placed_at, datetime):
            return None
        current = now or datetime.utcnow()
        if placed_at.tzinfo is not None and current.tzinfo is None:
            current = current.replace(tzinfo=placed_at.tzinfo)
        elif placed_at.tzinfo is None and current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        age = (current - placed_at).total_seconds() / 60.0
        return age if math.isfinite(age) and age >= 0 else None

    def reconcile_pending_buy(
        self, trade, *, now: Optional[datetime] = None
    ) -> bool:
        """Activate terminal fills and cancel stale entry remainders."""
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
        if not evidence.has_reconciled_executed_fill:
            age_minutes = self._pending_buy_age_minutes(trade, now=now)
            if (
                age_minutes is not None
                and age_minutes + 1e-9 >= self.config.max_snapshot_gap_minutes
                and getattr(trade, "buy_order_id", None)
            ):
                try:
                    terminal = self.clob.cancel_order_for_reconciliation(
                        trade.buy_order_id
                    )
                except SubmissionEvidenceError as error:
                    logger.warning(
                        "만료 BUY 취소 증명 실패로 PENDING_BUY 유지: Trade #%s "
                        "age=%.1fmin error=%s",
                        trade.id,
                        age_minutes,
                        type(error).__name__,
                    )
                    return False
                logger.info(
                    "entry signal TTL 만료로 BUY remainder 취소/종결 확인: "
                    "Trade #%s age=%.1fmin status=%s matched=%.6f; "
                    "다음 cycle exact ledger 대사 대기",
                    trade.id,
                    age_minutes,
                    terminal.get("verified_order_status"),
                    terminal.get("verified_size_matched", 0.0),
                )
                return False
            logger.info(
                "BUY terminal fill 대사 대기: Trade #%s state=%s full=%s "
                "age=%s detail=%s",
                trade.id,
                evidence.state,
                evidence.has_reconciled_full_fill,
                f"{age_minutes:.1f}m" if age_minutes is not None else "unknown",
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
            "exact terminal BUY fill로 HOLDING 활성화: Trade #%s size=%.6f "
            "vwap=%.4f requested_full=%s status=%s",
            trade.id,
            evidence.confirmed_size,
            evidence.confirmed_vwap,
            evidence.has_reconciled_full_fill,
            evidence.order_status,
        )
        return True

    def reconcile_pending_sell(self, trade) -> bool:
        """Finalize one live stop only from exact, full BUY/SELL fill proof."""
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
            self.repo.update_trade(
                trade.id,
                status=TradeStatus.HOLDING,
                exit_reason="stop_sell_terminal_zero_fill",
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
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.COMPLETED,
            exit_reason="absolute_stop_confirmed_fill",
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
            "confirmed stop SELL 완료: Trade #%s size=%.6f vwap=%.4f actual P&L=$%.4f",
            trade.id,
            size,
            sell_evidence.confirmed_vwap,
            realized_pnl,
        )
        return True

    def execute_sell(self, trade) -> bool:
        """Hold until resolution; never mutate account-wide/manual positions."""
        try:
            current_price = _valid_book_price(self.clob.get_midpoint(trade.token_id))
        except Exception as error:
            return self._handle_midpoint_unavailable(trade, error)
        if current_price is None:
            return self._handle_midpoint_unavailable(trade, "midpoint unavailable")
        logger.debug(
            "hold to resolution - trade=%s outcome=%s current=%.4f",
            trade.id,
            trade.outcome,
            current_price,
        )
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
