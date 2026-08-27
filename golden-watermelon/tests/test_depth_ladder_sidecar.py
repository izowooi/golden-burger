from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from polybot.config import (
    CLASSIFIER_VERSION,
    DATA_CONTRACT,
    ENTRY_THRESHOLDS,
    LEAGUE_MAPPING_SHA256,
    SCHEMA_PROFILE,
    UNIVERSE_PROFILE,
    league_registry_payload,
)
from polybot.db.repository import ResearchRepository
from polybot.source_digest import SOURCE_PATHS, compute_strategy_source_digest
from scripts import analyze_depth_ladder as sidecar


def _repository(tmp_path: Path, name: str) -> ResearchRepository:
    return ResearchRepository(
        tmp_path / name,
        busy_timeout_ms=1000,
        data_contract=DATA_CONTRACT,
        schema_profile=SCHEMA_PROFILE,
        universe_profile=UNIVERSE_PROFILE,
        classifier_version=CLASSIFIER_VERSION,
        league_mapping_sha256=LEAGUE_MAPPING_SHA256,
        league_mapping_json=json.dumps(
            league_registry_payload(), sort_keys=True, separators=(",", ":")
        ),
    )


def _add_cohort(
    repository: ResearchRepository,
    *,
    config_hash: str,
    source_digest: str,
    mode: str,
    job_name: str,
    run_id: str,
    first_seen_at: str,
    run_observed_at: str,
) -> None:
    repository.record_config(
        {
            "config_hash": config_hash,
            "strategy_source_digest": source_digest,
            "preregistration_sha256": "p" * 64,
            "job_name": job_name,
            "mode": mode,
            "config_json": "{}",
            "first_seen_at": first_seen_at,
        }
    )
    repository.record_run_event(
        {
            "event_id": f"run-event-{run_id}",
            "run_id": run_id,
            "event_type": "SUCCEEDED",
            "observed_at": run_observed_at,
            "config_hash": config_hash,
            "strategy_source_digest": source_digest,
            "detail_json": "{}",
        }
    )


def _insert(
    connection: sqlite3.Connection, table: str, row: dict[str, object]
) -> None:
    columns = tuple(row)
    connection.execute(
        f"INSERT INTO {table}({','.join(columns)}) "
        f"VALUES({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def _add_eligible_token(
    repository: ResearchRepository,
    *,
    run_id: str,
    token_id: str,
    asks: tuple[tuple[float, float], ...] = (),
    bids: tuple[tuple[float, float], ...] = (),
    attempt_status: str | None = "OBSERVED",
    save_snapshot: bool = True,
    decision_count: int = 5,
) -> None:
    snapshot_id = f"snapshot-{run_id}-{token_id}" if save_snapshot else None
    condition_id = f"condition-{token_id}"
    event_id = f"event-{token_id}"
    with sqlite3.connect(repository.path) as connection:
        _insert(
            connection,
            "outcome_observations",
            {
                "outcome_observation_id": f"outcome-observation-{run_id}-{token_id}",
                "market_observation_id": f"market-observation-{run_id}-{token_id}",
                "sweep_id": f"sweep-{run_id}",
                "run_id": run_id,
                "condition_id": condition_id,
                "event_id": event_id,
                "token_id": token_id,
                "outcome_index": 0,
                "outcome_label": "Home",
                "entry_eligible": 1,
                "gamma_probability": 0.96,
                "observed_at": "2026-08-27T00:00:00Z",
            },
        )
        if attempt_status is not None:
            _insert(
                connection,
                "orderbook_token_attempts",
                {
                    "attempt_id": f"attempt-{run_id}-{token_id}",
                    "run_id": run_id,
                    "token_id": token_id,
                    "status": attempt_status,
                    "request_id": f"request-{run_id}-{token_id}",
                    "observed_at": "2026-08-27T00:00:01Z",
                    "error_type": "BookError" if attempt_status != "OBSERVED" else None,
                    "error_message": "failed book" if attempt_status != "OBSERVED" else None,
                },
            )
        if save_snapshot:
            _insert(
                connection,
                "orderbook_snapshots",
                {
                    "snapshot_id": snapshot_id,
                    "run_id": run_id,
                    "token_id": token_id,
                    "request_id": f"request-{run_id}-{token_id}",
                    "observed_at": "2026-08-27T00:00:01Z",
                    "raw_book_sha256": hashlib.sha256(token_id.encode()).hexdigest(),
                    "best_bid": bids[0][0] if bids else None,
                    "best_ask": asks[0][0] if asks else None,
                    "bid_level_count": len(bids),
                    "ask_level_count": len(asks),
                    "source_timestamp": f"source-{token_id}",
                    "tick_size": 0.01,
                    "min_order_size": 5.0,
                },
            )
            for side, levels in (("ASK", asks), ("BID", bids)):
                for index, (price, size) in enumerate(levels):
                    _insert(
                        connection,
                        "orderbook_levels",
                        {
                            "level_id": f"level-{run_id}-{token_id}-{side}-{index}",
                            "snapshot_id": snapshot_id,
                            "side": side,
                            "level_index": index,
                            "price": price,
                            "size": size,
                        },
                    )
        for index, threshold in enumerate(ENTRY_THRESHOLDS[:decision_count]):
            _insert(
                connection,
                "signal_decisions",
                {
                    "decision_id": f"decision-{run_id}-{token_id}-{index}",
                    "run_id": run_id,
                    "market_observation_id": f"market-observation-{run_id}-{token_id}",
                    "snapshot_id": snapshot_id,
                    "condition_id": condition_id,
                    "event_id": event_id,
                    "token_id": token_id,
                    "outcome_index": 0,
                    "threshold": threshold,
                    "decided_at": "2026-08-27T00:00:01Z",
                    "best_ask": asks[0][0] if asks else None,
                    "entry_vwap": asks[0][0] if asks else None,
                    "entry_shares": None,
                    "entry_cost": None,
                    "prior_entry_vwap": None,
                    "entry_provenance": None,
                    "decision_status": "ABOVE_WITHOUT_NEW_CROSS",
                    "details_json": "{}",
                    "episode_id": None,
                },
            )


