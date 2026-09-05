"""Prevent profitable-looking replays from crossing evidence boundaries."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import analyze_direct_book_grid as replay
from scripts.analyze_direct_book_grid import Cohort, EvidenceContractError, ExitPolicy
from tests.test_direct_book_grid import _book, _entry, SOCCER_POLICY


COHORT = Cohort("a" * 64, "c" * 64, "peach-shadow-mlb-1m-v2", "sim", "mlb")
START = "2026-08-30T13:00:00Z"
END = "2026-08-30T14:00:00Z"


def _config(cohort=COHORT):
    return {
        "strategy_name": "golden-peach",
        "mode": cohort.mode,
        "trading": {
            "strategy_source_digest": cohort.source_digest,
            "sport_family": cohort.sport_family,
            "sport_profile_version": "peach-mlb-shadow-ready-v1",
            "book_shape": "direct-two-team-moneyline",
            "buy_amount_usdc": 5.0,
            "max_snapshot_gap_minutes": 2.0,
            "entry": {
                "late_exit_minute": 1000000.0,
                "late_profit_fraction": 0.5,
                "stop_cutoff_minute": 1000000.0,
                "max_stop_spread": 0.10,
                "take_profit_delta": 0.05,
                "stop_loss_delta": 0.10,
                "prob_min": 0.60,
                "prob_max": 0.94,
                "max_source_minute": 10,
            },
        },
    }


def _run(connection, run_id, at, cohort=COHORT, status="SUCCESS"):
    connection.execute(
        "INSERT OR IGNORE INTO strategy_configs VALUES (?,?,?,?)",
        (cohort.config_hash, "golden-peach", cohort.mode, json.dumps(_config(cohort))),
    )
    connection.execute(
        "INSERT INTO run_audits VALUES (?,?,?,?,?,?,?)",
        (
            run_id,
            cohort.config_hash,
            cohort.job_name,
            cohort.mode,
            status,
            "golden-peach",
            at,
        ),
    )


def _snapshot(
    connection,
    sid,
    run_id,
    at,
    *,
    event="event-1",
    token="home",
    price=0.80,
    minute=3.0,
):
    condition = "condition-" + event
    book = json.dumps(
        {
            "asks": [{"price": price, "size": 100}],
            "bids": [{"price": price - 0.01, "size": 100}],
        }
    )
    connection.execute(
        "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            condition,
            event,
            token,
            token,
            "TEAM",
            "HOME" if token.endswith("home") else "AWAY",
            price,
            price - 0.01,
            price,
            minute,
            "SCHEDULED_START_AGE_SHADOW_ONLY",
            book,
            run_id,
            "mlb",
            "peach-mlb-shadow-ready-v1",
            "direct-two-team-moneyline",
            at,
        ),
    )


def _episode(connection, eid, run_id, at, *, event="event-1", sid=1, cohort=COHORT):
    _run(connection, run_id, at, cohort)
    # Token IDs must be globally unique across different match events.
    home = event + "-home"
    away = event + "-away"
    _snapshot(connection, sid, run_id, at, event=event, token=home)
    _snapshot(connection, sid + 1, run_id, at, event=event, token=away, price=0.21)
    connection.execute(
        "INSERT INTO market_catalog VALUES (?,?,?,?,?)",
        ("condition-" + event, 1, 0.05, 1, 1),
    )
    connection.execute(
        "INSERT INTO entry_episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            eid,
            home,
            "condition-" + event,
            event,
            home,
            sid,
            0.80,
            at,
            3.0,
            "TRADE_CREATED",
            None,
            eid,
        ),
    )


@pytest.fixture
def database(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(replay, "BOOTSTRAP_SAMPLES", 100)
    path = tmp_path / "trades_sim.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE strategy_configs(config_hash TEXT PRIMARY KEY,strategy_name TEXT,mode TEXT,config_json TEXT);
        CREATE TABLE run_audits(run_id TEXT PRIMARY KEY,config_hash TEXT,job_name TEXT,mode TEXT,status TEXT,strategy_name TEXT,started_at TEXT);
        CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,condition_id TEXT,event_id TEXT,token_id TEXT,outcome TEXT,outcome_side TEXT,result_kind TEXT,probability REAL,best_bid REAL,best_ask REAL,source_elapsed_minutes REAL,source_clock_reason TEXT,book_json TEXT,run_id TEXT,sport_family TEXT,sport_profile_version TEXT,book_shape TEXT,timestamp TEXT);
        CREATE TABLE market_catalog(condition_id TEXT PRIMARY KEY,fees_enabled INTEGER,fee_rate REAL,fee_exponent INTEGER,fee_taker_only INTEGER);
        CREATE TABLE entry_episodes(id INTEGER PRIMARY KEY,token_id TEXT,condition_id TEXT,event_id TEXT,outcome TEXT,entry_snapshot_id INTEGER,exact_vwap REAL,observed_at TEXT,source_elapsed_minutes REAL,execution_state TEXT,execution_reason TEXT,trade_id INTEGER);
        """)
        _episode(connection, 1, "entry", "2026-08-30 13:00:00")
        _run(connection, "exit", "2026-08-30 13:01:00")
        _snapshot(
            connection,
            3,
            "exit",
            "2026-08-30 13:01:00",
            token="event-1-home",
            price=0.87,
            minute=4,
        )
    return path


