"""Resolved configuration for Golden Queen's Crown Momentum strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
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
DEFAULT_BUY_AMOUNT_USDC = 100.0


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


@dataclass(frozen=True)
class CrownEntryConfig:
    """Pure Crown Momentum entry/exit thresholds.

    Entry is a one-way threshold crossing: a previously archived YES price must
    be below ``prob_min`` and the current YES midpoint/Gamma price must be in
    ``[prob_min, prob_max]``.  Unresolved positions use immutable absolute
    ``stop_price`` and ``take_profit_price`` exits.  Resolution and redeem
    evidence remain separate from CLOB SELL fills.
    """

    prob_min: float = 0.90
    prob_max: float = 0.94
    stop_price: float = 0.85
    take_profit_price: float = 0.98
    hours_min: float = 0.0
    hours_max: float = 24.0


# A descriptive alias for callers that prefer the section name.
EntryConfig = CrownEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Research-universe archive bounds."""

    prob_min: float = 0.80
    hours_max: float = 72.0
    retention_days: int = 60


@dataclass(frozen=True)
class SportsConfig:
    """Sports clock rules.

    Sports are included by default.  These fields choose the trustworthy clock;
    they do not constitute a category exclusion.
    """

    use_game_start_time: bool = True
    allow_in_play: bool = True
    reject_without_game_start: bool = False
    max_in_play_minutes: float = 360.0


