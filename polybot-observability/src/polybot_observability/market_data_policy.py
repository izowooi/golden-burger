"""Explicit public-payload ownership for the golden-* storage adapters.

This is a reviewed allowlist, not a suffix matcher or a migration. Unknown
strategies/tables have no public columns. In particular, ``config_json``, fee
calculations, execution evidence, selection decisions and experiment state stay
with their owner even when they also contain public prices. The original bytes,
NULL values, hashes and source observation identities must survive indirection.

Public raw responses may naturally contain the venue's published fee schedule;
that does not authorize moving a strategy's derived fee/economic evidence.
Mixed envelopes need a separate projection/reassembly adapter and are recorded
below instead of being silently treated as wholly public.

Schema paths are repository-relative unless prefixed with ``remote:``. Apple
collection-v2 is deployed source outside the legacy golden-apple source tree.
Its compressed frame contains both market data and decisions, so a column-only
adapter cannot migrate it. Inspection metadata records that coverage gap.
"""

from __future__ import annotations

from dataclasses import dataclass


POLICY_VERSION = 1
KNOWN_STRATEGIES = frozenset({
    "golden-apple", "golden-apricot", "golden-banana", "golden-black",
    "golden-blueberry", "golden-cherry", "golden-coconut", "golden-date",
    "golden-elderberry", "golden-fig", "golden-grape", "golden-guava",
    "golden-honeydew", "golden-kiwi", "golden-lime", "golden-mango",
    "golden-melon", "golden-nectarine", "golden-orange", "golden-papaya",
    "golden-peach", "golden-plum", "golden-pomegranate", "golden-queen",
    "golden-quince", "golden-raspberry", "golden-strawberry",
    "golden-tangerine", "golden-watermelon", "golden-watermelon-live",
})


@dataclass(frozen=True)
class PublicColumnRule:
    strategy: str
    table: str
    columns: frozenset[str]
    source_schema: str
    reason: str


@dataclass(frozen=True)
class RetainedPayload:
    """An inspected payload that cannot be moved as an undivided column."""

    strategy: str
    table: str
    columns: frozenset[str]
    source_schema: str
    reason: str


@dataclass(frozen=True)
class PublicLevelTable:
    """Future scalar-level projection; no table routing is implemented here.

    Keys remain source-scoped: the destination must prefix strategy/runtime/
    source identity before combining unrelated databases. ``parent_key`` maps
    local columns to the named parent's columns in the same order.
    """

    strategy: str
    table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    parent_table: str
    parent_key: tuple[str, ...]
    parent_columns: tuple[str, ...]
    natural_key: tuple[str, ...]
    retained_columns: frozenset[str]
    source_schema: str
    reason: str

    @property
    def whole_table(self) -> bool:
        return not self.retained_columns


@dataclass(frozen=True)
class SchemaInspection:
    strategy: str
    source_schema: str
    note: str
    source_sha256: str | None = None


def _rule(strategy: str, table: str, columns: tuple[str, ...],
          source: str, reason: str) -> PublicColumnRule:
    return PublicColumnRule(strategy, table, frozenset(columns), source, reason)


_CATALOG_STRATEGIES = (
    "golden-apricot", "golden-blueberry", "golden-kiwi", "golden-melon",
    "golden-papaya", "golden-peach", "golden-plum", "golden-queen",
    "golden-quince", "golden-tangerine", "golden-watermelon-live",
)
_CATALOG_ARRAYS = ("outcomes_json", "outcome_prices_json", "token_ids_json", "tags_json")
_SPORTS_RAW_STRATEGIES = ("golden-apricot", "golden-peach", "golden-plum")
_COCONUT_SCHEMA = "golden-coconut/src/polybot/db/migrations/0006_major_sports_lifecycle_v6.sql"
_COCONUT_RECORDER = "golden-coconut/src/polybot/recorder_store.py"
_APPLE_REMOTE = "remote:golden-apple/polybot-do/apple/"

