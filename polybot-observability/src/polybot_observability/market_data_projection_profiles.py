"""Reviewed public projections of extended strategy snapshot schemas.

This registry has no storage, network or SQLAlchemy dependency. It describes
logical source cells; a projection adapter must resolve existing body references
before extracting them. Runtime timestamps, local IDs and strategy evidence are
kept in the owning database. Registering a profile does not migrate that database.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


MAX_PROJECTION_TEXT_BYTES = 4096
MAX_PROJECTION_JSON_BYTES = 64 << 20


@dataclass(frozen=True)
class ProjectionField:
    name: str
    storage: str
    nullable: bool = True
    max_bytes: int = MAX_PROJECTION_TEXT_BYTES

    def __post_init__(self):
        if not self.name or self.storage not in {"TEXT", "REAL", "INTEGER", "SCALAR"}:
            raise ValueError("unsupported public projection field")
        if type(self.nullable) is not bool or type(self.max_bytes) is not int or self.max_bytes <= 0:
            raise ValueError("invalid public projection field bounds")


@dataclass(frozen=True)
class ProjectionProfile:
    kind: str
    fields: tuple[ProjectionField, ...]
    condition_field: str | None = "condition_id"
    token_field: str | None = None
    source_time_field: str | None = None

    def __post_init__(self):
        names = self.columns
        if not self.kind or not names or len(names) != len(set(names)):
            raise ValueError("invalid public projection kind fields")
        for name in (self.condition_field, self.token_field, self.source_time_field):
            if name is not None and name not in names:
                raise ValueError("public projection index field is absent")
        for name in (self.condition_field, self.token_field):
            if name is not None and self.fields[names.index(name)].storage != "TEXT":
                raise ValueError("public projection subjects must be exact TEXT fields")

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)


def _text(name, *, nullable=True, json_body=False):
    return ProjectionField(name, "TEXT", nullable,
                           MAX_PROJECTION_JSON_BYTES if json_body else MAX_PROJECTION_TEXT_BYTES)


_QUOTE_VALUES = tuple(ProjectionField(name, "REAL") for name in
                      ("liquidity", "volume_24h", "best_bid", "best_ask", "spread"))
_CONDITION = _text("condition_id", nullable=False)
_SOURCE_TIME = _text("source_updated_at")

# token/outcome are nullable here because additive legacy upgrades used plain
# TEXT, although newly created ORM tables declare NOT NULL Python defaults.
_TOKEN_FIELDS = (_text("token_id"), _text("outcome"))
_KIWI_CATALOG_FIELDS = (
    _text("catalog_event_id"), _text("catalog_event_slug"),
    ProjectionField("catalog_event_market_count", "INTEGER"),
    _text("catalog_end_date"),
    *(_text(name, json_body=True) for name in (
        "catalog_outcomes_json", "catalog_outcome_prices_json",
        "catalog_token_ids_json", "catalog_tags_json")),
    *(ProjectionField(name, "INTEGER") for name in (
        "catalog_neg_risk", "catalog_active", "catalog_closed",
        "catalog_accepting_orders", "catalog_enable_order_book")),
    _text("catalog_source_updated_at"),
)

# Catalog source groups are separate from snapshot-owned Kiwi catalog cells.
# Long descriptive/JSON source strings retain lexical bytes under the shared
# per-projection bound; identifiers retain the usual key bound.
_CATALOG_IDENTITY = (_CONDITION, *(_text(name, json_body=True) for name in
    ("market_id", "market_slug", "question", "event_id", "event_slug", "event_title")),
    ProjectionField("event_market_count", "INTEGER"), _text("end_date"),
    *(_text(name, json_body=True) for name in ("outcomes_json", "token_ids_json", "tags_json")),
    ProjectionField("neg_risk", "INTEGER"))
_CATALOG_IDENTITY_LITE = tuple(field for field in _CATALOG_IDENTITY
    if field.name not in {"event_title", "event_market_count", "neg_risk"})
_CATALOG_FEES = (ProjectionField("fees_enabled", "INTEGER"), ProjectionField("fee_rate", "REAL"))
_CATALOG_SPORT_FEES = (ProjectionField("fee_exponent", "INTEGER"), ProjectionField("fee_taker_only", "INTEGER"))
_CATALOG_STATE = (_CONDITION, _text("outcome_prices_json", json_body=True),
    *(ProjectionField(name, "INTEGER") for name in ("active", "closed", "accepting_orders", "enable_order_book")),
    _SOURCE_TIME, _text("resolved_at"), *_CATALOG_FEES)
_CATALOG_STATE_PLUM = tuple(field for field in _CATALOG_STATE
    if field.name not in {"active", "closed", "accepting_orders", "resolved_at"}) + _CATALOG_SPORT_FEES

PROJECTION_PROFILES = MappingProxyType({
    "catalog-identity-v1": ProjectionProfile("catalog-identity-v1", _CATALOG_IDENTITY),
    "catalog-identity-lite-v1": ProjectionProfile("catalog-identity-lite-v1", _CATALOG_IDENTITY_LITE),
    "catalog-state-lite-v1": ProjectionProfile("catalog-state-lite-v1", (_CONDITION, *_CATALOG_FEES)),
    "catalog-state-sports-v1": ProjectionProfile("catalog-state-sports-v1", (*_CATALOG_STATE, *_CATALOG_SPORT_FEES), source_time_field="source_updated_at"),
    "catalog-state-v1": ProjectionProfile("catalog-state-v1", _CATALOG_STATE, source_time_field="source_updated_at"),
    "catalog-state-plum-v1": ProjectionProfile("catalog-state-plum-v1", _CATALOG_STATE_PLUM, source_time_field="source_updated_at"),
    "gamma-quote-v1": ProjectionProfile(
        "gamma-quote-v1", (_CONDITION, ProjectionField("probability", "REAL", False),
                           *_QUOTE_VALUES, _SOURCE_TIME),
        source_time_field="source_updated_at"),
    "token-quote-v1": ProjectionProfile(
        "token-quote-v1", (_CONDITION, *_TOKEN_FIELDS, *_QUOTE_VALUES, _SOURCE_TIME),
        token_field="token_id", source_time_field="source_updated_at"),
    "event-token-quote-v1": ProjectionProfile(
        "event-token-quote-v1", (_CONDITION, _text("event_id"), *_TOKEN_FIELDS,
                                 *_QUOTE_VALUES, _SOURCE_TIME),
        token_field="token_id", source_time_field="source_updated_at"),
    "kiwi-catalog-v1": ProjectionProfile(
        "kiwi-catalog-v1", (_CONDITION, *_KIWI_CATALOG_FIELDS),
        source_time_field="catalog_source_updated_at"),
})


def projection_profile(kind: str) -> ProjectionProfile:
    """Return an exact versioned kind; unknown names never gain a fallback."""
    try:
        return PROJECTION_PROFILES[kind]
    except (KeyError, TypeError):
        from .market_data_raw_profiles import RAW_PUBLIC_PROFILES
        try:
            return RAW_PUBLIC_PROFILES[kind]
        except (KeyError, TypeError):
            raise ValueError("unknown public projection kind") from None


@dataclass(frozen=True)
class SnapshotColumn:
    name: str
    model_type: str
    nullable: bool = True
    python_default: str | None = None
    legacy_nullable: bool | None = None

    @property
    def affinity(self) -> str:
        return {"Integer": "INTEGER", "String": "TEXT", "Float": "REAL",
                "DateTime": "NUMERIC"}[self.model_type]


def _column(name: str) -> SnapshotColumn:
    if name == "id":
        return SnapshotColumn(name, "Integer", False)
    if name == "condition_id":
        return SnapshotColumn(name, "String", False)
    if name == "probability":
        return SnapshotColumn(name, "Float", False)
    if name == "timestamp":
        return SnapshotColumn(name, "DateTime", python_default="datetime.utcnow")
    if name in {"token_id", "outcome"}:
        default = "legacy-unknown" if name == "token_id" else "Unknown"
        return SnapshotColumn(name, "String", False, default, legacy_nullable=True)
    if name in {"liquidity", "volume_24h", "best_bid", "best_ask", "spread",
                "midpoint", "source_elapsed_minutes"}:
        return SnapshotColumn(name, "Float")
    if name in {"catalog_event_market_count", "catalog_neg_risk", "catalog_active",
                "catalog_closed", "catalog_accepting_orders", "catalog_enable_order_book",
                "event_set_complete"}:
        return SnapshotColumn(name, "Integer")
    return SnapshotColumn(name, "String")


@dataclass(frozen=True)
class SnapshotProjectionProfile:
    strategy: str
    columns: tuple[SnapshotColumn, ...]
    groups: tuple[str, ...]
    private_columns: tuple[str, ...]
    body_columns: tuple[str, ...] = ()
    mutable_private_fields: frozenset[str] = frozenset()
    unique_constraints: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        names = self.column_names
        public = self.public_columns
        private, bodies = set(self.private_columns), set(self.body_columns)
        if len(names) != len(set(names)) or len(self.groups) != len(set(self.groups)):
            raise ValueError("duplicate snapshot schema field or group")
        if public & private or public & bodies or private & bodies:
            raise ValueError("snapshot public/private/body ownership overlaps")
        if set(names) != public | private | bodies:
            raise ValueError("snapshot profile does not cover the exact source schema")
        if not self.mutable_private_fields <= private:
            raise ValueError("only declared private fields may be mutable")
        if any(not set(key) <= set(names) for key in self.unique_constraints):
            raise ValueError("snapshot uniqueness references an absent field")

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def public_columns(self) -> frozenset[str]:
        return frozenset(name for kind in self.groups for name in projection_profile(kind).columns)

    @property
    def model_path(self) -> str:
        return f"{self.strategy}/src/polybot/db/models.py"


_GAMMA_COLUMNS = (
    "id", "condition_id", "probability", "liquidity", "volume_24h", "best_bid",
    "best_ask", "spread", "source_updated_at", "run_id", "timestamp",
)
_TOKEN_COLUMNS = (
    "id", "condition_id", "token_id", "outcome", "probability", "liquidity",
    "volume_24h", "best_bid", "best_ask", "spread", "source_updated_at", "run_id", "timestamp",
)
_EVENT_COLUMNS = (
    "id", "condition_id", "event_id", "token_id", "outcome", "outcome_side", "result_kind",
    "probability", "midpoint", "liquidity", "volume_24h", "best_bid", "best_ask", "spread",
    "source_updated_at", "source_elapsed_minutes", "source_clock_reason", "book_json",
    "execution_capacity_json", "run_id", "sport_family", "league_code", "league_name",
    "market_tags_json", "sport_profile_version", "book_shape", "timestamp",
)
_SPORT_PRIVATE = ("id", "probability", "run_id", "timestamp")
_SPORT_CONTEXT = ("sport_family", "league_code", "league_name")
_EVENT_PRIVATE = (
    "id", "outcome_side", "result_kind", "probability", "midpoint",
    "source_elapsed_minutes", "source_clock_reason", "execution_capacity_json", "run_id",
    "sport_family", "league_code", "league_name", "sport_profile_version", "book_shape", "timestamp",
)
_PLUM_EXTRA = (
    "config_hash", "protocol_sha256", "classifier_version", "league_mapping_sha256",
    "strategy_source_digest", "event_cycle_id", "event_set_complete", "event_set_reason",
)
_PLUM_COLUMNS = (
    *_EVENT_COLUMNS[:20], "config_hash", *_EVENT_COLUMNS[20:25],
    "protocol_sha256", "classifier_version", "league_mapping_sha256", "strategy_source_digest",
    "book_shape", "event_cycle_id", "event_set_complete", "event_set_reason", "timestamp",
)


def _snapshot(strategy, names, groups, private, *, bodies=(), mutable=("timestamp",), unique=()):
    return SnapshotProjectionProfile(strategy, tuple(map(_column, names)), groups, private,
                                     bodies, frozenset(mutable), unique)


SNAPSHOT_PROFILES = MappingProxyType({
    **{
        "golden-" + name: _snapshot(
            "golden-" + name, _GAMMA_COLUMNS, ("gamma-quote-v1",),
            ("id", "run_id", "timestamp"),
            mutable=() if name in {"honeydew", "nectarine"} else ("timestamp",))
        for name in ("blueberry", "honeydew", "melon", "nectarine", "papaya", "queen", "quince")
    },
    "golden-kiwi": _snapshot(
        "golden-kiwi", (*_GAMMA_COLUMNS[:-1], *(f.name for f in _KIWI_CATALOG_FIELDS), "timestamp"),
        ("gamma-quote-v1", "kiwi-catalog-v1"), ("id", "run_id", "timestamp"),
        unique=(("run_id", "condition_id"),)),
    "golden-tangerine": _snapshot(
        "golden-tangerine", _TOKEN_COLUMNS, ("token-quote-v1",), _SPORT_PRIVATE),
    "golden-watermelon-live": _snapshot(
        "golden-watermelon-live", (*_TOKEN_COLUMNS[:-1], *_SPORT_CONTEXT, "market_tags_json", "timestamp"),
        ("token-quote-v1",), (*_SPORT_PRIVATE, *_SPORT_CONTEXT), bodies=("market_tags_json",)),
    **{
        "golden-" + name: _snapshot(
            "golden-" + name, _EVENT_COLUMNS, ("event-token-quote-v1",), _EVENT_PRIVATE,
            bodies=("book_json", "market_tags_json"))
        for name in ("apricot", "peach")
    },
    "golden-plum": _snapshot(
        "golden-plum", _PLUM_COLUMNS, ("event-token-quote-v1",), (*_EVENT_PRIVATE, *_PLUM_EXTRA),
        bodies=("book_json", "market_tags_json"),
        mutable=("timestamp", "event_set_complete", "event_set_reason")),
})


def snapshot_profile(strategy: str) -> SnapshotProjectionProfile:
    try:
        return SNAPSHOT_PROFILES[strategy]
    except (KeyError, TypeError):
        raise ValueError("unknown extended snapshot strategy") from None


@dataclass(frozen=True)
class ProjectionGroupValues:
    kind: str
    columns: tuple[str, ...]
    values: tuple[object, ...]


@dataclass(frozen=True)
class SplitSnapshot:
    groups: tuple[ProjectionGroupValues, ...]
    private: Mapping[str, object]
    bodies: Mapping[str, object]


def split_snapshot_row(strategy: str, row: Mapping[str, object]) -> SplitSnapshot:
    """Partition a complete logical row without coercion, defaults or I/O.

    Core PublicProjection performs typed-cell validation and REAL canonicalization.
    Existing CAS bodies stay on their current path. Kiwi's catalog JSON cells,
    however, belong to its separate catalog group and must already be resolved.
    """
    profile = snapshot_profile(strategy)
    if not isinstance(row, Mapping):
        raise TypeError("snapshot projection requires a complete column mapping")
    expected = set(profile.column_names)
    if set(row) != expected:
        raise ValueError("snapshot projection fields differ from the registered schema")
    from .market_data_refs import is_external_value
    groups = []
    for kind in profile.groups:
        names = projection_profile(kind).columns
        values = tuple(row[name] for name in names)
        if any(is_external_value(value) for value in values):
            raise ValueError("resolve public body references before typed projection")
        groups.append(ProjectionGroupValues(kind, names, values))
    return SplitSnapshot(tuple(groups),
                         MappingProxyType({name: row[name] for name in profile.private_columns}),
                         MappingProxyType({name: row[name] for name in profile.body_columns}))


def validate_private_updates(strategy: str, values: Mapping[str, object]) -> None:
    """Allow only the current producers' observed private UPDATE fields."""
    if not isinstance(values, Mapping):
        raise TypeError("private snapshot updates require a column mapping")
    if not set(values) <= snapshot_profile(strategy).mutable_private_fields:
        raise ValueError("snapshot update includes an immutable or public field")