def _single_cohort_repository(
    tmp_path: Path,
    name: str = "evidence.db",
    *,
    source_digest: str = "source-v3d",
    job_name: str = "watermelon-white-1m-v3d",
) -> ResearchRepository:
    repository = _repository(tmp_path, name)
    _add_cohort(
        repository,
        config_hash=f"config-{job_name}",
        source_digest=source_digest,
        mode="sim",
        job_name=job_name,
        run_id=f"run-{job_name}",
        first_seen_at="2026-08-27T00:00:00Z",
        run_observed_at="2026-08-27T00:00:02Z",
    )
    return repository


def _row(report: dict[str, object], token_id: str, notional: int = 5) -> dict[str, object]:
    rows = report["rows"]
    assert isinstance(rows, list)
    return next(
        row
        for row in rows
        if row["token_id"] == token_id and row["notional_usdc"] == notional
    )


def test_depth_states_arithmetic_and_denominators(tmp_path: Path) -> None:
    repository = _single_cohort_repository(tmp_path)
    run_id = "run-watermelon-white-1m-v3d"
    _add_eligible_token(
        repository,
        run_id=run_id,
        token_id="full",
        asks=((0.95, 2000.0),),
        bids=((0.90, 2000.0),),
    )
    _add_eligible_token(
        repository,
        run_id=run_id,
        token_id="partial-ask",
        asks=((0.50, 4.0),),
        bids=((0.40, 4.0),),
    )
    _add_eligible_token(
        repository,
        run_id=run_id,
        token_id="no-ask-depth",
        asks=(),
        bids=((0.40, 100.0),),
    )
    _add_eligible_token(
        repository,
        run_id=run_id,
        token_id="book-unavailable",
        attempt_status="ERROR",
        save_snapshot=False,
    )
    _add_eligible_token(
        repository,
        run_id=run_id,
        token_id="partial-bid",
        asks=((0.50, 100.0),),
        bids=((0.40, 3.0),),
    )
    _add_eligible_token(
        repository,
        run_id=run_id,
        token_id="no-bid-depth",
        asks=((0.50, 100.0),),
        bids=(),
    )

    report = sidecar.analyze_database(repository.path)

    assert report["ladder_usdc"] == [
        5,
        10,
        15,
        20,
        25,
        30,
        40,
        50,
        75,
        100,
        150,
        250,
        500,
        750,
        1000,
    ]
    assert report["eligible_run_token_count"] == 6
    assert report["ladder_row_count"] == 6 * 15

    full = _row(report, "full")
    assert full["ask_state"] == "FULL"
    assert full["ask_spent_cash"] == pytest.approx(5.0)
    assert full["ask_unspent_cash"] == pytest.approx(0.0)
    assert full["ask_filled_shares"] == pytest.approx(5 / 0.95)
    assert full["ask_vwap"] == pytest.approx(0.95)
    assert full["immediate_bid_state"] == "FULL"
    assert full["immediate_bid_residual_shares"] == pytest.approx(0.0)
    assert full["immediate_bid_gross_proceeds"] == pytest.approx((5 / 0.95) * 0.90)
    assert full["full_round_trip_return"] == pytest.approx(0.90 / 0.95 - 1)
    assert full["raw_book_sha256"] == hashlib.sha256(b"full").hexdigest()
    assert full["source_timestamp"] == "source-full"
    assert full["tick_size"] == pytest.approx(0.01)
    assert full["min_order_size"] == pytest.approx(5.0)

    partial_ask = _row(report, "partial-ask")
    assert partial_ask["ask_state"] == "PARTIAL"
    assert partial_ask["ask_spent_cash"] == pytest.approx(2.0)
    assert partial_ask["ask_unspent_cash"] == pytest.approx(3.0)
    assert partial_ask["ask_filled_shares"] == pytest.approx(4.0)
    assert partial_ask["immediate_bid_state"] == "FULL"
    assert partial_ask["immediate_bid_residual_shares"] == pytest.approx(0.0)
    assert partial_ask["full_round_trip_return"] is None

    no_ask = _row(report, "no-ask-depth")
    assert no_ask["ask_state"] == "NO_DEPTH"
    assert no_ask["ask_unspent_cash"] == pytest.approx(5.0)
    assert no_ask["immediate_bid_state"] == "NOT_EVALUABLE"
    assert no_ask["full_round_trip_return"] is None

    unavailable = _row(report, "book-unavailable")
    assert unavailable["ask_state"] == "BOOK_UNAVAILABLE"
    assert unavailable["book_attempt_status"] == "ERROR"
    assert unavailable["snapshot_id"] is None
    assert unavailable["immediate_bid_state"] == "NOT_EVALUABLE"

    partial_bid = _row(report, "partial-bid")
    assert partial_bid["ask_state"] == "FULL"
    assert partial_bid["immediate_bid_state"] == "PARTIAL"
    assert partial_bid["immediate_bid_filled_shares"] == pytest.approx(3.0)
    assert partial_bid["immediate_bid_residual_shares"] == pytest.approx(7.0)
    assert partial_bid["full_round_trip_return"] is None

    no_bid = _row(report, "no-bid-depth")
    assert no_bid["ask_state"] == "FULL"
    assert no_bid["immediate_bid_state"] == "NO_DEPTH"
    assert no_bid["immediate_bid_residual_shares"] == pytest.approx(10.0)
    assert no_bid["full_round_trip_return"] is None

    denominators = report["denominators"]
    assert denominators["all_eligible"]["eligible_run_token_count"] == 6
    assert denominators["all_eligible"]["database_scoped_unique_event_count"] == 6
    assert denominators["all_eligible"]["database_scoped_unique_condition_count"] == 6
    assert denominators["all_eligible"]["database_scoped_unique_token_count"] == 6
    assert denominators["all_eligible"]["cross_database_distinct_event_id_count"] == 6
    assert denominators["all_eligible"]["book_attempt_status_counts"] == {
        "ERROR": 1,
        "OBSERVED": 5,
    }
    assert (
        denominators["full_5_usdc_ask_vwap_gte_0_95"][
            "eligible_run_token_count"
        ]
        == 1
    )
    assert (
        denominators["full_5_usdc_ask_vwap_gte_0_95"]
        ["database_scoped_unique_event_count"]
        == 1
    )
    assert denominators["all_eligible"]["by_notional_usdc"]["5"][
        "ask_states"
    ] == {"FULL": 3, "PARTIAL": 1, "NO_DEPTH": 1, "BOOK_UNAVAILABLE": 1}


