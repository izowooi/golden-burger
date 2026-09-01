from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot.api.clob_client import build_execution_capacity_evidence
from polybot.db.models import init_database
from scripts.replay_direct_six_book import (
    CAPACITY_NOTIONALS_USDC,
    database_report,
)


CONFIG_HASH = "a" * 64
PROTOCOL_HASH = "b" * 64
LEAGUE_HASH = "c" * 64
SOURCE_HASH = "d" * 64
PROFILE = "mlb-collection-uncalibrated-v1"
CLASSIFIER = "plum-major-sports-family-contract-v3"
BOOK_SHAPE = "direct-two-team-moneyline"


def _book(token_id: str, probability: float) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "token_id": token_id,
            "bids": [{"price": probability - 0.01, "size": 10_000}],
            "asks": [{"price": probability, "size": 10_000}],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _resolved_config() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "strategy_name": "golden-plum",
            "mode": "sim",
            "trading": {
                "sport_family": "mlb",
                "sport_profile_version": PROFILE,
                "preregistration_sha256": PROTOCOL_HASH,
                "classifier_version": CLASSIFIER,
                "league_mapping_sha256": LEAGUE_HASH,
                "strategy_source_digest": SOURCE_HASH,
                "book_shape": BOOK_SHAPE,
                "scaling_notionals_usdc": list(CAPACITY_NOTIONALS_USDC),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _strict_replay_db(path: Path, *, terminal: bool = True) -> Path:
    Session = init_database(str(path))
    session = Session()
    engine = session.get_bind()
    session.close()
    engine.dispose()

    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE strategy_configs (
                config_hash TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                git_commit TEXT NOT NULL
            );
            CREATE TABLE run_audits (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                job_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                git_commit TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                cycle_stats_json TEXT,
                db_summary_json TEXT,
                error_type TEXT,
                error_message TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO strategy_configs
                (config_hash,schema_version,strategy_name,mode,config_json,
                 first_seen_at,git_commit)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                CONFIG_HASH,
                1,
                "golden-plum",
                "sim",
                _resolved_config(),
                "2026-09-01T00:00:00+00:00",
                "e" * 40,
            ),
        )
        connection.execute(
            """
            INSERT INTO market_catalog
                (condition_id,market_id,event_id,outcomes_json,
                 outcome_prices_json,token_ids_json,tags_json,config_hash,
                 sport_family,sport_profile_version,protocol_sha256,
                 classifier_version,league_mapping_sha256,
                 strategy_source_digest,book_shape,first_seen_at,last_seen_at,
                 followup_attempt_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "mlb-condition-1",
                "mlb-market-1",
                "mlb-event-1",
                '["Home Club","Away Club"]',
                '[0.5,0.5]',
                '["mlb-home","mlb-away"]',
                "[]",
                CONFIG_HASH,
                "mlb",
                PROFILE,
                PROTOCOL_HASH,
                CLASSIFIER,
                LEAGUE_HASH,
                SOURCE_HASH,
                BOOK_SHAPE,
                "2026-09-01 00:00:00",
                "2026-09-01 00:00:00",
                0,
            ),
        )
        prices = (0.72, 0.74, 0.75, 0.91)
        for index, home_probability in enumerate(prices, start=1):
            run_id = f"run-{index}"
            timestamp = datetime(2026, 9, 1) + timedelta(minutes=index)
            event_cycle_id = hashlib.sha256(
                f"sweep-{index}:mlb-event-1".encode()
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO run_audits
                    (run_id,schema_version,strategy_name,job_name,mode,
                     config_hash,git_commit,started_at,finished_at,status)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    1,
                    "golden-plum",
                    "plum-shadow-gold-mlb-1m-v1",
                    "sim",
                    CONFIG_HASH,
                    "e" * 40,
                    timestamp.isoformat(),
                    (timestamp + timedelta(seconds=10)).isoformat(),
                    "SUCCESS",
                ),
            )
            connection.execute(
                """
                INSERT INTO market_sweeps
                    (sweep_id,schema_version,run_id,started_at,completed_at,
                     cursor_complete,pages,raw_market_count,
                     unique_condition_count,qualified_market_count,
                     excluded_condition_count,exclusion_counts_json,
                     missing_condition_id_count,duplicate_raw_count,min_liquidity,
                     min_volume,membership_digest_sha256,
                     snapshot_eligible_count,snapshotted_market_count,
                     membership_detail_stored,config_hash,sport_family,
                     sport_profile_version,protocol_sha256,classifier_version,
                     league_mapping_sha256,strategy_source_digest,book_shape,
                     expected_result_kinds_json,expected_market_count,
                     expected_token_count,event_count,complete_event_count,
                     incomplete_event_count,event_evidence_digest_sha256)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"sweep-{index}",
                    2,
                    run_id,
                    timestamp.isoformat(),
                    (timestamp + timedelta(seconds=1)).isoformat(),
                    1,
                    1,
                    1,
                    1,
                    1,
                    0,
                    "{}",
                    0,
                    0,
                    5000,
                    5000,
                    "f" * 64,
                    1,
                    1,
                    1,
                    CONFIG_HASH,
                    "mlb",
                    PROFILE,
                    PROTOCOL_HASH,
                    CLASSIFIER,
                    LEAGUE_HASH,
                    SOURCE_HASH,
                    BOOK_SHAPE,
                    '["AWAY","HOME"]',
                    1,
                    2,
                    1,
                    1,
                    0,
                    "1" * 64,
                ),
            )
            connection.execute(
                """
                INSERT INTO event_cycle_evidence
                    (event_cycle_id,sweep_id,run_id,config_hash,event_id,
                     observed_at,sport_family,sport_profile_version,
                     protocol_sha256,classifier_version,league_mapping_sha256,
                     strategy_source_digest,book_shape,
                     expected_result_kinds_json,observed_result_kinds_json,
                     missing_result_kinds_json,condition_ids_json,token_ids_json,
                     expected_market_count,observed_market_count,
                     expected_token_count,observed_token_count,
                     duplicate_condition_count,duplicate_token_count,
                     duplicate_identity_count,complete,reason,evidence_sha256)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_cycle_id,
                    f"sweep-{index}",
                    run_id,
                    CONFIG_HASH,
                    "mlb-event-1",
                    timestamp.isoformat(),
                    "mlb",
                    PROFILE,
                    PROTOCOL_HASH,
                    CLASSIFIER,
                    LEAGUE_HASH,
                    SOURCE_HASH,
                    BOOK_SHAPE,
                    '["AWAY","HOME"]',
                    '["AWAY","HOME"]',
                    "[]",
                    '["mlb-condition-1"]',
                    '["mlb-away","mlb-home"]',
                    1,
                    1,
                    2,
                    2,
                    0,
                    0,
                    0,
                    1,
                    "complete",
                    "2" * 64,
                ),
            )
            for token_id, result_kind, probability in (
                ("mlb-home", "HOME", home_probability),
                ("mlb-away", "AWAY", 1 - home_probability),
            ):
                book = _book(token_id, probability)
                capacity = build_execution_capacity_evidence(
                    book,
                    CAPACITY_NOTIONALS_USDC,
                )
                connection.execute(
                    """
                    INSERT INTO market_snapshots
                        (condition_id,event_id,token_id,outcome,outcome_side,
                         result_kind,probability,midpoint,best_bid,best_ask,
                         spread,book_json,execution_capacity_json,run_id,
                         config_hash,sport_family,sport_profile_version,
                         protocol_sha256,classifier_version,
                         league_mapping_sha256,strategy_source_digest,book_shape,
                         event_cycle_id,event_set_complete,event_set_reason,timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "mlb-condition-1",
                        "mlb-event-1",
                        token_id,
                        f"{result_kind} Club",
                        "DIRECT",
                        result_kind,
                        probability,
                        probability - 0.005,
                        probability - 0.01,
                        probability,
                        0.01,
                        book,
                        capacity,
                        run_id,
                        CONFIG_HASH,
                        "mlb",
                        PROFILE,
                        PROTOCOL_HASH,
                        CLASSIFIER,
                        LEAGUE_HASH,
                        SOURCE_HASH,
                        BOOK_SHAPE,
                        event_cycle_id,
                        1,
                        "complete",
                        timestamp.isoformat(),
                    ),
                )
        if terminal:
            payouts = json.dumps(
                {"mlb-away": 0.0, "mlb-home": 1.0},
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO tracked_resolution_observations
                    (resolution_id,condition_id,event_id,run_id,config_hash,
                     sport_family,sport_profile_version,protocol_sha256,
                     classifier_version,league_mapping_sha256,
                     strategy_source_digest,observed_at,source,winner_index,
                     winner_token_id,winner_outcome,payouts_json,
                     evidence_sha256,evidence_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "3" * 64,
                    "mlb-condition-1",
                    "mlb-event-1",
                    "run-4",
                    CONFIG_HASH,
                    "mlb",
                    PROFILE,
                    PROTOCOL_HASH,
                    CLASSIFIER,
                    LEAGUE_HASH,
                    SOURCE_HASH,
                    "2026-09-01T00:10:00",
                    "GAMMA_CONDITION_FOLLOWUP",
                    0,
                    "mlb-home",
                    "Home Club",
                    payouts,
                    "4" * 64,
                    "{}",
                ),
            )
    return path


