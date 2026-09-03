"""Resolved configuration for the Golden Plum full-match confirmation experiment."""

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
GOLD_START_UTC = "2026-09-01T00:00:00Z"
GOLD_ENTRY_END_UTC = "2026-10-01T00:00:00Z"
GOLD_FOLLOWUP_END_UTC = "2026-10-08T00:00:00Z"
US_MAJOR_START_UTC = "2026-09-02T10:30:00Z"
US_MAJOR_ENTRY_END_UTC = "2026-12-01T10:30:00Z"
US_MAJOR_FOLLOWUP_END_UTC = "2026-12-08T10:30:00Z"
MLB_LIVE_START_UTC = "2026-09-03T11:00:00Z"
MLB_LIVE_ENTRY_END_UTC = "2026-09-17T11:00:00Z"
MLB_LIVE_FOLLOWUP_END_UTC = "2026-09-24T11:00:00Z"
NHL_SHADOW_START_UTC = "2026-09-03T11:00:00Z"
NHL_SHADOW_ENTRY_END_UTC = "2026-12-03T11:00:00Z"
NHL_SHADOW_FOLLOWUP_END_UTC = "2026-12-10T11:00:00Z"
SOCCER_PREREGISTRATION = (
    "research/frozen-2026-08-31-full-match-no-time-exit-v2/"
    "PREREGISTRATION.md"
)
MLB_PREREGISTRATION = (
    "research/frozen-2026-09-01-multisport-mlb-shadow-v3/"
    "PREREGISTRATION.md"
)
US_MAJOR_PREREGISTRATION = (
    "research/frozen-2026-09-02-nba-nfl-shadow-v4/"
    "PREREGISTRATION.md"
)
MLB_LIVE_PREREGISTRATION = (
    "research/frozen-2026-09-03-mlb-live-ab-v7/PREREGISTRATION.md"
)
NHL_SHADOW_PREREGISTRATION = (
    "research/frozen-2026-09-03-nhl-shadow-v7/PREREGISTRATION.md"
)
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
CLASSIFIER_VERSION = "plum-major-sports-family-contract-v3"
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
    # Golden Plum trusts explicit Gamma live/ended lifecycle instead of a
    # wall-clock age ceiling. This also preserves delayed soccer matches,
    # baseball extra innings and overtime in the other supported sports.
    "soccer": None,
    "mlb": None,
    "nba": None,
    "nfl": None,
    "nhl": None,
}


@dataclass(frozen=True)
class SportParameterProfile:
    """Per-sport market shape and frozen exploratory parameter grid.

    Only soccer is live-enabled.  Direct two-team sports use the same broad
    initial grid until their own collected evidence supports a new frozen
    profile; keeping the values in separate records prevents a later sport
    change from silently changing every family.
    """

    code: str
    profile_version: str
    book_shape: str
    result_kinds: tuple[str, ...]
    expected_market_count: int
    expected_token_count: int
    source_clock_required: bool
    max_sweep_pages: int
    min_liquidity: float
    min_cumulative_volume: float
    min_volume_24h: float
    primary_prob_min: float
    primary_prob_max: float
    primary_take_profit: float
    primary_stop_delta: float
    primary_trend_observations: int
    primary_trend_min_cumulative_move: float
    primary_trend_max_pullback: float
    primary_trend_max_gap_seconds: float
    primary_min_leader_margin: float
    primary_max_entry_spread: float
    analysis_entry_thresholds: tuple[float, ...]
    analysis_target_prices: tuple[float, ...]
    analysis_stop_deltas: tuple[float, ...]
    analysis_trend_observations: tuple[int, ...]
    analysis_min_cumulative_moves: tuple[float, ...]

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "analysis_entry_thresholds": list(self.analysis_entry_thresholds),
            "analysis_min_cumulative_moves": list(
                self.analysis_min_cumulative_moves
            ),
            "analysis_stop_deltas": list(self.analysis_stop_deltas),
            "analysis_target_prices": list(self.analysis_target_prices),
            "analysis_trend_observations": list(
                self.analysis_trend_observations
            ),
            "book_shape": self.book_shape,
            "code": self.code,
            "expected_market_count": self.expected_market_count,
            "expected_token_count": self.expected_token_count,
            "max_sweep_pages": self.max_sweep_pages,
            "min_cumulative_volume": self.min_cumulative_volume,
            "min_liquidity": self.min_liquidity,
            "min_volume_24h": self.min_volume_24h,
            "primary_max_entry_spread": self.primary_max_entry_spread,
            "primary_min_leader_margin": self.primary_min_leader_margin,
            "primary_prob_max": self.primary_prob_max,
            "primary_prob_min": self.primary_prob_min,
            "primary_stop_delta": self.primary_stop_delta,
            "primary_take_profit": self.primary_take_profit,
            "primary_trend_max_gap_seconds": self.primary_trend_max_gap_seconds,
            "primary_trend_max_pullback": self.primary_trend_max_pullback,
            "primary_trend_min_cumulative_move": (
                self.primary_trend_min_cumulative_move
            ),
            "primary_trend_observations": self.primary_trend_observations,
            "profile_version": self.profile_version,
            "result_kinds": list(self.result_kinds),
            "source_clock_required": self.source_clock_required,
        }


