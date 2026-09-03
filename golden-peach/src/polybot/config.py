"""Resolved configuration for the Golden Peach kickoff-leader experiment."""

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
FROZEN_START_UTC = "2026-08-30T00:00:00Z"
FROZEN_ENTRY_END_UTC = "2026-09-13T00:00:00Z"
FROZEN_FOLLOWUP_END_UTC = "2026-09-20T00:00:00Z"
MLB_LIVE_START_UTC = "2026-09-03T11:00:00Z"
MLB_LIVE_ENTRY_END_UTC = "2026-09-17T11:00:00Z"
MLB_LIVE_FOLLOWUP_END_UTC = "2026-09-24T11:00:00Z"
DIRECT_LATE_SENTINEL_MINUTE = 1_000_000.0
FROZEN_JOB_TAKE_PROFIT = {
    "peach-live-eco-3pp-1m-v1": 0.03,
    "peach-live-fruit-5pp-1m-v1": 0.05,
    "peach-live-eco-mlb-7pp-20sl-1m-v1": 0.07,
    "peach-live-fruit-mlb-10pp-20sl-1m-v1": 0.10,
    "peach-shadow-1m-v1": 0.05,
    "peach-shadow-mlb-1m-v2": 0.05,
    "peach-shadow-nba-1m-v2": 0.05,
    "peach-shadow-nfl-1m-v2": 0.05,
    "peach-shadow-nhl-1m-v2": 0.05,
}
FROZEN_JOB_SPORT_FAMILY = {
    "peach-live-eco-3pp-1m-v1": "soccer",
    "peach-live-fruit-5pp-1m-v1": "soccer",
    "peach-live-eco-mlb-7pp-20sl-1m-v1": "mlb",
    "peach-live-fruit-mlb-10pp-20sl-1m-v1": "mlb",
    "peach-shadow-1m-v1": "soccer",
    "peach-shadow-mlb-1m-v2": "mlb",
    "peach-shadow-nba-1m-v2": "nba",
    "peach-shadow-nfl-1m-v2": "nfl",
    "peach-shadow-nhl-1m-v2": "nhl",
}
FROZEN_JOB_PROFILE_KEY = {
    job: (
        "mlb_live"
        if job in {
            "peach-live-eco-mlb-7pp-20sl-1m-v1",
            "peach-live-fruit-mlb-10pp-20sl-1m-v1",
        }
        else family
    )
    for job, family in FROZEN_JOB_SPORT_FAMILY.items()
}
FROZEN_JOB_STOP_LOSS = {
    job: (
        0.20
        if job in {
            "peach-live-eco-mlb-7pp-20sl-1m-v1",
            "peach-live-fruit-mlb-10pp-20sl-1m-v1",
        }
        else 0.10
    )
    for job in FROZEN_JOB_SPORT_FAMILY
}
FROZEN_SIMULATION_JOBS = frozenset(
    job for job in FROZEN_JOB_SPORT_FAMILY if "-shadow-" in job
)
FROZEN_JOB_EXPERIMENT_DATES = {
    job: (
        (MLB_LIVE_START_UTC, MLB_LIVE_ENTRY_END_UTC, MLB_LIVE_FOLLOWUP_END_UTC)
        if FROZEN_JOB_SPORT_FAMILY[job] == "mlb" and job not in FROZEN_SIMULATION_JOBS
        else (FROZEN_START_UTC, FROZEN_ENTRY_END_UTC, FROZEN_FOLLOWUP_END_UTC)
    )
    for job in FROZEN_JOB_SPORT_FAMILY
}
SIMULATION_SCALING_NOTIONALS_USDC = (
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
    200.0,
    250.0,
    500.0,
    750.0,
    1000.0,
)
BASELINE_EXECUTION_NOTIONAL_USDC = 5.0
MAX_TARGET_BUY_NOTIONAL_USDC = 1000.0
ADAPTIVE_BUY_NOTIONAL_LADDER_USDC = SIMULATION_SCALING_NOTIONALS_USDC
SOCCER_TAG_ID = 100350
MLB_TAG_ID = 100381
NBA_TAG_ID = 745
NFL_TAG_ID = 450
NHL_TAG_ID = 899
ESPORTS_TAG_ID = 64
REQUIRED_COMMON_TAG_IDS = (1, 100639, SOCCER_TAG_ID)
CLASSIFIER_VERSION = "peach-major-sports-family-contract-v2"
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
    "nba": DirectSportIdentity("nba", 34, "NBA", NBA_TAG_ID, 10345, "nba"),
    "nfl": DirectSportIdentity("nfl", 10, "NFL", NFL_TAG_ID, 10187, "nfl"),
    "nhl": DirectSportIdentity("nhl", 35, "NHL", NHL_TAG_ID, 10346, "nhl"),
}
SPORT_FAMILY_TAG_IDS = {
    "soccer": SOCCER_TAG_ID,
    "mlb": MLB_TAG_ID,
    "nba": NBA_TAG_ID,
    "nfl": NFL_TAG_ID,
    "nhl": NHL_TAG_ID,
}
SPORT_FAMILY_MAX_IN_PLAY_HOURS = {
    "soccer": 4.0,
    "mlb": 8.0,
    "nba": 5.0,
    "nfl": 6.0,
    "nhl": 5.0,
}


