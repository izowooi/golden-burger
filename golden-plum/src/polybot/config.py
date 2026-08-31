"""Resolved configuration for the Golden Plum midgame-confirmation experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

from dotenv import load_dotenv
from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)
import yaml

from .source_digest import compute_strategy_source_digest, preregistration_sha256


LIFECYCLE_MODES = frozenset({"active", "close_only", "archive_only"})
FROZEN_START_UTC = "2026-08-31T00:00:00Z"
FROZEN_ENTRY_END_UTC = "2026-09-14T00:00:00Z"
FROZEN_FOLLOWUP_END_UTC = "2026-09-21T00:00:00Z"
FROZEN_JOB_TAKE_PROFIT_PRICE = {
    "plum-live-king-90-1m-v1": 0.90,
    "plum-live-queen-95-1m-v1": 0.95,
    "plum-shadow-silver-1m-v1": 0.95,
}
SOCCER_TAG_ID = 100350
MLB_TAG_ID = 100381
NHL_TAG_ID = 899
ESPORTS_TAG_ID = 64
REQUIRED_COMMON_TAG_IDS = (1, 100639, SOCCER_TAG_ID)
CLASSIFIER_VERSION = "plum-soccer-eight-competitions-v1"
SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DirectSportIdentity:
    """Exact Gamma identity for a US two-team direct moneyline family."""

    code: str
    sport_id: int
    name: str
    primary_tag_id: int
    root_series_id: int
    team_league: str

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "primary_tag_id": self.primary_tag_id,
            "root_series_id": self.root_series_id,
            "sport_id": self.sport_id,
            "team_league": self.team_league,
        }


DIRECT_SPORT_IDENTITIES = {
    "mlb": DirectSportIdentity("mlb", 8, "MLB", MLB_TAG_ID, 3, "mlb"),
    "nhl": DirectSportIdentity("nhl", 35, "NHL", NHL_TAG_ID, 10346, "nhl"),
}
SPORT_FAMILY_TAG_IDS = {
    "soccer": SOCCER_TAG_ID,
    "mlb": MLB_TAG_ID,
    "nhl": NHL_TAG_ID,
}
SPORT_FAMILY_MAX_IN_PLAY_HOURS = {
    "soccer": 4.0,
    "mlb": 8.0,
    "nhl": 5.0,
}


@dataclass(frozen=True)
class LeagueIdentity:
    code: str
    sport_id: int
    name: str
    primary_tag_id: int
    series_id: str
    series_slug: str
    team_league: str
    required_tag_ids: tuple[int, ...]

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "primary_tag_id": self.primary_tag_id,
            "required_tag_ids": list(self.required_tag_ids),
            "series_id": self.series_id,
            "series_slug": self.series_slug,
            "sport_id": self.sport_id,
            "team_league": self.team_league,
        }


@dataclass(frozen=True)
class CupIdentity:
    code: str
    name: str
    tag_id: int
    series_id: str
    series_slug: str
    event_slug_prefix: str
    resolution_source_host: str

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "event_slug_prefix": self.event_slug_prefix,
            "name": self.name,
            "resolution_source_host": self.resolution_source_host,
            "series_id": self.series_id,
            "series_slug": self.series_slug,
            "tag_id": self.tag_id,
        }


FROZEN_LEAGUE_IDENTITIES = (
    LeagueIdentity(
        "epl", 2, "Premier League", 306, "10188",
        "premier-league-2025", "epl", (82, 306),
    ),
    LeagueIdentity(
        "bun", 7, "Bundesliga", 1494, "10194",
        "bundesliga-2025", "bun", (1494,),
    ),
    LeagueIdentity(
        "fl1", 11, "Ligue 1", 102070, "10195",
        "ligue-1-2025", "fl1", (102070,),
    ),
    LeagueIdentity(
        "lal", 3, "LaLiga", 780, "10193",
        "la-liga-2025", "lal", (780,),
    ),
    LeagueIdentity(
        "mls", 33, "MLS", 100100, "10189",
        "mls-2025", "mls", (100100,),
    ),
    LeagueIdentity(
        "sea", 12, "Serie A", 100618, "10203",
        "serie-a-2025", "sea", (101962,),
    ),
)

FROZEN_CUP_IDENTITIES = (
    CupIdentity(
        "ucl", "UEFA Champions League", 100977, "10204", "ucl-2025",
        "ucl-", "www.uefa.com",
    ),
    CupIdentity(
        "uel", "UEFA Europa League", 101787, "10209", "uel-2025",
        "uel-", "www.uefa.com",
    ),
)


def league_registry_payload(
    identities: Sequence[LeagueIdentity] = FROZEN_LEAGUE_IDENTITIES,
    cup_identities: Sequence[CupIdentity] = FROZEN_CUP_IDENTITIES,
) -> dict[str, Any]:
    return {
        "related_tags": False,
        "required_common_tag_ids": list(REQUIRED_COMMON_TAG_IDS),
        "soccer_tag_id": SOCCER_TAG_ID,
        "leagues": [identity.canonical_dict() for identity in identities],
        "uefa_competitions": [
            identity.canonical_dict() for identity in cup_identities
        ],
        "direct_sports": {
            code: identity.canonical_dict()
            for code, identity in sorted(DIRECT_SPORT_IDENTITIES.items())
        },
        "sport_family_tag_ids": SPORT_FAMILY_TAG_IDS,
        "sport_family_max_in_play_hours": SPORT_FAMILY_MAX_IN_PLAY_HOURS,
    }


def league_mapping_sha256(
    identities: Sequence[LeagueIdentity] = FROZEN_LEAGUE_IDENTITIES,
    cup_identities: Sequence[CupIdentity] = FROZEN_CUP_IDENTITIES,
) -> str:
    payload = {
        "classifier_version": CLASSIFIER_VERSION,
        **league_registry_payload(identities, cup_identities),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


LEAGUE_MAPPING_SHA256 = league_mapping_sha256()


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
class PlumEntryConfig:
    """Frozen first-cross trend confirmation and exit boundaries."""

    # ``prob_min`` is the first upward-crossing threshold. ``prob_max`` is a
    # three-point overshoot cap so a one-minute gap cannot turn a 0.75 entry
    # into an unregistered late/high-price trade.
    prob_min: float = 0.75
    prob_max: float = 0.78
    min_source_minute: float = 5.0
    max_source_minute: float = 75.0
    trend_observations: int = 3
    trend_min_cumulative_move: float = 0.02
    trend_max_pullback: float = 0.01
    trend_max_gap_seconds: float = 90.0
    min_leader_margin: float = 0.005
    max_entry_spread: float = 0.05
    take_profit_price: float = 0.90
    stop_loss_delta: float = 0.15
    force_exit_minute: float = 80.0
    # Absolute dust floor retained only as a defensive lower bound.  The
    # effective trigger is max(stop_price, confirmed entry - stop_loss_delta).
    stop_price: float = 0.01
    max_entry_drawdown: float = 0.15
    # A stop is a stop-limit contract, not permission to cross an arbitrary
    # post-game/dust book.  The full-depth FOK must remain within five points
    # of the trigger and inside a ten-point displayed spread.
    max_stop_slippage: float = 0.05
    max_stop_spread: float = 0.10
    max_stop_loss_fraction: float = 1.00
    hours_min: float = 0.0
    hours_max: float = 4.0


EntryConfig = PlumEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Small live-universe evidence archive bounds."""

    prob_min: float = 0.0
    hours_max: float = 4.0
    retention_days: int = 60


