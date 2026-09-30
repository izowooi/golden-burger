"""Reviewed market catalog ownership; source cells retain their exact SQLite values.

Runtime observation clocks, proven resolution and strategy/follow-up evidence are
private. Static description and changing source state use separate immutable
public groups. No JSON parsing, clock generation or defaults occur in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .market_data_projection_profiles import projection_profile, ProjectionGroupValues


@dataclass(frozen=True)
class CatalogColumn:
    name: str
    model_type: str
    nullable: bool = True
    python_default: str | int | None = None
    legacy_nullable: bool | None = None

    @property
    def affinity(self):
        return {
            "String": "TEXT",
            "Integer": "INTEGER",
            "Float": "REAL",
            "DateTime": "NUMERIC",
        }[self.model_type]


def _column(name):
    if name == "condition_id":
        # SQLite TEXT PRIMARY KEY historically allowed NULL declarations. Actual
        # NULL keys cannot identify a public projection and fail publication.
        return CatalogColumn(name, "String", False, legacy_nullable=True)
    if name in {"outcomes_json", "outcome_prices_json", "token_ids_json", "tags_json"}:
        return CatalogColumn(name, "String", False, "[]", legacy_nullable=True)
    if name in {"first_seen_at", "last_seen_at"}:
        return CatalogColumn(
            name, "DateTime", False, "datetime.utcnow", legacy_nullable=True
        )
    if name in {
        "last_live_seen_at",
        "followup_last_attempt_at",
        "followup_next_attempt_at",
        "resolution_observed_at",
    }:
        return CatalogColumn(name, "DateTime")
    if name == "followup_attempt_count":
        return CatalogColumn(name, "Integer", False, 0, legacy_nullable=True)
    if name in {
        "event_market_count",
        "neg_risk",
        "active",
        "closed",
        "accepting_orders",
        "enable_order_book",
        "fees_enabled",
        "fee_exponent",
        "fee_taker_only",
        "last_event_set_complete",
    }:
        return CatalogColumn(name, "Integer")
    if name in {"fee_rate", "resolved_value"}:
        return CatalogColumn(name, "Float")
    return CatalogColumn(name, "String")


@dataclass(frozen=True)
class CatalogProjectionProfile:
    strategy: str
    columns: tuple[CatalogColumn, ...]
    groups: tuple[str, ...]

    @property
    def column_names(self):
        return tuple(column.name for column in self.columns)

    @property
    def public_columns(self):
        return frozenset(
            name for kind in self.groups for name in projection_profile(kind).columns
        )

    @property
    def private_columns(self):
        return tuple(
            name for name in self.column_names if name not in self.public_columns
        )

    @property
    def model_path(self):
        return f"{self.strategy}/src/polybot/db/models.py"

    def __post_init__(self):
        if len(set(self.column_names)) != len(
            self.columns
        ) or not self.public_columns <= set(self.column_names):
            raise ValueError("catalog profile has duplicate or absent source fields")


_BLUEBERRY_COLUMNS = (
    "condition_id",
    "market_id",
    "market_slug",
    "question",
    "event_id",
    "event_slug",
    "event_title",
    "event_market_count",
    "end_date",
    "outcomes_json",
    "outcome_prices_json",
    "token_ids_json",
    "tags_json",
    "neg_risk",
    "active",
    "closed",
    "accepting_orders",
    "enable_order_book",
    "fees_enabled",
    "fee_rate",
    "resolution_status",
    "resolved_outcome",
    "resolved_value",
    "resolved_at",
    "source_updated_at",
    "first_seen_at",
    "last_seen_at",
)
_HONEYDEW_COLUMNS = (
    "condition_id",
    "market_id",
    "market_slug",
    "question",
    "event_id",
    "event_slug",
    "end_date",
    "outcomes_json",
    "token_ids_json",
    "tags_json",
    "fees_enabled",
    "fee_rate",
    "first_seen_at",
    "last_seen_at",
)
_WATERMELON_LIVE_COLUMNS = (
    "condition_id",
    "market_id",
    "market_slug",
    "question",
    "event_id",
    "event_slug",
    "event_title",
    "event_market_count",
    "end_date",
    "outcomes_json",
    "outcome_prices_json",
    "token_ids_json",
    "tags_json",
    "sport_family",
    "league_code",
    "league_name",
    "neg_risk",
    "active",
    "closed",
    "accepting_orders",
    "enable_order_book",
    "fees_enabled",
    "fee_rate",
    "fee_exponent",
    "fee_taker_only",
    "resolution_status",
    "resolved_outcome",
    "resolved_value",
    "resolved_at",
    "source_updated_at",
    "first_seen_at",
    "last_seen_at",
)
_PLUM_COLUMNS = (
    "condition_id",
    "market_id",
    "market_slug",
    "question",
    "event_id",
    "event_slug",
    "event_title",
    "event_market_count",
    "end_date",
    "outcomes_json",
    "outcome_prices_json",
    "token_ids_json",
    "tags_json",
    "neg_risk",
    "active",
    "closed",
    "accepting_orders",
    "enable_order_book",
    "fees_enabled",
    "fee_rate",
    "fee_exponent",
    "fee_taker_only",
    "resolution_status",
    "resolved_outcome",
    "resolved_value",
    "resolved_at",
    "source_updated_at",
    "config_hash",
    "sport_family",
    "league_code",
    "league_name",
    "sport_profile_version",
    "protocol_sha256",
    "classifier_version",
    "league_mapping_sha256",
    "strategy_source_digest",
    "book_shape",
    "last_event_cycle_id",
    "last_event_set_complete",
    "last_event_set_reason",
    "last_live_sweep_id",
    "last_live_seen_at",
    "followup_status",
    "followup_attempt_count",
    "followup_last_attempt_at",
    "followup_next_attempt_at",
    "followup_last_error",
    "resolution_evidence_json",
    "resolution_evidence_sha256",
    "resolution_observed_at",
    "first_seen_at",
    "last_seen_at",
)


def _profile(strategy, names, groups):
    return CatalogProjectionProfile(strategy, tuple(map(_column, names)), groups)


CATALOG_PROFILES = MappingProxyType(
    {
        **{
            "golden-" + name: _profile(
                "golden-" + name,
                _BLUEBERRY_COLUMNS,
                ("catalog-identity-v1", "catalog-state-v1"),
            )
            for name in (
                "blueberry",
                "melon",
                "papaya",
                "queen",
                "quince",
                "kiwi",
                "tangerine",
            )
        },
        **{
            "golden-" + name: _profile(
                "golden-" + name,
                _HONEYDEW_COLUMNS,
                ("catalog-identity-lite-v1", "catalog-state-lite-v1"),
            )
            for name in ("honeydew", "nectarine")
        },
        **{
            "golden-" + name: _profile(
                "golden-" + name,
                _WATERMELON_LIVE_COLUMNS,
                ("catalog-identity-v1", "catalog-state-sports-v1"),
            )
            for name in ("watermelon-live", "apricot", "peach")
        },
        "golden-plum": _profile(
            "golden-plum",
            _PLUM_COLUMNS,
            ("catalog-identity-v1", "catalog-state-plum-v1"),
        ),
    }
)


def catalog_profile(strategy):
    try:
        return CATALOG_PROFILES[strategy]
    except (KeyError, TypeError):
        raise ValueError("unreviewed market catalog strategy") from None


@dataclass(frozen=True)
class SplitCatalog:
    groups: tuple[ProjectionGroupValues, ...]
    private: Mapping


def split_catalog_row(strategy, row):
    profile = catalog_profile(strategy)
    if not isinstance(row, Mapping) or set(row) != set(profile.column_names):
        raise ValueError("catalog row must contain its exact reviewed source fields")
    from .market_data_refs import is_external_value

    if any(is_external_value(row[name]) for name in profile.public_columns):
        raise ValueError("resolve catalog public body references before projection")
    return SplitCatalog(
        tuple(
            ProjectionGroupValues(
                kind,
                projection_profile(kind).columns,
                tuple(row[name] for name in projection_profile(kind).columns),
            )
            for kind in profile.groups
        ),
        MappingProxyType({name: row[name] for name in profile.private_columns}),
    )