def test_database_replay_requires_one_successful_cohort_and_reports_ladder(
    tmp_path,
) -> None:
    path = _strict_replay_db(tmp_path / "strict.db")

    report = database_report(path, sport_family="mlb")

    assert report["cohort"]["config_hash"] == CONFIG_HASH
    assert report["cohort"]["job_name"] == "plum-shadow-gold-mlb-1m-v1"
    assert report["event_cycle_health"] == {
        "complete": 4,
        "incomplete": 0,
        "reasons": {},
    }
    assert report["capacity_notionals_usdc"] == list(CAPACITY_NOTIONALS_USDC)
    assert {row["notional_usdc"] for row in report["scaling"]} == set(
        CAPACITY_NOTIONALS_USDC
    )
    assert report["terminal_token_payouts"] == 2


def test_database_replay_rejects_caller_family_mismatch(tmp_path) -> None:
    path = _strict_replay_db(tmp_path / "family.db")

    with pytest.raises(ValueError, match="caller sport family"):
        database_report(path, sport_family="soccer")


def test_database_replay_rejects_mixed_config_cohorts(tmp_path) -> None:
    path = _strict_replay_db(tmp_path / "mixed.db")
    other_hash = "9" * 64
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO strategy_configs VALUES (?,?,?,?,?,?,?)",
            (
                other_hash,
                1,
                "golden-plum",
                "sim",
                _resolved_config(),
                "2026-09-01T00:00:00",
                "e" * 40,
            ),
        )
        connection.execute(
            "UPDATE run_audits SET config_hash=? WHERE run_id='run-4'",
            (other_hash,),
        )

    with pytest.raises(ValueError, match="one config_hash"):
        database_report(path, sport_family="mlb")


