"""Atomic, append-only ``research-full-v1`` SQLite evidence tests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import pytest

from polybot.config import RESEARCH_DATA_CONTRACT
from polybot.collector import MARKET_OBSERVATION_COLUMNS
from polybot.db.repository import FACT_TABLES, ResearchRepository
from polybot.utils.retry import canonical_json


NOW = "2026-08-06T00:00:01+00:00"


def _repository(tmp_path: Path) -> ResearchRepository:
    repository = ResearchRepository(
        tmp_path / "data" / "job" / "trades_sim.db",
        clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    repository.initialize()
    return repository


def _raw_market(condition_id: str = "condition-1") -> dict:
    return {
        "id": "market-1",
        "conditionId": condition_id,
        "eventId": "event-1",
        "outcomes": ["Yes", "No", "Invalid"],
        "clobTokenIds": ["token-yes", "token-no", "token-invalid"],
        "outcomePrices": ["0.51", "0.48", "0.01"],
        "volume": "12345.67",
        "volume24hr": "890.12",
        "closed": False,
        "redeemable": False,
    }


def _bundle(
    *,
    cycle: int = 1,
    sweep_id: str | None = None,
    condition_id: str = "condition-1",
    component_status: str = "SUCCESS",
) -> dict:
    sweep_id = sweep_id or f"sweep-{cycle}"
    run_id = f"run-{cycle}"
    observation_id = f"observation-{cycle}"
    raw_market = _raw_market(condition_id)
    raw_json = canonical_json(raw_market)
    raw_sha = hashlib.sha256(raw_json.encode()).hexdigest()
    membership_shape = [
        {
            "ordinal": 0,
            "page": 1,
            "item": 0,
            "key": condition_id,
            "raw_sha256": raw_sha,
        }
    ]
    membership_digest = hashlib.sha256(
        canonical_json(membership_shape).encode()
    ).hexdigest()
    observation = {
        "observation_id": observation_id,
        "sweep_id": sweep_id,
        "run_id": run_id,
        "cycle_number": cycle,
        "page_number": 1,
        "item_number": 0,
        "page_received_at": NOW,
        "page_request_id": f"request-{cycle}",
        "source_market_key": condition_id,
        "condition_id": condition_id,
        "market_id": "market-1",
        "event_id": "event-1",
        "volume_total_raw": "12345.67",
        "volume_total": 12345.67,
        "volume_24h_raw": "890.12",
        "volume_24h": 890.12,
        "liquidity_variants_json": "{}",
        "outcome_prices_json": '["0.51","0.48","0.01"]',
        "price_changes_json": "{}",
        "tags_json": "[]",
        "sports_json": "{}",
        "fee_metadata_json": "{}",
        "source_clocks_json": "{}",
        "parse_quality_json": "{}",
        "raw_market_sha256": raw_sha,
        "_raw_market_json": raw_json,
    }
    observation_columns = MARKET_OBSERVATION_COLUMNS
    outcome_rows = [
        {
            "outcome_observation_id": f"outcome-{cycle}-{index}",
            "observation_id": observation_id,
            "sweep_id": sweep_id,
            "outcome_index": index,
            "outcome_label": label,
            "token_id": token,
            "price_raw": price,
            "price": float(price),
            "label_present": 1,
            "token_present": 1,
            "price_present": 1,
        }
        for index, (label, token, price) in enumerate(
            zip(
                raw_market["outcomes"],
                raw_market["clobTokenIds"],
                raw_market["outcomePrices"],
            )
        )
    ]
    return {
        "run_id": run_id,
        "cycle_number": cycle,
        "components": [
            {
                "component_run_id": f"component-{cycle}",
                "run_id": run_id,
                "cycle_number": cycle,
                "component": "gamma",
                "status": component_status,
                "started_at": NOW,
                "completed_at": NOW,
                "requested_count": 1,
                "observed_count": 1,
                "error_count": 0 if component_status == "SUCCESS" else 1,
                "possible_gap": 0,
                "details_json": "{}",
                "error_message": None,
            }
        ],
        "market_sweep": {
            "sweep_id": sweep_id,
            "run_id": run_id,
            "cycle_number": cycle,
            "started_at": NOW,
            "completed_at": NOW,
            "cursor_complete": 1,
            "page_count": 1,
            "raw_market_count": 1,
            "unique_condition_count": 1,
            "missing_condition_id_count": 0,
            "duplicate_condition_count": 0,
            "request_attestation_json": "[]",
            "request_attestation_sha256": hashlib.sha256(b"[]").hexdigest(),
            "membership_digest_sha256": membership_digest,
            "raw_payload_page_count": 1,
            "data_contract": RESEARCH_DATA_CONTRACT,
        },
        "raw_payloads": [
            {
                "payload_id": f"gamma-raw-{cycle}",
                "request_id": f"request-{cycle}",
                "payload_kind": "gamma_markets_keyset_page",
                "content_encoding": "zlib",
                "payload_sha256": hashlib.sha256(raw_json.encode()).hexdigest(),
                "uncompressed_bytes": len(raw_json.encode()),
                "compressed_bytes": len(zlib.compress(raw_json.encode(), level=6)),
                "blob_stored": 1,
                "payload_blob": zlib.compress(raw_json.encode(), level=6),
                "recorded_at": NOW,
            }
        ],
        "market_observation_columns": observation_columns,
        "market_observations": [observation],
        "market_memberships": [
            {
                "membership_id": f"membership-{cycle}",
                "sweep_id": sweep_id,
                "observation_id": observation_id,
                "membership_ordinal": 0,
                "page_number": 1,
                "item_number": 0,
                "page_received_at": NOW,
                "source_market_key": condition_id,
                "condition_id": condition_id,
                "market_id": "market-1",
                "event_id": "event-1",
                "raw_market_sha256": raw_sha,
                "duplicate_ordinal": 0,
            }
        ],
        "outcome_observations": outcome_rows,
        "metadata_versions": [
            {
                "metadata_version_id": f"metadata-{cycle}",
                "source_market_key": condition_id,
                "condition_id": condition_id,
                "market_id": "market-1",
                "content_sha256": raw_sha,
                "metadata_json": raw_json,
                "first_observed_sweep_id": sweep_id,
                "first_observed_at": NOW,
            }
        ],
        "watchlist_additions": [
            {
                "condition_id": condition_id,
                "market_id": "market-1",
                "source_market_key": condition_id,
                "first_seen_sweep_id": sweep_id,
                "first_seen_at": NOW,
                "selection_reason": "observed_non_closed_universe",
                "carried_from_utc_date": None,
                "prior_state_json": None,
                "terminal": 0,
            }
        ],
    }


def _publish(repository: ResearchRepository, bundle: dict) -> None:
    """Publish a fixture after creating the request evidence its raw pages reference."""
    with sqlite3.connect(repository.db_path) as connection:
        existing = {
            row[0] for row in connection.execute("SELECT request_id FROM api_requests")
        }
    for raw in bundle.get("raw_payloads", []):
        request_id = raw["request_id"]
        if request_id in existing:
            continue
        repository.record_api_request(
            {
                "request_id": request_id,
                "run_id": bundle["run_id"],
                "sweep_attempt_id": bundle["market_sweep"]["sweep_id"],
                "request_kind": "gamma_markets_keyset",
                "page_number": 1,
                "attempt_number": 1,
                "method": "GET",
                "url": "https://gamma-api.polymarket.com/markets/keyset",
                "params_json": '{"closed":"false"}',
                "body_sha256": None,
                "request_hash": f"request-hash-{request_id}",
                "started_at": NOW,
                "completed_at": NOW,
                "elapsed_ms": 1.0,
                "status": "SUCCESS",
                "http_status": 200,
                "retryable": 0,
                "retry_after_seconds": None,
                "response_sha256": raw["payload_sha256"],
                "response_bytes": raw["uncompressed_bytes"],
                "error_type": None,
                "error_message": None,
            }
        )
        existing.add(request_id)
    repository.publish_cycle(bundle)


def _count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_initialize_marks_research_full_and_installs_append_only_guards(tmp_path):
    repository = _repository(tmp_path)

    with sqlite3.connect(repository.db_path) as connection:
        contract = connection.execute(
            "SELECT contract_name, metadata_json FROM collection_contracts"
        ).fetchone()
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }

    assert contract[0] == "research-full-v1"
    metadata = json.loads(contract[1])
    assert metadata["fact_policy"] == "append-only evidence"
    assert metadata["compact_v1"] is False
    for table in FACT_TABLES:
        assert f"{table}_append_only_update" in triggers
        assert f"{table}_append_only_delete" in triggers


def test_complete_sweep_preserves_every_membership_outcome_and_volume_field(tmp_path):
    repository = _repository(tmp_path)

    _publish(repository, _bundle())

    assert _count(repository.db_path, "market_sweeps") == 1
    assert _count(repository.db_path, "market_observations") == 1
    assert _count(repository.db_path, "market_sweep_memberships") == 1
    assert _count(repository.db_path, "outcome_observations") == 3
    assert _count(repository.db_path, "market_metadata_versions") == 1
    assert _count(repository.db_path, "resolution_watchlist") == 1
    with sqlite3.connect(repository.db_path) as connection:
        volume = connection.execute(
            "SELECT volume_total_raw, volume_total, volume_24h_raw, volume_24h "
            "FROM market_observations"
        ).fetchone()
        outcomes = connection.execute(
            "SELECT outcome_index, outcome_label, token_id FROM outcome_observations "
            "ORDER BY outcome_index"
        ).fetchall()
    assert volume == ("12345.67", 12345.67, "890.12", 890.12)
    assert outcomes == [
        (0, "Yes", "token-yes"),
        (1, "No", "token-no"),
        (2, "Invalid", "token-invalid"),
    ]


def test_any_gamma_bundle_insert_failure_rolls_back_every_census_fact(tmp_path):
    repository = _repository(tmp_path)
    bundle = _bundle()
    bundle["outcome_observations"][1]["outcome_observation_id"] = bundle[
        "outcome_observations"
    ][0]["outcome_observation_id"]

    with pytest.raises(sqlite3.IntegrityError):
        _publish(repository, bundle)

    for table in (
        "source_component_runs",
        "market_sweeps",
        "market_observations",
        "market_sweep_memberships",
        "outcome_observations",
        "market_metadata_versions",
    ):
        assert _count(repository.db_path, table) == 0


def test_gamma_raw_payload_is_rolled_back_with_a_failed_census_bundle(tmp_path):
    repository = _repository(tmp_path)
    repository.record_api_request(
        {
            "request_id": "gamma-request",
            "run_id": "run-1",
            "sweep_attempt_id": "sweep-1",
            "request_kind": "gamma_markets_keyset",
            "page_number": 1,
            "attempt_number": 1,
            "method": "GET",
            "url": "https://gamma-api.polymarket.com/markets/keyset",
            "params_json": "{}",
            "body_sha256": None,
            "request_hash": "request-hash",
            "started_at": NOW,
            "completed_at": NOW,
            "elapsed_ms": 1.0,
            "status": "SUCCESS",
            "http_status": 200,
            "retryable": 0,
            "retry_after_seconds": None,
            "response_sha256": "response-hash",
            "response_bytes": 2,
            "error_type": None,
            "error_message": None,
        }
    )
    bundle = _bundle()
    raw = b"{}"
    compressed = zlib.compress(raw, level=6)
    bundle["raw_payloads"] = [
        {
            "payload_id": "gamma-raw",
            "request_id": "gamma-request",
            "payload_kind": "gamma_markets_keyset_page",
            "content_encoding": "zlib",
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
            "uncompressed_bytes": len(raw),
            "compressed_bytes": len(compressed),
            "blob_stored": 1,
            "payload_blob": compressed,
            "recorded_at": NOW,
        }
    ]
    bundle["outcome_observations"][1]["outcome_observation_id"] = bundle[
        "outcome_observations"
    ][0]["outcome_observation_id"]

    with pytest.raises(sqlite3.IntegrityError):
        _publish(repository, bundle)

    assert _count(repository.db_path, "api_requests") == 1
    assert _count(repository.db_path, "raw_payloads") == 0
    assert _count(repository.db_path, "market_sweeps") == 0


@pytest.mark.parametrize("missing_part", ["membership", "outcome"])
def test_incomplete_market_or_outcome_membership_is_rejected_before_publish(
    tmp_path, missing_part
):
    repository = _repository(tmp_path)
    bundle = _bundle()
    if missing_part == "membership":
        bundle["market_memberships"] = []
    else:
        bundle["outcome_observations"] = bundle["outcome_observations"][:-1]

    with pytest.raises(
        (ValueError, sqlite3.IntegrityError), match="membership|outcome|count"
    ):
        _publish(repository, bundle)

    assert _count(repository.db_path, "market_sweeps") == 0
    assert _count(repository.db_path, "market_observations") == 0


def test_secondary_error_component_is_committed_without_discarding_gamma(tmp_path):
    repository = _repository(tmp_path)
    bundle = _bundle()
    bundle["components"].append(
        {
            "component_run_id": "clob-error",
            "run_id": "run-1",
            "cycle_number": 1,
            "component": "clob_books",
            "status": "ERROR",
            "started_at": NOW,
            "completed_at": NOW,
            "requested_count": 2,
            "observed_count": 0,
            "error_count": 2,
            "possible_gap": 1,
            "details_json": '{"reason":"public endpoint unavailable"}',
            "error_message": "public endpoint unavailable",
        }
    )

    _publish(repository, bundle)

    assert _count(repository.db_path, "market_sweeps") == 1
    with sqlite3.connect(repository.db_path) as connection:
        statuses = connection.execute(
            "SELECT component, status FROM source_component_runs ORDER BY component"
        ).fetchall()
    assert statuses == [("clob_books", "ERROR"), ("gamma", "SUCCESS")]


def test_existing_facts_cannot_be_compacted_updated_or_deleted(tmp_path):
    repository = _repository(tmp_path)
    _publish(repository, _bundle())

    with sqlite3.connect(repository.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE market_observations SET volume_total = 0")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM market_observations")

    assert _count(repository.db_path, "market_observations") == 1


def test_unchanged_metadata_is_deduped_but_changed_metadata_adds_a_version(tmp_path):
    repository = _repository(tmp_path)
    first = _bundle(cycle=1)
    _publish(repository, first)

    second = _bundle(cycle=2)
    second["metadata_versions"][0]["metadata_version_id"] = "duplicate-content"
    _publish(repository, second)
    assert _count(repository.db_path, "market_metadata_versions") == 1

    third = _bundle(cycle=3)
    changed = json.loads(third["metadata_versions"][0]["metadata_json"])
    changed["volume24hr"] = "999.99"
    changed_json = canonical_json(changed)
    third["metadata_versions"][0]["metadata_json"] = changed_json
    third["metadata_versions"][0]["content_sha256"] = hashlib.sha256(
        changed_json.encode()
    ).hexdigest()
    _publish(repository, third)

    assert _count(repository.db_path, "market_metadata_versions") == 2


def test_resolution_and_redeemable_are_persisted_as_independent_values(tmp_path):
    repository = _repository(tmp_path)
    bundle = _bundle()
    bundle["resolution_observations"] = [
        {
            "resolution_observation_id": "resolution-1",
            "run_id": "run-1",
            "cycle_number": 1,
            "condition_id": "condition-1",
            "requested_at": NOW,
            "observed_at": NOW,
            "lookup_status": "OBSERVED",
            "request_id": None,
            "market_id": "market-1",
            "closed": 1,
            "one_hot": 1,
            "one_hot_outcome_index": 0,
            "one_hot_outcome_label": "Yes",
            "resolution_value_raw": "Yes",
            "redeemable": 0,
            "outcome_prices_json": '["1","0","0"]',
            "raw_market_sha256": None,
            "raw_market_json": None,
            "error_type": None,
            "error_message": None,
        },
        {
            "resolution_observation_id": "resolution-2",
            "run_id": "run-1",
            "cycle_number": 1,
            "condition_id": "condition-1",
            "requested_at": NOW,
            "observed_at": "2026-08-06T00:15:01+00:00",
            "lookup_status": "OBSERVED",
            "request_id": None,
            "market_id": "market-1",
            "closed": 1,
            "one_hot": 1,
            "one_hot_outcome_index": 0,
            "one_hot_outcome_label": "Yes",
            "resolution_value_raw": "Yes",
            "redeemable": 1,
            "outcome_prices_json": '["1","0","0"]',
            "raw_market_sha256": None,
            "raw_market_json": None,
            "error_type": None,
            "error_message": None,
        },
    ]

    _publish(repository, bundle)

    with sqlite3.connect(repository.db_path) as connection:
        observations = connection.execute(
            "SELECT closed, one_hot, redeemable FROM resolution_observations "
            "ORDER BY observed_at"
        ).fetchall()
    assert observations == [(1, 1, 0), (1, 1, 1)]


def test_trade_schema_has_only_allowlisted_trade_not_profile_fields(tmp_path):
    repository = _repository(tmp_path)

    with sqlite3.connect(repository.db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(trade_observations)")
        }

    assert {"side", "asset", "condition_id", "price", "size", "proxy_wallet"} <= columns
    assert (
        not {
            "name",
            "pseudonym",
            "bio",
            "profile_image",
            "title",
            "slug",
            "icon",
            "event_slug",
        }
        & columns
    )