def test_five_threshold_decisions_collapse_to_one_run_token_notional_row(
    tmp_path: Path,
) -> None:
    repository = _single_cohort_repository(tmp_path)
    _add_eligible_token(
        repository,
        run_id="run-watermelon-white-1m-v3d",
        token_id="dedupe",
        asks=((0.96, 2000.0),),
        bids=((0.95, 2000.0),),
        decision_count=5,
    )

    report = sidecar.analyze_database(repository.path)

    assert report["eligible_run_token_count"] == 1
    assert report["ladder_row_count"] == 15
    assert len(report["rows"]) == 15
    assert {row["collapsed_decision_rows"] for row in report["rows"]} == {5}
    assert {row["decision_threshold_count"] for row in report["rows"]} == {5}


def test_cohort_ambiguity_explicit_selection_and_latest_flag(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "ambiguous.db")
    _add_cohort(
        repository,
        config_hash="config-a",
        source_digest="source-a",
        mode="sim",
        job_name="watermelon-white-1m-v3d",
        run_id="run-a",
        first_seen_at="2026-08-27T00:00:00Z",
        run_observed_at="2026-08-27T00:01:00Z",
    )
    _add_eligible_token(
        repository,
        run_id="run-a",
        token_id="token-a",
        asks=((0.95, 100.0),),
        bids=((0.94, 100.0),),
    )
    _add_cohort(
        repository,
        config_hash="config-b",
        source_digest="source-b",
        mode="sim",
        job_name="watermelon-white-1m-v3d",
        run_id="run-b",
        first_seen_at="2026-08-27T01:00:00Z",
        run_observed_at="2026-08-27T01:01:00Z",
    )
    _add_eligible_token(
        repository,
        run_id="run-b",
        token_id="token-b",
        asks=((0.96, 100.0),),
        bids=((0.95, 100.0),),
    )

    with pytest.raises(ValueError, match="multiple config_hash"):
        sidecar.analyze_database(repository.path)
    with pytest.raises(ValueError, match="requires config_hash"):
        sidecar.analyze_database(repository.path, config_hash="config-a")

    explicit = sidecar.analyze_database(
        repository.path,
        config_hash="config-a",
        strategy_source_digest="source-a",
        mode="sim",
        job_name="watermelon-white-1m-v3d",
    )
    assert explicit["cohort"]["selection"] == "EXPLICIT"
    assert explicit["cohort"]["config_hash"] == "config-a"
    assert {row["token_id"] for row in explicit["rows"]} == {"token-a"}

    latest = sidecar.analyze_database(repository.path, latest_cohort=True)
    assert latest["cohort"]["selection"] == "LATEST_COHORT"
    assert latest["cohort"]["config_hash"] == "config-b"
    assert {row["token_id"] for row in latest["rows"]} == {"token-b"}


