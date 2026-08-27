from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot.analyzer import _query_tag_accounting, analyze_database
from polybot.api.clob_client import (
    BookAttempt,
    BookLevel,
    FeeObservation,
    ParsedBook,
    ResolutionObservation,
)
from polybot.api.gamma_client import EventFollowup, EventPage, EventSweep
from polybot.api.sports_client import ClockBatch, ClockUpdate
from polybot.api.transport import CycleBudget
from polybot.collector import Collector
from polybot.db.repository import ResearchRepository
from polybot.registry import FAMILY_ORDER
from polybot.run_audit import ResearchRunAudit


class FakeGamma:
    def __init__(self, events, received_at):
        self.events = events
        self.received_at = received_at
        self.incomplete_family = None

    def fetch_family_events(self, run_id, family, *, budget, slot_start):
        payload = {"events": [self.events[family.code]], "next_cursor": None}
        raw = json.dumps(payload, sort_keys=True).encode()
        page = EventPage(
            family=family.code,
            page_number=1,
            request_id=f"gamma-{run_id}-{family.code}",
            received_at=self.received_at,
            response_sha256="a" * 64,
            raw=raw,
            events=(self.events[family.code],),
            after_cursor=None,
            next_cursor="still-more" if family.code == self.incomplete_family else None,
        )
        return EventSweep(
            family.code,
            family.tag_id,
            (page,),
            family.code != self.incomplete_family,
            "still-more" if family.code == self.incomplete_family else None,
            "2026-08-26T00:00:00Z",
            "2026-08-29T00:00:00Z",
        )

    def fetch_event(self, run_id, event_id, family, *, budget):
        raise AssertionError("same-cycle discovery should suppress follow-up")


class FakeClob:
    def __init__(self, price, received_at):
        self.price = price
        self.received_at = received_at
        self.book_calls = 0

    def fetch_books(self, run_id, token_ids, *, budget):
        self.book_calls += 1
        result = {}
        for token in dict.fromkeys(token_ids):
            raw = {
                "asset_id": token,
                "bids": [{"price": f"{self.price - 0.01:.2f}", "size": "10000"}],
                "asks": [{"price": f"{self.price:.2f}", "size": "10000"}],
                "timestamp": "source-time",
                "tick_size": "0.01",
                "min_order_size": "1",
            }
            parsed = ParsedBook(
                token,
                (BookLevel(self.price - 0.01, 10000),),
                (BookLevel(self.price, 10000),),
                "source-time",
                0.01,
                1.0,
            )
            result[token] = BookAttempt(
                token, "OBSERVED", f"book-{run_id}-{token}", self.received_at,
                raw=raw, parsed=parsed
            )
        return result

    def fetch_fee(self, run_id, token_id, *, budget):
        return FeeObservation(token_id, "NOT_REQUESTED", None, None, None, None)

    def fetch_resolution(self, run_id, condition_id, *, budget):
        return ResolutionObservation(
            condition_id, "OPEN", f"resolution-{condition_id}", self.received_at,
            (), b'{"closed":false,"tokens":[{},{}]}',
            {"closed": False, "tokens": [{}, {}]},
        )


class FakeClock:
    def __init__(self, received_at):
        self.received_at = received_at

    def collect(self, run_id, targets, *, budget):
        updates = {
            slug: ClockUpdate(
                slug,
                target.event_cluster_id,
                self.received_at,
                {"slug": slug, "period": "2H", "elapsed": "55:00", "live": True},
                "CANONICAL_SLUG",
                slug,
                target.game_id_aliases[0] if target.game_id_aliases else None,
            )
            for slug, target in targets.items()
        }
        return ClockBatch(
            f"clock-{run_id}",
            "OBSERVED" if targets else "NO_TARGETS",
            self.received_at,
            self.received_at,
            len(targets),
            len(updates),
            len(updates),
            updates,
            (),
        )