def _analyze(path, **changes):
    return replay.analyze(
        path,
        cohort=changes.pop("cohort", COHORT),
        review_start=START,
        review_end_exclusive=changes.pop("end", END),
        **changes,
    )


def test_readonly_replay_is_reproducible_and_does_not_claim_live_pnl(database):
    before = database.read_bytes()
    a = _analyze(database)
    b = _analyze(database)
    assert a == b and before == database.read_bytes()
    assert a["episodes"]["selected_unique_events"] == 1
    assert a["configured_policy"]["evaluated_events"] == 1
    assert a["episodes"]["selected_sides"] == {"TEAM": 1}
    assert (
        a["interpretation"]
        == "DISPLAYED_BOOK_COUNTERFACTUAL_NOT_ACTUAL_FILL_OR_REALIZED_PNL"
    )
    assert a["contract"]["exit_policy"]["stop_cutoff_minute"] == 1000000
    assert not any("direct NO" in s for s in a["limitations"])


@pytest.mark.parametrize(
    "cohort",
    [
        replace(COHORT, source_digest="wrong"),
        replace(COHORT, sport_family="soccer"),
        replace(COHORT, mode="live"),
        replace(COHORT, job_name="another-job"),
    ],
)
def test_explicit_cohort_identity_must_match(database, cohort):
    with pytest.raises(EvidenceContractError):
        _analyze(database, cohort=cohort)


def test_missing_selection_cannot_silently_combine_all_history(database):
    with pytest.raises(TypeError):
        replay.analyze(database)
    assert replay.list_cohorts(database)[0]["source_digest"] == COHORT.source_digest


def test_other_cohort_entries_are_not_pooled(database):
    other = replace(COHORT, config_hash="b" * 64, source_digest="d" * 64)
    with sqlite3.connect(database) as c:
        _episode(
            c, 2, "other", "2026-08-30 13:02:00", event="event-2", sid=4, cohort=other
        )
    report = _analyze(database)
    assert report["episodes"]["selected_unique_events"] == 1
    assert (
        report["episodes"]["exclusions"]["reasons"]["OTHER_OR_UNPROVEN_ENTRY_COHORT"]
        == 1
    )


@pytest.mark.parametrize(
    "boundary", ["failed_run", "new_source", "wrong_sport", "missing_book"]
)
def test_path_boundary_censors_instead_of_selecting_later_winner(database, boundary):
    with sqlite3.connect(database) as c:
        c.execute(
            "UPDATE market_snapshots SET probability=.81,best_bid=.80,best_ask=.81,book_json=? WHERE id=3",
            (
                json.dumps(
                    {
                        "asks": [{"price": 0.81, "size": 100}],
                        "bids": [{"price": 0.80, "size": 100}],
                    }
                ),
            ),
        )
        if boundary == "failed_run":
            c.execute("UPDATE run_audits SET status='FAILED' WHERE run_id='exit'")
        elif boundary == "new_source":
            c.execute(
                "UPDATE run_audits SET config_hash=? WHERE run_id='exit'", ("b" * 64,)
            )
        elif boundary == "wrong_sport":
            c.execute("UPDATE market_snapshots SET sport_family='soccer' WHERE id=3")
        else:
            c.execute("UPDATE market_snapshots SET book_json=NULL WHERE id=3")
        _run(c, "later", "2026-08-30 13:02:00")
        _snapshot(
            c,
            4,
            "later",
            "2026-08-30 13:02:00",
            token="event-1-home",
            price=0.99,
            minute=5,
        )
    report = _analyze(database)
    assert report["configured_policy"]["evaluated_events"] == 0
    assert report["configured_policy"]["censored_event_ids"] == ["event-1"]


