"""Baseline-$5 in-play soccer/MLB/NHL winner scanner."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
from typing import Any, Dict, List, Optional

from ..api.clob_client import BuyBookWalk, ClobClientWrapper
from ..api.gamma_client import GammaClient
from ..config import BASELINE_EXECUTION_NOTIONAL_USDC, TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    get_event,
    get_event_metadata,
    get_match_result_outcomes,
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


def parse_end_date(value: Optional[str]) -> Optional[datetime]:
    """Compatibility parser used for all UTC Gamma clock fields."""
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


class MarketScanner:
    """Persist executable YES books and claim each event at most once."""

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
        self._first_episode_ids: Dict[str, int] = {}

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
        """Persist baseline `$5` winner VWAP and a cursor-complete sweep proof."""
        if self.repo is None or self.clob is None:
            raise RuntimeError("repository and CLOB client are required")
        attestation = self.gamma.last_sweep_attestation
        if not attestation:
            raise RuntimeError("completed Gamma sweep attestation is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        result_outcomes = [
            outcome
            for market in markets
            for outcome in get_match_result_outcomes(market)
        ]
        token_ids = [
            str(outcome["token_id"])
            for outcome in result_outcomes
            if outcome
        ]
        self._walks = self.clob.get_buy_book_walks(
            token_ids, notional_usdc=BASELINE_EXECUTION_NOTIONAL_USDC
        )
        self._snapshot_ids.clear()
        self._first_episode_ids.clear()
        snapshot_results: Dict[str, Dict[str, Any]] = {}
        saved = 0
        try:
            for market in markets:
                condition_id = str(market.get("conditionId") or "").strip()
                if not condition_id:
                    raise ValueError("qualified Gamma market has no conditionId")
                tags_json = _market_tags_json(market)
                self.repo.save_market_catalog(
                    condition_id,
                    market,
                    sport_family=self.config.sport_family,
                    league_code=(
                        market.get("leagueCode") or self.config.sport_family
                    ),
                    league_name=(
                        market.get("leagueName") or self.config.sport_family.upper()
                    ),
                    commit=False,
                )
                eligible, reason, game_start, in_play_hours = self._market_eligible(
                    market, reference
                )
                if not eligible:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                    }
                    continue
                outcomes = get_match_result_outcomes(market)
                saved_for_condition = 0
                missing_book_count = 0
                for outcome in outcomes:
                    token_id = str(outcome.get("token_id") or "")
                    walk = self._walks.get(token_id)
                    if walk is None:
                        missing_book_count += 1
                        continue
                    snapshot = self.repo.save_snapshot(
                        condition_id=condition_id,
                        token_id=token_id,
                        outcome=str(outcome["outcome"]),
                        probability=walk.vwap,
                        liquidity=_finite_nonnegative(
                            market.get("liquidityNum", market.get("liquidity"))
                        ),
                        volume_24h=_finite_nonnegative(market.get("volume24hr")),
                        best_bid=walk.best_bid,
                        best_ask=walk.best_ask,
                        spread=walk.spread,
                        source_updated_at=market.get("updatedAt"),
                        sport_family=self.config.sport_family,
                        league_code=(
                            market.get("leagueCode") or self.config.sport_family
                        ),
                        league_name=(
                            market.get("leagueName")
                            or self.config.sport_family.upper()
                        ),
                        market_tags_json=tags_json,
                        commit=False,
                    )
                    snapshot.timestamp = reference.astimezone(timezone.utc).replace(
                        tzinfo=None
                    )
                    self._snapshot_ids[token_id] = snapshot.id
                    experiment_start = parse_end_date(self.config.experiment_start_utc)
                    experiment_end = parse_end_date(
                        self.config.experiment_entry_end_utc
                    )
                    entry_period_open = bool(
                        experiment_start
                        and experiment_end
                        and experiment_start <= reference < experiment_end
                    )
                    if (
                        entry_period_open
                        and self.config.entry.prob_min - 1e-9
                        <= walk.vwap
                        <= self.config.entry.prob_max + 1e-9
                    ):
                        event = get_event_metadata(market)
                        episode = self.repo.claim_entry_episode(
                            token_id=token_id,
                            condition_id=condition_id,
                            event_id=event["event_id"],
                            outcome=str(outcome["outcome"]),
                            entry_snapshot_id=snapshot.id,
                            exact_vwap=walk.vwap,
                            arm_prob_min=self.config.entry.prob_min,
                            arm_prob_max=self.config.entry.prob_max,
                            observed_at=reference.astimezone(timezone.utc).replace(
                                tzinfo=None
                            ),
                            game_start_time=(
                                game_start.astimezone(timezone.utc).replace(tzinfo=None)
                                if game_start is not None
                                else None
                            ),
                            in_play_hours=in_play_hours,
                        )
                        if episode is not None:
                            self._first_episode_ids[token_id] = episode.id
                    saved_for_condition += 1
                    saved += 1
                if not outcomes:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": True,
                        "snapshotted": False,
                        "snapshot_reason": "no_exact_winner_outcome",
                    }
                    continue
                snapshot_results[condition_id] = {
                    "snapshot_eligible": True,
                    "snapshotted": saved_for_condition > 0,
                    "snapshot_reason": (
                        f"exact_5_usdc_winner_books_saved:{saved_for_condition}"
                        if saved_for_condition
                        else f"no_full_exact_5_usdc_winner_book:{missing_book_count}"
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
            "Golden Watermelon Live exact-$5 YES snapshots=%s complete=%s/%s",
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
        """Select first executable arm observations, with one result per event."""
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
            logger.info(
                "outside frozen entry period - now=%s start=%s end=%s",
                reference.isoformat(),
                self.config.experiment_start_utc,
                self.config.experiment_entry_end_utc,
            )
            return []

        candidates_by_event: Dict[str, List[Dict[str, Any]]] = {}
        rejected: Dict[str, int] = {}
        for market in markets:
            condition_id = str(market.get("conditionId") or "").strip()
            eligible, reason, game_start, in_play_hours = self._market_eligible(
                market, reference
            )
            if not eligible:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            event = get_event_metadata(market)
            if not event["event_id"]:
                rejected["missing_event_id"] = rejected.get("missing_event_id", 0) + 1
                continue
            for outcome in get_match_result_outcomes(market):
                token_id = str(outcome["token_id"])
                walk = self._walks.get(token_id)
                if walk is None:
                    rejected["no_full_exact_book"] = rejected.get(
                        "no_full_exact_book", 0
                    ) + 1
                    continue
                if not (
                    self.config.entry.prob_min - 1e-9
                    <= walk.vwap
                    <= self.config.entry.prob_max + 1e-9
                ):
                    rejected["outside_entry_band"] = rejected.get(
                        "outside_entry_band", 0
                    ) + 1
                    continue
                entry_snapshot_id = self._snapshot_ids.get(token_id)
                episode_id = self._first_episode_ids.get(token_id)
                if entry_snapshot_id is None:
                    rejected["missing_entry_snapshot"] = rejected.get(
                        "missing_entry_snapshot", 0
                    ) + 1
                    continue
                if episode_id is None:
                    rejected["not_first_in_arm_observation"] = rejected.get(
                        "not_first_in_arm_observation", 0
                    ) + 1
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
                    "outcome": outcome["outcome"],
                    "outcome_index": outcome["token_index"],
                    "result_kind": outcome["result_kind"],
                    "league_code": (
                        market.get("leagueCode") or self.config.sport_family
                    ),
                    "league_name": (
                        market.get("leagueName") or self.config.sport_family.upper()
                    ),
                    "token_id": token_id,
                    "probability": walk.vwap,
                    "prior_yes_price": None,
                    "prior_snapshot_id": None,
                    "entry_snapshot_id": entry_snapshot_id,
                    "entry_episode_id": episode_id,
                    "yes_probability": float(outcome["probability"]),
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
                        "first_observed_in_play_result_exact_5_usdc_band"
                    ),
                    "end_date": parse_end_date(market.get("endDate")),
                    "game_start_time": game_start,
                    "in_play_hours": in_play_hours,
                    "hours_until_resolution": in_play_hours,
                    "market_tags": (
                        f"{tag_text}, league={market.get('leagueCode')}, "
                        f"result={outcome['result_kind']}"
                    ).strip(", "),
                    "market_tags_json": _market_tags_json(market),
                }
                candidates_by_event.setdefault(str(event["event_id"]), []).append(
                    candidate
                )

        candidates: List[Dict[str, Any]] = []
        for event_id, event_candidates in candidates_by_event.items():
            if len(event_candidates) != 1:
                rejected["multiple_result_tokens_above_threshold"] = rejected.get(
                    "multiple_result_tokens_above_threshold", 0
                ) + len(event_candidates)
                # These fresh claims were deliberately not sent to the
                # execution layer. Mark that no-POST guard outcome explicitly
                # so a later cycle can retry if the anomaly clears and exactly
                # one result remains in-band.
                for candidate in event_candidates:
                    episode_id = candidate.get("entry_episode_id")
                    if (
                        not isinstance(episode_id, bool)
                        and isinstance(episode_id, int)
                    ):
                        self.repo.mark_entry_episode_execution(
                            episode_id,
                            state="BLOCKED_GUARD",
                            reason="multiple_result_tokens_above_threshold",
                        )
                logger.error(
                    "event has multiple threshold-qualified match results; fail closed - "
                    "event=%s candidates=%s",
                    event_id,
                    len(event_candidates),
                )
                continue
            candidates.append(event_candidates[0])
        if rejected:
            logger.info(
                "entry exclusion summary - %s",
                ", ".join(
                    f"{key}={value}" for key, value in sorted(rejected.items())
                ),
            )
        candidates.sort(
            key=lambda item: (
                float(item["in_play_hours"]),
                str(item["event_id"]),
                str(item["condition_id"]),
            )
        )
        logger.info(
            "Golden Watermelon Live arm %.3f-%.3f candidates=%s",
            self.config.entry.prob_min,
            self.config.entry.prob_max,
            len(candidates),
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
