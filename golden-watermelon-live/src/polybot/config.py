"""Resolved configuration for the Golden Watermelon live sports strategy."""

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
FROZEN_START_UTC = "2026-08-29T04:00:00Z"
# A one-week continuation, not a fresh balance/loss-budget epoch. Keep the
# original start so existing positions and sport-specific guard history survive.
FROZEN_RESUME_UTC = "2026-09-05T09:05:00Z"
FROZEN_RETUNE_UTC = "2026-09-12T12:00:00Z"
FROZEN_ENTRY_END_UTC = "2026-09-15T12:00:00Z"
FROZEN_FOLLOWUP_END_UTC = "2026-09-22T12:00:00Z"
# The MLB safety budget restarts at the first successful v3e run.  Earlier MLB
# trades remain immutable performance evidence, but they used the superseded
# 5pp stop and must not consume the corrected cohort's entry kill switch.
MLB_ECONOMIC_GUARD_START_UTC = "2026-09-02T12:12:00Z"
FROZEN_ARMS = frozenset({(0.91, 0.999), (0.92, 0.999)})
BASELINE_EXECUTION_NOTIONAL_USDC = 5.0
MAX_TARGET_BUY_NOTIONAL_USDC = 1000.0
ADAPTIVE_BUY_NOTIONAL_LADDER_USDC = (
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
SOCCER_TAG_ID = 100350
MLB_TAG_ID = 100381
NHL_TAG_ID = 899
NFL_TAG_ID = 450
ESPORTS_TAG_ID = 64
REQUIRED_COMMON_TAG_IDS = (1, 100639, SOCCER_TAG_ID)
CLASSIFIER_VERSION = "watermelon-major-sports-identity-v1"
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
    "nfl": DirectSportIdentity("nfl", 10, "NFL", NFL_TAG_ID, 10187, "nfl"),
}
SPORT_FAMILY_TAG_IDS = {
    "soccer": SOCCER_TAG_ID,
    "mlb": MLB_TAG_ID,
    "nhl": NHL_TAG_ID,
    "nfl": NFL_TAG_ID,
}
SPORT_FAMILY_MAX_IN_PLAY_HOURS = {
    "soccer": 4.0,
    "mlb": 8.0,
    "nhl": 5.0,
    "nfl": 6.0,
}
ECONOMIC_GUARD_START_UTC_BY_SPORT = {
    "soccer": FROZEN_START_UTC,
    "mlb": MLB_ECONOMIC_GUARD_START_UTC,
    "nhl": FROZEN_START_UTC,
    "nfl": "2026-09-06T00:00:00Z",
}


@dataclass(frozen=True)
class RuntimeSpec:
    """One indivisible Jenkins/runtime/family/arm execution contract."""

    runtime_job: str
    jenkins_job: str
    sport_family: str
    prob_min: float
    simulation_mode: bool = False
    policy_key: str = ""


RUNTIME_SPECS = {
    spec.runtime_job: spec
    for spec in (
        RuntimeSpec(
            "watermelon-live-cat-96-1m-v2h", "polybot-cat", "soccer", 0.91
        ),
        RuntimeSpec(
            "watermelon-live-dog-99-1m-v2h", "polybot-dog", "soccer", 0.92
        ),
        RuntimeSpec(
            "watermelon-live-bear-mlb-96-1m-v3a", "polybot-bear", "mlb", 0.96
        ),
        RuntimeSpec(
            "watermelon-live-tiger-mlb-99-1m-v3a", "polybot-tiger", "mlb", 0.99
        ),
        RuntimeSpec(
            "watermelon-live-lion-nhl-96-1m-v3a", "polybot-lion", "nhl", 0.96
        ),
        RuntimeSpec(
            "watermelon-live-wolf-nhl-99-1m-v3a", "polybot-wolf", "nhl", 0.99
        ),
        RuntimeSpec(
            "watermelon-live-cat-mlb-96-1m-v4", "polybot-cat", "mlb", 0.96, policy_key="catdog_mlb"
        ),
        RuntimeSpec(
            "watermelon-live-dog-mlb-99-1m-v4", "polybot-dog", "mlb", 0.99, policy_key="catdog_mlb"
        ),
        RuntimeSpec("watermelon-live-cat-nfl-96-1m-v5", "polybot-cat", "nfl", 0.96, policy_key="catdog_nfl"),
        RuntimeSpec("watermelon-live-dog-nfl-99-1m-v5", "polybot-dog", "nfl", 0.99, policy_key="catdog_nfl"),
    )
}

