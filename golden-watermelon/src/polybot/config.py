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
    verify_frozen_manifest,
)


DATA_CONTRACT = "watermelon-five-major-sports-inplay-match-winner-v6"
SCHEMA_PROFILE = "golden-watermelon-v4b-schema-v1"
UNIVERSE_PROFILE = "watermelon-soccer-mlb-nba-nfl-nhl-2026-09-v4b"
CLASSIFIER_VERSION = "watermelon-major-sports-identity-v2"
CANONICAL_JOB = "watermelon-white-1m-v4b"
LIFECYCLE_MODES = frozenset({"archive_only"})
SOCCER_TAG_ID = 100350
MLB_TAG_ID = 100381
NBA_TAG_ID = 745
NFL_TAG_ID = 450
NHL_TAG_ID = 899
ESPORTS_TAG_ID = 64
REQUIRED_COMMON_TAG_IDS = (1, 100639, SOCCER_TAG_ID)
SPORT_FAMILY_TAG_IDS = (
    ("soccer", SOCCER_TAG_ID),
    ("mlb", MLB_TAG_ID),
    ("nba", NBA_TAG_ID),
    ("nfl", NFL_TAG_ID),
    ("nhl", NHL_TAG_ID),
)
SPORT_FAMILIES = tuple(item[0] for item in SPORT_FAMILY_TAG_IDS)