def test_paired_databases_require_the_same_strategy_source_digest(
    tmp_path: Path,
) -> None:
    white = _single_cohort_repository(
        tmp_path,
        "white.db",
        source_digest="source-a",
        job_name="watermelon-white-1m-v3d",
    )
    grey = _single_cohort_repository(
        tmp_path,
        "grey.db",
        source_digest="source-b",
        job_name="watermelon-grey-5m-v3d",
    )
    _add_eligible_token(
        white,
        run_id="run-watermelon-white-1m-v3d",
        token_id="white-token",
        asks=((0.95, 100.0),),
        bids=((0.94, 100.0),),
    )
    _add_eligible_token(
        grey,
        run_id="run-watermelon-grey-5m-v3d",
        token_id="grey-token",
        asks=((0.95, 100.0),),
        bids=((0.94, 100.0),),
    )

    with pytest.raises(ValueError, match="strategy_source_digest mismatch"):
        sidecar.analyze_databases([white.path, grey.path])


def test_read_only_query_only_source_hash_and_independent_sidecar_digest(
    tmp_path: Path,
) -> None:
    repository = _single_cohort_repository(tmp_path)
    _add_eligible_token(
        repository,
        run_id="run-watermelon-white-1m-v3d",
        token_id="immutable-source",
        asks=((0.95, 100.0),),
        bids=((0.94, 100.0),),
    )
    source_hash_before = hashlib.sha256(repository.path.read_bytes()).hexdigest()
    strategy_digest_before = compute_strategy_source_digest()

    connection = sidecar._connect_read_only(repository.path)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden_write(value TEXT)")
    finally:
        connection.close()

    report = sidecar.analyze_database(repository.path)

    assert hashlib.sha256(repository.path.read_bytes()).hexdigest() == source_hash_before
    assert compute_strategy_source_digest() == strategy_digest_before
    assert "scripts/analyze_depth_ladder.py" not in SOURCE_PATHS
    assert report["read_only"] == {"sqlite_uri_mode": "ro", "query_only": True}
    assert report["sidecar_source_sha256"] == hashlib.sha256(
        Path(sidecar.__file__).read_bytes()
    ).hexdigest()


def test_exact_schema_epoch_is_required(tmp_path: Path) -> None:
    repository = _single_cohort_repository(tmp_path)
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA user_version=999")

    with pytest.raises(ValueError, match="application/schema epoch mismatch"):
        sidecar.analyze_database(repository.path)
