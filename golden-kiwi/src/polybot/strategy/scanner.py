"""Atomic research archive and persisted-lineage scanner for Micro-Cascade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
from typing import Dict, List, Optional, Sequence

from polybot_observability import current_run_id

from ..api.gamma_client import GammaClient
from ..config import ExperimentCollectionConfig, TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    get_event_metadata,
    get_strict_binary_yes,
    is_excluded_market,
    passes_liquidity_filter,
    passes_volume_filter,
    strict_binary_reason,
)
from .signals import EPSILON, EntryDecision, evaluate_entry


logger = logging.getLogger(__name__)
_BOOK_TOLERANCE = 1e-6
_NUMERIC_REASON_PART = re.compile(r"^[+-]?\d[\d.]*[a-z%]*$")
_ARCHIVE_FETCH_MIN_LIQUIDITY = 1_000.0


@dataclass(frozen=True)
class CascadeLineage:
    snapshots: Sequence[object]
    prices: tuple[float, ...]
    gap_minutes: tuple[float, ...]

    @property
    def start(self):
        return self.snapshots[0]

    @property
    def prior(self):
        return self.snapshots[-2]

    @property
    def current(self):
        return self.snapshots[-1]


def parse_end_date(value) -> Optional[datetime]:
    """Parse one Gamma deadline into an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_hours_until_resolution(
    end_date: Optional[datetime],
    now: Optional[datetime] = None,
) -> Optional[float]:
    if end_date is None:
        return None
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (end_date - reference).total_seconds() / 3600.0