PUBLIC_COLUMN_RULES: tuple[PublicColumnRule, ...] = (
    *(_rule(s, "market_catalog", _CATALOG_ARRAYS,
            f"{s}/src/polybot/db/models.py", "Gamma outcome/token/tag arrays; fee columns stay local.")
      for s in _CATALOG_STRATEGIES),
    *(_rule(s, "market_catalog", ("outcomes_json", "token_ids_json", "tags_json"),
            f"{s}/src/polybot/db/models.py", "Legacy Gamma catalog has no outcome_prices_json column.")
      for s in ("golden-honeydew", "golden-nectarine")),
    _rule("golden-kiwi", "market_snapshots",
          ("catalog_outcomes_json", "catalog_outcome_prices_json", "catalog_token_ids_json", "catalog_tags_json"),
          "golden-kiwi/src/polybot/db/models.py", "Copied source catalog arrays; trend/decision snapshots retain their IDs."),
    *(_rule(s, "market_snapshots", ("book_json", "market_tags_json"),
            f"{s}/src/polybot/db/models.py", "Displayed full book and public tags; execution_capacity_json remains derived state.")
      for s in _SPORTS_RAW_STRATEGIES),
    *(_rule(s, "raw_book_observations", ("book_json",),
            f"{s}/src/polybot/db/models.py", "Canonical source book; attempt status and collection evidence stay local.")
      for s in _SPORTS_RAW_STRATEGIES),
    _rule("golden-watermelon-live", "market_snapshots", ("market_tags_json",),
          "golden-watermelon-live/src/polybot/db/models.py", "Public tags; this schema has no full-book JSON column."),
    _rule("golden-plum", "exit_execution_observations", ("book_json",),
          "golden-plum/src/polybot/db/models.py", "Source bid book only; exit plan, result and ledger attribution remain local."),
    _rule("golden-black", "raw_payloads", ("payload_gzip",),
          "golden-black/src/polybot/db/repository.py", "Exact accountless Gamma/CLOB HTTP response bytes."),
    _rule("golden-black", "market_observations",
          ("outcome_labels_json", "token_ids_json", "outcome_prices_json"),
          "golden-black/src/polybot/db/repository.py", "Source outcome arrays; normalized_json includes derived fallback fees."),
    _rule("golden-watermelon", "raw_payloads", ("payload_gzip",),
          "golden-watermelon/src/polybot/db/repository.py", "Exact public HTTP payloads; also used by db/raw_repository.py."),
    _rule("golden-watermelon", "market_observations",
          ("event_tag_slugs_json", "team_leagues_json", "outcome_labels_json", "token_ids_json", "outcome_prices_json"),
          "golden-watermelon/src/polybot/db/repository.py", "Public event/catalog arrays; classification and fee calculations stay local."),
    _rule("golden-watermelon", "raw_events", ("event_json",),
          "golden-watermelon/src/polybot/db/raw_repository.py", "Source event body; source references, slots and terminal proof remain local."),
    _rule("golden-cherry", "shadow_raw_payloads", ("payload_gzip",),
          "golden-cherry/src/polybot/shadow/db.py", "Shadow collector's public HTTP bytes, separate from live execution evidence."),
    _rule("golden-cherry", "shadow_market_observations",
          ("event_tags_json", "market_tags_json", "outcomes_json", "token_ids_json", "gamma_probabilities_json"),
          "golden-cherry/src/polybot/shadow/db.py", "Source Gamma metadata; exclusion reasons and cell decisions stay local."),
    _rule("golden-cherry", "shadow_resolution_observations",
          ("outcomes_json", "token_ids_json", "final_prices_json"),
          "golden-cherry/src/polybot/shadow/db.py", "Source terminal market arrays; experiment settlement attribution stays local."),
    _rule("golden-coconut", "raw_payloads", ("payload_gzip",),
          _COCONUT_SCHEMA, "Historical full public response bytes."),
    _rule("golden-coconut", "book_snapshots", ("book_gzip",),
          _COCONUT_SCHEMA, "Historical canonical full depth; notional book_ladder_observations are derived."),
    _rule("golden-coconut", "event_observations", ("raw_lifecycle_json",),
          _COCONUT_SCHEMA, "Historical source lifecycle projection; classification/normalized state stays local."),
    _rule("golden-coconut", "market_observations", ("labels_json", "token_ids_json", "probabilities_json"),
          _COCONUT_SCHEMA, "Historical Gamma arrays without classification evidence."),
    *(_rule("golden-coconut", table, (column,), _COCONUT_SCHEMA, "Original public event metadata object.")
      for table, column in (("event_tag_observations", "tag_json"),
                            ("event_series_observations", "series_json"),
                            ("event_team_observations", "team_json"))),
    _rule("golden-coconut", "sports_clock_observations", ("clock_json",),
          _COCONUT_SCHEMA, "Public sports clock message; matching and lifecycle fields stay local."),
    _rule("golden-coconut", "requests", ("raw_gzip",),
          _COCONUT_RECORDER, "Active recorder captures each physical HTTP attempt before cycle publication."),
    _rule("golden-coconut", "event_observations", ("event_json", "clock_json"),
          _COCONUT_RECORDER, "Active recorder source event/clock bodies; its schema differs from historical event_observations."),
    _rule("golden-coconut", "book_observations", ("book_gzip",),
          _COCONUT_RECORDER, "Active recorder full book; fee_json remains separate and local."),
    _rule("golden-coconut", "clock_observations", ("raw_gzip",),
          _COCONUT_RECORDER, "Active recorder raw public websocket message."),
    _rule("golden-pomegranate", "raw_payloads", ("payload_blob",),
          "golden-pomegranate/src/polybot/db/repository.py", "Public response body; Data API acquisition applies its existing sanitization first."),
    _rule("golden-pomegranate", "market_observations",
          ("liquidity_variants_json", "outcome_prices_json", "price_changes_json", "tags_json", "sports_json", "source_clocks_json"),
          "golden-pomegranate/src/polybot/db/repository.py", "Source market fields; parse_quality_json and fee_metadata_json remain local."),
    _rule("golden-pomegranate", "orderbook_selections", ("token_ids_json", "outcome_labels_json"),
          "golden-pomegranate/src/polybot/db/repository.py", "Public catalog arrays only, not the collector's selection state."),
    _rule("golden-pomegranate", "resolution_observations", ("outcome_prices_json", "raw_market_json"),
          "golden-pomegranate/src/polybot/db/repository.py", "Public terminal-market source, distinct from live strategy trade resolutions."),
    _rule("golden-pomegranate", "trade_observations", ("sanitized_trade_json",),
          "golden-pomegranate/src/polybot/db/repository.py", "Accountless public taker tape; never a strategy CONFIRMED fill ledger."),
    _rule("golden-raspberry", "raw_payloads", ("payload_blob",),
          "golden-raspberry/src/polybot/db/repository.py", "Public CLOB universe/followup bodies."),
    _rule("golden-raspberry", "market_observations",
          ("token_ids_json", "outcome_labels_json", "outcome_prices_json", "tags_json"),
          "golden-raspberry/src/polybot/db/repository.py", "Gamma source arrays; panel/shard selection stays local."),
    _rule("golden-strawberry", "raw_payloads", ("payload_blob",),
          "golden-strawberry/src/polybot/db/repository.py", "Exact accountless sampling/Gamma/CLOB response bytes."),
    _rule("golden-strawberry", "market_catalog_versions",
          ("event_ids_json", "tags_json", "outcome_labels_json", "token_ids_json", "outcome_prices_json", "normalized_market_json"),
          "golden-strawberry/src/polybot/db/repository.py", "Normalized source catalog body excludes the adjacent tradability/classifier decision fields."),
    _rule("golden-strawberry", "outcome_observations", ("tags_json",),
          "golden-strawberry/src/polybot/db/repository.py", "Copied source tags; crossing decisions remain local."),
    _rule("golden-strawberry", "candidate_metadata_observations", ("event_ids_json", "tags_json"),
          "golden-strawberry/src/polybot/db/repository.py", "Source metadata arrays only."),
    _rule("golden-strawberry", "compact_books", ("book_blob",),
          "golden-strawberry/src/polybot/db/followup_repository.py", "Followup public compact full book with existing gzip/checksum contract."),
    _rule("golden-strawberry", "resolution_observations", ("market_blob",),
          "golden-strawberry/src/polybot/db/followup_repository.py", "Followup raw market; token attribution and target-jump analysis stay local."),
    _rule("golden-guava", "source_requests", ("payload_gzip",),
          "golden-guava/src/polybot/evidence.py", "Accountless response/projection bytes; params and receipts remain source-local."),
    _rule("golden-guava", "events", ("raw_gzip",),
          "golden-guava/src/polybot/evidence.py", "Raw event only; event_json also serializes eligibility decisions."),
    _rule("golden-guava", "book_attempts", ("raw_gzip",),
          "golden-guava/src/polybot/evidence.py", "Raw book only; book_json also serializes derived fee/depth evidence."),
    _rule("golden-apple", "market_observations", ("outcome_prices",),
          _APPLE_REMOTE + "store.py", "Remote legacy collector source prices; absent from the local legacy trading schema."),
)


