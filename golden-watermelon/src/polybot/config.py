"""Frozen configuration and safety boundary for Golden Watermelon."""

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


DATA_CONTRACT = "sports-inplay-match-winner-v1"
CANONICAL_JOB = "watermelon-white-1m"
LIFECYCLE_MODES = frozenset({"archive_only"})

# The first successful Jenkins run remains the actual source cutoff. This
# boundary prevents a delayed deploy from silently creating a different cohort.
FROZEN_START = datetime(2026, 8, 22, 16, 15, tzinfo=timezone.utc)
FROZEN_ENTRY_END = datetime(2026, 9, 5, 16, 15, tzinfo=timezone.utc)
FROZEN_FOLLOWUP_END = datetime(2026, 9, 19, 16, 15, tzinfo=timezone.utc)
ENTRY_THRESHOLDS = (0.95, 0.96, 0.97, 0.98, 0.99)
STOP_LEVELS = (0.95, 0.93, 0.90, 0.85, 0.80, 0.70)


@dataclass(frozen=True)
class JobProfile:
    cadence_arm: str
    cadence_minutes: int


JOB_PROFILES: dict[str, JobProfile] = {
    "watermelon-white-1m": JobProfile("FAST_1M", 1),
    "watermelon-grey-5m": JobProfile("CONTROL_5M", 5),
}

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


def assert_no_credentials(env: Mapping[str, str] | None = None) -> None:
    values = os.environ if env is None else env
    present = sorted(key for key in _CREDENTIAL_ENV_KEYS if key in values)
    if present:
        raise ValueError(
            "Golden Watermelon refuses credential-bearing environments: "
            + ", ".join(present)
        )


