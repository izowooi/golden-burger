"""Independent regressions for raw collection isolation and point-in-time facts.

No API in this module can submit an order or contact a network service.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from polybot.api.clob_client import BookAttempt, BookCollection, RawPayload, ResolutionResult
from polybot.api.gamma_client import EventPage, EventSweep
from polybot.collector import Collector
from polybot.utils.retry import CycleBudget, canonical_json, iso_utc
from test_collector import NOW, FakeSportsClock, configured, event, market, repository_for


def _soccer_event():
    source = event()
    source.update(elapsed="76:00", period="2H")
    source["teams"][0]["ordering"] = "home"
    source["teams"][1]["ordering"] = "away"
    source["markets"] = []
    for suffix, title, question in (
        ("a", "Team A", "Will Team A win?"),
        ("draw", "Draw (Team A vs. Team B)", "Will Team A vs. Team B end in a draw?"),
        ("b", "Team B", "Will Team B win?"),
    ):
        item = market(
            id=f"market-{suffix}", conditionId=f"condition-{suffix}",
            groupItemTitle=title, question=question,
            clobTokenIds=json.dumps([f"team-{suffix}", f"team-{suffix}-no"]),
            feeSchedule={"rate": 0.05, "exponent": 1, "takerOnly": True},
        )
        item.pop("events")
        source["markets"].append(item)
    return source


def _request_receipt(repository, *, run_id, request_id, kind, raw, started, received):
    repository.record_api_request({
        "request_id": request_id, "run_id": run_id, "request_kind": kind,
        "page_number": None, "attempt_number": 1, "method": "GET",
        "url": "https://public.example.invalid/fixture", "params_json": "{}",
        "body_sha256": None, "started_at": iso_utc(started), "completed_at": iso_utc(received),
        "elapsed_ms": (received - started).total_seconds() * 1000,
        "status": "SUCCESS", "http_status": 200,
        "response_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes": len(raw),
        "error_type": None, "error_message": None,
    })


class TraceGamma:
    def __init__(self, trace, *, source=None, discover=True, now=NOW, repository=None):
        self.trace, self.source = trace, source or _soccer_event()
        self.discover, self.now = discover, now
        self.repository = repository

    def fetch_live_events(self, run_id, *, observed_at):
        self.trace.append((run_id, "core_sweep"))
        events = (deepcopy(self.source),) if self.discover else ()
        raw = canonical_json({"events": list(events), "next_cursor": None}).encode()
        if self.repository:
            _request_receipt(self.repository, run_id=run_id, request_id=f"sweep-{run_id}",
                             kind="gamma_live_events_keyset:soccer", raw=raw,
                             started=observed_at, received=observed_at)
        page = EventPage(1, f"sweep-{run_id}", iso_utc(observed_at),
                         hashlib.sha256(raw).hexdigest(), raw, events, None, None)
        return EventSweep((page,), True)

    def fetch_event_by_id(self, run_id, event_id):
        self.trace.append((run_id, "raw_event", event_id))
        raw = canonical_json([self.source]).encode()
        request_id = f"followup-{run_id}-{event_id}"
        if self.repository:
            _request_receipt(self.repository, run_id=run_id, request_id=request_id,
                             kind="gamma_raw_event_followup", raw=raw,
                             started=self.now + timedelta(seconds=3.5), received=self.now + timedelta(seconds=4))
        return SimpleNamespace(
            status="OBSERVED", event=deepcopy(self.source),
            request_id=request_id,
            received_at=iso_utc(self.now + timedelta(seconds=4)),
            response_sha256=hashlib.sha256(raw).hexdigest(), raw=raw,
            error_type=None, error_message=None,
        )


class TraceClob:
    def __init__(self, trace, *, now=NOW, ask=0.97, bid_levels=None, terminal=False, repository=None):
        self.trace, self.now, self.ask = trace, now, ask
        self.bid_levels = bid_levels or [{"price": "0.96", "size": "20"}]
        self.terminal = terminal
        self.book_calls = 0
        self.repository = repository

    def fetch_books(self, run_id, token_ids):
        self.book_calls += 1
        tokens = list(dict.fromkeys(token_ids))
        self.trace.append((run_id, "books", tuple(tokens)))
        if not tokens:
            return BookCollection({}, {}, ())
        received = iso_utc(self.now + timedelta(seconds=2 if self.book_calls == 1 else 6))
        request_id = f"books-{run_id}-{self.book_calls}"
        books = {}
        for token in tokens:
            books[token] = {
                "asset_id": token,
                "market": {"team-a": "condition-a", "team-b": "condition-b", "team-draw": "condition-draw"}.get(token),
                "bids": deepcopy(self.bid_levels) if token == "team-a" else [{"price": "0.19", "size": "40"}],
                "asks": [{"price": str(self.ask if token == "team-a" else 0.20), "size": "40"}],
                "timestamp": str(int((self.now + timedelta(seconds=2)).timestamp() * 1000)),
            }
        raw = canonical_json(list(books.values())).encode()
        if self.repository:
            _request_receipt(self.repository, run_id=run_id, request_id=request_id,
                             kind="clob_books", raw=raw,
                             started=self.now + timedelta(seconds=1 if self.book_calls == 1 else 5),
                             received=self.now + timedelta(seconds=2 if self.book_calls == 1 else 6))
        attempts = {token: BookAttempt(token, "OBSERVED", request_id, received) for token in tokens}
        return BookCollection(books, attempts, (RawPayload(request_id, received, hashlib.sha256(raw).hexdigest(), raw),))

    def fetch_resolution(self, run_id, condition_id):
        self.trace.append((run_id, "core_resolution", condition_id))
        token = {"condition-a": "team-a", "condition-b": "team-b", "condition-draw": "team-draw"}[condition_id]
        payload = {"closed": self.terminal, "tokens": [
            {"token_id": token, "outcome": "Yes", "winner": self.terminal},
            {"token_id": token + "-no", "outcome": "No", "winner": False},
        ]}
        raw = canonical_json(payload).encode()
        received = iso_utc(self.now + timedelta(seconds=3))
        request_id = f"resolution-{run_id}-{condition_id}"
        return ResolutionResult(condition_id, "RESOLVED" if self.terminal else "OPEN", received,
                                request_id, 0 if self.terminal else None, payload,
                                RawPayload(request_id, received, hashlib.sha256(raw).hexdigest(), raw))


def _economic_state(repository):
    """Compare all economic columns and semantic links, never random UUID bytes."""
    tables = (
        "signal_decisions", "hypothetical_episodes", "counterfactual_exit_policies",
        "episode_path_observations", "stop_execution_attempts", "counterfactual_stop_exits",
        "resolution_attempts", "resolution_observations",
    )
    generated = {"decision_id", "market_observation_id", "event_observation_id", "snapshot_id",
                 "episode_id", "policy_id", "path_id", "attempt_id", "exit_id",
                 "completed_attempt_id", "resolution_id"}
    with repository.connect() as connection:
        episode_keys = {r["episode_id"]: (r["condition_id"], r["token_id"], r["threshold"])
                        for r in connection.execute("SELECT * FROM hypothetical_episodes")}
        policy_keys = {r["policy_id"]: (*episode_keys[r["episode_id"]], r["policy_key"])
                       for r in connection.execute("SELECT * FROM counterfactual_exit_policies")}
        result = {}
        for table in tables:
            rows = []
            for value in connection.execute(f"SELECT * FROM {table}"):
                row = dict(value)
                normalized = {k: v for k, v in row.items() if k not in generated}
                if row.get("episode_id"):
                    normalized["episode_key"] = episode_keys[row["episode_id"]]
                if row.get("policy_id"):
                    normalized["policy_key_semantic"] = policy_keys[row["policy_id"]]
                if "snapshot_id" in row:
                    normalized["has_snapshot"] = row["snapshot_id"] is not None
                rows.append(normalized)
            result[table] = sorted(rows, key=canonical_json)
    return result


def _run_cycle(config, repository, trace, *, run_id, now, enabled, discover=True, ask=0.97,
               bid_levels=None, terminal=False, budget=None):
    gamma = TraceGamma(trace, discover=discover, now=now, repository=repository)
    clob = TraceClob(trace, now=now, ask=ask, bid_levels=bid_levels, terminal=terminal, repository=repository)
    worker = Collector(config, repository, gamma, clob, FakeSportsClock(),
                       research_raw_enabled=enabled)
    result = worker.collect(run_id, now=now, budget=budget)
    return result, gamma, clob


def test_raw_followup_preserves_legacy_entry_partial_stop_and_resolution(tmp_path):
    states, traces, snapshots = {}, {}, {}
    for enabled in (False, True):
        config = configured(tmp_path / str(enabled), compact_grid=True)
        repository = repository_for(config)
        trace = []
        try:
            for index, args in enumerate((
                {"ask": 0.94},
                {"ask": 0.97},
                {"discover": False, "bid_levels": [{"price": "0.79", "size": "2"}]},
                {"discover": False, "bid_levels": [{"price": "0.77", "size": "20"}]},
                {"discover": False, "terminal": True},
            )):
                now = NOW + timedelta(minutes=(0, 1, 2, 3, 32)[index])
                _run_cycle(config, repository, trace, run_id=f"run-{index}", now=now,
                           enabled=enabled, **args)
                if index == 1:
                    with repository.connect() as c:
                        assert c.execute("SELECT count(*) FROM hypothetical_episodes").fetchone()[0] == 1
                        assert c.execute("SELECT count(*) FROM episode_path_observations").fetchone()[0] == 0
            states[enabled] = _economic_state(repository)
            traces[enabled] = trace
            with repository.connect() as c:
                snapshots[enabled] = [tuple(r) for r in c.execute(
                    "SELECT run_id,token_id,best_bid,best_ask FROM orderbook_snapshots ORDER BY run_id,token_id")]
            assert len(states[enabled]["stop_execution_attempts"]) == 2
            assert len(states[enabled]["counterfactual_stop_exits"]) == 1
            assert len(states[enabled]["resolution_observations"]) == 1
        finally:
            repository.close()
    assert states[True] == states[False]
    assert snapshots[True] == snapshots[False]
    # The old episode follows only its original token once discovery stops.
    for run_id in ("run-2", "run-3", "run-4"):
        calls = [row for row in traces[True] if row[0] == run_id]
        first_book = next(i for i, row in enumerate(calls) if row[1] == "books")
        assert calls[first_book][2] == ("team-a",)
        extra = [i for i, row in enumerate(calls) if row[1] == "raw_event"]
        if extra:
            assert first_book < min(extra)
            resolutions = [i for i, row in enumerate(calls) if row[1] == "core_resolution"]
            assert not resolutions or max(resolutions) < min(extra)


def _sidecar_rows(config, table, run_id=None):
    from polybot.db.raw_repository import RAW_SIDECAR_FILENAME
    with sqlite3.connect(config.db_path.with_name(RAW_SIDECAR_FILENAME)) as c:
        c.row_factory = sqlite3.Row
        if run_id is None:
            return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} WHERE run_id=?", (run_id,))]


def test_accepted_event_without_any_entry_is_followed_with_all_soccer_yes_tokens(tmp_path):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="seed", now=NOW, enabled=True, ask=0.40)
        assert repository.open_episodes() == []
        first_books = _sidecar_rows(config, "raw_books", "seed")
        assert len(first_books) == 3
        assert all(r["point_in_time_identity_valid"] == 1 for r in first_books)
        assert {r["source_kind"] for r in first_books} == {"PARENT_BOOK"}
        tracked = _sidecar_rows(config, "raw_tracked_events")
        assert len(tracked) == 1 and tracked[0]["state"] == "TRACKING"
        _run_cycle(config, repository, trace, run_id="absent", now=NOW + timedelta(minutes=1),
                   enabled=True, discover=False, ask=0.40)
        assert repository.open_episodes() == []
        books = _sidecar_rows(config, "raw_books", "absent")
        assert {r["token_id"] for r in books} == {"team-a", "team-b", "team-draw"}
        assert len(books) == 3 and {r["status"] for r in books} == {"FULL"}
        assert all(r["point_in_time_identity_valid"] == 1 for r in books)
        assert not any(r["token_id"].endswith("-no") for r in books)
        assert all(r["source_kind"] == "RAW_BOOK" for r in books)
        followup = next(i for i, call in enumerate(trace) if call[:2] == ("absent", "raw_event"))
        fresh = [i for i, call in enumerate(trace) if call[0] == "absent" and call[1] == "books" and call[2]]
        assert fresh and followup < min(fresh)
    finally:
        repository.close()


def test_metadata_received_after_legacy_book_requires_a_new_raw_book(tmp_path):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="entry", now=NOW, enabled=True)
        assert len(repository.open_episodes()) == 1
        _run_cycle(config, repository, trace, run_id="absent", now=NOW + timedelta(minutes=1),
                   enabled=True, discover=False)
        books = _sidecar_rows(config, "raw_books", "absent")
        assert len(books) == 3
        assert all(r["point_in_time_identity_valid"] == 1 for r in books)
        assert {r["source_kind"] for r in books} == {"RAW_BOOK"}
        assert {r["token_id"] for r in books} == {"team-a", "team-b", "team-draw"}
        parent_book = None
        with repository.connect() as c:
            parent_book = dict(c.execute("SELECT * FROM orderbook_snapshots WHERE run_id='absent'").fetchone())
            assert c.execute("SELECT count(*) FROM orderbook_snapshots WHERE run_id='absent'").fetchone()[0] == 1
        raw_favorite = next(r for r in books if r["token_id"] == "team-a")
        assert parent_book["token_id"] == "team-a"
        assert parent_book["request_id"] != raw_favorite["request_id"]
        assert parent_book["observed_at"] < raw_favorite["requested_at"]
    finally:
        repository.close()


def _raw_cycle(run_id="one", component_run_id="component-one"):
    from polybot.db.raw_repository import RAW_CONTRACT
    return {
        "run_id": run_id, "component_run_id": component_run_id,
        "contract": RAW_CONTRACT, "parent_filename": "trades_sim.db",
        "config_hash": "config-one", "source_digest": "source-one",
        "job_name": "watermelon-white-1m-v4b", "reference_at": iso_utc(NOW),
        "published_at": iso_utc(NOW + timedelta(seconds=10)), "status": "COMPLETE",
        "expected_events": 0, "expected_tokens": 0, "stats_json": "{}",
    }


@pytest.mark.parametrize("conflict", ["primary_run", "unique_component"])
def test_raw_cycle_replace_cannot_delete_another_published_run(tmp_path, conflict):
    from polybot.db.raw_repository import RawRepository
    raw = RawRepository(tmp_path / "trades_sim.db")
    try:
        with raw.transaction() as c:
            raw.insert(c, "raw_cycles", _raw_cycle())
        original = dict(raw.connection.execute("SELECT * FROM raw_cycles").fetchone())
        candidate = _raw_cycle(
            "one" if conflict == "primary_run" else "other-run",
            "different-component" if conflict == "primary_run" else "component-one",
        )
        with pytest.raises(sqlite3.IntegrityError):
            with raw.transaction() as c:
                c.execute("PRAGMA recursive_triggers=OFF")
                columns = list(candidate)
                c.execute(f"INSERT OR REPLACE INTO raw_cycles ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                          tuple(candidate[k] for k in columns))
        assert dict(raw.connection.execute("SELECT * FROM raw_cycles").fetchone()) == original
        assert raw.connection.execute("SELECT count(*) FROM raw_cycles").fetchone()[0] == 1
    finally:
        raw.close()


def test_registry_and_raw_publication_rollback_together(tmp_path):
    from polybot.db.raw_repository import RawRepository
    raw = RawRepository(tmp_path / "trades_sim.db")
    try:
        with pytest.raises(RuntimeError, match="publication interruption"):
            with raw.transaction() as c:
                raw.insert(c, "raw_tracked_events", {
                    "event_id": "1001", "family": "soccer", "state": "TRACKING",
                    "slots_json": "[]", "first_run_id": "one", "last_run_id": "one",
                    "next_attempt_at": iso_utc(NOW), "missing_count": 0, "terminal_reason": None,
                })
                raw.insert(c, "raw_cycles", _raw_cycle())
                raise RuntimeError("publication interruption")
        assert raw.connection.execute("SELECT count(*) FROM raw_tracked_events").fetchone()[0] == 0
        assert raw.published("one") is None
    finally:
        raw.close()


def test_raw_sidecar_never_migrates_the_parent_schema(tmp_path):
    from polybot.db.raw_repository import RawRepository
    config = configured(tmp_path)
    parent = repository_for(config)
    parent.close()
    before = hashlib.sha256(config.db_path.read_bytes()).hexdigest()
    raw = RawRepository(config.db_path)
    try:
        with raw.transaction() as c:
            raw.insert(c, "raw_cycles", _raw_cycle())
    finally:
        raw.close()
    assert hashlib.sha256(config.db_path.read_bytes()).hexdigest() == before
    reopened = repository_for(config)
    try:
        with reopened.connect() as c:
            assert c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name LIKE 'raw_%'").fetchone()[0] == 1  # legacy raw_payloads only
    finally:
        reopened.close()


@pytest.mark.parametrize("family", ["mlb", "nba", "nfl", "nhl"])
def test_raw_direct_sport_identity_requires_one_condition_two_team_tokens(tmp_path, family):
    from polybot.research_raw import event_identity
    from test_league_classifier import direct_event
    config = configured(tmp_path)
    source = direct_event(family)
    source["markets"] = [{"sportsMarketType": "moneyline", "negRisk": False,
        "conditionId": "direct-condition", "outcomes": ["A", "B"],
        "clobTokenIds": ["a", "b"], "outcomePrices": [0.6, 0.4], "question": "A vs B"}]
    slots, valid = event_identity(source, family, config.trading.gamma)
    assert valid and len(slots) == 2
    assert {s["token_id"] for s in slots} == {"a", "b"}
    assert {s["condition_id"] for s in slots} == {"direct-condition"}
    duplicate = deepcopy(source)
    duplicate["markets"].append({**duplicate["markets"][0], "conditionId": "other-condition"})
    assert event_identity(duplicate, family, config.trading.gamma) == ([], False)


@pytest.mark.parametrize("elapsed", [42.0, 49.0])
def test_raw_budget_skip_keeps_expected_tokens_and_does_no_optional_http(tmp_path, elapsed):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="seed", now=NOW, enabled=True, ask=0.40)
        trace.clear()
        budget = CycleBudget(0.0, network_seconds=42, cycle_seconds=50, monotonic=lambda: elapsed)
        with pytest.raises(RuntimeError, match="incomplete"):
            _run_cycle(config, repository, trace, run_id="cutoff", now=NOW + timedelta(minutes=1),
                       enabled=True, discover=False, budget=budget)
        assert budget.incomplete_reasons
        assert not any(row[1] == "raw_event" for row in trace)
        assert not any(row[1] == "books" and row[2] for row in trace)
        cycles = _sidecar_rows(config, "raw_cycles", "cutoff")
        assert len(cycles) == 1 and cycles[0]["status"] == "FAILED"
        assert cycles[0]["expected_tokens"] == 3
        books = _sidecar_rows(config, "raw_books", "cutoff")
        assert {r["token_id"] for r in books} == {"team-a", "team-b", "team-draw"}
        assert all(r["point_in_time_identity_valid"] == 0 for r in books)
        assert _sidecar_rows(config, "raw_events", "cutoff")[0]["metadata_status"] == "BUDGET_DEFERRED"
        assert repository.open_episodes() == []
    finally:
        repository.close()


def test_raw_lookup_error_publishes_failure_without_losing_pending_event(tmp_path):
    class FailingGamma(TraceGamma):
        def fetch_event_by_id(self, run_id, event_id):
            self.trace.append((run_id, "raw_event", event_id))
            return SimpleNamespace(status="ERROR", event=None, request_id=None,
                                   received_at=None, response_sha256=None, raw=None,
                                   error_type="ReadTimeout")
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="seed", now=NOW, enabled=True, ask=0.40)
        now = NOW + timedelta(minutes=1)
        gamma, clob = FailingGamma(trace, discover=False, now=now, repository=repository), TraceClob(trace, now=now, repository=repository)
        with pytest.raises(RuntimeError, match="incomplete"):
            Collector(config, repository, gamma, clob, FakeSportsClock()).collect("error", now=now)
        assert _sidecar_rows(config, "raw_cycles", "error")[0]["status"] == "FAILED"
        assert _sidecar_rows(config, "raw_events", "error")[0]["metadata_status"] == "ERROR"
        pending = _sidecar_rows(config, "raw_tracked_events")
        assert len(pending) == 1 and pending[0]["state"] == "TRACKING"
        assert pending[0]["missing_count"] == 1
        assert len(_sidecar_rows(config, "raw_books", "error")) == 3
        assert repository.open_episodes() == []
    finally:
        repository.close()


def test_closed_unresolved_event_waits_without_becoming_a_resolution(tmp_path):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="seed", now=NOW, enabled=True, ask=0.40)
        source = _soccer_event()
        source.update(closed=True, active=False, ended=True, live=False, gameStatus="FT", elapsed="90:00")
        for item in source["markets"]:
            item.update(closed=True, active=False, acceptingOrders=False,
                        outcomePrices=[0.5, 0.5])  # no authoritative void confirmation
        now = NOW + timedelta(minutes=1)
        gamma = TraceGamma(trace, source=source, discover=False, now=now, repository=repository)
        clob = TraceClob(trace, now=now, repository=repository)
        Collector(config, repository, gamma, clob, FakeSportsClock()).collect("ended", now=now)
        observed = _sidecar_rows(config, "raw_events", "ended")[0]
        assert observed["lifecycle_state"] == "WAIT_RESOLUTION"
        assert observed["terminal_json"] is None
        pending = _sidecar_rows(config, "raw_tracked_events")[0]
        assert pending["state"] == "WAIT_RESOLUTION" and pending["terminal_reason"] is None
        assert pending["next_attempt_at"] == iso_utc(now + timedelta(minutes=5))
        with repository.connect() as c:
            assert c.execute("SELECT count(*) FROM resolution_observations").fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM hypothetical_episodes").fetchone()[0] == 0
    finally:
        repository.close()


def test_capture_publication_failure_rolls_back_raw_rows_and_registry_updates(tmp_path, monkeypatch):
    from polybot.db.raw_repository import RawRepository
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="seed", now=NOW, enabled=True, ask=0.40)
        tracked_before = _sidecar_rows(config, "raw_tracked_events")
        insert = RawRepository.insert
        def fail_publication(c, table, row):
            if table == "raw_cycles" and row["run_id"] == "interrupted":
                raise sqlite3.OperationalError("simulated publication failure")
            insert(c, table, row)
        monkeypatch.setattr(RawRepository, "insert", staticmethod(fail_publication))
        with pytest.raises(sqlite3.OperationalError, match="publication failure"):
            _run_cycle(config, repository, trace, run_id="interrupted", now=NOW + timedelta(minutes=1),
                       enabled=True, discover=False, ask=0.40)
        for table in ("raw_cycles", "raw_events", "raw_books", "raw_payloads"):
            assert _sidecar_rows(config, table, "interrupted") == []
        assert _sidecar_rows(config, "raw_tracked_events") == tracked_before
        # The legacy cycle had already completed and remains intact.
        with repository.connect() as c:
            assert c.execute("SELECT count(*) FROM market_sweeps WHERE run_id='interrupted'").fetchone()[0] == 1
    finally:
        repository.close()


@pytest.mark.parametrize("bad_time", ["future_metadata", "future_book", "book_before_metadata"])
def test_raw_point_in_time_flag_rejects_impossible_receipt_order(tmp_path, bad_time):
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    trace = []
    try:
        _run_cycle(config, repository, trace, run_id="seed", now=NOW, enabled=True, ask=0.40)
        now = NOW + timedelta(minutes=1)
        gamma_now = now + timedelta(days=1000) if bad_time == "future_metadata" else now
        clob_now = (now + timedelta(days=1000) if bad_time == "future_book" else
                    now - timedelta(seconds=4) if bad_time == "book_before_metadata" else now)
        gamma = TraceGamma(trace, discover=False, now=gamma_now, repository=repository)
        clob = TraceClob(trace, now=clob_now, repository=repository)
        Collector(config, repository, gamma, clob, FakeSportsClock()).collect("bad-clock", now=now)
        rows = _sidecar_rows(config, "raw_books", "bad-clock")
        assert len(rows) == 3
        assert all(r["point_in_time_identity_valid"] == 0 for r in rows)
        if bad_time == "future_metadata":
            assert clob.book_calls == 1  # only the empty legacy token list
        assert repository.open_episodes() == []
    finally:
        repository.close()
