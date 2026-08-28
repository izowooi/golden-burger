"""Resolved configuration for the Golden Watermelon live soccer strategy."""

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
FROZEN_START_UTC = "2026-08-26T18:30:00Z"
FROZEN_ENTRY_END_UTC = "2026-09-02T18:30:00Z"
FROZEN_FOLLOWUP_END_UTC = "2026-09-09T18:30:00Z"
FROZEN_ARMS = frozenset({(0.96, 0.999), (0.99, 0.999)})
SOCCER_TAG_ID = 100350
ESPORTS_TAG_ID = 64
REQUIRED_COMMON_TAG_IDS = (1, 100639, SOCCER_TAG_ID)
CLASSIFIER_VERSION = "soccer-elite-competition-identity-v3"
SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
class WatermelonLiveEntryConfig:
    """One frozen exact-$5 in-play arm and its emergency stop."""

    prob_min: float = 0.96
    prob_max: float = 0.999
    stop_price: float = 0.70
    # A stop is a stop-limit contract, not permission to cross an arbitrary
    # post-game/dust book.  The full-depth FOK must remain within five points
    # of the trigger and inside a ten-point displayed spread.
    max_stop_slippage: float = 0.05
    max_stop_spread: float = 0.10
    max_stop_loss_fraction: float = 0.35
    hours_min: float = 0.0
    hours_max: float = 4.0


EntryConfig = WatermelonLiveEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Small live-universe evidence archive bounds."""

    prob_min: float = 0.0
    hours_max: float = 4.0
    retention_days: int = 60


@dataclass
class TradingConfig:
    """Golden Watermelon live trading and evidence configuration."""

    lifecycle_mode: str = "active"
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 0.0
    min_volume_24h: float = 0.0
    min_cumulative_volume: float = 0.0
    max_positions: int = 20
    max_event_positions: int = 1
    max_new_positions_per_cycle: int = 20
    max_emergency_sells_per_cycle: int = 1
    experiment_capital_usdc: float = 100.0
    max_drawdown_stop: float = 0.10
    reentry_cooldown_hours: float = 720.0
    max_snapshot_gap_minutes: float = 15.0
    min_order_size: float = 5.0
    min_order_buffer_shares: float = 0.0
    yes_only_mode: bool = True
    experiment_start_utc: str = FROZEN_START_UTC
    experiment_entry_end_utc: str = FROZEN_ENTRY_END_UTC
    experiment_followup_end_utc: str = FROZEN_FOLLOWUP_END_UTC
    strategy_source_digest: str = ""
    preregistration_sha256: str = ""
    classifier_version: str = CLASSIFIER_VERSION
    league_mapping_sha256: str = LEAGUE_MAPPING_SHA256
    entry: WatermelonLiveEntryConfig = field(default_factory=WatermelonLiveEntryConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    excluded_categories: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> WatermelonLiveEntryConfig:
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
    """Reject parameter drift before any network or database mutation."""
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
        "min_order_size": trading.min_order_size,
        "min_order_buffer_shares": trading.min_order_buffer_shares,
        "entry.prob_min": entry.prob_min,
        "entry.prob_max": entry.prob_max,
        "entry.stop_price": entry.stop_price,
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
    if trading.buy_amount_usdc != 5:
        raise ValueError("Golden Watermelon live notional must remain exactly $5")
    if (
        trading.min_liquidity != 0
        or trading.min_cumulative_volume != 0
        or trading.min_volume_24h != 0
    ):
        raise ValueError(
            "Golden Watermelon uses the exact $5 executable book as its frozen liquidity gate"
        )
    if (
        trading.max_positions != 20
        or trading.max_event_positions != 1
        or trading.max_new_positions_per_cycle != 20
    ):
        raise ValueError("Golden Watermelon exposure limits are frozen at 20/1/20")
    if trading.max_emergency_sells_per_cycle != 1:
        raise ValueError("only one emergency SELL may be submitted per cycle")
    if trading.experiment_capital_usdc != 100:
        raise ValueError("experiment capital is frozen at $100 requested exposure")
    if trading.max_drawdown_stop != 0.10:
        raise ValueError("economic drawdown entry guard is frozen at 10%")
    if trading.max_event_positions > trading.max_positions:
        raise ValueError("max_event_positions must be <= max_positions")
    if trading.reentry_cooldown_hours != 720:
        raise ValueError("reentry cooldown is frozen at 720 hours")
    if trading.max_snapshot_gap_minutes != 15:
        raise ValueError("pending-order reconciliation TTL is frozen at 15 minutes")
    if trading.min_order_size != 5 or trading.min_order_buffer_shares != 0:
        raise ValueError("minimum order contract is frozen at 5 shares with no buffer")
    if not trading.yes_only_mode:
        raise ValueError("only YES tokens of home/draw/away result propositions are allowed")
    if (entry.prob_min, entry.prob_max) not in FROZEN_ARMS:
        raise ValueError("entry band must be exactly 0.96-0.999 or 0.99-0.999")
    if entry.stop_price != 0.70:
        raise ValueError("emergency stop_price is frozen at 0.70")
    if (
        entry.max_stop_slippage != 0.05
        or entry.max_stop_spread != 0.10
        or entry.max_stop_loss_fraction != 0.35
    ):
        raise ValueError(
            "stop execution safety is frozen at 5pp slippage, 10pp spread, 35% loss"
        )
    if entry.hours_min != 0 or entry.hours_max != 4:
        raise ValueError("in-play age window must remain [0h, 4h]")
    if archive.prob_min != 0 or archive.hours_max != 4:
        raise ValueError("archive envelope must cover the four-hour in-play universe")
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
        raise ValueError("soccer league classifier identity drift")
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
    """Load and validate the immutable Cat/Dog A/B configuration."""
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

    entry = WatermelonLiveEntryConfig(
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", entry_cfg.get("prob_min"), 0.96
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.999
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.70
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
            0.35,
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
        "POLYBOT_YES_ONLY", trading_cfg.get("yes_only_mode"), True
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
            "POLYBOT_MIN_LIQUIDITY", trading_cfg.get("min_liquidity"), 0.0
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H", trading_cfg.get("min_volume_24h"), 0.0
        ),
        min_cumulative_volume=_get_config_value(
            "POLYBOT_MIN_CUMULATIVE_VOLUME",
            trading_cfg.get("min_cumulative_volume"),
            0.0,
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
        max_new_positions_per_cycle=_get_config_value(
            "POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE",
            trading_cfg.get("max_new_positions_per_cycle"),
            20,
            int,
        ),
        max_emergency_sells_per_cycle=_get_config_value(
            "POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE",
            trading_cfg.get("max_emergency_sells_per_cycle"),
            1,
            int,
        ),
        experiment_capital_usdc=_get_config_value(
            "POLYBOT_EXPERIMENT_CAPITAL_USDC",
            trading_cfg.get("experiment_capital_usdc"),
            100.0,
        ),
        max_drawdown_stop=_get_config_value(
            "POLYBOT_MAX_DRAWDOWN_STOP",
            trading_cfg.get("max_drawdown_stop"),
            0.10,
        ),
        reentry_cooldown_hours=_get_config_value(
            "POLYBOT_REENTRY_COOLDOWN_HOURS",
            trading_cfg.get("reentry_cooldown_hours"),
            720.0,
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
