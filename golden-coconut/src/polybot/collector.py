"""Five-family lifecycle census and all-notional displayed-book collector."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Mapping
from uuid import uuid4

from .api.clob_client import (
    BookAttempt,
    ClobClient,
    ClobClientPool,
    canonical_book_gzip,
    walk_asks,
    walk_bids,
)
from .api.gamma_client import (
    EventFollowupAttempt,
    EventSweep,
    GammaClient,
    GammaFamilyPool,
    TimedEventSweep,
)
from .api.sports_client import (
    ClockBatch,
    ClockTarget,
    SportsClockClient,
)
from .api.transport import CycleBudget, canonical_json, iso_utc, receipt_skew_seconds
from .classifier import EventClassification, classify_event, classify_market
from .config import BotConfig
from .crossings import PriorThresholdState, evaluate_threshold_vector
from .db.repository import ResearchRepository, storage_metric_row
from .lifecycle import (
    TERMINAL_LIFECYCLE_STATES,
    classify_gamma_lifecycle,
    gamma_clock_fallback,
    minutes_to_scheduled_start,
    parse_source_utc,
    raw_lifecycle_json,
)


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


def _validate_discovery_window(
    classification: EventClassification,
    *,
    window_min: datetime,
    window_max: datetime,
    tracked_event: bool,
) -> EventClassification:
    evidence = dict(classification.evidence)
    scheduled_start_raw = evidence.get("scheduled_start_raw")
    scheduled_start = parse_source_utc(evidence.get("scheduled_start_utc"))
    if scheduled_start_raw in (None, ""):
        status = "MISSING"
    elif scheduled_start is None:
        status = "INVALID"
    elif window_min <= scheduled_start < window_max:
        status = "WITHIN_WINDOW"
    else:
        status = "OUTSIDE_WINDOW"
    evidence["discovery_window_validation"] = {
        "status": status,
        "tracked_event": tracked_event,
        "half_open": True,
        "start_time_min": iso_utc(window_min),
        "start_time_max": iso_utc(window_max),
    }
    if tracked_event or status == "WITHIN_WINDOW":
        return replace(classification, evidence=evidence)
    reason = {
        "MISSING": "DISCOVERY_SCHEDULE_MISSING",
        "INVALID": "DISCOVERY_SCHEDULE_INVALID",
        "OUTSIDE_WINDOW": "DISCOVERY_SCHEDULE_OUTSIDE_WINDOW",
    }[status]
    reasons = tuple(dict.fromkeys((*classification.reasons, reason)))
    return replace(
        classification,
        status="REJECTED",
        reasons=reasons,
        evidence=evidence,
    )


@dataclass(frozen=True)
class CollectionProduct:
    bundle: Mapping[str, Any]
    summary: Mapping[str, Any]
    fatal_error: str | None = None


@dataclass(frozen=True)
class EventMaterial:
    source: Mapping[str, Any]
    classification: EventClassification
    row: Mapping[str, Any]
    lifecycle_row: Mapping[str, Any] | None
    observed_at: str
    logical_request_id: str


class Collector:
    def __init__(
        self,
        config: BotConfig,
        repository: ResearchRepository,
        gamma: GammaClient | GammaFamilyPool,
        clob: ClobClient | ClobClientPool,
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
        sweep_id: str | None,
        raw_payload_id: str,
        run_id: str,
        observed_at: str,
        source_kind: str,
        logical_request_id: str,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any] | None,
    ]:
        event_observation_id = uuid4().hex
        metrics = _source_metrics(event)
        sport = event.get("sport") if isinstance(event.get("sport"), Mapping) else {}
        evidence = classification.evidence
        lifecycle_state = str(evidence["lifecycle_state"])
        row = {
            "event_observation_id": event_observation_id,
            "cycle_id": cycle_id,
            "sweep_id": sweep_id,
            "raw_payload_id": raw_payload_id,
            "run_id": run_id,
            "source_kind": source_kind,
            "sport_family": classification.family,
            "event_id": str(event.get("id") or f"MISSING:{uuid4().hex}"),
            "canonical_game_slug": str(evidence.get("canonical_game_slug") or ""),
            "game_id_alias": str(event.get("gameId") or event.get("game_id") or "") or None,
            "event_cluster_id": classification.cluster_id,
            "title": str(event.get("title") or "") or None,
            "slug": str(event.get("slug") or "") or None,
            "observed_at": observed_at,
            "competition_code": classification.competition_code,
            "competition_name": classification.competition_name,
            "season_phase": str(evidence["season_phase"]),
            "lifecycle_state": lifecycle_state,
            "lifecycle_reason": str(evidence["lifecycle_reason"]),
            "scheduled_start_field": evidence.get("scheduled_start_field"),
            "scheduled_start_raw": evidence.get("scheduled_start_raw"),
            "scheduled_start_utc": evidence.get("scheduled_start_utc"),
            "classification_status": classification.status,
            "classification_reason": (
                "ELIGIBLE"
                if not classification.reasons
                else ";".join(classification.reasons)
            ),
            "classifier_version": self.config.trading.classifier_version,
            "sports_registry_sha256": self.config.trading.sports_registry_sha256,
            **metrics,
            "active": _boolean_integer(event.get("active")),
            "closed": _boolean_integer(event.get("closed")),
            "live": _boolean_integer(event.get("live")),
            "ended": _boolean_integer(event.get("ended")),
            "end_date": str(event.get("endDate") or "") or None,
            "raw_lifecycle_json": str(evidence["raw_lifecycle_json"]),
            "sport_json": canonical_json(sport),
            "classification_evidence_json": canonical_json(evidence),
            "normalized_json": canonical_json(
                {
                    "family": classification.family,
                    "competition_code": classification.competition_code,
                    "cluster_id": classification.cluster_id,
                    "season_phase": evidence["season_phase"],
                    "lifecycle_state": lifecycle_state,
                    "scheduled_start_utc": evidence.get("scheduled_start_utc"),
                    **metrics,
                }
            ),
        }
        tags: list[dict[str, Any]] = []
        for index, item in enumerate(
            event.get("tags", []) if isinstance(event.get("tags"), list) else []
        ):
            if isinstance(item, Mapping):
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
        for index, item in enumerate(
            event.get("series", []) if isinstance(event.get("series"), list) else []
        ):
            if isinstance(item, Mapping):
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
        for index, item in enumerate(
            event.get("teams", []) if isinstance(event.get("teams"), list) else []
        ):
            if isinstance(item, Mapping):
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
        lifecycle_row = None
        if classification.accepted:
            lifecycle_row = {
                "game_lifecycle_observation_id": uuid4().hex,
                "cycle_id": cycle_id,
                "run_id": run_id,
                "event_observation_id": event_observation_id,
                "sport_family": classification.family,
                "event_id": row["event_id"],
                "canonical_game_slug": row["canonical_game_slug"],
                "game_id_alias": row["game_id_alias"],
                "event_cluster_id": classification.cluster_id,
                "observed_at": observed_at,
                "source_kind": (
                    "GAMMA_DISCOVERY" if source_kind == "DISCOVERY" else "GAMMA_FOLLOWUP"
                ),
                "lifecycle_state": lifecycle_state,
                "is_terminal": int(lifecycle_state in TERMINAL_LIFECYCLE_STATES),
                "phase_source": str(evidence["lifecycle_reason"]),
                "scheduled_start_field": row["scheduled_start_field"],
                "scheduled_start_raw": row["scheduled_start_raw"],
                "scheduled_start_utc": row["scheduled_start_utc"],
                "logical_request_id": logical_request_id,
                "raw_lifecycle_json": row["raw_lifecycle_json"],
                "evidence_json": canonical_json(
                    {
                        "classification_status": classification.status,
                        "classification_reasons": list(classification.reasons),
                    }
                ),
            }
        return row, tags, series, teams, lifecycle_row

    def collect(
        self,
        run_id: str,
        *,
        slot_start: str,
        budget: CycleBudget,
        now: datetime | None = None,
    ) -> CollectionProduct:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cycle_id = uuid4().hex
        receipts: list[str] = []
        sweeps: list[dict[str, Any]] = []
        raw_payloads: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        tags: list[dict[str, Any]] = []
        series: list[dict[str, Any]] = []
        teams: list[dict[str, Any]] = []
        game_lifecycle: list[dict[str, Any]] = []
        schedule_revisions: list[dict[str, Any]] = []
        markets: list[dict[str, Any]] = []
        outcomes: list[dict[str, Any]] = []
        quality: list[dict[str, Any]] = []
        materials: list[EventMaterial] = []
        seen_event_keys: set[tuple[str, str]] = set()
        prior_games = self.repository.latest_game_states()
        prior_by_event = {
            (str(row["sport_family"]), str(row["event_id"])): row
            for row in prior_games.values()
        }
        tracked_event_keys = {
            key
            for key, row in prior_by_event.items()
            if str(row["lifecycle_state"]) not in TERMINAL_LIFECYCLE_STATES
        }
        identity_fatal = False

        def append_event(
            source_event: Mapping[str, Any],
            family_code: str,
            sweep_id: str | None,
            raw_payload_id: str,
            observed_at: str,
            source_kind: str,
            logical_request_id: str,
            discovery_window: tuple[datetime, datetime] | None = None,
            tracked_discovery: bool = False,
        ) -> EventClassification:
            nonlocal identity_fatal
            family = self.config.registry.by_code[family_code]
            classification = classify_event(source_event, family, self.config.registry)
            if discovery_window is not None:
                classification = _validate_discovery_window(
                    classification,
                    window_min=discovery_window[0],
                    window_max=discovery_window[1],
                    tracked_event=tracked_discovery,
                )
            event_row, event_tags, event_series, event_teams, lifecycle_row = self._event_rows(
                event=source_event,
                classification=classification,
                cycle_id=cycle_id,
                sweep_id=sweep_id,
                raw_payload_id=raw_payload_id,
                run_id=run_id,
                observed_at=observed_at,
                source_kind=source_kind,
                logical_request_id=logical_request_id,
            )
            events.append(event_row)
            tags.extend(event_tags)
            series.extend(event_series)
            teams.extend(event_teams)
            if lifecycle_row is not None:
                game_lifecycle.append(lifecycle_row)
            material = EventMaterial(
                source_event,
                classification,
                event_row,
                lifecycle_row,
                observed_at,
                logical_request_id,
            )
            materials.append(material)
            prior = prior_by_event.get((family_code, str(event_row["event_id"])))
            if prior is not None:
                if str(prior["event_cluster_id"]) != classification.cluster_id:
                    identity_fatal = True
                    quality.append(
                        self._quality_row(
                            cycle_id,
                            run_id,
                            "CRITICAL",
                            "CANONICAL_GAME_SLUG_REVISION",
                            family_code,
                            {
                                "event_id": event_row["event_id"],
                                "prior_cluster": prior["event_cluster_id"],
                                "new_cluster": classification.cluster_id,
                            },
                        )
                    )
                prior_schedule = prior.get("scheduled_start_utc") or prior.get(
                    "scheduled_start_raw"
                )
                new_schedule = event_row.get("scheduled_start_utc") or event_row.get(
                    "scheduled_start_raw"
                )
                if prior_schedule and new_schedule and prior_schedule != new_schedule:
                    schedule_revisions.append(
                        {
                            "schedule_revision_observation_id": uuid4().hex,
                            "cycle_id": cycle_id,
                            "run_id": run_id,
                            "sport_family": family_code,
                            "event_id": event_row["event_id"],
                            "event_cluster_id": classification.cluster_id,
                            "observed_at": observed_at,
                            "prior_scheduled_start_field": prior.get(
                                "scheduled_start_field"
                            ),
                            "prior_scheduled_start_raw": prior.get("scheduled_start_raw"),
                            "prior_scheduled_start_utc": prior.get("scheduled_start_utc"),
                            "new_scheduled_start_field": event_row.get(
                                "scheduled_start_field"
                            ),
                            "new_scheduled_start_raw": event_row.get(
                                "scheduled_start_raw"
                            ),
                            "new_scheduled_start_utc": event_row.get(
                                "scheduled_start_utc"
                            ),
                            "source_kind": source_kind,
                            "evidence_json": canonical_json(
                                {
                                    "logical_request_id": logical_request_id,
                                    "append_only_revision": True,
                                }
                            ),
                        }
                    )
            if classification.status == "DRIFT":
                quality.append(
                    self._quality_row(
                        cycle_id,
                        run_id,
                        "HIGH",
                        "SPORT_IDENTITY_DRIFT",
                        family_code,
                        {
                            "event_id": event_row["event_id"],
                            "reasons": classification.reasons,
                        },
                    )
                )
            return classification

        if isinstance(self.gamma, GammaFamilyPool):
            timed_sweeps = self.gamma.fetch_families_events(
                run_id,
                self.config.registry.families,
                budget=budget,
                slot_start=slot_start,
            )
        else:
            timed_sweeps = tuple(
                TimedEventSweep(
                    family,
                    iso_utc(),
                    self.gamma.fetch_family_events(
                        run_id, family, budget=budget, slot_start=slot_start
                    ),
                )
                for family in self.config.registry.families
            )

        sweep_results: list[EventSweep] = []
        for timed_sweep in timed_sweeps:
            family = timed_sweep.family
            family_started = timed_sweep.started_at
            sweep = timed_sweep.sweep
            sweep_results.append(sweep)
            sweep_id = uuid4().hex
            window_min = parse_source_utc(sweep.start_time_min)
            window_max = parse_source_utc(sweep.start_time_max)
            if window_min is None or window_max is None or window_min >= window_max:
                raise ValueError("Gamma discovery sweep must carry an exact UTC time window")
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
                for event in page.events:
                    event_id = str(event.get("id") or "")
                    event_key = (family.code, event_id)
                    if event_id and event_key in seen_event_keys:
                        quality.append(
                            self._quality_row(
                                cycle_id,
                                run_id,
                                "INFO",
                                "DUPLICATE_EVENT_ACROSS_QUERY_TAGS_DEDUPED",
                                family.code,
                                {"event_id": event_id},
                            )
                        )
                        continue
                    seen_event_keys.add(event_key)
                    classification = append_event(
                        event,
                        family.code,
                        sweep_id,
                        str(payload_row["raw_payload_id"]),
                        page.received_at,
                        "DISCOVERY",
                        page.request_id,
                        (window_min, window_max),
                        event_key in tracked_event_keys,
                    )
                    accepted += int(classification.status == "ACCEPTED")
                    rejected += int(classification.status == "REJECTED")
                    drift += int(classification.status == "DRIFT")
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
                    "start_time_min": sweep.start_time_min,
                    "start_time_max": sweep.start_time_max,
                    "request_envelope_json": canonical_json(
                        {
                            "endpoint": self.config.trading.gamma.endpoint,
                            "closed": False,
                            "include_children": False,
                            "tag_id": family.tag_id,
                            "query_tag_ids": list(family.query_tag_ids),
                            "related_tags": False,
                            "start_time_min": sweep.start_time_min,
                            "start_time_max": sweep.start_time_max,
                            "page_size": self.config.trading.gamma.page_size,
                            "live": None,
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
                            cycle_id,
                            run_id,
                            "CRITICAL",
                            "FAMILY_CURSOR_INCOMPLETE",
                            sweep.family,
                            {
                                "pages": len(sweep.pages),
                                "query_tag_ids": list(
                                    self.config.registry.by_code[
                                        sweep.family
                                    ].query_tag_ids
                                ),
                                "terminal_cursor": sweep.terminal_cursor,
                            },
                        )
                    )

        followup_complete = all_complete
        if all_complete:
            pending_tracks: list[dict[str, Any]] = []
            pending_keys: set[tuple[str, str]] = set()
            for tracked in sorted(
                self.repository.tracked_games(),
                key=lambda row: (str(row["sport_family"]), str(row["event_id"])),
            ):
                key = (str(tracked["sport_family"]), str(tracked["event_id"]))
                if key in seen_event_keys or key in pending_keys:
                    continue
                pending_keys.add(key)
                pending_tracks.append(tracked)

            if isinstance(self.gamma, GammaFamilyPool):
                followup_attempts = self.gamma.fetch_events(
                    run_id,
                    tuple(
                        (str(row["sport_family"]), str(row["event_id"]))
                        for row in pending_tracks
                    ),
                    budget=budget,
                )
            else:
                fallback_attempts: list[EventFollowupAttempt] = []
                for tracked in pending_tracks:
                    family = str(tracked["sport_family"])
                    event_id = str(tracked["event_id"])
                    try:
                        followup = self.gamma.fetch_event(
                            run_id,
                            event_id,
                            family,
                            budget=budget,
                        )
                    except (RuntimeError, ValueError) as error:
                        fallback_attempts.append(
                            EventFollowupAttempt(
                                family,
                                event_id,
                                None,
                                type(error).__name__,
                                str(error)[:500],
                            )
                        )
                    else:
                        fallback_attempts.append(
                            EventFollowupAttempt(
                                family, event_id, followup, None, None
                            )
                        )
                followup_attempts = tuple(fallback_attempts)

            for tracked, attempt in zip(
                pending_tracks, followup_attempts, strict=True
            ):
                if attempt.followup is None:
                    followup_complete = False
                    quality.append(
                        self._quality_row(
                            cycle_id,
                            run_id,
                            "HIGH",
                            "TRACKED_GAME_FOLLOWUP_FAILED",
                            str(tracked["sport_family"]),
                            {
                                "event_id": tracked["event_id"],
                                "event_cluster_id": tracked["event_cluster_id"],
                                "error_type": attempt.error_type,
                                "error_message": attempt.error_message,
                            },
                        )
                    )
                    continue
                followup = attempt.followup
                receipts.append(followup.received_at)
                payload_row = self.repository.raw_payload_row(
                    cycle_id=cycle_id,
                    run_id=run_id,
                    payload_kind="GAMMA_EVENT_FOLLOWUP",
                    sport_family=str(tracked["sport_family"]),
                    logical_request_id=followup.request_id,
                    observed_at=followup.received_at,
                    raw=followup.raw,
                )
                raw_payloads.append(payload_row)
                seen_event_keys.add((attempt.family, attempt.event_id))
                append_event(
                    followup.event,
                    str(tracked["sport_family"]),
                    None,
                    str(payload_row["raw_payload_id"]),
                    followup.received_at,
                    "FOLLOWUP",
                    followup.request_id,
                )
        if not followup_complete and fatal_error is None:
            fatal_error = "one or more carried games lacked an explicit Gamma follow-up"
        if identity_fatal and fatal_error is None:
            fatal_error = "canonical game identity changed inside the immutable active epoch"

        census_healthy = all_complete and followup_complete and not identity_fatal
        empty_clock = ClockBatch(
            request_id=uuid4().hex,
            status="SKIPPED_INCOMPLETE_CENSUS",
            started_at=iso_utc(),
            completed_at=iso_utc(),
            target_count=0,
            matched_count=0,
            message_count=0,
            updates={},
            raw_messages=(),
        )
        clock_batch = empty_clock
        sports_clock_rows: list[dict[str, Any]] = []
        context_by_cluster = {
            material.classification.cluster_id: material
            for material in materials
            if material.classification.accepted
        }
        if census_healthy:
            clock_targets: dict[str, ClockTarget] = {}
            for cluster, material in context_by_cluster.items():
                slug = str(material.row["canonical_game_slug"])
                alias = str(material.row["game_id_alias"] or "")
                target = ClockTarget(slug, cluster, (alias,) if alias else ())
                prior = clock_targets.get(slug)
                if prior is not None and prior != target:
                    raise RuntimeError("canonical Sports WSS target conflict")
                clock_targets[slug] = target
            clock_batch = self.sports_clock.collect(
                run_id, clock_targets, budget=budget
            )
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
                        cycle_id,
                        run_id,
                        "WARN",
                        "SPORTS_WSS_NO_MESSAGE_IS_NOT_ABSENCE",
                        None,
                        {
                            "status": clock_batch.status,
                            "target_count": clock_batch.target_count,
                            "matched_count": clock_batch.matched_count,
                            "error_type": clock_batch.error_type,
                        },
                    )
                )
            for slug, update in clock_batch.updates.items():
                payload = update.payload
                material = context_by_cluster[update.event_cluster_id]
                canonical = canonical_json(payload)
                sports_clock_rows.append(
                    {
                        "sports_clock_observation_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "event_cluster_id": update.event_cluster_id,
                        "canonical_game_slug": slug,
                        "game_id_alias": update.game_id_alias,
                        "observed_at": update.received_at,
                        "source_kind": "SPORTS_WSS",
                        "matched_by": update.matched_by,
                        "source_identity": update.source_identity,
                        "period_raw": _clock_raw(payload.get("period")),
                        "elapsed_raw": _clock_raw(
                            payload.get("elapsed", payload.get("clock"))
                        ),
                        "score_raw": _clock_raw(payload.get("score")),
                        "live": _boolean_integer(payload.get("live")),
                        "ended": _boolean_integer(payload.get("ended")),
                        "logical_request_id": clock_batch.request_id,
                        "raw_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                        "clock_json": canonical,
                    }
                )
                if any(
                    key in payload
                    for key in ("live", "ended", "gameStatus", "status", "resolution")
                ):
                    state, reason = classify_gamma_lifecycle(payload)
                    game_lifecycle.append(
                        {
                            "game_lifecycle_observation_id": uuid4().hex,
                            "cycle_id": cycle_id,
                            "run_id": run_id,
                            "event_observation_id": material.row["event_observation_id"],
                            "sport_family": material.classification.family,
                            "event_id": material.row["event_id"],
                            "canonical_game_slug": slug,
                            "game_id_alias": update.game_id_alias,
                            "event_cluster_id": update.event_cluster_id,
                            "observed_at": update.received_at,
                            "source_kind": "SPORTS_WSS",
                            "lifecycle_state": state,
                            "is_terminal": int(state in TERMINAL_LIFECYCLE_STATES),
                            "phase_source": reason,
                            "scheduled_start_field": material.row[
                                "scheduled_start_field"
                            ],
                            "scheduled_start_raw": material.row["scheduled_start_raw"],
                            "scheduled_start_utc": material.row["scheduled_start_utc"],
                            "logical_request_id": clock_batch.request_id,
                            "raw_lifecycle_json": raw_lifecycle_json(payload),
                            "evidence_json": canonical_json(
                                {
                                    "matched_by": update.matched_by,
                                    "source_identity": update.source_identity,
                                }
                            ),
                        }
                    )
            for cluster, material in context_by_cluster.items():
                slug = str(material.row["canonical_game_slug"])
                if slug in clock_batch.updates:
                    continue
                fallback = gamma_clock_fallback(material.source)
                if fallback is None:
                    continue
                canonical = canonical_json(fallback)
                sports_clock_rows.append(
                    {
                        "sports_clock_observation_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "event_cluster_id": cluster,
                        "canonical_game_slug": slug,
                        "game_id_alias": material.row["game_id_alias"],
                        "observed_at": material.observed_at,
                        "source_kind": "GAMMA_FALLBACK",
                        "matched_by": "SAME_CYCLE_GAMMA",
                        "source_identity": slug,
                        "period_raw": _clock_raw(fallback.get("period")),
                        "elapsed_raw": _clock_raw(
                            fallback.get("elapsed", fallback.get("clock"))
                        ),
                        "score_raw": _clock_raw(fallback.get("score")),
                        "live": _boolean_integer(fallback.get("live")),
                        "ended": _boolean_integer(fallback.get("ended")),
                        "logical_request_id": material.logical_request_id,
                        "raw_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                        "clock_json": canonical,
                    }
                )

        token_contexts: dict[str, dict[str, Any]] = {}
        condition_contexts: dict[str, dict[str, Any]] = {}
        conditions_by_game: defaultdict[str, set[str]] = defaultdict(set)
        if census_healthy:
            for material in materials:
                source_event = material.source
                event_classification = material.classification
                event_row = material.row
                if not event_classification.accepted:
                    continue
                source_markets = source_event.get("markets")
                if not isinstance(source_markets, list):
                    quality.append(
                        self._quality_row(
                            cycle_id,
                            run_id,
                            "WARN",
                            "EVENT_MARKETS_ARRAY_MISSING",
                            event_classification.family,
                            {"event_id": event_row["event_id"]},
                        )
                    )
                    continue
                event_metrics = _source_metrics(source_event)
                for source_market in source_markets:
                    if not isinstance(source_market, Mapping):
                        continue
                    classification = classify_market(
                        source_event, source_market, event_classification
                    )
                    market_observation_id = uuid4().hex
                    condition_id = str(
                        source_market.get("conditionId")
                        or source_market.get("condition_id")
                        or ""
                    )
                    market_id = str(source_market.get("id") or "")
                    extra_reasons: list[str] = []
                    if not condition_id or not market_id:
                        extra_reasons.append("MARKET_IDENTITY_MISSING")
                    phase_eligible = event_row["lifecycle_state"] in {
                        "DISCOVERED_OPEN",
                        "PREGAME",
                        "IN_PLAY",
                    }
                    if not phase_eligible:
                        extra_reasons.append("LIFECYCLE_PHASE_NOT_EXPLICITLY_EXECUTABLE")
                    reasons = tuple((*classification.reasons, *extra_reasons))
                    book_eligible = classification.eligible and not extra_reasons
                    metrics = _source_metrics(source_market)
                    market_row = {
                        "market_observation_id": market_observation_id,
                        "cycle_id": cycle_id,
                        "event_observation_id": event_row["event_observation_id"],
                        "run_id": run_id,
                        "sport_family": event_classification.family,
                        "season_phase": event_row["season_phase"],
                        "lifecycle_state": event_row["lifecycle_state"],
                        "event_id": event_row["event_id"],
                        "event_cluster_id": event_classification.cluster_id,
                        "condition_id": condition_id or None,
                        "market_id": market_id or None,
                        "question": str(source_market.get("question") or "") or None,
                        "group_item_title": str(
                            source_market.get("groupItemTitle") or ""
                        )
                        or None,
                        "sports_market_type": str(
                            source_market.get("sportsMarketType") or ""
                        )
                        or None,
                        "structure_kind": classification.structure,
                        "result_kind": classification.result_kind,
                        "neg_risk": _boolean_integer(source_market.get("negRisk")),
                        "observed_at": material.observed_at,
                        "active": _boolean_integer(source_market.get("active")),
                        "closed": _boolean_integer(source_market.get("closed")),
                        "accepting_source_activity": _boolean_integer(
                            source_market.get("acceptingOrders")
                        ),
                        "public_book_enabled": _boolean_integer(
                            source_market.get("enableOrderBook")
                        ),
                        **metrics,
                        "event_volume_num": event_metrics["volume_num"],
                        "event_volume_24hr": event_metrics["volume_24hr"],
                        "event_liquidity": event_metrics["liquidity"],
                        "event_liquidity_num": event_metrics["liquidity_num"],
                        "structure_eligible": int(
                            classification.structure_eligible and bool(condition_id)
                        ),
                        "eligible": int(book_eligible),
                        "exclusion_reason": (
                            "ELIGIBLE" if book_eligible else ";".join(reasons)
                        ),
                        "labels_json": canonical_json(list(classification.labels)),
                        "token_ids_json": canonical_json(
                            list(classification.token_ids)
                        ),
                        "probabilities_json": canonical_json(
                            list(classification.probabilities)
                        ),
                        "classification_evidence_json": canonical_json(
                            classification.evidence
                        ),
                        "normalized_json": canonical_json(
                            {
                                "family": event_classification.family,
                                "season_phase": event_row["season_phase"],
                                "lifecycle_state": event_row["lifecycle_state"],
                                "cluster": event_classification.cluster_id,
                                "structure": classification.structure,
                                **metrics,
                                "event_metrics": event_metrics,
                            }
                        ),
                    }
                    markets.append(market_row)
                    aligned = (
                        len(classification.labels)
                        == len(classification.token_ids)
                        == len(classification.probabilities)
                        == 2
                    )
                    if classification.structure_eligible and aligned and condition_id:
                        conditions_by_game[event_classification.cluster_id].add(
                            condition_id
                        )
                        condition_contexts[condition_id] = {
                            "event": event_row,
                            "market": market_row,
                        }
                        for index, (label, token, probability) in enumerate(
                            zip(
                                classification.labels,
                                classification.token_ids,
                                classification.probabilities,
                            )
                        ):
                            threshold_eligible = (
                                book_eligible
                                and index in classification.eligible_indices
                            )
                            outcome_row = {
                                "outcome_observation_id": uuid4().hex,
                                "market_observation_id": market_observation_id,
                                "cycle_id": cycle_id,
                                "run_id": run_id,
                                "sport_family": event_classification.family,
                                "season_phase": event_row["season_phase"],
                                "lifecycle_state": event_row["lifecycle_state"],
                                "event_cluster_id": event_classification.cluster_id,
                                "condition_id": condition_id,
                                "token_id": token,
                                "outcome_index": index,
                                "outcome_label": label,
                                "gamma_probability": probability,
                                "structure_eligible": 1,
                                "threshold_eligible": int(threshold_eligible),
                                "observed_at": material.observed_at,
                            }
                            outcomes.append(outcome_row)
                            if threshold_eligible:
                                if token in token_contexts:
                                    quality.append(
                                        self._quality_row(
                                            cycle_id,
                                            run_id,
                                            "HIGH",
                                            "TOKEN_IDENTITY_COLLISION",
                                            event_classification.family,
                                            {
                                                "token_id": token,
                                                "condition_id": condition_id,
                                            },
                                        )
                                    )
                                    continue
                                token_contexts[token] = {
                                    "outcome": outcome_row,
                                    "market": market_row,
                                    "event": event_row,
                                }

        open_before = self.repository.open_episodes() if census_healthy else []
        book_tokens = list(token_contexts)
        book_tokens.extend(str(row["token_id"]) for row in open_before)
        book_attempt_objects: dict[str, BookAttempt] = {}
        if census_healthy:
            book_attempt_objects = self.clob.fetch_books(
                run_id, book_tokens, budget=budget
            )
        fee_tokens = tuple(
            token
            for token, attempt in book_attempt_objects.items()
            if attempt.status == "OBSERVED"
            and attempt.raw is not None
            and attempt.parsed is not None
        )
        if isinstance(self.clob, ClobClientPool):
            fees_by_token = self.clob.fetch_fees(
                run_id, fee_tokens, budget=budget
            )
        else:
            fees_by_token = {
                token: self.clob.fetch_fee(run_id, token, budget=budget)
                for token in fee_tokens
            }
        book_attempts: list[dict[str, Any]] = []
        book_snapshots: list[dict[str, Any]] = []
        book_ladder: list[dict[str, Any]] = []
        snapshot_by_token: dict[str, dict[str, Any]] = {}
        parsed_by_token: dict[str, Any] = {}
        walks_by_key: dict[tuple[str, float], Any] = {}
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
            if (
                attempt.status != "OBSERVED"
                or attempt.raw is None
                or attempt.parsed is None
            ):
                continue
            fee = fees_by_token[token]
            if fee.received_at:
                receipts.append(fee.received_at)
            if fee.raw is not None and fee.received_at is not None:
                raw_payloads.append(
                    self.repository.raw_payload_row(
                        cycle_id=cycle_id,
                        run_id=run_id,
                        payload_kind="CLOB_PUBLIC_FEE",
                        sport_family=token_contexts.get(token, {})
                        .get("outcome", {})
                        .get("sport_family"),
                        logical_request_id=fee.request_id,
                        observed_at=fee.received_at,
                        raw=fee.raw,
                    )
                )
            compressed, canonical_sha, canonical_bytes = canonical_book_gzip(
                attempt.raw, token
            )
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
                walks_by_key[(token, notional)] = ask
                bid = (
                    walk_bids(parsed.bids, ask.shares)
                    if ask.status == "FULL" and ask.shares > 0
                    else None
                )
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
                        "immediate_bid_status": bid.status if bid else "NOT_APPLICABLE",
                        "immediate_bid_filled_shares": bid.filled if bid else 0.0,
                        "immediate_bid_remaining_shares": (
                            bid.remaining if bid else ask.shares
                        ),
                        "immediate_bid_vwap": bid.vwap if bid else None,
                        "immediate_bid_worst_price": bid.worst_price if bid else None,
                        "immediate_bid_levels_used": bid.levels_used if bid else 0,
                    }
                )

        prior_states = self.repository.latest_threshold_states() if census_healthy else {}
        existing_keys = self.repository.existing_episode_keys() if census_healthy else set()
        threshold_vectors: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        anchors: list[dict[str, Any]] = []
        for token, context in token_contexts.items():
            attempt = book_attempt_objects.get(token)
            snapshot = snapshot_by_token.get(token)
            outcome = context["outcome"]
            market = context["market"]
            event = context["event"]
            if snapshot is not None and event["lifecycle_state"] in {
                "DISCOVERED_OPEN",
                "PREGAME",
            }:
                minutes = minutes_to_scheduled_start(
                    str(snapshot["observed_at"]), event.get("scheduled_start_utc")
                )
                if minutes is not None and minutes > 0:
                    anchors.append(
                        {
                            "game_anchor_observation_id": uuid4().hex,
                            "cycle_id": cycle_id,
                            "run_id": run_id,
                            "sport_family": outcome["sport_family"],
                            "season_phase": outcome["season_phase"],
                            "event_cluster_id": outcome["event_cluster_id"],
                            "condition_id": outcome["condition_id"],
                            "token_id": token,
                            "book_snapshot_id": snapshot["book_snapshot_id"],
                            "observed_at": snapshot["observed_at"],
                            "scheduled_start_field": event["scheduled_start_field"],
                            "scheduled_start_raw": event["scheduled_start_raw"],
                            "scheduled_start_utc": event["scheduled_start_utc"],
                            "minutes_to_scheduled_start": minutes,
                            "anchor_role": "PRESTART_CANDIDATE",
                        }
                    )
            for notional in self.config.trading.research.executable_notional_ladder_usdc:
                walk = walks_by_key.get((token, notional))
                current_vwap = (
                    walk.vwap if walk is not None and walk.status == "FULL" else None
                )
                observation_status = (
                    walk.status
                    if walk is not None
                    else "BOOK_UNAVAILABLE"
                    if attempt is None
                    else attempt.status
                )
                observed_at = (
                    str(attempt.received_at)
                    if attempt and attempt.received_at
                    else iso_utc(current)
                )
                prior_row = prior_states.get((token, notional))
                prior = None
                if prior_row is not None:
                    prior = PriorThresholdState(
                        observed_at=str(prior_row["observed_at"]),
                        executable_ask_vwap=_number(
                            prior_row["executable_ask_vwap"]
                        ),
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
                threshold_vectors.append(
                    {
                        "threshold_vector_id": vector_id,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "sport_family": outcome["sport_family"],
                        "season_phase": outcome["season_phase"],
                        "lifecycle_state": outcome["lifecycle_state"],
                        "event_cluster_id": outcome["event_cluster_id"],
                        "condition_id": outcome["condition_id"],
                        "token_id": token,
                        "notional_usdc": notional,
                        "book_snapshot_id": (
                            snapshot["book_snapshot_id"] if snapshot else None
                        ),
                        "observed_at": observed_at,
                        "observation_status": observation_status,
                        "executable_ask_vwap": current_vwap,
                        "executable_ask_shares": (
                            walk.shares
                            if walk is not None and walk.status == "FULL"
                            else None
                        ),
                        "prior_observed_at": prior.observed_at if prior else None,
                        "prior_observation_status": (
                            prior.observation_status if prior else None
                        ),
                        "prior_executable_ask_vwap": (
                            prior.executable_ask_vwap if prior else None
                        ),
                        "observation_gap_seconds": vector.gap_seconds,
                        "states_json": canonical_json(vector.states),
                        "upward_crossings_json": canonical_json(
                            [f"{value:.2f}" for value in vector.upward_crossings]
                        ),
                        "left_censored_json": canonical_json(
                            [f"{value:.2f}" for value in vector.left_censored]
                        ),
                        "gap_censored_json": canonical_json(
                            [f"{value:.2f}" for value in vector.gap_censored]
                        ),
                    }
                )
                if snapshot is None or walk is None or walk.status != "FULL":
                    continue
                for threshold in vector.upward_crossings:
                    key = (
                        str(outcome["condition_id"]),
                        token,
                        notional,
                        float(threshold),
                    )
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
                            "lifecycle_state": outcome["lifecycle_state"],
                            "competition_code": event["competition_code"],
                            "event_id": market["event_id"],
                            "event_cluster_id": outcome["event_cluster_id"],
                            "condition_id": outcome["condition_id"],
                            "token_id": token,
                            "outcome_index": outcome["outcome_index"],
                            "outcome_label": outcome["outcome_label"],
                            "notional_usdc": notional,
                            "threshold": float(threshold),
                            "crossed_at": observed_at,
                            "entry_ask_vwap": float(walk.vwap),
                            "entry_shares": walk.shares,
                            "entry_book_snapshot_id": snapshot["book_snapshot_id"],
                            "liquidity": (
                                market["liquidity_num"]
                                if market["liquidity_num"] is not None
                                else market["liquidity"]
                            ),
                            "volume_num": market["volume_num"],
                            "volume_24hr": market["volume_24hr"],
                        }
                    )

        paths: list[dict[str, Any]] = []
        for episode in open_before:
            token = str(episode["token_id"])
            parsed = parsed_by_token.get(token)
            snapshot = snapshot_by_token.get(token)
            bid = (
                walk_bids(parsed.bids, float(episode["entry_shares"]))
                if parsed is not None and snapshot is not None
                else None
            )
            paths.append(
                {
                    "episode_path_observation_id": uuid4().hex,
                    "episode_id": episode["episode_id"],
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "token_id": token,
                    "notional_usdc": episode["notional_usdc"],
                    "book_snapshot_id": (
                        snapshot["book_snapshot_id"] if snapshot else None
                    ),
                    "observed_at": (
                        snapshot["observed_at"] if snapshot else iso_utc(current)
                    ),
                    "path_status": bid.status if bid else "BOOK_UNAVAILABLE",
                    "best_bid": (
                        parsed.bids[0].price if parsed and parsed.bids else None
                    ),
                    "requested_shares": float(episode["entry_shares"]),
                    "filled_shares": bid.filled if bid else 0.0,
                    "remaining_shares": (
                        bid.remaining if bid else float(episode["entry_shares"])
                    ),
                    "executable_bid_vwap": bid.vwap if bid else None,
                    "worst_bid": bid.worst_price if bid else None,
                    "levels_used": bid.levels_used if bid else 0,
                }
            )

        resolution_attempts: list[dict[str, Any]] = []
        resolutions: list[dict[str, Any]] = []
        latest_resolution = self.repository.latest_resolution_statuses()
        condition_to_cluster = {
            condition: str(context["event"]["event_cluster_id"])
            for condition, context in condition_contexts.items()
        }
        for episode in open_before:
            condition_to_cluster.setdefault(
                str(episode["condition_id"]), str(episode["event_cluster_id"])
            )
        resolution_conditions = {
            str(episode["condition_id"]) for episode in open_before
        }
        resolution_conditions.update(
            condition
            for condition, context in condition_contexts.items()
            if str(context["event"]["lifecycle_state"]) == "ENDED"
        )
        if census_healthy:
            due_conditions = tuple(
                condition_id
                for condition_id in sorted(resolution_conditions)
                if self.repository.resolution_due(
                    condition_id,
                    now=current,
                    interval_minutes=self.config.trading.research.resolution_retry_minutes,
                )
            )
            if isinstance(self.clob, ClobClientPool):
                resolution_results = self.clob.fetch_resolutions(
                    run_id, due_conditions, budget=budget
                )
            else:
                resolution_results = {
                    condition_id: self.clob.fetch_resolution(
                        run_id, condition_id, budget=budget
                    )
                    for condition_id in due_conditions
                }
            for condition_id in due_conditions:
                result = resolution_results[condition_id]
                if result.received_at:
                    receipts.append(result.received_at)
                cluster = condition_to_cluster[condition_id]
                resolution_attempts.append(
                    {
                        "resolution_attempt_id": uuid4().hex,
                        "cycle_id": cycle_id,
                        "run_id": run_id,
                        "condition_id": condition_id,
                        "event_cluster_id": cluster,
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
                    latest_resolution[condition_id] = result.status
                    resolutions.append(
                        {
                            "resolution_observation_id": uuid4().hex,
                            "cycle_id": cycle_id,
                            "run_id": run_id,
                            "condition_id": condition_id,
                            "event_cluster_id": cluster,
                            "observed_at": result.received_at or iso_utc(current),
                            "resolution_status": result.status,
                            "winner_indices_json": canonical_json(
                                list(result.winner_indices)
                            ),
                            "logical_request_id": result.request_id,
                            "raw_sha256": (
                                hashlib.sha256(result.raw).hexdigest()
                                if result.raw
                                else None
                            ),
                            "evidence_json": canonical_json(result.payload or {}),
                        }
                    )

        for cluster, condition_ids in conditions_by_game.items():
            material = context_by_cluster.get(cluster)
            if material is None or material.row["lifecycle_state"] != "ENDED":
                continue
            statuses = [latest_resolution.get(value) for value in condition_ids]
            terminal_statuses = {"RESOLVED", "VOID", "TIE"}
            if not statuses or any(status not in terminal_statuses for status in statuses):
                continue
            if all(status == "RESOLVED" for status in statuses):
                terminal = "RESOLVED"
            elif all(status == "VOID" for status in statuses):
                terminal = "VOID"
            elif any(status == "TIE" for status in statuses):
                terminal = "TIE"
            else:
                quality.append(
                    self._quality_row(
                        cycle_id,
                        run_id,
                        "HIGH",
                        "MIXED_GAME_TERMINAL_STATUS",
                        material.classification.family,
                        {"event_cluster_id": cluster, "statuses": statuses},
                    )
                )
                continue
            game_lifecycle.append(
                {
                    "game_lifecycle_observation_id": uuid4().hex,
                    "cycle_id": cycle_id,
                    "run_id": run_id,
                    "event_observation_id": material.row["event_observation_id"],
                    "sport_family": material.classification.family,
                    "event_id": material.row["event_id"],
                    "canonical_game_slug": material.row["canonical_game_slug"],
                    "game_id_alias": material.row["game_id_alias"],
                    "event_cluster_id": cluster,
                    "observed_at": iso_utc(current),
                    "source_kind": "CLOB_DERIVED",
                    "lifecycle_state": terminal,
                    "is_terminal": 1,
                    "phase_source": "ALL_GAME_CONDITIONS_TERMINAL",
                    "scheduled_start_field": material.row["scheduled_start_field"],
                    "scheduled_start_raw": material.row["scheduled_start_raw"],
                    "scheduled_start_utc": material.row["scheduled_start_utc"],
                    "logical_request_id": None,
                    "raw_lifecycle_json": "{}",
                    "evidence_json": canonical_json(
                        {
                            "condition_ids": sorted(condition_ids),
                            "condition_statuses": statuses,
                        }
                    ),
                }
            )

        skew = receipt_skew_seconds(receipts)
        if skew > self.config.trading.max_receipt_skew_seconds:
            quality.append(
                self._quality_row(
                    cycle_id,
                    run_id,
                    "CRITICAL",
                    "MAX_RECEIPT_SKEW_EXCEEDED",
                    None,
                    {
                        "observed_seconds": skew,
                        "limit_seconds": self.config.trading.max_receipt_skew_seconds,
                    },
                )
            )
            fatal_error = "source receipt skew exceeded the frozen 90-second boundary"
            episodes = []

        budget.assert_within_hard_deadline()
        lifecycle_counts = Counter(
            row["lifecycle_state"] for row in game_lifecycle
        )
        family_counts = Counter(
            row["sport_family"]
            for row in events
            if row["classification_status"] == "ACCEPTED"
        )
        summary = {
            "status": "FAILED_HEALTH_GATE" if fatal_error else "COLLECTED",
            "cycle_id": cycle_id,
            "families": {
                family: {
                    "source_events": next(
                        row["source_event_count"]
                        for row in sweeps
                        if row["sport_family"] == family
                    ),
                    "accepted_events": family_counts[family],
                    "cursor_complete": bool(
                        next(
                            row["cursor_complete"]
                            for row in sweeps
                            if row["sport_family"] == family
                        )
                    ),
                }
                for family in self.config.registry.by_code
            },
            "followup_complete": followup_complete,
            "unique_games": len(context_by_cluster),
            "lifecycle_states": dict(sorted(lifecycle_counts.items())),
            "schedule_revisions": len(schedule_revisions),
            "markets": len(markets),
            "eligible_outcomes": sum(
                int(row["threshold_eligible"]) for row in outcomes
            ),
            "book_snapshots": len(book_snapshots),
            "notional_threshold_vectors": len(threshold_vectors),
            "episodes_opened": len(episodes),
            "pregame_anchor_candidates": len(anchors),
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
            "started_at": iso_utc(current),
            "cooperative_deadline_at": iso_utc(
                current
                + timedelta(
                    seconds=self.config.trading.cooperative_budget_seconds
                )
            ),
            "request_stop_at": iso_utc(
                current
                + timedelta(
                    seconds=(
                        self.config.trading.cooperative_budget_seconds
                        - self.config.trading.stop_margin_seconds
                    )
                )
            ),
            "hard_deadline_at": iso_utc(
                current + timedelta(seconds=self.config.trading.hard_cycle_seconds)
            ),
            "completed_at": iso_utc(),
            "elapsed_seconds": budget.elapsed(),
            "receipt_skew_seconds": skew,
            "all_families_cursor_complete": int(all_complete),
            "followup_complete": int(followup_complete),
            "request_envelope_json": canonical_json(
                {
                    "families": {
                        item.code: {
                            "tag_id": item.tag_id,
                            "query_tag_ids": list(item.query_tag_ids),
                            "closed": False,
                            "include_children": False,
                            "related_tags": False,
                            "live": None,
                        }
                        for item in self.config.registry.families
                    },
                    "start_time_min": sweeps[0]["start_time_min"],
                    "start_time_max": sweeps[0]["start_time_max"],
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
                self._quality_row(
                    cycle_id,
                    run_id,
                    "WARN",
                    "STORAGE_USED_RATIO_WARNING",
                    None,
                    storage,
                )
            )
        bundle = {
            "cycle": cycle,
            "sweeps": sweeps,
            "raw_payloads": raw_payloads,
            "events": events,
            "game_lifecycle": game_lifecycle,
            "schedule_revisions": schedule_revisions,
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
            "anchors": anchors,
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
