from __future__ import annotations

import ast
from pathlib import Path
import re
import sqlite3

import pytest

from polybot_observability.market_data_policy import (
    KNOWN_STRATEGIES,
    PUBLIC_COLUMN_RULES,
    PUBLIC_LEVEL_PROJECTIONS,
    public_columns,
    public_level_projections,
    public_level_tables,
    retained_payloads,
    schema_inspections,
)


REPO = Path(__file__).resolve().parents[2]


def test_every_golden_project_has_an_explicit_coverage_record():
    actual = frozenset(p.name for p in REPO.glob("golden-*") if (p / "pyproject.toml").is_file())
    assert KNOWN_STRATEGIES == actual
    covered = {r.strategy for r in PUBLIC_COLUMN_RULES}
    covered.update(s for s in KNOWN_STRATEGIES if schema_inspections(s))
    assert covered == actual


@pytest.mark.parametrize("strategy,table", [
    ("unknown", "raw_payloads"),
    ("golden-plum", "new_public_json_table"),
    ("golden-plum:king", "market_catalog"),
    ("golden-PLUM", "market_catalog"),
    ("golden-apple", "raw_payloads"),
])
def test_unknown_names_fail_closed_without_suffix_or_namespace_guessing(strategy, table):
    assert public_columns(strategy, table) == frozenset()


@pytest.mark.parametrize("table", [
    "trades", "order_submissions", "order_fills", "order_status_events",
    "strategy_configs", "research_config_versions", "run_audits",
    "entry_episodes", "entry_signal_decisions", "signal_decisions",
    "experiment_state", "features", "guava_execution_events", "guava_position_plans",
])
def test_private_ledgers_configs_and_decisions_are_never_body_sources(table):
    assert all(not public_columns(s, table) for s in KNOWN_STRATEGIES)


def test_named_fee_and_derived_payloads_stay_local():
    forbidden = {
        "config_json", "receipt_json", "params_json", "fee_json",
        "fee_schedule_json", "fee_metadata_json", "fee_evidence_json",
        "depth_metrics_json", "execution_capacity_json", "details_json",
        "exclusion_counts_json", "classification_evidence_json", "evidence_json",
        "resolution_jump_without_target_json", "trend_prices_json",
    }
    assert all(not (r.columns & forbidden) for r in PUBLIC_COLUMN_RULES)


@pytest.mark.parametrize("strategy,table,expected", [
    ("golden-plum", "market_snapshots", {"book_json", "market_tags_json"}),
    ("golden-apricot", "raw_book_observations", {"book_json"}),
    ("golden-peach", "raw_book_observations", {"book_json"}),
    ("golden-black", "raw_payloads", {"payload_gzip"}),
    ("golden-watermelon", "raw_payloads", {"payload_gzip"}),
    ("golden-coconut", "requests", {"raw_gzip"}),
    ("golden-coconut", "book_observations", {"book_gzip"}),
    ("golden-coconut", "book_snapshots", {"book_gzip"}),
    ("golden-coconut", "clock_observations", {"raw_gzip"}),
    ("golden-pomegranate", "raw_payloads", {"payload_blob"}),
    ("golden-raspberry", "raw_payloads", {"payload_blob"}),
    ("golden-strawberry", "compact_books", {"book_blob"}),
    ("golden-strawberry", "resolution_observations", {"market_blob"}),
    ("golden-cherry", "shadow_raw_payloads", {"payload_gzip"}),
    ("golden-guava", "source_requests", {"payload_gzip"}),
    ("golden-guava", "book_attempts", {"raw_gzip"}),
])
def test_known_heavy_source_bodies_are_covered(strategy, table, expected):
    assert public_columns(strategy, table) == frozenset(expected)


def test_same_table_name_does_not_erase_ownership_boundaries():
    assert public_columns("golden-pomegranate", "resolution_observations") == {
        "outcome_prices_json", "raw_market_json",
    }
    assert not public_columns("golden-plum", "resolution_observations")
    assert not public_columns("golden-watermelon-live", "resolution_observations")
    assert public_columns("golden-coconut", "event_observations") == {
        "raw_lifecycle_json", "event_json", "clock_json",
    }
    assert not public_columns("golden-guava", "event_observations")