@dataclass(frozen=True)
class SportParameterProfile:
    """Per-sport market shape and evidence contract.

    Runtime names select a profile atomically.  A direct-sport collection
    profile therefore cannot silently become a live profile, and the separate
    MLB live profile records exactly which exploratory TP/SL cohort is running.
    """

    code: str
    profile_version: str
    book_shape: str
    expected_result_kinds: tuple[str, ...]
    expected_market_count: int
    expected_token_count: int
    source_clock_required: bool
    max_sweep_pages: int
    max_in_play_hours: float

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "book_shape": self.book_shape,
            "code": self.code,
            "expected_market_count": self.expected_market_count,
            "expected_result_kinds": list(self.expected_result_kinds),
            "expected_token_count": self.expected_token_count,
            "max_in_play_hours": self.max_in_play_hours,
            "max_sweep_pages": self.max_sweep_pages,
            "profile_version": self.profile_version,
            "source_clock_required": self.source_clock_required,
        }


SPORT_PARAMETER_PROFILES = {
    "soccer": SportParameterProfile(
        code="soccer",
        profile_version="peach-soccer-kickoff-v3",
        book_shape="direct-six-result-books",
        expected_result_kinds=("HOME", "DRAW", "AWAY"),
        expected_market_count=3,
        expected_token_count=6,
        source_clock_required=True,
        max_sweep_pages=4,
        max_in_play_hours=4.0,
    ),
    **{
        family: SportParameterProfile(
            code=family,
            profile_version=f"peach-{family}-shadow-ready-v1",
            book_shape="direct-two-team-moneyline",
            expected_result_kinds=("HOME", "AWAY"),
            expected_market_count=1,
            expected_token_count=2,
            # Gamma's scheduled start is kept as a coarse shadow-only clock.
            # No direct-sport live runtime is registered from this evidence.
            source_clock_required=False,
            max_sweep_pages=2,
            max_in_play_hours=SPORT_FAMILY_MAX_IN_PLAY_HOURS[family],
        )
        for family in ("mlb", "nba", "nfl", "nhl")
    },
}
SPORT_PARAMETER_PROFILES["mlb_live"] = SportParameterProfile(
    code="mlb",
    profile_version="peach-mlb-kickoff-live-gold-informed-v1",
    book_shape="direct-two-team-moneyline",
    expected_result_kinds=("HOME", "AWAY"),
    expected_market_count=1,
    expected_token_count=2,
    # MLB has no soccer-like minute feed. Entry requires explicit live state
    # plus scheduled-start age in [0,10m], which is retained as a limitation.
    source_clock_required=False,
    max_sweep_pages=2,
    max_in_play_hours=SPORT_FAMILY_MAX_IN_PLAY_HOURS["mlb"],
)


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
        "sport_parameter_profiles": {
            code: profile.canonical_dict()
            for code, profile in sorted(SPORT_PARAMETER_PROFILES.items())
        },
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
class PeachEntryConfig:
    """Frozen kickoff entry, profit target, and loss boundary."""

    prob_min: float = 0.60
    prob_max: float = 0.94
    max_source_minute: float = 10.0
    min_leader_margin: float = 0.005
    max_entry_spread: float = 0.05
    take_profit_delta: float = 0.03
    stop_loss_delta: float = 0.10
    late_exit_minute: float = 80.0
    late_profit_fraction: float = 0.50
    stop_cutoff_minute: float = 80.0
    # Absolute dust floor retained only as a defensive lower bound.  The
    # effective trigger is max(stop_price, confirmed entry - stop_loss_delta).
    stop_price: float = 0.01
    max_entry_drawdown: float = 0.10
    # A stop is a stop-limit contract, not permission to cross an arbitrary
    # post-game/dust book.  The full-depth FOK must remain within five points
    # of the trigger and inside a ten-point displayed spread.
    max_stop_slippage: float = 0.05
    max_stop_spread: float = 0.10
    max_stop_loss_fraction: float = 1.00
    hours_min: float = 0.0
    hours_max: float = 4.0