class FakeGammaFollowup:
    def __init__(self, events, received_at):
        self.events = events
        self.received_at = received_at

    def fetch_family_events(self, run_id, family, *, budget, slot_start):
        raw = b'{"events":[],"next_cursor":null}'
        page = EventPage(
            family.code,
            1,
            f"gamma-empty-{run_id}-{family.code}",
            self.received_at,
            hashlib.sha256(raw).hexdigest(),
            raw,
            (),
            None,
            None,
        )
        return EventSweep(
            family.code,
            family.tag_id,
            (page,),
            True,
            None,
            "2026-08-26T00:00:00Z",
            "2026-08-29T00:00:00Z",
        )

    def fetch_event(self, run_id, event_id, family, *, budget):
        event = deepcopy(self.events[family])
        event.update(
            {
                "active": False,
                "closed": True,
                "live": False,
                "ended": True,
                "status": "resolved",
            }
        )
        raw = json.dumps(event, sort_keys=True).encode()
        return EventFollowup(
            str(event_id),
            f"gamma-followup-{run_id}-{family}",
            self.received_at,
            hashlib.sha256(raw).hexdigest(),
            raw,
            event,
        )


class FakeClockNoMessage:
    def __init__(self, received_at):
        self.received_at = received_at

    def collect(self, run_id, targets, *, budget):
        return ClockBatch(
            f"clock-empty-{run_id}",
            "NO_MATCH",
            self.received_at,
            self.received_at,
            len(targets),
            0,
            0,
            {},
            (),
        )


def fake_storage_metric(**kwargs):
    return {
        "storage_metric_id": "storage-" + str(kwargs["cycle_id"]),
        "cycle_id": kwargs["cycle_id"],
        "run_id": kwargs["run_id"],
        "phase": kwargs["phase"],
        "observed_at": "2026-08-27T00:00:00Z",
        "database_bytes": 1,
        "wal_bytes": 0,
        "filesystem_total_bytes": 1000,
        "filesystem_used_bytes": 100,
        "filesystem_free_bytes": 900,
        "filesystem_used_ratio": 0.1,
        "guard_state": "OK",
    }


def source_events(config, make_us_event, make_us_market, make_soccer_event, make_soccer_market):
    events = {}
    soccer = make_soccer_event()
    soccer["markets"] = [
        make_soccer_market("Home FC", 1),
        make_soccer_market("Draw", 2),
        make_soccer_market("Away FC", 3),
    ]
    soccer["startTime"] = "2026-08-27T12:00:00Z"
    events["soccer"] = soccer
    for family in ("mlb", "nba", "nfl", "nhl"):
        event = make_us_event(
            family, phase="PRESEASON" if family == "nfl" else None
        )
        event["markets"] = [make_us_market(family)]
        event["startTime"] = "2026-08-27T12:00:00Z"
        events[family] = event
    return events


def publish(repository, config, product, run_id):
    repository.publish_cycle(
        product.bundle,
        terminal_event=ResearchRunAudit(config, run_id).event_row("SUCCEEDED", product.summary),
    )


def test_five_family_collection_crossing_and_analyzer(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )

    first_time = "2026-08-27T00:00:00Z"
    first = Collector(
        config,
        repository,
        FakeGamma(events, first_time),
        FakeClob(0.80, first_time),
        FakeClock(first_time),
    ).collect(
        "run-1",
        slot_start=first_time,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc),
    )
    assert first.fatal_error is None
    assert len(first.bundle["sweeps"]) == 5
    assert first.bundle["episodes"] == []
    assert all(
        "LEFT_CENSORED" in row["states_json"]
        for row in first.bundle["threshold_vectors"]
    )
    publish(repository, config, first, "run-1")

    second_time = "2026-08-27T00:05:00Z"
    second = Collector(
        config,
        repository,
        FakeGamma(events, second_time),
        FakeClob(0.82, second_time),
        FakeClock(second_time),
    ).collect(
        "run-2",
        slot_start=second_time,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, 0, 5, tzinfo=timezone.utc),
    )
    assert second.fatal_error is None
    assert second.bundle["episodes"]
    assert {row["threshold"] for row in second.bundle["episodes"]} == {0.81, 0.82}
    publish(repository, config, second, "run-2")

    analysis = analyze_database(repository.path)
    assert analysis["analyzer_contract"] == "major-sports-lifecycle-health-v5"
    assert analysis["cycle_selection"]["selected_cycles"] == 2
    assert analysis["sport_coverage"]["missing_sports"] == []
    assert analysis["sport_coverage"]["sport_equal_macro_public_book_coverage_pct"] == 100
    assert analysis["sport_coverage"]["lifecycle_states_are_never_pooled"] is True
    assert analysis["schedule_window_accounting"]["gate_passed"] is True
    assert analysis["schedule_window_accounting"]["accounted_observations"] == 10
    assert analysis["health"]["gate_checks"]["schedule_window_accounting"] is True
    assert analysis["season_phase_contract"]["phases_are_never_pooled"] is True
    assert analysis["sport_coverage"]["by_sport"]["nfl"]["by_season_phase"]["PRESEASON"]
    assert analysis["event_clustering"]["soccer_clusters_missing_home_draw_away"] == 0
    assert analysis["notional_evidence_completeness"]["frozen_ladder_usdc"][-1] == 1000
    assert (
        analysis["notional_evidence_completeness"][
            "threshold_vector_complete_pct"
        ]
        == 100
    )
    assert "5" in analysis["threshold_state_strata_by_notional"]["soccer"][
        "NOT_APPLICABLE"
    ]
    assert "1000" in analysis["threshold_state_strata_by_notional"]["soccer"][
        "NOT_APPLICABLE"
    ]
    assert analysis["profitability_conclusion"] is None