# Existing six identities/values remain unchanged. Only these accounts share
# resources; Bear/Tiger histories and retired NHL configurations stay separate.
ACCOUNT_RUNTIMES = {
    "polybot-cat": ("watermelon-live-cat-96-1m-v2h", "watermelon-live-cat-mlb-96-1m-v4"),
    "polybot-dog": ("watermelon-live-dog-99-1m-v2h", "watermelon-live-dog-mlb-99-1m-v4"),
}


def account_for_runtime(runtime):
    return next((account for account, jobs in ACCOUNT_RUNTIMES.items() if runtime in jobs), None)


@dataclass(frozen=True)
class SportPolicy:
    hours_max: float
    prob_max: float = 0.999
    stop_price: float = 0.70
    max_entry_drawdown: float = 0.30
    max_stop_slippage: float = 0.05
    max_stop_spread: float = 0.10
    max_stop_loss_fraction: float = 0.35
    max_positions: int = 20
    max_event_positions: int = 1
    max_new_positions_per_cycle: int = 5
    max_emergency_sells_per_cycle: int = 1
    experiment_capital_usdc: float = 100
    max_drawdown_stop: float = 0.10


# Separate immutable entries even when numbers match. Future retuning requires
# a new preregistration/review; the retired MLB/NHL profiles do not inherit it.
SPORT_POLICIES = {"soccer": SportPolicy(4), "mlb": SportPolicy(8),
                  "nhl": SportPolicy(5), "catdog_mlb": SportPolicy(8),
                  "catdog_nfl": SportPolicy(6)}


def runtime_policy(spec):
    return SPORT_POLICIES[spec.policy_key or spec.sport_family]


def profile_environment(runtime, inherited):
    """Private child environment; never mutate process-global os.environ.

    Explicit sport defaults replace the old soccer-only shell's hours=4 in the
    MLB child. Other frozen values/credentials/lifecycle remain inherited and
    are still validated by load_config (including close_only).
    """
    spec = RUNTIME_SPECS[runtime]
    policy = runtime_policy(spec)
    env = dict(inherited)
    env.update(POLYBOT_SPORT_FAMILY=spec.sport_family,
               POLYBOT_ENTRY_PROB_MIN=str(spec.prob_min),
               POLYBOT_ENTRY_PROB_MAX=str(policy.prob_max),
               POLYBOT_ENTRY_HOURS_MAX=str(policy.hours_max),
               POLYBOT_ARCHIVE_HOURS_MAX=str(policy.hours_max),
               POLYBOT_STOP_PRICE=str(policy.stop_price),
               POLYBOT_MAX_ENTRY_DRAWDOWN=str(policy.max_entry_drawdown),
               POLYBOT_MAX_STOP_SLIPPAGE=str(policy.max_stop_slippage),
               POLYBOT_MAX_STOP_SPREAD=str(policy.max_stop_spread),
               POLYBOT_MAX_STOP_LOSS_FRACTION=str(policy.max_stop_loss_fraction))
    close_only_families = {
        value.strip().lower()
        for value in str(env.get("POLYBOT_CLOSE_ONLY_SPORT_FAMILIES", "")).split(",")
        if value.strip()
    }
    if close_only_families - {"soccer", "mlb", "nfl", "nhl"}:
        raise ValueError(
            "POLYBOT_CLOSE_ONLY_SPORT_FAMILIES contains an unsupported family"
        )
    if spec.sport_family in close_only_families:
        env["POLYBOT_LIFECYCLE_MODE"] = "close_only"
    if str(env.get("POLYBOT_TAKE_PROFIT_ENABLED", "false")).lower() in {"true", "1", "yes", "on"}:
        if spec.jenkins_job not in {"polybot-cat", "polybot-dog"}:
            raise ValueError("take-profit account release is Cat/Dog only")
        env.update(POLYBOT_ENTRY_PROB_MIN="0.95" if spec.jenkins_job == "polybot-cat" else "0.97",
                   POLYBOT_ENTRY_PROB_MAX="0.989")
    return env


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
class WatermelonLiveEntryConfig:
    """One frozen baseline-$5 in-play arm and its emergency stop."""

    prob_min: float = 0.96
    prob_max: float = 0.999
    stop_price: float = 0.70
    # The former 5pp relative stop repeatedly sold eventual winners. Keep the
    # 0.70 catastrophic floor while making this relative leg non-binding for
    # the 0.96/0.99 entry arms.
    max_entry_drawdown: float = 0.30
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
class TakeProfitConfig:
    """Opt-in release proposal; the registered production default stays off."""

    enabled: bool = False
    price: float = 0.99
    effective_from_utc: str = ""
    include_existing_holdings: bool = False
    max_book_age_seconds: float = 3.0
    fee_rounding_reserve_usdc: float = 0.001


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
    sport_family: str = "soccer"
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 5000.0
    min_volume_24h: float = 0.0
    min_cumulative_volume: float = 5000.0
    max_positions: int = 20
    max_event_positions: int = 1
    max_new_positions_per_cycle: int = 5
    max_emergency_sells_per_cycle: int = 1
    experiment_capital_usdc: float = 100.0
    max_drawdown_stop: float = 0.10
    reentry_cooldown_hours: float = 720.0
    max_snapshot_gap_minutes: float = 15.0
    fok_reconciliation_timeout_minutes: float = 2.0
    stop_sell_quarantine_timeout_minutes: float = 180.0
    pending_buy_quarantine_timeout_minutes: float = 180.0
    min_order_size: float = 5.0
    min_order_buffer_shares: float = 0.0
    yes_only_mode: bool = True
    experiment_start_utc: str = FROZEN_START_UTC
    experiment_entry_end_utc: str = FROZEN_ENTRY_END_UTC
    experiment_followup_end_utc: str = FROZEN_FOLLOWUP_END_UTC
    economic_guard_start_utc: str = FROZEN_START_UTC
    strategy_source_digest: str = ""
    preregistration_sha256: str = ""
    classifier_version: str = CLASSIFIER_VERSION
    league_mapping_sha256: str = LEAGUE_MAPPING_SHA256
    entry: WatermelonLiveEntryConfig = field(default_factory=WatermelonLiveEntryConfig)
    take_profit: TakeProfitConfig = field(default_factory=TakeProfitConfig)
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
    jenkins_job: str = ""


