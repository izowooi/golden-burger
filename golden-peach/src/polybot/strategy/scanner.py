"""Direct six-token kickoff scanner for Golden Peach."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import re
from typing import Any, Dict, List, Optional

from ..api.clob_client import BuyBookWalk, ClobClientWrapper
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    get_event,
    get_event_metadata,
    get_match_result_sides,
    match_result_reason,
)


logger = logging.getLogger(__name__)
_RESULT_KINDS = frozenset({"HOME", "DRAW", "AWAY"})


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


class MarketScanner:
    """Archive six direct books and select one unique event leader."""

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
        if not (
            self.config.entry.hours_min - 1e-9
            <= in_play_hours
            <= self.config.archive.hours_max + 1e-9
        ):
            return False, "outside_in_play_window", game_start, in_play_hours
        return True, "archive_eligible", game_start, in_play_hours

    def save_market_snapshots(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> int:
        """Persist direct YES/NO levels, exact-$5 walks, and sweep proof."""
        if self.repo is None or self.clob is None:
            raise RuntimeError("repository and CLOB client are required")
        attestation = self.gamma.last_sweep_attestation
        if not attestation:
            raise RuntimeError("completed Gamma sweep attestation is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        sides = [
            side
            for market in markets
            for side in get_match_result_sides(market)
        ]
        token_ids = [str(side["token_id"]) for side in sides]
        self._walks = self.clob.get_buy_book_walks(
            token_ids, notional_usdc=self.config.buy_amount_usdc
        )
        self._snapshot_ids.clear()
        snapshot_results: Dict[str, Dict[str, Any]] = {}
        saved = 0
        try:
            for market in markets:
                condition_id = str(market.get("conditionId") or "").strip()
                if not condition_id:
                    raise ValueError("qualified Gamma market has no conditionId")
                self.repo.save_market_catalog(condition_id, market, commit=False)
                eligible, reason, _game_start, _in_play_hours = self._market_eligible(
                    market, reference
                )
                if not eligible:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                    }
                    continue
                event = get_event(market)
                event_meta = get_event_metadata(market)
                source_minute, clock_reason = get_source_regulation_minute(event)
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
                        book_json=(
                            self.clob.get_cached_book_evidence(token_id)
                            if callable(
                                getattr(self.clob, "get_cached_book_evidence", None)
                            )
                            else None
                        ),
                        commit=False,
                    )
                    snapshot.timestamp = reference.astimezone(timezone.utc).replace(
                        tzinfo=None
                    )
                    self._snapshot_ids[token_id] = snapshot.id
                    saved_for_condition += 1
                    saved += 1
                snapshot_results[condition_id] = {
                    "snapshot_eligible": True,
                    "snapshotted": saved_for_condition == 2,
                    "snapshot_reason": (
                        "direct_yes_no_exact_5_books_saved"
                        if saved_for_condition == 2
                        else f"direct_book_coverage:{saved_for_condition}/2"
                    ),
                }

            self.repo.record_market_sweep(attestation, snapshot_results, commit=False)
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
            "Golden Peach direct YES/NO snapshots=%s complete_markets=%s/%s",
            saved,
            sum(int(item["snapshotted"]) for item in snapshot_results.values()),
            len(markets),
        )
        return saved

    def scan_buy_candidates(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> List[Dict]:
        """Select the unique highest midpoint across a complete six-token event."""
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
            source_minute, clock_reason = get_source_regulation_minute(
                get_event(market)
            )
            if source_minute is None:
                rejected[clock_reason] = rejected.get(clock_reason, 0) + 1
                continue
            if not 0 <= source_minute <= self.config.entry.max_source_minute + 1e-9:
                rejected["outside_kickoff_source_window"] = rejected.get(
                    "outside_kickoff_source_window", 0
                ) + 1
                continue
            outcomes = get_match_result_sides(market)
            if len(outcomes) != 2:
                rejected["direct_yes_no_identity_gap"] = rejected.get(
                    "direct_yes_no_identity_gap", 0
                ) + 1
                continue
            result_kind = str(outcomes[0]["result_kind"])
            bucket = markets_by_event.setdefault(event_id, {})
            if result_kind in bucket:
                bucket[result_kind] = {"duplicate": True}
                continue
            bucket[result_kind] = {
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
            if set(result_markets) != _RESULT_KINDS or any(
                item.get("duplicate") for item in result_markets.values()
            ):
                rejected["incomplete_or_duplicate_result_triad"] = rejected.get(
                    "incomplete_or_duplicate_result_triad", 0
                ) + 1
                continue
            ranked: List[Dict[str, Any]] = []
            event_invalid = False
            for result_kind in ("HOME", "DRAW", "AWAY"):
                context = result_markets[result_kind]
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
                        or walk.spread > self.config.entry.max_entry_spread + 1e-9
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
            if event_invalid or len(ranked) != 6:
                rejected["six_direct_executable_books_required"] = rejected.get(
                    "six_direct_executable_books_required", 0
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
            if not (
                self.config.entry.prob_min - 1e-9
                <= walk.vwap
                <= self.config.entry.prob_max + 1e-9
            ):
                rejected["leader_vwap_outside_entry_band"] = rejected.get(
                    "leader_vwap_outside_entry_band", 0
                ) + 1
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
                    "league_code": market.get("leagueCode"),
                    "league_name": market.get("leagueName"),
                    "token_id": str(outcome["token_id"]),
                    "probability": walk.vwap,
                    "entry_snapshot_id": int(leader["snapshot_id"]),
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
                    "entry_reason": "unique_six_token_kickoff_leader",
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
                float(item["source_elapsed_minutes"]),
                str(item["event_id"]),
            )
        )
        logger.info(
            "Golden Peach kickoff leaders=%s arm_tp=+%.2f sl=-%.2f",
            len(candidates),
            self.config.entry.take_profit_delta,
            self.config.entry.stop_loss_delta,
        )
        return candidates

    def check_current_price(self, token_id: str, clob_client) -> float:
        try:
            return clob_client.get_buy_book_walk(
                token_id, notional_usdc=self.config.buy_amount_usdc
            ).vwap
        except Exception as error:
            logger.warning(
                "exact book lookup failed - token=%s error=%s",
                token_id,
                type(error).__name__,
            )
            return 0.0