EntryConfig = PeachEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Small live-universe evidence archive bounds."""

    prob_min: float = 0.0
    hours_max: float = 4.0
    retention_days: int = 60


@dataclass
class TradingConfig:
    """Golden Peach live/shadow trading and evidence configuration."""

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
    sport_profile_version: str = SPORT_PARAMETER_PROFILES["soccer"].profile_version
    book_shape: str = SPORT_PARAMETER_PROFILES["soccer"].book_shape
    expected_result_kinds: tuple[str, ...] = (
        SPORT_PARAMETER_PROFILES["soccer"].expected_result_kinds
    )
    expected_market_count: int = SPORT_PARAMETER_PROFILES["soccer"].expected_market_count
    expected_token_count: int = SPORT_PARAMETER_PROFILES["soccer"].expected_token_count
    source_clock_required: bool = SPORT_PARAMETER_PROFILES["soccer"].source_clock_required
    scaling_notionals_usdc: tuple[float, ...] = ()
    entry: PeachEntryConfig = field(default_factory=PeachEntryConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    excluded_categories: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> PeachEntryConfig:
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
    profile_key = FROZEN_JOB_PROFILE_KEY.get(job_name)
    profile = SPORT_PARAMETER_PROFILES.get(profile_key or "")
    if profile is None:
        raise ValueError(f"unsupported Golden Peach runtime job: {job_name}")
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
        "entry.max_source_minute": entry.max_source_minute,
        "entry.min_leader_margin": entry.min_leader_margin,
        "entry.max_entry_spread": entry.max_entry_spread,
        "entry.take_profit_delta": entry.take_profit_delta,
        "entry.stop_loss_delta": entry.stop_loss_delta,
        "entry.late_exit_minute": entry.late_exit_minute,
        "entry.late_profit_fraction": entry.late_profit_fraction,
        "entry.stop_cutoff_minute": entry.stop_cutoff_minute,
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
    if not (
        BASELINE_EXECUTION_NOTIONAL_USDC
        <= trading.buy_amount_usdc
        <= MAX_TARGET_BUY_NOTIONAL_USDC
    ) or not math.isclose(
        trading.buy_amount_usdc * 100,
        round(trading.buy_amount_usdc * 100),
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise ValueError(
            "Golden Peach target notional must be $5-$1000 in cent precision"
        )
    if (
        trading.min_liquidity != 5000
        or trading.min_cumulative_volume != 5000
        or trading.min_volume_24h != 0
    ):
        raise ValueError(
            "Golden Peach liquidity gate is frozen at $5k cumulative "
            "volume/$5k liquidity plus a baseline-$5 executable-book gate"
        )
    if (
        trading.max_positions != 10
        or trading.max_event_positions != 1
        or trading.max_new_positions_per_cycle != 5
    ):
        raise ValueError("Golden Peach exposure limits are frozen at 10/1/5")
    if trading.buy_amount_usdc * trading.max_new_positions_per_cycle > 5000:
        raise ValueError("per-cycle target BUY notional must not exceed $5000")
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
        raise ValueError("Golden Peach must inspect direct YES and NO books")
    if (entry.prob_min, entry.prob_max) != (0.60, 0.94):
        raise ValueError("entry executable VWAP band is frozen at 0.60-0.94")
    expected_take_profit = FROZEN_JOB_TAKE_PROFIT.get(job_name)
    if expected_take_profit is None:
        raise ValueError(f"unsupported Golden Peach runtime job: {job_name}")
    if entry.take_profit_delta != expected_take_profit:
        raise ValueError(
            f"{job_name} take-profit delta must remain {expected_take_profit:.2f}"
        )
    expected_family = FROZEN_JOB_SPORT_FAMILY.get(job_name)
    if trading.sport_family != expected_family:
        raise ValueError(
            f"{job_name} sport family must remain {expected_family}"
        )
    expected_simulation = job_name in FROZEN_SIMULATION_JOBS
    if simulation_mode is not expected_simulation:
        expected_mode = "simulation" if expected_simulation else "live"
        raise ValueError(f"{job_name} is frozen to {expected_mode} mode")
    expected_stop_loss = FROZEN_JOB_STOP_LOSS[job_name]
    expected_late_minute = (
        80.0
        if trading.sport_family == "soccer"
        else DIRECT_LATE_SENTINEL_MINUTE
    )
    if (
        entry.max_source_minute != 10
        or entry.min_leader_margin != 0.005
        or entry.max_entry_spread != 0.05
        or entry.stop_loss_delta != expected_stop_loss
        or entry.late_exit_minute != expected_late_minute
        or entry.late_profit_fraction != 0.50
        or entry.stop_cutoff_minute != expected_late_minute
    ):
        raise ValueError("kickoff/leader/TP-SL/late-exit contract drift")
    if entry.stop_price != 0.01:
        raise ValueError("defensive absolute stop floor is frozen at 0.01")
    if entry.max_entry_drawdown != entry.stop_loss_delta:
        raise ValueError("stored entry stop must match the frozen stop-loss delta")
    if (
        entry.max_stop_slippage != 0.05
        or entry.max_stop_spread != 0.10
        or entry.max_stop_loss_fraction != 1.00
    ):
        raise ValueError(
            "stop execution safety is frozen at 5pp slippage, 10pp spread, full live-gap loss"
        )
    if (
        trading.sport_profile_version != profile.profile_version
        or trading.book_shape != profile.book_shape
        or trading.expected_result_kinds != profile.expected_result_kinds
        or trading.expected_market_count != profile.expected_market_count
        or trading.expected_token_count != profile.expected_token_count
        or trading.source_clock_required is not profile.source_clock_required
    ):
        raise ValueError("sport-specific market-shape profile drift")
    expected_scaling = (
        SIMULATION_SCALING_NOTIONALS_USDC if simulation_mode else ()
    )
    if trading.scaling_notionals_usdc != expected_scaling:
        raise ValueError("simulation sizing ladder or live empty-ladder contract drift")
    expected_hours_max = profile.max_in_play_hours
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
    smallest_order = BASELINE_EXECUTION_NOTIONAL_USDC / entry.prob_max
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
    expected_dates = FROZEN_JOB_EXPERIMENT_DATES[job_name]
    if (
        trading.experiment_start_utc,
        trading.experiment_entry_end_utc,
        trading.experiment_followup_end_utc,
    ) != expected_dates:
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
    """Load and validate one immutable Eco/Fruit/Grey cohort."""
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
    frozen_sport_family = FROZEN_JOB_SPORT_FAMILY.get(job_name)
    requested_sport_family = str(
        os.getenv(
            "POLYBOT_SPORT_FAMILY",
            trading_cfg.get("sport_family", "soccer"),
        )
    ).strip().lower()
    resolved_sport_family = frozen_sport_family or requested_sport_family
    if (
        frozen_sport_family is not None
        and os.getenv("POLYBOT_SPORT_FAMILY") is not None
        and requested_sport_family != frozen_sport_family
    ):
        raise ValueError(
            f"{job_name} sport family must remain {frozen_sport_family}"
        )
    profile_key = FROZEN_JOB_PROFILE_KEY.get(job_name)
    profile = SPORT_PARAMETER_PROFILES.get(profile_key or "")
    if profile is None:
        raise ValueError(f"unsupported Golden Peach runtime job: {job_name}")
    if profile.code != resolved_sport_family:
        raise ValueError("Golden Peach runtime sport/profile identity mismatch")

    frozen_take_profit = FROZEN_JOB_TAKE_PROFIT.get(job_name, 0.03)
    frozen_stop_loss = FROZEN_JOB_STOP_LOSS.get(job_name, 0.10)
    frozen_late_minute = (
        80.0
        if resolved_sport_family == "soccer"
        else DIRECT_LATE_SENTINEL_MINUTE
    )
    frozen_dates = FROZEN_JOB_EXPERIMENT_DATES.get(
        job_name,
        (FROZEN_START_UTC, FROZEN_ENTRY_END_UTC, FROZEN_FOLLOWUP_END_UTC),
    )
    entry = PeachEntryConfig(
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", entry_cfg.get("prob_min"), 0.60
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.94
        ),
        max_source_minute=_get_config_value(
            "POLYBOT_MAX_SOURCE_MINUTE",
            entry_cfg.get("max_source_minute"),
            10.0,
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
        take_profit_delta=_get_config_value(
            "POLYBOT_TAKE_PROFIT_DELTA",
            None,
            frozen_take_profit,
        ),
        stop_loss_delta=_get_config_value(
            "POLYBOT_STOP_LOSS_DELTA",
            None,
            frozen_stop_loss,
        ),
        late_exit_minute=_get_config_value(
            "POLYBOT_LATE_EXIT_MINUTE",
            None,
            frozen_late_minute,
        ),
        late_profit_fraction=_get_config_value(
            "POLYBOT_LATE_PROFIT_FRACTION",
            entry_cfg.get("late_profit_fraction"),
            0.50,
        ),
        stop_cutoff_minute=_get_config_value(
            "POLYBOT_STOP_CUTOFF_MINUTE",
            None,
            frozen_late_minute,
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.01
        ),
        max_entry_drawdown=_get_config_value(
            "POLYBOT_MAX_ENTRY_DRAWDOWN",
            None,
            frozen_stop_loss,
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
            "POLYBOT_ENTRY_HOURS_MAX",
            entry_cfg.get("hours_max") if resolved_sport_family == "soccer" else None,
            profile.max_in_play_hours,
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ARCHIVE_HOURS_MAX",
            archive_cfg.get("hours_max") if resolved_sport_family == "soccer" else None,
            profile.max_in_play_hours,
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
        sport_family=resolved_sport_family,
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
            None,
            frozen_dates[0],
        ),
        experiment_entry_end_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_END_UTC",
            None,
            frozen_dates[1],
        ),
        experiment_followup_end_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC",
            None,
            frozen_dates[2],
        ),
        strategy_source_digest=compute_strategy_source_digest(SOURCE_PROJECT_ROOT),
        preregistration_sha256=preregistration_sha256(SOURCE_PROJECT_ROOT),
        sport_profile_version=profile.profile_version,
        book_shape=profile.book_shape,
        expected_result_kinds=profile.expected_result_kinds,
        expected_market_count=profile.expected_market_count,
        expected_token_count=profile.expected_token_count,
        source_clock_required=profile.source_clock_required,
        scaling_notionals_usdc=(
            SIMULATION_SCALING_NOTIONALS_USDC
            if job_name in FROZEN_SIMULATION_JOBS
            else ()
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