@dataclass
class TradingConfig:
    """Crown Momentum trading and evidence-capture configuration."""

    lifecycle_mode: str = "active"
    buy_amount_usdc: float = DEFAULT_BUY_AMOUNT_USDC
    max_buy_amount_usdc: float = 1_000.0
    min_liquidity: float = 10_000.0
    max_order_liquidity_ratio: float = 0.001
    min_volume_24h: float = 2_000.0
    max_order_volume_ratio: float = 0.02
    max_positions: int = 20
    max_event_positions: int = 1
    max_open_notional_multiple: float = 10.0
    max_new_positions_per_cycle: int = 1
    reentry_cooldown_hours: float = 168.0
    max_snapshot_gap_minutes: float = 15.0
    min_order_size: float = 5.0
    min_order_buffer_shares: float = 0.10
    max_spread: float = 0.02
    depth_price_window: float = 0.01
    depth_safety_multiple: float = 1.20
    yes_only_mode: bool = True
    entry: CrownEntryConfig = field(default_factory=CrownEntryConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    sports: SportsConfig = field(default_factory=SportsConfig)
    # Empty means no category is excluded.  Sports are therefore included
    # unless an operator explicitly sets POLYBOT_EXCLUDED_CATEGORIES.
    excluded_categories: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> CrownEntryConfig:
        """Stable strategy-section alias used by pure-interface callers."""
        return self.entry

    @property
    def effective_min_liquidity(self) -> float:
        """Scale metadata liquidity automatically with order notional."""
        return max(
            self.min_liquidity,
            self.buy_amount_usdc / self.max_order_liquidity_ratio,
        )

    @property
    def effective_min_volume_24h(self) -> float:
        """Scale recent volume automatically with order notional."""
        return max(
            self.min_volume_24h,
            self.buy_amount_usdc / self.max_order_volume_ratio,
        )

    @property
    def max_open_notional_usdc(self) -> float:
        """Capital cap that follows the single common size control."""
        return self.buy_amount_usdc * self.max_open_notional_multiple


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
        "max_buy_amount_usdc": trading.max_buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "max_order_liquidity_ratio": trading.max_order_liquidity_ratio,
        "min_volume_24h": trading.min_volume_24h,
        "max_order_volume_ratio": trading.max_order_volume_ratio,
        "max_positions": trading.max_positions,
        "max_event_positions": trading.max_event_positions,
        "max_open_notional_multiple": trading.max_open_notional_multiple,
        "max_new_positions_per_cycle": trading.max_new_positions_per_cycle,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
        "min_order_size": trading.min_order_size,
        "min_order_buffer_shares": trading.min_order_buffer_shares,
        "max_spread": trading.max_spread,
        "depth_price_window": trading.depth_price_window,
        "depth_safety_multiple": trading.depth_safety_multiple,
        "entry.prob_min": entry.prob_min,
        "entry.prob_max": entry.prob_max,
        "entry.stop_price": entry.stop_price,
        "entry.take_profit_price": entry.take_profit_price,
        "entry.hours_min": entry.hours_min,
        "entry.hours_max": entry.hours_max,
        "archive.prob_min": archive.prob_min,
        "archive.hours_max": archive.hours_max,
        "archive.retention_days": archive.retention_days,
        "sports.max_in_play_minutes": trading.sports.max_in_play_minutes,
    }
    for name, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError(
            "lifecycle_mode must be one of: active, close_only, archive_only"
        )
    if trading.buy_amount_usdc <= 0:
        raise ValueError("buy_amount_usdc must be > 0")
    if trading.max_buy_amount_usdc <= 0:
        raise ValueError("max_buy_amount_usdc must be > 0")
    if trading.buy_amount_usdc > trading.max_buy_amount_usdc:
        raise ValueError(
            "buy_amount_usdc must be <= max_buy_amount_usdc; "
            "scale beyond the hard cap requires a reviewed code change"
        )
    if trading.min_liquidity < 0 or trading.min_volume_24h < 0:
        raise ValueError("min_liquidity and min_volume_24h must be >= 0")
    if not 0 < trading.max_order_liquidity_ratio <= 1:
        raise ValueError("max_order_liquidity_ratio must be in (0, 1]")
    if not 0 < trading.max_order_volume_ratio <= 1:
        raise ValueError("max_order_volume_ratio must be in (0, 1]")
    if trading.max_positions <= 0 or trading.max_event_positions <= 0:
        raise ValueError("position limits must be positive integers")
    if trading.max_event_positions > trading.max_positions:
        raise ValueError("max_event_positions must be <= max_positions")
    if trading.max_open_notional_multiple < 1:
        raise ValueError("max_open_notional_multiple must be >= 1")
    if not 0 < trading.max_new_positions_per_cycle <= trading.max_positions:
        raise ValueError(
            "max_new_positions_per_cycle must be in (0, max_positions]"
        )
    if trading.reentry_cooldown_hours <= 0:
        raise ValueError("reentry_cooldown_hours must be > 0")
    if trading.max_snapshot_gap_minutes <= 0:
        raise ValueError("max_snapshot_gap_minutes must be > 0")
    if trading.min_order_size <= 0 or trading.min_order_buffer_shares < 0:
        raise ValueError("minimum order size/buffer must be non-negative and finite")
    if not 0 < trading.max_spread < 1:
        raise ValueError("max_spread must be in (0, 1)")
    if not 0 < trading.depth_price_window < 1:
        raise ValueError("depth_price_window must be in (0, 1)")
    if trading.depth_safety_multiple < 1:
        raise ValueError("depth_safety_multiple must be >= 1")
    if not trading.yes_only_mode:
        raise ValueError("Crown Momentum inherently requires yes_only_mode=true")
    if not (
        0
        < entry.stop_price
        < entry.prob_min
        <= entry.prob_max
        < entry.take_profit_price
        < 1
    ):
        raise ValueError(
            "entry prices must satisfy 0 < stop_price < prob_min <= prob_max "
            "< take_profit_price < 1"
        )
    if not 0 <= entry.hours_min < entry.hours_max <= 120:
        raise ValueError(
            "entry hours must satisfy 0 <= hours_min < hours_max <= 120"
        )
    if not 0 < archive.prob_min <= 0.85 or archive.prob_min >= entry.prob_min:
        raise ValueError(
            "archive.prob_min must be in (0, 0.85] and below entry.prob_min"
        )
    if archive.hours_max < entry.hours_max:
        raise ValueError("archive.hours_max must cover the entry horizon")
    if archive.retention_days < 60:
        raise ValueError("archive.retention_days must be at least 60")
    if trading.sports.max_in_play_minutes <= 0:
        raise ValueError("sports.max_in_play_minutes must be > 0")
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


