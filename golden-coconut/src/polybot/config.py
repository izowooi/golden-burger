"""Strict configuration and side-effect-free safety boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .registry import SportsRegistry, load_registry
from .source_digest import (
    PROJECT_ROOT,
    compute_strategy_source_digest,
    preregistration_sha256,
    verify_frozen_manifest,
)


CANONICAL_JOB = "coconut-major-sports-lifecycle-5m-v7"
DATA_CONTRACT = "major-sports-lifecycle-census-v7"
COLLECTION_CONTRACT = "research-full-v1"
SCHEMA_PROFILE = "golden-coconut-create-only-lifecycle-v6"
UNIVERSE_PROFILE = "major-sports-five-family-lifecycle-2026-08-v6"
CLASSIFIER_VERSION = "major-sports-exact-identity-lifecycle-v6"
THRESHOLD_GRID = tuple(Decimal(value) / 100 for value in range(75, 100))
NOTIONAL_LADDER = (
    5.0,
    10.0,
    15.0,
    20.0,
    25.0,
    30.0,
    40.0,
    50.0,
    75.0,
    100.0,
    150.0,
    250.0,
    500.0,
    750.0,
    1000.0,
)

_ALLOWED_POLYBOT_KEYS = frozenset(
    {"POLYBOT_LIFECYCLE_MODE", "POLYBOT_SIMULATION_MODE"}
)
_LEGACY_CREDENTIAL_ALIASES = frozenset(
    {
        "PRIVATE_KEY",
        "POLY_PRIVATE_KEY",
        "POLYGON_PRIVATE_KEY",
        "WALLET_PRIVATE_KEY",
        "FUNDER_ADDRESS",
        "POLY_FUNDER_ADDRESS",
        "WALLET_ADDRESS",
        "SIGNATURE_TYPE",
        "API_KEY",
        "API_SECRET",
        "API_PASSPHRASE",
        "SECRET_KEY",
        "ACCESS_TOKEN",
        "AUTH_TOKEN",
        "PASSPHRASE",
        "PK",
    }
)


def _parse_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
        return value.strip().casefold() == "true"
    raise ValueError(f"{name} must be a boolean")


def assert_safe_environment(env: Mapping[str, str] | None = None) -> None:
    """Reject credential-shaped state by key presence, including empty values."""

    values = os.environ if env is None else env
    names = set(values)
    forbidden = sorted(
        name
        for name in names
        if name.startswith("POLYMARKET_")
        or name.startswith("CLOB_")
        or name in _LEGACY_CREDENTIAL_ALIASES
    )
    if forbidden:
        raise ValueError(
            "Golden Coconut refuses credential-bearing environments: "
            + ", ".join(forbidden)
        )
    unknown = sorted(
        name
        for name in names
        if name.startswith("POLYBOT_") and name not in _ALLOWED_POLYBOT_KEYS
    )
    if unknown:
        raise ValueError("unknown POLYBOT_* environment keys: " + ", ".join(unknown))
    if "POLYBOT_LIFECYCLE_MODE" in values:
        if values["POLYBOT_LIFECYCLE_MODE"] != "archive_only":
            raise ValueError("POLYBOT_LIFECYCLE_MODE must be archive_only")
    if "POLYBOT_SIMULATION_MODE" in values:
        if not _parse_bool(values["POLYBOT_SIMULATION_MODE"], "POLYBOT_SIMULATION_MODE"):
            raise ValueError("POLYBOT_SIMULATION_MODE must be true")


def _mapping(parent: Mapping[str, Any], key: str, parent_name: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{parent_name}.{key} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{name} keys differ: missing={sorted(expected - actual)!r} "
            f"unknown={sorted(actual - expected)!r}"
        )


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be an integer")
    return result


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _origin(value: Any, name: str, *, scheme: str = "https", path: str = "") -> str:
    text = str(value).strip().rstrip("/")
    parsed = urlsplit(text)
    if (
        parsed.scheme != scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be the frozen credential-free endpoint")
    return text


def _decimal_grid(value: Any) -> tuple[Decimal, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("research.threshold_grid must be an array")
    try:
        result = tuple(Decimal(str(item)) for item in value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("research.threshold_grid contains an invalid decimal") from error
    if result != THRESHOLD_GRID:
        raise ValueError("threshold grid must be exactly 0.75 through 0.99 by 0.01")
    return result


@dataclass(frozen=True)
class GammaConfig:
    base_url: str
    endpoint: str
    followup_endpoint_template: str
    page_size: int
    max_pages_per_family: int
    related_tags: bool
    include_children: bool
    discovery_lookback_hours: int
    discovery_lookahead_hours: int
    parallel_family_workers: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    attempt_wall_seconds: float
    max_retries: int
    retry_base_seconds: float
    retry_max_seconds: float


@dataclass(frozen=True)
class ClobConfig:
    base_url: str
    batch_token_limit: int
    collect_public_fee: bool
    parallel_read_workers: int


@dataclass(frozen=True)
class SportsFeedConfig:
    websocket_url: str
    connect_timeout_seconds: float
    receive_window_seconds: float
    max_messages: int


@dataclass(frozen=True)
class ResearchConfig:
    threshold_grid: tuple[Decimal, ...]
    executable_notional_ladder_usdc: tuple[float, ...]
    resolution_retry_minutes: int
    minimum_health_days: int


@dataclass(frozen=True)
class StorageConfig:
    database_name: str
    busy_timeout_ms: int
    min_free_gib: float
    warn_used_ratio: float
    stop_used_ratio: float
    archive_retention_days: int


@dataclass(frozen=True)
class TradingConfig:
    lifecycle_mode: str
    data_contract: str
    collection_contract: str
    schema_profile: str
    universe_profile: str
    classifier_version: str
    sports_registry_sha256: str
    cadence_minutes: int
    cooperative_budget_seconds: float
    stop_margin_seconds: float
    hard_cycle_seconds: float
    max_receipt_skew_seconds: float
    crossing_max_gap_seconds: float
    gamma: GammaConfig
    clob: ClobConfig
    sports_feed: SportsFeedConfig
    research: ResearchConfig
    storage: StorageConfig
    strategy_source_digest: str
    preregistration_sha256: str


@dataclass(frozen=True)
class BotConfig:
    simulation_mode: bool
    mode: str
    job_name: str
    db_path: Path
    registry: SportsRegistry
    trading: TradingConfig
    config_hash: str

    def redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["db_path"] = str(self.db_path)
        payload["registry"] = {
            "path": str(self.registry.path),
            "sha256": self.registry.sha256,
            "profile": self.registry.profile,
            "classifier_version": self.registry.classifier_version,
            "families": [family.code for family in self.registry.families],
        }
        payload["trading"]["research"]["threshold_grid"] = [
            str(value) for value in self.trading.research.threshold_grid
        ]
        return payload


def _validate(config: BotConfig) -> None:
    trading = config.trading
    if config.job_name != CANONICAL_JOB:
        raise ValueError(f"job_name must be {CANONICAL_JOB}")
    if config.mode not in {"sim", "shadow"} or config.simulation_mode is not True:
        raise ValueError("Golden Coconut supports sim/shadow modes only")
    if trading.lifecycle_mode != "archive_only":
        raise ValueError("lifecycle_mode must be archive_only")
    if (
        trading.data_contract,
        trading.collection_contract,
        trading.schema_profile,
        trading.universe_profile,
        trading.classifier_version,
    ) != (
        DATA_CONTRACT,
        COLLECTION_CONTRACT,
        SCHEMA_PROFILE,
        UNIVERSE_PROFILE,
        CLASSIFIER_VERSION,
    ):
        raise ValueError("data/schema/universe/classifier contract drift")
    if trading.sports_registry_sha256 != config.registry.sha256:
        raise ValueError("sports registry hash drift")
    if config.registry.profile != UNIVERSE_PROFILE:
        raise ValueError("sports registry profile drift")
    if config.registry.classifier_version != CLASSIFIER_VERSION:
        raise ValueError("sports registry classifier drift")
    if trading.cadence_minutes != 5:
        raise ValueError("initial cadence must remain five minutes")
    if (
        trading.cooperative_budget_seconds,
        trading.stop_margin_seconds,
        trading.hard_cycle_seconds,
        trading.max_receipt_skew_seconds,
    ) != (225.0, 30.0, 240.0, 90.0):
        raise ValueError("cycle deadline/skew contract drift")
    if trading.crossing_max_gap_seconds != 450.0:
        raise ValueError("crossing gap censor boundary must remain 450 seconds")
    gamma = trading.gamma
    if gamma.base_url != "https://gamma-api.polymarket.com" or gamma.endpoint != "/events/keyset":
        raise ValueError("Gamma endpoint drift")
    if gamma.page_size != 500 or not 1 <= gamma.max_pages_per_family <= 20:
        raise ValueError("Gamma page envelope drift")
    if gamma.followup_endpoint_template != "/events/{event_id}":
        raise ValueError("Gamma follow-up endpoint drift")
    if gamma.related_tags is not False or gamma.include_children is not False:
        raise ValueError("Gamma must use related_tags=false and include_children=false")
    if (gamma.discovery_lookback_hours, gamma.discovery_lookahead_hours) != (24, 48):
        raise ValueError(
            "Gamma scheduled-start discovery window must remain slot-24h through slot+48h"
        )
    if gamma.parallel_family_workers != 5:
        raise ValueError("Gamma must acquire the five families in five isolated workers")
    if (
        gamma.connect_timeout_seconds,
        gamma.read_timeout_seconds,
        gamma.attempt_wall_seconds,
    ) != (3.0, 5.0, 15.0):
        raise ValueError("Gamma connect/read/total-attempt timeout contract drift")
    if not 0 <= gamma.max_retries <= 4:
        raise ValueError("Gamma retry count is outside the bounded envelope")
    if trading.clob.base_url != "https://clob.polymarket.com":
        raise ValueError("CLOB endpoint drift")
    if not 1 <= trading.clob.batch_token_limit <= 500:
        raise ValueError("CLOB batch_token_limit is invalid")
    if trading.clob.parallel_read_workers != 5:
        raise ValueError("CLOB public reads require five isolated workers")
    if trading.research.minimum_health_days != 7:
        raise ValueError("minimum health gate must remain seven UTC dates")
    if trading.sports_feed.websocket_url != "wss://sports-api.polymarket.com/ws":
        raise ValueError("sports feed endpoint drift")
    storage = trading.storage
    if storage.database_name != "trades_sim.db":
        raise ValueError("database_name must remain daily-rsync canonical trades_sim.db")
    if storage.min_free_gib != 150:
        raise ValueError("minimum free space must remain 150 GiB")
    if (storage.warn_used_ratio, storage.stop_used_ratio) != (0.70, 0.80):
        raise ValueError("storage warn/stop ratios must remain 70/80 percent")
    if storage.archive_retention_days < 120:
        raise ValueError("whole-shard retention horizon cannot be shortened")


def load_config(
    path: str | Path = "config.yaml",
    job_name: str = CANONICAL_JOB,
    *,
    mode: str = "sim",
) -> BotConfig:
    # This ordering is part of the security contract: no file read occurs first.
    assert_safe_environment()
    verify_frozen_manifest()
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("config root must be a mapping")
    _exact_keys(raw, {"simulation_mode", "trading"}, "config")
    simulation_mode = _parse_bool(raw["simulation_mode"], "simulation_mode")
    trading_raw = _mapping(raw, "trading", "config")
    _exact_keys(
        trading_raw,
        {
            "lifecycle_mode", "data_contract", "collection_contract", "schema_profile",
            "universe_profile", "classifier_version", "sports_registry_sha256",
            "cadence_minutes", "cooperative_budget_seconds", "stop_margin_seconds",
            "hard_cycle_seconds", "max_receipt_skew_seconds",
            "crossing_max_gap_seconds", "gamma", "clob", "sports_feed",
            "research", "storage",
        },
        "trading",
    )
    gamma_raw = _mapping(trading_raw, "gamma", "trading")
    clob_raw = _mapping(trading_raw, "clob", "trading")
    sports_raw = _mapping(trading_raw, "sports_feed", "trading")
    research_raw = _mapping(trading_raw, "research", "trading")
    storage_raw = _mapping(trading_raw, "storage", "trading")
    _exact_keys(
        gamma_raw,
        {
            "base_url", "endpoint", "followup_endpoint_template", "page_size",
            "max_pages_per_family", "related_tags", "include_children",
            "discovery_lookback_hours", "discovery_lookahead_hours",
            "parallel_family_workers", "connect_timeout_seconds",
            "read_timeout_seconds", "attempt_wall_seconds", "max_retries", "retry_base_seconds",
            "retry_max_seconds",
        },
        "trading.gamma",
    )
    _exact_keys(
        clob_raw,
        {
            "base_url", "batch_token_limit", "collect_public_fee",
            "parallel_read_workers",
        },
        "trading.clob",
    )
    _exact_keys(
        sports_raw,
        {"websocket_url", "connect_timeout_seconds", "receive_window_seconds", "max_messages"},
        "trading.sports_feed",
    )
    _exact_keys(
        research_raw,
        {
            "threshold_grid", "executable_notional_ladder_usdc",
            "resolution_retry_minutes", "minimum_health_days",
        },
        "trading.research",
    )
    _exact_keys(
        storage_raw,
        {
            "database_name", "busy_timeout_ms", "min_free_gib", "warn_used_ratio",
            "stop_used_ratio", "archive_retention_days",
        },
        "trading.storage",
    )
    registry_hash = str(trading_raw["sports_registry_sha256"]).strip()
    registry = load_registry(registry_hash)
    gamma = GammaConfig(
        base_url=_origin(gamma_raw["base_url"], "gamma.base_url"),
        endpoint=str(gamma_raw["endpoint"]).strip(),
        followup_endpoint_template=str(gamma_raw["followup_endpoint_template"]).strip(),
        page_size=_integer(gamma_raw["page_size"], "gamma.page_size"),
        max_pages_per_family=_integer(gamma_raw["max_pages_per_family"], "gamma.max_pages_per_family"),
        related_tags=_parse_bool(gamma_raw["related_tags"], "gamma.related_tags"),
        include_children=_parse_bool(gamma_raw["include_children"], "gamma.include_children"),
        discovery_lookback_hours=_integer(
            gamma_raw["discovery_lookback_hours"], "gamma.discovery_lookback_hours"
        ),
        discovery_lookahead_hours=_integer(
            gamma_raw["discovery_lookahead_hours"], "gamma.discovery_lookahead_hours"
        ),
        parallel_family_workers=_integer(
            gamma_raw["parallel_family_workers"], "gamma.parallel_family_workers"
        ),
        connect_timeout_seconds=_finite(gamma_raw["connect_timeout_seconds"], "gamma.connect_timeout_seconds"),
        read_timeout_seconds=_finite(gamma_raw["read_timeout_seconds"], "gamma.read_timeout_seconds"),
        attempt_wall_seconds=_finite(
            gamma_raw["attempt_wall_seconds"], "gamma.attempt_wall_seconds"
        ),
        max_retries=_integer(gamma_raw["max_retries"], "gamma.max_retries"),
        retry_base_seconds=_finite(gamma_raw["retry_base_seconds"], "gamma.retry_base_seconds"),
        retry_max_seconds=_finite(gamma_raw["retry_max_seconds"], "gamma.retry_max_seconds"),
    )
    clob = ClobConfig(
        base_url=_origin(clob_raw["base_url"], "clob.base_url"),
        batch_token_limit=_integer(clob_raw["batch_token_limit"], "clob.batch_token_limit"),
        collect_public_fee=_parse_bool(clob_raw["collect_public_fee"], "clob.collect_public_fee"),
        parallel_read_workers=_integer(
            clob_raw["parallel_read_workers"], "clob.parallel_read_workers"
        ),
    )
    websocket = _origin(
        sports_raw["websocket_url"], "sports_feed.websocket_url", scheme="wss", path="/ws"
    )
    sports_feed = SportsFeedConfig(
        websocket_url=websocket,
        connect_timeout_seconds=_finite(sports_raw["connect_timeout_seconds"], "sports_feed.connect_timeout_seconds"),
        receive_window_seconds=_finite(sports_raw["receive_window_seconds"], "sports_feed.receive_window_seconds"),
        max_messages=_integer(sports_raw["max_messages"], "sports_feed.max_messages"),
    )
    ladder = tuple(
        _finite(value, "research.executable_notional_ladder_usdc")
        for value in research_raw["executable_notional_ladder_usdc"]
    )
    if ladder != NOTIONAL_LADDER:
        raise ValueError("executable ladder must be exactly 5,10,25,50,100,250,500")
    research = ResearchConfig(
        threshold_grid=_decimal_grid(research_raw["threshold_grid"]),
        executable_notional_ladder_usdc=ladder,
        resolution_retry_minutes=_integer(research_raw["resolution_retry_minutes"], "research.resolution_retry_minutes"),
        minimum_health_days=_integer(
            research_raw["minimum_health_days"], "research.minimum_health_days"
        ),
    )
    storage = StorageConfig(
        database_name=str(storage_raw["database_name"]).strip(),
        busy_timeout_ms=_integer(storage_raw["busy_timeout_ms"], "storage.busy_timeout_ms"),
        min_free_gib=_finite(storage_raw["min_free_gib"], "storage.min_free_gib"),
        warn_used_ratio=_finite(storage_raw["warn_used_ratio"], "storage.warn_used_ratio"),
        stop_used_ratio=_finite(storage_raw["stop_used_ratio"], "storage.stop_used_ratio"),
        archive_retention_days=_integer(storage_raw["archive_retention_days"], "storage.archive_retention_days"),
    )
    resolved_lifecycle = os.environ.get(
        "POLYBOT_LIFECYCLE_MODE", str(trading_raw["lifecycle_mode"])
    )
    resolved_simulation = simulation_mode
    if "POLYBOT_SIMULATION_MODE" in os.environ:
        resolved_simulation = _parse_bool(
            os.environ["POLYBOT_SIMULATION_MODE"], "POLYBOT_SIMULATION_MODE"
        )
    trading = TradingConfig(
        lifecycle_mode=resolved_lifecycle,
        data_contract=str(trading_raw["data_contract"]).strip(),
        collection_contract=str(trading_raw["collection_contract"]).strip(),
        schema_profile=str(trading_raw["schema_profile"]).strip(),
        universe_profile=str(trading_raw["universe_profile"]).strip(),
        classifier_version=str(trading_raw["classifier_version"]).strip(),
        sports_registry_sha256=registry_hash,
        cadence_minutes=_integer(trading_raw["cadence_minutes"], "cadence_minutes"),
        cooperative_budget_seconds=_finite(trading_raw["cooperative_budget_seconds"], "cooperative_budget_seconds"),
        stop_margin_seconds=_finite(trading_raw["stop_margin_seconds"], "stop_margin_seconds"),
        hard_cycle_seconds=_finite(trading_raw["hard_cycle_seconds"], "hard_cycle_seconds"),
        max_receipt_skew_seconds=_finite(trading_raw["max_receipt_skew_seconds"], "max_receipt_skew_seconds"),
        crossing_max_gap_seconds=_finite(trading_raw["crossing_max_gap_seconds"], "crossing_max_gap_seconds"),
        gamma=gamma,
        clob=clob,
        sports_feed=sports_feed,
        research=research,
        storage=storage,
        strategy_source_digest=compute_strategy_source_digest(),
        preregistration_sha256=preregistration_sha256(),
    )
    provisional = BotConfig(
        simulation_mode=resolved_simulation,
        mode=mode,
        job_name=job_name,
        db_path=PROJECT_ROOT / "data" / job_name / storage.database_name,
        registry=registry,
        trading=trading,
        config_hash="",
    )
    payload = provisional.redacted_dict()
    payload.pop("config_hash", None)
    payload.pop("db_path", None)
    config_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = BotConfig(**{**provisional.__dict__, "config_hash": config_hash})
    _validate(result)
    return result
