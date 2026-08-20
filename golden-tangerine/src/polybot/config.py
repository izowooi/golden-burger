"""Resolved configuration for Golden Tangerine's Sports Resolution Hold Live strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import os
from pathlib import Path
from typing import List, Optional, Union

from dotenv import load_dotenv
from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)
import yaml


LIFECYCLE_MODES = frozenset({"active", "close_only", "archive_only"})
FROZEN_START_UTC = "2026-08-21T00:00:00Z"
FROZEN_ENTRY_END_UTC = "2026-09-20T00:00:00Z"
FROZEN_FOLLOWUP_END_UTC = "2026-10-20T00:00:00Z"
FROZEN_ARMS = frozenset({(0.92, 0.93), (0.94, 0.95)})


def _get_config_value(
    env_key: str,
    yaml_value,
    default,
    value_type: type = float,
) -> Union[float, int]:
    """Resolve a numeric setting using env > YAML > default precedence."""
    env_value = os.getenv(env_key)
    if env_value is not None:
        return value_type(env_value)
    if yaml_value is None:
        return default
    if isinstance(yaml_value, bool) or not isinstance(yaml_value, (int, float)):
        raise ValueError(f"{env_key} YAML value must be numeric")
    if value_type is int and not isinstance(yaml_value, int):
        raise ValueError(f"{env_key} YAML value must be an integer")
    return value_type(yaml_value)


def _get_bool_config_value(env_key: str, yaml_value, default: bool) -> bool:
    """Resolve a boolean setting using env > YAML > default precedence."""
    env_value = os.getenv(env_key)
    value = env_value if env_value is not None else yaml_value
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{env_key} must be a boolean")


def _get_list_config_value(
    env_key: str,
    yaml_value,
    default: List[str],
) -> List[str]:
    env_value = os.getenv(env_key)
    if env_value is not None:
        return [item.strip() for item in env_value.split(",") if item.strip()]
    if yaml_value is None:
        return list(default)
    if not isinstance(yaml_value, list) or any(
        not isinstance(item, str) for item in yaml_value
    ):
        raise ValueError(f"{env_key} YAML value must be a list of strings")
    return [item.strip() for item in yaml_value if item.strip()]


def _get_lifecycle_mode(yaml_value) -> str:
    env_value = os.getenv("POLYBOT_LIFECYCLE_MODE")
    value = env_value if env_value is not None else yaml_value
    if value is None:
        return "active"
    if not isinstance(value, str):
        raise ValueError(
            "POLYBOT_LIFECYCLE_MODE must be one of: active, close_only, archive_only"
        )
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in LIFECYCLE_MODES:
        raise ValueError(
            "POLYBOT_LIFECYCLE_MODE must be one of: active, close_only, archive_only"
        )
    return normalized


def _get_datetime_config_value(
    env_key: str,
    yaml_value,
    default: str,
) -> str:
    raw = os.getenv(env_key)
    value = raw if raw is not None else yaml_value
    if value is None:
        return default
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{env_key} must be ISO-8601") from error
    else:
        raise ValueError(f"{env_key} must be ISO-8601")
    if parsed.tzinfo is None:
        raise ValueError(f"{env_key} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class TangerineEntryConfig:
    """One frozen exact-book arm.

    ``stop_price=0`` explicitly disables pre-resolution selling.  Golden
    Black's 0.80/0.70/0.60 stops remain collector-only counterfactuals until a
    later cohort selects one.
    """

    prob_min: float = 0.94
    prob_max: float = 0.95
    stop_price: float = 0.0
    hours_min: float = 0.0
    hours_max: float = 6.0


# A descriptive alias for callers that prefer the section name.
EntryConfig = TangerineEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Research-universe archive bounds."""

    prob_min: float = 0.0
    hours_max: float = 6.0
    retention_days: int = 60