def test_entry_end_and_followup_asof_are_exclusive(database):
    report = _analyze(database, end="2026-08-30T13:01:00Z")
    assert report["configured_policy"]["evaluated_events"] == 0
    assert (
        _analyze(database, end="2026-08-30T13:01:00Z", as_of="2026-08-30T13:02:00Z")[
            "configured_policy"
        ]["evaluated_events"]
        == 1
    )
    with sqlite3.connect(database) as c:
        _episode(c, 2, "boundary-entry", "2026-08-30 13:01:00", event="event-2", sid=4)
    assert (
        _analyze(database, end="2026-08-30T13:01:00Z")["episodes"][
            "selected_unique_events"
        ]
        == 1
    )


def test_wrong_snapshot_sport_cannot_inherit_selected_config(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE market_snapshots SET sport_family='soccer' WHERE id=1")
    with pytest.raises(EvidenceContractError, match="sport/profile"):
        _analyze(database)


def test_failed_cycle_without_snapshot_cannot_be_bridged(database):
    with sqlite3.connect(database) as c:
        _run(c, "failed-without-book", "2026-08-30 13:00:30", status="FAILED")
    report = _analyze(database)
    assert report["configured_policy"]["evaluated_events"] == 0
    assert (
        report["collection"]["path_boundaries"]["RUN_WITHOUT_SUCCESS_OR_SAME_COHORT"]
        == 1
    )


def test_entry_observation_delay_in_same_run_is_bounded(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE entry_episodes SET observed_at='2026-08-30 13:00:00.500000'")
    assert _analyze(database)["episodes"]["selected_unique_events"] == 1
    with sqlite3.connect(database) as c:
        c.execute("UPDATE entry_episodes SET observed_at='2026-08-30 13:03:00'")
    assert _analyze(database)["episodes"]["selected_unique_events"] == 0


def test_incomplete_two_team_entry_is_excluded(database):
    with sqlite3.connect(database) as c:
        c.execute("DELETE FROM market_snapshots WHERE id=2")
    assert _analyze(database)["episodes"]["exclusions"]["reasons"] == {
        "INCOMPLETE_DIRECT_EVENT_BOOK_SET": 1
    }


def test_unknown_fee_never_becomes_zero(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE market_catalog SET fees_enabled=NULL")
    assert _analyze(database)["episodes"]["exclusions"]["reasons"] == {
        "FEE_ENABLEMENT_UNPROVEN": 1
    }


def test_cli_requires_explicit_selection(database, monkeypatch):
    monkeypatch.setattr("sys.argv", ["replay", "--db", str(database)])
    with pytest.raises(SystemExit) as error:
        replay.parse_args()
    assert error.value.code == 2


def test_cli_output_cannot_overwrite_input(database, monkeypatch):
    before = database.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        ["replay", "--db", str(database), "--list-cohorts", "--output", str(database)],
    )
    with pytest.raises(SystemExit, match="overwrite"):
        replay.main()
    assert database.read_bytes() == before


def test_mlb_uses_resolved_late_policy_instead_of_soccer_80_minutes():
    policy = ExitPolicy(1000000, 0.5, 1000000, 0.10, 2)
    path = (_book("2026-08-30 13:01:00", 0.83, 90),)
    args = {"take_profit_delta": 0.05, "stop_loss_delta": 0.10}
    assert replay._evaluate(_entry(), path, policy=policy, **args) is None
    assert (
        replay._evaluate(_entry(), path, policy=SOCCER_POLICY, **args).reason
        == "LATE_HALF_TARGET"
    )
    path = (_book("2026-08-30 13:01:00", 0.60, 90),)
    assert replay._evaluate(_entry(), path, policy=policy, **args).reason == "STOP"
    assert replay._evaluate(_entry(), path, policy=SOCCER_POLICY, **args) is None


def test_large_observation_gap_cannot_hide_stop_before_later_profit():
    result = replay._evaluate(
        _entry(),
        (_book("2026-08-30 13:20:00", 0.99, 23),),
        take_profit_delta=0.05,
        stop_loss_delta=0.10,
        policy=SOCCER_POLICY,
    )
    assert result is None