_COMMON_EXPLORATORY_GRID = {
    "min_liquidity": 5000.0,
    "min_cumulative_volume": 5000.0,
    "min_volume_24h": 0.0,
    "primary_prob_min": 0.75,
    "primary_prob_max": 0.78,
    "primary_take_profit": 0.95,
    "primary_stop_delta": 0.15,
    "primary_trend_observations": 3,
    "primary_trend_min_cumulative_move": 0.02,
    "primary_trend_max_pullback": 0.01,
    "primary_trend_max_gap_seconds": 90.0,
    "primary_min_leader_margin": 0.005,
    "primary_max_entry_spread": 0.05,
    "analysis_entry_thresholds": (0.55, 0.60, 0.65, 0.70, 0.75, 0.80),
    "analysis_target_prices": (0.85, 0.90, 0.95),
    "analysis_stop_deltas": (0.05, 0.10, 0.15, 0.20),
    "analysis_trend_observations": (2, 3, 5),
    "analysis_min_cumulative_moves": (0.01, 0.02, 0.03, 0.05),
}
SPORT_PARAMETER_PROFILES = {
    "soccer": SportParameterProfile(
        code="soccer",
        profile_version="soccer-full-match-v2",
        book_shape="direct-six-result-books",
        result_kinds=("HOME", "DRAW", "AWAY"),
        expected_market_count=3,
        expected_token_count=6,
        source_clock_required=True,
        max_sweep_pages=4,
        **_COMMON_EXPLORATORY_GRID,
    ),
    **{
        family: SportParameterProfile(
            code=family,
            profile_version=f"{family}-collection-uncalibrated-v1",
            book_shape="direct-two-team-moneyline",
            result_kinds=("HOME", "AWAY"),
            expected_market_count=1,
            expected_token_count=2,
            source_clock_required=False,
            max_sweep_pages=2,
            **_COMMON_EXPLORATORY_GRID,
        )
        for family in ("mlb", "nba", "nfl", "nhl")
    },
}
_MLB_LIVE_GRID = {
    **_COMMON_EXPLORATORY_GRID,
    "primary_prob_min": 0.55,
    "primary_prob_max": 0.58,
    "primary_take_profit": 0.95,
    "primary_stop_delta": 0.15,
    "primary_trend_observations": 5,
    "primary_trend_min_cumulative_move": 0.01,
}
SPORT_PARAMETER_PROFILES["mlb_live"] = SportParameterProfile(
    code="mlb",
    profile_version="mlb-gold-15-event-exploratory-live-v1",
    book_shape="direct-two-team-moneyline",
    result_kinds=("HOME", "AWAY"),
    expected_market_count=1,
    expected_token_count=2,
    source_clock_required=False,
    max_sweep_pages=2,
    **_MLB_LIVE_GRID,
)


@dataclass(frozen=True)
class RuntimeSpec:
    """One atomic Jenkins/runtime contract.

    Keeping mode, sport, protocol, target, cadence, and workspace in one
    immutable record prevents a shell override from composing an unregistered
    hybrid experiment.
    """

    runtime_job: str
    jenkins_job: str
    sport_family: str
    simulation_mode: bool
    lifecycle_mode: str
    execution_policy: str
    take_profit_price: float
    protocol_id: str
    preregistration_path: str
    cadence_seconds: int
    hard_deadline_seconds: Optional[float]
    external_workspace_path: Optional[str]
    experiment_start_utc: str
    experiment_entry_end_utc: str
    experiment_followup_end_utc: str
    scaling_notionals_usdc: tuple[float, ...] = ()
    sport_profile_key: Optional[str] = None