@dataclass
class TradingConfig:
    """Golden Plum live/shadow trading and evidence configuration."""

    lifecycle_mode: str = "active"
    sport_family: str = "soccer"
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 5000.0
    min_volume_24h: float = 0.0
    min_cumulative_volume: float = 5000.0
    max_positions: int = 10
    max_event_positions: int = 1
    max_new_positions_per_cycle: int = 5
    max_emergency_sells_per_cycle: int = 10
    experiment_capital_usdc: float = 50.0
    max_drawdown_stop: float = 0.20
    reentry_cooldown_hours: float = 720.0
    max_snapshot_gap_minutes: float = 2.0
    fok_reconciliation_timeout_minutes: float = 2.0
    stop_sell_quarantine_timeout_minutes: float = 180.0
    min_order_size: float = 5.0
    min_order_buffer_shares: float = 0.0
    yes_only_mode: bool = False
    experiment_start_utc: str = FROZEN_START_UTC
    experiment_entry_end_utc: str = FROZEN_ENTRY_END_UTC
    experiment_followup_end_utc: str = FROZEN_FOLLOWUP_END_UTC
    strategy_source_digest: str = ""
    preregistration_sha256: str = ""
    classifier_version: str = CLASSIFIER_VERSION
    league_mapping_sha256: str = LEAGUE_MAPPING_SHA256
    entry: PlumEntryConfig = field(default_factory=PlumEntryConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    excluded_categories: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> PlumEntryConfig:
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


def _validate_config(
    trading: TradingConfig,
    api: ApiConfig,
    *,
    job_name: str,
    simulation_mode: bool,
) -> None:
    """Reject cohort or mode drift before any network/database mutation."""
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
        "max_emergency_sells_per_cycle": trading.max_emergency_sells_per_cycle,
        "experiment_capital_usdc": trading.experiment_capital_usdc,
        "max_drawdown_stop": trading.max_drawdown_stop,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "max_snapshot_gap_minutes": trading.max_snapshot_gap_minutes,
        "fok_reconciliation_timeout_minutes": (
            trading.fok_reconciliation_timeout_minutes
        ),
        "stop_sell_quarantine_timeout_minutes": (
            trading.stop_sell_quarantine_timeout_minutes
        ),
        "min_order_size": trading.min_order_size,
        "min_order_buffer_shares": trading.min_order_buffer_shares,
        "entry.prob_min": entry.prob_min,
        "entry.prob_max": entry.prob_max,
        "entry.min_source_minute": entry.min_source_minute,
        "entry.max_source_minute": entry.max_source_minute,
        "entry.trend_observations": entry.trend_observations,
        "entry.trend_min_cumulative_move": entry.trend_min_cumulative_move,
        "entry.trend_max_pullback": entry.trend_max_pullback,
        "entry.trend_max_gap_seconds": entry.trend_max_gap_seconds,
        "entry.min_leader_margin": entry.min_leader_margin,
        "entry.max_entry_spread": entry.max_entry_spread,
        "entry.take_profit_price": entry.take_profit_price,
        "entry.stop_loss_delta": entry.stop_loss_delta,
        "entry.force_exit_minute": entry.force_exit_minute,
        "entry.stop_price": entry.stop_price,
        "entry.max_entry_drawdown": entry.max_entry_drawdown,
        "entry.max_stop_slippage": entry.max_stop_slippage,
        "entry.max_stop_spread": entry.max_stop_spread,
        "entry.max_stop_loss_fraction": entry.max_stop_loss_fraction,
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
    if trading.sport_family != "soccer":
        raise ValueError("Golden Plum is frozen to soccer")
    if trading.buy_amount_usdc != 5:
        raise ValueError("Golden Plum notional must remain exactly $5")
    if (
        trading.min_liquidity != 5000
        or trading.min_cumulative_volume != 5000
        or trading.min_volume_24h != 0
    ):
        raise ValueError(
            "Golden Plum liquidity gate is frozen at $5k cumulative "
            "volume/$5k liquidity plus an exact-$5 executable-book gate"
        )
    if (
        trading.max_positions != 10
        or trading.max_event_positions != 1
        or trading.max_new_positions_per_cycle != 5
    ):
        raise ValueError("Golden Plum exposure limits are frozen at 10/1/5")
    if (
        trading.buy_amount_usdc * trading.max_new_positions_per_cycle
        != 25
    ):
        raise ValueError("per-cycle new BUY notional must remain capped at $25")
    if trading.max_emergency_sells_per_cycle != 10:
        raise ValueError("all ten independent event exits must remain available")
    if trading.experiment_capital_usdc != 50:
        raise ValueError("experiment capital is frozen at $50 requested exposure")
    if trading.max_drawdown_stop != 0.20:
        raise ValueError("economic drawdown entry guard is frozen at 20%")
    if trading.max_event_positions > trading.max_positions:
        raise ValueError("max_event_positions must be <= max_positions")
    if trading.reentry_cooldown_hours != 720:
        raise ValueError("reentry cooldown is frozen at 720 hours")
    if trading.max_snapshot_gap_minutes != 2:
        raise ValueError("snapshot maintenance cadence is frozen at 2 minutes")
    if trading.fok_reconciliation_timeout_minutes != 2:
        raise ValueError("delayed FOK reconciliation timeout is frozen at 2 minutes")
    if trading.stop_sell_quarantine_timeout_minutes != 180:
        raise ValueError(
            "failed stop SELL quarantine timeout is frozen at 180 minutes; "
            "BUY reconciliation uses the same 180-minute timeout"
        )
    if trading.min_order_size != 5 or trading.min_order_buffer_shares != 0:
        raise ValueError("minimum order contract is frozen at 5 shares with no buffer")
    if trading.yes_only_mode:
        raise ValueError("Golden Plum must inspect direct YES and NO books")
    if (entry.prob_min, entry.prob_max) != (0.75, 0.78):
        raise ValueError("entry first-cross VWAP band is frozen at 0.75-0.78")
    expected_take_profit = FROZEN_JOB_TAKE_PROFIT_PRICE.get(job_name)
    if expected_take_profit is None:
        raise ValueError(f"unsupported Golden Plum runtime job: {job_name}")
    if entry.take_profit_price != expected_take_profit:
        raise ValueError(
            f"{job_name} take-profit price must remain {expected_take_profit:.2f}"
        )
    expected_simulation = job_name == "plum-shadow-silver-1m-v1"
    if simulation_mode is not expected_simulation:
        expected_mode = "simulation" if expected_simulation else "live"
        raise ValueError(f"{job_name} is frozen to {expected_mode} mode")
    if (
        entry.min_source_minute != 5
        or entry.max_source_minute != 75
        or entry.trend_observations != 3
        or entry.trend_min_cumulative_move != 0.02
        or entry.trend_max_pullback != 0.01
        or entry.trend_max_gap_seconds != 90
        or entry.min_leader_margin != 0.005
        or entry.max_entry_spread != 0.05
        or entry.stop_loss_delta != 0.15
        or entry.force_exit_minute != 80
    ):
        raise ValueError("midgame trend/first-cross/TP-SL/time-exit contract drift")
    if not (
        entry.prob_min < entry.prob_max < entry.take_profit_price < 1
        and entry.min_source_minute < entry.max_source_minute
        < entry.force_exit_minute <= 90
    ):
        raise ValueError("entry, target, and source-minute ordering is invalid")
    if entry.stop_price != 0.01:
        raise ValueError("defensive absolute stop floor is frozen at 0.01")
    if entry.max_entry_drawdown != entry.stop_loss_delta:
        raise ValueError("stored entry stop must match the 15pp stop-loss delta")
    if (
        entry.max_stop_slippage != 0.05
        or entry.max_stop_spread != 0.10
        or entry.max_stop_loss_fraction != 1.00
    ):
        raise ValueError(
            "stop execution safety is frozen at 5pp slippage, 10pp spread, full live-gap loss"
        )
    expected_hours_max = 4.0
    if entry.hours_min != 0 or entry.hours_max != expected_hours_max:
        raise ValueError(
            f"{trading.sport_family} in-play age window must remain "
            f"[0h, {expected_hours_max:g}h]"
        )
    if archive.prob_min != 0 or archive.hours_max != expected_hours_max:
        raise ValueError(
            f"archive envelope must cover the {expected_hours_max:g}-hour "
            f"{trading.sport_family} in-play universe"
        )
    if archive.retention_days < 60:
        raise ValueError("archive.retention_days must be at least 60")
    smallest_order = trading.buy_amount_usdc / entry.prob_max
    if smallest_order + 1e-9 < trading.min_order_size:
        raise ValueError("$5 cannot satisfy the venue minimum at entry.prob_max")
    if not isinstance(trading.excluded_categories, list) or any(
        not isinstance(item, str) for item in trading.excluded_categories
    ):
        raise ValueError("excluded_categories must be a list")
    if trading.excluded_categories:
        raise ValueError("category overrides are not permitted")
    if api.signature_type not in {1, 3}:
        raise ValueError("signature_type must be one of: 1, 3")
    if (
        trading.experiment_start_utc != FROZEN_START_UTC
        or trading.experiment_entry_end_utc != FROZEN_ENTRY_END_UTC
        or trading.experiment_followup_end_utc != FROZEN_FOLLOWUP_END_UTC
    ):
        raise ValueError("experiment timestamps differ from the frozen deployment")
    if (
        trading.classifier_version != CLASSIFIER_VERSION
        or trading.league_mapping_sha256 != LEAGUE_MAPPING_SHA256
    ):
        raise ValueError("sports classifier identity drift")
    for name, digest in (
        ("strategy_source_digest", trading.strategy_source_digest),
        ("preregistration_sha256", trading.preregistration_sha256),
    ):
        try:
            valid_digest = len(digest) == 64 and int(digest, 16) >= 0
        except (TypeError, ValueError):
            valid_digest = False
        if not valid_digest:
            raise ValueError(f"{name} must be a 64-character SHA-256 digest")


def load_config(
    config_path: str = "config.yaml",
    job_name: str = "default",
    env_path: Optional[str] = None,
    simulation_mode: Optional[bool] = None,
    yes_only_mode: Optional[bool] = None,
) -> BotConfig:
    """Load and validate one immutable King/Queen/Silver cohort."""
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

    frozen_take_profit = FROZEN_JOB_TAKE_PROFIT_PRICE.get(job_name, 0.90)
    entry = PlumEntryConfig(
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", entry_cfg.get("prob_min"), 0.75
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.78
        ),
        min_source_minute=_get_config_value(
            "POLYBOT_MIN_SOURCE_MINUTE",
            entry_cfg.get("min_source_minute"),
            5.0,
        ),
        max_source_minute=_get_config_value(
            "POLYBOT_MAX_SOURCE_MINUTE",
            entry_cfg.get("max_source_minute"),
            75.0,
        ),
        trend_observations=_get_config_value(
            "POLYBOT_TREND_OBSERVATIONS",
            entry_cfg.get("trend_observations"),
            3,
            int,
        ),
        trend_min_cumulative_move=_get_config_value(
            "POLYBOT_TREND_MIN_CUMULATIVE_MOVE",
            entry_cfg.get("trend_min_cumulative_move"),
            0.02,
        ),
        trend_max_pullback=_get_config_value(
            "POLYBOT_TREND_MAX_PULLBACK",
            entry_cfg.get("trend_max_pullback"),
            0.01,
        ),
        trend_max_gap_seconds=_get_config_value(
            "POLYBOT_TREND_MAX_GAP_SECONDS",
            entry_cfg.get("trend_max_gap_seconds"),
            90.0,
        ),
        min_leader_margin=_get_config_value(
            "POLYBOT_MIN_LEADER_MARGIN",
            entry_cfg.get("min_leader_margin"),
            0.005,
        ),
        max_entry_spread=_get_config_value(
            "POLYBOT_MAX_ENTRY_SPREAD",
            entry_cfg.get("max_entry_spread"),
            0.05,
        ),
        take_profit_price=_get_config_value(
            "POLYBOT_TAKE_PROFIT_PRICE",
            entry_cfg.get("take_profit_price"),
            frozen_take_profit,
        ),
        stop_loss_delta=_get_config_value(
            "POLYBOT_STOP_LOSS_DELTA",
            entry_cfg.get("stop_loss_delta"),
            0.15,
        ),
        force_exit_minute=_get_config_value(
            "POLYBOT_FORCE_EXIT_MINUTE",
            entry_cfg.get("force_exit_minute"),
            80.0,
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.01
        ),
        max_entry_drawdown=_get_config_value(
            "POLYBOT_MAX_ENTRY_DRAWDOWN",
            entry_cfg.get("max_entry_drawdown"),
            0.15,
        ),
        max_stop_slippage=_get_config_value(
            "POLYBOT_MAX_STOP_SLIPPAGE",
            entry_cfg.get("max_stop_slippage"),
            0.05,
        ),
        max_stop_spread=_get_config_value(
            "POLYBOT_MAX_STOP_SPREAD",
            entry_cfg.get("max_stop_spread"),
            0.10,
        ),
        max_stop_loss_fraction=_get_config_value(
            "POLYBOT_MAX_STOP_LOSS_FRACTION",
            entry_cfg.get("max_stop_loss_fraction"),
            1.00,
        ),
        hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN", entry_cfg.get("hours_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MAX", entry_cfg.get("hours_max"), 4.0
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ARCHIVE_HOURS_MAX", archive_cfg.get("hours_max"), 4.0
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
        sport_family=str(
            os.getenv(
                "POLYBOT_SPORT_FAMILY",
                trading_cfg.get("sport_family", "soccer"),
            )
        ).strip().lower(),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT", trading_cfg.get("buy_amount_usdc"), 5.0
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY", trading_cfg.get("min_liquidity"), 5000.0
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H", trading_cfg.get("min_volume_24h"), 0.0
        ),
        min_cumulative_volume=_get_config_value(
            "POLYBOT_MIN_CUMULATIVE_VOLUME",
            trading_cfg.get("min_cumulative_volume"),
            5000.0,
        ),
        max_positions=_get_config_value(
            "POLYBOT_MAX_POSITIONS", trading_cfg.get("max_positions"), 10, int
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
            5,
            int,
        ),
        max_emergency_sells_per_cycle=_get_config_value(
            "POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE",
            trading_cfg.get("max_emergency_sells_per_cycle"),
            10,
            int,
        ),
        experiment_capital_usdc=_get_config_value(
            "POLYBOT_EXPERIMENT_CAPITAL_USDC",
            trading_cfg.get("experiment_capital_usdc"),
            50.0,
        ),
        max_drawdown_stop=_get_config_value(
            "POLYBOT_MAX_DRAWDOWN_STOP",
            trading_cfg.get("max_drawdown_stop"),
            0.20,
        ),
        reentry_cooldown_hours=_get_config_value(
            "POLYBOT_REENTRY_COOLDOWN_HOURS",
            trading_cfg.get("reentry_cooldown_hours"),
            720.0,
        ),
        max_snapshot_gap_minutes=_get_config_value(
            "POLYBOT_MAX_SNAPSHOT_GAP_MINUTES",
            trading_cfg.get("max_snapshot_gap_minutes"),
            2.0,
        ),
        fok_reconciliation_timeout_minutes=_get_config_value(
            "POLYBOT_FOK_RECONCILIATION_TIMEOUT_MINUTES",
            trading_cfg.get("fok_reconciliation_timeout_minutes"),
            2.0,
        ),
        stop_sell_quarantine_timeout_minutes=_get_config_value(
            "POLYBOT_STOP_SELL_QUARANTINE_TIMEOUT_MINUTES",
            trading_cfg.get("stop_sell_quarantine_timeout_minutes"),
            180.0,
        ),
        min_order_size=_get_config_value(
            "POLYBOT_MIN_ORDER_SIZE", trading_cfg.get("min_order_size"), 5.0
        ),
        min_order_buffer_shares=_get_config_value(
            "POLYBOT_MIN_ORDER_BUFFER_SHARES",
            trading_cfg.get("min_order_buffer_shares"),
            0.0,
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
        strategy_source_digest=compute_strategy_source_digest(SOURCE_PROJECT_ROOT),
        preregistration_sha256=preregistration_sha256(SOURCE_PROJECT_ROOT),
        entry=entry,
        archive=archive,
        excluded_categories=_get_list_config_value(
            "POLYBOT_EXCLUDED_CATEGORIES",
            trading_cfg.get("excluded_categories"),
            [],
        ),
    )

    validate_yaml_config_shape(cfg, trading)

    if simulation_mode is None:
        simulation_mode = cfg.get("simulation_mode", True)
    if not isinstance(simulation_mode, bool):
        raise ValueError("simulation_mode must be a boolean")

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS")
    signature_type_raw = os.getenv("POLYMARKET_SIGNATURE_TYPE")
    if simulation_mode:
        if any((private_key, funder_address, signature_type_raw)):
            raise ValueError(
                "simulation runtime must not receive wallet credentials"
            )
        private_key = ""
        funder_address = ""
        signature_type_raw = "1"
    else:
        if not private_key:
            raise ValueError(
                "POLYMARKET_PRIVATE_KEY environment variable is required"
            )
        if not funder_address:
            raise ValueError(
                "POLYMARKET_FUNDER_ADDRESS environment variable is required"
            )
        if private_key.startswith("0x"):
            private_key = private_key[2:]
    api = ApiConfig(
        private_key=private_key,
        funder_address=funder_address,
        signature_type=int(signature_type_raw or "1"),
    )
    _validate_config(
        trading,
        api,
        job_name=job_name,
        simulation_mode=simulation_mode,
    )

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