RETAINED_PAYLOADS: tuple[RetainedPayload, ...] = (
    RetainedPayload("golden-apple", "runs", frozenset({"frame"}),
                    _APPLE_REMOTE + "compact_store.py",
                    "collection-v2 zlib frame mixes source books/events/markets with audit decisions, capacity and fees. "
                    "Split source subobjects and reassemble through encode_frame/decode_frame; never externalize the whole frame."),
    RetainedPayload("golden-apple", "payloads", frozenset({"compressed"}),
                    _APPLE_REMOTE + "store.py",
                    "Legacy content-addressed pool mixes HTTP bytes/depth with computed metrics_hash payloads. "
                    "Use reference-role inspection, not a blanket column rule."),
    RetainedPayload("golden-guava", "events", frozenset({"event_json"}),
                    "golden-guava/src/polybot/evidence.py", "Serialized event includes raw data plus eligibility/exclusion decisions; raw_gzip is allowed separately."),
    RetainedPayload("golden-guava", "book_attempts", frozenset({"book_json", "fee_evidence_json", "depth_metrics_json"}),
                    "golden-guava/src/polybot/evidence.py", "book_json duplicates the complete derived book object, including fees and depth metrics."),
    RetainedPayload("golden-raspberry", "market_sweeps", frozenset({"membership_blob"}),
                    "golden-raspberry/src/polybot/db/repository.py", "Compressed membership includes eligibility/rejection and selection decisions."),
    RetainedPayload("golden-strawberry", "market_membership_blobs", frozenset({"membership_blob"}),
                    "golden-strawberry/src/polybot/db/repository.py", "Compressed membership includes tradable/exclusion_reason decisions."),
    RetainedPayload("golden-pomegranate", "market_metadata_versions", frozenset({"metadata_json"}),
                    "golden-pomegranate/src/polybot/db/repository.py", "Catalog envelope includes fee_metadata; split it before applying the public-only column policy."),
    RetainedPayload("golden-strawberry", "clob_snapshots", frozenset({"source_metadata_json"}),
                    "golden-strawberry/src/polybot/db/repository.py", "Source metadata envelope includes fee_rate_bps; keep the separately stored fee context local."),
    *(
        RetainedPayload(s, "raw_event_observations", frozenset({"evidence_json"}),
                        f"{s}/src/polybot/db/models.py",
                        "Event envelope contains market fee context, collector decisions, slots and terminal proofs; needs projection.")
        for s in _SPORTS_RAW_STRATEGIES
    ),
)


