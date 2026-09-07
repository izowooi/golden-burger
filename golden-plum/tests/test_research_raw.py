from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sa_event, text
from sqlalchemy.exc import IntegrityError

from polybot.api.clob_client import ClobClientWrapper, _research_batch_observations
from polybot.config import TradingConfig
from polybot.db.models import (
    RawBookCycle,
    RawBookObservation,
    RawEventObservation,
    RawTrackedEvent,
    init_database,
)
from polybot.db.repository import TradeRepository
from polybot.strategy import research_raw as raw
from polybot.strategy.scanner import MarketScanner
from tests.test_scanner import _triad, _event, NOW


def book(token, *, asks=True, bids=True):
    return {
        "asset_id": token,
        "asks": [{"price": "0.6", "size": "0.01"}] if asks else [],
        "bids": [{"price": "0.59", "size": "30"}] if bids else [],
        "timestamp": "source-clock",
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    Session = init_database(
        str(path), maintenance_on_start=False, enable_research_raw=True
    )
    session = Session()
    session.execute(
        text(
            "CREATE TABLE run_audits(run_id TEXT PRIMARY KEY, config_hash TEXT, job_name TEXT, mode TEXT)"
        )
    )
    session.commit()
    run = ["r1"]
    monkeypatch.setattr(raw, "current_run_id", lambda: run[0])

    def next_run(name):
        run[0] = name
        session.execute(
            text("INSERT INTO run_audits VALUES(:r,'cfg','shadow','sim')"), {"r": name}
        )
        session.commit()

    next_run("r1")
    clob = ClobClientWrapper.__new__(ClobClientWrapper)
    clob.simulation_mode = True
    clob._initialized = True
    clob._client = SimpleNamespace(
        get_order_books=lambda params: [book(x.token_id) for x in params]
    )
    config = TradingConfig(strategy_source_digest="a" * 64)
    gamma = SimpleNamespace(cycle_budget=None, get_event_by_id=lambda _: None)
    scanner = SimpleNamespace(
        repo=TradeRepository(session), clob=clob, gamma=gamma, config=config
    )
    yield SimpleNamespace(
        path=path, session=session, scanner=scanner, next_run=next_run, Session=Session
    )
    session.close()
    Session.kw["bind"].dispose()


def collect(env, markets, now=NOW, error=None):
    archive, tokens = raw.begin_raw_archive(
        env.scanner, now, markets, discovery_error=error
    )
    if error is None:
        env.scanner.clob.get_buy_book_walks(tokens, notional_usdc=5)
    result = archive.publish()
    return result


def test_shallow_and_empty_books_are_raw_complete_without_buy_walk(env):
    triad = _triad()
    env.scanner.clob.client.get_order_books = lambda params: [
        book(x.token_id, asks=x.token_id != "yes-HOME", bids=x.token_id != "no-HOME")
        for x in params
    ]
    result = collect(env, triad)
    assert result["status"] == "COMPLETE"
    assert result["expected_tokens"] == result["observed_tokens"] == 6
    rows = env.session.query(RawBookObservation).all()
    assert {r.status for r in rows} == {"FULL", "EMPTY_ASKS", "EMPTY_BIDS"}
    assert all(r.received_at and r.book_sha256 for r in rows)
    # All positive ask books have only $.006 depth, so every BUY walk fails.
    assert env.scanner.clob.get_buy_book_walks(["yes-HOME"], notional_usdc=5) == {}
    assert env.session.query(RawTrackedEvent).one().state == "LIVE"


def test_missing_structural_slots_are_explicit_without_fake_tokens(env):
    result = collect(env, _triad()[:2])
    assert result["status"] == "PARTIAL"
    absent = (
        env.session.query(RawBookObservation).filter_by(status="IDENTITY_MISSING").all()
    )
    assert len(absent) == 2
    assert all(r.token_id is None and r.book_json is None for r in absent)


def test_followup_after_discovery_loss_continues_full_books_without_entry(env):
    collect(env, _triad())
    env.next_run("r2")
    result = collect(env, [], NOW + timedelta(minutes=1))
    assert result["expected_tokens"] == result["observed_tokens"] == 6
    assert result["status"] == "PARTIAL"  # source clock lookup is missing
    track = env.session.query(RawTrackedEvent).one()
    assert track.state == "LIVE" and track.missing_count == 1
    assert (
        env.session.query(RawEventObservation).filter_by(run_id="r2").one().reason
        == "event_source_missing_or_identity_gap"
    )


def test_ended_final_book_then_waits_for_exact_resolution(env):
    collect(env, _triad())
    ended = dict(_event(), ended=True, live=False, markets=[])
    env.scanner.gamma.get_event_by_id = lambda _: ended
    env.next_run("r2")
    result = collect(env, [], NOW + timedelta(minutes=1))
    assert result["observed_tokens"] == 6
    evidence = json.loads(
        env.session.query(RawEventObservation)
        .filter_by(run_id="r2")
        .one()
        .evidence_json
    )
    assert evidence["final_raw_book_complete"] is True
    assert env.session.query(RawTrackedEvent).one().state == "WAIT_RESOLUTION"
    env.next_run("r3")
    assert collect(env, [], NOW + timedelta(minutes=2))["expected_tokens"] == 0
    assert (
        env.session.query(RawEventObservation).filter_by(run_id="r3").one().reason
        == "resolution_retry_not_due"
    )
    terminal = _triad()
    for i, market in enumerate(terminal):
        market.update(closed=True, outcomePrices=["1", "0"] if i == 0 else ["0", "1"])
    ended["markets"] = terminal
    env.next_run("r4")
    collect(env, [], NOW + timedelta(minutes=7))
    assert env.session.query(RawTrackedEvent).one().state == "RESOLVED"
    assert (
        len(
            json.loads(
                env.session.query(RawEventObservation)
                .filter_by(run_id="r4")
                .one()
                .evidence_json
            )["terminal_proofs"]
        )
        == 3
    )


def test_contradictory_result_triad_does_not_resolve(env):
    collect(env, _triad())
    terminal = _triad()
    for market in terminal:
        market.update(closed=True, outcomePrices=["1", "0"])
    env.scanner.gamma.get_event_by_id = lambda _: dict(
        _event(), ended=True, live=False, markets=terminal
    )
    env.next_run("r2")
    collect(env, [], NOW + timedelta(minutes=1))
    assert env.session.query(RawTrackedEvent).one().state == "WAIT_RESOLUTION"


def test_discovery_failure_preserves_expected_not_attempted_and_previous_books_not_reused(
    env,
):
    collect(env, _triad())
    env.next_run("r2")
    result = collect(env, [], NOW + timedelta(minutes=1), error="discovery_timeout")
    assert result["observed_tokens"] == 0
    rows = env.session.query(RawBookObservation).filter_by(run_id="r2").all()
    assert len(rows) == 6 and all(
        r.status == "NOT_ATTEMPTED" and r.book_json is None for r in rows
    )


def test_publication_is_atomic_and_append_only(env):
    archive, tokens = raw.begin_raw_archive(env.scanner, NOW, _triad())
    env.scanner.clob.get_buy_book_walks(tokens, notional_usdc=5)

    def fail(*_):
        raise RuntimeError("injected evidence write failure")

    sa_event.listen(RawBookObservation, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            archive.publish()
    finally:
        sa_event.remove(RawBookObservation, "before_insert", fail)
    assert env.session.query(RawBookCycle).all() == []
    assert env.session.query(RawTrackedEvent).all() == []
    collect(env, _triad())
    with pytest.raises(IntegrityError, match="append-only raw evidence"):
        env.session.execute(text("DELETE FROM raw_book_observations"))
    env.session.rollback()
    with pytest.raises(IntegrityError, match="append-only raw evidence"):
        env.session.execute(text("UPDATE raw_book_cycles SET status='FAKE'"))
    env.session.rollback()


def test_run_is_idempotent_and_does_not_scan_history(env):
    queries = []

    def capture(conn, cursor, statement, params, context, executemany):
        queries.append(statement)

    sa_event.listen(env.Session.kw["bind"], "before_cursor_execute", capture)
    collect(env, _triad())
    first = env.session.query(RawBookObservation).all()
    result = collect(env, _triad())
    sa_event.remove(env.Session.kw["bind"], "before_cursor_execute", capture)
    assert result["idempotent"] is True
    assert env.session.query(RawBookObservation).all() == first
    assert not any("quick_check" in q.lower() or "count(" in q.lower() for q in queries)


def test_raw_archive_never_activates_for_live(env):
    env.scanner.clob.simulation_mode = False
    assert raw.begin_raw_archive(env.scanner, NOW, _triad()) == (None, [])


def test_additive_legacy_database_upgrade(tmp_path):
    path = tmp_path / "legacy.db"
    Session = init_database(
        str(path), maintenance_on_start=False, enable_research_raw=True
    )
    engine = Session.kw["bind"]
    with engine.begin() as conn:
        for table in (
            "raw_book_observations",
            "raw_event_observations",
            "raw_book_cycles",
            "raw_tracked_events",
        ):
            conn.exec_driver_sql(f"DROP TABLE {table}")
        conn.exec_driver_sql("CREATE TABLE legacy_marker(value TEXT)")
        conn.exec_driver_sql("INSERT INTO legacy_marker VALUES('preserve')")
    engine.dispose()
    NewSession = init_database(
        str(path), maintenance_on_start=False, enable_research_raw=True
    )
    with NewSession() as session:
        assert (
            session.execute(text("SELECT value FROM legacy_marker")).scalar()
            == "preserve"
        )
        assert session.query(RawBookCycle).all() == []
    NewSession.kw["bind"].dispose()


def test_batch_error_missing_duplicate_and_empty_are_distinct():
    observed = _research_batch_observations(
        [book("a", asks=False, bids=False), book("b"), book("b")],
        ["a", "b", "c"],
        "2026-09-07T01:00:00Z",
        "2026-09-07T01:00:01Z",
    )
    assert observed["a"]["status"] == "EMPTY_BOOK"
    assert observed["b"]["reason"] == "duplicate_token"
    assert observed["c"]["status"] == "MISSING"
    malformed = book("a")
    malformed["asks"] = None
    assert (
        _research_batch_observations([malformed], ["a"], None, None)["a"]["status"]
        == "ERROR"
    )
    assert (
        _research_batch_observations([book("wrong")], ["a"], None, None)["a"]["reason"]
        == "batch_token_identity_invalid"
    )


def test_scanner_persists_raw_even_when_all_five_dollar_walks_fail(env):
    from tests.test_scanner import _Gamma
    from polybot.db.models import MarketSnapshot

    markets = _triad()
    scanner = MarketScanner(
        _Gamma(markets),
        env.scanner.config,
        env.scanner.repo,
        clob_client=env.scanner.clob,
    )
    assert scanner.save_market_snapshots(markets, now=NOW) == 0
    assert env.session.query(MarketSnapshot).all() == []
    assert env.session.query(RawBookObservation).count() == 6
    assert env.session.query(RawBookCycle).one().status == "COMPLETE"


def test_timeout_and_budget_are_not_empty_liquidity(env):
    from polybot.utils.deadline import CycleDeadlineExceeded

    def timeout(_):
        raise TimeoutError("fixture")

    env.scanner.clob._client.get_order_books = timeout
    result = collect(env, _triad())
    assert result["book_status_counts"] == {"ERROR": 6}
    env.next_run("r2")

    def deadline(_):
        raise CycleDeadlineExceeded("fixture")

    env.scanner.clob._client.get_order_books = deadline
    result = collect(env, _triad(), NOW + timedelta(minutes=1))
    assert result["book_status_counts"] == {"NOT_ATTEMPTED": 6}
    assert all(
        r.requested_at is None and r.received_at is None
        for r in env.session.query(RawBookObservation).filter_by(run_id="r2")
    )


def test_two_team_sport_has_exactly_two_direct_slots(env):
    event = dict(
        _event(),
        id="mlb-event",
        title="Home Club vs. Away Club",
        teams=[
            {"name": "Home Club", "league": "mlb"},
            {"name": "Away Club", "league": "mlb"},
        ],
    )
    market = dict(
        _triad()[0],
        conditionId="mlb-condition",
        eventId="mlb-event",
        question="Home Club vs. Away Club",
        groupItemTitle="",
        negRisk=False,
        sportFamily="mlb",
        outcomes=["Home Club", "Away Club"],
        outcomePrices=["0.6", "0.4"],
        clobTokenIds=["home", "away"],
        events=[event],
    )
    env.scanner.config.sport_family = "mlb"
    result = collect(env, [market])
    assert result["expected_tokens"] == 2
    assert {r.slot for r in env.session.query(RawBookObservation)} == {
        "HOME:DIRECT",
        "AWAY:DIRECT",
    }
    assert {r.token_id for r in env.session.query(RawBookObservation)} == {
        "home",
        "away",
    }


def test_followup_cap_keeps_deferred_games_and_continues_their_books(env):
    markets = []
    for i in range(6):
        triad = deepcopy(_triad(event=dict(_event(), id=f"event-{i}")))
        for market in triad:
            market["conditionId"] += f"-{i}"
            market["clobTokenIds"] = [x + f"-{i}" for x in market["clobTokenIds"]]
        markets.extend(triad)
    assert collect(env, markets)["observed_tokens"] == 36
    calls = []
    env.scanner.gamma.get_event_by_id = lambda event: calls.append(event)
    env.next_run("r2")
    result = collect(env, [], NOW + timedelta(minutes=1))
    assert len(calls) == raw.MAX_EVENT_FOLLOWUPS
    assert result["observed_tokens"] == 36
    assert (
        env.session.query(RawEventObservation)
        .filter_by(run_id="r2", reason="followup_budget_deferred")
        .count()
        == 2
    )


@pytest.mark.parametrize(
    "table", ["raw_book_cycles", "raw_event_observations", "raw_book_observations"]
)
def test_insert_or_replace_cannot_bypass_append_only(env, table):
    collect(env, _triad())
    env.session.execute(text("PRAGMA recursive_triggers=OFF"))
    with pytest.raises(IntegrityError, match="append-only raw evidence"):
        env.session.execute(
            text(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1")
        )
    env.session.rollback()


def test_terminal_token_to_outcome_swap_is_rejected(env):
    collect(env, _triad())
    terminal = _triad()
    for i, market in enumerate(terminal):
        market.update(closed=True, outcomePrices=["1", "0"] if i == 0 else ["0", "1"])
    terminal[0]["clobTokenIds"] = list(reversed(terminal[0]["clobTokenIds"]))
    env.scanner.gamma.get_event_by_id = lambda _: dict(
        _event(), ended=True, live=False, markets=terminal
    )
    env.next_run("r2")
    collect(env, [], NOW + timedelta(minutes=1))
    assert env.session.query(RawTrackedEvent).one().state == "LIVE"
    assert (
        env.session.query(RawEventObservation).filter_by(run_id="r2").one().reason
        == "followup_token_identity_gap"
    )


def test_default_live_database_has_no_research_schema_and_keeps_trades(tmp_path):
    from polybot.db.models import Trade, TradeStatus

    path = tmp_path / "live.db"
    Session = init_database(str(path), maintenance_on_start=False)
    with Session() as session:
        names = {
            r[0]
            for r in session.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")
            )
        }
        assert not any(name.startswith("raw_") for name in names)
        session.add(
            Trade(
                condition_id="live-condition",
                token_id="live-token",
                outcome="Yes",
                status=TradeStatus.HOLDING,
            )
        )
        session.commit()
        assert session.query(Trade).one().token_id == "live-token"
    Session.kw["bind"].dispose()


def test_default_initialization_preserves_existing_raw_archive(tmp_path):
    path = tmp_path / "existing.db"
    Session = init_database(
        str(path), maintenance_on_start=False, enable_research_raw=True
    )
    Session.kw["bind"].dispose()
    Session = init_database(str(path), maintenance_on_start=False)
    with Session() as session:
        assert (
            session.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_book_cycles'"
                )
            ).scalar()
            == "raw_book_cycles"
        )
    Session.kw["bind"].dispose()