def test_database_replay_rejects_failed_run_snapshots(tmp_path) -> None:
    path = _strict_replay_db(tmp_path / "failed.db")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE run_audits SET status='FAILED' WHERE run_id='run-4'"
        )

    with pytest.raises(ValueError, match="outside successful run audits"):
        database_report(path, sport_family="mlb")


def test_database_replay_counts_right_censored_instead_of_dropping_it(tmp_path) -> None:
    path = _strict_replay_db(tmp_path / "censored.db", terminal=False)

    report = database_report(path, sport_family="mlb")

    primary = report["primary"]["0.75_to_0.95_stop_0.15"]["summary"]
    assert primary["signals"] == 1
    assert primary["right_censored"] == 1
    assert primary["known_pnl_count"] == 0


def test_init_database_repeated_additive_migration_is_deterministic(tmp_path) -> None:
    path = tmp_path / "repeat.db"
    first = init_database(str(path))
    first().close()
    second = init_database(str(path))
    second().close()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(market_snapshots)")
        }
    assert {"sport_family", "event_cycle_id", "protocol_sha256"} <= columns


def test_init_database_fails_closed_on_incompatible_existing_schema(tmp_path) -> None:
    path = tmp_path / "broken.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE trades (id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="incompatible trades schema"):
        init_database(str(path))
