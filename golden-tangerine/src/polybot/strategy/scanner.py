"""Exact-book sports scanner for Golden Tangerine live A/B."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from typing import Any, Dict, List, Optional

from ..api.clob_client import BuyBookWalk, ClobClientWrapper
from ..api.gamma_client import GammaClient
from ..config import TradingConfig
from ..db.repository import TradeRepository
from .filters import (
    aligned_binary_reason,
    get_aligned_binary_outcomes,
    get_event_metadata,
)


logger = logging.getLogger(__name__)


def parse_end_date(end_date_str: Optional[str]) -> Optional[datetime]:
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
    end_date: Optional[datetime], now: Optional[datetime] = None
) -> Optional[float]:
    if end_date is None:
        return None
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (end_date - reference).total_seconds() / 3600.0


def _finite_nonnegative(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class MarketScanner:
    """Persist both binary outcome paths and select one frozen exact-VWAP arm."""

    def __init__(
        self,
        gamma_client: GammaClient,
        config: TradingConfig,
        repo: Optional[TradeRepository] = None,
        history_client=None,
        clob_client: Optional[ClobClientWrapper] = None,
    ):
        self.gamma = gamma_client
        self.config = config
        self.repo = repo
        self.history = history_client
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
        reason = aligned_binary_reason(market)
        if reason != "ok":
            return False, reason, None, None
        liquidity = _finite_nonnegative(
            market.get("liquidityNum", market.get("liquidity"))
        )
        cumulative_volume = _finite_nonnegative(
            market.get("volumeNum", market.get("volume"))
        )
        if liquidity is None or liquidity < self.config.min_liquidity:
            return False, "low_liquidity", None, None
        if (
            cumulative_volume is None
            or cumulative_volume < self.config.min_cumulative_volume
        ):
            return False, "low_cumulative_volume", None, None
        end_date = parse_end_date(market.get("endDate"))
        hours_left = get_hours_until_resolution(end_date, now)
        if hours_left is None:
            return False, "no_end_date", end_date, None
        if not 0 < hours_left <= self.config.archive.hours_max + 1e-9:
            return False, "outside_six_hour_window", end_date, hours_left
        return True, "archive_eligible", end_date, hours_left

    def save_market_snapshots(
        self,
        markets: List[Dict],
        now: Optional[datetime] = None,
    ) -> int:
        """Persist exact $5 VWAP for both outcomes and the complete sweep proof."""
        if self.repo is None or self.clob is None:
            raise RuntimeError("repository and CLOB client are required")
        attestation = self.gamma.last_sweep_attestation
        if not attestation:
            raise RuntimeError("completed Gamma sweep attestation is required")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        token_ids = [
            outcome["token_id"]
            for market in markets
            for outcome in get_aligned_binary_outcomes(market)
        ]
        self._walks = self.clob.get_buy_book_walks(
            token_ids, notional_usdc=self.config.buy_amount_usdc
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
                self.repo.save_market_catalog(condition_id, market, commit=False)
                eligible, reason, _, _ = self._market_eligible(market, reference)
                if not eligible:
                    snapshot_results[condition_id] = {
                        "snapshot_eligible": False,
                        "snapshotted": False,
                        "snapshot_reason": reason,
                    }
                    continue

                market_saved = 0
                liquidity = _finite_nonnegative(
                    market.get("liquidityNum", market.get("liquidity"))
                )
                volume_24h = _finite_nonnegative(market.get("volume24hr"))
                event = get_event_metadata(market)
                experiment_start = parse_end_date(self.config.experiment_start_utc)
                experiment_end = parse_end_date(self.config.experiment_entry_end_utc)
                entry_period_open = bool(
                    experiment_start
                    and experiment_end
                    and experiment_start <= reference < experiment_end
                )
                for outcome in get_aligned_binary_outcomes(market):
                    token_id = str(outcome["token_id"])
                    walk = self._walks.get(token_id)
                    if walk is None:
                        continue
                    snapshot = self.repo.save_snapshot(
                        condition_id=condition_id,
                        token_id=token_id,
                        outcome=str(outcome["outcome"]),
                        probability=walk.vwap,
                        liquidity=liquidity,
                        volume_24h=volume_24h,
                        best_bid=walk.best_bid,
                        best_ask=walk.best_ask,
                        spread=walk.spread,
                        source_updated_at=market.get("updatedAt"),
                        commit=False,
                    )
                    snapshot.timestamp = reference.astimezone(timezone.utc).replace(
                        tzinfo=None
                    )
                    self._snapshot_ids[token_id] = snapshot.id
                    if (
                        entry_period_open
                        and self.config.entry.prob_min - 1e-9
                        <= walk.vwap
                        <= self.config.entry.prob_max + 1e-9
                    ):
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
                        )
                        if episode is not None:
                            self._first_episode_ids[token_id] = episode.id
                    market_saved += 1
                    saved += 1

                snapshot_results[condition_id] = {
                    "snapshot_eligible": True,
                    "snapshotted": market_saved == 2,
                    "snapshot_reason": (
                        "both_outcome_exact_books_saved"
                        if market_saved == 2
                        else f"exact_book_coverage_{market_saved}_of_2"
                    ),
                }

            self.repo.record_market_sweep(
                attestation, snapshot_results, commit=False
            )
            self.repo.commit()
            attestation["snapshot_eligible_count"] = sum(
                int(item["snapshot_eligible"])
                for item in snapshot_results.values()
            )
            attestation["snapshotted_market_count"] = sum(
                int(item["snapshotted"])
                for item in snapshot_results.values()
            )
        except Exception:
            self.repo.rollback()
            raise
        logger.info(
            "Golden Tangerine exact-book snapshots=%s complete_markets=%s/%s",
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
        """Select current exact-$5 books in this job's frozen one-cent arm."""
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

        candidates: List[Dict] = []
        rejected: Dict[str, int] = {}
        for market in markets:
            condition_id = str(market.get("conditionId") or "").strip()
            eligible, reason, end_date, hours_left = self._market_eligible(
                market, reference
            )
            if not eligible:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            event = get_event_metadata(market)
            if not event["event_id"]:
                rejected["missing_event_id"] = rejected.get("missing_event_id", 0) + 1
                continue
            tags = market.get("tags") or []
            tag_text = ", ".join(
                str(tag.get("label") or tag.get("slug") or "")
                for tag in tags
                if isinstance(tag, dict)
            )
            aligned_outcomes = get_aligned_binary_outcomes(market)
            for outcome in aligned_outcomes:
                token_id = str(outcome["token_id"])
                walk = self._walks.get(token_id)
                if walk is None:
                    rejected["no_full_exact_book"] = (
                        rejected.get("no_full_exact_book", 0) + 1
                    )
                    continue
                if not (
                    self.config.entry.prob_min - 1e-9
                    <= walk.vwap
                    <= self.config.entry.prob_max + 1e-9
                ):
                    rejected["outside_entry_band"] = (
                        rejected.get("outside_entry_band", 0) + 1
                    )
                    continue
                entry_snapshot_id = self._snapshot_ids.get(token_id)
                if entry_snapshot_id is None:
                    rejected["missing_entry_snapshot"] = (
                        rejected.get("missing_entry_snapshot", 0) + 1
                    )
                    continue
                episode_id = self._first_episode_ids.get(token_id)
                if episode_id is None:
                    rejected["not_first_in_arm_observation"] = (
                        rejected.get("not_first_in_arm_observation", 0) + 1
                    )
                    continue
                candidates.append(
                    {
                        "condition_id": condition_id,
                        "market_slug": market.get("slug", ""),
                        "question": market.get("question", ""),
                        "event_id": event["event_id"],
                        "event_slug": event["event_slug"],
                        "outcome": outcome["outcome"],
                        "outcome_index": outcome["token_index"],
                        "token_id": token_id,
                        "probability": walk.vwap,
                        "prior_yes_price": None,
                        "prior_snapshot_id": None,
                        "entry_snapshot_id": entry_snapshot_id,
                        "entry_episode_id": episode_id,
                        # Legacy DB field name: this is the first listed outcome,
                        # which is not necessarily literal Yes in sports markets.
                        "yes_probability": float(
                            aligned_outcomes[0]["probability"]
                        ),
                        "liquidity": _finite_nonnegative(market.get("liquidity")),
                        "volume_24h": _finite_nonnegative(market.get("volume24hr")),
                        "best_bid": walk.best_bid,
                        "best_ask": walk.best_ask,
                        "spread": walk.spread,
                        "entry_vwap": walk.vwap,
                        "entry_shares": walk.shares,
                        "entry_limit_price": walk.limit_price,
                        "entry_levels_used": walk.levels_used,
                        "entry_reason": "first_observed_exact_5_usdc_band",
                        "end_date": end_date,
                        "hours_until_resolution": hours_left,
                        "market_tags": tag_text,
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
                float(item["hours_until_resolution"]),
                str(item["condition_id"]),
                str(item["token_id"]),
            )
        )
        logger.info(
            "Golden Tangerine arm %.2f-%.2f candidates=%s",
            self.config.entry.prob_min,
            self.config.entry.prob_max,
            len(candidates),
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