def test_mixed_envelopes_are_reportable_exclusions_not_silently_public():
    for strategy, table, column in (
        ("golden-apple", "runs", "frame"),
        ("golden-apple", "payloads", "compressed"),
        ("golden-guava", "events", "event_json"),
        ("golden-guava", "book_attempts", "book_json"),
        ("golden-raspberry", "market_sweeps", "membership_blob"),
        ("golden-strawberry", "market_membership_blobs", "membership_blob"),
    ):
        assert column not in public_columns(strategy, table)
        matching = [r for r in retained_payloads(strategy) if r.table == table and column in r.columns]
        assert matching and all(r.reason and r.source_schema for r in matching)


def test_apple_remote_collection_schema_is_not_confused_with_legacy_trader():
    remote = [r for r in schema_inspections("golden-apple") if r.source_schema.startswith("remote:")]
    assert any(r.source_schema.endswith("compact_store.py") for r in remote)
    assert all(re.fullmatch("[a-f0-9]{64}", r.source_sha256 or "") for r in remote)
    assert not public_columns("golden-apple", "runs")
    assert not public_columns("golden-apple", "market_snapshots")
    assert public_columns("golden-apple", "market_observations") == {"outcome_prices"}


def test_raw_level_table_keys_and_mixed_raspberry_flags():
    assert {r.table for r in public_level_tables("golden-pomegranate")} == {"orderbook_levels"}
    pomegranate = public_level_tables("golden-pomegranate")[0]
    assert {"price_raw", "size_raw"}.issubset(pomegranate.columns)
    assert pomegranate.primary_key == ("level_id",)
    assert pomegranate.parent_table == "orderbook_snapshots"
    assert pomegranate.parent_key == pomegranate.parent_columns == ("snapshot_id",)
    assert pomegranate.natural_key == ("snapshot_id", "side", "level_index")
    assert not public_level_tables("golden-raspberry")
    raspberry = public_level_projections("golden-raspberry")[0]
    assert raspberry.retained_columns == {"in_near_touch_window", "used_for_entry"}
    assert not set(raspberry.columns) & raspberry.retained_columns
    # Coconut's similarly named table is a simulated notional walk, not a ladder.
    assert not public_level_projections("golden-coconut")
    assert not public_level_tables("unknown")


def _declared_columns(path: Path, table: str) -> set[str]:
    """Check policy against real DDL/model declarations without importing a bot."""
    source = path.read_text()
    if path.suffix == ".py":
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            assignments = {
                s.targets[0].id: s.value for s in node.body
                if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name)
            }
            name = assignments.get("__tablename__")
            if isinstance(name, ast.Constant) and name.value == table:
                return {
                    k for k, value in assignments.items()
                    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Column"
                }
    match = re.search(r"CREATE TABLE(?: IF NOT EXISTS)?\s+" + re.escape(table) + r"\s*\(", source)
    assert match is not None, (path, table)
    depth, quote, end = 1, None, match.end()
    while depth:
        char = source[end]
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        end += 1
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(source[match.start():end])
        return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    finally:
        connection.close()


def test_allowlisted_columns_exist_in_their_recorded_local_schema():
    for rule in PUBLIC_COLUMN_RULES:
        assert rule.strategy in KNOWN_STRATEGIES
        assert rule.columns and rule.reason
        if rule.source_schema.startswith("remote:"):
            continue  # Remote source hashes are inspection evidence, not a test network dependency.
        assert rule.columns <= _declared_columns(REPO / rule.source_schema, rule.table), rule


def test_level_projection_completely_accounts_for_actual_schema_columns():
    for rule in PUBLIC_LEVEL_PROJECTIONS:
        declared = _declared_columns(REPO / rule.source_schema, rule.table)
        assert declared == set(rule.columns) | rule.retained_columns, rule
        assert set(rule.primary_key + rule.parent_key + rule.natural_key) <= set(rule.columns)
        assert len(rule.parent_key) == len(rule.parent_columns)
        assert set(rule.parent_columns) <= _declared_columns(REPO / rule.source_schema, rule.parent_table)
