"""Full-game direct-book trend-confirmation scanner for Golden Plum."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

from ..api.clob_client import (
    BuyBookWalk,
    ClobClientWrapper,
    build_execution_capacity_evidence,
)
from ..api.gamma_client import GammaClient
from ..config import BASELINE_EXECUTION_NOTIONAL_USDC, TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    get_event,
    get_event_metadata,
    get_match_result_sides,
    match_result_reason,
)


logger = logging.getLogger(__name__)


def _market_tags_json(market: Dict[str, Any]) -> str:
    return json.dumps(
        [
            {
                "id": tag.get("id"),
                "slug": tag.get("slug"),
                "label": tag.get("label"),
            }
            for tag in (market.get("tags") or [])
            if isinstance(tag, dict)
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class TrendConfirmation:
    """Fresh, token-aligned evidence for one first upward crossing."""

    snapshot_ids: tuple[int, ...]
    prices: tuple[float, ...]
    cumulative_move: float
    max_pullback: float
    elapsed_seconds: float


def evaluate_trend_confirmation(
    snapshots: List[Any],
    *,
    current_snapshot_id: int,
    config: TradingConfig,
) -> tuple[Optional[TrendConfirmation], str]:
    """Validate the frozen three-observation 0.75 first-cross contract."""
    required = int(config.entry.trend_observations)
    if len(snapshots) != required:
        return None, "trend_history_incomplete"
    if int(getattr(snapshots[-1], "id", -1)) != int(current_snapshot_id):
        return None, "trend_latest_snapshot_identity_mismatch"

    try:
        prices = tuple(float(snapshot.probability) for snapshot in snapshots)
        timestamps = tuple(snapshot.timestamp for snapshot in snapshots)
        snapshot_ids = tuple(int(snapshot.id) for snapshot in snapshots)
    except (AttributeError, TypeError, ValueError):
        return None, "trend_history_value_invalid"
    raw_source_minutes = [
        getattr(snapshot, "source_elapsed_minutes", None) for snapshot in snapshots
    ]
    if any(value is None for value in raw_source_minutes):
        if config.source_clock_required:
            return None, "trend_source_clock_missing"
        if not all(value is None for value in raw_source_minutes):
            return None, "trend_source_clock_partial"
        source_minutes: tuple[float, ...] = ()
    else:
        try:
            source_minutes = tuple(float(value) for value in raw_source_minutes)
        except (TypeError, ValueError):
            return None, "trend_source_clock_invalid"
        if any(not math.isfinite(value) or value < 0 for value in source_minutes):
            return None, "trend_source_clock_invalid"
    if any(not math.isfinite(price) or not 0 < price < 1 for price in prices):
        return None, "trend_history_price_invalid"
    if any(timestamp is None for timestamp in timestamps):
        return None, "trend_history_timestamp_missing"
    gaps = [
        (timestamps[index] - timestamps[index - 1]).total_seconds()
        for index in range(1, required)
    ]
    if any(
        not math.isfinite(gap)
        or gap <= 0
        or gap > config.entry.trend_max_gap_seconds + 1e-9
        for gap in gaps
    ):
        return None, "trend_snapshot_cadence_gap"
    if source_minutes and any(
        source_minutes[index] + 1e-9 < source_minutes[index - 1]
        for index in range(1, required)
    ):
        return None, "trend_source_clock_regressed"

    deltas = [prices[index] - prices[index - 1] for index in range(1, required)]
    max_pullback = max(0.0, max(-delta for delta in deltas))
    if max_pullback > config.entry.trend_max_pullback + 1e-9:
        return None, "trend_pullback_too_large"
    cumulative_move = prices[-1] - prices[0]
    if cumulative_move + 1e-9 < config.entry.trend_min_cumulative_move:
        return None, "trend_cumulative_move_too_small"
    if not (
        prices[-2] < config.entry.prob_min - 1e-9
        and config.entry.prob_min - 1e-9
        <= prices[-1]
        <= config.entry.prob_max + 1e-9
    ):
        return None, "not_first_upward_crossing"
    return (
        TrendConfirmation(
            snapshot_ids=snapshot_ids,
            prices=prices,
            cumulative_move=cumulative_move,
            max_pullback=max_pullback,
            elapsed_seconds=sum(gaps),
        ),
        "confirmed",
    )


def parse_end_date(value: Optional[str]) -> Optional[datetime]:
    """Compatibility parser used for UTC Gamma clock fields."""
    if not value:
        return None
    try:
        text = str(value).strip()
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
            if "T" in text or " " in text
            else f"{text}T00:00:00+00:00"
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_hours_until_resolution(
    end_date: Optional[datetime], now: Optional[datetime] = None
) -> Optional[float]:
    """Legacy helper retained for settlement callers/tests."""
    if end_date is None:
        return None
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (end_date - reference).total_seconds() / 3600.0


def get_hours_since_game_start(
    game_start: Optional[datetime], now: Optional[datetime] = None
) -> Optional[float]:
    if game_start is None:
        return None
    if game_start.tzinfo is None:
        game_start = game_start.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - game_start).total_seconds() / 3600.0


def _finite_nonnegative(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _elapsed_minutes(value: Any) -> Optional[float]:
    """Decode common sports-clock minute, MM:SS, and 90+N forms."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    text = str(value).strip().casefold()
    text = re.sub(r"(?:minutes?|mins?|min|m|')$", "", text).strip()
    added = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)", text)
    if added:
        return float(added.group(1)) + float(added.group(2))
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if any(not math.isfinite(number) or number < 0 for number in numbers):
        return None
    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) == 2 and 0 <= numbers[1] < 60:
        return numbers[0] + numbers[1] / 60.0
    if len(numbers) == 3 and 0 <= numbers[1] < 60 and 0 <= numbers[2] < 60:
        return numbers[0] * 60 + numbers[1] + numbers[2] / 60.0
    return None


