"""Five-family in-play moneyline census and exact displayed-book collector."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Mapping
from uuid import uuid4

from .api.clob_client import (
    BookAttempt,
    ClobClient,
    canonical_book_gzip,
    walk_asks,
    walk_bids,
)
from .api.gamma_client import EventSweep, GammaClient
from .api.sports_client import ClockBatch, SportsClockClient
from .api.transport import CycleBudget, canonical_json, iso_utc, receipt_skew_seconds
from .classifier import EventClassification, classify_event, classify_market
from .config import BotConfig
from .crossings import PriorThresholdState, evaluate_threshold_vector
from .db.repository import ResearchRepository, storage_metric_row


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _boolean_integer(value: Any) -> int | None:
    return int(value) if isinstance(value, bool) else None


def _source_value(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _source_metrics(source: Mapping[str, Any]) -> dict[str, float | None]:
    return {
        "volume_num": _number(_source_value(source, "volumeNum", "volume")),
        "volume_24hr": _number(_source_value(source, "volume24hr", "volume24Hr")),
        "liquidity": _number(source.get("liquidity")),
        "liquidity_num": _number(source.get("liquidityNum")),
    }


def _clock_raw(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (Mapping, list)):
        return canonical_json(value)
    return str(value)


@dataclass(frozen=True)
class CollectionProduct:
    bundle: Mapping[str, Any]
    summary: Mapping[str, Any]
    fatal_error: str | None = None


class Collector:
    def __init__(
        self,
        config: BotConfig,
        repository: ResearchRepository,
        gamma: GammaClient,
        clob: ClobClient,
        sports_clock: SportsClockClient,
    ) -> None:
        self.config = config
        self.repository = repository
        self.gamma = gamma
        self.clob = clob
        self.sports_clock = sports_clock

    def _event_rows(
        self,
        *,
        event: Mapping[str, Any],
        classification: EventClassification,
        cycle_id: str,
        sweep_id: str,
        raw_payload_id: str,
        run_id: str,
        observed_at: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        event_observation_id = uuid4().hex
        metrics = _source_metrics(event)
        sport = event.get("sport") if isinstance(event.get("sport"), Mapping) else {}
        row = {
            "event_observation_id": event_observation_id,
            "cycle_id": cycle_id,
            "sweep_id": sweep_id,
            "raw_payload_id": raw_payload_id,
            "run_id": run_id,
            "sport_family": classification.family,
            "event_id": str(event.get("id") or f"MISSING:{uuid4().hex}"),
            "game_id": str(event.get("gameId") or event.get("game_id") or "") or None,
            "event_cluster_id": classification.cluster_id,
            "title": str(event.get("title") or "") or None,
            "slug": str(event.get("slug") or "") or None,
            "observed_at": observed_at,
            "competition_code": classification.competition_code,
            "competition_name": classification.competition_name,
            "season_phase": str(classification.evidence["season_phase"]),
            "classification_status": classification.status,
            "classification_reason": "ELIGIBLE" if not classification.reasons else ";".join(classification.reasons),
            "classifier_version": self.config.trading.classifier_version,
            "sports_registry_sha256": self.config.trading.sports_registry_sha256,
            **metrics,
            "active": _boolean_integer(event.get("active")),
            "closed": _boolean_integer(event.get("closed")),
            "live": _boolean_integer(event.get("live")),
            "ended": _boolean_integer(event.get("ended")),
            "game_start_time": str(_source_value(event, "startTime", "eventDate", "gameStartTime") or "") or None,
            "end_date": str(event.get("endDate") or "") or None,
            "sport_json": canonical_json(sport),
            "classification_evidence_json": canonical_json(classification.evidence),
            "normalized_json": canonical_json(
                {
                    "family": classification.family,
                    "competition_code": classification.competition_code,
                    "cluster_id": classification.cluster_id,
                    "season_phase": classification.evidence["season_phase"],
                    **metrics,
                }
            ),
        }
        tags: list[dict[str, Any]] = []
        for index, item in enumerate(event.get("tags", []) if isinstance(event.get("tags"), list) else []):
            if not isinstance(item, Mapping):
                continue
            tags.append(
                {
                    "event_tag_observation_id": uuid4().hex,
                    "event_observation_id": event_observation_id,
                    "tag_index": index,
                    "tag_id": str(item.get("id") or "") or None,
                    "tag_slug": str(item.get("slug") or "") or None,
                    "tag_label": str(item.get("label") or "") or None,
                    "tag_json": canonical_json(item),
                }
            )
        series: list[dict[str, Any]] = []
        for index, item in enumerate(event.get("series", []) if isinstance(event.get("series"), list) else []):
            if not isinstance(item, Mapping):
                continue
            series.append(
                {
                    "event_series_observation_id": uuid4().hex,
                    "event_observation_id": event_observation_id,
                    "series_index": index,
                    "series_id": str(item.get("id") or "") or None,
                    "series_slug": str(item.get("slug") or "") or None,
                    "series_title": str(item.get("title") or "") or None,
                    "series_json": canonical_json(item),
                }
            )
        teams: list[dict[str, Any]] = []
        for index, item in enumerate(event.get("teams", []) if isinstance(event.get("teams"), list) else []):
            if not isinstance(item, Mapping):
                continue
            teams.append(
                {
                    "event_team_observation_id": uuid4().hex,
                    "event_observation_id": event_observation_id,
                    "team_index": index,
                    "team_id": str(item.get("id") or "") or None,
                    "team_name": str(item.get("name") or "") or None,
                    "team_alias": str(item.get("alias") or "") or None,
                    "team_abbreviation": str(item.get("abbreviation") or "") or None,
                    "team_league": str(item.get("league") or "") or None,
                    "team_json": canonical_json(item),
                }
            )
        return row, tags, series, teams

    def collect(
        self,
        run_id: str,
        *,
        slot_start: str,
        budget: CycleBudget,
        now: datetime | None = None,
    ) -> CollectionProduct:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        started_wall = current
        cycle_id = uuid4().hex
        receipts: list[str] = []
        sweeps: list[dict[str, Any]] = []
        raw_payloads: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        tags: list[dict[str, Any]] = []
        series: list[dict[str, Any]] = []
        teams: list[dict[str, Any]] = []
        markets: list[dict[str, Any]] = []
        outcomes: list[dict[str, Any]] = []
        quality: list[dict[str, Any]] = []
        event_contexts: list[tuple[Mapping[str, Any], EventClassification, dict[str, Any], str]] = []
        seen_event_keys: set[tuple[str, str]] = set()

        sweep_results: list[EventSweep] = []
        raw_by_request: dict[str, str] = {}
        for family in self.config.registry.families:
            family_started = iso_utc()
            sweep = self.gamma.fetch_family_events(run_id, family, budget=budget)
            sweep_results.append(sweep)
            sweep_id = uuid4().hex
            accepted = rejected = drift = 0
            for page in sweep.pages:
                receipts.append(page.received_at)
                payload_row = self.repository.raw_payload_row(
                    cycle_id=cycle_id,
                    run_id=run_id,
                    payload_kind="GAMMA_EVENT_KEYSET_PAGE",
                    sport_family=family.code,
                    logical_request_id=page.request_id,
                    observed_at=page.received_at,
                    raw=page.raw,
                )
                raw_payloads.append(payload_row)
                raw_by_request[page.request_id] = str(payload_row["raw_payload_id"])
                for event in page.events:
                    event_id = str(event.get("id") or "")
                    event_key = (family.code, event_id)
                    if event_id and event_key in seen_event_keys:
                        quality.append(
                            self._quality_row(
                                cycle_id, run_id, "HIGH", "DUPLICATE_EVENT_IN_FAMILY_SWEEP",
                                family.code, {"event_id": event_id}
                            )
                        )
                        continue
                    seen_event_keys.add(event_key)
                    classification = classify_event(event, family, self.config.registry)
                    accepted += int(classification.status == "ACCEPTED")
                    rejected += int(classification.status == "REJECTED")
                    drift += int(classification.status == "DRIFT")
                    event_row, event_tags, event_series, event_teams = self._event_rows(
                        event=event,
                        classification=classification,
                        cycle_id=cycle_id,
                        sweep_id=sweep_id,
                        raw_payload_id=raw_by_request[page.request_id],
                        run_id=run_id,
                        observed_at=page.received_at,
                    )
                    events.append(event_row)
                    tags.extend(event_tags)
                    series.extend(event_series)
                    teams.extend(event_teams)
                    event_contexts.append((event, classification, event_row, page.received_at))
                    if classification.status == "DRIFT":
                        quality.append(
                            self._quality_row(
                                cycle_id, run_id, "HIGH", "SPORT_IDENTITY_DRIFT",
                                family.code,
                                {"event_id": event_id, "reasons": classification.reasons},
                            )
                        )
            sweeps.append(
                {
                    "sweep_id": sweep_id,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "sport_family": family.code,
                    "tag_id": family.tag_id,
                    "started_at": family_started,
                    "completed_at": iso_utc(),
                    "page_count": len(sweep.pages),
                    "source_event_count": sum(len(page.events) for page in sweep.pages),
                    "accepted_event_count": accepted,
                    "rejected_event_count": rejected,
                    "drift_event_count": drift,
                    "cursor_complete": int(sweep.cursor_complete),
                    "terminal_cursor": sweep.terminal_cursor,
                    "request_envelope_json": canonical_json(
                        {
                            "endpoint": self.config.trading.gamma.endpoint,
                            "closed": False,
                            "live": True,
                            "tag_id": family.tag_id,
                            "related_tags": False,
                            "page_size": self.config.trading.gamma.page_size,
                            "liquidity_gate": None,
                            "volume_gate": None,
                        }
                    ),
                }
            )

        all_complete = all(sweep.cursor_complete for sweep in sweep_results)
        fatal_error: str | None = None
        if not all_complete:
            fatal_error = "one or more family Gamma keyset sweeps did not reach a terminal cursor"
            for sweep in sweep_results:
                if not sweep.cursor_complete:
                    quality.append(
                        self._quality_row(
                            cycle_id, run_id, "CRITICAL", "FAMILY_CURSOR_INCOMPLETE",
                            sweep.family, {"pages": len(sweep.pages), "terminal_cursor": sweep.terminal_cursor}
                        )
                    )

        clock_batch = ClockBatch(uuid4().hex, "SKIPPED_INCOMPLETE_CENSUS", iso_utc(), iso_utc(), 0, 0, 0, {}, ())
        if all_complete:
            clock_targets: dict[str, str] = {}
            for _event, classification, event_row, _observed in event_contexts:
                game_id = str(event_row["game_id"] or "")
                if classification.accepted and game_id:
                    prior = clock_targets.get(game_id)
                    if prior is not None and prior != classification.cluster_id:
                        quality.append(
                            self._quality_row(
                                cycle_id, run_id, "HIGH", "GAME_ID_CLUSTER_CONFLICT",
                                classification.family,
                                {"game_id": game_id, "first": prior, "second": classification.cluster_id},
                            )
                        )
                    else:
                        clock_targets[game_id] = classification.cluster_id
            clock_batch = self.sports_clock.collect(run_id, clock_targets, budget=budget)
            receipts.append(clock_batch.completed_at)
            seen_clock_hashes: set[str] = set()
            for raw in clock_batch.raw_messages:
                digest = hashlib.sha256(raw).hexdigest()
                if digest in seen_clock_hashes:
                    continue
                seen_clock_hashes.add(digest)
                raw_payloads.append(
                    self.repository.raw_payload_row(
                        cycle_id=cycle_id,
                        run_id=run_id,
                        payload_kind="SPORTS_CLOCK_MESSAGE",
                        sport_family=None,
                        logical_request_id=clock_batch.request_id,
                        observed_at=clock_batch.completed_at,
                        raw=raw,
                    )
                )
            if clock_targets and clock_batch.status != "OBSERVED":
                quality.append(
                    self._quality_row(
                        cycle_id, run_id, "HIGH", "SPORTS_CLOCK_COVERAGE_GAP", None,
                        {
                            "status": clock_batch.status,
                            "target_count": clock_batch.target_count,
                            "matched_count": clock_batch.matched_count,
                            "error_type": clock_batch.error_type,
                        },
                    )
                )

        token_contexts: dict[str, dict[str, Any]] = {}
        if all_complete:
            for source_event, event_classification, event_row, observed_at in event_contexts:
                if not event_classification.accepted:
                    continue
                source_markets = source_event.get("markets")
                if not isinstance(source_markets, list):
                    quality.append(
                        self._quality_row(
                            cycle_id, run_id, "WARN", "EVENT_MARKETS_ARRAY_MISSING",
                            event_classification.family, {"event_id": event_row["event_id"]}
                        )
                    )
                    continue
                event_metrics = _source_metrics(source_event)
                for source_market in source_markets:
                    if not isinstance(source_market, Mapping):
                        continue
                    classification = classify_market(source_event, source_market, event_classification)
                    market_observation_id = uuid4().hex
                    condition_id = str(source_market.get("conditionId") or source_market.get("condition_id") or "")
                    market_id = str(source_market.get("id") or "")
                    extra_reasons: list[str] = []
                    if not condition_id or not market_id:
                        extra_reasons.append("MARKET_IDENTITY_MISSING")
                    reasons = tuple((*classification.reasons, *extra_reasons))
                    eligible = not reasons
                    metrics = _source_metrics(source_market)
                    market_row = {
                        "market_observation_id": market_observation_id,
                        "cycle_id": cycle_id,
                        "event_observation_id": event_row["event_observation_id"],
                        "run_id": run_id,
                        "sport_family": event_classification.family,
                        "season_phase": event_row["season_phase"],
                        "event_id": event_row["event_id"],
                        "event_cluster_id": event_classification.cluster_id,
                        "condition_id": condition_id or None,
                        "market_id": market_id or None,
                        "question": str(source_market.get("question") or "") or None,
                        "group_item_title": str(source_market.get("groupItemTitle") or "") or None,
                        "sports_market_type": str(source_market.get("sportsMarketType") or "") or None,
                        "structure_kind": classification.structure,
                        "result_kind": classification.result_kind,
                        "neg_risk": _boolean_integer(source_market.get("negRisk")),
                        "observed_at": observed_at,
                        "active": _boolean_integer(source_market.get("active")),
                        "closed": _boolean_integer(source_market.get("closed")),
                        "accepting_source_activity": _boolean_integer(source_market.get("acceptingOrders")),
                        "public_book_enabled": _boolean_integer(source_market.get("enableOrderBook")),
                        **metrics,
                        "event_volume_num": event_metrics["volume_num"],
                        "event_volume_24hr": event_metrics["volume_24hr"],
                        "event_liquidity": event_metrics["liquidity"],
                        "event_liquidity_num": event_metrics["liquidity_num"],
                        "eligible": int(eligible),
                        "exclusion_reason": "ELIGIBLE" if eligible else ";".join(reasons),
                        "labels_json": canonical_json(list(classification.labels)),
                        "token_ids_json": canonical_json(list(classification.token_ids)),
                        "probabilities_json": canonical_json(list(classification.probabilities)),
                        "classification_evidence_json": canonical_json(classification.evidence),
                        "normalized_json": canonical_json(
                            {
                                "family": event_classification.family,
                                "season_phase": event_row["season_phase"],
                                "cluster": event_classification.cluster_id,
                                "structure": classification.structure,
                                **metrics,
                                "event_metrics": event_metrics,
                            }
                        ),
                    }
                    markets.append(market_row)
                    if len(classification.labels) == len(classification.token_ids) == len(classification.probabilities) == 2 and condition_id:
                        for index, (label, token, probability) in enumerate(
                            zip(classification.labels, classification.token_ids, classification.probabilities)
                        ):
                            threshold_eligible = eligible and index in classification.eligible_indices
                            outcome_row = {
                                "outcome_observation_id": uuid4().hex,
                                "market_observation_id": market_observation_id,
                                "cycle_id": cycle_id,
                                "run_id": run_id,
                                "sport_family": event_classification.family,
                                "season_phase": event_row["season_phase"],
                                "event_cluster_id": event_classification.cluster_id,
                                "condition_id": condition_id,
                                "token_id": token,
                                "outcome_index": index,
                                "outcome_label": label,
                                "gamma_probability": probability,
                                "threshold_eligible": int(threshold_eligible),
                                "observed_at": observed_at,
                            }
                            outcomes.append(outcome_row)
                            if threshold_eligible:
                                if token in token_contexts:
                                    quality.append(
                                        self._quality_row(
                                            cycle_id, run_id, "HIGH", "TOKEN_IDENTITY_COLLISION",
                                            event_classification.family,
                                            {"token_id": token, "condition_id": condition_id},
                                        )
                                    )
                                    continue
                                token_contexts[token] = {
                                    "outcome": outcome_row,
                                    "market": market_row,
                                    "event": event_row,
                                }

        open_before = self.repository.open_episodes() if all_complete else []
        book_tokens = list(token_contexts)
        book_tokens.extend(str(row["token_id"]) for row in open_before)
        book_attempt_objects: dict[str, BookAttempt] = {}
        if all_complete:
            book_attempt_objects = self.clob.fetch_books(run_id, book_tokens, budget=budget)
        book_attempts: list[dict[str, Any]] = []
        book_snapshots: list[dict[str, Any]] = []
        book_ladder: list[dict[str, Any]] = []
        snapshot_by_token: dict[str, dict[str, Any]] = {}
        parsed_by_token: dict[str, Any] = {}
        five_walk_by_token: dict[str, Any] = {}
        for token, attempt in book_attempt_objects.items():
            if attempt.received_at:
                receipts.append(attempt.received_at)
            book_attempts.append(
                {
                    "book_attempt_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "token_id": token,
                    "status": attempt.status,
                    "logical_request_id": attempt.request_id,
                    "observed_at": attempt.received_at,
                    "error_type": attempt.error_type,
                    "error_message": attempt.error_message,
                }
            )
            if attempt.status != "OBSERVED" or attempt.raw is None or attempt.parsed is None:
                continue
            fee = self.clob.fetch_fee(run_id, token, budget=budget)
            if fee.received_at:
                receipts.append(fee.received_at)
            if fee.raw is not None and fee.received_at is not None:
                raw_payloads.append(
                    self.repository.raw_payload_row(
                        cycle_id=cycle_id,
                        run_id=run_id,
                        payload_kind="CLOB_PUBLIC_FEE",
                        sport_family=token_contexts.get(token, {}).get("outcome", {}).get("sport_family"),
                        logical_request_id=fee.request_id,
                        observed_at=fee.received_at,
                        raw=fee.raw,
                    )
                )
            compressed, canonical_sha, canonical_bytes = canonical_book_gzip(attempt.raw, token)
            parsed = attempt.parsed
            snapshot_id = uuid4().hex
            snapshot = {
                "book_snapshot_id": snapshot_id,
                "cycle_id": cycle_id,
                "run_id": run_id,
                "token_id": token,
                "logical_request_id": str(attempt.request_id),
                "observed_at": str(attempt.received_at),
                "source_timestamp": parsed.source_timestamp,
                "canonical_sha256": canonical_sha,
                "canonical_bytes": canonical_bytes,
                "gzip_bytes": len(compressed),
                "book_gzip": compressed,
                "best_bid": parsed.bids[0].price if parsed.bids else None,
                "best_ask": parsed.asks[0].price if parsed.asks else None,
                "bid_level_count": len(parsed.bids),
                "ask_level_count": len(parsed.asks),
                "tick_size": parsed.tick_size,
                "min_size": parsed.min_size,
                "fee_status": fee.status,
                "public_fee_rate_bps": fee.fee_rate_bps,
            }
            book_snapshots.append(snapshot)
            snapshot_by_token[token] = snapshot
            parsed_by_token[token] = parsed
            for notional in self.config.trading.research.executable_notional_ladder_usdc:
                ask = walk_asks(parsed.asks, notional)
                if notional == 5:
                    five_walk_by_token[token] = ask
                if ask.status == "FULL" and ask.shares > 0:
                    bid = walk_bids(parsed.bids, ask.shares)
                    bid_status = bid.status
                else:
                    bid = None
                    bid_status = "NOT_APPLICABLE"
                book_ladder.append(
                    {
                        "ladder_observation_id": uuid4().hex,
                        "book_snapshot_id": snapshot_id,
                        "cycle_id": cycle_id,
                        "token_id": token,
                        "notional_usdc": notional,
                        "ask_status": ask.status,
                        "ask_filled_usdc": ask.filled,
                        "ask_remaining_usdc": ask.remaining,
                        "ask_shares": ask.shares,
                        "ask_vwap": ask.vwap,
                        "ask_worst_price": ask.worst_price,
                        "ask_levels_used": ask.levels_used,
                        "immediate_bid_status": bid_status,
                        "immediate_bid_filled_shares": bid.filled if bid else 0.0,
                        "immediate_bid_remaining_shares": bid.remaining if bid else ask.shares,
                        "immediate_bid_vwap": bid.vwap if bid else None,
                        "immediate_bid_worst_price": bid.worst_price if bid else None,
                        "immediate_bid_levels_used": bid.levels_used if bid else 0,
                    }
                )

        prior_states = self.repository.latest_threshold_states() if all_complete else {}
        existing_keys = self.repository.existing_episode_keys() if all_complete else set()
        threshold_vectors: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        for token, context in token_contexts.items():
            attempt = book_attempt_objects.get(token)
            snapshot = snapshot_by_token.get(token)
            walk = five_walk_by_token.get(token)
            current_vwap = walk.vwap if walk is not None and walk.status == "FULL" else None
            observation_status = (
                walk.status if walk is not None else
                "BOOK_UNAVAILABLE" if attempt is None else attempt.status
            )
            observed_at = str(attempt.received_at) if attempt and attempt.received_at else iso_utc(current)
            prior_row = prior_states.get(token)
            prior = None
            if prior_row is not None:
                prior = PriorThresholdState(
                    observed_at=str(prior_row["observed_at"]),
                    executable_ask_vwap=_number(prior_row["executable_ask_vwap_5"]),
                    observation_status=str(prior_row["observation_status"]),
                )
            vector = evaluate_threshold_vector(
                current_vwap=current_vwap,
                current_observed_at=observed_at,
                prior=prior,
                thresholds=self.config.trading.research.threshold_grid,
                max_gap_seconds=self.config.trading.crossing_max_gap_seconds,
            )
            vector_id = uuid4().hex
            outcome = context["outcome"]
            market = context["market"]
            event = context["event"]
            threshold_vectors.append(
                {
                    "threshold_vector_id": vector_id,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "sport_family": outcome["sport_family"],
                    "season_phase": outcome["season_phase"],
                    "event_cluster_id": outcome["event_cluster_id"],
                    "condition_id": outcome["condition_id"],
                    "token_id": token,
                    "book_snapshot_id": snapshot["book_snapshot_id"] if snapshot else None,
                    "observed_at": observed_at,
                    "observation_status": observation_status,
                    "executable_ask_vwap_5": current_vwap,
                    "prior_observed_at": prior.observed_at if prior else None,
                    "prior_observation_status": prior.observation_status if prior else None,
                    "prior_executable_ask_vwap_5": prior.executable_ask_vwap if prior else None,
                    "observation_gap_seconds": vector.gap_seconds,
                    "states_json": canonical_json(vector.states),
                    "upward_crossings_json": canonical_json([f"{value:.2f}" for value in vector.upward_crossings]),
                    "left_censored_json": canonical_json([f"{value:.2f}" for value in vector.left_censored]),
                    "gap_censored_json": canonical_json([f"{value:.2f}" for value in vector.gap_censored]),
                }
            )
            if snapshot is None or walk is None or walk.status != "FULL":
                continue
            for threshold in vector.upward_crossings:
                key = (str(outcome["condition_id"]), token, float(threshold))
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                episodes.append(
                    {
                        "episode_id": uuid4().hex,
                        "threshold_vector_id": vector_id,
                        "origin_utc_date": self.repository.database_utc_date,
                        "created_run_id": run_id,
                        "sport_family": outcome["sport_family"],
                        "season_phase": outcome["season_phase"],
                        "competition_code": event["competition_code"],
                        "event_id": market["event_id"],
                        "event_cluster_id": outcome["event_cluster_id"],
                        "condition_id": outcome["condition_id"],
                        "token_id": token,
                        "outcome_index": outcome["outcome_index"],
                        "outcome_label": outcome["outcome_label"],
                        "threshold": float(threshold),
                        "crossed_at": observed_at,
                        "entry_ask_vwap": float(walk.vwap),
                        "entry_shares_5": walk.shares,
                        "entry_book_snapshot_id": snapshot["book_snapshot_id"],
                        "liquidity": market["liquidity_num"] if market["liquidity_num"] is not None else market["liquidity"],
                        "volume_num": market["volume_num"],
                        "volume_24hr": market["volume_24hr"],
                    }
                )

        paths: list[dict[str, Any]] = []
        for episode in open_before:
            token = str(episode["token_id"])
            parsed = parsed_by_token.get(token)
            snapshot = snapshot_by_token.get(token)
            if parsed is None or snapshot is None:
                status = "BOOK_UNAVAILABLE"
                bid = None
            else:
                bid = walk_bids(parsed.bids, float(episode["entry_shares_5"]))
                status = bid.status
            paths.append(
                {
                    "episode_path_observation_id": uuid4().hex,
                    "episode_id": episode["episode_id"],
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "token_id": token,
                    "book_snapshot_id": snapshot["book_snapshot_id"] if snapshot else None,
                    "observed_at": snapshot["observed_at"] if snapshot else iso_utc(current),
                    "path_status": status,
                    "best_bid": parsed.bids[0].price if parsed and parsed.bids else None,
                    "requested_shares": float(episode["entry_shares_5"]),
                    "filled_shares": bid.filled if bid else 0.0,
                    "remaining_shares": bid.remaining if bid else float(episode["entry_shares_5"]),
                    "executable_bid_vwap": bid.vwap if bid else None,
                    "worst_bid": bid.worst_price if bid else None,
                    "levels_used": bid.levels_used if bid else 0,
                }
            )

        resolution_attempts: list[dict[str, Any]] = []
        resolutions: list[dict[str, Any]] = []
        if all_complete:
            conditions = sorted({str(row["condition_id"]) for row in open_before})
            for condition_id in conditions:
                if not self.repository.resolution_due(
                    condition_id,
                    now=current,
                    interval_minutes=self.config.trading.research.resolution_retry_minutes,
                ):
                    continue
                result = self.clob.fetch_resolution(run_id, condition_id, budget=budget)
                if result.received_at:
                    receipts.append(result.received_at)
                resolution_attempts.append(
                    {
                        "resolution_attempt_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "condition_id": condition_id,
                        "attempted_at": iso_utc(current),
                        "status": result.status,
                        "logical_request_id": result.request_id,
                        "error_type": result.error_type,
                        "error_message": result.error_message,
                    }
                )
                if result.raw is not None and result.received_at is not None:
                    raw_payloads.append(
                        self.repository.raw_payload_row(
                            cycle_id=cycle_id,
                            run_id=run_id,
                            payload_kind="CLOB_PUBLIC_RESOLUTION",
                            sport_family=None,
                            logical_request_id=result.request_id,
                            observed_at=result.received_at,
                            raw=result.raw,
                        )
                    )
                if result.status != "ERROR":
                    resolutions.append(
                        {
                            "resolution_observation_id": uuid4().hex,
                            "cycle_id": cycle_id,
                            "run_id": run_id,
                            "condition_id": condition_id,
                            "observed_at": result.received_at or iso_utc(current),
                            "resolution_status": result.status,
                            "winner_indices_json": canonical_json(list(result.winner_indices)),
                            "logical_request_id": result.request_id,
                            "raw_sha256": hashlib.sha256(result.raw).hexdigest() if result.raw else None,
                            "evidence_json": canonical_json(result.payload or {}),
                        }
                    )

        sports_clock_rows: list[dict[str, Any]] = []
        for game_id, update in clock_batch.updates.items():
            payload = update.payload
            sports_clock_rows.append(
                {
                    "sports_clock_observation_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "event_cluster_id": update.cluster_id,
                    "game_id": game_id,
                    "observed_at": update.received_at,
                    "period_raw": _clock_raw(payload.get("period")),
                    "elapsed_raw": _clock_raw(payload.get("elapsed", payload.get("clock"))),
                    "score_raw": _clock_raw(payload.get("score")),
                    "live": _boolean_integer(payload.get("live")),
                    "ended": _boolean_integer(payload.get("ended")),
                    "logical_request_id": clock_batch.request_id,
                    "raw_sha256": hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
                    "clock_json": canonical_json(payload),
                }
            )

        skew = receipt_skew_seconds(receipts)
        if skew > self.config.trading.max_receipt_skew_seconds:
            quality.append(
                self._quality_row(
                    cycle_id, run_id, "CRITICAL", "MAX_RECEIPT_SKEW_EXCEEDED", None,
                    {"observed_seconds": skew, "limit_seconds": self.config.trading.max_receipt_skew_seconds},
                )
            )
            fatal_error = "source receipt skew exceeded the frozen 90-second boundary"
            episodes = []

        completed = datetime.now(timezone.utc)
        budget.assert_within_hard_deadline()
        family_counts = Counter(row["sport_family"] for row in events if row["classification_status"] == "ACCEPTED")
        summary = {
            "status": "FAILED_HEALTH_GATE" if fatal_error else "COLLECTED",
            "cycle_id": cycle_id,
            "families": {
                family: {
                    "source_events": next(row["source_event_count"] for row in sweeps if row["sport_family"] == family),
                    "accepted_events": family_counts[family],
                    "cursor_complete": bool(next(row["cursor_complete"] for row in sweeps if row["sport_family"] == family)),
                }
                for family in self.config.registry.by_code
            },
            "markets": len(markets),
            "eligible_outcomes": sum(int(row["threshold_eligible"]) for row in outcomes),
            "book_snapshots": len(book_snapshots),
            "threshold_vectors": len(threshold_vectors),
            "episodes_opened": len(episodes),
            "path_observations": len(paths),
            "resolution_observations": len(resolutions),
            "sports_clock_status": clock_batch.status,
            "receipt_skew_seconds": skew,
            "fatal_error": fatal_error,
        }
        cycle = {
            "cycle_id": cycle_id,
            "run_id": run_id,
            "slot_start_utc": slot_start,
            "job_name": self.config.job_name,
            "mode": self.config.mode,
            "started_at": iso_utc(started_wall),
            "cooperative_deadline_at": iso_utc(started_wall + timedelta(seconds=self.config.trading.cooperative_budget_seconds)),
            "request_stop_at": iso_utc(started_wall + timedelta(seconds=self.config.trading.cooperative_budget_seconds - self.config.trading.stop_margin_seconds)),
            "hard_deadline_at": iso_utc(started_wall + timedelta(seconds=self.config.trading.hard_cycle_seconds)),
            "completed_at": iso_utc(completed),
            "elapsed_seconds": budget.elapsed(),
            "receipt_skew_seconds": skew,
            "all_families_cursor_complete": int(all_complete),
            "request_envelope_json": canonical_json(
                {
                    "families": {
                        item.code: {"tag_id": item.tag_id, "live": True, "related_tags": False}
                        for item in self.config.registry.families
                    },
                    "liquidity_gate": None,
                    "volume_gate": None,
                    "cadence_minutes": 5,
                }
            ),
            "summary_json": canonical_json(summary),
        }
        storage = storage_metric_row(
            path=self.repository.path,
            storage=self.config.trading.storage,
            phase="pre_atomic_publication",
            cycle_id=cycle_id,
            run_id=run_id,
        )
        if storage["guard_state"] == "STOP":
            raise RuntimeError("storage safety gate reached STOP before atomic publication")
        if storage["guard_state"] == "WARN":
            quality.append(
                self._quality_row(cycle_id, run_id, "WARN", "STORAGE_USED_RATIO_WARNING", None, storage)
            )
        bundle = {
            "cycle": cycle,
            "sweeps": sweeps,
            "raw_payloads": raw_payloads,
            "events": events,
            "tags": tags,
            "series": series,
            "teams": teams,
            "markets": markets,
            "outcomes": outcomes,
            "book_attempts": book_attempts,
            "book_snapshots": book_snapshots,
            "book_ladder": book_ladder,
            "threshold_vectors": threshold_vectors,
            "episodes": episodes,
            "paths": paths,
            "resolution_attempts": resolution_attempts,
            "resolutions": resolutions,
            "sports_clock": sports_clock_rows,
            "quality_issues": quality,
            "storage_metrics": [storage],
            "database_checks": [],
        }
        return CollectionProduct(bundle, summary, fatal_error)

    @staticmethod
    def _quality_row(
        cycle_id: str,
        run_id: str,
        severity: str,
        issue_type: str,
        sport_family: str | None,
        detail: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "data_quality_issue_id": uuid4().hex,
            "cycle_id": cycle_id,
            "run_id": run_id,
            "observed_at": iso_utc(),
            "severity": severity,
            "issue_type": issue_type,
            "sport_family": sport_family,
            "detail_json": canonical_json(detail),
        }