RUNTIME_SPECS = {
    "plum-live-king-90-1m-v1": RuntimeSpec(
        runtime_job="plum-live-king-90-1m-v1",
        jenkins_job="polybot-king",
        sport_family="soccer",
        simulation_mode=False,
        lifecycle_mode="active",
        execution_policy="exact-5-usdc-fok-live",
        take_profit_price=0.90,
        protocol_id="plum-soccer-full-match-v2",
        preregistration_path=SOCCER_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=None,
        external_workspace_path=None,
        experiment_start_utc=FROZEN_START_UTC,
        experiment_entry_end_utc=FROZEN_ENTRY_END_UTC,
        experiment_followup_end_utc=FROZEN_FOLLOWUP_END_UTC,
    ),
    "plum-live-queen-95-1m-v1": RuntimeSpec(
        runtime_job="plum-live-queen-95-1m-v1",
        jenkins_job="polybot-queen",
        sport_family="soccer",
        simulation_mode=False,
        lifecycle_mode="active",
        execution_policy="exact-5-usdc-fok-live",
        take_profit_price=0.95,
        protocol_id="plum-soccer-full-match-v2",
        preregistration_path=SOCCER_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=None,
        external_workspace_path=None,
        experiment_start_utc=FROZEN_START_UTC,
        experiment_entry_end_utc=FROZEN_ENTRY_END_UTC,
        experiment_followup_end_utc=FROZEN_FOLLOWUP_END_UTC,
    ),
    "plum-live-king-mlb-90-1m-v1": RuntimeSpec(
        runtime_job="plum-live-king-mlb-90-1m-v1",
        jenkins_job="polybot-king",
        sport_family="mlb",
        simulation_mode=False,
        lifecycle_mode="active",
        execution_policy="adaptive-fok-live-baseline-5-usdc",
        take_profit_price=0.90,
        protocol_id="plum-mlb-live-gold-informed-v7",
        preregistration_path=MLB_LIVE_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=None,
        external_workspace_path=None,
        experiment_start_utc=MLB_LIVE_START_UTC,
        experiment_entry_end_utc=MLB_LIVE_ENTRY_END_UTC,
        experiment_followup_end_utc=MLB_LIVE_FOLLOWUP_END_UTC,
        sport_profile_key="mlb_live",
    ),
    "plum-live-queen-mlb-95-1m-v1": RuntimeSpec(
        runtime_job="plum-live-queen-mlb-95-1m-v1",
        jenkins_job="polybot-queen",
        sport_family="mlb",
        simulation_mode=False,
        lifecycle_mode="active",
        execution_policy="adaptive-fok-live-baseline-5-usdc",
        take_profit_price=0.95,
        protocol_id="plum-mlb-live-gold-informed-v7",
        preregistration_path=MLB_LIVE_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=None,
        external_workspace_path=None,
        experiment_start_utc=MLB_LIVE_START_UTC,
        experiment_entry_end_utc=MLB_LIVE_ENTRY_END_UTC,
        experiment_followup_end_utc=MLB_LIVE_FOLLOWUP_END_UTC,
        sport_profile_key="mlb_live",
    ),
    "plum-shadow-silver-1m-v1": RuntimeSpec(
        runtime_job="plum-shadow-silver-1m-v1",
        jenkins_job="polybot-silver",
        sport_family="soccer",
        simulation_mode=True,
        lifecycle_mode="active",
        execution_policy="credential-free-displayed-book-simulation",
        take_profit_price=0.95,
        protocol_id="plum-soccer-full-match-v2",
        preregistration_path=SOCCER_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=50.0,
        external_workspace_path="/Volumes/t7/jenkins/polybot-silver",
        experiment_start_utc=FROZEN_START_UTC,
        experiment_entry_end_utc=FROZEN_ENTRY_END_UTC,
        experiment_followup_end_utc=FROZEN_FOLLOWUP_END_UTC,
        scaling_notionals_usdc=SIMULATION_SCALING_NOTIONALS_USDC,
    ),
    "plum-shadow-gold-mlb-1m-v1": RuntimeSpec(
        runtime_job="plum-shadow-gold-mlb-1m-v1",
        jenkins_job="polybot-gold",
        sport_family="mlb",
        simulation_mode=True,
        lifecycle_mode="active",
        execution_policy="credential-free-displayed-book-simulation",
        take_profit_price=0.95,
        protocol_id="plum-mlb-shadow-v3",
        preregistration_path=MLB_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=50.0,
        external_workspace_path="/Volumes/t7/jenkins/polybot-gold",
        experiment_start_utc=GOLD_START_UTC,
        experiment_entry_end_utc=GOLD_ENTRY_END_UTC,
        experiment_followup_end_utc=GOLD_FOLLOWUP_END_UTC,
        scaling_notionals_usdc=SIMULATION_SCALING_NOTIONALS_USDC,
    ),
    "plum-shadow-gold-nfl-1m-v1": RuntimeSpec(
        runtime_job="plum-shadow-gold-nfl-1m-v1",
        jenkins_job="polybot-gold",
        sport_family="nfl",
        simulation_mode=True,
        lifecycle_mode="active",
        execution_policy="credential-free-displayed-book-simulation",
        take_profit_price=0.95,
        protocol_id="plum-nfl-shadow-v4",
        preregistration_path=US_MAJOR_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=50.0,
        external_workspace_path="/Volumes/t7/jenkins/polybot-gold",
        experiment_start_utc=US_MAJOR_START_UTC,
        experiment_entry_end_utc=US_MAJOR_ENTRY_END_UTC,
        experiment_followup_end_utc=US_MAJOR_FOLLOWUP_END_UTC,
        scaling_notionals_usdc=SIMULATION_SCALING_NOTIONALS_USDC,
    ),
    "plum-shadow-gold-nba-1m-v1": RuntimeSpec(
        runtime_job="plum-shadow-gold-nba-1m-v1",
        jenkins_job="polybot-gold",
        sport_family="nba",
        simulation_mode=True,
        lifecycle_mode="active",
        execution_policy="credential-free-displayed-book-simulation",
        take_profit_price=0.95,
        protocol_id="plum-nba-shadow-v4",
        preregistration_path=US_MAJOR_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=50.0,
        external_workspace_path="/Volumes/t7/jenkins/polybot-gold",
        experiment_start_utc=US_MAJOR_START_UTC,
        experiment_entry_end_utc=US_MAJOR_ENTRY_END_UTC,
        experiment_followup_end_utc=US_MAJOR_FOLLOWUP_END_UTC,
        scaling_notionals_usdc=SIMULATION_SCALING_NOTIONALS_USDC,
    ),
    "plum-shadow-gold-nhl-1m-v1": RuntimeSpec(
        runtime_job="plum-shadow-gold-nhl-1m-v1",
        jenkins_job="polybot-gold",
        sport_family="nhl",
        simulation_mode=True,
        lifecycle_mode="active",
        execution_policy="credential-free-displayed-book-simulation",
        take_profit_price=0.95,
        protocol_id="plum-nhl-shadow-v7",
        preregistration_path=NHL_SHADOW_PREREGISTRATION,
        cadence_seconds=60,
        hard_deadline_seconds=50.0,
        external_workspace_path="/Volumes/t7/jenkins/polybot-gold",
        experiment_start_utc=NHL_SHADOW_START_UTC,
        experiment_entry_end_utc=NHL_SHADOW_ENTRY_END_UTC,
        experiment_followup_end_utc=NHL_SHADOW_FOLLOWUP_END_UTC,
        scaling_notionals_usdc=SIMULATION_SCALING_NOTIONALS_USDC,
    ),
}

