"""Catalog field accounting is checked against every actual strategy model."""

import ast
from pathlib import Path

import pytest

from polybot_observability.market_data_catalog_profiles import (
    CATALOG_PROFILES,
    catalog_profile,
    split_catalog_row,
)
from polybot_observability.market_data_projections import PublicProjection

ROOT = Path(__file__).resolve().parents[2]


def model_columns(strategy):
    tree = ast.parse((ROOT / catalog_profile(strategy).model_path).read_text())
    model = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MarketCatalog"
    )
    result = []
    for node in model.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "Column"
        ):
            keywords = {key.arg: key.value for key in node.value.keywords}
            name = node.targets[0].id
            nullable = (
                ast.literal_eval(keywords["nullable"])
                if "nullable" in keywords
                else not ast.literal_eval(
                    keywords.get("primary_key", ast.Constant(False))
                )
            )
            default = keywords.get("default")
            default = (
                ast.literal_eval(default)
                if isinstance(default, ast.Constant)
                else ast.unparse(default)
                if default
                else None
            )
            result.append(
                (
                    name,
                    ast.unparse(node.value.args[0]),
                    nullable,
                    default,
                    ast.literal_eval(keywords.get("index", ast.Constant(False))),
                )
            )
    return result


@pytest.mark.parametrize("strategy", CATALOG_PROFILES)
def test_all_thirteen_catalog_models_are_exactly_accounted_for(strategy):
    profile = catalog_profile(strategy)
    actual = model_columns(strategy)
    assert [
        (column.name, column.model_type, column.nullable, column.python_default)
        for column in profile.columns
    ] == [row[:4] for row in actual]
    assert profile.public_columns | set(profile.private_columns) == set(
        profile.column_names
    )
    assert not profile.public_columns & set(profile.private_columns)
    assert {"condition_id", "fees_enabled", "fee_rate"} <= profile.public_columns
    assert {"first_seen_at", "last_seen_at"} <= set(profile.private_columns)
    if strategy in {
        "golden-watermelon-live",
        "golden-apricot",
        "golden-peach",
        "golden-plum",
    }:
        assert {"fee_exponent", "fee_taker_only"} <= profile.public_columns
        assert {"sport_family", "league_code", "league_name"} <= set(
            profile.private_columns
        )
    if strategy == "golden-plum":
        assert {
            "active",
            "closed",
            "accepting_orders",
            "resolved_at",
            "followup_status",
            "resolution_evidence_json",
        } <= set(profile.private_columns)
    elif "active" in profile.column_names:
        assert {
            "active",
            "closed",
            "accepting_orders",
            "resolved_at",
        } <= profile.public_columns


@pytest.mark.parametrize("strategy", CATALOG_PROFILES)
def test_group_split_preserves_null_and_raw_json_and_excludes_runtime_private_cells(
    strategy,
):
    profile = catalog_profile(strategy)
    values = dict.fromkeys(profile.column_names)
    values.update(
        condition_id="condition",
        outcomes_json='  [ "Yes", "No" ] ',
        token_ids_json='"[\\"token\\"]"',
        tags_json="[]",
        fees_enabled=1,
        fee_rate=0.0123,
        first_seen_at="PRIVATE_OBSERVER_CLOCK",
        last_seen_at="PRIVATE_LATEST_CLOCK",
    )
    for name in ("config_hash", "resolution_evidence_json", "resolution_status"):
        if name in values:
            values[name] = "PRIVATE_PROOF_SENTINEL"
    split = split_catalog_row(strategy, values)
    restored = {}
    for group in split.groups:
        projection = PublicProjection(group.kind, group.values)
        restored.update(zip(group.columns, projection.row()))
        assert all(
            "PRIVATE_" not in cell for cell in projection.row() if isinstance(cell, str)
        )
    assert restored == {name: values[name] for name in profile.public_columns}
    assert dict(split.private) == {
        name: values[name] for name in profile.private_columns
    }
    with pytest.raises(ValueError, match="exact"):
        split_catalog_row(strategy, {**values, "unknown_private": "not accepted"})
