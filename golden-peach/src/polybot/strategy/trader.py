"""Order execution and evidence-safe settlement for Golden Peach."""

from __future__ import annotations

import datetime as datetime_module
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import logging
import math
import re
from typing import Iterable, Mapping, Optional

from polybot_observability import (
    ClobResponseUnavailableError,
    SubmissionEvidenceError,
)

from ..api.clob_client import (
    ClobClientWrapper,
    ClobResolutionProof,
    PreSubmissionContractError,
)
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.models import (
    STOP_SELL_ISOLATION_REASONS,
    STOP_SELL_LEDGER_QUARANTINE_REASON,
    STOP_SELL_QUARANTINE_REASON,
    STRATEGY_NAME,
    TradeStatus,
)
from ..db.repository import ExactFillEvidence, TradeRepository
from .filters import get_aligned_binary_outcomes, get_event, get_proven_resolution
from .scanner import (
    get_hours_since_game_start,
    get_source_regulation_minute,
    parse_end_date,
)


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
_MAX_SIGNED_SELL_DUST_SHARES = 0.01 + _FILL_SIZE_TOLERANCE
_EXIT_SELL_FAILURE_RETRY_PREFIX = "exit_sell_failure_retrying:"


def _sdk_sellable_shares(holding_shares: float) -> float:
    """Return the two-decimal SELL envelope the current SDK can sign.

    The live SDK floors SELL shares to two decimals. Walking the book for the
    finer BUY fill first can falsely report insufficient depth even when every
    signable share is executable. The sub-cent-share remainder is explicit
    unsellable dust and is handled by the existing lifecycle evidence path.
    """
    if not math.isfinite(holding_shares) or holding_shares <= 0:
        raise ValueError("holding shares must be finite and positive")
    sellable = float(
        Decimal(str(holding_shares)).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
    )
    residual = holding_shares - sellable
    if (
        sellable <= 0
        or residual < -_FILL_SIZE_TOLERANCE
        or residual >= _MAX_SIGNED_SELL_DUST_SHARES
    ):
        raise ValueError("holding cannot be represented by one safe SDK SELL")
    return sellable


def _sdk_sell_submission_shares(sellable_shares: float) -> float:
    """Nudge a two-decimal SELL envelope above its binary-float boundary.

    py-clob-client-v2 floors SELL shares to two decimals while signing.  A
    binary float such as ``5.10`` can arrive as ``5.099999...`` and be floored
    a second time to ``5.09``.  ``nextafter`` keeps the signed order at the
    intended two-decimal envelope without ever increasing it by a venue share
    quantum.
    """
    if not math.isfinite(sellable_shares) or sellable_shares <= 0:
        raise ValueError("sellable shares must be finite and positive")
    return math.nextafter(sellable_shares, math.inf)


def _entry_stop_price(entry_vwap: float, config: TradingConfig) -> float:
    """Return the common absolute-or-entry-drawdown protective trigger."""
    try:
        normalized_entry = float(entry_vwap)
    except (TypeError, ValueError):
        normalized_entry = math.nan
    if not math.isfinite(normalized_entry) or not 0 < normalized_entry < 1:
        return float(config.entry.stop_price)
    return round(
        max(
            float(config.entry.stop_price),
            normalized_entry - float(config.entry.max_entry_drawdown),
        ),
        6,
    )


def _effective_stop_price(trade: object, config: TradingConfig) -> float:
    """Protect legacy holdings without rewriting their historical entry row."""
    stored = getattr(trade, "stop_price_at_entry", None)
    try:
        stored_stop = float(stored)
    except (TypeError, ValueError):
        stored_stop = float(config.entry.stop_price)
    if not math.isfinite(stored_stop) or not 0 < stored_stop < 1:
        stored_stop = float(config.entry.stop_price)
    entry_vwap = getattr(trade, "buy_confirmed_vwap", None)
    if entry_vwap is None:
        entry_vwap = getattr(trade, "buy_price", None)
    return max(stored_stop, _entry_stop_price(entry_vwap, config))


def _orphan_catalog_identity_matches(
    *,
    token_id: str,
    episode: object,
    snapshot: object,
    catalog: object,
) -> bool:
    """Prove selected winner token, condition, event, and snapshot alignment."""
    episode_outcome = str(getattr(episode, "outcome", "") or "").strip()
    neg_risk = getattr(catalog, "neg_risk", None) in (1, True)
    aligned = get_aligned_binary_outcomes(
        {
            "outcomes": getattr(catalog, "outcomes_json", None),
            "outcomePrices": getattr(catalog, "outcome_prices_json", None),
            "clobTokenIds": getattr(catalog, "token_ids_json", None),
            "negRisk": neg_risk,
            # Direct MLB/NHL labels share the same two-outcome alignment
            # contract.  MarketCatalog predates sport_family, but negRisk=false
            # unambiguously distinguishes these rows from soccer Yes/No rows.
            "sportFamily": "soccer" if neg_risk else "mlb",
        }
    )
    selected = [
        item
        for item in aligned
        if item["token_id"] == token_id and item["outcome"] == episode_outcome
    ]
    episode_condition = str(
        getattr(episode, "condition_id", "") or ""
    ).strip()
    episode_event = str(getattr(episode, "event_id", "") or "").strip()
    snapshot_side = str(getattr(snapshot, "outcome_side", "") or "").upper()
    snapshot_result_kind = str(
        getattr(snapshot, "result_kind", "") or ""
    ).upper()
    return (
        len(aligned) == 2
        and len(selected) == 1
        and episode_condition
        and episode_event
        and bool(episode_outcome)
        and str(getattr(catalog, "condition_id", "") or "").strip()
        == episode_condition
        and str(getattr(catalog, "event_id", "") or "").strip()
        == episode_event
        and str(getattr(snapshot, "condition_id", "") or "").strip()
        == episode_condition
        and str(getattr(snapshot, "token_id", "") or "").strip() == token_id
        and str(getattr(snapshot, "outcome", "") or "").strip()
        == episode_outcome
        and snapshot_side == episode_outcome.upper()
        and snapshot_result_kind in {"HOME", "DRAW", "AWAY"}
        and getattr(snapshot, "id", None)
        == getattr(episode, "entry_snapshot_id", None)
    )


def _catalog_yes_probability(catalog: object) -> Optional[float]:
    aligned = get_aligned_binary_outcomes(
        {
            "outcomes": getattr(catalog, "outcomes_json", None),
            "outcomePrices": getattr(catalog, "outcome_prices_json", None),
            "clobTokenIds": getattr(catalog, "token_ids_json", None),
            "negRisk": getattr(catalog, "neg_risk", None) in (1, True),
            "sportFamily": "soccer",
        }
    )
    for item in aligned:
        if item.get("outcome") == "Yes":
            return float(item["probability"])
    return None


