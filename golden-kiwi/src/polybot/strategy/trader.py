"""Simulation execution and evidence-safe settlement for Micro-Cascade."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import re
from typing import Optional

from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
    current_run_id,
)

from ..api.clob_client import ClobClientWrapper
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.models import STRATEGY_NAME, TradeStatus
from ..db.repository import ExactFillEvidence, TradeRepository
from .filters import get_proven_resolution
from .scanner import get_hours_until_resolution, parse_end_date
from .signals import evaluate_entry, evaluate_exit


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
    """Execute evidence-bound simulated entries and time-only exits."""

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
        if not simulation_mode:
            raise RuntimeError(
                "Golden Kiwi is research/simulation-only; live Trader "
                "construction is disabled by source policy"
            )
        self.mode = "sim"
        self.buying_disabled = False
        self.last_attempt_evidence_by_condition: dict[str, dict] = {}

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

    def _fresh_exit_bid(self, token_id: str) -> Optional[float]:
        """Return the executable exit mark without requiring an ask quote."""
        try:
            best_bid = _valid_book_price(self.clob.get_best_bid(token_id))
        except Exception as error:
            logger.warning(
                "fresh exit bid 조회 실패 - token=%s error=%s", token_id, error
            )
            return None
        if best_bid is None:
            logger.warning(
                "invalid fresh exit bid - token=%s bid=%s", token_id, best_bid
            )
            return None
        return best_bid

    def evaluate_drawdown_stop(self) -> bool:
        """Stage a candidate-independent first crossing from strict evidence."""
        existing = self.repo.get_drawdown_kill_switch()
        if existing is not None:
            self.buying_disabled = True
            logger.error(
                "Micro-Cascade drawdown kill switch가 이미 영구 작동했습니다 "
                "- tripped_at=%s run=%s trip_pnl=$%.2f; new simulations disabled",
                existing["tripped_at"],
                existing["tripped_run_id"],
                existing["economic_pnl"],
            )
            return True
        limit = (
            self.config.experiment_capital_usdc
            * self.config.max_drawdown_stop
        )
        run_id = current_run_id()
        if not run_id:
            raise RuntimeError(
                "drawdown 평가를 기록할 current RunAudit ID가 없습니다"
            )
        evaluation = self.repo.strict_terminal_economic_path(
            current_run_id_value=run_id,
            loss_limit_usdc=limit,
        )
        if not evaluation.tripped:
            return False
        if (
            evaluation.trip_economic_pnl is None
            or not evaluation.source_terminal_run_id
        ):
            raise RuntimeError("drawdown first-crossing source 증거가 없습니다")
        state = self.repo.stage_drawdown_kill_switch(
            detection_run_id=run_id,
            source_terminal_run_id=evaluation.source_terminal_run_id,
            economic_pnl=evaluation.trip_economic_pnl,
            loss_limit_usdc=limit,
            experiment_capital_usdc=self.config.experiment_capital_usdc,
            max_drawdown_stop=self.config.max_drawdown_stop,
        )
        logger.error(
            "Micro-Cascade drawdown kill switch - research economic P&L "
            "$%.2f <= -$%.2f ($%.2f x %.0f%%); pending latch staged "
            "detector=%s source=%s",
            evaluation.trip_economic_pnl,
            limit,
            self.config.experiment_capital_usdc,
            self.config.max_drawdown_stop * 100,
            run_id,
            state["source_terminal_run_id"],
        )
        self.buying_disabled = True
        return True

    def _start_attempt(self, candidate: dict) -> tuple[str, dict]:
        condition_id = str(candidate.get("condition_id") or "").strip()
        evidence = {
            "fresh_attempt_order": candidate.get("_fresh_attempt_order"),
            "fresh_attempted": 0,
            "fresh_observed_at": None,
            "fresh_best_bid": None,
            "fresh_best_ask": None,
            "fresh_spread": None,
            "fresh_depth_shares": None,
            "fresh_depth_limit_price": None,
            "fresh_gate_passed": 0,
            "fresh_fail_reason": "attempt_started",
            "execution_selected": 0,
            "trade_id": None,
        }
        if condition_id:
            self.last_attempt_evidence_by_condition[condition_id] = evidence
        return condition_id, evidence

    @staticmethod
    def _fail_attempt(evidence: dict, reason: str, **values) -> None:
        evidence.update(values)
        evidence["fresh_gate_passed"] = 0
        evidence["fresh_fail_reason"] = reason

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Revalidate the persisted staircase, then record a simulated BUY."""
        condition_id, attempt = self._start_attempt(candidate)
        if self.buying_disabled:
            self._fail_attempt(attempt, "drawdown_kill_switch")
            return None

        token_id = str(candidate.get("token_id") or "").strip()
        event_id = str(candidate.get("event_id") or "").strip()
        if not condition_id or not token_id or not event_id:
            logger.warning("진입 identity 증거가 불완전합니다 - condition=%s", condition_id)
            self._fail_attempt(attempt, "identity_incomplete")
            return None
        if candidate.get("outcome") != "Yes":
            logger.error("Micro-Cascade는 strict binary YES만 허용합니다: %s", condition_id)
            self._fail_attempt(attempt, "outcome_not_yes")
            return None
        if candidate.get("cooldown_allowed") is False:
            self._fail_attempt(
                attempt,
                str(candidate.get("cooldown_reason") or "raw_event_signal_cooldown"),
            )
            return None

        steps = self.config.entry.confirmation_steps
        snapshot_ids = candidate.get("trend_snapshot_ids")
        if (
            not isinstance(snapshot_ids, list)
            or len(snapshot_ids) != steps + 1
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                for value in snapshot_ids
            )
            or snapshot_ids != sorted(snapshot_ids)
            or len(set(snapshot_ids)) != len(snapshot_ids)
        ):
            logger.warning(
                "persisted staircase snapshot ID 증거가 유효하지 않습니다 - "
                "condition=%s ids=%s",
                condition_id,
                snapshot_ids,
            )
            self._fail_attempt(attempt, "snapshot_ids_invalid")
            return None
        trend_start_snapshot_id = candidate.get("trend_start_snapshot_id")
        prior_snapshot_id = candidate.get("prior_snapshot_id")
        entry_snapshot_id = candidate.get("entry_snapshot_id")
        if (
            trend_start_snapshot_id != snapshot_ids[0]
            or prior_snapshot_id != snapshot_ids[-2]
            or entry_snapshot_id != snapshot_ids[-1]
        ):
            logger.warning(
                "staircase start/prior/current snapshot 증거가 불일치합니다 - "
                "condition=%s",
                condition_id,
            )
            self._fail_attempt(attempt, "snapshot_anchors_mismatch")
            return None

        can_enter, reason = self.repo.can_reenter(
            condition_id, self.config.reentry_cooldown_hours
        )
        if not can_enter:
            logger.info("condition 재진입 skip - condition=%s reason=%s", condition_id, reason)
            self._fail_attempt(attempt, f"condition_{reason}")
            return None
        can_enter_event, event_reason = self.repo.can_enter_event(
            event_id, self.config.reentry_cooldown_hours
        )
        if not can_enter_event:
            logger.info("event 진입 skip - event=%s reason=%s", event_id, event_reason)
            self._fail_attempt(attempt, f"event_{event_reason}")
            return None
        if self.repo.get_position_count() >= self.config.max_positions:
            logger.info("최대 포지션 수 %s 도달", self.config.max_positions)
            self._fail_attempt(attempt, "max_positions")
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
            self._fail_attempt(attempt, "max_open_notional")
            return None

        end_date = parse_end_date(candidate.get("end_date"))
        prices = candidate.get("trend_prices")
        gaps = candidate.get("trend_gap_minutes")
        timestamps = candidate.get("trend_snapshot_timestamps")
        if (
            not isinstance(prices, list)
            or not isinstance(gaps, list)
            or not isinstance(timestamps, list)
            or len(timestamps) != steps + 1
            or any(not isinstance(value, datetime) for value in timestamps)
        ):
            logger.warning("staircase 가격/간격 증거가 없습니다 - condition=%s", condition_id)
            self._fail_attempt(attempt, "staircase_vectors_invalid")
            return None
        run_id = current_run_id()
        if not run_id:
            logger.error("current RunAudit ID 없이 simulation BUY를 기록할 수 없습니다")
            self._fail_attempt(attempt, "current_run_missing")
            return None

        attempt["fresh_attempted"] = 1
        attempt["fresh_observed_at"] = datetime.now(timezone.utc).replace(
            tzinfo=None
        )
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
            self._fail_attempt(attempt, "fresh_book_unavailable")
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
        attempt.update(
            {
                "fresh_best_bid": best_bid,
                "fresh_best_ask": best_ask,
                "fresh_spread": spread if math.isfinite(spread) else None,
                "fresh_depth_shares": (
                    depth_shares if math.isfinite(depth_shares) else None
                ),
                "fresh_depth_limit_price": depth_limit,
            }
        )
        if (
            best_bid is None
            or best_ask is None
            or depth_limit is None
            or best_bid > best_ask + 1e-9
            or not math.isfinite(spread)
            or spread < 0
            or not math.isclose(spread, best_ask - best_bid, rel_tol=0, abs_tol=1e-6)
            or spread > self.config.max_spread + 1e-9
            or not math.isfinite(depth_shares)
            or depth_shares < 0
            or best_ask > self.config.entry.prob_max + 1e-9
            or depth_limit < best_ask - 1e-9
        ):
            logger.info(
                "fresh BUY book gate 실패 - condition=%s bid=%s ask=%s "
                "spread=%s depth=%s limit=%s",
                condition_id,
                best_bid,
                best_ask,
                spread,
                depth_shares,
                depth_limit,
            )
            self._fail_attempt(attempt, "fresh_book_gate_failed")
            return None

        # Bind the final staircase point to the same single CLOB book used for
        # spread/depth gates. A separate, earlier midpoint read would permit a
        # fast reversal between two network observations (TOCTOU).
        current_yes = (best_bid + best_ask) / 2.0
        if _valid_book_price(current_yes) is None:
            self._fail_attempt(attempt, "fresh_midpoint_invalid")
            return None
        fresh_prices = list(prices)
        if len(fresh_prices) != steps + 1:
            self._fail_attempt(attempt, "fresh_price_vector_length")
            return None
        fresh_prices[-1] = current_yes

        buy_shares = self.config.buy_amount_usdc / depth_limit
        required_shares = (
            self.config.min_order_size + self.config.min_order_buffer_shares
        )
        required_depth = buy_shares * self.config.depth_safety_multiple
        if (
            buy_shares + 1e-9 < required_shares
            or depth_shares + 1e-9 < required_depth
        ):
            logger.info(
                "fresh BUY size/depth gate 실패 - condition=%s shares=%.6f "
                "minimum=%.6f depth=%.6f required_depth=%.6f",
                condition_id,
                buy_shares,
                required_shares,
                depth_shares,
                required_depth,
            )
            self._fail_attempt(attempt, "fresh_depth_or_size_failed")
            return None

        # Anchor the entry after the book read, and recompute the last cadence
        # gap against that exact observation instead of reusing the older
        # Gamma snapshot clock.
        now = datetime.now(timezone.utc)
        persisted_timestamps = [
            (
                value.astimezone(timezone.utc)
                if value.tzinfo is not None
                else value.replace(tzinfo=timezone.utc)
            )
            for value in timestamps
        ]
        fresh_timestamps = list(persisted_timestamps)
        fresh_timestamps[-1] = now
        fresh_gaps = [
            (
                fresh_timestamps[index + 1] - fresh_timestamps[index]
            ).total_seconds()
            / 60.0
            for index in range(steps)
        ]
        hours_left = get_hours_until_resolution(end_date, now)
        decision = evaluate_entry(
            fresh_prices,
            fresh_gaps,
            hours_left,
            self.config.entry,
        )
        if not decision.should_enter:
            logger.info(
                "final entry-clock 재검증 실패 - condition=%s reason=%s",
                condition_id,
                decision.reason,
            )
            self._fail_attempt(
                attempt, f"fresh_staircase_{decision.reason}"
            )
            return None
        result = self.clob.place_limit_order(
            token_id=token_id,
            price=depth_limit,
            size=buy_shares,
            side="BUY",
        )
        if not (result.get("success") or result.get("orderID")):
            logger.error("simulation BUY 기록 실패: %s", result)
            self._fail_attempt(attempt, "simulation_order_record_failed")
            return None

        def finite_optional(value):
            try:
                normalized = float(value)
            except (TypeError, ValueError):
                return None
            return normalized if math.isfinite(normalized) else None

        deadline = (
            end_date.astimezone(timezone.utc).replace(tzinfo=None)
            if end_date is not None
            else None
        )
        signal_timestamp = persisted_timestamps[-1].replace(
            tzinfo=None
        )
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
            buy_timestamp=now.replace(tzinfo=None),
            buy_probability=current_yes,
            status=TradeStatus.HOLDING,
            entry_reason=decision.reason,
            strategy_name=STRATEGY_NAME,
            mode="sim",
            entry_run_id=run_id,
            market_end_date=deadline,
            hours_until_resolution_at_buy=hours_left,
            liquidity_at_buy=finite_optional(candidate.get("liquidity")),
            volume_24h_at_buy=finite_optional(candidate.get("volume_24h")),
            market_tags=candidate.get("market_tags", ""),
            prior_yes_price_at_entry=fresh_prices[-2],
            yes_price_at_buy=current_yes,
            stop_price_at_entry=None,
            take_profit_price_at_entry=None,
            entry_prob_min_at_buy=self.config.entry.prob_min,
            entry_prob_max_at_buy=self.config.entry.prob_max,
            entry_hours_min_at_buy=self.config.entry.min_hours_to_resolution,
            entry_hours_max_at_buy=None,
            entry_time_reference="endDate",
            entry_deadline_at_buy=deadline,
            hours_until_entry_deadline_at_buy=hours_left,
            sports_phase_at_buy="excluded_by_frozen_tags",
            trend_start_snapshot_id_at_entry=trend_start_snapshot_id,
            prior_snapshot_id_at_entry=prior_snapshot_id,
            entry_snapshot_id=entry_snapshot_id,
            signal_timestamp_at_entry=signal_timestamp,
            trend_snapshot_ids_json=json.dumps(snapshot_ids),
            trend_snapshot_timestamps_json=json.dumps(
                [
                    value.astimezone(timezone.utc).isoformat()
                    for value in timestamps
                ]
            ),
            trend_persisted_prices_json=json.dumps(prices),
            trend_decision_prices_json=json.dumps(fresh_prices),
            trend_gap_minutes_json=json.dumps(gaps),
            trend_decision_timestamps_json=json.dumps(
                [value.isoformat() for value in fresh_timestamps]
            ),
            trend_decision_gap_minutes_json=json.dumps(fresh_gaps),
            decision_observed_at_at_entry=now.replace(tzinfo=None),
            decision_price_source_at_entry="clob_single_order_book_midpoint",
            trend_start_yes_price_at_entry=decision.start_price,
            confirmation_steps_at_entry=decision.confirmation_steps,
            cumulative_move_at_entry=decision.cumulative_move,
            min_step_move_at_entry=decision.min_step_move,
            max_step_move_at_entry=decision.max_step_move,
            min_snapshot_gap_minutes_at_entry=decision.min_gap_minutes,
            max_snapshot_gap_minutes_at_entry=decision.max_gap_minutes,
            signal_best_bid_at_entry=finite_optional(
                candidate.get("signal_best_bid")
            ),
            signal_best_ask_at_entry=finite_optional(
                candidate.get("signal_best_ask")
            ),
            signal_spread_at_entry=finite_optional(
                candidate.get("signal_spread")
            ),
            hold_minutes_target_at_entry=self.config.entry.hold_minutes,
            best_bid_at_buy=best_bid,
            best_ask_at_buy=best_ask,
            spread_at_buy=spread,
            book_depth_shares_at_buy=depth_shares,
            depth_limit_price_at_buy=depth_limit,
        )
        logger.info(
            "Micro-Cascade simulation BUY - Trade #%s arm=%s "
            "steps=%s cumulative=%.4f ask=%.4f limit=%.4f",
            trade.id,
            self.config.arm_name,
            decision.confirmation_steps,
            decision.cumulative_move,
            best_ask,
            depth_limit,
        )
        attempt.update(
            {
                "fresh_gate_passed": 1,
                "fresh_fail_reason": "selected",
                "execution_selected": 1,
                "trade_id": trade.id,
                "fresh_observed_at": now.replace(tzinfo=None),
            }
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
            exit_run_id=current_run_id(),
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
            promotion_eligible=0,
            promotion_exclusion_reason="resolved_before_valid_60_75m_exit",
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

    def _handle_exit_quote_unavailable(self, trade, error) -> bool:
        if self.gamma is None:
            logger.warning(
                "exit quote unavailable and Gamma client not injected - "
                "trade=%s error=%s",
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
            "exit quote unavailable; closed+final payout 증거 없음 - "
            "condition=%s error=%s",
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

        Kiwi deliberately does not retry a smaller SELL after a balance
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
        """Exit at the first observed cycle at or after the immutable 60m target."""
        preflight_now = datetime.now(timezone.utc)
        hold_target = getattr(trade, "hold_minutes_target_at_entry", None)
        if hold_target is None:
            hold_target = self.config.entry.hold_minutes
        decision = evaluate_exit(trade.buy_timestamp, preflight_now, hold_target)
        if not decision.should_exit:
            logger.debug(
                "Micro-Cascade 보유 유지 - trade=%s reason=%s elapsed=%s",
                trade.id,
                decision.reason,
                decision.elapsed_minutes,
            )
            return False

        best_bid = self._fresh_exit_bid(trade.token_id)
        if best_bid is None:
            return self._handle_exit_quote_unavailable(
                trade, "fresh best bid unavailable"
            )
        # The observation clock is the instant the network quote has actually
        # returned, not the instant immediately before the request.
        now = datetime.now(timezone.utc)
        decision = evaluate_exit(trade.buy_timestamp, now, hold_target)
        if not decision.should_exit or decision.delay_minutes is None:
            logger.warning(
                "fresh exit quote 후 clock evidence가 유효하지 않습니다 - trade=%s",
                trade.id,
            )
            return False
        current_yes = best_bid
        result = self.clob.place_limit_order(
            token_id=trade.token_id,
            price=best_bid,
            size=trade.buy_shares,
            side="SELL",
        )
        if not (result.get("success") or result.get("orderID")):
            logger.error("simulation SELL 기록 실패: %s", result)
            return False

        common = {
            "exit_run_id": current_run_id(),
            "sell_price": best_bid,
            "sell_shares": trade.buy_shares,
            "sell_order_id": result.get("orderID"),
            "sell_timestamp": now.replace(tzinfo=None),
            "sell_probability": current_yes,
            "yes_price_at_exit": current_yes,
            "best_bid_at_exit": best_bid,
            # Exit outcome is defined only by the executable bid. Requiring an
            # ask would censor an otherwise valid one-sided exit observation.
            "best_ask_at_exit": None,
            "spread_at_exit": None,
            "hold_minutes_observed_at_exit": decision.elapsed_minutes,
            "exit_delay_minutes": decision.delay_minutes,
            "promotion_eligible": int(
                decision.delay_minutes
                <= self.config.entry.max_exit_delay_minutes + 1e-9
            ),
            "promotion_exclusion_reason": (
                None
                if decision.delay_minutes
                <= self.config.entry.max_exit_delay_minutes + 1e-9
                else "exit_observed_after_75m_window"
            ),
            "sell_confirmed_size": None,
            "sell_confirmed_vwap": None,
            "sell_confirmed_fee_usdc": None,
            "sell_fill_matched_at": None,
        }
        if self.mode == "sim":
            hypothetical_pnl = (best_bid - trade.buy_price) * trade.buy_shares
            self.repo.update_trade(
                trade.id,
                **common,
                status=TradeStatus.COMPLETED,
                exit_reason="time_exit_simulation_hypothetical",
                realized_pnl=None,
                hypothetical_pnl=hypothetical_pnl,
                pnl_basis="simulation_hypothetical_fresh_best_bid_fees_excluded",
            )
            logger.info(
                "Micro-Cascade time exit - Trade #%s elapsed=%.2fmin "
                "delay=%.2fmin bid=%.4f hypothetical P&L=$%.4f",
                trade.id,
                decision.elapsed_minutes,
                decision.delay_minutes,
                best_bid,
                hypothetical_pnl,
            )
            return True

        # Unreachable while source-level research-only guards are active. Keep
        # the inherited exact accepted-order boundary intact for a future reviewed
        # promotion: acceptance is PENDING_SELL, never realized performance.
        self.repo.update_trade(
            trade.id,
            **common,
            status=TradeStatus.PENDING_SELL,
            exit_reason="time_exit_pending_confirmed_fill",
            realized_pnl=None,
            hypothetical_pnl=None,
            pnl_basis=None,
        )
        logger.info(
            "Micro-Cascade time exit order accepted - Trade #%s "
            "elapsed=%.2fmin delay=%.2fmin; exact fill pending",
            trade.id,
            decision.elapsed_minutes,
            decision.delay_minutes,
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