def test_incomplete_family_cursor_is_fatal_and_never_calls_books(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )
    gamma = FakeGamma(events, "2026-08-27T00:00:00Z")
    gamma.incomplete_family = "nhl"
    clob = FakeClob(0.80, "2026-08-27T00:00:00Z")
    product = Collector(
        config, repository, gamma, clob, FakeClock("2026-08-27T00:00:00Z")
    ).collect(
        "run",
        slot_start="2026-08-27T00:00:00Z",
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert product.fatal_error is not None
    assert product.bundle["episodes"] == []
    assert clob.book_calls == 0
    repository.publish_cycle(
        product.bundle,
        terminal_event=ResearchRunAudit(config, "run").event_row(
            "FAILED", {"error_message": product.fatal_error}
        ),
    )
    analysis = analyze_database(repository.path)
    assert analysis["cycle_selection"]["selected_cycles"] == 0
    assert analysis["cycle_selection"]["exclusion_reasons"] == {
        "NOT_UNIQUELY_SUCCEEDED": 1
    }


def test_missing_sports_produce_null_macro(config):
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    result = analyze_database(repository.path)
    assert result["sport_coverage"]["missing_sports"] == list(config.registry.by_code)
    assert result["sport_coverage"]["sport_equal_macro_public_book_coverage_pct"] is None


def test_discovered_games_are_followed_to_explicit_terminal_state(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )
    first_time = "2026-08-27T00:00:00Z"
    first = Collector(
        config,
        repository,
        FakeGamma(events, first_time),
        FakeClob(0.80, first_time),
        FakeClock(first_time),
    ).collect(
        "run-lifecycle-1",
        slot_start=first_time,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc),
    )
    publish(repository, config, first, "run-lifecycle-1")

    second_time = "2026-08-27T00:05:00Z"
    second = Collector(
        config,
        repository,
        FakeGammaFollowup(events, second_time),
        FakeClob(0.81, second_time),
        FakeClockNoMessage(second_time),
    ).collect(
        "run-lifecycle-2",
        slot_start=second_time,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, 0, 5, tzinfo=timezone.utc),
    )
    assert second.fatal_error is None
    assert second.summary["followup_complete"] is True
    assert len(
        [
            row
            for row in second.bundle["events"]
            if row["source_kind"] == "FOLLOWUP"
        ]
    ) == len(FAMILY_ORDER)
    assert {
        row["lifecycle_state"]
        for row in second.bundle["game_lifecycle"]
        if row["source_kind"] == "GAMMA_FOLLOWUP"
    } == {"RESOLVED"}
    publish(repository, config, second, "run-lifecycle-2")

    analysis = analyze_database(repository.path)
    assert analysis["lifecycle_health"]["followup_event_observations"] == len(
        FAMILY_ORDER
    )
    assert analysis["lifecycle_health"]["explicit_terminal_unique_games"] == len(
        FAMILY_ORDER
    )
    assert (
        analysis["lifecycle_health"]["terminal_coverage_for_ended_games_pct"]
        == 100
    )