def _levels(strategy: str, table: str, parent: str, source: str,
            *, raw_decimals: bool = False,
            retained: frozenset[str] = frozenset()) -> PublicLevelTable:
    values = ("price_raw", "price", "size_raw", "size") if raw_decimals else ("price", "size")
    return PublicLevelTable(
        strategy, table, ("level_id", "snapshot_id", "side", "level_index", *values),
        ("level_id",), parent, ("snapshot_id",), ("snapshot_id",),
        ("snapshot_id", "side", "level_index"), retained, source,
        "Public displayed price/size levels plus source identity; preserve parent linkage and original decimal text."
        if not retained else "Public levels require projection because experimental near-touch/entry-use flags stay local.",
    )


PUBLIC_LEVEL_PROJECTIONS: tuple[PublicLevelTable, ...] = (
    *(_levels(s, "orderbook_levels", "orderbook_snapshots", f"{s}/src/polybot/db/repository.py")
      for s in ("golden-black", "golden-watermelon")),
    _levels("golden-pomegranate", "orderbook_levels", "orderbook_snapshots",
            "golden-pomegranate/src/polybot/db/repository.py", raw_decimals=True),
    _levels("golden-strawberry", "clob_levels", "clob_snapshots",
            "golden-strawberry/src/polybot/db/repository.py"),
    _levels("golden-cherry", "shadow_book_levels", "shadow_book_snapshots",
            "golden-cherry/src/polybot/shadow/db.py"),
    _levels("golden-raspberry", "orderbook_levels", "orderbook_snapshots",
            "golden-raspberry/src/polybot/db/repository.py",
            retained=frozenset({"in_near_touch_window", "used_for_entry"})),
)