def get_source_regulation_minute(
    event: Dict[str, Any],
) -> tuple[Optional[float], str]:
    """Normalize an explicit source clock; wall-clock age is never a substitute."""
    minute = _elapsed_minutes(event.get("elapsed"))
    period = str(event.get("period") or "").strip().casefold()
    if period in {"ht", "half time", "halftime"}:
        return 45.0, "SOURCE_HALFTIME"
    if period in {"ft", "full time", "fulltime"}:
        return 90.0, "SOURCE_FULLTIME"
    if minute is None:
        return None, "SOURCE_ELAPSED_MISSING_OR_INVALID"
    if period in {"1h", "first half", "first_half", "1", "first"}:
        return minute, "SOURCE_FIRST_HALF_ELAPSED"
    if period in {"2h", "second half", "second_half", "2", "second"}:
        if minute < 45:
            return 45.0 + minute, "SOURCE_SECOND_HALF_PERIOD_OFFSET"
        return minute, "SOURCE_TOTAL_ELAPSED"
    return None, "SOURCE_PERIOD_UNSUPPORTED"


def get_source_progress(
    event: Dict[str, Any], sport_family: str
) -> tuple[Optional[float], str]:
    """Return a comparable source minute only where the source defines one.

    Soccer has a stable regulation-minute contract. Innings, quarters and
    periods are not interchangeable with minutes, so direct US sports retain
    a null source minute and use timestamp cadence plus explicit live/ended
    lifecycle. Wall-clock age is never written into the source-minute field.
    """
    family = str(sport_family or "").strip().lower()
    if family == "soccer":
        return get_source_regulation_minute(event)
    if family in {"mlb", "nba", "nfl", "nhl"}:
        return None, f"SOURCE_CLOCK_NOT_COMPARABLE_{family.upper()}"
    return None, "SOURCE_SPORT_FAMILY_UNSUPPORTED"