# Compatibility/readability aliases are derived from the atomic records; they
# are never independently maintained.
FROZEN_JOB_TAKE_PROFIT_PRICE = {
    job: spec.take_profit_price for job, spec in RUNTIME_SPECS.items()
}
FROZEN_JOB_SPORT_FAMILY = {
    job: spec.sport_family for job, spec in RUNTIME_SPECS.items()
}
SIMULATION_RUNTIME_JOBS = frozenset(
    job for job, spec in RUNTIME_SPECS.items() if spec.simulation_mode
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
    registry = league_registry_payload(identities, cup_identities)
    # Parameter grids have their own identity.  Changing an MLB exploratory
    # threshold must not masquerade as a league/classifier mapping change.
    registry.pop("sport_parameter_profiles", None)
    payload = {
        "classifier_version": CLASSIFIER_VERSION,
        **registry,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


LEAGUE_MAPPING_SHA256 = league_mapping_sha256()
SPORT_PARAMETER_PROFILES_SHA256 = hashlib.sha256(
    json.dumps(
        {
            code: profile.canonical_dict()
            for code, profile in sorted(SPORT_PARAMETER_PROFILES.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


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


def _get_frozen_profile_value(
    env_key: str,
    profile_value,
    value_type: type = float,
):
    """Resolve env > versioned sport profile, never shared soccer YAML.

    The resulting value is still checked against the selected immutable
    profile.  This lets future MLB/NBA/NFL/NHL profiles differ without a
    change to ``config.yaml`` silently changing soccer.
    """
    env_value = os.getenv(env_key)
    if env_value is None:
        return profile_value
    return value_type(env_value)


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
    min_source_minute: float = 0.0
    # No source-minute ceiling: explicit live=true/ended=false is authoritative.
    max_source_minute: Optional[float] = None
    trend_observations: int = 3
    trend_min_cumulative_move: float = 0.02
    trend_max_pullback: float = 0.01
    trend_max_gap_seconds: float = 90.0
    min_leader_margin: float = 0.005
    max_entry_spread: float = 0.05
    take_profit_price: float = 0.90
    stop_loss_delta: float = 0.15
    # No time exit. Positions leave only by target, stop, or proven resolution.
    force_exit_minute: Optional[float] = None
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
    hours_max: Optional[float] = None


EntryConfig = PlumEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Small live-universe evidence archive bounds."""

    prob_min: float = 0.0
    hours_max: Optional[float] = None
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
    protocol_id: str = "plum-soccer-full-match-v2"
    preregistration_path: str = SOCCER_PREREGISTRATION
    runtime_spec_version: str = "golden-plum-runtime-v1"
    execution_policy: str = "exact-5-usdc-fok-live"
    cadence_seconds: int = 60
    cycle_hard_deadline_seconds: Optional[float] = None
    external_workspace_path: Optional[str] = None
    classifier_version: str = CLASSIFIER_VERSION
    league_mapping_sha256: str = LEAGUE_MAPPING_SHA256
    sport_parameter_profiles_sha256: str = SPORT_PARAMETER_PROFILES_SHA256
    sport_profile_version: str = "soccer-full-match-v2"
    book_shape: str = "direct-six-result-books"
    expected_result_kinds: tuple[str, ...] = ("HOME", "DRAW", "AWAY")
    expected_market_count: int = 3
    expected_token_count: int = 6
    source_clock_required: bool = True
    analysis_entry_thresholds: tuple[float, ...] = ()
    analysis_target_prices: tuple[float, ...] = ()
    analysis_stop_deltas: tuple[float, ...] = ()
    analysis_trend_observations: tuple[int, ...] = ()
    analysis_min_cumulative_moves: tuple[float, ...] = ()
    # Populated only for credential-free simulation runtimes. Live jobs retain
    # raw full-depth books but do not spend cycle time materializing this grid.
    scaling_notionals_usdc: tuple[float, ...] = ()
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
        "entry.trend_observations": entry.trend_observations,
        "entry.trend_min_cumulative_move": entry.trend_min_cumulative_move,
        "entry.trend_max_pullback": entry.trend_max_pullback,
        "entry.trend_max_gap_seconds": entry.trend_max_gap_seconds,
        "entry.min_leader_margin": entry.min_leader_margin,
        "entry.max_entry_spread": entry.max_entry_spread,
        "entry.take_profit_price": entry.take_profit_price,
        "entry.stop_loss_delta": entry.stop_loss_delta,
        "entry.stop_price": entry.stop_price,
        "entry.max_entry_drawdown": entry.max_entry_drawdown,
        "entry.max_stop_slippage": entry.max_stop_slippage,
        "entry.max_stop_spread": entry.max_stop_spread,
        "entry.max_stop_loss_fraction": entry.max_stop_loss_fraction,
        "entry.hours_min": entry.hours_min,
        "archive.prob_min": archive.prob_min,
        "archive.retention_days": archive.retention_days,
    }
    for name, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    for index, value in enumerate(trading.scaling_notionals_usdc):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"scaling_notionals_usdc[{index}] must be finite and positive"
            )
    for field_name, values in (
        ("analysis_entry_thresholds", trading.analysis_entry_thresholds),
        ("analysis_target_prices", trading.analysis_target_prices),
        ("analysis_stop_deltas", trading.analysis_stop_deltas),
        ("analysis_min_cumulative_moves", trading.analysis_min_cumulative_moves),
    ):
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError(f"{field_name} must contain finite positive values")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in trading.analysis_trend_observations
    ):
        raise ValueError(
            "analysis_trend_observations must contain positive integers"
        )
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError(
            "lifecycle_mode must be one of: active, close_only, archive_only"
        )
    runtime_spec = RUNTIME_SPECS.get(job_name)
    if runtime_spec is None:
        raise ValueError(f"unsupported Golden Plum runtime job: {job_name}")
    expected_sport_family = runtime_spec.sport_family
    if trading.sport_family != expected_sport_family:
        raise ValueError(
            f"{job_name} sport family must remain {expected_sport_family}"
        )
    profile_key = runtime_spec.sport_profile_key or trading.sport_family
    profile = SPORT_PARAMETER_PROFILES[profile_key]
    if (
        profile.code != trading.sport_family
        or trading.lifecycle_mode != runtime_spec.lifecycle_mode
        or trading.protocol_id != runtime_spec.protocol_id
        or trading.preregistration_path != runtime_spec.preregistration_path
        or trading.runtime_spec_version != "golden-plum-runtime-v1"
        or trading.execution_policy != runtime_spec.execution_policy
        or trading.cadence_seconds != runtime_spec.cadence_seconds
        or trading.cycle_hard_deadline_seconds
        != runtime_spec.hard_deadline_seconds
        or trading.external_workspace_path
        != runtime_spec.external_workspace_path
        or trading.sport_profile_version != profile.profile_version
        or trading.book_shape != profile.book_shape
        or tuple(trading.expected_result_kinds) != profile.result_kinds
        or trading.expected_market_count != profile.expected_market_count
        or trading.expected_token_count != profile.expected_token_count
        or trading.source_clock_required is not profile.source_clock_required
        or tuple(trading.analysis_entry_thresholds)
        != profile.analysis_entry_thresholds
        or tuple(trading.analysis_target_prices) != profile.analysis_target_prices
        or tuple(trading.analysis_stop_deltas) != profile.analysis_stop_deltas
        or tuple(trading.analysis_trend_observations)
        != profile.analysis_trend_observations
        or tuple(trading.analysis_min_cumulative_moves)
        != profile.analysis_min_cumulative_moves
    ):
        raise ValueError("runtime or sport-specific parameter profile drift")
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
        raise ValueError("Golden Plum target notional must be $5-$1000 in cent precision")
    if (
        trading.min_liquidity != profile.min_liquidity
        or trading.min_cumulative_volume != profile.min_cumulative_volume
        or trading.min_volume_24h != profile.min_volume_24h
    ):
        raise ValueError(
            "Golden Plum liquidity gate is frozen at $5k cumulative "
            "volume/$5k liquidity plus a baseline-$5 executable-book gate"
        )
    if (
        trading.max_positions != 10
        or trading.max_event_positions != 1
        or trading.max_new_positions_per_cycle != 5
    ):
        raise ValueError("Golden Plum exposure limits are frozen at 10/1/5")
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
        raise ValueError("Golden Plum must inspect direct YES and NO books")
    if (entry.prob_min, entry.prob_max) != (
        profile.primary_prob_min,
        profile.primary_prob_max,
    ):
        raise ValueError(
            f"{trading.sport_family} entry first-cross VWAP band drift"
        )
    expected_take_profit = runtime_spec.take_profit_price
    if entry.take_profit_price != expected_take_profit:
        raise ValueError(
            f"{job_name} take-profit price must remain {expected_take_profit:.2f}"
        )
    expected_simulation = runtime_spec.simulation_mode
    if simulation_mode is not expected_simulation:
        expected_mode = "simulation" if expected_simulation else "live"
        raise ValueError(f"{job_name} is frozen to {expected_mode} mode")
    expected_scaling_notionals = runtime_spec.scaling_notionals_usdc
    if tuple(trading.scaling_notionals_usdc) != expected_scaling_notionals:
        raise ValueError(
            "order-size scaling evidence is frozen to simulation runtimes only"
        )
    if (
        entry.min_source_minute != 0
        or entry.max_source_minute is not None
        or entry.trend_observations != profile.primary_trend_observations
        or entry.trend_min_cumulative_move
        != profile.primary_trend_min_cumulative_move
        or entry.trend_max_pullback != profile.primary_trend_max_pullback
        or entry.trend_max_gap_seconds != profile.primary_trend_max_gap_seconds
        or entry.min_leader_margin != profile.primary_min_leader_margin
        or entry.max_entry_spread != profile.primary_max_entry_spread
        or entry.stop_loss_delta != profile.primary_stop_delta
        or entry.force_exit_minute is not None
    ):
        raise ValueError("full-match trend/first-cross/TP-SL contract drift")
    if not (
        entry.prob_min < entry.prob_max < entry.take_profit_price < 1
        and entry.min_source_minute == 0
    ):
        raise ValueError("entry, target, and source-minute contract is invalid")
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
    if entry.hours_min != 0 or entry.hours_max is not None:
        raise ValueError("in-play age must start at zero with no upper limit")
    if archive.prob_min != 0 or archive.hours_max is not None:
        raise ValueError("archive must cover the full explicitly live match")
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
        trading.experiment_start_utc != runtime_spec.experiment_start_utc
        or trading.experiment_entry_end_utc
        != runtime_spec.experiment_entry_end_utc
        or trading.experiment_followup_end_utc
        != runtime_spec.experiment_followup_end_utc
    ):
        raise ValueError("experiment timestamps differ from the frozen deployment")
    if (
        trading.classifier_version != CLASSIFIER_VERSION
        or trading.league_mapping_sha256 != LEAGUE_MAPPING_SHA256
        or trading.sport_parameter_profiles_sha256
        != SPORT_PARAMETER_PROFILES_SHA256
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
    """Load and validate one immutable live or research runtime cohort."""
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

    runtime_spec = RUNTIME_SPECS.get(job_name)
    if runtime_spec is None:
        raise ValueError(f"unsupported Golden Plum runtime job: {job_name}")

    configured_family = os.getenv("POLYBOT_SPORT_FAMILY")
    if configured_family is None:
        configured_family = runtime_spec.sport_family
    resolved_sport_family = str(configured_family).strip().lower()
    profile_key = runtime_spec.sport_profile_key or resolved_sport_family
    profile = SPORT_PARAMETER_PROFILES.get(profile_key)
    if profile is None:
        raise ValueError(
            f"unsupported Golden Plum sport family: "
            f"{resolved_sport_family or '<empty>'}"
        )
    if profile.code != resolved_sport_family:
        raise ValueError("Golden Plum runtime sport/profile identity mismatch")
    entry = PlumEntryConfig(
        prob_min=_get_frozen_profile_value(
            "POLYBOT_ENTRY_PROB_MIN",
            profile.primary_prob_min,
        ),
        prob_max=_get_frozen_profile_value(
            "POLYBOT_ENTRY_PROB_MAX",
            profile.primary_prob_max,
        ),
        min_source_minute=_get_frozen_profile_value(
            "POLYBOT_MIN_SOURCE_MINUTE",
            0.0,
        ),
        max_source_minute=_get_frozen_profile_value(
            "POLYBOT_MAX_SOURCE_MINUTE",
            None,
        ),
        trend_observations=_get_frozen_profile_value(
            "POLYBOT_TREND_OBSERVATIONS",
            profile.primary_trend_observations,
            int,
        ),
        trend_min_cumulative_move=_get_frozen_profile_value(
            "POLYBOT_TREND_MIN_CUMULATIVE_MOVE",
            profile.primary_trend_min_cumulative_move,
        ),
        trend_max_pullback=_get_frozen_profile_value(
            "POLYBOT_TREND_MAX_PULLBACK",
            profile.primary_trend_max_pullback,
        ),
        trend_max_gap_seconds=_get_frozen_profile_value(
            "POLYBOT_TREND_MAX_GAP_SECONDS",
            profile.primary_trend_max_gap_seconds,
        ),
        min_leader_margin=_get_frozen_profile_value(
            "POLYBOT_MIN_LEADER_MARGIN",
            profile.primary_min_leader_margin,
        ),
        max_entry_spread=_get_frozen_profile_value(
            "POLYBOT_MAX_ENTRY_SPREAD",
            profile.primary_max_entry_spread,
        ),
        take_profit_price=_get_frozen_profile_value(
            "POLYBOT_TAKE_PROFIT_PRICE",
            runtime_spec.take_profit_price,
        ),
        stop_loss_delta=_get_frozen_profile_value(
            "POLYBOT_STOP_LOSS_DELTA",
            profile.primary_stop_delta,
        ),
        force_exit_minute=_get_frozen_profile_value(
            "POLYBOT_FORCE_EXIT_MINUTE",
            None,
        ),
        stop_price=_get_config_value(
            "POLYBOT_STOP_PRICE", entry_cfg.get("stop_price"), 0.01
        ),
        max_entry_drawdown=_get_config_value(
            "POLYBOT_MAX_ENTRY_DRAWDOWN",
            None,
            profile.primary_stop_delta,
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
            "POLYBOT_ENTRY_HOURS_MAX", entry_cfg.get("hours_max"), None
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.0
        ),
        hours_max=_get_config_value(
            "POLYBOT_ARCHIVE_HOURS_MAX", archive_cfg.get("hours_max"), None
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
        lifecycle_mode=_get_lifecycle_mode(runtime_spec.lifecycle_mode),
        sport_family=resolved_sport_family,
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT", trading_cfg.get("buy_amount_usdc"), 5.0
        ),
        min_liquidity=_get_frozen_profile_value(
            "POLYBOT_MIN_LIQUIDITY", profile.min_liquidity
        ),
        min_volume_24h=_get_frozen_profile_value(
            "POLYBOT_MIN_VOLUME_24H", profile.min_volume_24h
        ),
        min_cumulative_volume=_get_frozen_profile_value(
            "POLYBOT_MIN_CUMULATIVE_VOLUME",
            profile.min_cumulative_volume,
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
            runtime_spec.experiment_start_utc,
        ),
        experiment_entry_end_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_END_UTC",
            None,
            runtime_spec.experiment_entry_end_utc,
        ),
        experiment_followup_end_utc=_get_datetime_config_value(
            "POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC",
            None,
            runtime_spec.experiment_followup_end_utc,
        ),
        strategy_source_digest=compute_strategy_source_digest(
            SOURCE_PROJECT_ROOT, runtime_spec.preregistration_path
        ),
        preregistration_sha256=preregistration_sha256(
            SOURCE_PROJECT_ROOT, runtime_spec.preregistration_path
        ),
        protocol_id=runtime_spec.protocol_id,
        preregistration_path=runtime_spec.preregistration_path,
        runtime_spec_version="golden-plum-runtime-v1",
        execution_policy=runtime_spec.execution_policy,
        cadence_seconds=runtime_spec.cadence_seconds,
        cycle_hard_deadline_seconds=runtime_spec.hard_deadline_seconds,
        external_workspace_path=runtime_spec.external_workspace_path,
        sport_profile_version=profile.profile_version,
        book_shape=profile.book_shape,
        expected_result_kinds=profile.result_kinds,
        expected_market_count=profile.expected_market_count,
        expected_token_count=profile.expected_token_count,
        source_clock_required=profile.source_clock_required,
        analysis_entry_thresholds=profile.analysis_entry_thresholds,
        analysis_target_prices=profile.analysis_target_prices,
        analysis_stop_deltas=profile.analysis_stop_deltas,
        analysis_trend_observations=profile.analysis_trend_observations,
        analysis_min_cumulative_moves=profile.analysis_min_cumulative_moves,
        scaling_notionals_usdc=runtime_spec.scaling_notionals_usdc,
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
        simulation_mode = runtime_spec.simulation_mode
    if not isinstance(simulation_mode, bool):
        raise ValueError("simulation_mode must be a boolean")
    if simulation_mode is not runtime_spec.simulation_mode:
        expected_mode = "simulation" if runtime_spec.simulation_mode else "live"
        raise ValueError(f"{job_name} is frozen to {expected_mode} mode")

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
