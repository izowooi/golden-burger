"""Strict configuration for the compact Last Mile follow-up v2a epoch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from .config import (
    ENTRY_THRESHOLDS,
    FROZEN_ENTRY_END,
    FROZEN_ENTRY_START,
    FROZEN_FOLLOWUP_END,
    PRIMARY_ENTRY_THRESHOLD,
    PRIMARY_STOP_THRESHOLD,
    STOP_THRESHOLDS,
    TARGET_THRESHOLDS,
    GammaConfig,
    OrderBookConfig,
    StorageConfig,
    _boolean,
    _finite,
    _float_tuple,
    _integer,
    _public_origin,
    _utc,
    assert_no_credentials,
)
from .followup_source_digest import (
    compute_followup_source_digest,
    followup_preregistration_sha256,
)
from .source_digest import PROJECT_ROOT


FOLLOWUP_DATA_CONTRACT = "last-mile-clob-followup-v2a"
FOLLOWUP_CANONICAL_JOB = "strawberry-shadow-one-followup-v2a"
V1_DATA_CONTRACT = "last-mile-clob-v1"
V1_SCHEMA_VERSION = 1
V1_CANONICAL_JOB = "strawberry-shadow-one"
V1_SOURCE_RELATIVE_PATH = Path("data/strawberry-shadow-one/trades_sim.db")
FOLLOWUP_DB_RELATIVE_PATH = Path(
    "data/strawberry-shadow-one-followup-v2a/trades_sim.db"
)
LIFECYCLE_MODE = "archive_only"
_ALLOWED_POLYBOT_ENV_KEYS = frozenset(
    {"POLYBOT_LIFECYCLE_MODE", "POLYBOT_SIMULATION_MODE"}
)


def _mapping(parent: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = parent.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"followup.{name} must be a mapping")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{label} keys changed; missing={missing}, extra={extra}"
        )


@dataclass(frozen=True)
class V1SourceConfig:
    db_path: Path
    configured_path: str
    expected_schema_version: int
    expected_data_contract: str
    expected_job_name: str
    expected_entry_start_utc: datetime
    expected_entry_end_utc: datetime
    expected_followup_end_utc: datetime
    minimum_successful_cutoff_utc: datetime
    require_no_sidecars: bool


@dataclass(frozen=True)
class FollowupExperimentConfig:
    entry_start_utc: datetime
    entry_end_utc: datetime
    followup_end_utc: datetime
    entry_thresholds: tuple[float, ...]
    stop_thresholds: tuple[float, ...]
    target_thresholds: tuple[float, ...]
    primary_entry_threshold: float
    primary_stop_threshold: float
    simulated_notional_usdc: float
    preregistration_sha256: str


@dataclass(frozen=True)
class FollowupRuntimeConfig:
    network_cycle_deadline_seconds: float
    pinned_fast_hard_sla_seconds: float
    full_seed_budget_seconds: float


@dataclass(frozen=True)
class FollowupTradingConfig:
    lifecycle_mode: str
    data_contract: str
    cadence_minutes: int
    cadence_offset_minute: int
    v1_source: V1SourceConfig
    gamma: GammaConfig
    orderbook: OrderBookConfig
    experiment: FollowupExperimentConfig
    runtime: FollowupRuntimeConfig
    storage: StorageConfig
    strategy_source_digest: str


@dataclass(frozen=True)
class FollowupConfig:
    simulation_mode: bool
    job_name: str
    db_path: Path
    trading: FollowupTradingConfig
    config_hash: str

    def redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Cohort identity must be stable across the local checkout and the
        # pinned Jenkins workspace. Runtime resolution still uses absolute
        # Path fields, while persisted config identity uses frozen relatives.
        payload["db_path"] = FOLLOWUP_DB_RELATIVE_PATH.as_posix()
        payload["trading"]["v1_source"]["db_path"] = (
            self.trading.v1_source.configured_path
        )
        experiment = payload["trading"]["experiment"]
        for key in ("entry_start_utc", "entry_end_utc", "followup_end_utc"):
            experiment[key] = getattr(self.trading.experiment, key).isoformat()
        source = payload["trading"]["v1_source"]
        for key in (
            "expected_entry_start_utc",
            "expected_entry_end_utc",
            "expected_followup_end_utc",
            "minimum_successful_cutoff_utc",
        ):
            source[key] = getattr(self.trading.v1_source, key).isoformat()
        return payload


def _validate(config: FollowupConfig) -> None:
    if not config.simulation_mode:
        raise ValueError("Golden Strawberry follow-up can never run live")
    if config.job_name != FOLLOWUP_CANONICAL_JOB:
        raise ValueError(f"job must be {FOLLOWUP_CANONICAL_JOB}")
    trading = config.trading
    if trading.lifecycle_mode != LIFECYCLE_MODE:
        raise ValueError("follow-up lifecycle_mode must remain archive_only")
    if trading.data_contract != FOLLOWUP_DATA_CONTRACT:
        raise ValueError(f"data_contract must be {FOLLOWUP_DATA_CONTRACT}")
    if trading.cadence_minutes != 10 or trading.cadence_offset_minute != 7:
        raise ValueError("follow-up cadence must remain 10 minutes at offset 7")
    source = trading.v1_source
    if source.configured_path != V1_SOURCE_RELATIVE_PATH.as_posix():
        raise ValueError("v1 source path must remain the canonical runtime DB path")
    if source.expected_schema_version != V1_SCHEMA_VERSION:
        raise ValueError("v1 expected schema version must remain 1")
    if source.expected_data_contract != V1_DATA_CONTRACT:
        raise ValueError("v1 expected data contract must remain last-mile-clob-v1")
    if source.expected_job_name != V1_CANONICAL_JOB:
        raise ValueError("v1 expected runtime job must remain strawberry-shadow-one")
    if (
        source.expected_entry_start_utc != FROZEN_ENTRY_START
        or source.expected_entry_end_utc != FROZEN_ENTRY_END
        or source.expected_followup_end_utc != FROZEN_FOLLOWUP_END
    ):
        raise ValueError("v1 expected experiment clocks changed")
    if source.minimum_successful_cutoff_utc != FROZEN_ENTRY_END:
        raise ValueError("v1 handoff cannot precede the frozen entry-window end")
    if not source.require_no_sidecars:
        raise ValueError("v1 source sidecars must fail closed")
    if source.db_path == config.db_path:
        raise ValueError("v1 source DB and follow-up DB must be distinct")
    gamma = trading.gamma
    if gamma.resolution_batch_size != 50:
        raise ValueError("resolution_batch_size must remain 50")
    if (
        gamma.connect_timeout_seconds != 3.05
        or gamma.read_timeout_seconds != 30
    ):
        raise ValueError("follow-up public timeouts must remain 3.05/30 seconds")
    if gamma.max_retries != 4:
        raise ValueError("follow-up retry budget must remain four retries")
    if not (0 < gamma.retry_base_seconds <= gamma.retry_max_seconds <= 20):
        raise ValueError("follow-up retry delay envelope changed")
    if trading.orderbook.batch_token_limit != 250:
        raise ValueError("orderbook batch_token_limit must remain 250")
    experiment = trading.experiment
    if (
        experiment.entry_start_utc != FROZEN_ENTRY_START
        or experiment.entry_end_utc != FROZEN_ENTRY_END
        or experiment.followup_end_utc != FROZEN_FOLLOWUP_END
    ):
        raise ValueError("follow-up clocks must preserve the v1 frozen experiment")
    if experiment.entry_thresholds != ENTRY_THRESHOLDS:
        raise ValueError("entry thresholds are inherited and frozen")
    if experiment.stop_thresholds != STOP_THRESHOLDS:
        raise ValueError("stop thresholds are inherited and frozen")
    if experiment.target_thresholds != TARGET_THRESHOLDS:
        raise ValueError("target thresholds are inherited and frozen")
    if (
        experiment.primary_entry_threshold != PRIMARY_ENTRY_THRESHOLD
        or experiment.primary_stop_threshold != PRIMARY_STOP_THRESHOLD
        or experiment.simulated_notional_usdc != 5
    ):
        raise ValueError("follow-up policy must preserve v1 primary parameters")
    runtime = trading.runtime
    if runtime.network_cycle_deadline_seconds != 450:
        raise ValueError("network cycle deadline must remain 450 seconds")
    if runtime.pinned_fast_hard_sla_seconds != 480:
        raise ValueError("PINNED_FAST hard SLA must remain 480 seconds")
    if runtime.full_seed_budget_seconds != 1800:
        raise ValueError("FULL_SEED maintenance budget must remain 1800 seconds")
    if not (
        0
        < runtime.network_cycle_deadline_seconds
        < runtime.pinned_fast_hard_sla_seconds
        < trading.cadence_minutes * 60
    ):
        raise ValueError("runtime deadlines must retain fail-recording cadence margin")
    storage = trading.storage
    if storage.min_free_gib < 100:
        raise ValueError("storage free-space floor cannot be loosened")
    if not (0 < storage.warn_used_ratio < storage.stop_used_ratio <= 0.90):
        raise ValueError("storage ratios must preserve the 90% stop")
    if storage.bot_log_retention_days != 45:
        raise ValueError("bot log retention must remain 45 days")


def load_followup_config(
    path: str | Path = "config.followup-v2a.yaml",
    job_name: str = FOLLOWUP_CANONICAL_JOB,
    *,
    simulation_mode: bool | None = None,
) -> FollowupConfig:
    assert_no_credentials()
    unknown_env = sorted(
        key
        for key in os.environ
        if key.startswith("POLYBOT_") and key not in _ALLOWED_POLYBOT_ENV_KEYS
    )
    if unknown_env:
        raise ValueError(
            "unknown POLYBOT_* environment keys: " + ", ".join(unknown_env)
        )
    if job_name != FOLLOWUP_CANONICAL_JOB:
        raise ValueError(f"job must be {FOLLOWUP_CANONICAL_JOB}")
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or set(raw) != {"simulation_mode", "followup"}:
        raise ValueError("follow-up YAML must contain only simulation_mode and followup")
    followup = raw.get("followup")
    if not isinstance(followup, Mapping):
        raise ValueError("followup must be a mapping")
    _exact_keys(
        followup,
        {
            "lifecycle_mode",
            "data_contract",
            "cadence_minutes",
            "cadence_offset_minute",
            "v1_source",
            "gamma",
            "orderbook",
            "experiment",
            "runtime",
            "storage",
        },
        "followup",
    )
    yaml_simulation = _boolean(raw.get("simulation_mode"), "simulation_mode")
    env_simulation = os.environ.get("POLYBOT_SIMULATION_MODE")
    resolved_simulation = (
        _boolean(env_simulation, "POLYBOT_SIMULATION_MODE")
        if env_simulation is not None
        else yaml_simulation
    )
    if simulation_mode is not None and simulation_mode != resolved_simulation:
        raise ValueError("CLI mode contradicts resolved simulation_mode")

    source_raw = _mapping(followup, "v1_source")
    _exact_keys(
        source_raw,
        {
            "db_path",
            "expected_schema_version",
            "expected_data_contract",
            "expected_job_name",
            "expected_entry_start_utc",
            "expected_entry_end_utc",
            "expected_followup_end_utc",
            "minimum_successful_cutoff_utc",
            "require_no_sidecars",
        },
        "followup.v1_source",
    )
    configured_source = str(source_raw.get("db_path", "")).strip()
    source_path = (PROJECT_ROOT / configured_source).resolve(strict=False)
    source = V1SourceConfig(
        db_path=source_path,
        configured_path=configured_source,
        expected_schema_version=_integer(
            source_raw.get("expected_schema_version"),
            "v1_source.expected_schema_version",
        ),
        expected_data_contract=str(
            source_raw.get("expected_data_contract", "")
        ).strip(),
        expected_job_name=str(source_raw.get("expected_job_name", "")).strip(),
        expected_entry_start_utc=_utc(
            source_raw.get("expected_entry_start_utc"),
            "v1_source.expected_entry_start_utc",
        ),
        expected_entry_end_utc=_utc(
            source_raw.get("expected_entry_end_utc"),
            "v1_source.expected_entry_end_utc",
        ),
        expected_followup_end_utc=_utc(
            source_raw.get("expected_followup_end_utc"),
            "v1_source.expected_followup_end_utc",
        ),
        minimum_successful_cutoff_utc=_utc(
            source_raw.get("minimum_successful_cutoff_utc"),
            "v1_source.minimum_successful_cutoff_utc",
        ),
        require_no_sidecars=_boolean(
            source_raw.get("require_no_sidecars"),
            "v1_source.require_no_sidecars",
        ),
    )
    gamma_raw = _mapping(followup, "gamma")
    _exact_keys(
        gamma_raw,
        {
            "base_url",
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "max_retries",
            "retry_base_seconds",
            "retry_max_seconds",
            "resolution_batch_size",
        },
        "followup.gamma",
    )
    gamma = GammaConfig(
        base_url=_public_origin(gamma_raw.get("base_url"), "gamma.base_url"),
        connect_timeout_seconds=_finite(
            gamma_raw.get("connect_timeout_seconds"),
            "gamma.connect_timeout_seconds",
        ),
        read_timeout_seconds=_finite(
            gamma_raw.get("read_timeout_seconds"), "gamma.read_timeout_seconds"
        ),
        max_retries=_integer(gamma_raw.get("max_retries"), "gamma.max_retries"),
        retry_base_seconds=_finite(
            gamma_raw.get("retry_base_seconds"), "gamma.retry_base_seconds"
        ),
        retry_max_seconds=_finite(
            gamma_raw.get("retry_max_seconds"), "gamma.retry_max_seconds"
        ),
        resolution_batch_size=_integer(
            gamma_raw.get("resolution_batch_size"),
            "gamma.resolution_batch_size",
        ),
    )
    book_raw = _mapping(followup, "orderbook")
    _exact_keys(book_raw, {"base_url", "batch_token_limit"}, "followup.orderbook")
    orderbook = OrderBookConfig(
        base_url=_public_origin(book_raw.get("base_url"), "orderbook.base_url"),
        batch_token_limit=_integer(
            book_raw.get("batch_token_limit"), "orderbook.batch_token_limit"
        ),
    )
    experiment_raw = _mapping(followup, "experiment")
    _exact_keys(
        experiment_raw,
        {
            "entry_start_utc",
            "entry_end_utc",
            "followup_end_utc",
            "entry_thresholds",
            "stop_thresholds",
            "target_thresholds",
            "primary_entry_threshold",
            "primary_stop_threshold",
            "simulated_notional_usdc",
        },
        "followup.experiment",
    )
    experiment = FollowupExperimentConfig(
        entry_start_utc=_utc(
            experiment_raw.get("entry_start_utc"), "experiment.entry_start_utc"
        ),
        entry_end_utc=_utc(
            experiment_raw.get("entry_end_utc"), "experiment.entry_end_utc"
        ),
        followup_end_utc=_utc(
            experiment_raw.get("followup_end_utc"), "experiment.followup_end_utc"
        ),
        entry_thresholds=_float_tuple(
            experiment_raw.get("entry_thresholds"), "experiment.entry_thresholds"
        ),
        stop_thresholds=_float_tuple(
            experiment_raw.get("stop_thresholds"), "experiment.stop_thresholds"
        ),
        target_thresholds=_float_tuple(
            experiment_raw.get("target_thresholds"), "experiment.target_thresholds"
        ),
        primary_entry_threshold=_finite(
            experiment_raw.get("primary_entry_threshold"),
            "experiment.primary_entry_threshold",
        ),
        primary_stop_threshold=_finite(
            experiment_raw.get("primary_stop_threshold"),
            "experiment.primary_stop_threshold",
        ),
        simulated_notional_usdc=_finite(
            experiment_raw.get("simulated_notional_usdc"),
            "experiment.simulated_notional_usdc",
        ),
        preregistration_sha256=followup_preregistration_sha256(),
    )
    runtime_raw = _mapping(followup, "runtime")
    _exact_keys(
        runtime_raw,
        {
            "network_cycle_deadline_seconds",
            "pinned_fast_hard_sla_seconds",
            "full_seed_budget_seconds",
        },
        "followup.runtime",
    )
    runtime = FollowupRuntimeConfig(
        network_cycle_deadline_seconds=_finite(
            runtime_raw.get("network_cycle_deadline_seconds"),
            "runtime.network_cycle_deadline_seconds",
        ),
        pinned_fast_hard_sla_seconds=_finite(
            runtime_raw.get("pinned_fast_hard_sla_seconds"),
            "runtime.pinned_fast_hard_sla_seconds",
        ),
        full_seed_budget_seconds=_finite(
            runtime_raw.get("full_seed_budget_seconds"),
            "runtime.full_seed_budget_seconds",
        ),
    )
    storage_raw = _mapping(followup, "storage")
    _exact_keys(
        storage_raw,
        {
            "busy_timeout_ms",
            "min_free_gib",
            "warn_used_ratio",
            "stop_used_ratio",
            "bot_log_retention_days",
        },
        "followup.storage",
    )
    storage = StorageConfig(
        busy_timeout_ms=_integer(
            storage_raw.get("busy_timeout_ms"), "storage.busy_timeout_ms"
        ),
        min_free_gib=_finite(storage_raw.get("min_free_gib"), "storage.min_free_gib"),
        warn_used_ratio=_finite(
            storage_raw.get("warn_used_ratio"), "storage.warn_used_ratio"
        ),
        stop_used_ratio=_finite(
            storage_raw.get("stop_used_ratio"), "storage.stop_used_ratio"
        ),
        bot_log_retention_days=_integer(
            storage_raw.get("bot_log_retention_days"),
            "storage.bot_log_retention_days",
        ),
    )
    lifecycle = str(
        os.environ.get(
            "POLYBOT_LIFECYCLE_MODE", followup.get("lifecycle_mode", "")
        )
    ).strip()
    trading = FollowupTradingConfig(
        lifecycle_mode=lifecycle,
        data_contract=str(followup.get("data_contract", "")).strip(),
        cadence_minutes=_integer(
            followup.get("cadence_minutes"), "followup.cadence_minutes"
        ),
        cadence_offset_minute=_integer(
            followup.get("cadence_offset_minute"),
            "followup.cadence_offset_minute",
        ),
        v1_source=source,
        gamma=gamma,
        orderbook=orderbook,
        experiment=experiment,
        runtime=runtime,
        storage=storage,
        strategy_source_digest=compute_followup_source_digest(),
    )
    provisional = FollowupConfig(
        simulation_mode=resolved_simulation,
        job_name=job_name,
        db_path=(PROJECT_ROOT / FOLLOWUP_DB_RELATIVE_PATH).resolve(strict=False),
        trading=trading,
        config_hash="",
    )
    payload = provisional.redacted_dict()
    payload.pop("config_hash", None)
    config_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = FollowupConfig(
        **{**provisional.__dict__, "config_hash": config_hash}
    )
    _validate(result)
    return result


__all__ = [
    "FOLLOWUP_CANONICAL_JOB",
    "FOLLOWUP_DATA_CONTRACT",
    "FOLLOWUP_DB_RELATIVE_PATH",
    "FollowupConfig",
    "FollowupExperimentConfig",
    "FollowupRuntimeConfig",
    "FollowupTradingConfig",
    "V1_CANONICAL_JOB",
    "V1_DATA_CONTRACT",
    "V1_SCHEMA_VERSION",
    "V1_SOURCE_RELATIVE_PATH",
    "V1SourceConfig",
    "load_followup_config",
]