@pytest.mark.parametrize(
    ("scheduled_start", "expected_reason"),
    [
        (None, "DISCOVERY_SCHEDULE_MISSING"),
        ("not-a-timestamp", "DISCOVERY_SCHEDULE_INVALID"),
        ("2026-08-29T00:00:00Z", "DISCOVERY_SCHEDULE_OUTSIDE_WINDOW"),
    ],
    ids=("missing", "invalid", "upper-bound-exclusive"),
)
def test_new_discovery_schedule_is_revalidated_and_raw_evidence_is_preserved(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
    scheduled_start,
    expected_reason,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )
    if scheduled_start is None:
        events["nba"].pop("startTime")
    else:
        events["nba"]["startTime"] = scheduled_start

    observed_at = "2026-08-27T00:00:00Z"
    product = Collector(
        config,
        repository,
        FakeGamma(events, observed_at),
        FakeClob(0.80, observed_at),
        FakeClockNoMessage(observed_at),
    ).collect(
        f"run-schedule-{expected_reason}",
        slot_start=observed_at,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    assert product.fatal_error is None
    event = next(
        row for row in product.bundle["events"] if row["sport_family"] == "nba"
    )
    assert event["classification_status"] == "REJECTED"
    assert expected_reason in event["classification_reason"].split(";")
    assert not any(
        row["sport_family"] == "nba" for row in product.bundle["outcomes"]
    )

    raw_payload = next(
        row
        for row in product.bundle["raw_payloads"]
        if row["raw_payload_id"] == event["raw_payload_id"]
    )
    decoded = json.loads(gzip.decompress(raw_payload["payload_gzip"]))
    assert decoded["events"][0]["id"] == events["nba"]["id"]
    if scheduled_start is not None:
        assert decoded["events"][0]["startTime"] == scheduled_start

    run_id = f"run-schedule-{expected_reason}"
    publish(repository, config, product, run_id)
    analysis = analyze_database(repository.path)
    family = analysis["schedule_window_accounting"]["by_sport"]["nba"]
    assert family["schedule_rejection_reasons"][expected_reason] == 1
    assert analysis["schedule_window_accounting"]["gate_passed"] is True


def test_tracked_event_reappearing_outside_window_keeps_followup_tracking(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )
    first_time = "2026-08-27T00:00:00Z"
    first = Collector(
        config,
        repository,
        FakeGamma(events, first_time),
        FakeClob(0.80, first_time),
        FakeClockNoMessage(first_time),
    ).collect(
        "run-tracked-window-1",
        slot_start=first_time,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    publish(repository, config, first, "run-tracked-window-1")

    events["nba"]["startTime"] = "2026-09-01T00:00:00Z"
    second_time = "2026-08-27T00:05:00Z"
    second = Collector(
        config,
        repository,
        FakeGamma(events, second_time),
        FakeClob(0.81, second_time),
        FakeClockNoMessage(second_time),
    ).collect(
        "run-tracked-window-2",
        slot_start=second_time,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, 0, 5, tzinfo=timezone.utc),
    )

    event = next(
        row for row in second.bundle["events"] if row["sport_family"] == "nba"
    )
    validation = json.loads(event["classification_evidence_json"])[
        "discovery_window_validation"
    ]
    assert event["classification_status"] == "ACCEPTED"
    assert validation["status"] == "OUTSIDE_WINDOW"
    assert validation["tracked_event"] is True
    assert any(
        row["sport_family"] == "nba"
        and row["source_kind"] == "GAMMA_DISCOVERY"
        for row in second.bundle["game_lifecycle"]
    )


def test_discovered_open_collects_books_vectors_and_future_prestart_anchor(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )
    events["nba"].pop("live")
    events["nba"].pop("ended")
    events["nba"]["startTime"] = "2026-08-27T01:00:00Z"
    observed_at = "2026-08-27T00:00:00Z"

    product = Collector(
        config,
        repository,
        FakeGamma(events, observed_at),
        FakeClob(0.80, observed_at),
        FakeClockNoMessage(observed_at),
    ).collect(
        "run-discovered-open",
        slot_start=observed_at,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    nba_event = next(
        row for row in product.bundle["events"] if row["sport_family"] == "nba"
    )
    nba_tokens = {"nba-token-a", "nba-token-b"}
    assert nba_event["classification_status"] == "ACCEPTED"
    assert nba_event["lifecycle_state"] == "DISCOVERED_OPEN"
    assert {
        row["lifecycle_state"]
        for row in product.bundle["markets"]
        if row["sport_family"] == "nba"
    } == {"DISCOVERED_OPEN"}
    assert {
        row["token_id"]
        for row in product.bundle["book_snapshots"]
        if row["token_id"] in nba_tokens
    } == nba_tokens
    assert sum(
        row["token_id"] in nba_tokens for row in product.bundle["book_ladder"]
    ) == len(nba_tokens) * len(
        config.trading.research.executable_notional_ladder_usdc
    )
    assert sum(
        row["token_id"] in nba_tokens for row in product.bundle["threshold_vectors"]
    ) == len(nba_tokens) * len(
        config.trading.research.executable_notional_ladder_usdc
    )
    assert {
        row["lifecycle_state"]
        for row in product.bundle["threshold_vectors"]
        if row["token_id"] in nba_tokens
    } == {"DISCOVERED_OPEN"}
    assert {
        row["token_id"] for row in product.bundle["anchors"]
    } == nba_tokens
    assert all(
        row["minutes_to_scheduled_start"] == 60
        and row["anchor_role"] == "PRESTART_CANDIDATE"
        for row in product.bundle["anchors"]
    )
    publish(repository, config, product, "run-discovered-open")
    analysis = analyze_database(repository.path)
    nba = analysis["sport_coverage"]["by_sport"]["nba"]["by_lifecycle_state"]
    assert nba["DISCOVERED_OPEN"]["public_book_coverage_pct"] == 100
    assert nba["PREGAME"]["public_book_coverage_pct"] is None
    assert analysis["schedule_anchor_health"]["lifecycle_strata_are_never_pooled"] is True


def test_discovery_lower_time_bound_is_inclusive(
    config,
    make_us_event,
    make_us_market,
    make_soccer_event,
    make_soccer_market,
    monkeypatch,
):
    monkeypatch.setattr("polybot.collector.storage_metric_row", fake_storage_metric)
    repository = ResearchRepository(config, database_utc_date="2026-08-27")
    repository.register_config()
    events = source_events(
        config, make_us_event, make_us_market, make_soccer_event, make_soccer_market
    )
    events["nba"]["startTime"] = "2026-08-26T00:00:00Z"
    observed_at = "2026-08-27T00:00:00Z"
    product = Collector(
        config,
        repository,
        FakeGamma(events, observed_at),
        FakeClob(0.80, observed_at),
        FakeClockNoMessage(observed_at),
    ).collect(
        "run-lower-inclusive",
        slot_start=observed_at,
        budget=CycleBudget(0, monotonic=lambda: 0),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    event = next(
        row for row in product.bundle["events"] if row["sport_family"] == "nba"
    )
    evidence = json.loads(event["classification_evidence_json"])
    assert event["classification_status"] == "ACCEPTED"
    assert evidence["discovery_window_validation"]["status"] == "WITHIN_WINDOW"


@pytest.mark.parametrize("version", [2, 3, 4])
def test_analyzer_rejects_historical_schema(tmp_path, version):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "trades_sim.db"
    migration = (
        root
        / "src/polybot/db/migrations"
        / f"000{version}_major_sports_lifecycle_v{version}.sql"
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="schema epoch must be v5"):
        analyze_database(database)


def test_query_tag_accounting_requires_exact_family_tag_sets(config):
    cycles = [{"run_id": "run-1"}]
    sweeps = []
    requests = []
    for family in config.registry.families:
        sweeps.append(
            {
                "run_id": "run-1",
                "sport_family": family.code,
                "request_envelope_json": json.dumps(
                    {"query_tag_ids": list(family.query_tag_ids)}
                ),
            }
        )
        for tag_id in family.query_tag_ids:
            requests.append(
                {
                    "run_id": "run-1",
                    "sport_family": family.code,
                    "request_kind": "gamma_events_keyset",
                    "status": "SUCCESS",
                    "params_json": json.dumps({"tag_id": tag_id}),
                }
            )
    result = _query_tag_accounting(cycles, sweeps, requests)
    assert result["gate_passed"] is True
    assert result["by_sport"]["soccer"]["expected_query_tag_ids"] == [
        306,
        780,
        1494,
        100100,
        100977,
        101787,
        101962,
        102070,
    ]

    requests.pop()
    result = _query_tag_accounting(cycles, sweeps, requests)
    assert result["gate_passed"] is False
    assert result["violations"] == {"QUERY_TAG_SET_MISMATCH": 1}
