"""Strict resolved configuration for the accountless research collector."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
from typing import Any, TypeVar
from urllib.parse import urlsplit

from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)
import yaml

from .source_digest import compute_strategy_source_digest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DATA_CONTRACT = "research-full-v1"
LIFECYCLE_MODES = frozenset({"archive_only"})
DEPTH_NOTIONALS = (1.0, 5.0, 10.0, 100.0, 1_000.0, 10_000.0)
_JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_API_PASSPHRASE",
        "CLOB_API_KEY",
        "CLOB_SECRET",
        "CLOB_PASSPHRASE",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE",
    }
)
_ALLOWED_POLYBOT_ENV_KEYS = frozenset(
    {
        "POLYBOT_LIFECYCLE_MODE",
        "POLYBOT_CADENCE_MINUTES",
        "POLYBOT_DATA_CONTRACT",
        "POLYBOT_SIMULATION_MODE",
        "POLYBOT_GAMMA_BASE_URL",
        "POLYBOT_GAMMA_PAGE_SIZE",
        "POLYBOT_GAMMA_MAX_PAGES",
        "POLYBOT_GAMMA_CONNECT_TIMEOUT_SECONDS",
        "POLYBOT_GAMMA_READ_TIMEOUT_SECONDS",
        "POLYBOT_GAMMA_MAX_RETRIES",
        "POLYBOT_GAMMA_RETRY_BASE_SECONDS",
        "POLYBOT_GAMMA_RETRY_MAX_SECONDS",
        "POLYBOT_GAMMA_MIN_LIQUIDITY",
        "POLYBOT_GAMMA_MIN_TOTAL_VOLUME",
        "POLYBOT_GAMMA_MAX_END_HORIZON_DAYS",
        "POLYBOT_DATA_API_BASE_URL",
        "POLYBOT_DATA_TRADE_LIMIT",
        "POLYBOT_TRADE_SAFETY_LAG_SECONDS",
        "POLYBOT_TRADE_OVERLAP_SECONDS",
        "POLYBOT_TRADE_INITIAL_LOOKBACK_HOURS",
        "POLYBOT_TRADE_CATCHUP_CHUNK_SECONDS",
        "POLYBOT_TRADE_MAX_REQUEST_ATTEMPTS_PER_CYCLE",
        "POLYBOT_TRADE_MAX_WINDOWS_PER_CYCLE",
        "POLYBOT_TRADE_RUNTIME_BUDGET_SECONDS",
        "POLYBOT_CLOB_BASE_URL",
        "POLYBOT_ORDERBOOK_BUCKET_COUNT",
        "POLYBOT_MAX_MARKETS_PER_CYCLE",
        "POLYBOT_ORDERBOOK_LEVELS",
        "POLYBOT_ORDERBOOK_BATCH_TOKEN_LIMIT",
        "POLYBOT_RESOLUTION_LOOKUPS_PER_CYCLE",
        "POLYBOT_RESOLUTION_BATCH_SIZE",
        "POLYBOT_RAW_PAYLOAD_EVERY_CYCLES",
        "POLYBOT_MIN_FREE_GIB",
        "POLYBOT_WARN_USED_RATIO",
        "POLYBOT_STOP_USED_RATIO",
        "POLYBOT_BUSY_TIMEOUT_MS",
    }
)


def _validate_public_base_url(name: str, value: str) -> None:
    """Require a credential-free HTTPS origin for an accountless client."""
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError(f"{name} must be a credential-free HTTPS origin")
    try:
        parsed = urlsplit(value)
        # Accessing ``port`` validates malformed/non-numeric port declarations.
        parsed.port
    except ValueError as error:
        raise ValueError(
            f"{name} must be a valid credential-free HTTPS origin"
        ) from error
    if parsed.scheme != "https" or not parsed.netloc or parsed.hostname is None:
        raise ValueError(f"{name} must use https with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{name} must not contain account credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(
            f"{name} must be a base origin without path, query, or fragment"
        )


@dataclass(frozen=True)
class GammaConfig:
    base_url: str = "https://gamma-api.polymarket.com"
    page_size: int = 100
    max_pages: int = 10_000
    connect_timeout_seconds: float = 3.05
    read_timeout_seconds: float = 30.0
    max_retries: int = 6
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 60.0
    min_liquidity: float = 10_000.0
    min_total_volume: float = 2_000.0
    max_end_horizon_days: int = 120


@dataclass(frozen=True)
class OrderBookConfig:
    base_url: str = "https://clob.polymarket.com"
    bucket_count: int = 48
    max_markets_per_cycle: int = 400
    normalized_levels: int = 10
    batch_token_limit: int = 500


@dataclass(frozen=True)
class DataApiConfig:
    base_url: str = "https://data-api.polymarket.com"
    trade_limit: int = 10_000
    safety_lag_seconds: int = 300
    overlap_seconds: int = 1800
    initial_lookback_hours: int = 24
    catchup_chunk_seconds: int = 3600
    max_request_attempts_per_cycle: int = 64
    max_windows_per_cycle: int = 32
    runtime_budget_seconds: float = 120.0


@dataclass(frozen=True)
class ResolutionConfig:
    max_condition_lookups_per_cycle: int = 50
    batch_size: int = 50


@dataclass(frozen=True)
class StorageConfig:
    raw_payload_every_cycles: int = 1
    min_free_gib: float = 150.0
    warn_used_ratio: float = 0.70
    stop_used_ratio: float = 0.80
    busy_timeout_ms: int = 30_000


@dataclass(frozen=True)
class TradingConfig:
    lifecycle_mode: str = "archive_only"
    cadence_minutes: int = 15
    data_contract: str = RESEARCH_DATA_CONTRACT
    strategy_source_digest: str = ""
    gamma: GammaConfig = field(default_factory=GammaConfig)
    data_api: DataApiConfig = field(default_factory=DataApiConfig)
    orderbook: OrderBookConfig = field(default_factory=OrderBookConfig)
    resolution: ResolutionConfig = field(default_factory=ResolutionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    @property
    def collects_full_universe(self) -> bool:
        return self.lifecycle_mode == "archive_only"

    @property
    def collects_resolution_only(self) -> bool:
        return False


@dataclass(frozen=True)
class BotConfig:
    trading: TradingConfig
    db_path: Path
    simulation_mode: bool = True
    job_name: str = "pomegranate-research"


T = TypeVar("T", int, float)


def _get_config_value(
    env: Mapping[str, str],
    env_key: str,
    yaml_value: Any,
    default: T,
    value_type: type[T] = float,
) -> T:
    """Resolve a strict numeric value with env > YAML > default precedence."""
    raw: Any = env[env_key] if env_key in env else yaml_value
    if raw is None:
        raw = default
    if isinstance(raw, bool):
        raise ValueError(f"{env_key} must be a strict numeric value, not boolean")
    if env_key not in env:
        if not isinstance(raw, (int, float)):
            raise ValueError(f"{env_key} YAML value must be numeric")
        if value_type is int and not isinstance(raw, int):
            raise ValueError(f"{env_key} YAML value must be an integer")
    try:
        value = value_type(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{env_key} must be a valid {value_type.__name__}") from error
    if value_type is int and isinstance(raw, str):
        stripped = raw.strip()
        if not stripped or any(character in stripped.lower() for character in ".e"):
            raise ValueError(f"{env_key} must be an integer")
    if not math.isfinite(float(value)):
        raise ValueError(f"{env_key} must be finite")
    return value


def _get_string_value(
    env: Mapping[str, str], env_key: str, yaml_value: Any, default: str
) -> str:
    raw: Any = env[env_key] if env_key in env else yaml_value
    if raw is None:
        raw = default
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{env_key} must be a non-empty string")
    return raw.strip()


def _get_bool_value(
    env: Mapping[str, str], env_key: str, yaml_value: Any, default: bool
) -> bool:
    raw: Any = env[env_key] if env_key in env else yaml_value
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{env_key} must be a boolean")


def _get_lifecycle_mode(
    env: Mapping[str, str], yaml_value: Any, default: str = "archive_only"
) -> str:
    value = _get_string_value(env, "POLYBOT_LIFECYCLE_MODE", yaml_value, default)
    if value not in LIFECYCLE_MODES:
        raise ValueError(
            "POLYBOT_LIFECYCLE_MODE must be archive_only for this research-only collector"
        )
    return value


def assert_no_credentials(env: Mapping[str, str] | None = None) -> None:
    """Fail closed when an account credential is injected into this process."""
    source = os.environ if env is None else env
    present = sorted(key for key in _CREDENTIAL_ENV_KEYS if key in source)
    if present:
        raise ValueError(
            "Golden Pomegranate is accountless; credential environment variables "
            f"are forbidden: {present}"
        )


def _assert_no_unknown_polybot_env(env: Mapping[str, str]) -> None:
    unknown = sorted(
        key
        for key in env
        if key.startswith("POLYBOT_") and key not in _ALLOWED_POLYBOT_ENV_KEYS
    )
    if unknown:
        raise ValueError(f"unknown POLYBOT environment variables: {unknown}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} config must be a mapping")
    return value


def _validate_config(trading: TradingConfig) -> None:
    """Validate the full resolved collector contract before any I/O."""
    gamma = trading.gamma
    data_api = trading.data_api
    books = trading.orderbook
    resolution = trading.resolution
    storage = trading.storage
    numeric_values = {
        "cadence_minutes": trading.cadence_minutes,
        "gamma.page_size": gamma.page_size,
        "gamma.max_pages": gamma.max_pages,
        "gamma.connect_timeout_seconds": gamma.connect_timeout_seconds,
        "gamma.read_timeout_seconds": gamma.read_timeout_seconds,
        "gamma.max_retries": gamma.max_retries,
        "gamma.retry_base_seconds": gamma.retry_base_seconds,
        "gamma.retry_max_seconds": gamma.retry_max_seconds,
        "gamma.min_liquidity": gamma.min_liquidity,
        "gamma.min_total_volume": gamma.min_total_volume,
        "gamma.max_end_horizon_days": gamma.max_end_horizon_days,
        "data_api.trade_limit": data_api.trade_limit,
        "data_api.safety_lag_seconds": data_api.safety_lag_seconds,
        "data_api.overlap_seconds": data_api.overlap_seconds,
        "data_api.initial_lookback_hours": data_api.initial_lookback_hours,
        "data_api.catchup_chunk_seconds": data_api.catchup_chunk_seconds,
        "data_api.max_request_attempts_per_cycle": (
            data_api.max_request_attempts_per_cycle
        ),
        "data_api.max_windows_per_cycle": data_api.max_windows_per_cycle,
        "data_api.runtime_budget_seconds": data_api.runtime_budget_seconds,
        "orderbook.bucket_count": books.bucket_count,
        "orderbook.max_markets_per_cycle": books.max_markets_per_cycle,
        "orderbook.normalized_levels": books.normalized_levels,
        "orderbook.batch_token_limit": books.batch_token_limit,
        "resolution.max_condition_lookups_per_cycle": resolution.max_condition_lookups_per_cycle,
        "resolution.batch_size": resolution.batch_size,
        "storage.raw_payload_every_cycles": storage.raw_payload_every_cycles,
        "storage.min_free_gib": storage.min_free_gib,
        "storage.warn_used_ratio": storage.warn_used_ratio,
        "storage.stop_used_ratio": storage.stop_used_ratio,
        "storage.busy_timeout_ms": storage.busy_timeout_ms,
    }
    for name, value in numeric_values.items():
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError("invalid lifecycle_mode")
    if trading.data_contract != RESEARCH_DATA_CONTRACT:
        if trading.data_contract == "compact-v1":
            raise ValueError("compact-v1 is forbidden for Golden Pomegranate")
        raise ValueError(f"data_contract must be {RESEARCH_DATA_CONTRACT}")
    if trading.cadence_minutes <= 0:
        raise ValueError("cadence_minutes must be > 0")
    if trading.cadence_minutes not in {15, 30, 60}:
        raise ValueError("cadence_minutes must be one of: 15, 30, 60")
    _validate_public_base_url("gamma.base_url", gamma.base_url)
    if not 1 <= gamma.page_size <= 100:
        raise ValueError("gamma.page_size must be in [1, 100]")
    if gamma.max_pages <= 0:
        raise ValueError("gamma.max_pages must be > 0")
    if gamma.connect_timeout_seconds <= 0 or gamma.read_timeout_seconds <= 0:
        raise ValueError("Gamma timeouts must be > 0")
    if gamma.max_retries <= 0:
        raise ValueError("gamma.max_retries must be > 0")
    if gamma.retry_base_seconds < 0 or gamma.retry_max_seconds <= 0:
        raise ValueError("Gamma retry delays must be non-negative/positive")
    if gamma.retry_base_seconds > gamma.retry_max_seconds:
        raise ValueError("retry_base_seconds must be <= retry_max_seconds")
    if gamma.min_liquidity < 10_000:
        raise ValueError("gamma.min_liquidity cannot be below the $10,000 storage gate")
    if gamma.min_total_volume < 2_000:
        raise ValueError(
            "gamma.min_total_volume cannot be below the $2,000 storage gate"
        )
    if not 1 <= gamma.max_end_horizon_days <= 120:
        raise ValueError("gamma.max_end_horizon_days must be in [1, 120]")
    _validate_public_base_url("data_api.base_url", data_api.base_url)
    if data_api.trade_limit != 10_000:
        raise ValueError("data_api.trade_limit is fixed at the public 10000-row cap")
    if data_api.safety_lag_seconds < 0:
        raise ValueError("data_api.safety_lag_seconds must be >= 0")
    if data_api.overlap_seconds < 0:
        raise ValueError("data_api.overlap_seconds must be >= 0")
    if data_api.initial_lookback_hours <= 0:
        raise ValueError("data_api.initial_lookback_hours must be > 0")
    if not 60 <= data_api.catchup_chunk_seconds <= 3600:
        raise ValueError("data_api.catchup_chunk_seconds must be in [60, 3600]")
    if not 1 <= data_api.max_request_attempts_per_cycle <= 64:
        raise ValueError("data_api.max_request_attempts_per_cycle must be in [1, 64]")
    if not 1 <= data_api.max_windows_per_cycle <= 32:
        raise ValueError("data_api.max_windows_per_cycle must be in [1, 32]")
    if not 1 <= data_api.runtime_budget_seconds <= 120:
        raise ValueError("data_api.runtime_budget_seconds must be in [1, 120]")
    if (
        data_api.safety_lag_seconds != 300
        or data_api.overlap_seconds != 1800
        or data_api.initial_lookback_hours != 24
    ):
        raise ValueError(
            "Data tape preregistration fixes safety_lag=300s, overlap=1800s, "
            "and initial_lookback=24h"
        )
    _validate_public_base_url("orderbook.base_url", books.base_url)
    if books.bucket_count <= 0:
        raise ValueError("orderbook.bucket_count must be > 0")
    if not 1 <= books.max_markets_per_cycle <= 400:
        raise ValueError("orderbook.max_markets_per_cycle must be in [1, 400]")
    if books.normalized_levels <= 0:
        raise ValueError("orderbook.normalized_levels must be > 0")
    if not 1 <= books.batch_token_limit <= 500:
        raise ValueError("orderbook.batch_token_limit must be in [1, 500]")
    if resolution.max_condition_lookups_per_cycle <= 0:
        raise ValueError("resolution lookup limit must be > 0")
    if not 1 <= resolution.batch_size <= resolution.max_condition_lookups_per_cycle:
        raise ValueError("resolution.batch_size must fit within the per-cycle limit")
    if storage.raw_payload_every_cycles <= 0:
        raise ValueError("raw_payload_every_cycles must be > 0")
    if storage.raw_payload_every_cycles != 1:
        raise ValueError("research-full-v1 requires raw_payload_every_cycles=1")
    if storage.min_free_gib < 150:
        raise ValueError("min_free_gib must be >= 150")
    if not 0 < storage.warn_used_ratio < storage.stop_used_ratio < 1:
        raise ValueError("disk ratios must satisfy 0 < warn < stop < 1")
    if storage.warn_used_ratio > 0.70 or storage.stop_used_ratio > 0.80:
        raise ValueError(
            "disk guard overrides may be stricter, but cannot weaken the "
            "preregistered 70% warning / 80% stop thresholds"
        )
    if storage.busy_timeout_ms <= 0:
        raise ValueError("busy_timeout_ms must be > 0")
    if len(trading.strategy_source_digest) != 64:
        raise ValueError("strategy_source_digest must be a SHA-256 hex digest")


def load_config(
    config_path: str | Path = "config.yaml",
    job_name: str = "pomegranate-research",
    env: Mapping[str, str] | None = None,
    simulation_mode: bool | None = None,
) -> BotConfig:
    """Load a strict, accountless, simulation-only resolved configuration."""
    resolved_env: Mapping[str, str] = os.environ if env is None else env
    assert_no_credentials(resolved_env)
    _assert_no_unknown_polybot_env(resolved_env)
    if not isinstance(job_name, str) or not _JOB_NAME.fullmatch(job_name):
        raise ValueError("job_name must be a safe single path component")

    path = Path(config_path)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    else:
        payload = {}
    if "simulation_mode" in payload and payload["simulation_mode"] is None:
        raise ValueError("simulation_mode cannot be null")
    trading_raw = get_trading_config_mapping(payload)
    if "lifecycle_mode" in trading_raw and trading_raw["lifecycle_mode"] is None:
        raise ValueError("trading.lifecycle_mode cannot be null")
    gamma_raw = _mapping(trading_raw.get("gamma"), "trading.gamma")
    data_api_raw = _mapping(trading_raw.get("data_api"), "trading.data_api")
    books_raw = _mapping(trading_raw.get("orderbook"), "trading.orderbook")
    resolution_raw = _mapping(trading_raw.get("resolution"), "trading.resolution")
    storage_raw = _mapping(trading_raw.get("storage"), "trading.storage")

    gamma = GammaConfig(
        base_url=_get_string_value(
            resolved_env,
            "POLYBOT_GAMMA_BASE_URL",
            gamma_raw.get("base_url"),
            GammaConfig.base_url,
        ).rstrip("/"),
        page_size=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_PAGE_SIZE",
            gamma_raw.get("page_size"),
            GammaConfig.page_size,
            int,
        ),
        max_pages=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_MAX_PAGES",
            gamma_raw.get("max_pages"),
            GammaConfig.max_pages,
            int,
        ),
        connect_timeout_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_CONNECT_TIMEOUT_SECONDS",
            gamma_raw.get("connect_timeout_seconds"),
            GammaConfig.connect_timeout_seconds,
        ),
        read_timeout_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_READ_TIMEOUT_SECONDS",
            gamma_raw.get("read_timeout_seconds"),
            GammaConfig.read_timeout_seconds,
        ),
        max_retries=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_MAX_RETRIES",
            gamma_raw.get("max_retries"),
            GammaConfig.max_retries,
            int,
        ),
        retry_base_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_RETRY_BASE_SECONDS",
            gamma_raw.get("retry_base_seconds"),
            GammaConfig.retry_base_seconds,
        ),
        retry_max_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_RETRY_MAX_SECONDS",
            gamma_raw.get("retry_max_seconds"),
            GammaConfig.retry_max_seconds,
        ),
        min_liquidity=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_MIN_LIQUIDITY",
            gamma_raw.get("min_liquidity"),
            GammaConfig.min_liquidity,
        ),
        min_total_volume=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_MIN_TOTAL_VOLUME",
            gamma_raw.get("min_total_volume"),
            GammaConfig.min_total_volume,
        ),
        max_end_horizon_days=_get_config_value(
            resolved_env,
            "POLYBOT_GAMMA_MAX_END_HORIZON_DAYS",
            gamma_raw.get("max_end_horizon_days"),
            GammaConfig.max_end_horizon_days,
            int,
        ),
    )
    data_api = DataApiConfig(
        base_url=_get_string_value(
            resolved_env,
            "POLYBOT_DATA_API_BASE_URL",
            data_api_raw.get("base_url"),
            DataApiConfig.base_url,
        ).rstrip("/"),
        trade_limit=_get_config_value(
            resolved_env,
            "POLYBOT_DATA_TRADE_LIMIT",
            data_api_raw.get("trade_limit"),
            DataApiConfig.trade_limit,
            int,
        ),
        safety_lag_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_SAFETY_LAG_SECONDS",
            data_api_raw.get("safety_lag_seconds"),
            DataApiConfig.safety_lag_seconds,
            int,
        ),
        overlap_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_OVERLAP_SECONDS",
            data_api_raw.get("overlap_seconds"),
            DataApiConfig.overlap_seconds,
            int,
        ),
        initial_lookback_hours=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_INITIAL_LOOKBACK_HOURS",
            data_api_raw.get("initial_lookback_hours"),
            DataApiConfig.initial_lookback_hours,
            int,
        ),
        catchup_chunk_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_CATCHUP_CHUNK_SECONDS",
            data_api_raw.get("catchup_chunk_seconds"),
            DataApiConfig.catchup_chunk_seconds,
            int,
        ),
        max_request_attempts_per_cycle=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_MAX_REQUEST_ATTEMPTS_PER_CYCLE",
            data_api_raw.get("max_request_attempts_per_cycle"),
            DataApiConfig.max_request_attempts_per_cycle,
            int,
        ),
        max_windows_per_cycle=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_MAX_WINDOWS_PER_CYCLE",
            data_api_raw.get("max_windows_per_cycle"),
            DataApiConfig.max_windows_per_cycle,
            int,
        ),
        runtime_budget_seconds=_get_config_value(
            resolved_env,
            "POLYBOT_TRADE_RUNTIME_BUDGET_SECONDS",
            data_api_raw.get("runtime_budget_seconds"),
            DataApiConfig.runtime_budget_seconds,
        ),
    )
    orderbook = OrderBookConfig(
        base_url=_get_string_value(
            resolved_env,
            "POLYBOT_CLOB_BASE_URL",
            books_raw.get("base_url"),
            OrderBookConfig.base_url,
        ).rstrip("/"),
        bucket_count=_get_config_value(
            resolved_env,
            "POLYBOT_ORDERBOOK_BUCKET_COUNT",
            books_raw.get("bucket_count"),
            OrderBookConfig.bucket_count,
            int,
        ),
        max_markets_per_cycle=_get_config_value(
            resolved_env,
            "POLYBOT_MAX_MARKETS_PER_CYCLE",
            books_raw.get("max_markets_per_cycle"),
            OrderBookConfig.max_markets_per_cycle,
            int,
        ),
        normalized_levels=_get_config_value(
            resolved_env,
            "POLYBOT_ORDERBOOK_LEVELS",
            books_raw.get("normalized_levels"),
            OrderBookConfig.normalized_levels,
            int,
        ),
        batch_token_limit=_get_config_value(
            resolved_env,
            "POLYBOT_ORDERBOOK_BATCH_TOKEN_LIMIT",
            books_raw.get("batch_token_limit"),
            OrderBookConfig.batch_token_limit,
            int,
        ),
    )
    resolution = ResolutionConfig(
        max_condition_lookups_per_cycle=_get_config_value(
            resolved_env,
            "POLYBOT_RESOLUTION_LOOKUPS_PER_CYCLE",
            resolution_raw.get("max_condition_lookups_per_cycle"),
            ResolutionConfig.max_condition_lookups_per_cycle,
            int,
        ),
        batch_size=_get_config_value(
            resolved_env,
            "POLYBOT_RESOLUTION_BATCH_SIZE",
            resolution_raw.get("batch_size"),
            ResolutionConfig.batch_size,
            int,
        ),
    )
    storage = StorageConfig(
        raw_payload_every_cycles=_get_config_value(
            resolved_env,
            "POLYBOT_RAW_PAYLOAD_EVERY_CYCLES",
            storage_raw.get("raw_payload_every_cycles"),
            StorageConfig.raw_payload_every_cycles,
            int,
        ),
        min_free_gib=_get_config_value(
            resolved_env,
            "POLYBOT_MIN_FREE_GIB",
            storage_raw.get("min_free_gib"),
            StorageConfig.min_free_gib,
        ),
        warn_used_ratio=_get_config_value(
            resolved_env,
            "POLYBOT_WARN_USED_RATIO",
            storage_raw.get("warn_used_ratio"),
            StorageConfig.warn_used_ratio,
        ),
        stop_used_ratio=_get_config_value(
            resolved_env,
            "POLYBOT_STOP_USED_RATIO",
            storage_raw.get("stop_used_ratio"),
            StorageConfig.stop_used_ratio,
        ),
        busy_timeout_ms=_get_config_value(
            resolved_env,
            "POLYBOT_BUSY_TIMEOUT_MS",
            storage_raw.get("busy_timeout_ms"),
            StorageConfig.busy_timeout_ms,
            int,
        ),
    )
    trading = TradingConfig(
        lifecycle_mode=_get_lifecycle_mode(
            resolved_env, trading_raw.get("lifecycle_mode")
        ),
        cadence_minutes=_get_config_value(
            resolved_env,
            "POLYBOT_CADENCE_MINUTES",
            trading_raw.get("cadence_minutes"),
            TradingConfig.cadence_minutes,
            int,
        ),
        data_contract=_get_string_value(
            resolved_env,
            "POLYBOT_DATA_CONTRACT",
            trading_raw.get("data_contract"),
            RESEARCH_DATA_CONTRACT,
        ),
        strategy_source_digest=compute_strategy_source_digest(PROJECT_ROOT),
        gamma=gamma,
        data_api=data_api,
        orderbook=orderbook,
        resolution=resolution,
        storage=storage,
    )
    validate_yaml_config_shape(payload, trading)
    _validate_config(trading)

    yaml_simulation = payload.get("simulation_mode")
    resolved_simulation = _get_bool_value(
        resolved_env,
        "POLYBOT_SIMULATION_MODE",
        yaml_simulation,
        True,
    )
    if simulation_mode is not None:
        if not isinstance(simulation_mode, bool):
            raise ValueError("simulation_mode must be a boolean")
        resolved_simulation = simulation_mode
    if not resolved_simulation:
        raise ValueError("Golden Pomegranate is research-only; live mode is forbidden")

    data_root = (path.resolve().parent if path.exists() else PROJECT_ROOT) / "data"
    db_path = data_root / job_name / "trades_sim.db"
    return BotConfig(
        trading=trading,
        db_path=db_path,
        simulation_mode=True,
        job_name=job_name,
    )


__all__ = [
    "BotConfig",
    "DataApiConfig",
    "DEPTH_NOTIONALS",
    "GammaConfig",
    "LIFECYCLE_MODES",
    "OrderBookConfig",
    "RESEARCH_DATA_CONTRACT",
    "ResolutionConfig",
    "StorageConfig",
    "TradingConfig",
    "assert_no_credentials",
    "load_config",
]