def _observation_time(value) -> Optional[datetime]:
    """Parse the local Gamma page-receipt clock used for cadence evidence."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_nonnegative(market: Dict, field: str) -> Optional[float]:
    raw = market.get(field)
    if (
        raw is None
        or isinstance(raw, bool)
        or (isinstance(raw, str) and not raw.strip())
    ):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _optional_book_value(market: Dict, field: str) -> tuple[bool, Optional[float]]:
    raw = market.get(field)
    if raw in (None, ""):
        return True, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False, None
    if not math.isfinite(value) or not 0 <= value <= 1:
        return False, None
    return True, value


def _snapshot_values(market: Dict, yes_price: float) -> tuple[Optional[Dict], str]:
    if not math.isfinite(yes_price) or not 0 <= yes_price <= 1:
        return None, "invalid_yes_price"
    liquidity = _finite_nonnegative(market, "liquidity")
    volume = _finite_nonnegative(market, "volume24hr")
    if liquidity is None:
        return None, "missing_or_invalid_liquidity"
    if volume is None:
        return None, "missing_or_invalid_volume_24h"
    ok_bid, best_bid = _optional_book_value(market, "bestBid")
    ok_ask, best_ask = _optional_book_value(market, "bestAsk")
    ok_spread, spread = _optional_book_value(market, "spread")
    if not ok_bid:
        return None, "invalid_best_bid"
    if not ok_ask:
        return None, "invalid_best_ask"
    if not ok_spread:
        return None, "invalid_spread"
    if best_bid is not None and best_ask is not None:
        if best_bid > best_ask + _BOOK_TOLERANCE:
            return None, "invalid_order_book"
        calculated = best_ask - best_bid
        if spread is None:
            spread = calculated
        elif not math.isclose(spread, calculated, rel_tol=0, abs_tol=_BOOK_TOLERANCE):
            return None, "invalid_spread_consistency"
    return {
        "liquidity": liquidity,
        "volume_24h": volume,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
    }, "snapshot_valid"


def _reason_key(reason: str) -> str:
    parts = [
        part
        for part in reason.split("_")
        if part and not _NUMERIC_REASON_PART.match(part)
    ]
    return "_".join(parts) or reason


class MarketScanner:
    """Persist the buffered universe, then prove an exact 3/5-step staircase."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        history_client=None,
        *,
        experiment: Optional[ExperimentCollectionConfig] = None,
        job_name: str = "",
    ):
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        # Deliberately unused: Micro-Cascade never infers or backfills lineage.
        self.history = history_client
        self.experiment = experiment or ExperimentCollectionConfig()
        self.job_name = job_name
        self._current_snapshot_ids: Dict[str, int] = {}
        self._current_snapshots: Dict[str, object] = {}
        self.last_signal_funnel: List[Dict] = []

    def fetch_markets(self) -> List[Dict]:
        return self.gamma.get_all_tradable_markets(
            min_liquidity=min(
                self.config.min_liquidity, _ARCHIVE_FETCH_MIN_LIQUIDITY
            ),
            min_volume=0,
        )

    def _archive_decision(
        self,
        market: Dict,
        yes_price: float,
        now: datetime,
    ) -> tuple[bool, str]:
        reason = strict_binary_reason(market)
        if reason != "ok":
            return False, reason
        if is_excluded_market(market, self.config.excluded_categories):
            return False, "excluded_category"
        end_date = parse_end_date(market.get("endDate"))
        hours_left = get_hours_until_resolution(end_date, now)
        if hours_left is None:
            return False, "no_end_date"
        if hours_left < self.config.entry.min_hours_to_resolution - EPSILON:
            return False, "resolution_too_close"
        if yes_price < self.config.archive.prob_min - EPSILON:
            return False, "archive_price_below"
        if yes_price > self.config.archive.prob_max + EPSILON:
            return False, "archive_price_above"
        return True, "archive_eligible"

    def save_market_snapshots(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> int:
        """Persist catalog, selected observations, and sweep proof atomically."""
        if self.repo is None:
            raise RuntimeError("repository is required for Micro-Cascade evidence")
        attestation = self.gamma.last_sweep_attestation
        if not attestation:
            raise RuntimeError("completed Gamma sweep attestation is required")
        forced_reference = _observation_time(now) if now is not None else None
        if now is not None and forced_reference is None:
            raise ValueError("snapshot reference time must be a valid datetime")
        self._current_snapshot_ids.clear()
        self._current_snapshots.clear()
        snapshot_results: Dict[str, Dict] = {}
        saved = 0
        try:
            for market in markets:
                condition_id = str(market.get("conditionId") or "")
                if not condition_id:
                    raise ValueError("qualified Gamma market has no conditionId")
                reference = forced_reference or _observation_time(
                    market.get("_gammaObservedAt")
                )
                if reference is None:
                    raise ValueError(
                        "qualified Gamma market has no page observation clock"
                    )
                self.repo.save_market_catalog(condition_id, market, commit=False)
                yes = get_strict_binary_yes(market)
                if not yes:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": strict_binary_reason(market),
                    }
                    continue
                eligible, reason = self._archive_decision(
                    market, yes["probability"], reference
                )
                if not eligible:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                    }
                    continue
                values, values_reason = _snapshot_values(
                    market, yes["probability"]
                )
                if values is None:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": True,
                        "snapshotted": False,
                        "snapshot_reason": values_reason,
                    }
                    continue
                snapshot = self.repo.save_snapshot(
                    condition_id=condition_id,
                    probability=yes["probability"],
                    **values,
                    source_updated_at=market.get("updatedAt"),
                    market=market,
                    commit=False,
                )
                snapshot.timestamp = reference.replace(tzinfo=None)
                self._current_snapshot_ids[condition_id] = snapshot.id
                self._current_snapshots[condition_id] = snapshot
                snapshot_results[condition_id] = {
                    "snapshot_eligible": True,
                    "snapshotted": True,
                    "snapshot_reason": "snapshot_saved",
                }
                saved += 1
            self.repo.record_market_sweep(attestation, snapshot_results, commit=False)
            self.repo.commit()
            attestation["snapshot_eligible_count"] = sum(
                int(item["snapshot_eligible"])
                for item in snapshot_results.values()
            )
            attestation["snapshotted_market_count"] = saved
        except Exception:
            self.repo.rollback()
            raise
        logger.info(
            "Micro-Cascade snapshot %s개 저장 (YES %.2f~%.2f, >=%.0fh)",
            saved,
            self.config.archive.prob_min,
            self.config.archive.prob_max,
            self.config.entry.min_hours_to_resolution,
        )
        return saved

    @staticmethod
    def _snapshot_timestamp(snapshot) -> Optional[datetime]:
        raw = getattr(snapshot, "timestamp", None)
        if not isinstance(raw, datetime):
            return None
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)

    @staticmethod
    def _snapshot_probability(snapshot) -> Optional[float]:
        try:
            value = float(getattr(snapshot, "probability"))
        except (AttributeError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) and 0 <= value <= 1 else None

    def _entry_snapshot_lineage(
        self,
        condition_id: str,
        current_probability: float,
    ) -> tuple[Optional[CascadeLineage], str]:
        if self.repo is None:
            return None, "lineage_repository_missing"
        current_id = self._current_snapshot_ids.get(condition_id)
        current = self._current_snapshots.get(condition_id)
        if current_id is None or current is None:
            return None, "current_snapshot_missing"
        if (
            isinstance(current_id, bool)
            or not isinstance(current_id, int)
            or current_id <= 0
        ):
            return None, "current_snapshot_id_invalid"
        if (
            getattr(current, "id", None) != current_id
            or getattr(current, "condition_id", None) != condition_id
        ):
            return None, "current_snapshot_identity_mismatch"
        stored_probability = self._snapshot_probability(current)
        if stored_probability is None or not math.isclose(
            stored_probability, current_probability, rel_tol=0, abs_tol=EPSILON
        ):
            return None, "current_snapshot_probability_mismatch"
        current_timestamp = self._snapshot_timestamp(current)
        if current_timestamp is None:
            return None, "current_snapshot_timestamp_invalid"
        history_start = current_timestamp - timedelta(
            days=self.config.archive.retention_days
        )
        run_id = current_run_id()
        if not run_id:
            return None, "current_run_missing"
        if getattr(current, "run_id", None) != run_id:
            return None, "current_snapshot_run_mismatch"
        try:
            history = self.repo.get_entry_lineage_snapshots(
                condition_id,
                history_start.replace(tzinfo=None),
                run_id,
            )
        except Exception as error:
            logger.warning(
                "snapshot lineage 조회 실패 - condition=%s error=%s",
                condition_id,
                type(error).__name__,
            )
            return None, "snapshot_history_unavailable"
        ordered = sorted(
            history,
            key=lambda row: (
                self._snapshot_timestamp(row)
                or datetime.min.replace(tzinfo=timezone.utc),
                int(getattr(row, "id", 0) or 0),
            ),
        )
        indexes = [
            index
            for index, row in enumerate(ordered)
            if getattr(row, "id", None) == current_id
        ]
        if len(indexes) != 1:
            return None, "current_snapshot_not_persisted"
        current_index = indexes[0]
        required = self.config.entry.confirmation_steps + 1
        if current_index + 1 < required:
            return None, "insufficient_persisted_observations"
        selected = ordered[current_index - required + 1 : current_index + 1]
        if selected[-1] is not current and getattr(
            selected[-1], "id", None
        ) != current_id:
            return None, "current_snapshot_not_last"

        ids: List[int] = []
        timestamps: List[datetime] = []
        prices: List[float] = []
        for row in selected:
            row_id = getattr(row, "id", None)
            if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
                return None, "snapshot_id_invalid"
            if getattr(row, "condition_id", None) != condition_id:
                return None, "snapshot_condition_mismatch"
            timestamp = self._snapshot_timestamp(row)
            probability = self._snapshot_probability(row)
            if timestamp is None:
                return None, "snapshot_timestamp_invalid"
            if probability is None:
                return None, "snapshot_probability_invalid"
            ids.append(row_id)
            timestamps.append(timestamp)
            prices.append(probability)
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            return None, "snapshot_id_order_invalid"
        gaps = [
            (timestamps[index + 1] - timestamps[index]).total_seconds() / 60.0
            for index in range(len(timestamps) - 1)
        ]
        if any(gap <= 0 for gap in gaps):
            return None, "snapshot_timestamp_order_invalid"
        return (
            CascadeLineage(
                snapshots=tuple(selected),
                prices=tuple(prices),
                gap_minutes=tuple(gaps),
            ),
            "lineage_valid",
        )

    @staticmethod
    def _current_book_reason(snapshot, max_spread: float) -> Optional[str]:
        bid = getattr(snapshot, "best_bid", None)
        ask = getattr(snapshot, "best_ask", None)
        spread = getattr(snapshot, "spread", None)
        values = (bid, ask, spread)
        if any(value is None for value in values):
            return "current_book_missing"
        try:
            bid, ask, spread = (float(value) for value in values)
        except (TypeError, ValueError):
            return "current_book_invalid"
        if any(not math.isfinite(value) for value in (bid, ask, spread)):
            return "current_book_invalid"
        if not 0 < bid <= ask < 1:
            return "current_book_invalid"
        if spread < 0 or not math.isclose(
            spread, ask - bid, rel_tol=0, abs_tol=_BOOK_TOLERANCE
        ):
            return "current_spread_inconsistent"
        if spread > max_spread + EPSILON:
            return "current_spread_too_wide"
        return None

    def scan_buy_candidates(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
        *,
        drawdown_blocked: bool = False,
    ) -> List[Dict]:
        """Return at most one highest-liquidity candidate per event."""
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        reference = reference.astimezone(timezone.utc)
        raw_candidates: List[Dict] = []
        rejected: Dict[str, int] = {}
        self.last_signal_funnel = []

        def reject(reason: str) -> None:
            key = _reason_key(reason)
            rejected[key] = rejected.get(key, 0) + 1

        for market in markets:
            condition_id = str(market.get("conditionId") or "")
            if not condition_id:
                continue
            yes = get_strict_binary_yes(market)
            if not yes:
                reject(strict_binary_reason(market))
                continue
            if is_excluded_market(market, self.config.excluded_categories):
                reject("excluded_category")
                continue
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                reject("low_liquidity")
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                reject("low_volume")
                continue
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date, reference)
            lineage, lineage_reason = self._entry_snapshot_lineage(
                condition_id, yes["probability"]
            )
            if lineage is None:
                reject(lineage_reason)
                continue
            book_reason = self._current_book_reason(
                lineage.current, self.config.max_spread
            )
            if book_reason:
                reject(book_reason)
                continue
            decision: EntryDecision = evaluate_entry(
                lineage.prices,
                lineage.gap_minutes,
                hours_left,
                self.config.entry,
            )
            if not decision.should_enter:
                reject(decision.reason)
                continue
            event = get_event_metadata(market)
            if not event["event_id"]:
                reject("missing_event_id")
                continue
            tags = market.get("tags") or []
            tag_text = ", ".join(
                str(tag.get("label") or tag.get("slug") or "")
                for tag in tags
                if isinstance(tag, dict)
            )
            raw_candidates.append(
                {
                    "condition_id": condition_id,
                    "market_slug": market.get("slug", ""),
                    "question": market.get("question", ""),
                    "event_id": event["event_id"],
                    "event_slug": event["event_slug"],
                    "outcome": "Yes",
                    "token_id": yes["token_id"],
                    "probability": yes["probability"],
                    "yes_price": yes["probability"],
                    "prior_yes_price": lineage.prices[-2],
                    "trend_start_yes_price": lineage.prices[0],
                    "trend_prices": list(lineage.prices),
                    "trend_gap_minutes": list(lineage.gap_minutes),
                    "trend_snapshot_ids": [
                        snapshot.id for snapshot in lineage.snapshots
                    ],
                    "trend_snapshot_timestamps": [
                        self._snapshot_timestamp(snapshot)
                        for snapshot in lineage.snapshots
                    ],
                    "trend_start_snapshot_id": lineage.start.id,
                    "prior_snapshot_id": lineage.prior.id,
                    "entry_snapshot_id": lineage.current.id,
                    "confirmation_steps": decision.confirmation_steps,
                    "cumulative_move": decision.cumulative_move,
                    "min_gap_minutes": decision.min_gap_minutes,
                    "max_gap_minutes": decision.max_gap_minutes,
                    "min_step_move": decision.min_step_move,
                    "max_step_move": decision.max_step_move,
                    "liquidity": lineage.current.liquidity,
                    "volume_24h": lineage.current.volume_24h,
                    "signal_best_bid": lineage.current.best_bid,
                    "signal_best_ask": lineage.current.best_ask,
                    "signal_spread": lineage.current.spread,
                    "entry_reason": decision.reason,
                    "end_date": end_date,
                    "hours_until_resolution": hours_left,
                    "market_tags": tag_text,
                    "scan_evaluated_at": self._snapshot_timestamp(
                        lineage.current
                    ),
                }
            )

        # Persist the complete signal funnel, including siblings that lose the
        # deterministic event rank.  The primary counterfactual samples the
        # first cooldown-eligible event winner, independent of portfolio caps
        # and fresh execution success.
        ranked_by_event: Dict[str, List[Dict]] = {}
        for candidate in raw_candidates:
            ranked_by_event.setdefault(str(candidate["event_id"]), []).append(
                candidate
            )
        winners: List[Dict] = []
        for event_id, siblings in sorted(ranked_by_event.items()):
            ordered = sorted(
                siblings,
                key=lambda item: (
                    -float(item["liquidity"]),
                    str(item["condition_id"]),
                ),
            )
            sibling_count = len(ordered)
            for rank, candidate in enumerate(ordered, start=1):
                candidate["event_sibling_count"] = sibling_count
                candidate["event_rank"] = rank
                candidate["event_selected"] = rank == 1
                if rank == 1:
                    winners.append(candidate)
                else:
                    reject("event_sibling_lower_rank")
        candidates = sorted(
            winners,
            key=lambda item: (-float(item["liquidity"]), str(item["condition_id"])),
        )
        for rank, candidate in enumerate(candidates, start=1):
            candidate["global_rank"] = rank

        run_id = current_run_id()
        if not run_id and raw_candidates:
            raise RuntimeError("raw signal funnel 기록에는 current RunAudit가 필요합니다")
        position_count = (
            self.repo.get_position_count()
            if self.repo is not None
            and hasattr(self.repo, "get_position_count")
            else 0
        )
        open_notional = (
            self.repo.get_open_notional_usdc()
            if self.repo is not None
            and hasattr(self.repo, "get_open_notional_usdc")
            else 0.0
        )
        selected_condition: Optional[str] = None
        for candidate in candidates:
            event_id = str(candidate["event_id"])
            observed_at = candidate["scan_evaluated_at"] or reference
            cooldown_allowed = True
            cooldown_reason = "ok"
            if self.repo is not None and hasattr(
                self.repo, "latest_successful_raw_selected_at"
            ):
                last_selected = self.repo.latest_successful_raw_selected_at(
                    event_id
                )
                if last_selected is not None:
                    if last_selected.tzinfo is None:
                        last_selected = last_selected.replace(
                            tzinfo=timezone.utc
                        )
                    if (
                        observed_at - last_selected
                    ).total_seconds() < (
                        self.config.reentry_cooldown_hours * 3600
                    ):
                        cooldown_allowed = False
                        cooldown_reason = "raw_event_signal_cooldown"
            candidate["cooldown_allowed"] = cooldown_allowed
            candidate["cooldown_reason"] = cooldown_reason
            if (
                selected_condition is None
                and cooldown_allowed
                and not drawdown_blocked
            ):
                selected_condition = str(candidate["condition_id"])

        for candidate in raw_candidates:
            observed_at = candidate["scan_evaluated_at"] or reference
            collection_eligible = self.experiment.contains(observed_at)
            event_selected = bool(candidate["event_selected"])
            winner = next(
                (
                    item
                    for item in candidates
                    if item["event_id"] == candidate["event_id"]
                ),
                None,
            )
            cooldown_allowed = bool(
                winner and winner.get("cooldown_allowed")
            )
            cooldown_reason = (
                str(winner.get("cooldown_reason"))
                if winner is not None
                else "event_sibling_lower_rank"
            )
            self.last_signal_funnel.append(
                {
                    "run_id": run_id,
                    "condition_id": str(candidate["condition_id"]),
                    "event_id": str(candidate["event_id"]),
                    "token_id": str(candidate["token_id"]),
                    "arm": self.config.arm_name,
                    "canonical_job": self.job_name,
                    "collection_eligible": int(collection_eligible),
                    "scan_evaluated_at": observed_at.replace(tzinfo=None),
                    "trend_snapshot_ids_json": json.dumps(
                        candidate["trend_snapshot_ids"]
                    ),
                    "trend_snapshot_timestamps_json": json.dumps(
                        [
                            value.astimezone(timezone.utc).isoformat()
                            for value in candidate[
                                "trend_snapshot_timestamps"
                            ]
                        ]
                    ),
                    "trend_prices_json": json.dumps(candidate["trend_prices"]),
                    "trend_gap_minutes_json": json.dumps(
                        candidate["trend_gap_minutes"]
                    ),
                    "entry_snapshot_id": int(
                        candidate["entry_snapshot_id"]
                    ),
                    "snapshot_probability": float(
                        candidate["yes_price"]
                    ),
                    "snapshot_best_bid": float(
                        candidate["signal_best_bid"]
                    ),
                    "snapshot_best_ask": float(
                        candidate["signal_best_ask"]
                    ),
                    "snapshot_spread": float(candidate["signal_spread"]),
                    "snapshot_liquidity": float(candidate["liquidity"]),
                    "snapshot_volume_24h": float(
                        candidate["volume_24h"]
                    ),
                    "market_end_date": candidate["end_date"].astimezone(
                        timezone.utc
                    ).replace(tzinfo=None),
                    "event_sibling_count": int(
                        candidate["event_sibling_count"]
                    ),
                    "event_rank": int(candidate["event_rank"]),
                    "event_selected": int(event_selected),
                    "global_rank": (
                        int(winner["global_rank"])
                        if event_selected and winner is not None
                        else None
                    ),
                    "cooldown_allowed": int(
                        event_selected and cooldown_allowed
                    ),
                    "cooldown_reason": cooldown_reason,
                    "position_count": int(position_count),
                    "open_notional_usdc": float(open_notional),
                    "drawdown_tripped": int(drawdown_blocked),
                    "raw_selected": int(
                        event_selected
                        and str(candidate["condition_id"])
                        == selected_condition
                    ),
                    "fresh_attempt_order": None,
                    "fresh_attempted": 0,
                    "fresh_observed_at": None,
                    "fresh_best_bid": None,
                    "fresh_best_ask": None,
                    "fresh_spread": None,
                    "fresh_depth_shares": None,
                    "fresh_depth_limit_price": None,
                    "fresh_gate_passed": None,
                    "fresh_fail_reason": (
                        "not_event_winner"
                        if not event_selected
                        else "not_attempted"
                    ),
                    "execution_selected": 0,
                    "trade_id": None,
                }
            )
        if rejected:
            logger.info(
                "제외 사유 요약 - %s",
                ", ".join(
                    f"{key}: {value}"
                    for key, value in sorted(
                        rejected.items(), key=lambda item: (-item[1], item[0])
                    )
                ),
            )
        logger.info("Micro-Cascade 매수 후보 %s개 발견", len(candidates))
        return candidates

    def check_current_price(self, token_id: str, clob_client) -> float:
        try:
            return clob_client.get_midpoint(token_id)
        except Exception as error:
            logger.warning("midpoint 조회 실패 - token=%s error=%s", token_id, error)
            return 0.0
