import ast
from pathlib import Path
import sqlite3

import pytest

from polybot_observability.market_data_projection_profiles import (
    MAX_PROJECTION_JSON_BYTES, PROJECTION_PROFILES, SNAPSHOT_PROFILES,
    ProjectionField, ProjectionProfile, projection_profile, snapshot_profile,
    split_snapshot_row, validate_private_updates,
)
from polybot_observability.market_data_refs import PREFIX


ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = {
    "golden-blueberry": 11, "golden-honeydew": 11, "golden-melon": 11,
    "golden-nectarine": 11, "golden-papaya": 11, "golden-queen": 11,
    "golden-quince": 11, "golden-kiwi": 25, "golden-tangerine": 13,
    "golden-watermelon-live": 17, "golden-apricot": 27, "golden-peach": 27,
    "golden-plum": 35,
}


def model_columns(strategy):
    """Inspect declarations without importing a strategy or its credentials."""
    path = ROOT / snapshot_profile(strategy).model_path
    tree = ast.parse(path.read_text())
    model = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "MarketSnapshot")
    result = []
    for node in model.body:
        if (not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call)
                or not isinstance(node.value.func, ast.Name) or node.value.func.id != "Column"):
            continue
        call = node.value
        keywords = {item.arg: item.value for item in call.keywords}
        primary_key = ast.literal_eval(keywords["primary_key"]) if "primary_key" in keywords else False
        nullable = ast.literal_eval(keywords["nullable"]) if "nullable" in keywords else not primary_key
        default = keywords.get("default")
        if isinstance(default, ast.Constant):
            default = default.value
        elif default is not None:
            default = ast.unparse(default)
        assert "server_default" not in keywords
        if primary_key:
            assert node.targets[0].id == "id"
            assert ast.literal_eval(keywords["autoincrement"]) is True
        result.append((node.targets[0].id, call.args[0].id, nullable, default))
    unique = tuple(tuple(arg.value for arg in node.args) for node in ast.walk(model)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "UniqueConstraint")
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "ForeignKey" for node in ast.walk(model))
    return result, unique


def row_for(strategy):
    result = {name: None for name in snapshot_profile(strategy).column_names}
    result.update(id=17, condition_id="condition-서울", probability=.71,
                  liquidity=0.0, volume_24h=230.0, best_bid=.70, best_ask=.72,
                  spread=.02, source_updated_at=" 2026-09-30T00:00:00+00:00 ",
                  run_id="private-run", timestamp="2026-09-30 01:02:03.000004")
    return result


def test_registry_has_exactly_the_reviewed_thirteen_strategies():
    assert set(SNAPSHOT_PROFILES) == set(STRATEGIES)
    assert {kind for profile in SNAPSHOT_PROFILES.values() for kind in profile.groups} == {
        "gamma-quote-v1", "token-quote-v1", "event-token-quote-v1", "kiwi-catalog-v1",
    }
    with pytest.raises(TypeError):
        SNAPSHOT_PROFILES["golden-other"] = snapshot_profile("golden-blueberry")


@pytest.mark.parametrize("strategy,count", STRATEGIES.items())
def test_actual_ast_schema_order_defaults_nullability_and_ownership(strategy, count):
    profile = snapshot_profile(strategy)
    actual, unique = model_columns(strategy)
    assert len(actual) == count
    assert actual == [(column.name, column.model_type, column.nullable, column.python_default)
                      for column in profile.columns]
    assert unique == profile.unique_constraints
    public, private, bodies = profile.public_columns, set(profile.private_columns), set(profile.body_columns)
    assert set(profile.column_names) == public | private | bodies
    assert not public & private and not public & bodies and not private & bodies
    assert {"id", "run_id", "timestamp"} <= private
    assert profile.mutable_private_fields <= private


def test_kind_column_order_and_source_clock_are_explicit():
    gamma = projection_profile("gamma-quote-v1")
    assert gamma.columns == (
        "condition_id", "probability", "liquidity", "volume_24h", "best_bid",
        "best_ask", "spread", "source_updated_at",
    )
    token = projection_profile("token-quote-v1")
    assert token.columns == (
        "condition_id", "token_id", "outcome", "liquidity", "volume_24h",
        "best_bid", "best_ask", "spread", "source_updated_at",
    )
    event = projection_profile("event-token-quote-v1")
    assert event.columns == ("condition_id", "event_id", *token.columns[1:])
    for profile in (gamma, token, event):
        assert profile.source_time_field == "source_updated_at"
        assert profile.fields[-1].storage == "TEXT"
        assert "timestamp" not in profile.columns