@dataclass
class TradingConfig:
    """Sports Resolution Hold Live trading and evidence-capture configuration."""

    lifecycle_mode: str = "active"
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 10_000.0
    min_volume_24h: float = 0.0
    min_cumulative_volume: float = 5_000.0
    max_positions: int = 3
    max_event_positions: int = 1
    max_new_positions_per_cycle: int = 1
    reentry_cooldown_hours: float = 720.0
    max_snapshot_gap_minutes: float = 30.0
    min_order_size: float = 5.0
    min_order_buffer_shares: float = 0.10
    yes_only_mode: bool = False
    experiment_start_utc: str = FROZEN_START_UTC
    experiment_entry_end_utc: str = FROZEN_ENTRY_END_UTC
    experiment_followup_end_utc: str = FROZEN_FOLLOWUP_END_UTC
    entry: TangerineEntryConfig = field(default_factory=TangerineEntryConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    excluded_categories: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> TangerineEntryConfig:
        """Stable strategy-section alias used by pure-interface callers."""
        return self.entry


@dataclass
class ApiConfig:
    private_key: str
    funder_address: str
    signature_type: int = 1
    chain_id: int = 137


@dataclass
class BotConfig:
    trading: TradingConfig
    api: ApiConfig
    db_path: Path
    simulation_mode: bool = True
    job_name: str = "default"


def _validate_config(trading: TradingConfig, api: ApiConfig) -> None:
    """Reject unsafe, ambiguous, or internally inconsistent settings."""
    entry = trading.entry
    archive = trading.archive
    numeric = {
        "buy_amount_usdc": trading.buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "min_volume_24h": trading.min_volume_24h,
        "min_cumulative_volume": trading.min_cumulative_volume,
        "max_positions": trading.max_positions,
        "max_event_positions": trading.max_event_positions,
        "max_new_positions_per_cycle": trading.max_new_positions_per_cycle,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
        "min_order_size": trading.min_order_size,
        "min_order_buffer_shares": trading.min_order_buffer_shares,
        "entry.prob_min": entry.prob_min,
        "entry.prob_max": entry.prob_max,
        "entry.stop_price": entry.stop_price,
        "entry.hours_min": entry.hours_min,
        "entry.hours_max": entry.hours_max,
        "archive.prob_min": archive.prob_min,
        "archive.hours_max": archive.hours_max,
        "archive.retention_days": archive.retention_days,
    }
    for name, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError(
            "lifecycle_mode must be one of: active, close_only, archive_only"
        )
    if trading.buy_amount_usdc != 5:
        raise ValueError("Golden Tangerine live notional must remain exactly $5")
    if (
        trading.min_liquidity != 10_000
        or trading.min_cumulative_volume != 5_000
        or trading.min_volume_24h != 0
    ):
        raise ValueError("Golden Tangerine universe gates are frozen")
    if (
        trading.max_positions != 3
        or trading.max_event_positions != 1
        or trading.max_new_positions_per_cycle != 1
    ):
        raise ValueError("Golden Tangerine exposure limits are frozen at 3/1/1")
    if trading.max_positions <= 0 or trading.max_event_positions <= 0:
        raise ValueError("position limits must be positive integers")
    if trading.max_event_positions > trading.max_positions:
        raise ValueError("max_event_positions must be <= max_positions")
    if trading.reentry_cooldown_hours <= 0:
        raise ValueError("reentry_cooldown_hours must be > 0")
    if trading.max_snapshot_gap_minutes <= 0:
        raise ValueError("max_snapshot_gap_minutes must be > 0")
    if trading.min_order_size <= 0 or trading.min_order_buffer_shares < 0:
        raise ValueError("minimum order size/buffer must be non-negative and finite")
    if trading.yes_only_mode:
        raise ValueError("Golden Tangerine must evaluate both binary outcomes")
    if (entry.prob_min, entry.prob_max) not in FROZEN_ARMS:
        raise ValueError("entry band must be exactly 0.92-0.93 or 0.94-0.95")
    if not 0 < entry.prob_min <= entry.prob_max < 1 or entry.stop_price != 0:
        raise ValueError(
            "entry prices must be a frozen arm and stop_price must be disabled at 0"
        )
    if entry.hours_min != 0 or entry.hours_max != 6:
        raise ValueError("entry window must remain (0h, 6h]")
    if archive.prob_min != 0 or archive.hours_max != 6:
        raise ValueError("archive envelope must cover all outcomes in the six-hour universe")
    if archive.retention_days < 60:
        raise ValueError("archive.retention_days must be at least 60")
    # Validate the worst (highest-price) order, not just today's candidate.
    smallest_default_order = trading.buy_amount_usdc / entry.prob_max
    required_shares = trading.min_order_size + trading.min_order_buffer_shares
    if smallest_default_order + 1e-9 < required_shares:
        raise ValueError(
            "buy_amount_usdc is too small for min_order_size plus the configured "
            "buffer at entry.prob_max"
        )
    if not isinstance(trading.excluded_categories, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in trading.excluded_categories
    ):
        raise ValueError("excluded_categories must be a list of non-empty strings")
    if api.signature_type not in {1, 3}:
        raise ValueError("signature_type must be one of: 1, 3")
    if (
        trading.experiment_start_utc != FROZEN_START_UTC
        or trading.experiment_entry_end_utc != FROZEN_ENTRY_END_UTC
        or trading.experiment_followup_end_utc != FROZEN_FOLLOWUP_END_UTC
    ):
        raise ValueError("experiment timestamps differ from the frozen deployment")


def load_config(
    config_path: str = "config.yaml",
    job_name: str = "default",
    env_path: Optional[str] = None,
    simulation_mode: Optional[bool] = None,
    yes_only_mode: Optional[bool] = None,
) -> BotConfig:
    """Load and validate resolved configuration.

    ``yes_only_mode`` remains in the signature for compatibility, but this
    strategy requires ``False`` because either aligned sports outcome can be
    the high-probability token.
    """
    load_dotenv(env_path) if env_path else load_dotenv()

    path = Path(config_path)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    else:
        cfg = {}
    trading_cfg = get_trading_config_mapping(cfg)
    entry_cfg = trading_cfg.get("entry", {})
    archive_cfg = trading_cfg.get("archive", {})
    if not isinstance(entry_cfg, dict) or not isinstance(archive_cfg, dict):
        raise ValueError("trading.entry and trading.archive must be mappings")

    entry = TangerineEntryConfig(
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", entry_cfg.get("prob_min"), 0.94
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.95
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.0
        ),
        hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN", entry_cfg.get("hours_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MAX", entry_cfg.get("hours_max"), 6.0
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ARCHIVE_HOURS_MAX", archive_cfg.get("hours_max"), 6.0
        ),
        retention_days=_get_config_value(
            "POLYBOT_SNAPSHOT_RETENTION_DAYS",
            archive_cfg.get("retention_days"),
            60,
            int,
        ),
    )

    resolved_yes_only = _get_bool_config_value(
        "POLYBOT_YES_ONLY", trading_cfg.get("yes_only_mode"), False
    )
    if yes_only_mode is not None:
        if not isinstance(yes_only_mode, bool):
            raise ValueError("yes_only_mode override must be a boolean")
        resolved_yes_only = yes_only_mode

    trading = TradingConfig(
        lifecycle_mode=_get_lifecycle_mode(trading_cfg.get("lifecycle_mode")),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT", trading_cfg.get("buy_amount_usdc"), 5.0
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY", trading_cfg.get("min_liquidity"), 10_000.0
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H", trading_cfg.get("min_volume_24h"), 0.0
        ),
        min_cumulative_volume=_get_config_value(
            "POLYBOT_MIN_CUMULATIVE_VOLUME",
            trading_cfg.get("min_cumulative_volume"),
            5_000.0,
        ),
        max_positions=_get_config_value(
            "POLYBOT_MAX_POSITIONS", trading_cfg.get("max_positions"), 3, int
        ),
        max_event_positions=_get_config_value(
            "POLYBOT_MAX_EVENT_POSITIONS",
            trading_cfg.get("max_event_positions"),
            1,
            int,
        ),
        max_new_positions_per_cycle=_get_config_value(
            "POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE",
            trading_cfg.get("max_new_positions_per_cycle"),
            1,
            int,
        ),
        reentry_cooldown_hours=_get_config_value(
            "POLYBOT_REENTRY_COOLDOWN_HOURS",
            trading_cfg.get("reentry_cooldown_hours"),
            720.0,
        ),
        max_snapshot_gap_minutes=_get_config_value(
            "POLYBOT_MAX_SNAPSHOT_GAP_MINUTES",
            trading_cfg.get("max_snapshot_gap_minutes"),
            30.0,
        ),
        min_order_size=_get_config_value(
            "POLYBOT_MIN_ORDER_SIZE", trading_cfg.get("min_order_size"), 5.0
        ),
        min_order_buffer_shares=_get_config_value(
            "POLYBOT_MIN_ORDER_BUFFER_SHARES",
            trading_cfg.get("min_order_buffer_shares"),
            0.10,
        ),
        yes_only_mode=resolved_yes_only,
        experiment_start_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_START_UTC",
            trading_cfg.get("experiment_start_utc"),
            FROZEN_START_UTC,
        ),
        experiment_entry_end_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_END_UTC",
            trading_cfg.get("experiment_entry_end_utc"),
            FROZEN_ENTRY_END_UTC,
        ),
        experiment_followup_end_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC",
            trading_cfg.get("experiment_followup_end_utc"),
            FROZEN_FOLLOWUP_END_UTC,
        ),
        entry=entry,
        archive=archive,
        excluded_categories=_get_list_config_value(
            "POLYBOT_EXCLUDED_CATEGORIES",
            trading_cfg.get("excluded_categories"),
            [],
        ),
    )

    validate_yaml_config_shape(cfg, trading)

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS")
    if not private_key:
        raise ValueError("POLYMARKET_PRIVATE_KEY environment variable is required")
    if not funder_address:
        raise ValueError("POLYMARKET_FUNDER_ADDRESS environment variable is required")
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    api = ApiConfig(
        private_key=private_key,
        funder_address=funder_address,
        signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
    )
    _validate_config(trading, api)

    if simulation_mode is None:
        simulation_mode = cfg.get("simulation_mode", True)
    if not isinstance(simulation_mode, bool):
        raise ValueError("simulation_mode must be a boolean")

    db_dir = Path("data") / job_name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / ("trades_sim.db" if simulation_mode else "trades.db")
    return BotConfig(
        trading=trading,
        api=api,
        db_path=db_path,
        simulation_mode=simulation_mode,
        job_name=job_name,
    )