SCHEMA_INSPECTIONS: tuple[SchemaInspection, ...] = (
    *(SchemaInspection(s, f"{s}/src/polybot/db/models.py",
                       "Legacy SQLAlchemy snapshots contain scalar observations, with no public payload body column. "
                       "Empty payload allowlist does not mean scalar market data has been centralized.")
      for s in ("golden-apple", "golden-banana", "golden-date", "golden-elderberry",
                "golden-fig", "golden-grape", "golden-lime", "golden-mango", "golden-orange")),
    SchemaInspection("golden-coconut", _COCONUT_SCHEMA,
                     "book_ladder_observations contains notional-dependent hypothetical fills/VWAP, not raw price levels. "
                     "It stays private; book_snapshots.book_gzip holds the source ladder."),
    SchemaInspection("golden-apple", _APPLE_REMOTE + "compact_store.py",
                     "Read-only inspection on macmini-m5, 2026-09-29. Actual root: /Volumes/t7/jenkins/golden-apple. "
                     "Format apple-filtered-frames-v1, application_id 0x47415032. "
                     "Writer MonthStore.finish, reader decode_frame. This source is absent from the monorepo legacy Apple tree.",
                     "c354e7a4c653fa985bc982773cb64e88d480a9c091050c986ff4abb99c794eb3"),
    SchemaInspection("golden-apple", _APPLE_REMOTE + "compact_collection.py",
                     "Identical inspected source SHA in polybot-do/re/mi/shadow-one. "
                     "raw_http_bodies_retained=False; frame carries filtered public projections and private audit/capacity.",
                     "9bae4c4591b45c29b2a3dbc4127e768fe202a3f869da1504a70ca386166d28aa"),
    SchemaInspection("golden-apple", _APPLE_REMOTE + "store.py",
                     "Remote legacy schema v5. save_payload/read_payload share a mixed zlib pool; "
                     "book_storage.py exposes compact/legacy rows through a union view and INSTEAD OF INSERT trigger.",
                     "ba7cccf7160262e30b2b57fd0140c48136f3143e6b62d537abec03addfb15356"),
)


def public_columns(strategy: str, table: str) -> frozenset[str]:
    """Return the exact permitted payload columns for this source namespace."""
    return frozenset(
        column
        for rule in PUBLIC_COLUMN_RULES
        if rule.strategy == strategy and rule.table == table
        for column in rule.columns
    )


def public_level_tables(strategy: str) -> tuple[PublicLevelTable, ...]:
    """Return only wholly public scalar level tables suitable for later routing."""
    return tuple(rule for rule in PUBLIC_LEVEL_PROJECTIONS
                 if rule.strategy == strategy and rule.whole_table)


def public_level_projections(strategy: str) -> tuple[PublicLevelTable, ...]:
    """Include mixed tables whose public columns require a selective projection."""
    return tuple(rule for rule in PUBLIC_LEVEL_PROJECTIONS if rule.strategy == strategy)


def retained_payloads(strategy: str) -> tuple[RetainedPayload, ...]:
    """Expose inspected mixed-body exclusions to migration/coverage reports."""
    return tuple(rule for rule in RETAINED_PAYLOADS if rule.strategy == strategy)


def schema_inspections(strategy: str) -> tuple[SchemaInspection, ...]:
    return tuple(item for item in SCHEMA_INSPECTIONS if item.strategy == strategy)