def load_config(
    config_path: str = "config.yaml",
    job_name: str = "default",
    env_path: Optional[str] = None,
    simulation_mode: Optional[bool] = None,
    yes_only_mode: Optional[bool] = None,
) -> BotConfig:
    """Load and validate resolved configuration.

    ``yes_only_mode`` remains in the signature for callers shared with sibling
    strategies.  Passing ``False`` is rejected; Queen never permits NO-side
    trading.
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
    sports_cfg = trading_cfg.get("sports", {})
    if (
        not isinstance(entry_cfg, dict)
        or not isinstance(archive_cfg, dict)
        or not isinstance(sports_cfg, dict)
    ):
        raise ValueError(
            "trading.entry, trading.archive, and trading.sports must be mappings"
        )

    entry = CrownEntryConfig(
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", entry_cfg.get("prob_min"), 0.90
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.94
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.85
        ),
        take_profit_price=_get_config_value(
            "POLYBOT_TAKE_PROFIT_PRICE",
            entry_cfg.get("take_profit_price"),
            0.98,
        ),
        hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN", entry_cfg.get("hours_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MAX", entry_cfg.get("hours_max"), 24.0
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.80
        ),
        hours_max=_get_config_value(
            "POLYBOT_ARCHIVE_HOURS_MAX", archive_cfg.get("hours_max"), 72.0
        ),
        retention_days=_get_config_value(
            "POLYBOT_SNAPSHOT_RETENTION_DAYS",
            archive_cfg.get("retention_days"),
            60,
            int,
        ),
    )
    sports = SportsConfig(
        use_game_start_time=_get_bool_config_value(
            "POLYBOT_GAME_START_FILTER_ENABLED",
            sports_cfg.get("use_game_start_time"),
            True,
        ),
        allow_in_play=_get_bool_config_value(
            "POLYBOT_ALLOW_IN_PLAY",
            sports_cfg.get("allow_in_play"),
            True,
        ),
        reject_without_game_start=_get_bool_config_value(
            "POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START",
            sports_cfg.get("reject_without_game_start"),
            False,
        ),
        max_in_play_minutes=_get_config_value(
            "POLYBOT_MAX_IN_PLAY_MINUTES",
            sports_cfg.get("max_in_play_minutes"),
            360.0,
        ),
    )

    resolved_yes_only = _get_bool_config_value(
        "POLYBOT_YES_ONLY", trading_cfg.get("yes_only_mode"), True
    )
    if yes_only_mode is not None:
        if not isinstance(yes_only_mode, bool):
            raise ValueError("yes_only_mode override must be a boolean")
        resolved_yes_only = yes_only_mode

    trading = TradingConfig(
        lifecycle_mode=_get_lifecycle_mode(trading_cfg.get("lifecycle_mode")),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT",
            trading_cfg.get("buy_amount_usdc"),
            DEFAULT_BUY_AMOUNT_USDC,
        ),
        max_buy_amount_usdc=_get_config_value(
            "POLYBOT_MAX_BUY_AMOUNT_USDC",
            trading_cfg.get("max_buy_amount_usdc"),
            1_000.0,
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY", trading_cfg.get("min_liquidity"), 10_000.0
        ),
        max_order_liquidity_ratio=_get_config_value(
            "POLYBOT_MAX_ORDER_LIQUIDITY_RATIO",
            trading_cfg.get("max_order_liquidity_ratio"),
            0.001,
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H", trading_cfg.get("min_volume_24h"), 2_000.0
        ),
        max_order_volume_ratio=_get_config_value(
            "POLYBOT_MAX_ORDER_VOLUME_RATIO",
            trading_cfg.get("max_order_volume_ratio"),
            0.02,
        ),
        max_positions=_get_config_value(
            "POLYBOT_MAX_POSITIONS", trading_cfg.get("max_positions"), 20, int
        ),
        max_event_positions=_get_config_value(
            "POLYBOT_MAX_EVENT_POSITIONS",
            trading_cfg.get("max_event_positions"),
            1,
            int,
        ),
        max_open_notional_multiple=_get_config_value(
            "POLYBOT_MAX_OPEN_NOTIONAL_MULTIPLE",
            trading_cfg.get("max_open_notional_multiple"),
            10.0,
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
            168.0,
        ),
        max_snapshot_gap_minutes=_get_config_value(
            "POLYBOT_MAX_SNAPSHOT_GAP_MINUTES",
            trading_cfg.get("max_snapshot_gap_minutes"),
            15.0,
        ),
        min_order_size=_get_config_value(
            "POLYBOT_MIN_ORDER_SIZE", trading_cfg.get("min_order_size"), 5.0
        ),
        min_order_buffer_shares=_get_config_value(
            "POLYBOT_MIN_ORDER_BUFFER_SHARES",
            trading_cfg.get("min_order_buffer_shares"),
            0.10,
        ),
        max_spread=_get_config_value(
            "POLYBOT_MAX_SPREAD",
            trading_cfg.get("max_spread"),
            0.02,
        ),
        depth_price_window=_get_config_value(
            "POLYBOT_DEPTH_PRICE_WINDOW",
            trading_cfg.get("depth_price_window"),
            0.01,
        ),
        depth_safety_multiple=_get_config_value(
            "POLYBOT_DEPTH_SAFETY_MULTIPLE",
            trading_cfg.get("depth_safety_multiple"),
            1.20,
        ),
        yes_only_mode=resolved_yes_only,
        entry=entry,
        archive=archive,
        sports=sports,
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