def _utc(value: Any, name: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{name} must be ISO-8601") from error
    if result.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return result.astimezone(timezone.utc)


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
    parsed = urlsplit(text)
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


def _mapping(parent: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = parent.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"trading.{name} must be a mapping")
    return value


def _float_tuple(value: Any, name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    result = tuple(_finite(item, name) for item in value)
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    result = tuple(str(item).strip() for item in value)
    if not result or any(not item for item in result) or len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique non-empty values")
    return result


@dataclass(frozen=True)
class GammaConfig:
    base_url: str
    page_size: int
    max_pages: int
    tag_slug: str
    live_only: bool
    sports_market_types: tuple[str, ...]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    retry_max_seconds: float


@dataclass(frozen=True)
class OrderBookConfig:
    base_url: str
    batch_token_limit: int


@dataclass(frozen=True)
class ExperimentConfig:
    start_utc: datetime
    entry_end_utc: datetime
    followup_end_utc: datetime
    entry_thresholds: tuple[float, ...]
    stop_levels: tuple[float, ...]
    simulated_notional_usdc: float
    fee_rate_fallback: float
    preregistration_sha256: str


@dataclass(frozen=True)
class StorageConfig:
    busy_timeout_ms: int
    min_free_gib: float
    stop_used_ratio: float
    bot_log_retention_days: int


@dataclass(frozen=True)
class TradingConfig:
    lifecycle_mode: str
    data_contract: str
    cadence_arm: str
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
        result = asdict(self)
        result["db_path"] = str(self.db_path)
        experiment = result["trading"]["experiment"]
        for key in ("start_utc", "entry_end_utc", "followup_end_utc"):
            experiment[key] = getattr(self.trading.experiment, key).isoformat()
        return result


def _validate_config(config: BotConfig) -> None:
    if not config.simulation_mode:
        raise ValueError("Golden Watermelon can never run live")
    profile = JOB_PROFILES.get(config.job_name)
    if profile is None:
        raise ValueError("job must be one of " + ", ".join(sorted(JOB_PROFILES)))
    trading = config.trading
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError("lifecycle_mode must be archive_only")
    if trading.data_contract != DATA_CONTRACT:
        raise ValueError(f"data_contract must be {DATA_CONTRACT}")
    if (trading.cadence_arm, trading.cadence_minutes) != (
        profile.cadence_arm,
        profile.cadence_minutes,
    ):
        raise ValueError("cadence treatment must match the frozen job profile")
    gamma = trading.gamma
    if gamma.base_url != "https://gamma-api.polymarket.com":
        raise ValueError("Gamma origin is frozen")
    if gamma.page_size != 500 or not 1 <= gamma.max_pages <= 4:
        raise ValueError("Gamma event keyset envelope must remain 500 × at most 4 pages")
    if gamma.tag_slug != "sports" or gamma.live_only is not True:
        raise ValueError("Gamma discovery must remain live sports only")
    if gamma.sports_market_types != ("moneyline",):
        raise ValueError("only top-level moneyline is permitted")
    if not 0 <= gamma.max_retries <= 10:
        raise ValueError("Gamma retry budget is invalid")
    if trading.orderbook.base_url != "https://clob.polymarket.com":
        raise ValueError("CLOB origin is frozen")
    if not 1 <= trading.orderbook.batch_token_limit <= 500:
        raise ValueError("orderbook batch size is invalid")
    experiment = trading.experiment
    if (
        experiment.start_utc != FROZEN_START
        or experiment.entry_end_utc != FROZEN_ENTRY_END
        or experiment.followup_end_utc != FROZEN_FOLLOWUP_END
    ):
        raise ValueError("experiment dates differ from frozen preregistration")
    if experiment.entry_thresholds != ENTRY_THRESHOLDS:
        raise ValueError("entry threshold grid must remain 0.95 through 0.99")
    if experiment.stop_levels != STOP_LEVELS:
        raise ValueError("stop grid differs from the frozen preregistration")
    if any(not 0 < value < 1 for value in (*ENTRY_THRESHOLDS, *STOP_LEVELS)):
        raise ValueError("probability grid must remain within (0,1)")
    if experiment.simulated_notional_usdc != 5:
        raise ValueError("simulated notional must remain $5")
    if experiment.fee_rate_fallback != 0.05:
        raise ValueError("sports taker fee fallback must remain 0.05")
    storage = trading.storage
    if storage.min_free_gib < 50 or not 0 < storage.stop_used_ratio <= 0.90:
        raise ValueError("storage safety floor cannot be loosened")


def load_config(
    path: str | Path = "config.yaml",
    job_name: str = CANONICAL_JOB,
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
    profile = JOB_PROFILES.get(job_name)
    if profile is None:
        raise ValueError("job must be one of " + ", ".join(sorted(JOB_PROFILES)))

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("config root must be a mapping")
    trading_raw = get_trading_config_mapping(raw)
    yaml_sim = _boolean(raw.get("simulation_mode"), "simulation_mode")
    env_sim = os.environ.get("POLYBOT_SIMULATION_MODE")
    resolved_sim = _boolean(env_sim, "POLYBOT_SIMULATION_MODE") if env_sim else yaml_sim
    if simulation_mode is not None and simulation_mode != resolved_sim:
        raise ValueError("CLI mode contradicts resolved simulation_mode")

    gamma_raw = _mapping(trading_raw, "gamma")
    orderbook_raw = _mapping(trading_raw, "orderbook")
    experiment_raw = _mapping(trading_raw, "experiment")
    storage_raw = _mapping(trading_raw, "storage")
    gamma = GammaConfig(
        base_url=_public_origin(gamma_raw["base_url"], "gamma.base_url"),
        page_size=_integer(gamma_raw["page_size"], "gamma.page_size"),
        max_pages=_integer(gamma_raw["max_pages"], "gamma.max_pages"),
        tag_slug=str(gamma_raw["tag_slug"]).strip(),
        live_only=_boolean(gamma_raw["live_only"], "gamma.live_only"),
        sports_market_types=_string_tuple(
            gamma_raw["sports_market_types"], "gamma.sports_market_types"
        ),
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
    )
    orderbook = OrderBookConfig(
        base_url=_public_origin(orderbook_raw["base_url"], "orderbook.base_url"),
        batch_token_limit=_integer(
            orderbook_raw["batch_token_limit"], "orderbook.batch_token_limit"
        ),
    )
    experiment = ExperimentConfig(
        start_utc=_utc(experiment_raw["start_utc"], "experiment.start_utc"),
        entry_end_utc=_utc(experiment_raw["entry_end_utc"], "experiment.entry_end_utc"),
        followup_end_utc=_utc(
            experiment_raw["followup_end_utc"], "experiment.followup_end_utc"
        ),
        entry_thresholds=_float_tuple(
            experiment_raw["entry_thresholds"], "experiment.entry_thresholds"
        ),
        stop_levels=_float_tuple(
            experiment_raw["stop_levels"], "experiment.stop_levels"
        ),
        simulated_notional_usdc=_finite(
            experiment_raw["simulated_notional_usdc"],
            "experiment.simulated_notional_usdc",
        ),
        fee_rate_fallback=_finite(
            experiment_raw["fee_rate_fallback"], "experiment.fee_rate_fallback"
        ),
        preregistration_sha256=preregistration_sha256(),
    )
    storage = StorageConfig(
        busy_timeout_ms=_integer(
            storage_raw["busy_timeout_ms"], "storage.busy_timeout_ms"
        ),
        min_free_gib=_finite(storage_raw["min_free_gib"], "storage.min_free_gib"),
        stop_used_ratio=_finite(
            storage_raw["stop_used_ratio"], "storage.stop_used_ratio"
        ),
        bot_log_retention_days=_integer(
            storage_raw["bot_log_retention_days"], "storage.bot_log_retention_days"
        ),
    )
    trading = TradingConfig(
        lifecycle_mode=str(
            os.environ.get(
                "POLYBOT_LIFECYCLE_MODE", trading_raw.get("lifecycle_mode", "")
            )
        ).strip(),
        data_contract=str(trading_raw.get("data_contract", "")).strip(),
        cadence_arm=profile.cadence_arm,
        cadence_minutes=profile.cadence_minutes,
        gamma=gamma,
        orderbook=orderbook,
        experiment=experiment,
        storage=storage,
        strategy_source_digest=compute_strategy_source_digest(),
    )
    validate_yaml_config_shape(raw, trading)
    provisional = BotConfig(
        simulation_mode=resolved_sim,
        job_name=job_name,
        db_path=PROJECT_ROOT / "data" / job_name / "trades_sim.db",
        trading=trading,
        config_hash="",
    )
    payload = provisional.redacted_dict()
    payload.pop("config_hash", None)
    payload.pop("db_path", None)
    config_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = BotConfig(**{**provisional.__dict__, "config_hash": config_hash})
    _validate_config(result)
    return result
