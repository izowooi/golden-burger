"""Strict, frozen configuration for the Last Mile experiment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)
import yaml

from .source_digest import (
    PROJECT_ROOT,
    compute_strategy_source_digest,
    preregistration_sha256,
)


DATA_CONTRACT = "last-mile-clob-v1"
LIFECYCLE_MODES = frozenset({"archive_only"})
CANONICAL_JOB = "strawberry-shadow-one"
FROZEN_ENTRY_START = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)
FROZEN_ENTRY_END = datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc)
FROZEN_FOLLOWUP_END = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)
ENTRY_THRESHOLDS = (0.90, 0.92, 0.95, 0.97)
STOP_THRESHOLDS = (0.80, 0.85, 0.90)
TARGET_THRESHOLDS = (0.98, 0.99)
PRIMARY_ENTRY_THRESHOLD = 0.95
PRIMARY_STOP_THRESHOLD = 0.85
SPORTS_CLASSIFIER_VERSION = "clob-fields-tags-v1"

# This exact deny-list is shared with Golden Raspberry. Presence, including an
# empty value, is forbidden before any database, log, or network construction.
_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_API_PASSPHRASE",
        "CLOB_API_KEY",
        "CLOB_SECRET",
        "CLOB_PASSPHRASE",
    }
)
_ALLOWED_POLYBOT_ENV_KEYS = frozenset(
    {"POLYBOT_LIFECYCLE_MODE", "POLYBOT_SIMULATION_MODE"}
)


def _utc(value: Any, name: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{name} must be a boolean")


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, str) and value.strip() not in {str(result), f"{result}.0"}:
        raise ValueError(f"{name} must be an integer")
    return result


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must satisfy math.isfinite")
    return result


def _public_origin(value: Any, name: str) -> str:
    text = str(value).strip().rstrip("/")
    try:
        parsed = urlsplit(text)
        parsed.port
    except ValueError as error:
        raise ValueError(f"{name} must be a valid public HTTPS origin") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be a credential-free HTTPS origin")
    return text


def _float_tuple(value: Any, name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    result = tuple(_finite(item, name) for item in value)
    if not result or len(set(result)) != len(result):
        raise ValueError(f"{name} must contain unique values")
    return result


@dataclass(frozen=True)
class GammaConfig:
    base_url: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    retry_max_seconds: float
    resolution_batch_size: int


@dataclass(frozen=True)
class SamplingConfig:
    base_url: str
    page_size: int
    max_pages: int


@dataclass(frozen=True)
class OrderBookConfig:
    base_url: str
    batch_token_limit: int


@dataclass(frozen=True)
class ExperimentConfig:
    entry_start_utc: datetime
    entry_end_utc: datetime
    followup_end_utc: datetime
    entry_thresholds: tuple[float, ...]
    stop_thresholds: tuple[float, ...]
    target_thresholds: tuple[float, ...]
    primary_entry_threshold: float
    primary_stop_threshold: float
    simulated_notional_usdc: float
    prior_gap_max_minutes: float
    sports_classifier_version: str
    base_cost_stress_bps: float
    severe_cost_stress_bps: float
    preregistration_sha256: str


@dataclass(frozen=True)
class StorageConfig:
    busy_timeout_ms: int
    min_free_gib: float
    warn_used_ratio: float
    stop_used_ratio: float
    bot_log_retention_days: int


@dataclass(frozen=True)
class TradingConfig:
    lifecycle_mode: str
    data_contract: str
    cadence_minutes: int
    cadence_offset_minute: int
    sampling: SamplingConfig
    gamma: GammaConfig
    orderbook: OrderBookConfig
    experiment: ExperimentConfig
    storage: StorageConfig
    strategy_source_digest: str


@dataclass(frozen=True)
class BotConfig:
    simulation_mode: bool
    job_name: str
    db_path: Path
    trading: TradingConfig
    config_hash: str

    def redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["db_path"] = str(self.db_path)
        experiment = payload["trading"]["experiment"]
        for key in ("entry_start_utc", "entry_end_utc", "followup_end_utc"):
            experiment[key] = getattr(self.trading.experiment, key).isoformat()
        return payload


def assert_no_credentials(env: Mapping[str, str] | None = None) -> None:
    values = os.environ if env is None else env
    present = sorted(key for key in _CREDENTIAL_ENV_KEYS if key in values)
    if present:
        raise ValueError(
            "Golden Strawberry refuses credential-bearing environments: "
            + ", ".join(present)
        )


def _mapping(parent: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = parent.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"trading.{name} must be a mapping")
    return value


def _validate_config(config: BotConfig) -> None:
    if not config.simulation_mode:
        raise ValueError("Golden Strawberry can never run live")
    if config.job_name != CANONICAL_JOB:
        raise ValueError(f"job must be {CANONICAL_JOB}")
    trading = config.trading
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError("lifecycle_mode must be archive_only")
    if trading.data_contract != DATA_CONTRACT:
        raise ValueError(f"data_contract must be {DATA_CONTRACT}")
    if trading.cadence_minutes != 10:
        raise ValueError("cadence_minutes must remain 10")
    if trading.cadence_offset_minute != 7:
        raise ValueError("cadence_offset_minute must remain 7")
    sampling = trading.sampling
    if sampling.page_size != 1000 or sampling.max_pages != 100:
        raise ValueError("sampling page_size/max_pages must remain 1000/100")
    if sampling.base_url != "https://clob.polymarket.com":
        raise ValueError("sampling source must remain the public CLOB origin")
    gamma = trading.gamma
    if not (1 <= gamma.resolution_batch_size <= 100):
        raise ValueError("resolution_batch_size must be between 1 and 100")
    if gamma.max_retries < 0 or gamma.max_retries > 10:
        raise ValueError("Gamma retry budget is invalid")
    if not (0 < gamma.retry_base_seconds <= gamma.retry_max_seconds <= 120):
        raise ValueError("Gamma retry delays are invalid")
    if not (1 <= trading.orderbook.batch_token_limit <= 500):
        raise ValueError("orderbook batch_token_limit must be between 1 and 500")
    experiment = trading.experiment
    if (
        experiment.entry_start_utc != FROZEN_ENTRY_START
        or experiment.entry_end_utc != FROZEN_ENTRY_END
        or experiment.followup_end_utc != FROZEN_FOLLOWUP_END
    ):
        raise ValueError("experiment clocks must match the frozen preregistration")
    if not (
        experiment.entry_start_utc
        < experiment.entry_end_utc
        < experiment.followup_end_utc
    ):
        raise ValueError("experiment clocks must be strictly ordered")
    if experiment.entry_thresholds != ENTRY_THRESHOLDS:
        raise ValueError("entry thresholds are frozen")
    if experiment.stop_thresholds != STOP_THRESHOLDS:
        raise ValueError("stop thresholds are frozen")
    if experiment.target_thresholds != TARGET_THRESHOLDS:
        raise ValueError("target thresholds are frozen")
    if (
        experiment.primary_entry_threshold != PRIMARY_ENTRY_THRESHOLD
        or experiment.primary_stop_threshold != PRIMARY_STOP_THRESHOLD
    ):
        raise ValueError("primary policy must remain entry 0.95 / stop 0.85")
    if experiment.simulated_notional_usdc != 5:
        raise ValueError("hypothetical notional must remain $5")
    if experiment.prior_gap_max_minutes != 25:
        raise ValueError("prior gap maximum must remain 25 minutes")
    if experiment.sports_classifier_version != SPORTS_CLASSIFIER_VERSION:
        raise ValueError("sports classifier version is frozen")
    if (
        experiment.base_cost_stress_bps != 10.4
        or experiment.severe_cost_stress_bps != 72.5
    ):
        raise ValueError("round-trip cost stresses must remain 10.4/72.5 bps")
    storage = trading.storage
    if storage.min_free_gib < 100:
        raise ValueError("storage free-space floor cannot be loosened")
    if not (0 < storage.warn_used_ratio < storage.stop_used_ratio <= 0.90):
        raise ValueError("storage ratios must preserve the 90% stop")
    if storage.bot_log_retention_days != 45:
        raise ValueError("bot log retention must remain 45 days")


def load_config(
    path: str | Path = "config.yaml",
    job_name: str = CANONICAL_JOB,
    *,
    simulation_mode: bool | None = None,
) -> BotConfig:
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
    if job_name != CANONICAL_JOB:
        raise ValueError(f"job must be {CANONICAL_JOB}")

    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    trading_raw = get_trading_config_mapping(raw)
    yaml_simulation = _boolean(raw.get("simulation_mode"), "simulation_mode")
    env_simulation = os.environ.get("POLYBOT_SIMULATION_MODE")
    resolved_simulation = (
        _boolean(env_simulation, "POLYBOT_SIMULATION_MODE")
        if env_simulation is not None
        else yaml_simulation
    )
    if simulation_mode is not None and simulation_mode != resolved_simulation:
        raise ValueError("CLI mode contradicts resolved simulation_mode")

    sampling_raw = _mapping(trading_raw, "sampling")
    gamma_raw = _mapping(trading_raw, "gamma")
    book_raw = _mapping(trading_raw, "orderbook")
    experiment_raw = _mapping(trading_raw, "experiment")
    storage_raw = _mapping(trading_raw, "storage")
    sampling = SamplingConfig(
        base_url=_public_origin(sampling_raw["base_url"], "sampling.base_url"),
        page_size=_integer(sampling_raw["page_size"], "sampling.page_size"),
        max_pages=_integer(sampling_raw["max_pages"], "sampling.max_pages"),
    )
    gamma = GammaConfig(
        base_url=_public_origin(gamma_raw["base_url"], "gamma.base_url"),
        connect_timeout_seconds=_finite(
            gamma_raw["connect_timeout_seconds"], "gamma.connect_timeout_seconds"
        ),
        read_timeout_seconds=_finite(
            gamma_raw["read_timeout_seconds"], "gamma.read_timeout_seconds"
        ),
        max_retries=_integer(gamma_raw["max_retries"], "gamma.max_retries"),
        retry_base_seconds=_finite(
            gamma_raw["retry_base_seconds"], "gamma.retry_base_seconds"
        ),
        retry_max_seconds=_finite(
            gamma_raw["retry_max_seconds"], "gamma.retry_max_seconds"
        ),
        resolution_batch_size=_integer(
            gamma_raw["resolution_batch_size"], "gamma.resolution_batch_size"
        ),
    )
    orderbook = OrderBookConfig(
        base_url=_public_origin(book_raw["base_url"], "orderbook.base_url"),
        batch_token_limit=_integer(
            book_raw["batch_token_limit"], "orderbook.batch_token_limit"
        ),
    )
    experiment = ExperimentConfig(
        entry_start_utc=_utc(
            experiment_raw["entry_start_utc"], "experiment.entry_start_utc"
        ),
        entry_end_utc=_utc(experiment_raw["entry_end_utc"], "experiment.entry_end_utc"),
        followup_end_utc=_utc(
            experiment_raw["followup_end_utc"], "experiment.followup_end_utc"
        ),
        entry_thresholds=_float_tuple(
            experiment_raw["entry_thresholds"], "experiment.entry_thresholds"
        ),
        stop_thresholds=_float_tuple(
            experiment_raw["stop_thresholds"], "experiment.stop_thresholds"
        ),
        target_thresholds=_float_tuple(
            experiment_raw["target_thresholds"], "experiment.target_thresholds"
        ),
        primary_entry_threshold=_finite(
            experiment_raw["primary_entry_threshold"],
            "experiment.primary_entry_threshold",
        ),
        primary_stop_threshold=_finite(
            experiment_raw["primary_stop_threshold"],
            "experiment.primary_stop_threshold",
        ),
        simulated_notional_usdc=_finite(
            experiment_raw["simulated_notional_usdc"],
            "experiment.simulated_notional_usdc",
        ),
        prior_gap_max_minutes=_finite(
            experiment_raw["prior_gap_max_minutes"],
            "experiment.prior_gap_max_minutes",
        ),
        sports_classifier_version=str(
            experiment_raw["sports_classifier_version"]
        ).strip(),
        base_cost_stress_bps=_finite(
            experiment_raw["base_cost_stress_bps"],
            "experiment.base_cost_stress_bps",
        ),
        severe_cost_stress_bps=_finite(
            experiment_raw["severe_cost_stress_bps"],
            "experiment.severe_cost_stress_bps",
        ),
        preregistration_sha256=preregistration_sha256(),
    )
    storage = StorageConfig(
        busy_timeout_ms=_integer(
            storage_raw["busy_timeout_ms"], "storage.busy_timeout_ms"
        ),
        min_free_gib=_finite(storage_raw["min_free_gib"], "storage.min_free_gib"),
        warn_used_ratio=_finite(
            storage_raw["warn_used_ratio"], "storage.warn_used_ratio"
        ),
        stop_used_ratio=_finite(
            storage_raw["stop_used_ratio"], "storage.stop_used_ratio"
        ),
        bot_log_retention_days=_integer(
            storage_raw["bot_log_retention_days"],
            "storage.bot_log_retention_days",
        ),
    )
    lifecycle = str(
        os.environ.get("POLYBOT_LIFECYCLE_MODE", trading_raw.get("lifecycle_mode", ""))
    ).strip()
    resolved_trading = TradingConfig(
        lifecycle_mode=lifecycle,
        data_contract=str(trading_raw.get("data_contract", "")).strip(),
        cadence_minutes=_integer(
            trading_raw.get("cadence_minutes"), "trading.cadence_minutes"
        ),
        cadence_offset_minute=_integer(
            trading_raw.get("cadence_offset_minute"),
            "trading.cadence_offset_minute",
        ),
        sampling=sampling,
        gamma=gamma,
        orderbook=orderbook,
        experiment=experiment,
        storage=storage,
        strategy_source_digest=compute_strategy_source_digest(),
    )
    validate_yaml_config_shape(raw, resolved_trading)
    provisional = BotConfig(
        simulation_mode=resolved_simulation,
        job_name=job_name,
        db_path=PROJECT_ROOT / "data" / CANONICAL_JOB / "trades_sim.db",
        trading=resolved_trading,
        config_hash="",
    )
    payload = provisional.redacted_dict()
    payload.pop("config_hash", None)
    config_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = BotConfig(**{**provisional.__dict__, "config_hash": config_hash})
    _validate_config(result)
    return result


__all__ = [
    "BotConfig",
    "CANONICAL_JOB",
    "DATA_CONTRACT",
    "ENTRY_THRESHOLDS",
    "FROZEN_ENTRY_END",
    "FROZEN_ENTRY_START",
    "FROZEN_FOLLOWUP_END",
    "GammaConfig",
    "LIFECYCLE_MODES",
    "OrderBookConfig",
    "SamplingConfig",
    "PRIMARY_ENTRY_THRESHOLD",
    "PRIMARY_STOP_THRESHOLD",
    "PROJECT_ROOT",
    "SPORTS_CLASSIFIER_VERSION",
    "STOP_THRESHOLDS",
    "TARGET_THRESHOLDS",
    "_CREDENTIAL_ENV_KEYS",
    "assert_no_credentials",
    "load_config",
]
