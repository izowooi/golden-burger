"""Strict resolved configuration for the Queue Echo experiment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)
import yaml

from .source_digest import compute_strategy_source_digest, preregistration_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_CONTRACT = "queue-echo-v1"
LIFECYCLE_MODES = frozenset({"archive_only"})
CANONICAL_JOBS: dict[str, tuple[int, int]] = {
    "raspberry-do-shard-0": (0, 0),
    "raspberry-re-shard-1": (1, 1),
    "raspberry-mi-shard-2": (2, 2),
}
_JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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
    {
        "POLYBOT_LIFECYCLE_MODE",
        "POLYBOT_SIMULATION_MODE",
        "POLYBOT_SHARD_INDEX",
        "POLYBOT_SHARD_COUNT",
        "POLYBOT_CADENCE_OFFSET_MINUTE",
        "POLYBOT_EXPERIMENT_START_UTC",
        "POLYBOT_EXPERIMENT_END_UTC",
    }
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


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must satisfy math.isfinite")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(value).strip() not in {str(result), f"{result}.0"} and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return result


def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{name}: simulation_mode must be a boolean")


@dataclass(frozen=True)
class GammaConfig:
    base_url: str
    page_size: int
    max_pages: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    retry_max_seconds: float
    min_liquidity: float
    min_total_volume: float
    min_volume_24h: float
    min_hours_to_end: float
    max_hours_to_end: float


@dataclass(frozen=True)
class OrderBookConfig:
    base_url: str
    batch_token_limit: int
    stored_levels_per_side: int
    near_touch_window: float
    max_spread: float
    min_price: float
    max_price: float
    min_near_touch_notional: float


@dataclass(frozen=True)
class ExperimentConfig:
    start_utc: datetime
    end_utc: datetime
    shard_index: int
    shard_count: int
    cadence_offset_minute: int
    score_threshold: float
    neutral_score_max: float
    weighted_tick_levels: int
    level_weight_decay: float
    followup_minutes: int
    followup_grace_minutes: int
    history_gap_min_minutes: float
    history_gap_max_minutes: float
    cooldown_hours: float
    simulated_notional_usdc: float
    base_cost_stress_bps: float
    severe_taker_stress_bps: float
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
        payload["trading"]["experiment"]["start_utc"] = self.trading.experiment.start_utc.isoformat()
        payload["trading"]["experiment"]["end_utc"] = self.trading.experiment.end_utc.isoformat()
        return payload


def assert_no_credentials(env: Mapping[str, str] | None = None) -> None:
    values = os.environ if env is None else env
    present = sorted(key for key in _CREDENTIAL_ENV_KEYS if key in values)
    if present:
        raise ValueError(
            "Golden Raspberry refuses credential-bearing environments: "
            + ", ".join(present)
        )


def _get_config_value(
    mapping: Mapping[str, Any], key: str, env_key: str | None = None
) -> Any:
    if env_key and env_key in os.environ:
        return os.environ[env_key]
    if key not in mapping:
        raise ValueError(f"missing config value: {key}")
    return mapping[key]


def _get_lifecycle_mode(trading: Mapping[str, Any]) -> str:
    value = str(
        _get_config_value(trading, "lifecycle_mode", "POLYBOT_LIFECYCLE_MODE")
    ).strip()
    if value not in LIFECYCLE_MODES:
        raise ValueError("lifecycle_mode must be archive_only")
    return value


def _validate_config(config: BotConfig) -> None:
    if not config.simulation_mode:
        raise ValueError("Golden Raspberry can never run live")
    if config.trading.lifecycle_mode != "archive_only":
        raise ValueError("lifecycle must remain archive_only")
    if config.trading.data_contract != DATA_CONTRACT:
        raise ValueError(f"data_contract must be {DATA_CONTRACT}")
    if config.trading.cadence_minutes != 5:
        raise ValueError("cadence_minutes must remain 5")
    gamma = config.trading.gamma
    if not (1 <= gamma.page_size <= 100 and 1 <= gamma.max_pages <= 100):
        raise ValueError("Gamma page budget is invalid")
    if gamma.min_liquidity < 20_000 or gamma.min_total_volume < 10_000:
        raise ValueError("Gamma server filters cannot be loosened")
    if gamma.min_volume_24h != 2_000:
        raise ValueError("min_volume_24h is preregistered at 2000")
    if not (gamma.min_hours_to_end == 6 and gamma.max_hours_to_end == 2160):
        raise ValueError("horizon must remain [6h, 2160h]")
    book = config.trading.orderbook
    if not (2 <= book.batch_token_limit <= 500):
        raise ValueError("batch_token_limit must be between 2 and 500")
    if not (
        book.near_touch_window == 0.02
        and book.max_spread == 0.02
        and book.min_price == 0.20
        and book.max_price == 0.80
        and book.min_near_touch_notional == 50
    ):
        raise ValueError("order-book gates are frozen by preregistration")
    exp = config.trading.experiment
    if exp.end_utc - exp.start_utc != timedelta(days=30):
        raise ValueError("experiment window must be exactly 30 days")
    expected_shard, expected_offset = CANONICAL_JOBS[config.job_name]
    if exp.shard_count != 3 or exp.shard_index != expected_shard:
        raise ValueError("job and hash shard identity do not match")
    if exp.cadence_offset_minute != expected_offset:
        raise ValueError("job and cadence offset do not match")
    if not (
        exp.score_threshold == 0.50
        and exp.neutral_score_max == 0.10
        and exp.weighted_tick_levels == 3
        and exp.level_weight_decay == 0.50
        and exp.followup_minutes == 60
        and exp.followup_grace_minutes == 15
        and exp.history_gap_min_minutes == 3
        and exp.history_gap_max_minutes == 10
        and exp.cooldown_hours == 6
        and exp.simulated_notional_usdc == 5
        and exp.base_cost_stress_bps == 10.4
        and exp.severe_taker_stress_bps == 72.5
    ):
        raise ValueError("experiment thresholds are frozen by preregistration")
    storage = config.trading.storage
    if not (0 < storage.warn_used_ratio < storage.stop_used_ratio < 1):
        raise ValueError("storage ratios must be ordered inside (0,1)")
    if storage.min_free_gib < 30:
        raise ValueError("storage free-space floor cannot be loosened")


def load_config(
    path: str | Path = "config.yaml",
    job_name: str = "raspberry-re-shard-1",
    *,
    simulation_mode: bool | None = None,
) -> BotConfig:
    assert_no_credentials()
    unknown = sorted(
        key
        for key in os.environ
        if key.startswith("POLYBOT_") and key not in _ALLOWED_POLYBOT_ENV_KEYS
    )
    if unknown:
        raise ValueError("unknown POLYBOT_* environment keys: " + ", ".join(unknown))
    if not _JOB_NAME.fullmatch(job_name) or job_name not in CANONICAL_JOBS:
        raise ValueError(
            "job must be one of: " + ", ".join(sorted(CANONICAL_JOBS))
        )
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    trading = get_trading_config_mapping(raw)
    yaml_sim = _boolean(raw.get("simulation_mode"), "simulation_mode")
    env_sim = os.environ.get("POLYBOT_SIMULATION_MODE")
    resolved_sim = _boolean(env_sim, "simulation_mode") if env_sim is not None else yaml_sim
    if simulation_mode is not None and simulation_mode != resolved_sim:
        raise ValueError("CLI mode contradicts resolved simulation_mode")

    gamma_raw = trading.get("gamma")
    book_raw = trading.get("orderbook")
    exp_raw = trading.get("experiment")
    storage_raw = trading.get("storage")
    if not all(isinstance(value, Mapping) for value in (gamma_raw, book_raw, exp_raw, storage_raw)):
        raise ValueError("gamma, orderbook, experiment, and storage must be mappings")
    expected_shard, expected_offset = CANONICAL_JOBS[job_name]
    shard_index = _integer(
        os.environ.get("POLYBOT_SHARD_INDEX", expected_shard),
        "shard_index",
    )
    shard_count = _integer(os.environ.get("POLYBOT_SHARD_COUNT", 3), "shard_count")
    offset = _integer(
        os.environ.get("POLYBOT_CADENCE_OFFSET_MINUTE", expected_offset),
        "cadence_offset_minute",
    )
    start = _utc(
        os.environ.get("POLYBOT_EXPERIMENT_START_UTC", exp_raw["start_utc"]),
        "experiment.start_utc",
    )
    end = _utc(
        os.environ.get("POLYBOT_EXPERIMENT_END_UTC", exp_raw["end_utc"]),
        "experiment.end_utc",
    )
    gamma = GammaConfig(
        base_url=_public_origin(gamma_raw["base_url"], "gamma.base_url"),
        page_size=_integer(gamma_raw["page_size"], "gamma.page_size"),
        max_pages=_integer(gamma_raw["max_pages"], "gamma.max_pages"),
        connect_timeout_seconds=_finite(gamma_raw["connect_timeout_seconds"], "gamma.connect_timeout_seconds"),
        read_timeout_seconds=_finite(gamma_raw["read_timeout_seconds"], "gamma.read_timeout_seconds"),
        max_retries=_integer(gamma_raw["max_retries"], "gamma.max_retries"),
        retry_base_seconds=_finite(gamma_raw["retry_base_seconds"], "gamma.retry_base_seconds"),
        retry_max_seconds=_finite(gamma_raw["retry_max_seconds"], "gamma.retry_max_seconds"),
        min_liquidity=_finite(gamma_raw["min_liquidity"], "gamma.min_liquidity"),
        min_total_volume=_finite(gamma_raw["min_total_volume"], "gamma.min_total_volume"),
        min_volume_24h=_finite(gamma_raw["min_volume_24h"], "gamma.min_volume_24h"),
        min_hours_to_end=_finite(gamma_raw["min_hours_to_end"], "gamma.min_hours_to_end"),
        max_hours_to_end=_finite(gamma_raw["max_hours_to_end"], "gamma.max_hours_to_end"),
    )
    book = OrderBookConfig(
        base_url=_public_origin(book_raw["base_url"], "orderbook.base_url"),
        batch_token_limit=_integer(book_raw["batch_token_limit"], "orderbook.batch_token_limit"),
        stored_levels_per_side=_integer(book_raw["stored_levels_per_side"], "orderbook.stored_levels_per_side"),
        near_touch_window=_finite(book_raw["near_touch_window"], "orderbook.near_touch_window"),
        max_spread=_finite(book_raw["max_spread"], "orderbook.max_spread"),
        min_price=_finite(book_raw["min_price"], "orderbook.min_price"),
        max_price=_finite(book_raw["max_price"], "orderbook.max_price"),
        min_near_touch_notional=_finite(book_raw["min_near_touch_notional"], "orderbook.min_near_touch_notional"),
    )
    experiment = ExperimentConfig(
        start_utc=start,
        end_utc=end,
        shard_index=shard_index,
        shard_count=shard_count,
        cadence_offset_minute=offset,
        score_threshold=_finite(exp_raw["score_threshold"], "experiment.score_threshold"),
        neutral_score_max=_finite(exp_raw["neutral_score_max"], "experiment.neutral_score_max"),
        weighted_tick_levels=_integer(exp_raw["weighted_tick_levels"], "experiment.weighted_tick_levels"),
        level_weight_decay=_finite(exp_raw["level_weight_decay"], "experiment.level_weight_decay"),
        followup_minutes=_integer(exp_raw["followup_minutes"], "experiment.followup_minutes"),
        followup_grace_minutes=_integer(exp_raw["followup_grace_minutes"], "experiment.followup_grace_minutes"),
        history_gap_min_minutes=_finite(exp_raw["history_gap_min_minutes"], "experiment.history_gap_min_minutes"),
        history_gap_max_minutes=_finite(exp_raw["history_gap_max_minutes"], "experiment.history_gap_max_minutes"),
        cooldown_hours=_finite(exp_raw["cooldown_hours"], "experiment.cooldown_hours"),
        simulated_notional_usdc=_finite(exp_raw["simulated_notional_usdc"], "experiment.simulated_notional_usdc"),
        base_cost_stress_bps=_finite(exp_raw["base_cost_stress_bps"], "experiment.base_cost_stress_bps"),
        severe_taker_stress_bps=_finite(exp_raw["severe_taker_stress_bps"], "experiment.severe_taker_stress_bps"),
        preregistration_sha256=preregistration_sha256(),
    )
    storage = StorageConfig(
        busy_timeout_ms=_integer(storage_raw["busy_timeout_ms"], "storage.busy_timeout_ms"),
        min_free_gib=_finite(storage_raw["min_free_gib"], "storage.min_free_gib"),
        warn_used_ratio=_finite(storage_raw["warn_used_ratio"], "storage.warn_used_ratio"),
        stop_used_ratio=_finite(storage_raw["stop_used_ratio"], "storage.stop_used_ratio"),
        bot_log_retention_days=_integer(storage_raw["bot_log_retention_days"], "storage.bot_log_retention_days"),
    )
    resolved_trading = TradingConfig(
        lifecycle_mode=_get_lifecycle_mode(trading),
        data_contract=str(trading.get("data_contract", "")),
        cadence_minutes=_integer(trading.get("cadence_minutes"), "cadence_minutes"),
        gamma=gamma,
        orderbook=book,
        experiment=experiment,
        storage=storage,
        strategy_source_digest=compute_strategy_source_digest(),
    )
    validate_yaml_config_shape(raw, resolved_trading)
    provisional = BotConfig(
        simulation_mode=resolved_sim,
        job_name=job_name,
        db_path=PROJECT_ROOT / "data" / job_name / "trades_sim.db",
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
    "CANONICAL_JOBS",
    "DATA_CONTRACT",
    "LIFECYCLE_MODES",
    "assert_no_credentials",
    "load_config",
]
