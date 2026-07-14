"""Gamma universe archive and threshold-crossing scanner for Final Five."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
import re
from typing import Dict, List, Optional

from polybot_observability import current_run_id

from ..api.gamma_client import GammaClient
from ..config import TradingConfig
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
_PAPAYA_ARCHIVE_MIN_LIQUIDITY = 1_000.0


def parse_end_date(end_date_str: Optional[str]) -> Optional[datetime]:
    """Parse a Gamma endDate into an aware UTC datetime."""
    if not end_date_str:
        return None
    try:
        text = str(end_date_str).strip()
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
            if "T" in text
            else f"{text}T00:00:00+00:00"
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_hours_until_resolution(
    end_date: Optional[datetime],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Return hours to resolution, treating naive datetimes as UTC."""
    if end_date is None:
        return None
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (end_date - reference).total_seconds() / 3600.0


def _finite_nonnegative(market: Dict, field: str) -> Optional[float]:
    if field not in market:
        return None
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


def _numeric_field_missing(market: Dict, field: str) -> bool:
    if field not in market:
        return True
    raw = market.get(field)
    return raw is None or (isinstance(raw, str) and not raw.strip())


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
    """Validate quote/numeric fields before they become archive evidence."""
    if not math.isfinite(yes_price) or not 0 <= yes_price <= 1:
        return None, "invalid_yes_price"
    liquidity = _finite_nonnegative(market, "liquidity")
    volume = _finite_nonnegative(market, "volume24hr")
    if liquidity is None:
        reason = (
            "missing_liquidity"
            if _numeric_field_missing(market, "liquidity")
            else "invalid_liquidity"
        )
        return None, reason
    if volume is None:
        reason = (
            "missing_volume_24h"
            if _numeric_field_missing(market, "volume24hr")
            else "invalid_volume_24h"
        )
        return None, reason
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
    """Archive a broad research universe, then scan the narrow entry set."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        history_client=None,
    ):
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        # Kept in the signature for shared construction code; Final Five relies
        # only on timestamped local sweep evidence, never inferred backfill.
        self.history = history_client
        self._prior_snapshots: Dict[str, object] = {}
        self._current_snapshot_ids: Dict[str, int] = {}
        self._current_snapshots: Dict[str, object] = {}

    def fetch_markets(self) -> List[Dict]:
        # The archive must keep the $1k baseline even when the entry cohort
        # raises its liquidity threshold.  Otherwise a first crossing rejected
        # by the higher entry threshold would disappear from durable history
        # and a later re-crossing could be misclassified as the first one.
        archive_min_liquidity = min(
            self.config.min_liquidity, _PAPAYA_ARCHIVE_MIN_LIQUIDITY
        )
        return self.gamma.get_all_tradable_markets(
            min_liquidity=archive_min_liquidity,
            min_volume=0,
        )

    def _archive_decision(
        self,
        market: Dict,
        yes_price: float,
        now: datetime,
    ) -> tuple[bool, str, Optional[datetime], Optional[float]]:
        strict_reason = strict_binary_reason(market)
        if strict_reason != "ok":
            return False, strict_reason, None, None
        if is_excluded_market(market, self.config.excluded_categories):
            return False, "excluded_category", None, None
        end_date = parse_end_date(market.get("endDate"))
        hours_left = get_hours_until_resolution(end_date, now)
        if hours_left is None:
            return False, "no_end_date", end_date, None
        if hours_left < 0:
            return False, "already_resolved", end_date, hours_left
        if hours_left > self.config.archive.hours_max + 1e-9:
            return False, f"archive_too_early_{hours_left:.1f}h", end_date, hours_left
        if yes_price < self.config.archive.prob_min - 1e-9:
            return False, f"archive_price_below_{yes_price:.3f}", end_date, hours_left
        return True, "archive_eligible", end_date, hours_left

    def save_market_snapshots(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> int:
        """Persist catalog, eligible quotes, and derived sweep proof atomically."""
        if self.repo is None:
            raise RuntimeError("repository is required for Final Five archive evidence")
        attestation = self.gamma.last_sweep_attestation
        if not attestation:
            raise RuntimeError("completed Gamma sweep attestation is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        self._prior_snapshots.clear()
        self._current_snapshot_ids.clear()
        self._current_snapshots.clear()
        snapshot_results: Dict[str, Dict] = {}
        saved = 0
        try:
            for market in markets:
                condition_id = str(market.get("conditionId") or "")
                if not condition_id:
                    raise ValueError("qualified Gamma market has no conditionId")
                self.repo.save_market_catalog(condition_id, market, commit=False)

                yes = get_strict_binary_yes(market)
                if not yes:
                    reason = strict_binary_reason(market)
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                    }
                    continue
                yes_price = yes["probability"]
                eligible, reason, _, _ = self._archive_decision(
                    market, yes_price, reference
                )
                if not eligible:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                    }
                    continue
                values, values_reason = _snapshot_values(market, yes_price)
                if values is None:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": True,
                        "snapshotted": False,
                        "snapshot_reason": values_reason,
                    }
                    continue

                prior = self.repo.get_latest_snapshot(condition_id)
                if prior is not None:
                    self._prior_snapshots[condition_id] = prior
                snapshot = self.repo.save_snapshot(
                    condition_id=condition_id,
                    probability=yes_price,
                    **values,
                    source_updated_at=market.get("updatedAt"),
                    commit=False,
                )
                # Repository rows are UTC-naive for SQLite compatibility.  Use
                # the sweep observation time supplied to this method so the
                # lineage gap is deterministic and independently replayable.
                snapshot.timestamp = reference.astimezone(timezone.utc).replace(
                    tzinfo=None
                )
                self._current_snapshot_ids[condition_id] = snapshot.id
                self._current_snapshots[condition_id] = snapshot
                snapshot_results[condition_id] = {
                    "snapshot_eligible": True,
                    "snapshotted": True,
                    "snapshot_reason": "snapshot_saved",
                }
                saved += 1

            self.repo.record_market_sweep(
                attestation, snapshot_results, commit=False
            )
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
            "Final Five research snapshot %s개 저장 (strict binary, YES>=%.2f, <=%.0fh)",
            saved,
            self.config.archive.prob_min,
            self.config.archive.hours_max,
        )
        return saved

    def _prior_snapshot(self, condition_id: str):
        cached = self._prior_snapshots.get(condition_id)
        if cached is not None:
            return cached
        if self.repo is None:
            return None
        return self.repo.get_latest_snapshot_before_run(
            condition_id, run_id=current_run_id()
        )

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
    ) -> tuple[Optional[object], Optional[object], str]:
        """Prove a fresh, persisted, never-before-crossed snapshot pair.

        The snapshot table itself is the durable one-shot state.  Once any
        stored observation reaches the configured crossing threshold, every
        later re-crossing is rejected even when the original attempt failed a
        downstream window, liquidity, volume, event-cap, or fresh-ask check.
        """
        if self.repo is None:
            return None, None, "lineage_repository_missing"
        current_id = self._current_snapshot_ids.get(condition_id)
        current = self._current_snapshots.get(condition_id)
        if current_id is None or current is None:
            return None, None, "current_snapshot_missing"
        if (
            isinstance(current_id, bool)
            or not isinstance(current_id, int)
            or current_id <= 0
        ):
            return None, None, "current_snapshot_id_invalid"
        if getattr(current, "id", None) != current_id:
            return None, None, "current_snapshot_id_mismatch"
        if getattr(current, "condition_id", None) != condition_id:
            return None, None, "current_snapshot_condition_mismatch"
        stored_current_probability = self._snapshot_probability(current)
        if stored_current_probability is None or not math.isclose(
            stored_current_probability,
            current_probability,
            rel_tol=0,
            abs_tol=EPSILON,
        ):
            return None, None, "current_snapshot_probability_mismatch"
        current_timestamp = self._snapshot_timestamp(current)
        if current_timestamp is None:
            return None, None, "current_snapshot_timestamp_invalid"

        # Query the persisted history rather than trusting only process-local
        # caches.  The 60-day minimum retention is far wider than Papaya's
        # seven-day archive envelope and three-day entry horizon.
        history_start = current_timestamp - timedelta(
            days=self.config.archive.retention_days
        )
        try:
            history = self.repo.get_snapshots_since(
                condition_id,
                history_start.replace(tzinfo=None),
            )
        except Exception as error:
            logger.warning(
                "snapshot lineage 조회 실패 - condition=%s error=%s",
                condition_id,
                type(error).__name__,
            )
            return None, None, "snapshot_history_unavailable"

        ordered = sorted(
            history,
            key=lambda row: (
                self._snapshot_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc),
                int(getattr(row, "id", 0) or 0),
            ),
        )
        current_indexes = [
            index
            for index, row in enumerate(ordered)
            if getattr(row, "id", None) == current_id
        ]
        if len(current_indexes) != 1:
            return None, None, "current_snapshot_not_persisted"
        current_index = current_indexes[0]
        if current_index == 0:
            return None, current, "prior_snapshot_missing"

        prior = ordered[current_index - 1]
        cached_prior = self._prior_snapshot(condition_id)
        if cached_prior is None or getattr(cached_prior, "id", None) != getattr(
            prior, "id", None
        ):
            return None, current, "prior_snapshot_lineage_mismatch"
        prior_timestamp = self._snapshot_timestamp(prior)
        if prior_timestamp is None or prior_timestamp >= current_timestamp:
            return None, current, "prior_snapshot_timestamp_invalid"
        gap_minutes = (current_timestamp - prior_timestamp).total_seconds() / 60.0
        if gap_minutes > self.config.max_snapshot_gap_minutes + EPSILON:
            return None, current, f"prior_snapshot_stale_{gap_minutes:.1f}m"

        prior_probability = self._snapshot_probability(prior)
        if prior_probability is None:
            return None, current, "prior_snapshot_probability_invalid"
        for historical in ordered[:current_index]:
            probability = self._snapshot_probability(historical)
            if probability is None:
                return None, current, "snapshot_history_probability_invalid"
            if probability >= self.config.entry.prob_min - EPSILON:
                return None, current, "first_crossing_already_observed"
        return prior, current, "lineage_valid"

    def scan_buy_candidates(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> List[Dict]:
        """Return markets that have just crossed into the Final Five band."""
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        candidates: List[Dict] = []
        rejected: Dict[str, int] = {}
        for market in markets:
            condition_id = str(market.get("conditionId") or "")
            if not condition_id:
                continue
            yes = get_strict_binary_yes(market)
            if not yes:
                reason = strict_binary_reason(market)
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            if is_excluded_market(market, self.config.excluded_categories):
                rejected["excluded_category"] = rejected.get("excluded_category", 0) + 1
                continue
            if not passes_liquidity_filter(market, self.config.min_liquidity):
                rejected["low_liquidity"] = rejected.get("low_liquidity", 0) + 1
                continue
            if not passes_volume_filter(market, self.config.min_volume_24h):
                rejected["low_volume"] = rejected.get("low_volume", 0) + 1
                continue
            end_date = parse_end_date(market.get("endDate"))
            hours_left = get_hours_until_resolution(end_date, reference)
            prior, current_snapshot, lineage_reason = self._entry_snapshot_lineage(
                condition_id,
                yes["probability"],
            )
            if lineage_reason != "lineage_valid":
                key = _reason_key(lineage_reason)
                rejected[key] = rejected.get(key, 0) + 1
                continue
            decision: EntryDecision = evaluate_entry(
                prior.probability,
                yes["probability"],
                hours_left,
                self.config.entry,
            )
            if not decision.should_enter:
                key = _reason_key(decision.reason)
                rejected[key] = rejected.get(key, 0) + 1
                continue

            event = get_event_metadata(market)
            if not event["event_id"]:
                # Per-event exposure is a hard risk limit.  Falling back to a
                # condition id would silently turn every market with missing
                # Gamma event metadata into its own event and bypass that cap.
                rejected["missing_event_id"] = (
                    rejected.get("missing_event_id", 0) + 1
                )
                continue
            tags = market.get("tags") or []
            tag_text = ", ".join(
                str(tag.get("label") or tag.get("slug") or "")
                for tag in tags
                if isinstance(tag, dict)
            )
            candidate = {
                "condition_id": condition_id,
                "market_slug": market.get("slug", ""),
                "question": market.get("question", ""),
                "event_id": event["event_id"],
                "event_slug": event["event_slug"],
                "outcome": "Yes",
                "token_id": yes["token_id"],
                "probability": yes["probability"],
                "yes_price": yes["probability"],
                "prior_yes_price": prior.probability,
                "entry_snapshot_id": current_snapshot.id,
                "liquidity": current_snapshot.liquidity,
                "volume_24h": current_snapshot.volume_24h,
                "best_bid": current_snapshot.best_bid,
                "best_ask": current_snapshot.best_ask,
                "spread": current_snapshot.spread,
                "entry_reason": decision.reason,
                "end_date": end_date,
                "hours_until_resolution": hours_left,
                "market_tags": tag_text,
            }
            candidates.append(candidate)

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
        logger.info("Final Five 매수 후보 %s개 발견", len(candidates))
        return candidates

    def check_current_price(self, token_id: str, clob_client) -> float:
        try:
            return clob_client.get_midpoint(token_id)
        except Exception as error:
            logger.warning("midpoint 조회 실패 - token=%s error=%s", token_id, error)
            return 0.0