def test_kiwi_catalog_is_a_separate_group_and_unchanged_quotes_do_not_duplicate_it():
    row = row_for("golden-kiwi")
    lexical_json = ' [ "Yes" , "No" ]\n'
    row.update(catalog_outcomes_json=lexical_json, catalog_neg_risk=0,
               catalog_active=1, catalog_closed=None, catalog_event_market_count=0)
    first = split_snapshot_row("golden-kiwi", row)
    changed = split_snapshot_row("golden-kiwi", {**row, "probability": .8, "timestamp": "later"})
    assert [group.kind for group in first.groups] == ["gamma-quote-v1", "kiwi-catalog-v1"]
    assert first.groups[0] != changed.groups[0]
    assert first.groups[1] == changed.groups[1]
    catalog = dict(zip(first.groups[1].columns, first.groups[1].values, strict=True))
    assert len(catalog) == 15
    assert catalog["catalog_outcomes_json"] == lexical_json
    assert type(catalog["catalog_neg_risk"]) is int and catalog["catalog_neg_risk"] == 0
    assert type(catalog["catalog_active"]) is int and catalog["catalog_active"] == 1
    assert catalog["catalog_closed"] is None and catalog["catalog_event_market_count"] == 0
    assert not first.bodies
    fields = projection_profile("kiwi-catalog-v1").fields
    assert all(field.max_bytes == MAX_PROJECTION_JSON_BYTES for field in fields if field.name.endswith("_json"))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_split_is_lossless_without_default_injection_or_private_leakage(strategy):
    row = row_for(strategy)
    profile = snapshot_profile(strategy)
    for name in profile.private_columns:
        if name not in {"id", "probability", "timestamp"}:
            row[name] = "PRIVATE_SENTINEL"
    split = split_snapshot_row(strategy, row)
    restored = dict(split.private) | dict(split.bodies)
    for group in split.groups:
        restored.update(zip(group.columns, group.values, strict=True))
        assert "PRIVATE_SENTINEL" not in group.values
    assert restored == row
    assert split.private["timestamp"] == row["timestamp"]
    assert all("timestamp" not in group.columns for group in split.groups)
    with pytest.raises(TypeError):
        split.private["timestamp"] = "replaced"


@pytest.mark.parametrize("strategy", ["golden-tangerine", "golden-watermelon-live", "golden-apricot", "golden-peach", "golden-plum"])
def test_sports_vwap_config_classification_and_mutable_health_remain_private(strategy):
    row = row_for(strategy)
    row["probability"] = .7133333333333334
    split = split_snapshot_row(strategy, row)
    assert split.private["probability"] == row["probability"]
    assert all("probability" not in group.columns for group in split.groups)
    private = set(snapshot_profile(strategy).private_columns)
    for name in ("execution_capacity_json", "outcome_side", "result_kind", "midpoint",
                 "source_elapsed_minutes", "source_clock_reason", "config_hash",
                 "sport_family", "league_code", "league_name", "event_set_complete",
                 "event_set_reason", "strategy_source_digest"):
        if name in row:
            assert name in private


def test_existing_body_cas_path_is_kept_but_catalog_projection_requires_logical_cells():
    marker = PREFIX + "T:" + "a" * 64
    row = row_for("golden-plum")
    row.update(book_json=marker, market_tags_json=marker)
    split = split_snapshot_row("golden-plum", row)
    assert dict(split.bodies) == {"book_json": marker, "market_tags_json": marker}
    kiwi = row_for("golden-kiwi")
    kiwi["catalog_outcomes_json"] = marker
    with pytest.raises(ValueError, match="resolve public body references"):
        split_snapshot_row("golden-kiwi", kiwi)


def test_missing_unknown_and_private_execution_columns_fail_closed():
    row = row_for("golden-blueberry")
    for changed in ({key: value for key, value in row.items() if key != "run_id"},
                    {**row, "confirmed_fill_price": .75}, {**row, "config_json": "private"}):
        with pytest.raises(ValueError, match="fields differ"):
            split_snapshot_row("golden-blueberry", changed)
    with pytest.raises(TypeError):
        split_snapshot_row("golden-blueberry", tuple(row.values()))
    for name in ("unknown", None, []):
        with pytest.raises(ValueError):
            snapshot_profile(name)
        with pytest.raises(ValueError):
            projection_profile(name)


def test_only_observed_private_update_paths_are_allowed():
    for strategy in STRATEGIES:
        profile = snapshot_profile(strategy)
        validate_private_updates(strategy, {})
        if strategy in {"golden-honeydew", "golden-nectarine"}:
            assert not profile.mutable_private_fields
        else:
            validate_private_updates(strategy, {"timestamp": "same-cycle-source-reference"})
        for key in ("probability", "run_id", "condition_id", "best_bid", "unknown"):
            with pytest.raises(ValueError):
                validate_private_updates(strategy, {key: None})
    validate_private_updates("golden-plum", {"event_set_complete": 1, "event_set_reason": "COMPLETE"})
    with pytest.raises(ValueError):
        validate_private_updates("golden-apricot", {"event_set_complete": 1})


def test_legacy_nullable_token_columns_and_sqlite_affinities_are_preserved():
    profile = snapshot_profile("golden-tangerine")
    contracts = {column.name: column for column in profile.columns}
    for name in ("token_id", "outcome"):
        assert contracts[name].nullable is False
        assert contracts[name].legacy_nullable is True
        assert next(field for field in projection_profile(profile.groups[0]).fields if field.name == name).nullable
    assert contracts["timestamp"].affinity == "NUMERIC"
    assert contracts["probability"].affinity == "REAL"
    assert contracts["id"].affinity == "INTEGER"
    assert contracts["token_id"].affinity == "TEXT"
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE legacy (token_id TEXT, outcome TEXT, source_updated_at TEXT)")
        connection.execute("INSERT INTO legacy VALUES(NULL,NULL,' 2026-09-30Z ')")
        cells = dict(connection.execute("SELECT * FROM legacy").fetchone())
    row = row_for("golden-tangerine") | cells
    split = split_snapshot_row("golden-tangerine", row)
    public = dict(zip(split.groups[0].columns, split.groups[0].values, strict=True))
    assert public["token_id"] is None and public["outcome"] is None
    assert public["source_updated_at"] == " 2026-09-30Z "


def test_profile_definition_guards():
    with pytest.raises(ValueError):
        ProjectionField("field", "BLOB")
    with pytest.raises(ValueError):
        ProjectionProfile("bad-v1", (ProjectionField("a", "TEXT"),))
    with pytest.raises(ValueError):
        ProjectionProfile("bad-v1", (ProjectionField("condition_id", "TEXT"),) * 2)