def test_accepted_event_collects_market_suppressed_by_entry_gate_from_full_sweep(env):
    full = _triad()
    full[2].update(liquidityNum=0, volumeNum=0, acceptingOrders=False)
    env.scanner.gamma.last_raw_sweep_events = {"event-1": dict(_event(), markets=full)}
    env.scanner.gamma.get_event_by_id = lambda _: (_ for _ in ()).throw(
        AssertionError("cache already complete")
    )
    result = collect(env, full[:2])
    assert result["observed_tokens"] == result["expected_tokens"] == 6
    assert result["status"] == "COMPLETE"
    assert (
        env.session.query(RawBookObservation)
        .filter_by(token_id="yes-AWAY")
        .one()
        .status
        == "FULL"
    )
    assert (
        json.loads(env.session.query(RawEventObservation).one().evidence_json)[
            "identity_source"
        ]
        == "same_sweep_full_event"
    )


def test_incomplete_current_event_repairs_by_id_without_broadening_entry_list(env):
    full = _triad()
    seen = []

    def lookup(event_id):
        seen.append(event_id)
        return dict(_event(), markets=full)

    env.scanner.gamma.get_event_by_id = lookup
    original = deepcopy(full[:2])
    assert collect(env, original)["observed_tokens"] == 6
    assert len(original) == 2
    assert seen == ["event-1"]
    assert env.session.query(RawTrackedEvent).one().identity_attempt_count == 1


def test_invalid_existing_raw_schema_fails_closed_when_enabled(tmp_path):
    import sqlite3

    path = tmp_path / "bad-raw.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE raw_book_cycles(run_id TEXT PRIMARY KEY)")
    connection.close()
    with pytest.raises(RuntimeError, match="incompatible raw_book_cycles schema"):
        init_database(str(path), maintenance_on_start=False, enable_research_raw=True)


def test_boolean_book_numbers_are_not_valid_raw_depth():
    malformed = book("a")
    malformed["asks"] = [{"price": True, "size": 2}]
    assert (
        _research_batch_observations([malformed], ["a"], None, None)["a"]["status"]
        == "ERROR"
    )
