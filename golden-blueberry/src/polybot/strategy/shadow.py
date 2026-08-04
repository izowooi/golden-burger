"""Accountless counterfactual research for Golden Blueberry.

Shadow mode observes the same first crossing as production but expands it into
four fixed treatments.  It never submits an order and never creates a Trade.
All P&L is explicitly hypothetical, gross of fees, and based on a conservative
entry limit plus the first observed executable bid or proven resolution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from typing import Dict, Iterable, Optional

from polybot_observability import current_run_id

from ..api.clob_client import ClobClientWrapper
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.models import ShadowSignal
from ..db.repository import TradeRepository
from .filters import get_proven_resolution, get_strict_binary_yes
from .scanner import MarketScanner, get_hours_until_resolution, parse_end_date
from .signals import EPSILON
from .timing import evaluate_entry_clock


logger = logging.getLogger(__name__)

SHADOW_MIN_SURGES = (0.02, 0.05)
SHADOW_HORIZONS_HOURS = (72.0, 168.0)


def _aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    aware = _aware_utc(value)
    return aware.replace(tzinfo=None) if aware is not None else None


def _open_price(value) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and 0 < price < 1 else None


def _nonnegative(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class ShadowResearcher:
    """Run the fixed counterfactual grid without an exchange account."""

    def __init__(
        self,
        repo: TradeRepository,
        scanner: MarketScanner,
        gamma: GammaClient,
        clob: ClobClientWrapper,
        config: TradingConfig,
    ):
        self.repo = repo
        self.scanner = scanner
        self.gamma = gamma
        self.clob = clob
        self.config = config

    @staticmethod
    def _run_id(reference: datetime) -> str:
        return str(current_run_id() or f"shadow-{reference.isoformat()}")

    def run(
        self,
        markets: Iterable[Dict],
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        reference = _aware_utc(now or datetime.now(timezone.utc))
        assert reference is not None
        market_list = list(markets)

        watched_before = self.repo.get_watched_shadow_condition_ids()
        observations = self._observe_existing(
            watched_before,
            market_list,
            reference,
        )

        crossings = self.scanner.scan_shadow_crossings(
            market_list,
            now=reference,
        )
        signals_created = 0
        crossing_observations = 0
        for crossing in crossings:
            created, observed = self._record_crossing(crossing, reference)
            signals_created += created
            crossing_observations += int(observed)
        self.repo.commit()

        stats = self.repo.get_stats()
        return {
            "shadow_crossings": len(crossings),
            "shadow_signals_created": signals_created,
            "shadow_observations_saved": observations + crossing_observations,
            "shadow_open": int(stats["shadow_open"]),
            "shadow_counterfactual_open": int(
                stats["shadow_counterfactual_open"]
            ),
            "shadow_closed": int(stats["shadow_closed"]),
            "shadow_not_executable": int(stats["shadow_not_executable"]),
        }

    def _observe_existing(
        self,
        condition_ids: Iterable[str],
        markets: Iterable[Dict],
        reference: datetime,
    ) -> int:
        by_condition = {
            str(market.get("conditionId")): market
            for market in markets
            if market.get("conditionId") not in (None, "")
        }
        saved = 0
        for condition_id in condition_ids:
            market = by_condition.get(condition_id)
            if market is None:
                market = self.gamma.get_market_by_condition_id(condition_id)
            if market is None:
                _, created = self.repo.record_shadow_observation(
                    run_id=self._run_id(reference),
                    condition_id=condition_id,
                    probability=None,
                    best_bid=None,
                    best_ask=None,
                    spread=None,
                    liquidity=None,
                    volume_24h=None,
                    market_end_date=None,
                    hours_until_resolution=None,
                    entry_deadline=None,
                    hours_until_entry_deadline=None,
                    sports_phase=None,
                    source_updated_at=None,
                    resolution_outcome=None,
                    resolution_value=None,
                    resolution_evidence=None,
                    data_status="MARKET_UNAVAILABLE",
                    observed_at=_naive_utc(reference),
                    commit=False,
                )
                saved += int(created)
                continue
            saved += int(self._observe_market(market, reference))
        return saved

    def _observe_market(self, market: Dict, reference: datetime) -> bool:
        condition_id = str(market.get("conditionId") or "")
        if not condition_id:
            return False
        yes = get_strict_binary_yes(market)
        resolution = get_proven_resolution(market)
        probability = float(yes["probability"]) if yes else None
        best_bid = None
        if yes and resolution is None:
            try:
                best_bid = _open_price(self.clob.get_best_bid(yes["token_id"]))
            except Exception as error:
                logger.warning(
                    "Shadow fresh bid 조회 실패 - condition=%s error=%s",
                    condition_id,
                    type(error).__name__,
                )
        best_ask = _open_price(market.get("bestAsk"))
        spread = (
            best_ask - best_bid
            if best_bid is not None
            and best_ask is not None
            and best_bid <= best_ask + EPSILON
            else None
        )
        end_date = parse_end_date(market.get("endDate"))
        clock = evaluate_entry_clock(market, self.config.sports, reference)
        data_status = (
            "RESOLVED"
            if resolution is not None
            else "FRESH_BID"
            if best_bid is not None
            else "BID_UNAVAILABLE"
            if yes
            else "MARKET_CONTRACT_INVALID"
        )
        observation, created = self.repo.record_shadow_observation(
            run_id=self._run_id(reference),
            condition_id=condition_id,
            probability=probability,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            liquidity=_nonnegative(market.get("liquidity")),
            volume_24h=_nonnegative(market.get("volume24hr")),
            market_end_date=_naive_utc(end_date),
            hours_until_resolution=get_hours_until_resolution(
                end_date, reference
            ),
            entry_deadline=_naive_utc(clock.deadline),
            hours_until_entry_deadline=clock.hours_left,
            sports_phase=clock.phase if clock.is_sports else "not_sports",
            source_updated_at=market.get("updatedAt"),
            resolution_outcome=(resolution or {}).get("outcome"),
            resolution_value=(resolution or {}).get("yes_payout"),
            resolution_evidence=(resolution or {}).get("evidence"),
            data_status=data_status,
            observed_at=_naive_utc(reference),
            commit=False,
        )
        if created:
            self._apply_observation(
                condition_id,
                probability=observation.probability,
                best_bid=observation.best_bid,
                resolution=resolution,
                observed_at=reference,
            )
        return created

    def _common_execution(
        self,
        crossing: Dict,
    ) -> tuple[Optional[str], Dict[str, Optional[float]]]:
        """Return a common execution rejection or conservative book evidence."""
        evidence: Dict[str, Optional[float]] = {
            "fresh_midpoint": None,
            "surge": float(crossing["surge"]),
            "best_bid": None,
            "best_ask": None,
            "spread": None,
            "ask_depth_shares": None,
            "entry_limit_price": None,
            "hypothetical_shares": None,
        }
        if not crossing.get("event_id"):
            return "missing_event_id", evidence
        liquidity = _nonnegative(crossing.get("liquidity"))
        volume = _nonnegative(crossing.get("volume_24h"))
        if liquidity is None or liquidity + EPSILON < self.config.effective_min_liquidity:
            return "low_liquidity", evidence
        if volume is None or volume + EPSILON < self.config.effective_min_volume_24h:
            return "low_volume", evidence

        try:
            midpoint = _open_price(self.clob.get_midpoint(crossing["token_id"]))
        except Exception as error:
            logger.warning(
                "Shadow fresh midpoint 조회 실패 - condition=%s error=%s",
                crossing["condition_id"],
                type(error).__name__,
            )
            return "midpoint_unavailable", evidence
        if midpoint is None:
            return "midpoint_invalid", evidence
        evidence["fresh_midpoint"] = midpoint
        evidence["surge"] = midpoint - float(crossing["prior_probability"])
        if not (
            self.config.entry.prob_min - EPSILON
            <= midpoint
            <= self.config.entry.prob_max + EPSILON
        ):
            return "fresh_midpoint_out_of_band", evidence
        if float(crossing["prior_probability"]) >= self.config.entry.prob_min - EPSILON:
            return "fresh_midpoint_not_crossing", evidence

        try:
            book = self.clob.get_buy_book_depth(
                crossing["token_id"],
                ask_limit_price=self.config.entry.prob_max,
                max_price_window=self.config.depth_price_window,
            )
        except Exception as error:
            logger.warning(
                "Shadow fresh BUY depth 조회 실패 - condition=%s error=%s",
                crossing["condition_id"],
                type(error).__name__,
            )
            return "book_unavailable", evidence
        best_bid = _open_price(book.best_bid)
        best_ask = _open_price(book.best_ask)
        depth_limit = _open_price(book.ask_limit_price)
        spread = _nonnegative(book.spread)
        depth = _nonnegative(book.ask_depth_shares)
        evidence.update(
            {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "ask_depth_shares": depth,
                "entry_limit_price": depth_limit,
            }
        )
        if (
            best_bid is None
            or best_ask is None
            or depth_limit is None
            or spread is None
            or depth is None
            or best_bid > best_ask + EPSILON
            or not math.isclose(
                spread, best_ask - best_bid, rel_tol=0, abs_tol=1e-6
            )
        ):
            return "book_invalid", evidence
        if spread > self.config.max_spread + EPSILON:
            return "spread_too_wide", evidence
        if best_ask > self.config.entry.prob_max + EPSILON:
            return "ask_above_entry_band", evidence
        shares = self.config.buy_amount_usdc / depth_limit
        evidence["hypothetical_shares"] = shares
        minimum = self.config.min_order_size + self.config.min_order_buffer_shares
        if shares + EPSILON < minimum:
            return "minimum_order_not_met", evidence
        if depth + EPSILON < shares * self.config.depth_safety_multiple:
            return "insufficient_depth", evidence
        return None, evidence

    def _record_crossing(
        self,
        crossing: Dict,
        reference: datetime,
    ) -> tuple[int, bool]:
        common_reason, evidence = self._common_execution(crossing)
        _, observation_created = self.repo.record_shadow_observation(
            run_id=self._run_id(reference),
            condition_id=crossing["condition_id"],
            probability=crossing["current_probability"],
            best_bid=evidence["best_bid"],
            best_ask=evidence["best_ask"],
            spread=evidence["spread"],
            liquidity=crossing.get("liquidity"),
            volume_24h=crossing.get("volume_24h"),
            market_end_date=_naive_utc(crossing.get("market_end_date")),
            hours_until_resolution=crossing.get("hours_until_resolution"),
            entry_deadline=_naive_utc(crossing.get("entry_deadline")),
            hours_until_entry_deadline=crossing.get("hours_left"),
            sports_phase=crossing.get("sports_phase"),
            source_updated_at=crossing.get("source_updated_at"),
            resolution_outcome=None,
            resolution_value=None,
            resolution_evidence=None,
            data_status=(
                "EXECUTABLE_CROSSING"
                if common_reason is None
                else f"NOT_EXECUTABLE_{common_reason.upper()}"
            ),
            observed_at=_naive_utc(reference),
            commit=False,
        )

        created_rows = 0
        for min_surge in SHADOW_MIN_SURGES:
            for horizon in SHADOW_HORIZONS_HOURS:
                treatment_reasons = []
                if evidence["surge"] is None or (
                    float(evidence["surge"]) + EPSILON < min_surge
                ):
                    treatment_reasons.append("surge_below_min")
                if crossing.get("sports_phase") != "in_play":
                    hours_left = crossing.get("hours_left")
                    if hours_left is None or not math.isfinite(float(hours_left)):
                        treatment_reasons.append("entry_clock_unavailable")
                    elif not (0 < float(hours_left) <= horizon + EPSILON):
                        treatment_reasons.append("outside_horizon")

                if common_reason is not None:
                    decision = "NOT_EXECUTABLE"
                    status = "NOT_EXECUTABLE"
                    reason = common_reason
                elif treatment_reasons:
                    decision = "REJECTED_TREATMENT"
                    status = "COUNTERFACTUAL_OPEN"
                    reason = "+".join(treatment_reasons)
                else:
                    decision = "ENTERED"
                    status = "OPEN"
                    reason = "treatment_and_execution_gates_passed"

                signal, created = self.repo.record_shadow_signal(
                    run_id=self._run_id(reference),
                    condition_id=crossing["condition_id"],
                    event_id=crossing.get("event_id"),
                    question=crossing.get("question"),
                    token_id=crossing.get("token_id"),
                    prior_snapshot_id=crossing["prior_snapshot_id"],
                    current_snapshot_id=crossing["current_snapshot_id"],
                    prior_probability=crossing["prior_probability"],
                    current_probability=crossing["current_probability"],
                    fresh_midpoint=evidence["fresh_midpoint"],
                    surge=evidence["surge"],
                    snapshot_gap_minutes=crossing["snapshot_gap_minutes"],
                    min_surge=min_surge,
                    horizon_hours=horizon,
                    stop_price=self.config.entry.stop_price,
                    take_profit_price=self.config.entry.take_profit_price,
                    entry_decision=decision,
                    entry_reason=reason,
                    status=status,
                    clock_reference=crossing.get("clock_reference"),
                    sports_phase=crossing.get("sports_phase"),
                    is_sports=int(bool(crossing.get("is_sports"))),
                    entry_deadline=_naive_utc(crossing.get("entry_deadline")),
                    hours_left_at_signal=crossing.get("hours_left"),
                    market_end_date=_naive_utc(crossing.get("market_end_date")),
                    hours_until_resolution_at_signal=crossing.get(
                        "hours_until_resolution"
                    ),
                    liquidity_at_signal=crossing.get("liquidity"),
                    volume_24h_at_signal=crossing.get("volume_24h"),
                    best_bid_at_entry=evidence["best_bid"],
                    best_ask_at_entry=evidence["best_ask"],
                    spread_at_entry=evidence["spread"],
                    ask_depth_shares_at_entry=evidence["ask_depth_shares"],
                    entry_limit_price=(
                        evidence["entry_limit_price"]
                        if common_reason is None
                        else None
                    ),
                    hypothetical_notional=(
                        self.config.buy_amount_usdc
                        if common_reason is None
                        else None
                    ),
                    hypothetical_shares=(
                        evidence["hypothetical_shares"]
                        if common_reason is None
                        else None
                    ),
                    first_observed_at=_naive_utc(reference),
                    last_observed_at=_naive_utc(reference),
                    min_probability=crossing["current_probability"],
                    max_probability=crossing["current_probability"],
                    min_best_bid=evidence["best_bid"],
                    max_best_bid=evidence["best_bid"],
                    classification=(
                        "NOT_EXECUTABLE" if common_reason is not None else None
                    ),
                    commit=False,
                )
                created_rows += int(created)
                if created:
                    logger.info(
                        "Shadow signal - condition=%s surge=%.0fpp horizon=%.0fh "
                        "decision=%s reason=%s",
                        signal.condition_id,
                        min_surge * 100,
                        horizon,
                        decision,
                        reason,
                    )
        return created_rows, observation_created

    def _apply_observation(
        self,
        condition_id: str,
        *,
        probability: Optional[float],
        best_bid: Optional[float],
        resolution: Optional[Dict],
        observed_at: datetime,
    ) -> None:
        for signal in self.repo.get_open_shadow_signals(condition_id):
            signal.last_observed_at = _naive_utc(observed_at)
            if probability is not None:
                signal.min_probability = (
                    probability
                    if signal.min_probability is None
                    else min(signal.min_probability, probability)
                )
                signal.max_probability = (
                    probability
                    if signal.max_probability is None
                    else max(signal.max_probability, probability)
                )
            if best_bid is not None:
                signal.min_best_bid = (
                    best_bid
                    if signal.min_best_bid is None
                    else min(signal.min_best_bid, best_bid)
                )
                signal.max_best_bid = (
                    best_bid
                    if signal.max_best_bid is None
                    else max(signal.max_best_bid, best_bid)
                )

            if resolution is not None:
                signal.resolution_outcome = resolution["outcome"]
                signal.resolution_value = float(resolution["yes_payout"])
                signal.resolution_evidence = resolution["evidence"]
                self._close_signal(
                    signal,
                    exit_price=float(resolution["yes_payout"]),
                    exit_reason="PROVEN_RESOLUTION",
                    observed_at=observed_at,
                    pnl_basis=(
                        "proven_resolution_payout_minus_conservative_entry_limit;"
                        "hypothetical_gross_fees_excluded"
                    ),
                )
            elif (
                probability is not None
                and best_bid is not None
                and probability >= signal.take_profit_price - EPSILON
                and best_bid >= signal.take_profit_price - EPSILON
            ):
                self._close_signal(
                    signal,
                    exit_price=best_bid,
                    exit_reason="FIRST_OBSERVED_TAKE_PROFIT_BID",
                    observed_at=observed_at,
                    pnl_basis=(
                        "fresh_bid_minus_conservative_entry_limit;"
                        "hypothetical_gross_fees_excluded"
                    ),
                )
            elif (
                probability is not None
                and best_bid is not None
                and probability <= signal.stop_price + EPSILON
            ):
                self._close_signal(
                    signal,
                    exit_price=best_bid,
                    exit_reason="FIRST_OBSERVED_ABSOLUTE_STOP_BID",
                    observed_at=observed_at,
                    pnl_basis=(
                        "fresh_bid_minus_conservative_entry_limit;"
                        "hypothetical_gross_fees_excluded"
                    ),
                )

    @staticmethod
    def _close_signal(
        signal: ShadowSignal,
        *,
        exit_price: float,
        exit_reason: str,
        observed_at: datetime,
        pnl_basis: str,
    ) -> None:
        if signal.entry_limit_price is None or signal.hypothetical_shares is None:
            return
        gross = (
            float(exit_price) - float(signal.entry_limit_price)
        ) * float(signal.hypothetical_shares)
        signal.status = "CLOSED"
        signal.exit_observed_at = _naive_utc(observed_at)
        signal.exit_price = float(exit_price)
        signal.exit_reason = exit_reason
        signal.hypothetical_gross_pnl = gross
        signal.pnl_basis = pnl_basis
        if signal.entry_decision == "ENTERED":
            signal.classification = (
                "ENTERED_PROFIT"
                if gross > EPSILON
                else "ENTERED_LOSS"
                if gross < -EPSILON
                else "ENTERED_FLAT"
            )
        else:
            signal.classification = (
                "MISSED_PROFIT"
                if gross > EPSILON
                else "AVOIDED_LOSS"
                if gross < -EPSILON
                else "COUNTERFACTUAL_FLAT"
            )