def _validate_config(
    trading: TradingConfig,
    api: ApiConfig,
    *,
    runtime_spec: RuntimeSpec,
    simulation_mode: bool,
) -> None:
    """Reject parameter drift before any network or database mutation."""
    entry = trading.entry
    archive = trading.archive
    policy = runtime_policy(runtime_spec)
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
        "pending_buy_quarantine_timeout_minutes": (
            trading.pending_buy_quarantine_timeout_minutes
        ),
        "min_order_size": trading.min_order_size,
        "min_order_buffer_shares": trading.min_order_buffer_shares,
        "entry.prob_min": entry.prob_min,
        "entry.prob_max": entry.prob_max,
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
    if trading.sport_family != runtime_spec.sport_family:
        raise ValueError(
            f"{runtime_spec.runtime_job} sport family must remain "
            f"{runtime_spec.sport_family}"
        )
    if simulation_mode is not runtime_spec.simulation_mode:
        expected = "simulation" if runtime_spec.simulation_mode else "live"
        raise ValueError(f"{runtime_spec.runtime_job} is frozen to {expected} mode")
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
            "Golden Watermelon target notional must be $5-$1000 in cent precision"
        )
    if (
        trading.min_liquidity != 5000
        or trading.min_cumulative_volume != 5000
        or trading.min_volume_24h != 0
    ):
        raise ValueError(
            "Golden Watermelon liquidity gate is frozen at $5k cumulative "
            "volume/$5k liquidity plus a baseline-$5 executable-book gate"
        )
    if (
        trading.max_positions != policy.max_positions
        or trading.max_event_positions != policy.max_event_positions
        or trading.max_new_positions_per_cycle != policy.max_new_positions_per_cycle
    ):
        raise ValueError("Golden Watermelon exposure limits are frozen at 20/1/5")
    if trading.buy_amount_usdc * trading.max_new_positions_per_cycle > 5000:
        raise ValueError("per-cycle target BUY notional must not exceed $5000")
    if trading.max_emergency_sells_per_cycle != policy.max_emergency_sells_per_cycle:
        raise ValueError("only one emergency SELL may be submitted per cycle")
    if trading.experiment_capital_usdc != policy.experiment_capital_usdc:
        raise ValueError("experiment capital is frozen at $100 requested exposure")
    if trading.max_drawdown_stop != policy.max_drawdown_stop:
        raise ValueError("economic drawdown entry guard is frozen at 10%")
    if trading.max_event_positions > trading.max_positions:
        raise ValueError("max_event_positions must be <= max_positions")
    if trading.reentry_cooldown_hours != 720:
        raise ValueError("reentry cooldown is frozen at 720 hours")
    if trading.max_snapshot_gap_minutes != 15:
        raise ValueError("snapshot maintenance cadence is frozen at 15 minutes")
    if trading.fok_reconciliation_timeout_minutes != 2:
        raise ValueError("delayed FOK reconciliation timeout is frozen at 2 minutes")
    if trading.stop_sell_quarantine_timeout_minutes != 180:
        raise ValueError("failed stop SELL quarantine timeout is frozen at 180 minutes")
    if trading.pending_buy_quarantine_timeout_minutes != 180:
        raise ValueError("ambiguous PENDING BUY quarantine timeout is frozen at 180 minutes")
    if trading.min_order_size != 5 or trading.min_order_buffer_shares != 0:
        raise ValueError("minimum order contract is frozen at 5 shares with no buffer")
    if not trading.yes_only_mode:
        raise ValueError(
            "YES tokens / direct winner tokens must remain winner-only"
        )
    tp = trading.take_profit
    if not isinstance(tp.enabled, bool) or not isinstance(tp.include_existing_holdings, bool):
        raise ValueError("take_profit flags must be booleans")
    expected_band = (runtime_spec.prob_min, policy.prob_max)
    if tp.enabled:
        if runtime_spec.jenkins_job not in {"polybot-cat", "polybot-dog"}:
            raise ValueError("the proposed take-profit release is Cat/Dog only")
        if tp.price != 0.99 or tp.max_book_age_seconds != 3.0 or tp.fee_rounding_reserve_usdc != 0.001:
            raise ValueError("take-profit release requires 0.99, 3s freshness and $0.001 fee reserve")
        try:
            effective = datetime.fromisoformat(tp.effective_from_utc.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("take_profit.effective_from_utc must be explicit ISO-8601") from error
        if effective.tzinfo is None:
            raise ValueError("take_profit.effective_from_utc must include timezone")
        if effective > datetime.now(timezone.utc):
            raise ValueError("take_profit effective time must not be in the future; entry and TP change together")
        expected_band = (0.95 if runtime_spec.jenkins_job == "polybot-cat" else 0.97, 0.989)
        if trading.buy_amount_usdc != 5:
            raise ValueError("the proposed take-profit release is fixed at $5")
    elif tp.include_existing_holdings:
        raise ValueError("existing holdings opt-in requires take_profit.enabled")
    if (entry.prob_min, entry.prob_max) != expected_band:
        raise ValueError(
            f"{runtime_spec.runtime_job} entry band must remain "
            f"{expected_band[0]:.2f}-{expected_band[1]}"
        )
    if entry.stop_price != policy.stop_price:
        raise ValueError("emergency stop_price is frozen at 0.70")
    if entry.max_entry_drawdown != policy.max_entry_drawdown:
        raise ValueError(
            "entry-relative stop must leave the absolute 0.70 floor binding"
        )
    if (
        entry.max_stop_slippage != policy.max_stop_slippage
        or entry.max_stop_spread != policy.max_stop_spread
        or entry.max_stop_loss_fraction != policy.max_stop_loss_fraction
    ):
        raise ValueError(
            "stop execution safety is frozen at 5pp slippage, 10pp spread, 35% loss"
        )
    expected_hours_max = policy.hours_max
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
    if (
        trading.experiment_start_utc != FROZEN_START_UTC
        or trading.experiment_entry_end_utc != FROZEN_ENTRY_END_UTC
        or trading.experiment_followup_end_utc != FROZEN_FOLLOWUP_END_UTC
    ):
        raise ValueError("experiment timestamps differ from the frozen deployment")
    expected_guard_start = ECONOMIC_GUARD_START_UTC_BY_SPORT[
        trading.sport_family
    ]
    if trading.economic_guard_start_utc != expected_guard_start:
        raise ValueError("economic guard start differs from the frozen sport epoch")
    guard_start = datetime.fromisoformat(
        trading.economic_guard_start_utc.replace("Z", "+00:00")
    )
    experiment_start = datetime.fromisoformat(
        trading.experiment_start_utc.replace("Z", "+00:00")
    )
    entry_end = datetime.fromisoformat(
        trading.experiment_entry_end_utc.replace("Z", "+00:00")
    )
    if not experiment_start <= guard_start < entry_end:
        raise ValueError("economic guard start must be inside the entry window")
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
    """Load and validate one registered, indivisible live runtime."""
    load_dotenv(env_path) if env_path else load_dotenv()

    runtime_spec = RUNTIME_SPECS.get(job_name)
    if runtime_spec is None:
        raise ValueError(f"unsupported Golden Watermelon Live runtime job: {job_name}")
    observed_jenkins_job = str(os.getenv("JOB_NAME") or "").strip()
    if observed_jenkins_job and observed_jenkins_job != runtime_spec.jenkins_job:
        raise ValueError(
            f"{job_name} must run under Jenkins job {runtime_spec.jenkins_job}; "
            f"got {observed_jenkins_job}"
        )

    path = Path(config_path)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    else:
        cfg = {}
    trading_cfg = get_trading_config_mapping(cfg)
    entry_cfg = trading_cfg.get("entry", {})
    archive_cfg = trading_cfg.get("archive", {})
    tp_cfg = trading_cfg.get("take_profit", {})
    if not isinstance(tp_cfg, dict):
        raise ValueError("trading.take_profit must be a mapping")
    if not isinstance(entry_cfg, dict) or not isinstance(archive_cfg, dict):
        raise ValueError("trading.entry and trading.archive must be mappings")

    take_profit = TakeProfitConfig(
        enabled=_get_bool_config_value("POLYBOT_TAKE_PROFIT_ENABLED", tp_cfg.get("enabled"), False),
        price=_get_config_value("POLYBOT_TAKE_PROFIT_PRICE", tp_cfg.get("price"), 0.99),
        effective_from_utc=str(os.getenv("POLYBOT_TAKE_PROFIT_EFFECTIVE_FROM_UTC", tp_cfg.get("effective_from_utc", ""))),
        include_existing_holdings=_get_bool_config_value("POLYBOT_TAKE_PROFIT_INCLUDE_EXISTING", tp_cfg.get("include_existing_holdings"), False),
        max_book_age_seconds=_get_config_value("POLYBOT_TAKE_PROFIT_MAX_BOOK_AGE_SECONDS", tp_cfg.get("max_book_age_seconds"), 3.0),
        fee_rounding_reserve_usdc=_get_config_value("POLYBOT_TAKE_PROFIT_FEE_RESERVE_USDC", tp_cfg.get("fee_rounding_reserve_usdc"), 0.001),
    )
    entry = WatermelonLiveEntryConfig(
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", None, runtime_spec.prob_min
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.999
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.70
        ),
        max_entry_drawdown=_get_config_value(
            "POLYBOT_MAX_ENTRY_DRAWDOWN",
            entry_cfg.get("max_entry_drawdown"),
            0.30,
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
            "POLYBOT_ENTRY_HOURS_MAX",
            None,
            SPORT_FAMILY_MAX_IN_PLAY_HOURS[runtime_spec.sport_family],
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ARCHIVE_HOURS_MAX",
            None,
            SPORT_FAMILY_MAX_IN_PLAY_HOURS[runtime_spec.sport_family],
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

    sport_family = str(
        os.getenv("POLYBOT_SPORT_FAMILY", runtime_spec.sport_family)
    ).strip().lower()
    trading = TradingConfig(
        lifecycle_mode=_get_lifecycle_mode(trading_cfg.get("lifecycle_mode")),
        sport_family=sport_family,
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
            5,
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
        pending_buy_quarantine_timeout_minutes=_get_config_value(
            "POLYBOT_PENDING_BUY_QUARANTINE_TIMEOUT_MINUTES",
            trading_cfg.get("pending_buy_quarantine_timeout_minutes"),
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
        economic_guard_start_utc=ECONOMIC_GUARD_START_UTC_BY_SPORT.get(
            sport_family, FROZEN_START_UTC
        ),
        strategy_source_digest=compute_strategy_source_digest(SOURCE_PROJECT_ROOT),
        preregistration_sha256=preregistration_sha256(SOURCE_PROJECT_ROOT, account_profile=runtime_spec.policy_key in {"catdog_mlb", "catdog_nfl"}, take_profit_profile=take_profit.enabled),
        entry=entry,
        take_profit=take_profit,
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
    if simulation_mode is None:
        simulation_mode = cfg.get("simulation_mode", True)
    if not isinstance(simulation_mode, bool):
        raise ValueError("simulation_mode must be a boolean")

    _validate_config(
        trading,
        api,
        runtime_spec=runtime_spec,
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
        jenkins_job=runtime_spec.jenkins_job,
    )