def _ledger_timestamp(value: object, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


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



class Trader:
    """Trade only bot-owned direct result tokens under the Peach contract."""

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
        self.local_untracked_buy_reservations = 0
        self.emergency_sell_submissions = 0
        self.emergency_sell_guard_blocks = 0
        self._cycle_markets: dict[str, dict] = {}
        self.last_entry_outcome_reason: Optional[str] = None
        # False means this candidate is proven not to have reached a venue
        # POST. True is deliberately conservative: the result may need ledger
        # reconciliation before the first-observation episode can be retried.
        self.last_entry_may_have_reached_venue = False

    def set_cycle_markets(self, markets: Iterable[Mapping]) -> None:
        """Cache the cursor-complete live sweep for stop lifecycle preflight."""
        indexed: dict[str, dict] = {}
        for market in markets:
            if not isinstance(market, Mapping):
                continue
            condition_id = str(
                market.get("conditionId") or market.get("condition_id") or ""
            ).strip()
            if condition_id and condition_id not in indexed:
                indexed[condition_id] = dict(market)
        self._cycle_markets = indexed

    @staticmethod
    def signable_sell_shares(trade) -> float:
        return _sdk_sellable_shares(float(trade.buy_shares))

    @staticmethod
    def effective_stop_price(trade, config: TradingConfig) -> float:
        return _effective_stop_price(trade, config)

    def _source_minute_for_trade(self, trade) -> tuple[Optional[float], str]:
        market = self._cycle_markets.get(str(trade.condition_id))
        if isinstance(market, dict):
            minute, reason = get_source_regulation_minute(get_event(market))
            if minute is not None:
                return minute, reason
        event_id = str(getattr(trade, "event_id", "") or "").strip()
        if self.gamma is not None and event_id:
            try:
                event = self.gamma.get_event_by_id(event_id)
            except Exception as error:
                logger.warning(
                    "source-clock fallback failed - trade=%s error=%s",
                    trade.id,
                    type(error).__name__,
                )
            else:
                if isinstance(event, Mapping):
                    minute, reason = get_source_regulation_minute(dict(event))
                    if minute is not None:
                        return minute, f"GAMMA_EVENT_FALLBACK:{reason}"
        return None, "CURRENT_SOURCE_CLOCK_UNPROVEN"

    def _exit_signal(self, trade, walk) -> tuple[Optional[str], float, Optional[float]]:
        """Evaluate TP, late half-target, then pre-80-minute stop."""
        raw_entry = getattr(trade, "buy_confirmed_vwap", None)
        if raw_entry is None:
            raw_entry = getattr(trade, "buy_price", None)
        try:
            entry_vwap = float(raw_entry)
        except (TypeError, ValueError):
            return None, math.nan, None
        if not math.isfinite(entry_vwap) or not 0 < entry_vwap < 1:
            return None, math.nan, None
        take_profit = getattr(trade, "take_profit_delta_at_buy", None)
        stop_loss = getattr(trade, "stop_loss_delta_at_buy", None)
        late_minute = getattr(trade, "late_exit_minute_at_buy", None)
        try:
            take_profit = float(
                take_profit
                if take_profit is not None
                else self.config.entry.take_profit_delta
            )
            stop_loss = float(
                stop_loss
                if stop_loss is not None
                else self.config.entry.stop_loss_delta
            )
            late_minute = float(
                late_minute
                if late_minute is not None
                else self.config.entry.late_exit_minute
            )
        except (TypeError, ValueError):
            return None, math.nan, None
        source_minute, _clock_reason = self._source_minute_for_trade(trade)
        full_exit_vwap = float(walk.vwap)
        normal_target = min(0.999, entry_vwap + take_profit)
        if full_exit_vwap + 1e-9 >= normal_target:
            return "take_profit", normal_target, source_minute
        late_target = min(
            0.999,
            entry_vwap + take_profit * self.config.entry.late_profit_fraction,
        )
        if (
            source_minute is not None
            and source_minute + 1e-9 >= late_minute
            and full_exit_vwap + 1e-9 >= late_target
        ):
            return "late_half_target", late_target, source_minute
        stop_trigger = max(0.01, entry_vwap - stop_loss)
        # A missing clock cannot prove that the match is still before minute
        # 80. Fail closed instead of accidentally creating a forbidden late
        # stop after the strategy has switched to hold-to-resolution.
        stop_active = (
            source_minute is not None
            and source_minute < self.config.entry.stop_cutoff_minute - 1e-9
        )
        if stop_active and float(walk.best_bid) <= stop_trigger + 1e-9:
            return "absolute_stop", stop_trigger, source_minute
        return None, stop_trigger, source_minute

    def _reject_entry(self, reason: str) -> None:
        self.last_entry_outcome_reason = str(reason)
        return None

    def execute_buy(self, candidate: dict) -> Optional[int]:
        """Revalidate the exact $5 walk, then submit a FOK BUY."""
        self.last_entry_outcome_reason = None
        self.last_entry_may_have_reached_venue = False
        if self.buying_disabled:
            return self._reject_entry("cycle_buying_disabled")
        condition_id = str(candidate["condition_id"])
        token_id = str(candidate["token_id"])
        outcome = str(candidate.get("outcome") or "").strip()
        result_kind = str(candidate.get("result_kind") or "").strip()
        if not outcome or result_kind not in {"HOME", "DRAW", "AWAY"}:
            logger.error(
                "whole-match result identity missing: condition=%s outcome=%s result=%s",
                condition_id,
                outcome,
                result_kind,
            )
            return self._reject_entry("whole_match_result_identity_missing")
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
            return self._reject_entry("current_run_entry_snapshot_missing")
        episode_id = candidate.get("entry_episode_id")
        normalized_episode_id = (
            episode_id
            if not isinstance(episode_id, bool) and isinstance(episode_id, int)
            else None
        )
        raw_event_id = candidate.get("event_id")
        if raw_event_id is None or not str(raw_event_id).strip():
            logger.warning(
                "event_id 없는 진입 후보를 fail-closed 처리합니다 - condition=%s",
                condition_id,
            )
            return self._reject_entry("event_id_missing")
        event_id = str(raw_event_id).strip()
        can_enter, reason = self.repo.can_reenter(
            condition_id,
            self.config.reentry_cooldown_hours,
            event_id=event_id,
            token_id=token_id,
        )
        if not can_enter:
            logger.info("재진입 skip - condition=%s reason=%s", condition_id, reason)
            return self._reject_entry(f"reentry_{reason}")
        capacity = self.repo.get_entry_capacity_state()
        total_reserved = (
            capacity["total_reserved"] + self.local_untracked_buy_reservations
        )
        if total_reserved >= self.config.max_positions:
            logger.info(
                "최대 진입 capacity %s 도달 - open=%s untracked_buy=%s "
                "local_untracked=%s",
                self.config.max_positions,
                capacity["open_positions"],
                capacity["untracked_buy_reservations"],
                self.local_untracked_buy_reservations,
            )
            return self._reject_entry("max_capacity_reserved")
        if (
            self.repo.get_event_position_count(event_id)
            >= self.config.max_event_positions
        ):
            logger.info(
                "event 포지션 한도 도달 - event=%s limit=%s",
                event_id,
                self.config.max_event_positions,
            )
            return self._reject_entry("event_capacity_reserved")

        now = datetime.now(timezone.utc)
        experiment_start = parse_end_date(self.config.experiment_start_utc)
        experiment_end = parse_end_date(self.config.experiment_entry_end_utc)
        if (
            experiment_start is None
            or experiment_end is None
            or not (experiment_start <= now < experiment_end)
        ):
            logger.info("frozen entry period is closed - condition=%s", condition_id)
            return self._reject_entry("frozen_entry_period_closed")
        in_play_hours = get_hours_since_game_start(
            candidate.get("game_start_time"), now=now
        )
        if (
            in_play_hours is None
            or not self.config.entry.hours_min - 1e-9
            <= in_play_hours
            <= self.config.entry.hours_max + 1e-9
        ):
            logger.info(
                "in-play window revalidation failed - condition=%s hours=%s",
                condition_id,
                in_play_hours,
            )
            return self._reject_entry("in_play_window_revalidation_failed")
        market = self._cycle_markets.get(condition_id)
        source_minute, source_clock_reason = (
            get_source_regulation_minute(get_event(market))
            if isinstance(market, dict)
            else (None, "CURRENT_CYCLE_MARKET_MISSING")
        )
        if (
            source_minute is None
            or source_minute < 0
            or source_minute > self.config.entry.max_source_minute + 1e-9
        ):
            logger.info(
                "kickoff source-clock revalidation failed - condition=%s "
                "minute=%s reason=%s",
                condition_id,
                source_minute,
                source_clock_reason,
            )
            return self._reject_entry("kickoff_source_clock_revalidation_failed")
        event_token_ids = [
            str(value).strip()
            for value in candidate.get("event_token_ids", [])
            if str(value).strip()
        ]
        if len(event_token_ids) != 6 or len(set(event_token_ids)) != 6:
            return self._reject_entry("six_token_fresh_revalidation_identity_gap")
        try:
            fresh_walks = self.clob.get_buy_book_walks(
                event_token_ids,
                notional_usdc=self.config.buy_amount_usdc,
            )
        except Exception as error:
            logger.warning(
                "fresh exact-book walk failed - condition=%s error=%s",
                condition_id,
                type(error).__name__,
            )
            return self._reject_entry(
                f"fresh_exact_book_{type(error).__name__}"
            )
        if len(fresh_walks) != 6:
            return self._reject_entry("fresh_six_token_book_coverage_gap")
        fresh_ranked = []
        for fresh_token in event_token_ids:
            item = fresh_walks.get(fresh_token)
            if (
                item is None
                or item.best_bid is None
                or item.spread is None
                or item.spread > self.config.entry.max_entry_spread + 1e-9
            ):
                return self._reject_entry("fresh_six_token_book_contract_gap")
            fresh_ranked.append(
                ((item.best_bid + item.best_ask) / 2.0, fresh_token, item)
            )
        fresh_ranked.sort(key=lambda item: (-item[0], item[1]))
        fresh_margin = fresh_ranked[0][0] - fresh_ranked[1][0]
        if (
            fresh_ranked[0][1] != token_id
            or fresh_margin + 1e-9 < self.config.entry.min_leader_margin
        ):
            logger.info(
                "fresh six-token leader changed - expected=%s actual=%s margin=%.6f",
                token_id[:16],
                fresh_ranked[0][1][:16],
                fresh_margin,
            )
            return self._reject_entry("fresh_six_token_leader_changed")
        walk = fresh_ranked[0][2]
        if not (
            self.config.entry.prob_min - 1e-9
            <= walk.vwap
            <= self.config.entry.prob_max + 1e-9
        ):
            logger.info(
                "fresh exact VWAP left arm band - condition=%s vwap=%.4f band=%.3f-%.3f",
                condition_id,
                walk.vwap,
                self.config.entry.prob_min,
                self.config.entry.prob_max,
            )
            return self._reject_entry("fresh_exact_vwap_left_arm")
        required = self.config.min_order_size + self.config.min_order_buffer_shares
        if walk.shares + 1e-9 < required:
            logger.warning(
                "min-order buffer 미달 - condition=%s shares=%.6f required=%.6f",
                condition_id,
                walk.shares,
                required,
            )
            return self._reject_entry("minimum_order_shares_unavailable")
        if not 0 < walk.limit_price < 1:
            logger.warning(
                "FOK limit price is not orderable - condition=%s price=%s",
                condition_id,
                walk.limit_price,
            )
            return self._reject_entry("fok_limit_not_orderable")

        logger.info(
            "Golden Peach FOK BUY: '%s' result=%s side=%s exact_vwap=%.2f%% "
            "best_ask=%.2f%% limit=%.2f%% shares=%.4f",
            str(candidate.get("question") or "")[:60],
            result_kind,
            outcome,
            walk.vwap * 100,
            walk.best_ask * 100,
            walk.limit_price * 100,
            walk.shares,
        )
        # Treat an arbitrary exception from the submission wrapper as possibly
        # post-POST. Only the explicit local pre-submission contract exception
        # proves that no order could have reached the venue.
        if normalized_episode_id is not None:
            self.repo.mark_entry_episode_execution(
                normalized_episode_id,
                state="SUBMISSION_IN_PROGRESS",
                reason="fresh_book_validated_before_submission_wrapper",
            )
        self.last_entry_may_have_reached_venue = True
        try:
            result = self.clob.place_fok_buy(
                token_id=token_id,
                amount_usdc=self.config.buy_amount_usdc,
                limit_price=walk.limit_price,
                max_limit_price=self.config.entry.prob_max,
            )
        except PreSubmissionContractError:
            self.last_entry_may_have_reached_venue = False
            raise
        if not (result.get("success") or result.get("orderID")):
            if result.get("submission_outcome_unknown"):
                self.local_untracked_buy_reservations += 1
                # An unknown POST can already be real exposure.  Reserve it
                # immediately and prevent every later candidate in this cycle
                # from issuing another irreversible BUY.
                self.buying_disabled = True
                rejection_reason = "buy_submission_outcome_unknown"
            else:
                rejection_reason = "buy_order_rejected"
            if is_balance_allowance_error(result):
                self.buying_disabled = True
                logger.warning(
                    "collateral 잔고/allowance 부족으로 이번 cycle의 남은 매수를 중단합니다"
                )
            else:
                logger.error("매수 주문 실패: %s", result)
            return self._reject_entry(rejection_reason)
        try:
            submitted_shares = float(result["requested_size"])
        except (KeyError, TypeError, ValueError):
            logger.error("FOK BUY 제출 수량 증거가 없어 trade 생성을 중단합니다")
            return self._reject_entry("buy_requested_size_evidence_missing")
        if not math.isfinite(submitted_shares) or submitted_shares <= 0:
            logger.error(
                "FOK BUY 제출 수량 증거가 유효하지 않습니다: %s",
                submitted_shares,
            )
            return self._reject_entry("buy_requested_size_invalid")

        trade = self.repo.create_trade(
            entry_episode_id=normalized_episode_id,
            condition_id=condition_id,
            market_slug=candidate.get("market_slug", ""),
            question=candidate.get("question", ""),
            event_id=event_id,
            event_slug=candidate.get("event_slug"),
            outcome=outcome,
            outcome_side=str(candidate.get("outcome_side") or outcome).upper(),
            result_kind=result_kind,
            token_id=token_id,
            buy_price=walk.vwap,
            buy_amount=self.config.buy_amount_usdc,
            buy_shares=submitted_shares,
            buy_order_id=result.get("orderID"),
            buy_timestamp=datetime.utcnow(),
            buy_probability=walk.vwap,
            status=(
                TradeStatus.HOLDING
                if self.mode == "sim"
                else TradeStatus.PENDING_BUY
            ),
            entry_reason=(
                "unique_six_token_kickoff_leader_exact_5_usdc_fok:"
                f"{str(candidate.get('candidate_kind') or result_kind)}"
            ),
            strategy_name=STRATEGY_NAME,
            mode=self.mode,
            # Legacy column names: for this strategy these values are the
            # authoritative game start and elapsed in-play hours.
            market_end_date=candidate.get("game_start_time"),
            hours_until_resolution_at_buy=in_play_hours,
            liquidity_at_buy=candidate.get("liquidity"),
            volume_24h_at_buy=candidate.get("volume_24h"),
            market_tags=candidate.get("market_tags", ""),
            prior_yes_price_at_entry=None,
            yes_price_at_buy=candidate.get("yes_probability"),
            stop_price_at_entry=_entry_stop_price(walk.vwap, self.config),
            entry_prob_min_at_buy=self.config.entry.prob_min,
            entry_prob_max_at_buy=self.config.entry.prob_max,
            entry_hours_min_at_buy=self.config.entry.hours_min,
            entry_hours_max_at_buy=self.config.entry.hours_max,
            prior_snapshot_id_at_entry=None,
            entry_snapshot_id=entry_snapshot_id,
            source_elapsed_minutes_at_buy=source_minute,
            take_profit_delta_at_buy=self.config.entry.take_profit_delta,
            stop_loss_delta_at_buy=self.config.entry.stop_loss_delta,
            late_exit_minute_at_buy=self.config.entry.late_exit_minute,
            best_bid_at_buy=walk.best_bid,
            best_ask_at_buy=walk.best_ask,
            spread_at_buy=walk.spread,
        )
        logger.info(
            "매수 주문 접수: Trade #%s Order=%s", trade.id, result.get("orderID")
        )
        self.last_entry_outcome_reason = "trade_created"
        return trade.id

    def recover_orphan_buys(self) -> dict:
        """Reconstruct one ledger-proven BUY lost between POST and Trade commit.

        The recovery is deliberately narrow: one submission per token, a
        pre-existing first-observation episode, exact condition/token/snapshot
        identity, terminal positive fill evidence, and complete fee evidence.
        Anything else remains reserved and blocks new entry for operator review.
        """
        stats = {
            "checked": 0,
            "recovered": 0,
            "evidence_gaps": 0,
            "identity_gaps": 0,
            "duplicate_token_submissions": 0,
        }
        submissions = self.repo.get_untracked_buy_submissions()
        counts: dict[str, int] = {}
        for submission in submissions:
            token_id = str(submission.get("token_id") or "")
            counts[token_id] = counts.get(token_id, 0) + 1

        for submission in submissions:
            stats["checked"] += 1
            token_id = str(submission.get("token_id") or "").strip()
            order_id = str(submission.get("order_id") or "").strip()
            if counts.get(token_id, 0) != 1:
                stats["duplicate_token_submissions"] += 1
                logger.critical(
                    "동일 token의 orphan BUY submission이 복수라 자동 복구를 "
                    "중단합니다 - token=%s count=%s",
                    token_id[:16],
                    counts.get(token_id, 0),
                )
                continue
            if not order_id:
                stats["evidence_gaps"] += 1
                continue
            evidence = self.repo.get_exact_buy_fill_evidence(order_id)
            if not (
                evidence.has_reconciled_executed_fill
                and evidence.fee_complete
                and evidence.confirmed_size is not None
                and evidence.confirmed_vwap is not None
                and evidence.confirmed_fee_usdc is not None
            ):
                stats["evidence_gaps"] += 1
                logger.warning(
                    "orphan BUY는 capacity에 예약하지만 자동 복구 증거가 "
                    "불완전합니다 - order=%s state=%s executed=%s fee=%s",
                    order_id,
                    evidence.state,
                    evidence.has_reconciled_executed_fill,
                    evidence.fee_complete,
                )
                continue

            episode = self.repo.get_entry_episode_by_token(token_id)
            if episode is None or episode.trade_id is not None:
                stats["identity_gaps"] += 1
                continue
            snapshot = self.repo.get_snapshot_by_id(episode.entry_snapshot_id)
            catalog = self.repo.get_market_catalog_by_condition_id(
                episode.condition_id
            )
            try:
                signed_maker_usdc = float(submission.get("making_amount"))
                submitted_size = float(submission.get("requested_size"))
            except (TypeError, ValueError):
                signed_maker_usdc = math.nan
                submitted_size = math.nan
            if (
                snapshot is None
                or catalog is None
                or not _orphan_catalog_identity_matches(
                    token_id=token_id,
                    episode=episode,
                    snapshot=snapshot,
                    catalog=catalog,
                )
                or str(submission.get("strategy_name") or "") != STRATEGY_NAME
                or not math.isclose(
                    signed_maker_usdc,
                    self.config.buy_amount_usdc,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
                or evidence.requested_size is None
                or not math.isclose(
                    submitted_size,
                    float(evidence.requested_size),
                    rel_tol=0,
                    abs_tol=_FILL_SIZE_TOLERANCE,
                )
                or not math.isclose(
                    float(episode.arm_prob_min),
                    self.config.entry.prob_min,
                    rel_tol=0,
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    float(episode.arm_prob_max),
                    self.config.entry.prob_max,
                    rel_tol=0,
                    abs_tol=1e-9,
                )
            ):
                stats["identity_gaps"] += 1
                logger.critical(
                    "orphan BUY episode/catalog/config identity 불일치로 자동 "
                    "복구를 중단합니다 - order=%s token=%s",
                    order_id,
                    token_id[:16],
                )
                continue

            trade = self.repo.create_recovered_orphan_trade(
                episode.id,
                condition_id=str(episode.condition_id),
                market_slug=catalog.market_slug,
                question=catalog.question,
                event_id=episode.event_id,
                event_slug=catalog.event_slug,
                outcome=str(episode.outcome),
                outcome_side=str(episode.outcome).upper(),
                result_kind=getattr(snapshot, "result_kind", None),
                token_id=token_id,
                buy_price=evidence.confirmed_vwap,
                buy_amount=self.config.buy_amount_usdc,
                buy_shares=evidence.confirmed_size,
                buy_order_id=order_id,
                buy_timestamp=_ledger_timestamp(
                    submission.get("submitted_at"), episode.observed_at
                ),
                buy_probability=episode.exact_vwap,
                buy_confirmed_size=evidence.confirmed_size,
                buy_confirmed_vwap=evidence.confirmed_vwap,
                buy_confirmed_fee_usdc=evidence.confirmed_fee_usdc,
                status=TradeStatus.HOLDING,
                entry_reason=(
                    "recovered_orphan_exact_fok_buy:"
                    "first_observed_in_play_match_result"
                ),
                strategy_name=STRATEGY_NAME,
                mode=self.mode,
                market_end_date=episode.game_start_time,
                hours_until_resolution_at_buy=episode.in_play_hours,
                liquidity_at_buy=snapshot.liquidity,
                volume_24h_at_buy=snapshot.volume_24h,
                market_tags=catalog.tags_json,
                prior_yes_price_at_entry=None,
                yes_price_at_buy=_catalog_yes_probability(catalog),
                stop_price_at_entry=_entry_stop_price(
                    evidence.confirmed_vwap, self.config
                ),
                entry_prob_min_at_buy=episode.arm_prob_min,
                entry_prob_max_at_buy=episode.arm_prob_max,
                entry_hours_min_at_buy=self.config.entry.hours_min,
                entry_hours_max_at_buy=self.config.entry.hours_max,
                prior_snapshot_id_at_entry=None,
                entry_snapshot_id=episode.entry_snapshot_id,
                source_elapsed_minutes_at_buy=episode.source_elapsed_minutes,
                take_profit_delta_at_buy=self.config.entry.take_profit_delta,
                stop_loss_delta_at_buy=self.config.entry.stop_loss_delta,
                late_exit_minute_at_buy=self.config.entry.late_exit_minute,
                best_bid_at_buy=snapshot.best_bid,
                best_ask_at_buy=snapshot.best_ask,
                spread_at_buy=snapshot.spread,
            )
            stats["recovered"] += 1
            logger.warning(
                "ledger-proven orphan BUY를 Trade로 원자적 복구했습니다 - "
                "trade=%s order=%s size=%.6f",
                trade.id,
                order_id,
                evidence.confirmed_size,
            )
        return stats

    def _record_resolution_values(
        self,
        trade,
        *,
        payout: float,
        first_outcome_payout: float,
        winner_outcome: str,
        resolution_status: str,
        evidence_source: str,
        observed_at: datetime,
        source_updated_at: Optional[str],
        fill_evidence: Optional[ExactFillEvidence] = None,
    ) -> bool:
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
                f"{evidence_source}+execution_ledger_exact_confirmed_buy"
            )
        else:
            confirmed_size = getattr(trade, "buy_shares", None)
            confirmed_vwap = getattr(trade, "buy_price", None)
            confirmed_fee = None
            assumption = None
            if confirmed_vwap is not None and confirmed_size is not None:
                assumption = (payout - confirmed_vwap) * confirmed_size
            assumption_basis = "simulation_requested_order_assumption"
            resolution_evidence = f"{evidence_source}+simulation_order"
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.RESOLVED,
            exit_reason="resolved_with_payout_evidence",
            # Legacy column name: stores payout of the first listed outcome.
            yes_price_at_exit=first_outcome_payout,
            resolution_outcome=winner_outcome,
            resolution_value=payout,
            resolution_status=resolution_status,
            resolution_observed_at=observed_at,
            resolution_source_updated_at=source_updated_at,
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
            "proven payout으로 RESOLVED 기록: Trade #%s selected=%s winner=%s "
            "payout=%.2f source=%s "
            "(settlement assumption=%s, realized_pnl=NULL)",
            trade.id,
            trade.outcome,
            winner_outcome,
            payout,
            evidence_source,
            assumption,
        )
        return True

    def _record_proven_resolution(
        self,
        trade,
        market: dict,
        fill_evidence: Optional[ExactFillEvidence] = None,
    ) -> bool:
        proof = get_proven_resolution(market)
        if proof is None:
            return False
        returned_condition_id = str(
            market.get("conditionId") or market.get("condition_id") or ""
        ).strip()
        if returned_condition_id != str(trade.condition_id):
            logger.error(
                "Gamma resolution condition mismatch - trade=%s expected=%s got=%s",
                trade.id,
                trade.condition_id,
                returned_condition_id,
            )
            return False
        aligned = get_aligned_binary_outcomes(market)
        selected = [
            item
            for item in aligned
            if str(item.get("token_id") or "") == str(trade.token_id)
            and str(item.get("outcome") or "") == str(trade.outcome)
        ]
        if len(selected) != 1:
            logger.error(
                "Gamma resolution token/outcome mismatch - trade=%s token=%s outcome=%s",
                trade.id,
                trade.token_id,
                trade.outcome,
            )
            return False
        payouts = proof.get("payouts_by_outcome") or {}
        if trade.outcome not in payouts:
            logger.error(
                "resolution payout lacks selected outcome - trade=%s outcome=%s",
                trade.id,
                trade.outcome,
            )
            return False
        if not math.isclose(
            float(payouts[trade.outcome]),
            float(selected[0]["probability"]),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            logger.error(
                "Gamma resolution selected-token payout mismatch - trade=%s",
                trade.id,
            )
            return False
        # Preserve the Gamma catalog evidence as well as the trade-local proof.
        self.repo.save_market_catalog(trade.condition_id, market, commit=True)
        return self._record_resolution_values(
            trade,
            payout=float(payouts[trade.outcome]),
            first_outcome_payout=float(proof["first_outcome_payout"]),
            winner_outcome=str(proof["outcome"]),
            resolution_status=str(proof["status"]),
            evidence_source=str(proof["evidence"]),
            observed_at=datetime.utcnow(),
            source_updated_at=market.get("updatedAt"),
            fill_evidence=fill_evidence,
        )

    def _record_clob_resolution(
        self,
        trade,
        proof: ClobResolutionProof,
        fill_evidence: Optional[ExactFillEvidence] = None,
    ) -> bool:
        if (
            proof.status != "RESOLVED"
            or proof.winner_index not in (0, 1)
            or len(proof.tokens) != 2
        ):
            return False
        selected = next(
            (token for token in proof.tokens if token.token_id == str(trade.token_id)),
            None,
        )
        if selected is None or selected.outcome != str(trade.outcome):
            logger.error(
                "CLOB resolution selected token/outcome mismatch - trade=%s token=%s outcome=%s",
                trade.id,
                trade.token_id,
                trade.outcome,
            )
            return False
        winner = proof.tokens[proof.winner_index]
        observed_at = datetime.fromisoformat(
            proof.observed_at.replace("Z", "+00:00")
        )
        if observed_at.tzinfo is not None:
            observed_at = observed_at.astimezone(timezone.utc).replace(tzinfo=None)
        self.repo.stage_clob_resolution_observation(
            trade_id=trade.id,
            condition_id=trade.condition_id,
            observed_at=observed_at,
            winner_index=proof.winner_index,
            winner_token_id=winner.token_id,
            winner_outcome=winner.outcome,
            selected_token_id=selected.token_id,
            selected_outcome=selected.outcome,
            selected_payout=selected.price,
            evidence_sha256=proof.evidence_sha256,
            evidence_json=proof.evidence_json,
        )
        return self._record_resolution_values(
            trade,
            payout=selected.price,
            first_outcome_payout=proof.tokens[0].price,
            winner_outcome=winner.outcome,
            resolution_status="clob_closed_unique_winner",
            evidence_source=(
                "clob_closed_unique_winner_sha256:" + proof.evidence_sha256
            ),
            observed_at=observed_at,
            source_updated_at=proof.observed_at,
            fill_evidence=fill_evidence,
        )

    def _apply_proven_resolution(self, trade, recorder) -> bool:
        """Apply one proven payout only after exact BUY execution evidence."""
        if self.mode == "sim" or str(getattr(trade, "buy_order_id", "")).startswith(
            "SIM_"
        ):
            recorder(None)
            return False
        evidence = self.repo.get_exact_buy_fill_evidence(
            getattr(trade, "buy_order_id", None)
        )
        if self._resolution_fill_ready(evidence):
            recorder(evidence)
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
            "resolved payout은 확인했지만 terminal BUY fill/fee 증거가 "
            "완전하지 않아 HOLDING 유지: Trade #%s state=%s executed=%s "
            "fee=%s detail=%s",
            trade.id,
            evidence.state,
            evidence.has_reconciled_executed_fill,
            evidence.fee_complete,
            evidence.detail,
        )
        return False

    def _handle_midpoint_unavailable(self, trade, error) -> bool:
        market = None
        if self.gamma is not None:
            try:
                market = self.gamma.get_market_by_condition_id(trade.condition_id)
            except Exception as gamma_error:
                logger.warning(
                    "Gamma resolution lookup 실패 - condition=%s error=%s",
                    trade.condition_id,
                    gamma_error,
                )
        proof = get_proven_resolution(market) if market else None
        if proof is not None:
            return self._apply_proven_resolution(
                trade,
                lambda fill: self._record_proven_resolution(
                    trade, market, fill_evidence=fill
                ),
            )
        try:
            clob_proof = self.clob.get_market_resolution(trade.condition_id)
        except Exception as clob_error:
            logger.warning(
                "CLOB resolution lookup 실패 - condition=%s error=%s",
                trade.condition_id,
                type(clob_error).__name__,
            )
            return False
        if clob_proof.status == "RESOLVED":
            return self._apply_proven_resolution(
                trade,
                lambda fill: self._record_clob_resolution(
                    trade, clob_proof, fill_evidence=fill
                ),
            )
        logger.warning(
            "midpoint unavailable; Gamma/CLOB final payout 증거 없음 - "
            "condition=%s clob_status=%s error=%s",
            trade.condition_id,
            clob_proof.status,
            error,
        )
        return False

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
    def _resolution_fill_ready(evidence: ExactFillEvidence) -> bool:
        """Resolution may settle a proven terminal partial FOK, never unknown fee."""
        return (
            evidence.has_reconciled_executed_fill
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
        if not isinstance(placed_at, datetime_module.datetime):
            return None
        current = now or datetime.utcnow()
        if placed_at.tzinfo is not None and current.tzinfo is None:
            current = current.replace(tzinfo=placed_at.tzinfo)
        elif placed_at.tzinfo is None and current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        age = (current - placed_at).total_seconds() / 60.0
        return age if math.isfinite(age) and age >= 0 else None

    @staticmethod
    def _pending_sell_age_minutes(
        trade, now: Optional[datetime] = None
    ) -> Optional[float]:
        placed_at = getattr(trade, "sell_timestamp", None)
        if not isinstance(placed_at, datetime_module.datetime):
            return None
        current = now or datetime.utcnow()
        if placed_at.tzinfo is not None and current.tzinfo is None:
            current = current.replace(tzinfo=placed_at.tzinfo)
        elif placed_at.tzinfo is None and current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        age = (current - placed_at).total_seconds() / 60.0
        return age if math.isfinite(age) and age >= 0 else None

    def _quarantine_stop_sell_if_due(
        self,
        trade,
        *,
        now: Optional[datetime] = None,
        detail: str,
    ) -> bool:
        """End active retries after 3h without claiming a successful exit.

        QUARANTINED remains an economically open status and therefore keeps
        one position-capacity reservation.  It merely prevents one uncertain
        stop from blocking or repeatedly submitting against unrelated events.
        """
        age_minutes = self._pending_sell_age_minutes(trade, now=now)
        if (
            age_minutes is None
            or age_minutes + 1e-9
            < self.config.stop_sell_quarantine_timeout_minutes
        ):
            return False
        if (
            getattr(trade, "status", None) == TradeStatus.QUARANTINED
            and getattr(trade, "exit_reason", None)
            in STOP_SELL_ISOLATION_REASONS
        ):
            return True
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.QUARANTINED,
            exit_reason=STOP_SELL_QUARANTINE_REASON,
            realized_pnl=None,
            hypothetical_pnl=None,
            pnl_basis=None,
        )
        logger.critical(
            "손절 실패 3시간 자동 격리 종결: Trade #%s age=%.1fmin "
            "detail=%s; 성공 매도/0체결로 간주하지 않으며 노출 한도는 유지",
            trade.id,
            age_minutes,
            detail,
        )
        return True

    def _quarantine_stop_sell_ledger_failure(
        self,
        trade,
        *,
        error: SubmissionEvidenceError,
    ) -> None:
        """Contain an accepted-or-unknown SELL whose ledger cannot be bound."""
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.QUARANTINED,
            exit_reason=STOP_SELL_LEDGER_QUARANTINE_REASON,
            sell_timestamp=(
                getattr(trade, "sell_timestamp", None) or datetime.utcnow()
            ),
            realized_pnl=None,
            hypothetical_pnl=None,
            pnl_basis=None,
        )
        logger.critical(
            "손절 execution ledger 실패를 즉시 국소 격리: Trade #%s error=%s; "
            "다른 경기 cycle은 계속하되 이 노출은 성공 매도로 간주하지 않음",
            trade.id,
            type(error).__name__,
        )

    def _record_stop_sell_failure(
        self,
        trade,
        *,
        walk,
        best_bid: float,
        best_ask: Optional[float],
        spread: Optional[float],
        detail: str,
        signal: str = "absolute_stop",
    ) -> None:
        """Persist the first continuous exit failure for the 3h deadline."""
        previous_reason = str(getattr(trade, "exit_reason", "") or "")
        previous_timestamp = getattr(trade, "sell_timestamp", None)
        started_at = (
            previous_timestamp
            if previous_reason.startswith(_EXIT_SELL_FAILURE_RETRY_PREFIX)
            and isinstance(previous_timestamp, datetime_module.datetime)
            else datetime.utcnow()
        )
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.HOLDING,
            exit_reason=f"{_EXIT_SELL_FAILURE_RETRY_PREFIX}{signal}",
            sell_price=walk.vwap,
            sell_shares=None,
            sell_order_id=None,
            sell_timestamp=started_at,
            sell_probability=walk.vwap,
            yes_price_at_exit=walk.vwap,
            best_bid_at_exit=best_bid,
            best_ask_at_exit=best_ask,
            spread_at_exit=spread,
            sell_confirmed_size=None,
            sell_confirmed_vwap=None,
            sell_confirmed_fee_usdc=None,
            sell_fill_matched_at=None,
            sell_residual_shares=None,
            realized_pnl=None,
            hypothetical_pnl=None,
            pnl_basis=None,
        )
        logger.warning(
            "청산 실패 추적 시작/유지: Trade #%s signal=%s first=%s detail=%s",
            trade.id,
            signal,
            started_at.isoformat(),
            detail,
        )

    def _clear_stop_sell_failure(self, trade) -> None:
        if (
            not str(getattr(trade, "exit_reason", "") or "").startswith(
                _EXIT_SELL_FAILURE_RETRY_PREFIX
            )
        ):
            return
        self.repo.update_trade(
            trade.id,
            exit_reason=None,
            sell_price=None,
            sell_shares=None,
            sell_order_id=None,
            sell_timestamp=None,
            sell_probability=None,
            yes_price_at_exit=None,
            best_bid_at_exit=None,
            best_ask_at_exit=None,
            spread_at_exit=None,
        )
        logger.info("청산 조건 회복으로 연속 실패 타이머 해제: Trade #%s", trade.id)

    def _restore_holding_after_terminal_zero_sell(
        self, trade, sell_evidence, *, log_prefix: str
    ) -> None:
        """Re-arm a position only after exact zero-fill SELL evidence."""
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.HOLDING,
            exit_reason="exit_sell_terminal_zero_fill",
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
            sell_residual_shares=None,
        )
        logger.warning(
            "%s: Trade #%s order=%s",
            log_prefix,
            trade.id,
            sell_evidence.order_id,
        )

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
                and age_minutes + 1e-9
                >= self.config.fok_reconciliation_timeout_minutes
                and getattr(trade, "buy_order_id", None)
            ):
                try:
                    terminal = self.clob.cancel_order_for_reconciliation(
                        trade.buy_order_id,
                        minimum_age_minutes=(
                            self.config.fok_reconciliation_timeout_minutes
                        ),
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
        if not evidence.fee_complete or evidence.confirmed_fee_usdc is None:
            logger.warning(
                "BUY terminal fill의 fee 증거가 불완전해 PENDING_BUY 유지: "
                "Trade #%s state=%s size=%.6f vwap=%.4f",
                trade.id,
                evidence.state,
                evidence.confirmed_size,
                evidence.confirmed_vwap,
            )
            return False
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.HOLDING,
            buy_price=evidence.confirmed_vwap,
            buy_shares=evidence.confirmed_size,
            buy_confirmed_size=evidence.confirmed_size,
            buy_confirmed_vwap=evidence.confirmed_vwap,
            buy_confirmed_fee_usdc=evidence.confirmed_fee_usdc,
            stop_price_at_entry=_entry_stop_price(
                evidence.confirmed_vwap, self.config
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

    def reconcile_pending_sell(
        self, trade, *, now: Optional[datetime] = None
    ) -> bool:
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
            self._restore_holding_after_terminal_zero_sell(
                trade,
                sell_evidence,
                log_prefix="exact terminal zero-fill SELL 증거로 HOLDING 복귀",
            )
            return False
        if not self._actual_fill_ready(sell_evidence):
            age_minutes = self._pending_sell_age_minutes(trade, now=now)
            if (
                age_minutes is not None
                and age_minutes + 1e-9
                >= self.config.fok_reconciliation_timeout_minutes
                and getattr(trade, "sell_order_id", None)
            ):
                try:
                    terminal = self.clob.cancel_order_for_reconciliation(
                        trade.sell_order_id,
                        minimum_age_minutes=(
                            self.config.fok_reconciliation_timeout_minutes
                        ),
                    )
                except SubmissionEvidenceError as error:
                    logger.warning(
                        "만료 SELL 취소 증명 실패로 PENDING_SELL 유지: "
                        "Trade #%s age=%.1fmin error=%s",
                        trade.id,
                        age_minutes,
                        type(error).__name__,
                    )
                    self._quarantine_stop_sell_if_due(
                        trade,
                        now=now,
                        detail="delayed FOK cancellation evidence unavailable",
                    )
                    return False
                logger.info(
                    "지연 FOK SELL 취소/종결 확인: Trade #%s age=%.1fmin "
                    "status=%s matched=%.6f",
                    trade.id,
                    age_minutes,
                    terminal.get("verified_order_status"),
                    terminal.get("verified_size_matched", 0.0),
                )
                # The terminal-absence path writes its zero-fill proof into the
                # co-located ledger atomically. Re-read now so one stale SELL
                # cannot block every unrelated event for an extra cycle.
                sell_evidence = self.repo.get_exact_sell_fill_evidence(
                    trade.sell_order_id
                )
                if sell_evidence.state == "terminal_zero_fill":
                    self._restore_holding_after_terminal_zero_sell(
                        trade,
                        sell_evidence,
                        log_prefix=(
                            "지연 FOK SELL의 exact 0체결 증거로 HOLDING 복귀"
                        ),
                    )
                    return False
                if self._actual_fill_ready(sell_evidence):
                    logger.info(
                        "지연 FOK SELL 종결 직후 confirmed fill을 발견했습니다: "
                        "Trade #%s order=%s",
                        trade.id,
                        sell_evidence.order_id,
                    )
                else:
                    logger.info(
                        "지연 FOK SELL 종결 뒤 exact ledger 반영을 기다립니다: "
                        "Trade #%s state=%s detail=%s",
                        trade.id,
                        sell_evidence.state,
                        sell_evidence.detail,
                    )
                    self._quarantine_stop_sell_if_due(
                        trade,
                        now=now,
                        detail="delayed FOK terminal call remained ambiguous",
                    )
                    return False
        if not self._actual_fill_ready(sell_evidence):
            if self._quarantine_stop_sell_if_due(
                trade,
                now=now,
                detail="SELL fill/zero-fill evidence remained unresolved",
            ):
                return False
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
        if not self._resolution_fill_ready(buy_evidence):
            logger.error(
                "SELL은 full fill이지만 BUY terminal-fill/fee 증거가 없어 "
                "PENDING_SELL 유지: Trade #%s state=%s executed=%s fee=%s detail=%s",
                trade.id,
                buy_evidence.state,
                buy_evidence.has_reconciled_executed_fill,
                buy_evidence.fee_complete,
                buy_evidence.detail,
            )
            return False
        expected_sell_size = getattr(trade, "sell_shares", None)
        if (
            expected_sell_size is None
            or not math.isfinite(float(expected_sell_size))
            or not math.isclose(
                sell_evidence.confirmed_size,
                float(expected_sell_size),
                rel_tol=1e-9,
                abs_tol=_FILL_SIZE_TOLERANCE,
            )
        ):
            logger.error(
                "signed SELL requested/confirmed size 불일치로 PENDING_SELL 유지: "
                "Trade #%s requested=%s confirmed=%.6f",
                trade.id,
                expected_sell_size,
                sell_evidence.confirmed_size,
            )
            return False
        residual_shares = (
            buy_evidence.confirmed_size - sell_evidence.confirmed_size
        )
        if (
            residual_shares < -_FILL_SIZE_TOLERANCE
            or residual_shares >= _MAX_SIGNED_SELL_DUST_SHARES
        ):
            logger.error(
                "BUY/SELL residual이 SDK 0.01-share quantum을 초과해 "
                "PENDING_SELL 유지: Trade #%s buy=%.6f sell=%.6f residual=%.6f",
                trade.id,
                buy_evidence.confirmed_size,
                sell_evidence.confirmed_size,
                residual_shares,
            )
            return False
        residual_shares = max(0.0, residual_shares)

        size = sell_evidence.confirmed_size
        allocated_buy_fee = (
            buy_evidence.confirmed_fee_usdc
            * size
            / buy_evidence.confirmed_size
        )
        realized_pnl = (
            (sell_evidence.confirmed_vwap - buy_evidence.confirmed_vwap) * size
            - allocated_buy_fee
            - sell_evidence.confirmed_fee_usdc
        )
        has_dust = residual_shares > _FILL_SIZE_TOLERANCE
        pending_reason = str(getattr(trade, "exit_reason", "") or "")
        exit_base = next(
            (
                value
                for value in ("take_profit", "late_half_target", "absolute_stop")
                if pending_reason.startswith(value)
            ),
            "absolute_stop",
        )
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.COMPLETED,
            exit_reason=(
                f"{exit_base}_confirmed_fill_with_recorded_sdk_dust"
                if has_dust
                else f"{exit_base}_confirmed_fill"
            ),
            sell_price=sell_evidence.confirmed_vwap,
            sell_shares=size,
            realized_pnl=realized_pnl,
            hypothetical_pnl=None,
            pnl_basis=(
                "exact_reconciled_buy_sell_confirmed_fills_net_allocated_fees_"
                "excluding_recorded_unsellable_dust"
                if has_dust
                else "exact_reconciled_buy_sell_confirmed_fills_net_known_fees"
            ),
            buy_confirmed_size=buy_evidence.confirmed_size,
            buy_confirmed_vwap=buy_evidence.confirmed_vwap,
            buy_confirmed_fee_usdc=buy_evidence.confirmed_fee_usdc,
            sell_confirmed_size=size,
            sell_confirmed_vwap=sell_evidence.confirmed_vwap,
            sell_confirmed_fee_usdc=sell_evidence.confirmed_fee_usdc,
            sell_fill_matched_at=sell_evidence.matched_at,
            sell_residual_shares=residual_shares,
        )
        logger.info(
            "confirmed %s SELL 완료: Trade #%s size=%.6f residual=%.6f "
            "vwap=%.4f actual sold-portion P&L=$%.4f",
            exit_base,
            trade.id,
            size,
            residual_shares,
            sell_evidence.confirmed_vwap,
            realized_pnl,
        )
        return True

    def execute_sell(
        self,
        trade,
        *,
        prefetched_walk=None,
        book_prefetched: bool = False,
    ) -> bool:
        """Submit one full-depth FOK TP/late/stop exit, except proven SDK dust."""
        if (
            self.emergency_sell_submissions
            >= self.config.max_emergency_sells_per_cycle
        ):
            self.emergency_sell_guard_blocks += 1
            logger.critical(
                "exit SELL cycle circuit is open; additional holdings are "
                "left untouched - trade=%s submitted=%s limit=%s",
                trade.id,
                self.emergency_sell_submissions,
                self.config.max_emergency_sells_per_cycle,
            )
            return False
        try:
            sellable_shares = _sdk_sellable_shares(float(trade.buy_shares))
            if book_prefetched:
                if prefetched_walk is None:
                    raise ClobResponseUnavailableError(
                        "prefetched holding book has no full executable depth"
                    )
                walk = prefetched_walk
                if not math.isclose(
                    float(walk.shares),
                    sellable_shares,
                    rel_tol=0,
                    abs_tol=_FILL_SIZE_TOLERANCE,
                ):
                    raise ValueError("prefetched holding-book share mismatch")
            else:
                walk = self.clob.get_sell_book_walk(
                    trade.token_id,
                    shares=sellable_shares,
                )
        except Exception as error:
            logger.warning(
                "full-depth exit book unavailable - trade=%s token=%s error=%s",
                trade.id,
                str(trade.token_id)[:16],
                type(error).__name__,
            )
            if self._quarantine_stop_sell_if_due(
                trade,
                detail="continuous exit failure and current book unavailable",
            ):
                return False
            return self._handle_midpoint_unavailable(
                trade, "full-depth exit book unavailable"
            )
        best_bid, best_ask, spread = walk.best_bid, walk.best_ask, walk.spread
        exit_signal, trigger_price, source_minute = self._exit_signal(trade, walk)
        if exit_signal is None:
            self._clear_stop_sell_failure(trade)
            logger.debug(
                "no exit signal - trade=%s bid=%.4f vwap=%.4f minute=%s",
                trade.id,
                best_bid,
                walk.vwap,
                source_minute,
            )
            return False

        # A post-game/pre-resolution book can briefly contain only a 0.001
        # cleanup bid.  That is not an in-play adverse move.  The strategy is
        # hold-to-resolution, so a stop is allowed only while both Gamma event
        # and market lifecycle fields explicitly prove live order-taking.
        if not self._stop_execution_is_explicitly_live(trade):
            logger.warning(
                "%s exit suppressed because live lifecycle is not explicitly "
                "proven - trade=%s condition=%s event=%s bid=%.4f",
                exit_signal,
                trade.id,
                trade.condition_id,
                getattr(trade, "event_id", None),
                best_bid,
            )
            return self._handle_midpoint_unavailable(
                trade, "exit lifecycle not explicitly live"
            )

        # The lifecycle reads above take time.  Never submit against the older
        # trigger book: re-read exact full depth and re-run every price guard.
        try:
            walk = self.clob.get_sell_book_walk(
                trade.token_id,
                shares=sellable_shares,
            )
        except Exception as error:
            logger.warning(
                "fresh post-preflight exit book unavailable - trade=%s error=%s",
                trade.id,
                type(error).__name__,
            )
            return self._handle_midpoint_unavailable(
                trade, "fresh post-preflight exit book unavailable"
            )
        best_bid, best_ask, spread = walk.best_bid, walk.best_ask, walk.spread
        exit_signal, trigger_price, source_minute = self._exit_signal(trade, walk)
        if exit_signal is None:
            self._clear_stop_sell_failure(trade)
            logger.info(
                "exit signal cleared during lifecycle preflight; no SELL - "
                "trade=%s fresh_bid=%.4f fresh_vwap=%.4f minute=%s",
                trade.id,
                best_bid,
                walk.vwap,
                source_minute,
            )
            return False
        if self._quarantine_stop_sell_if_due(
            trade,
            detail=f"continuous {exit_signal} failure remained triggered",
        ):
            return False
        execution_safe = (
            self._stop_execution_price_is_safe(trade, walk, trigger_price)
            if exit_signal == "absolute_stop"
            else self._profit_execution_price_is_safe(walk, trigger_price)
        )
        if not execution_safe:
            self.emergency_sell_guard_blocks += 1
            self._record_stop_sell_failure(
                trade,
                walk=walk,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                detail=f"fresh {exit_signal} book failed safety envelope",
                signal=exit_signal,
            )
            return False

        logger.warning(
            "%s exit 충족: Trade #%s bid=%.2f%% trigger=%.2f%% minute=%s "
            "gap=%.2fpp full_depth_vwap=%.2f%% limit=%.2f%% levels=%s shares=%.6f",
            exit_signal,
            trade.id,
            best_bid * 100,
            trigger_price * 100,
            source_minute,
            abs(trigger_price - best_bid) * 100,
            walk.vwap * 100,
            walk.limit_price * 100,
            walk.levels_used,
            walk.shares,
        )
        try:
            result = self.clob.place_limit_order(
                token_id=trade.token_id,
                price=walk.limit_price,
                size=_sdk_sell_submission_shares(walk.shares),
                side="SELL",
                order_type="FOK",
            )
        except SubmissionEvidenceError as error:
            self._quarantine_stop_sell_ledger_failure(trade, error=error)
            return False
        accepted = bool(result.get("success") or result.get("orderID"))
        try:
            sell_shares = float(result.get("requested_size", walk.shares))
        except (TypeError, ValueError):
            logger.critical(
                "signed SELL requested size evidence is invalid - trade=%s",
                trade.id,
            )
            if accepted:
                self.emergency_sell_submissions += 1
                self._bind_uncertain_sell_submission(
                    trade,
                    result=result,
                    walk=walk,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    sell_shares=None,
                    reason="signed_sell_size_evidence_invalid",
                )
            return False
        residual_shares = float(trade.buy_shares) - sell_shares
        if (
            not math.isfinite(sell_shares)
            or sell_shares <= 0
            or residual_shares < -_FILL_SIZE_TOLERANCE
            or residual_shares >= _MAX_SIGNED_SELL_DUST_SHARES
        ):
            logger.critical(
                "signed SELL size drift is unsafe - trade=%s bought=%.6f "
                "signed=%.6f residual=%.6f",
                trade.id,
                float(trade.buy_shares),
                sell_shares,
                residual_shares,
            )
            if accepted:
                self.emergency_sell_submissions += 1
                self._bind_uncertain_sell_submission(
                    trade,
                    result=result,
                    walk=walk,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    sell_shares=(
                        sell_shares
                        if math.isfinite(sell_shares) and sell_shares > 0
                        else None
                    ),
                    reason="signed_sell_size_drift_unsafe",
                )
            return False
        residual_shares = max(0.0, residual_shares)
        if accepted:
            self.emergency_sell_submissions += 1
            common = {
                "sell_price": walk.vwap,
                "sell_shares": sell_shares,
                "sell_order_id": result.get("orderID"),
                "sell_timestamp": datetime.utcnow(),
                "sell_probability": walk.vwap,
                "yes_price_at_exit": walk.vwap,
                "best_bid_at_exit": best_bid,
                "best_ask_at_exit": best_ask,
                "spread_at_exit": spread,
                "sell_confirmed_size": None,
                "sell_confirmed_vwap": None,
                "sell_confirmed_fee_usdc": None,
                "sell_fill_matched_at": None,
                "sell_residual_shares": residual_shares,
            }
            if self.mode == "sim":
                hypothetical_pnl = (walk.vwap - trade.buy_price) * sell_shares
                self.repo.update_trade(
                    trade.id,
                    **common,
                    status=TradeStatus.COMPLETED,
                    exit_reason=f"{exit_signal}_simulation_hypothetical",
                    realized_pnl=None,
                    hypothetical_pnl=hypothetical_pnl,
                    pnl_basis="simulation_hypothetical_best_bid_fees_excluded",
                )
                return True
            self.repo.update_trade(
                trade.id,
                **common,
                status=TradeStatus.PENDING_SELL,
                exit_reason=f"{exit_signal}_pending_confirmed_fill",
                realized_pnl=None,
                hypothetical_pnl=None,
                pnl_basis=None,
            )
            logger.info(
                "%s FOK SELL 접수, confirmed fill 대기: "
                "Trade #%s order=%s bid=%.4f size=%.6f",
                exit_signal,
                trade.id,
                result.get("orderID"),
                best_bid,
                sell_shares,
            )
            return False
        if is_zero_balance_error(result):
            self._mark_unfilled(trade)
            return False
        available = available_shares_from_error(result)
        logger.warning(
            "매도 실패 진단 - 사유=%s trade=%s token=%s 요청=%.6f 가용=%s",
            classify_sell_failure(result, trade.buy_shares),
            trade.id,
            str(trade.token_id)[:16],
            trade.buy_shares,
            f"{available:.6f}" if available is not None else "미상",
        )
        logger.error("%s FOK SELL 실패: %s", exit_signal, result)
        self._record_stop_sell_failure(
            trade,
            walk=walk,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            detail=classify_sell_failure(result, trade.buy_shares),
            signal=exit_signal,
        )
        return False

    def _stop_execution_is_explicitly_live(self, trade) -> bool:
        """Require independent Gamma and CLOB lifecycle proof before a stop."""
        event_id = str(getattr(trade, "event_id", "") or "").strip()
        condition_id = str(getattr(trade, "condition_id", "") or "").strip()
        if not event_id or not condition_id:
            return False
        market = self._cycle_markets.get(condition_id)
        event = None
        if isinstance(market, dict):
            events = market.get("events")
            if isinstance(events, list):
                event = next(
                    (
                        item
                        for item in events
                        if isinstance(item, Mapping)
                        and str(item.get("id") or "").strip() == event_id
                    ),
                    None,
                )
        if market is None or event is None:
            if self.gamma is None:
                logger.error(
                    "Gamma client absent; fail-closed stop preflight - trade=%s",
                    trade.id,
                )
                return False
            try:
                market = self.gamma.get_market_by_condition_id(condition_id)
                event = self.gamma.get_event_by_id(event_id)
            except Exception as error:
                logger.warning(
                    "stop lifecycle preflight failed - trade=%s error=%s",
                    trade.id,
                    type(error).__name__,
                )
                return False
        if not isinstance(market, dict) or not isinstance(event, dict):
            return False
        observed_condition = str(
            market.get("conditionId") or market.get("condition_id") or ""
        ).strip()
        observed_event = str(event.get("id") or "").strip()
        gamma_live = (
            observed_condition == condition_id
            and observed_event == event_id
            and event.get("active") is True
            and event.get("closed") is False
            and event.get("live") is True
            and event.get("ended") is False
            and market.get("active") is True
            and market.get("closed") is False
            and market.get("enableOrderBook") is True
            and market.get("acceptingOrders") is True
        )
        if not gamma_live:
            return False
        try:
            clob_proof = self.clob.get_market_resolution(condition_id)
        except Exception as error:
            logger.warning(
                "independent CLOB lifecycle preflight failed - trade=%s error=%s",
                trade.id,
                type(error).__name__,
            )
            return False
        if clob_proof.status != "OPEN":
            logger.warning(
                "independent CLOB lifecycle is not OPEN - trade=%s status=%s",
                trade.id,
                clob_proof.status,
            )
            return False
        return True

    def _stop_execution_price_is_safe(self, trade, walk, stop_price: float) -> bool:
        """Validate a complete live book before any irreversible stop POST.

        The caller has already proven the Gamma event and independent CLOB
        market are OPEN, then refreshed the book.  Under that dual proof a
        discontinuous price gap must not turn a stop into an accidental hold.
        The old 35% loss cap did exactly that during Lille–PSG: the book jumped
        from roughly 0.95 to 0.058 between one-minute observations.  We retain
        full-depth and spread bounds, but allow the first executable live gap.
        """
        minimum_price = stop_price - self.config.entry.max_stop_slippage
        spread = walk.spread
        try:
            buy_price = float(trade.buy_price)
            loss_fraction = max(0.0, (buy_price - walk.vwap) / buy_price)
            signable_shares = _sdk_sellable_shares(float(trade.buy_shares))
        except (TypeError, ValueError, ZeroDivisionError):
            loss_fraction = math.inf
            signable_shares = math.nan
        executable_book = (
            math.isfinite(float(walk.best_bid))
            and 0 < float(walk.best_bid) < 1
            and math.isfinite(float(walk.vwap))
            and 0 < float(walk.vwap) < 1
            and math.isfinite(float(walk.limit_price))
            and 0 < float(walk.limit_price) < 1
            and math.isfinite(float(walk.shares))
            and abs(float(walk.shares) - signable_shares) <= _FILL_SIZE_TOLERANCE
            and spread is not None
            and math.isfinite(float(spread))
            and 0 <= float(spread)
            and spread <= self.config.entry.max_stop_spread + 1e-9
        )
        normal_envelope = (
            executable_book
            and minimum_price > 0
            and walk.vwap + 1e-9 >= minimum_price
            and walk.limit_price + 1e-9 >= minimum_price
            and math.isfinite(loss_fraction)
            and loss_fraction
            <= self.config.entry.max_stop_loss_fraction + 1e-9
        )
        if normal_envelope:
            return True
        if executable_book:
            logger.critical(
                "live gap-stop allowed after dual lifecycle proof - "
                "trade=%s bid=%.4f ask=%s spread=%.4f vwap=%.4f limit=%.4f "
                "normal_minimum=%.4f projected_loss=%s",
                trade.id,
                walk.best_bid,
                f"{walk.best_ask:.4f}" if walk.best_ask is not None else "none",
                spread,
                walk.vwap,
                walk.limit_price,
                minimum_price,
                f"{loss_fraction:.2%}" if math.isfinite(loss_fraction) else "invalid",
            )
            return True
        else:
            logger.critical(
                "emergency SELL blocked by incomplete/unsafe live book - "
                "trade=%s bid=%.4f ask=%s spread=%s vwap=%.4f limit=%.4f "
                "shares=%s expected=%s minimum=%.4f projected_loss=%s",
                trade.id,
                walk.best_bid,
                f"{walk.best_ask:.4f}" if walk.best_ask is not None else "none",
                f"{spread:.4f}" if spread is not None else "none",
                walk.vwap,
                walk.limit_price,
                getattr(walk, "shares", None),
                signable_shares,
                minimum_price,
                f"{loss_fraction:.2%}" if math.isfinite(loss_fraction) else "invalid",
            )
        return False

    def _profit_execution_price_is_safe(self, walk, target_price: float) -> bool:
        """Require the complete holding VWAP—not only top bid—to clear TP."""
        spread = getattr(walk, "spread", None)
        try:
            values_are_valid = (
                math.isfinite(float(walk.best_bid))
                and 0 < float(walk.best_bid) < 1
                and math.isfinite(float(walk.vwap))
                and 0 < float(walk.vwap) < 1
                and math.isfinite(float(walk.limit_price))
                and 0 < float(walk.limit_price) < 1
                and math.isfinite(float(walk.shares))
                and float(walk.shares) > 0
                and spread is not None
                and math.isfinite(float(spread))
                and 0 <= float(spread) <= self.config.entry.max_stop_spread + 1e-9
            )
        except (TypeError, ValueError):
            values_are_valid = False
        return bool(
            values_are_valid
            and float(walk.vwap) + 1e-9 >= float(target_price)
        )

    def _bind_uncertain_sell_submission(
        self,
        trade,
        *,
        result: dict,
        walk,
        best_bid: float,
        best_ask: Optional[float],
        spread: Optional[float],
        sell_shares: Optional[float],
        reason: str,
    ) -> None:
        """Never leave an irreversible accepted SELL orphaned from its trade."""
        residual = None
        if sell_shares is not None:
            residual = max(0.0, float(trade.buy_shares) - sell_shares)
        self.repo.update_trade(
            trade.id,
            status=TradeStatus.PENDING_SELL,
            exit_reason=reason,
            sell_price=walk.vwap,
            sell_shares=sell_shares,
            sell_order_id=result.get("orderID"),
            sell_timestamp=datetime.utcnow(),
            sell_probability=walk.vwap,
            yes_price_at_exit=walk.vwap,
            best_bid_at_exit=best_bid,
            best_ask_at_exit=best_ask,
            spread_at_exit=spread,
            sell_confirmed_size=None,
            sell_confirmed_vwap=None,
            sell_confirmed_fee_usdc=None,
            sell_fill_matched_at=None,
            sell_residual_shares=residual,
            realized_pnl=None,
            hypothetical_pnl=None,
            pnl_basis=None,
        )
        logger.critical(
            "accepted SELL bound to PENDING_SELL after contract violation - "
            "trade=%s order=%s reason=%s",
            trade.id,
            result.get("orderID"),
            reason,
        )

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