class MarketScanner:
    """Archive direct books and confirm one unique rising event leader."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        clob_client: Optional[ClobClientWrapper] = None,
    ):
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.clob = clob_client
        self._walks: Dict[str, BuyBookWalk] = {}
        self._snapshot_ids: Dict[str, int] = {}
        self._event_health: Dict[str, Dict[str, Any]] = {}
        self._condition_event_health: Dict[str, Dict[str, Any]] = {}

    def _evidence_context(self) -> Dict[str, Any]:
        return {
            "sport_family": self.config.sport_family,
            "sport_profile_version": self.config.sport_profile_version,
            "protocol_sha256": self.config.preregistration_sha256,
            "classifier_version": self.config.classifier_version,
            "league_mapping_sha256": self.config.league_mapping_sha256,
            "strategy_source_digest": self.config.strategy_source_digest,
            "book_shape": self.config.book_shape,
            "expected_result_kinds": self.config.expected_result_kinds,
            "expected_market_count": self.config.expected_market_count,
            "expected_token_count": self.config.expected_token_count,
        }

    def _build_event_cycle_evidence(
        self,
        markets: List[Dict[str, Any]],
        *,
        sweep_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Derive exact event sets before any candidate or replay can use them."""

        buckets: Dict[str, Dict[str, Any]] = {}
        for index, market in enumerate(markets):
            condition_id = str(market.get("conditionId") or "").strip()
            event_id = str(get_event_metadata(market).get("event_id") or "").strip()
            if not event_id:
                event_id = f"MISSING_EVENT:{condition_id or index}"
            bucket = buckets.setdefault(
                event_id,
                {
                    "event_id": event_id,
                    "condition_occurrences": [],
                    "token_occurrences": [],
                    "identities": [],
                    "observed_result_kinds": [],
                    "missing_event_identity": event_id.startswith("MISSING_EVENT:"),
                },
            )
            if condition_id:
                bucket["condition_occurrences"].append(condition_id)
            sides = get_match_result_sides(market)
            for side in sides:
                token_id = str(side.get("token_id") or "").strip()
                result_kind = str(side.get("result_kind") or "").strip().upper()
                outcome_side = str(side.get("outcome_side") or "").strip().upper()
                if token_id:
                    bucket["token_occurrences"].append(token_id)
                if result_kind:
                    bucket["observed_result_kinds"].append(result_kind)
                if result_kind and outcome_side:
                    bucket["identities"].append((result_kind, outcome_side))

        expected_kinds = sorted(set(self.config.expected_result_kinds))
        expected_identities = (
            {
                (kind, side)
                for kind in expected_kinds
                for side in ("YES", "NO")
            }
            if self.config.sport_family == "soccer"
            else {(kind, "DIRECT") for kind in expected_kinds}
        )
        evidence: Dict[str, Dict[str, Any]] = {}
        self._condition_event_health.clear()
        for event_id, bucket in sorted(buckets.items()):
            condition_occurrences = list(bucket["condition_occurrences"])
            token_occurrences = list(bucket["token_occurrences"])
            condition_ids = sorted(set(condition_occurrences))
            token_ids = sorted(set(token_occurrences))
            observed_kinds = sorted(set(bucket["observed_result_kinds"]))
            missing_kinds = sorted(set(expected_kinds) - set(observed_kinds))
            duplicate_conditions = len(condition_occurrences) - len(condition_ids)
            duplicate_tokens = len(token_occurrences) - len(token_ids)
            duplicate_identities = len(bucket["identities"]) - len(
                set(bucket["identities"])
            )
            identity_complete = bool(
                len(bucket["identities"]) == self.config.expected_token_count
                and set(bucket["identities"]) == expected_identities
                and len(set(bucket["identities"]))
                == self.config.expected_token_count
            )
            structure_complete = bool(
                not bucket["missing_event_identity"]
                and len(condition_ids) == self.config.expected_market_count
                and len(token_ids) == self.config.expected_token_count
                and duplicate_conditions == 0
                and duplicate_tokens == 0
                and not missing_kinds
                and identity_complete
            )
            reasons: List[str] = []
            if bucket["missing_event_identity"]:
                reasons.append("missing_event_id")
            if len(condition_ids) != self.config.expected_market_count:
                reasons.append(
                    f"market_count:{len(condition_ids)}/"
                    f"{self.config.expected_market_count}"
                )
            if len(token_ids) != self.config.expected_token_count:
                reasons.append(
                    f"token_count:{len(token_ids)}/"
                    f"{self.config.expected_token_count}"
                )
            if duplicate_conditions:
                reasons.append(f"duplicate_conditions:{duplicate_conditions}")
            if duplicate_tokens:
                reasons.append(f"duplicate_tokens:{duplicate_tokens}")
            if duplicate_identities:
                reasons.append(f"duplicate_direct_identities:{duplicate_identities}")
            if missing_kinds:
                reasons.append("missing_result_kinds:" + ",".join(missing_kinds))
            if not identity_complete:
                reasons.append("direct_identity_set_mismatch")
            reason = "structure_complete" if structure_complete else ";".join(reasons)
            event_cycle_id = hashlib.sha256(
                f"{sweep_id}:{event_id}".encode()
            ).hexdigest()
            payload = {
                "event_cycle_id": event_cycle_id,
                "event_id": event_id,
                "condition_ids": condition_ids,
                "token_ids": token_ids,
                "expected_result_kinds": expected_kinds,
                "observed_result_kinds": observed_kinds,
                "missing_result_kinds": missing_kinds,
                "expected_market_count": self.config.expected_market_count,
                "observed_market_count": len(condition_ids),
                "expected_token_count": self.config.expected_token_count,
                "observed_token_count": len(token_ids),
                "duplicate_condition_count": duplicate_conditions,
                "duplicate_token_count": duplicate_tokens,
                "duplicate_identity_count": duplicate_identities,
                "identity_complete": identity_complete,
                "structure_complete": structure_complete,
                "book_complete": False,
                "complete": False,
                "reason": reason,
            }
            payload["evidence_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            evidence[event_id] = payload
            for condition_id in condition_ids:
                self._condition_event_health[condition_id] = payload
        self._event_health = evidence
        return evidence

    @staticmethod
    def _finalize_event_cycle_evidence(
        event_results: Dict[str, Dict[str, Any]],
        snapshot_results: Dict[str, Dict[str, Any]],
    ) -> None:
        for item in event_results.values():
            condition_ids = item["condition_ids"]
            book_complete = bool(
                condition_ids
                and all(
                    snapshot_results.get(condition_id, {}).get("snapshotted") is True
                    for condition_id in condition_ids
                )
            )
            item["book_complete"] = book_complete
            item["complete"] = bool(item["structure_complete"] and book_complete)
            if item["complete"]:
                item["reason"] = "complete"
            elif item["structure_complete"] and not book_complete:
                item["reason"] = "incomplete_direct_book_coverage"
            payload = {
                key: value
                for key, value in item.items()
                if key != "evidence_sha256"
            }
            item["evidence_sha256"] = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()

    def fetch_markets(self) -> List[Dict]:
        return self.gamma.get_all_tradable_markets(
            min_liquidity=self.config.min_liquidity,
            min_volume=self.config.min_cumulative_volume,
        )

    def _market_eligible(
        self, market: Dict[str, Any], now: datetime
    ) -> tuple[bool, str, Optional[datetime], Optional[float]]:
        reason, _ = match_result_reason(market)
        if reason != "ok":
            return False, reason, None, None
        event = get_event(market)
        if (
            event.get("active") is not True
            or event.get("closed") is not False
            or event.get("live") is not True
            or event.get("ended") is not False
        ):
            return False, "event_not_explicitly_in_play", None, None
        game_start = parse_end_date(
            market.get("gameStartTime")
            or event.get("startTime")
            or event.get("eventDate")
            or event.get("startDate")
            or event.get("eventStartTime")
            or event.get("gameStartTime")
        )
        in_play_hours = get_hours_since_game_start(game_start, now)
        if in_play_hours is None:
            return False, "game_start_time_missing", game_start, None
        if in_play_hours < self.config.entry.hours_min - 1e-9:
            return False, "outside_in_play_window", game_start, in_play_hours
        if (
            self.config.archive.hours_max is not None
            and in_play_hours > self.config.archive.hours_max + 1e-9
        ):
            return False, "outside_in_play_window", game_start, in_play_hours
        return True, "archive_eligible", game_start, in_play_hours

    def save_market_snapshots(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> int:
        """Persist direct outcome levels, exact-$5 walks, and sweep proof."""
        if self.repo is None or self.clob is None:
            raise RuntimeError("repository and CLOB client are required")
        attestation = self.gamma.last_sweep_attestation
        if not attestation:
            raise RuntimeError("completed Gamma sweep attestation is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        observed_at = reference.astimezone(timezone.utc).replace(tzinfo=None)
        sweep_id = str(attestation.get("sweep_id") or "").strip()
        if not sweep_id:
            raise RuntimeError("completed Gamma sweep has no sweep_id")
        event_results = self._build_event_cycle_evidence(
            markets,
            sweep_id=sweep_id,
        )
        evidence_context = self._evidence_context()

        sides = [
            side
            for market in markets
            for side in get_match_result_sides(market)
        ]
        token_ids = list(
            dict.fromkeys(str(side["token_id"]) for side in sides)
        )
        self._walks = self.clob.get_buy_book_walks(
            token_ids, notional_usdc=BASELINE_EXECUTION_NOTIONAL_USDC
        )
        self._snapshot_ids.clear()
        snapshot_results: Dict[str, Dict[str, Any]] = {}
        saved = 0
        try:
            processed_conditions: set[str] = set()
            for market in markets:
                condition_id = str(market.get("conditionId") or "").strip()
                if not condition_id:
                    raise ValueError("qualified Gamma market has no conditionId")
                if condition_id in processed_conditions:
                    continue
                processed_conditions.add(condition_id)
                event_health = self._condition_event_health.get(condition_id)
                if event_health is None:
                    raise RuntimeError(
                        "qualified condition has no event-cycle evidence"
                    )
                self.repo.save_market_catalog(
                    condition_id,
                    market,
                    evidence_context=evidence_context,
                    event_cycle=event_health,
                    live_sweep_id=sweep_id,
                    seen_at=observed_at,
                    commit=False,
                )
                eligible, reason, _game_start, _in_play_hours = self._market_eligible(
                    market, reference
                )
                if not eligible:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                        "event_id": event_health["event_id"],
                        "event_cycle_id": event_health["event_cycle_id"],
                        "event_set_complete": event_health["complete"],
                        "event_set_reason": event_health["reason"],
                    }
                    continue
                event = get_event(market)
                event_meta = get_event_metadata(market)
                source_minute, clock_reason = get_source_progress(
                    event, self.config.sport_family
                )
                outcomes = get_match_result_sides(market)
                saved_for_condition = 0
                for outcome in outcomes:
                    token_id = str(outcome["token_id"])
                    walk = self._walks.get(token_id)
                    if walk is None:
                        continue
                    midpoint = (
                        (walk.best_bid + walk.best_ask) / 2.0
                        if walk.best_bid is not None
                        else None
                    )
                    book_json = (
                        self.clob.get_cached_book_evidence(token_id)
                        if callable(
                            getattr(self.clob, "get_cached_book_evidence", None)
                        )
                        else None
                    )
                    execution_capacity_json = None
                    if self.config.scaling_notionals_usdc:
                        if book_json is None:
                            raise RuntimeError(
                                "simulation scaling evidence requires a cached full book"
                            )
                        execution_capacity_json = build_execution_capacity_evidence(
                            book_json,
                            self.config.scaling_notionals_usdc,
                        )
                    snapshot = self.repo.save_snapshot(
                        condition_id=condition_id,
                        event_id=event_meta["event_id"],
                        token_id=token_id,
                        outcome=str(outcome["outcome"]),
                        outcome_side=str(outcome["outcome_side"]),
                        result_kind=str(outcome["result_kind"]),
                        probability=walk.vwap,
                        midpoint=midpoint,
                        liquidity=_finite_nonnegative(
                            market.get("liquidityNum", market.get("liquidity"))
                        ),
                        volume_24h=_finite_nonnegative(market.get("volume24hr")),
                        best_bid=walk.best_bid,
                        best_ask=walk.best_ask,
                        spread=walk.spread,
                        source_updated_at=market.get("updatedAt"),
                        source_elapsed_minutes=source_minute,
                        source_clock_reason=clock_reason,
                        book_json=book_json,
                        execution_capacity_json=execution_capacity_json,
                        league_code=(
                            market.get("leagueCode") or self.config.sport_family
                        ),
                        league_name=(
                            market.get("leagueName")
                            or self.config.sport_family.upper()
                        ),
                        market_tags_json=_market_tags_json(market),
                        evidence_context=evidence_context,
                        event_cycle_id=event_health["event_cycle_id"],
                        event_set_complete=event_health["complete"],
                        event_set_reason=event_health["reason"],
                        commit=False,
                    )
                    snapshot.timestamp = observed_at
                    self._snapshot_ids[token_id] = snapshot.id
                    saved_for_condition += 1
                    saved += 1
                snapshot_results[condition_id] = {
                    "snapshot_eligible": True,
                    "snapshotted": saved_for_condition == 2,
                    "snapshot_reason": (
                        "direct_outcome_exact_5_books_saved"
                        if saved_for_condition == 2
                        else f"direct_book_coverage:{saved_for_condition}/2"
                    ),
                    "event_id": event_health["event_id"],
                    "event_cycle_id": event_health["event_cycle_id"],
                    "event_set_complete": event_health["complete"],
                    "event_set_reason": event_health["reason"],
                }

            self._finalize_event_cycle_evidence(event_results, snapshot_results)
            self.repo.finalize_staged_event_cycle_health(event_results)
            self.repo.record_market_sweep(
                attestation,
                snapshot_results,
                event_results,
                evidence_context=evidence_context,
                commit=False,
            )
            self.repo.commit()
            attestation["snapshot_eligible_count"] = sum(
                int(item["snapshot_eligible"])
                for item in snapshot_results.values()
            )
            attestation["snapshotted_market_count"] = sum(
                int(item["snapshotted"]) for item in snapshot_results.values()
            )
        except Exception:
            self.repo.rollback()
            raise
        logger.info(
            "Golden Plum %s direct snapshots=%s complete_markets=%s/%s "
            "complete_events=%s/%s",
            self.config.sport_family,
            saved,
            sum(int(item["snapshotted"]) for item in snapshot_results.values()),
            len(markets),
            sum(int(item["complete"]) for item in event_results.values()),
            len(event_results),
        )
        return saved

    def follow_tracked_conditions(
        self,
        current_markets: List[Dict[str, Any]],
        *,
        now: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, int]:
        """Boundedly follow conditions that disappeared from live discovery.

        This queue is independent of entry episodes and trades.  A game that
        never generated an order is still followed until Gamma supplies a
        unique terminal one-hot payout; source gaps remain explicit and retry.
        """

        if self.repo is None:
            raise RuntimeError("repository is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is not None:
            reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
        max_conditions = limit or (
            self.config.expected_market_count
            if self.config.sport_family == "soccer"
            else 4
        )
        current_ids = {
            str(market.get("conditionId") or "").strip()
            for market in current_markets
            if str(market.get("conditionId") or "").strip()
        }
        due = self.repo.get_due_followup_catalogs(
            now=reference,
            evidence_context=self._evidence_context(),
            exclude_condition_ids=current_ids,
            limit=max_conditions,
        )
        stats = {
            "due": len(due),
            "attempted": 0,
            "terminal": 0,
            "pending": 0,
            "source_missing": 0,
        }
        for catalog in due:
            condition_id = str(catalog.condition_id)
            market = self.gamma.get_market_by_condition_id(condition_id)
            stats["attempted"] += 1
            if market is None:
                if self.clob is not None:
                    try:
                        clob_proof = self.clob.get_market_resolution(condition_id)
                    except Exception as error:
                        logger.warning(
                            "CLOB tracked-condition fallback 실패 - "
                            "condition=%s error=%s",
                            condition_id,
                            type(error).__name__,
                        )
                    else:
                        if clob_proof.status == "RESOLVED":
                            try:
                                self.repo.record_followup_clob_resolution(
                                    condition_id,
                                    clob_proof,
                                    attempted_at=reference,
                                    evidence_context=self._evidence_context(),
                                    commit=True,
                                )
                            except ValueError as error:
                                logger.error(
                                    "CLOB tracked-condition identity 검증 실패 - "
                                    "condition=%s error=%s",
                                    condition_id,
                                    str(error),
                                )
                                self.repo.record_followup_missing(
                                    condition_id,
                                    attempted_at=reference,
                                    reason="clob_resolution_identity_mismatch",
                                    commit=True,
                                )
                                stats["source_missing"] += 1
                            else:
                                stats["terminal"] += 1
                            continue
                self.repo.record_followup_missing(
                    condition_id,
                    attempted_at=reference,
                    commit=True,
                )
                stats["source_missing"] += 1
                continue
            updated = self.repo.record_followup_market(
                condition_id,
                market,
                attempted_at=reference,
                evidence_context=self._evidence_context(),
                commit=True,
            )
            if updated.followup_status == "TERMINAL":
                stats["terminal"] += 1
            else:
                stats["pending"] += 1
        if due:
            logger.info(
                "tracked condition follow-up - sport=%s attempted=%s "
                "terminal=%s pending=%s source_missing=%s",
                self.config.sport_family,
                stats["attempted"],
                stats["terminal"],
                stats["pending"],
                stats["source_missing"],
            )
        return stats

    def scan_buy_candidates(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> List[Dict]:
        """Select a unique direct-book leader after a fresh first-cross trend."""
        if self.repo is None:
            raise RuntimeError("repository is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        experiment_start = parse_end_date(self.config.experiment_start_utc)
        experiment_end = parse_end_date(self.config.experiment_entry_end_utc)
        if (
            experiment_start is None
            or experiment_end is None
            or not (experiment_start <= reference < experiment_end)
        ):
            logger.info("outside frozen entry period - now=%s", reference.isoformat())
            return []

        markets_by_event: Dict[str, Dict[str, Dict[str, Any]]] = {}
        rejected: Dict[str, int] = {}
        for market in markets:
            eligible, reason, game_start, in_play_hours = self._market_eligible(
                market, reference
            )
            if not eligible:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            event_meta = get_event_metadata(market)
            event_id = event_meta["event_id"]
            if not event_id:
                rejected["missing_event_id"] = rejected.get("missing_event_id", 0) + 1
                continue
            condition_id = str(market.get("conditionId") or "").strip()
            event_health = self._condition_event_health.get(condition_id)
            if event_health is not None and event_health.get("complete") is not True:
                rejected["event_cycle_complete_set_required"] = rejected.get(
                    "event_cycle_complete_set_required", 0
                ) + 1
                continue
            source_minute, clock_reason = get_source_progress(
                get_event(market), self.config.sport_family
            )
            if self.config.source_clock_required and source_minute is None:
                rejected[clock_reason] = rejected.get(clock_reason, 0) + 1
                continue
            if (
                source_minute is not None
                and source_minute < self.config.entry.min_source_minute - 1e-9
            ):
                rejected["before_match_source_window"] = rejected.get(
                    "before_match_source_window", 0
                ) + 1
                continue
            if (
                source_minute is not None
                and self.config.entry.max_source_minute is not None
                and source_minute
                > self.config.entry.max_source_minute + 1e-9
            ):
                rejected["after_source_window"] = rejected.get(
                    "after_source_window", 0
                ) + 1
                continue
            outcomes = get_match_result_sides(market)
            if len(outcomes) != 2:
                rejected["direct_outcome_identity_gap"] = rejected.get(
                    "direct_outcome_identity_gap", 0
                ) + 1
                continue
            if not condition_id:
                rejected["missing_condition_id"] = rejected.get(
                    "missing_condition_id", 0
                ) + 1
                continue
            bucket = markets_by_event.setdefault(event_id, {})
            if condition_id in bucket:
                bucket[condition_id] = {"duplicate": True}
                continue
            bucket[condition_id] = {
                "market": market,
                "outcomes": outcomes,
                "game_start": game_start,
                "in_play_hours": in_play_hours,
                "source_minute": source_minute,
                "clock_reason": clock_reason,
                "event_slug": event_meta["event_slug"],
            }

        candidates: List[Dict[str, Any]] = []
        for event_id, result_markets in markets_by_event.items():
            if len(result_markets) != self.config.expected_market_count or any(
                item.get("duplicate") for item in result_markets.values()
            ):
                rejected["incomplete_or_duplicate_event_market_set"] = rejected.get(
                    "incomplete_or_duplicate_event_market_set", 0
                ) + 1
                continue
            ranked: List[Dict[str, Any]] = []
            event_invalid = False
            for condition_id in sorted(result_markets):
                context = result_markets[condition_id]
                market = context["market"]
                for outcome in context["outcomes"]:
                    token_id = str(outcome["token_id"])
                    walk = self._walks.get(token_id)
                    snapshot_id = self._snapshot_ids.get(token_id)
                    if (
                        walk is None
                        or snapshot_id is None
                        or walk.best_bid is None
                        or walk.spread is None
                    ):
                        event_invalid = True
                        break
                    ranked.append(
                        {
                            "market": market,
                            "context": context,
                            "outcome": outcome,
                            "walk": walk,
                            "snapshot_id": snapshot_id,
                            "midpoint": (walk.best_bid + walk.best_ask) / 2.0,
                        }
                    )
                if event_invalid:
                    break
            observed_kinds = {
                str(item["outcome"].get("result_kind")) for item in ranked
            }
            if (
                event_invalid
                or len(ranked) != self.config.expected_token_count
                or len(
                    {
                        str(item["outcome"].get("token_id")) for item in ranked
                    }
                )
                != self.config.expected_token_count
                or observed_kinds != set(self.config.expected_result_kinds)
            ):
                rejected["complete_direct_book_set_required"] = rejected.get(
                    "complete_direct_book_set_required", 0
                ) + 1
                continue
            ranked.sort(
                key=lambda item: (
                    -float(item["midpoint"]),
                    str(item["outcome"]["candidate_kind"]),
                )
            )
            leader, runner_up = ranked[0], ranked[1]
            margin = float(leader["midpoint"]) - float(runner_up["midpoint"])
            if margin + 1e-9 < self.config.entry.min_leader_margin:
                rejected["leader_margin_too_small"] = rejected.get(
                    "leader_margin_too_small", 0
                ) + 1
                continue
            walk = leader["walk"]
            if walk.spread > self.config.entry.max_entry_spread + 1e-9:
                rejected["leader_spread_too_wide"] = rejected.get(
                    "leader_spread_too_wide", 0
                ) + 1
                continue
            if not (
                self.config.entry.prob_min - 1e-9
                <= walk.vwap
                <= self.config.entry.prob_max + 1e-9
            ):
                rejected["leader_vwap_outside_entry_band"] = rejected.get(
                    "leader_vwap_outside_entry_band", 0
                ) + 1
                continue

            token_id = str(leader["outcome"]["token_id"])
            trend, trend_reason = evaluate_trend_confirmation(
                self.repo.get_recent_token_snapshots(
                    token_id,
                    limit=self.config.entry.trend_observations,
                ),
                current_snapshot_id=int(leader["snapshot_id"]),
                config=self.config,
            )
            if trend is None:
                rejected[trend_reason] = rejected.get(trend_reason, 0) + 1
                continue

            market = leader["market"]
            context = leader["context"]
            outcome = leader["outcome"]
            episode = self.repo.claim_entry_episode(
                token_id=str(outcome["token_id"]),
                condition_id=str(market.get("conditionId") or ""),
                event_id=event_id,
                outcome=str(outcome["outcome"]),
                entry_snapshot_id=int(leader["snapshot_id"]),
                exact_vwap=float(walk.vwap),
                arm_prob_min=self.config.entry.prob_min,
                arm_prob_max=self.config.entry.prob_max,
                observed_at=reference.astimezone(timezone.utc).replace(tzinfo=None),
                game_start_time=(
                    context["game_start"].astimezone(timezone.utc).replace(tzinfo=None)
                    if context["game_start"] is not None
                    else None
                ),
                in_play_hours=context["in_play_hours"],
                source_elapsed_minutes=context["source_minute"],
                trend_start_snapshot_id=trend.snapshot_ids[0],
                trend_middle_snapshot_id=trend.snapshot_ids[-2],
                trend_observations=len(trend.snapshot_ids),
                trend_cumulative_move=trend.cumulative_move,
                trend_max_pullback=trend.max_pullback,
                trend_elapsed_seconds=trend.elapsed_seconds,
            )
            if episode is None:
                rejected["event_or_token_already_claimed"] = rejected.get(
                    "event_or_token_already_claimed", 0
                ) + 1
                continue
            tags = market.get("tags") or []
            tag_text = ", ".join(
                str(tag.get("label") or tag.get("slug") or "")
                for tag in tags
                if isinstance(tag, dict)
            )
            candidates.append(
                {
                    "condition_id": str(market.get("conditionId") or ""),
                    "market_slug": market.get("slug", ""),
                    "question": market.get("question", ""),
                    "event_id": event_id,
                    "event_slug": context["event_slug"],
                    "outcome": outcome["outcome"],
                    "outcome_side": outcome["outcome_side"],
                    "outcome_index": outcome["token_index"],
                    "result_kind": outcome["result_kind"],
                    "candidate_kind": outcome["candidate_kind"],
                    "event_token_ids": [
                        str(item["outcome"]["token_id"]) for item in ranked
                    ],
                    "league_code": (
                        market.get("leagueCode") or self.config.sport_family
                    ),
                    "league_name": (
                        market.get("leagueName") or self.config.sport_family.upper()
                    ),
                    "token_id": str(outcome["token_id"]),
                    "probability": walk.vwap,
                    "entry_snapshot_id": int(leader["snapshot_id"]),
                    "trend_start_snapshot_id": trend.snapshot_ids[0],
                    "trend_middle_snapshot_id": trend.snapshot_ids[-2],
                    "trend_snapshot_ids": list(trend.snapshot_ids),
                    "trend_prices": list(trend.prices),
                    "trend_cumulative_move": trend.cumulative_move,
                    "trend_max_pullback": trend.max_pullback,
                    "trend_elapsed_seconds": trend.elapsed_seconds,
                    "entry_episode_id": episode.id,
                    "yes_probability": float(outcome["yes_probability"]),
                    "selected_midpoint": float(leader["midpoint"]),
                    "runner_up_midpoint": float(runner_up["midpoint"]),
                    "leader_margin": margin,
                    "liquidity": _finite_nonnegative(
                        market.get("liquidityNum", market.get("liquidity"))
                    ),
                    "volume_24h": _finite_nonnegative(market.get("volume24hr")),
                    "best_bid": walk.best_bid,
                    "best_ask": walk.best_ask,
                    "spread": walk.spread,
                    "entry_vwap": walk.vwap,
                    "entry_shares": walk.shares,
                    "entry_limit_price": walk.limit_price,
                    "entry_levels_used": walk.levels_used,
                    "entry_reason": (
                        f"{self.config.book_shape}_full_game_first_cross_trend"
                    ),
                    "game_start_time": context["game_start"],
                    "in_play_hours": context["in_play_hours"],
                    "hours_until_resolution": context["in_play_hours"],
                    "source_elapsed_minutes": context["source_minute"],
                    "source_clock_reason": context["clock_reason"],
                    "market_tags": (
                        f"{tag_text}, league={market.get('leagueCode')}, "
                        f"candidate={outcome['candidate_kind']}, "
                        f"leader_margin={margin:.6f}"
                    ).strip(", "),
                    "market_tags_json": _market_tags_json(market),
                }
            )

        if rejected:
            logger.info(
                "entry exclusion summary - %s",
                ", ".join(
                    f"{key}={value}" for key, value in sorted(rejected.items())
                ),
            )
        candidates.sort(
            key=lambda item: (
                str(item["event_id"]),
                str(item["token_id"]),
            )
        )
        logger.info(
            "Golden Plum %s full-game confirmations=%s target=%.2f sl=-%.2f",
            self.config.sport_family,
            len(candidates),
            self.config.entry.take_profit_price,
            self.config.entry.stop_loss_delta,
        )
        return candidates

    def check_current_price(self, token_id: str, clob_client) -> float:
        try:
            return clob_client.get_buy_book_walk(
                token_id, notional_usdc=BASELINE_EXECUTION_NOTIONAL_USDC
            ).vwap
        except Exception as error:
            logger.warning(
                "exact book lookup failed - token=%s error=%s",
                token_id,
                type(error).__name__,
            )
            return 0.0