# Immutable legacy epochs. The v4b runtime never accepts these jobs/contracts.
# The literals also keep repository-wide discovery aware of preserved evidence.
LEGACY_DATA_CONTRACT_V3 = "soccer-inplay-major-league-match-winner-v1"
LEGACY_DATA_CONTRACT_V3C = "soccer-inplay-elite-competition-match-winner-v3"
LEGACY_RUNTIME_JOBS = (
    "watermelon-white-1m-v3",
    "watermelon-grey-5m-v3",
    "watermelon-white-1m-v3a",
    "watermelon-grey-5m-v3a",
    "watermelon-white-1m-v3b",
    "watermelon-grey-5m-v3b",
    "watermelon-white-1m-v3c",
    "watermelon-grey-5m-v3c",
    "watermelon-white-1m-v3d",
    "watermelon-grey-5m-v3d",
    "watermelon-white-1m-v4a",
    "watermelon-grey-5m-v4a",
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
    """Authoritative identity for a cross-league UEFA competition.

    Participating teams legitimately have different domestic ``league`` codes,
    so cup identity is anchored to numeric Gamma tag and series relations rather
    than pretending that the cup is a domestic sport row.
    """

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


@dataclass(frozen=True)
class DirectSportIdentity:
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


FROZEN_LEAGUE_IDENTITIES = (
    LeagueIdentity("epl", 2, "Premier League", 306, "10188", "premier-league-2025", "epl", (82, 306)),
    LeagueIdentity("bun", 7, "Bundesliga", 1494, "10194", "bundesliga-2025", "bun", (1494,)),
    LeagueIdentity("fl1", 11, "Ligue 1", 102070, "10195", "ligue-1-2025", "fl1", (102070,)),
    LeagueIdentity("lal", 3, "LaLiga", 780, "10193", "la-liga-2025", "lal", (780,)),
    LeagueIdentity("mls", 33, "MLS", 100100, "10189", "mls-2025", "mls", (100100,)),
    LeagueIdentity("sea", 12, "Serie A", 100618, "10203", "serie-a-2025", "sea", (101962,)),
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

FROZEN_DIRECT_SPORT_IDENTITIES = (
    DirectSportIdentity("mlb", 8, "MLB", MLB_TAG_ID, 3, "mlb"),
    DirectSportIdentity("nba", 34, "NBA", NBA_TAG_ID, 10345, "nba"),
    DirectSportIdentity("nfl", 10, "NFL", NFL_TAG_ID, 10187, "nfl"),
    DirectSportIdentity("nhl", 35, "NHL", NHL_TAG_ID, 10346, "nhl"),
)

# Compatibility name retained for the root contract verifier. Unlike the old
# dict, this freezes every authoritative Gamma identity field.
MAJOR_SOCCER_LEAGUES = FROZEN_LEAGUE_IDENTITIES


def league_registry_payload(
    identities: Sequence[LeagueIdentity] = FROZEN_LEAGUE_IDENTITIES,
    cup_identities: Sequence[CupIdentity] = FROZEN_CUP_IDENTITIES,
    direct_identities: Sequence[DirectSportIdentity] = FROZEN_DIRECT_SPORT_IDENTITIES,
) -> dict[str, Any]:
    return {
        "related_tags": False,
        "required_common_tag_ids": list(REQUIRED_COMMON_TAG_IDS),
        "soccer_tag_id": SOCCER_TAG_ID,
        "leagues": [identity.canonical_dict() for identity in identities],
        "uefa_competitions": [
            identity.canonical_dict() for identity in cup_identities
        ],
        "sport_family_tag_ids": {
            "soccer": SOCCER_TAG_ID,
            **{
                identity.code: identity.primary_tag_id
                for identity in direct_identities
            },
        },
        "direct_sports": [
            identity.canonical_dict() for identity in direct_identities
        ],
    }


def league_mapping_sha256(
    identities: Sequence[LeagueIdentity] = FROZEN_LEAGUE_IDENTITIES,
    cup_identities: Sequence[CupIdentity] = FROZEN_CUP_IDENTITIES,
    direct_identities: Sequence[DirectSportIdentity] = FROZEN_DIRECT_SPORT_IDENTITIES,
) -> str:
    payload = {
        "classifier_version": CLASSIFIER_VERSION,
        **league_registry_payload(identities, cup_identities, direct_identities),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


LEAGUE_MAPPING_SHA256 = league_mapping_sha256()

# Entry begins well after this source edit; first successful source receipt is
# provenance, not permission to backdate the preregistered statistical window.
FROZEN_START = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
FROZEN_ENTRY_END = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
FROZEN_FOLLOWUP_END = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
ENTRY_THRESHOLDS = (0.95, 0.96, 0.97, 0.98, 0.99)
STOP_LEVELS = (0.95, 0.93, 0.90, 0.85, 0.80, 0.70)
LATE_ENTRY_MINUTE_FLOORS = (75, 80, 85)
NOTIONAL_LADDER_USDC = (
    5.0, 10.0, 15.0, 20.0, 25.0, 30.0,
    40.0, 50.0, 75.0, 100.0, 150.0, 250.0, 500.0, 750.0, 1000.0,
)
NETWORK_BUDGET_SECONDS = 42.0
CYCLE_BUDGET_SECONDS = 50.0


@dataclass(frozen=True)
class JobProfile:
    cadence_arm: str
    cadence_minutes: int


JOB_PROFILES: dict[str, JobProfile] = {
    "watermelon-white-1m-v4b": JobProfile("FAST_1M", 1),
    "watermelon-grey-5m-v4b": JobProfile("CONTROL_5M", 5),
}

_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_SIGNATURE_TYPE", "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET", "POLYMARKET_API_PASSPHRASE",
        "CLOB_API_KEY", "CLOB_SECRET", "CLOB_PASSPHRASE",
    }
)
_ALLOWED_POLYBOT_ENV_KEYS = frozenset({"POLYBOT_LIFECYCLE_MODE", "POLYBOT_SIMULATION_MODE"})


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
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise ValueError(f"{name} must be a credential-free HTTPS origin")
    return text


def _public_websocket(value: Any, name: str) -> str:
    text = str(value).strip().rstrip("/")
    parsed = urlsplit(text)
    if (
        parsed.scheme != "wss" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.path != "/ws" or parsed.query or parsed.fragment
    ):
        raise ValueError(f"{name} must be a credential-free WSS /ws endpoint")
    return text


def _mapping(parent: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = parent.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"trading.{name} must be a mapping")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _float_tuple(value: Any, name: str) -> tuple[float, ...]:
    result = tuple(_finite(item, name) for item in _sequence(value, name))
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    result = tuple(str(item).strip() for item in _sequence(value, name))
    if not result or any(not item for item in result) or len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique non-empty values")
    return result


def _integer_tuple(value: Any, name: str) -> tuple[int, ...]:
    result = tuple(_integer(item, name) for item in _sequence(value, name))
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{name} must contain unique values")
    return result


def _league_identities(value: Any) -> tuple[LeagueIdentity, ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("gamma.league_mapping must be a non-empty mapping")
    identities: list[LeagueIdentity] = []
    allowed = {
        "sport_id", "name", "primary_tag_id", "series_id",
        "series_slug", "team_league", "required_tag_ids",
    }
    for raw_code, raw_identity in value.items():
        raw_code_text = str(raw_code).strip()
        code = raw_code_text.casefold()
        if raw_code_text != code:
            raise ValueError("gamma.league_mapping codes must use exact lowercase values")
        if not isinstance(raw_identity, Mapping):
            raise ValueError(f"gamma.league_mapping.{code} must be a mapping")
        unknown = set(raw_identity) - allowed
        if unknown:
            raise ValueError(
                f"unknown gamma.league_mapping.{code} keys: {sorted(map(str, unknown))}"
            )
        identity = LeagueIdentity(
            code=code,
            sport_id=_integer(raw_identity.get("sport_id"), f"league_mapping.{code}.sport_id"),
            name=str(raw_identity.get("name") or "").strip(),
            primary_tag_id=_integer(raw_identity.get("primary_tag_id"), f"league_mapping.{code}.primary_tag_id"),
            series_id=str(raw_identity.get("series_id") or "").strip(),
            series_slug=str(raw_identity.get("series_slug") or "").strip(),
            team_league=str(raw_identity.get("team_league") or "").strip(),
            required_tag_ids=_integer_tuple(raw_identity.get("required_tag_ids"), f"league_mapping.{code}.required_tag_ids"),
        )
        if not all((identity.name, identity.series_id, identity.series_slug, identity.team_league)):
            raise ValueError(f"gamma.league_mapping.{code} strings cannot be empty")
        if identity.team_league != identity.team_league.casefold():
            raise ValueError(f"gamma.league_mapping.{code}.team_league must be exact lowercase")
        identities.append(identity)
    return tuple(identities)


def _cup_identities(value: Any) -> tuple[CupIdentity, ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("gamma.cup_mapping must be a non-empty mapping")
    identities: list[CupIdentity] = []
    allowed = {
        "name", "tag_id", "series_id", "series_slug",
        "event_slug_prefix", "resolution_source_host",
    }
    for raw_code, raw_identity in value.items():
        raw_code_text = str(raw_code).strip()
        code = raw_code_text.casefold()
        if raw_code_text != code:
            raise ValueError("gamma.cup_mapping codes must use exact lowercase values")
        if not isinstance(raw_identity, Mapping):
            raise ValueError(f"gamma.cup_mapping.{code} must be a mapping")
        unknown = set(raw_identity) - allowed
        if unknown:
            raise ValueError(
                f"unknown gamma.cup_mapping.{code} keys: "
                f"{sorted(map(str, unknown))}"
            )
        identity = CupIdentity(
            code=code,
            name=str(raw_identity.get("name") or "").strip(),
            tag_id=_integer(raw_identity.get("tag_id"), f"cup_mapping.{code}.tag_id"),
            series_id=str(raw_identity.get("series_id") or "").strip(),
            series_slug=str(raw_identity.get("series_slug") or "").strip(),
            event_slug_prefix=str(
                raw_identity.get("event_slug_prefix") or ""
            ).strip(),
            resolution_source_host=str(
                raw_identity.get("resolution_source_host") or ""
            ).strip().casefold(),
        )
        if not all(
            (
                identity.name, identity.series_id, identity.series_slug,
                identity.event_slug_prefix, identity.resolution_source_host,
            )
        ):
            raise ValueError(f"gamma.cup_mapping.{code} strings cannot be empty")
        if identity.event_slug_prefix != f"{code}-":
            raise ValueError(
                f"gamma.cup_mapping.{code}.event_slug_prefix must be {code}-"
            )
        identities.append(identity)
    return tuple(identities)


def _direct_sport_identities(value: Any) -> tuple[DirectSportIdentity, ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("gamma.direct_sport_mapping must be a non-empty mapping")
    identities: list[DirectSportIdentity] = []
    allowed = {
        "sport_id", "name", "primary_tag_id", "root_series_id", "team_league"
    }
    for raw_code, raw_identity in value.items():
        code = str(raw_code).strip().casefold()
        if not code or not isinstance(raw_identity, Mapping):
            raise ValueError("gamma.direct_sport_mapping entries must be mappings")
        unknown = set(raw_identity) - allowed
        if unknown:
            raise ValueError(
                f"gamma.direct_sport_mapping.{code} has unknown keys: "
                + ", ".join(sorted(unknown))
            )
        identities.append(
            DirectSportIdentity(
                code=code,
                sport_id=_integer(raw_identity.get("sport_id"), f"direct.{code}.sport_id"),
                name=str(raw_identity.get("name") or "").strip(),
                primary_tag_id=_integer(
                    raw_identity.get("primary_tag_id"),
                    f"direct.{code}.primary_tag_id",
                ),
                root_series_id=_integer(
                    raw_identity.get("root_series_id"),
                    f"direct.{code}.root_series_id",
                ),
                team_league=str(raw_identity.get("team_league") or "").strip(),
            )
        )
    return tuple(identities)


@dataclass(frozen=True)
class GammaConfig:
    base_url: str
    page_size: int
    max_pages: int
    tag_id: int
    related_tags: bool
    live_only: bool
    sport_family: str
    sport_families: tuple[str, ...]
    family_tag_ids: tuple[tuple[str, int], ...]
    required_common_tag_ids: tuple[int, ...]
    league_mapping: tuple[LeagueIdentity, ...]
    cup_mapping: tuple[CupIdentity, ...]
    direct_sport_mapping: tuple[DirectSportIdentity, ...]
    sports_market_types: tuple[str, ...]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    retry_max_seconds: float

    @property
    def league_codes(self) -> tuple[str, ...]:
        return tuple(identity.code for identity in self.league_mapping)

    @property
    def identities_by_code(self) -> dict[str, LeagueIdentity]:
        return {identity.code: identity for identity in self.league_mapping}

    @property
    def cups_by_code(self) -> dict[str, CupIdentity]:
        return {identity.code: identity for identity in self.cup_mapping}

    @property
    def competition_codes(self) -> tuple[str, ...]:
        return (
            *self.league_codes,
            *(identity.code for identity in self.cup_mapping),
            *(identity.code for identity in self.direct_sport_mapping),
        )

    @property
    def family_tags(self) -> dict[str, int]:
        return dict(self.family_tag_ids)

    @property
    def direct_identities_by_code(self) -> dict[str, DirectSportIdentity]:
        return {identity.code: identity for identity in self.direct_sport_mapping}


@dataclass(frozen=True)
class OrderBookConfig:
    base_url: str
    batch_token_limit: int


@dataclass(frozen=True)
class SportsFeedConfig:
    websocket_url: str
    connect_timeout_seconds: float
    receive_window_seconds: float
    max_messages: int


@dataclass(frozen=True)
class ExperimentConfig:
    start_utc: datetime
    entry_end_utc: datetime
    followup_end_utc: datetime
    entry_thresholds: tuple[float, ...]
    stop_levels: tuple[float, ...]
    simulated_notional_usdc: float
    notional_ladder_usdc: tuple[float, ...]
    late_entry_minute_floors: tuple[int, ...]
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
    schema_profile: str
    universe_profile: str
    classifier_version: str
    league_mapping_sha256: str
    cadence_arm: str
    cadence_minutes: int
    gamma: GammaConfig
    orderbook: OrderBookConfig
    sports_feed: SportsFeedConfig
    experiment: ExperimentConfig
    storage: StorageConfig
    strategy_source_digest: str
    network_budget_seconds: float
    cycle_budget_seconds: float


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
    if (
        trading.network_budget_seconds != NETWORK_BUDGET_SECONDS
        or trading.cycle_budget_seconds != CYCLE_BUDGET_SECONDS
    ):
        raise ValueError(
            "simulation runtime budget is frozen at 42s network / 50s cycle"
        )
    exact_metadata = (
        trading.data_contract == DATA_CONTRACT
        and trading.schema_profile == SCHEMA_PROFILE
        and trading.universe_profile == UNIVERSE_PROFILE
        and trading.classifier_version == CLASSIFIER_VERSION
        and trading.league_mapping_sha256 == LEAGUE_MAPPING_SHA256
    )
    if not exact_metadata:
        raise ValueError("data/schema/universe/classifier mapping contract differs")
    if (trading.cadence_arm, trading.cadence_minutes) != (profile.cadence_arm, profile.cadence_minutes):
        raise ValueError("cadence treatment must match the frozen job profile")
    gamma = trading.gamma
    if gamma.base_url != "https://gamma-api.polymarket.com":
        raise ValueError("Gamma origin is frozen")
    if gamma.page_size != 500 or not 1 <= gamma.max_pages <= 4:
        raise ValueError("Gamma event keyset envelope must remain 500 × at most 4 pages")
    if gamma.tag_id != SOCCER_TAG_ID or gamma.related_tags is not False or gamma.live_only is not True:
        raise ValueError("Gamma canonical tag must remain the exact numeric soccer tag")
    if gamma.sport_family != "soccer":
        raise ValueError("canonical sport_family must remain soccer")
    if gamma.sport_families != SPORT_FAMILIES:
        raise ValueError("sport_families must remain soccer, mlb, nba, nfl, nhl")
    if gamma.family_tag_ids != SPORT_FAMILY_TAG_IDS:
        raise ValueError("family numeric Gamma tag IDs differ")
    if gamma.required_common_tag_ids != REQUIRED_COMMON_TAG_IDS:
        raise ValueError("required common sport/games/soccer tag IDs differ")
    if gamma.league_mapping != FROZEN_LEAGUE_IDENTITIES:
        raise ValueError("league mapping differs from the frozen authoritative tuple")
    if gamma.cup_mapping != FROZEN_CUP_IDENTITIES:
        raise ValueError("UEFA cup mapping differs from the frozen authoritative tuple")
    if gamma.direct_sport_mapping != FROZEN_DIRECT_SPORT_IDENTITIES:
        raise ValueError(
            "MLB/NBA/NFL/NHL direct sport mapping differs from frozen identity"
        )
    if gamma.sports_market_types != ("moneyline",):
        raise ValueError("only top-level moneyline is permitted")
    if not 0 <= gamma.max_retries <= 10:
        raise ValueError("Gamma retry budget is invalid")
    if trading.orderbook.base_url != "https://clob.polymarket.com":
        raise ValueError("CLOB origin is frozen")
    if not 1 <= trading.orderbook.batch_token_limit <= 500:
        raise ValueError("orderbook batch size is invalid")
    sports_feed = trading.sports_feed
    if sports_feed.websocket_url != "wss://sports-api.polymarket.com/ws":
        raise ValueError("sports clock websocket origin is frozen")
    if not 0 < sports_feed.connect_timeout_seconds <= 10:
        raise ValueError("sports clock connect timeout is invalid")
    if not 5 <= sports_feed.receive_window_seconds <= 20:
        raise ValueError("sports clock receive window must remain bounded")
    if not 100 <= sports_feed.max_messages <= 20_000:
        raise ValueError("sports clock message cap is invalid")
    experiment = trading.experiment
    if (experiment.start_utc, experiment.entry_end_utc, experiment.followup_end_utc) != (
        FROZEN_START, FROZEN_ENTRY_END, FROZEN_FOLLOWUP_END,
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
    if experiment.notional_ladder_usdc != NOTIONAL_LADDER_USDC:
        raise ValueError("counterfactual notional ladder differs from preregistration")
    if experiment.late_entry_minute_floors != LATE_ENTRY_MINUTE_FLOORS:
        raise ValueError("late-entry replay minute floors differ from preregistration")
    if tuple(sorted(experiment.notional_ladder_usdc)) != experiment.notional_ladder_usdc:
        raise ValueError("counterfactual notional ladder must increase strictly")
    if experiment.notional_ladder_usdc[0] != experiment.simulated_notional_usdc:
        raise ValueError("notional ladder must begin at the live pilot's $5 floor")
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
    verify_frozen_manifest()
    unknown = sorted(
        key for key in os.environ
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
    sports_feed_raw = _mapping(trading_raw, "sports_feed")
    experiment_raw = _mapping(trading_raw, "experiment")
    storage_raw = _mapping(trading_raw, "storage")
    gamma = GammaConfig(
        base_url=_public_origin(gamma_raw["base_url"], "gamma.base_url"),
        page_size=_integer(gamma_raw["page_size"], "gamma.page_size"),
        max_pages=_integer(gamma_raw["max_pages"], "gamma.max_pages"),
        tag_id=_integer(gamma_raw["tag_id"], "gamma.tag_id"),
        related_tags=_boolean(gamma_raw["related_tags"], "gamma.related_tags"),
        live_only=_boolean(gamma_raw["live_only"], "gamma.live_only"),
        sport_family=str(gamma_raw["sport_family"]).strip().casefold(),
        sport_families=_string_tuple(
            gamma_raw["sport_families"], "gamma.sport_families"
        ),
        family_tag_ids=tuple(
            (
                str(family).strip().casefold(),
                _integer(tag_id, f"gamma.family_tag_ids.{family}"),
            )
            for family, tag_id in _mapping(
                gamma_raw, "family_tag_ids"
            ).items()
        ),
        required_common_tag_ids=_integer_tuple(gamma_raw["required_common_tag_ids"], "gamma.required_common_tag_ids"),
        league_mapping=_league_identities(gamma_raw["league_mapping"]),
        cup_mapping=_cup_identities(gamma_raw["cup_mapping"]),
        direct_sport_mapping=_direct_sport_identities(
            gamma_raw["direct_sport_mapping"]
        ),
        sports_market_types=_string_tuple(gamma_raw["sports_market_types"], "gamma.sports_market_types"),
        connect_timeout_seconds=_finite(gamma_raw["connect_timeout_seconds"], "gamma.connect_timeout_seconds"),
        read_timeout_seconds=_finite(gamma_raw["read_timeout_seconds"], "gamma.read_timeout_seconds"),
        max_retries=_integer(gamma_raw["max_retries"], "gamma.max_retries"),
        retry_base_seconds=_finite(gamma_raw["retry_base_seconds"], "gamma.retry_base_seconds"),
        retry_max_seconds=_finite(gamma_raw["retry_max_seconds"], "gamma.retry_max_seconds"),
    )
    orderbook = OrderBookConfig(
        base_url=_public_origin(orderbook_raw["base_url"], "orderbook.base_url"),
        batch_token_limit=_integer(orderbook_raw["batch_token_limit"], "orderbook.batch_token_limit"),
    )
    sports_feed = SportsFeedConfig(
        websocket_url=_public_websocket(
            sports_feed_raw["websocket_url"], "sports_feed.websocket_url"
        ),
        connect_timeout_seconds=_finite(
            sports_feed_raw["connect_timeout_seconds"],
            "sports_feed.connect_timeout_seconds",
        ),
        receive_window_seconds=_finite(
            sports_feed_raw["receive_window_seconds"],
            "sports_feed.receive_window_seconds",
        ),
        max_messages=_integer(
            sports_feed_raw["max_messages"], "sports_feed.max_messages"
        ),
    )
    experiment = ExperimentConfig(
        start_utc=_utc(experiment_raw["start_utc"], "experiment.start_utc"),
        entry_end_utc=_utc(experiment_raw["entry_end_utc"], "experiment.entry_end_utc"),
        followup_end_utc=_utc(experiment_raw["followup_end_utc"], "experiment.followup_end_utc"),
        entry_thresholds=_float_tuple(experiment_raw["entry_thresholds"], "experiment.entry_thresholds"),
        stop_levels=_float_tuple(experiment_raw["stop_levels"], "experiment.stop_levels"),
        simulated_notional_usdc=_finite(experiment_raw["simulated_notional_usdc"], "experiment.simulated_notional_usdc"),
        notional_ladder_usdc=_float_tuple(
            experiment_raw["notional_ladder_usdc"],
            "experiment.notional_ladder_usdc",
        ),
        late_entry_minute_floors=_integer_tuple(
            experiment_raw["late_entry_minute_floors"],
            "experiment.late_entry_minute_floors",
        ),
        fee_rate_fallback=_finite(experiment_raw["fee_rate_fallback"], "experiment.fee_rate_fallback"),
        preregistration_sha256=preregistration_sha256(),
    )
    storage = StorageConfig(
        busy_timeout_ms=_integer(storage_raw["busy_timeout_ms"], "storage.busy_timeout_ms"),
        min_free_gib=_finite(storage_raw["min_free_gib"], "storage.min_free_gib"),
        stop_used_ratio=_finite(storage_raw["stop_used_ratio"], "storage.stop_used_ratio"),
        bot_log_retention_days=_integer(storage_raw["bot_log_retention_days"], "storage.bot_log_retention_days"),
    )
    trading = TradingConfig(
        lifecycle_mode=str(os.environ.get("POLYBOT_LIFECYCLE_MODE", trading_raw.get("lifecycle_mode", ""))).strip(),
        data_contract=str(trading_raw.get("data_contract", "")).strip(),
        schema_profile=str(trading_raw.get("schema_profile", "")).strip(),
        universe_profile=str(trading_raw.get("universe_profile", "")).strip(),
        classifier_version=str(trading_raw.get("classifier_version", "")).strip(),
        league_mapping_sha256=league_mapping_sha256(
            gamma.league_mapping,
            gamma.cup_mapping,
            gamma.direct_sport_mapping,
        ),
        cadence_arm=profile.cadence_arm,
        cadence_minutes=profile.cadence_minutes,
        gamma=gamma,
        orderbook=orderbook,
        sports_feed=sports_feed,
        experiment=experiment,
        storage=storage,
        strategy_source_digest=compute_strategy_source_digest(),
        network_budget_seconds=_finite(
            trading_raw.get("network_budget_seconds"),
            "trading.network_budget_seconds",
        ),
        cycle_budget_seconds=_finite(
            trading_raw.get("cycle_budget_seconds"),
            "trading.cycle_budget_seconds",
        ),
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
