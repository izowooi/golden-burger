"""Resolved, fail-closed configuration for Golden Kiwi / Micro-Cascade."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import List, Optional, Union

from dotenv import load_dotenv
from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)
import yaml


LIFECYCLE_MODES = frozenset({"active", "close_only", "archive_only"})
ALLOWED_CONFIRMATION_STEPS = frozenset({3, 5})
ALLOWED_MIN_CUMULATIVE_MOVES = (0.01, 0.02)
REQUIRED_EXCLUDED_CATEGORIES = (
    "sports",
    "games",
    "esports",
    "crypto-prices",
    "up-or-down",
    "multi-strikes",
    "5m",
    "15m",
    "1h",
)
DEFAULT_BUY_AMOUNT_USDC = 5.0
LIVE_EXECUTION_ENABLED = False
EXPERIMENT_SCHEMA_VERSION = 1
ANALYZER_SCHEMA_VERSION = 2
PREREGISTRATION_SHA256 = (
    "0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006"
)
EXPERIMENT_WINDOW_DAYS = 30
EXPECTED_CADENCE_MINUTES = 5
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_JOB_ARMS = {
    "kiwi-sim-a-3x1": (3, 0.01, "A"),
    "kiwi-sim-b-3x2": (3, 0.02, "B"),
    "kiwi-sim-c-5x1": (5, 0.01, "C"),
    "kiwi-sim-d-5x2": (5, 0.02, "D"),
}
CANONICAL_JOB_OFFSETS = {
    "kiwi-sim-a-3x1": 0,
    "kiwi-sim-b-3x2": 1,
    "kiwi-sim-c-5x1": 2,
    "kiwi-sim-d-5x2": 3,
}


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


@dataclass(frozen=True)
class MicroCascadeEntryConfig:
    """Frozen 2x2 staircase-confirmation treatment.

    Only ``confirmation_steps`` and ``min_cumulative_move`` differ between
    arms.  All remaining values are immutable safety or universe controls.
    """

    confirmation_steps: int = 3
    min_cumulative_move: float = 0.02
    min_step_move: float = 0.0
    max_step_move: float = 0.02
    max_cumulative_move: float = 0.04
    min_snapshot_gap_minutes: float = 3.0
    max_snapshot_gap_minutes: float = 10.0
    prob_min: float = 0.20
    prob_max: float = 0.80
    min_hours_to_resolution: float = 6.0
    hold_minutes: float = 60.0
    max_exit_delay_minutes: float = 15.0


EntryConfig = MicroCascadeEntryConfig


@dataclass(frozen=True)
class ArchiveConfig:
    """Buffered research-universe bounds needed for five-step lineage."""

    prob_min: float = 0.16
    prob_max: float = 0.84
    retention_days: int = 60


@dataclass
class TradingConfig:
    """Micro-Cascade trading, experiment, and evidence configuration."""

    lifecycle_mode: str = "active"
    max_drawdown_stop: float = 0.20
    experiment_capital_usdc: float = 100.0
    buy_amount_usdc: float = DEFAULT_BUY_AMOUNT_USDC
    max_buy_amount_usdc: float = 5.0
    min_liquidity: float = 20_000.0
    min_volume_24h: float = 10_000.0
    max_positions: int = 3
    max_event_positions: int = 1
    max_open_notional_usdc: float = 15.0
    max_new_positions_per_cycle: int = 1
    reentry_cooldown_hours: float = 6.0
    min_order_size: float = 5.0
    min_order_buffer_shares: float = 0.10
    max_spread: float = 0.02
    depth_price_window: float = 0.01
    depth_safety_multiple: float = 1.20
    yes_only_mode: bool = True
    entry: MicroCascadeEntryConfig = field(default_factory=MicroCascadeEntryConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    excluded_categories: List[str] = field(
        default_factory=lambda: list(REQUIRED_EXCLUDED_CATEGORIES)
    )

    @property
    def strategy(self) -> MicroCascadeEntryConfig:
        return self.entry

    @property
    def effective_min_liquidity(self) -> float:
        return self.min_liquidity

    @property
    def effective_min_volume_24h(self) -> float:
        return self.min_volume_24h

    @property
    def max_snapshot_gap_minutes(self) -> float:
        """Compatibility alias consumed by SQLite cadence maintenance."""
        return self.entry.max_snapshot_gap_minutes

    @property
    def arm_name(self) -> str:
        mapping = {
            (3, 0.01): "A",
            (3, 0.02): "B",
            (5, 0.01): "C",
            (5, 0.02): "D",
        }
        for (steps, move), arm in mapping.items():
            if self.entry.confirmation_steps == steps and math.isclose(
                self.entry.min_cumulative_move, move, rel_tol=0, abs_tol=1e-12
            ):
                return arm
        raise ValueError("entry settings do not map to a frozen Micro-Cascade arm")


@dataclass
class ApiConfig:
    private_key: str
    funder_address: str
    signature_type: int = 1
    chain_id: int = 137


@dataclass(frozen=True)
class ExperimentCollectionConfig:
    """Explicit shared window; absent values mean non-promotion smoke mode."""

    enabled: bool = False
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    expected_cadence_minutes: int = EXPECTED_CADENCE_MINUTES
    expected_offset_minute: Optional[int] = None
    preregistration_sha256: str = PREREGISTRATION_SHA256
    analyzer_version: int = ANALYZER_SCHEMA_VERSION

    def contains(self, value: datetime) -> bool:
        if (
            not self.enabled
            or self.window_start is None
            or self.window_end is None
        ):
            return False
        normalized = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return self.window_start <= normalized < self.window_end


@dataclass
class BotConfig:
    trading: TradingConfig
    api: ApiConfig
    db_path: Path
    simulation_mode: bool = True
    job_name: str = "default"
    experiment: ExperimentCollectionConfig = field(
        default_factory=ExperimentCollectionConfig
    )


def _same_float(value: float, expected: float) -> bool:
    return math.isclose(value, expected, rel_tol=0, abs_tol=1e-12)


def _validate_config(trading: TradingConfig, api: ApiConfig) -> None:
    entry = trading.entry
    archive = trading.archive
    numeric = {
        "max_drawdown_stop": trading.max_drawdown_stop,
        "experiment_capital_usdc": trading.experiment_capital_usdc,
        "buy_amount_usdc": trading.buy_amount_usdc,
        "max_buy_amount_usdc": trading.max_buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "min_volume_24h": trading.min_volume_24h,
        "max_positions": trading.max_positions,
        "max_event_positions": trading.max_event_positions,
        "max_open_notional_usdc": trading.max_open_notional_usdc,
        "max_new_positions_per_cycle": trading.max_new_positions_per_cycle,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "min_order_size": trading.min_order_size,
        "min_order_buffer_shares": trading.min_order_buffer_shares,
        "max_spread": trading.max_spread,
        "depth_price_window": trading.depth_price_window,
        "depth_safety_multiple": trading.depth_safety_multiple,
        "entry.confirmation_steps": entry.confirmation_steps,
        "entry.min_cumulative_move": entry.min_cumulative_move,
        "entry.min_step_move": entry.min_step_move,
        "entry.max_step_move": entry.max_step_move,
        "entry.max_cumulative_move": entry.max_cumulative_move,
        "entry.min_snapshot_gap_minutes": entry.min_snapshot_gap_minutes,
        "entry.max_snapshot_gap_minutes": entry.max_snapshot_gap_minutes,
        "entry.prob_min": entry.prob_min,
        "entry.prob_max": entry.prob_max,
        "entry.min_hours_to_resolution": entry.min_hours_to_resolution,
        "entry.hold_minutes": entry.hold_minutes,
        "entry.max_exit_delay_minutes": entry.max_exit_delay_minutes,
        "archive.prob_min": archive.prob_min,
        "archive.prob_max": archive.prob_max,
        "archive.retention_days": archive.retention_days,
    }
    for name, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")

    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError(
            "lifecycle_mode must be one of: active, close_only, archive_only"
        )
    if entry.confirmation_steps not in ALLOWED_CONFIRMATION_STEPS:
        raise ValueError("confirmation_steps must be one of the frozen values: 3, 5")
    if not any(
        _same_float(entry.min_cumulative_move, value)
        for value in ALLOWED_MIN_CUMULATIVE_MOVES
    ):
        raise ValueError(
            "min_cumulative_move must be one of the frozen values: 0.01, 0.02"
        )
    frozen_values = {
        "entry.min_step_move": (entry.min_step_move, 0.0),
        "entry.max_step_move": (entry.max_step_move, 0.02),
        "entry.max_cumulative_move": (entry.max_cumulative_move, 0.04),
        "entry.min_snapshot_gap_minutes": (
            entry.min_snapshot_gap_minutes,
            3.0,
        ),
        "entry.max_snapshot_gap_minutes": (
            entry.max_snapshot_gap_minutes,
            10.0,
        ),
        "entry.prob_min": (entry.prob_min, 0.20),
        "entry.prob_max": (entry.prob_max, 0.80),
        "entry.min_hours_to_resolution": (
            entry.min_hours_to_resolution,
            6.0,
        ),
        "entry.hold_minutes": (entry.hold_minutes, 60.0),
        "entry.max_exit_delay_minutes": (
            entry.max_exit_delay_minutes,
            15.0,
        ),
        "archive.prob_min": (archive.prob_min, 0.16),
        "archive.prob_max": (archive.prob_max, 0.84),
    }
    for name, (actual, expected) in frozen_values.items():
        if not _same_float(actual, expected):
            raise ValueError(
                f"{name} is frozen at {expected}; changing the experiment "
                "requires a reviewed code change"
            )
    if not 0 <= entry.min_step_move <= entry.max_step_move:
        raise ValueError("step moves must satisfy 0 <= min_step_move <= max_step_move")
    if not (
        entry.min_cumulative_move
        <= entry.max_cumulative_move
        <= entry.confirmation_steps * entry.max_step_move + 1e-12
    ):
        raise ValueError("cumulative move bounds are inconsistent")
    if entry.min_snapshot_gap_minutes > entry.max_snapshot_gap_minutes:
        raise ValueError("snapshot gap bounds are inconsistent")
    if not 0 < archive.prob_min < entry.prob_min < entry.prob_max < archive.prob_max < 1:
        raise ValueError("archive probability buffer must cover the entry band")
    if archive.retention_days != 60:
        raise ValueError("archive.retention_days is frozen at 60")

    fixed_controls = {
        "max_drawdown_stop": (trading.max_drawdown_stop, 0.20),
        "experiment_capital_usdc": (trading.experiment_capital_usdc, 100.0),
        "buy_amount_usdc": (trading.buy_amount_usdc, 5.0),
        "max_buy_amount_usdc": (trading.max_buy_amount_usdc, 5.0),
        "min_liquidity": (trading.min_liquidity, 20_000.0),
        "min_volume_24h": (trading.min_volume_24h, 10_000.0),
        "max_open_notional_usdc": (trading.max_open_notional_usdc, 15.0),
        "reentry_cooldown_hours": (trading.reentry_cooldown_hours, 6.0),
        "min_order_size": (trading.min_order_size, 5.0),
        "min_order_buffer_shares": (
            trading.min_order_buffer_shares,
            0.10,
        ),
        "max_spread": (trading.max_spread, 0.02),
        "depth_price_window": (trading.depth_price_window, 0.01),
        "depth_safety_multiple": (trading.depth_safety_multiple, 1.20),
    }
    for name, (actual, expected) in fixed_controls.items():
        if not _same_float(actual, expected):
            raise ValueError(
                f"{name} is frozen at {expected}; only the registered arm axes "
                "may vary"
            )
    if trading.max_positions != 3:
        raise ValueError("max_positions is frozen at 3")
    if trading.max_event_positions != 1:
        raise ValueError("max_event_positions is frozen at 1")
    if trading.max_new_positions_per_cycle != 1:
        raise ValueError("max_new_positions_per_cycle is frozen at 1")
    if trading.min_order_size <= 0 or trading.min_order_buffer_shares < 0:
        raise ValueError("minimum order size/buffer must be non-negative")
    if not 0 < trading.depth_price_window < 1:
        raise ValueError("depth_price_window must be in (0, 1)")
    if trading.depth_safety_multiple < 1:
        raise ValueError("depth_safety_multiple must be >= 1")
    if not trading.yes_only_mode:
        raise ValueError("Micro-Cascade inherently requires yes_only_mode=true")
    if not 0 < trading.max_drawdown_stop <= 1:
        raise ValueError("max_drawdown_stop must be in (0, 1]")
    if trading.experiment_capital_usdc <= 0:
        raise ValueError("experiment_capital_usdc must be > 0")
    if trading.buy_amount_usdc > trading.max_buy_amount_usdc:
        raise ValueError("buy_amount_usdc must be <= max_buy_amount_usdc")
    smallest_order = trading.buy_amount_usdc / entry.prob_max
    if smallest_order + 1e-9 < (
        trading.min_order_size + trading.min_order_buffer_shares
    ):
        raise ValueError("buy amount cannot satisfy the minimum-share buffer")

    normalized_exclusions = tuple(
        item.strip().lower() for item in trading.excluded_categories
    )
    if not isinstance(trading.excluded_categories, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in trading.excluded_categories
    ):
        raise ValueError("excluded_categories must be a list of non-empty strings")
    if normalized_exclusions != REQUIRED_EXCLUDED_CATEGORIES:
        raise ValueError(
            "excluded_categories is frozen to the exact Micro-Cascade universe"
        )
    if api.signature_type not in {1, 3}:
        raise ValueError("signature_type must be one of: 1, 3")


def _validate_canonical_job_arm(job_name: str, trading: TradingConfig) -> None:
    expected = CANONICAL_JOB_ARMS.get(job_name)
    if expected is None:
        allowed = ", ".join(CANONICAL_JOB_ARMS)
        raise ValueError(
            "Golden Kiwi는 실험 오염 방지를 위해 canonical job만 허용합니다: "
            f"{allowed}"
        )
    expected_steps, expected_move, expected_arm = expected
    if (
        trading.entry.confirmation_steps != expected_steps
        or not _same_float(trading.entry.min_cumulative_move, expected_move)
        or trading.arm_name != expected_arm
    ):
        raise ValueError(
            f"{job_name}은 arm {expected_arm}="
            f"({expected_steps}, {expected_move}) 전용입니다; "
            "job과 treatment 환경변수를 함께 맞추세요"
        )


def _validate_existing_db_arm(
    db_path: Path,
    job_name: str,
    trading: TradingConfig,
) -> None:
    """Reject a DB that already contains another treatment arm."""
    if not db_path.exists():
        return
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                SELECT DISTINCT configs.config_json
                FROM run_audits AS runs
                JOIN strategy_configs AS configs
                  ON configs.config_hash = runs.config_hash
                WHERE runs.strategy_name = 'golden-kiwi'
                  AND runs.job_name = ?
                  AND runs.status IN ('RUNNING', 'SUCCESS')
                """,
                (job_name,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        # A brand-new or pre-observability DB has no cohort evidence to compare.
        return
    for (raw_config,) in rows:
        try:
            payload = json.loads(raw_config)
            entry = payload["trading"]["entry"]
            steps = int(entry["confirmation_steps"])
            move = float(entry["min_cumulative_move"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"{db_path}의 기존 Golden Kiwi config evidence를 해석할 수 없습니다"
            ) from error
        if (
            steps != trading.entry.confirmation_steps
            or not _same_float(move, trading.entry.min_cumulative_move)
        ):
            raise ValueError(
                f"{db_path}에 다른 arm cohort ({steps}, {move})가 이미 있어 "
                "동일 DB 사용을 거부합니다"
            )


def _parse_collection_timestamp(env_key: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{env_key} must be an ISO-8601 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{env_key} must include the UTC offset/Z")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.second or parsed.microsecond:
        raise ValueError(f"{env_key} must be aligned to an exact minute")
    return parsed


def _load_experiment_collection(job_name: str) -> ExperimentCollectionConfig:
    keys = (
        "POLYBOT_EXPERIMENT_START_UTC",
        "POLYBOT_EXPERIMENT_END_UTC",
        "POLYBOT_CADENCE_OFFSET_MINUTE",
    )
    raw = {key: os.getenv(key) for key in keys}
    present = {key for key, value in raw.items() if value not in (None, "")}
    if not present:
        return ExperimentCollectionConfig()
    if present != set(keys):
        missing = ", ".join(sorted(set(keys) - present))
        raise ValueError(
            "promotion collection env는 셋 모두 필요합니다; missing="
            f"{missing}"
        )
    start = _parse_collection_timestamp(keys[0], str(raw[keys[0]]))
    end = _parse_collection_timestamp(keys[1], str(raw[keys[1]]))
    if end - start != timedelta(days=EXPERIMENT_WINDOW_DAYS):
        raise ValueError("experiment UTC window must be exactly 30 days")
    try:
        offset = int(str(raw[keys[2]]))
    except (TypeError, ValueError) as error:
        raise ValueError("POLYBOT_CADENCE_OFFSET_MINUTE must be an integer") from error
    if not 0 <= offset < EXPECTED_CADENCE_MINUTES:
        raise ValueError(
            "POLYBOT_CADENCE_OFFSET_MINUTE must be one of 0, 1, 2, 3, 4"
        )
    expected_offset = CANONICAL_JOB_OFFSETS.get(job_name)
    if expected_offset is None:
        raise ValueError(
            "promotion collection에는 canonical Golden Kiwi job이 필요합니다"
        )
    if offset != expected_offset:
        raise ValueError(
            f"{job_name}의 POLYBOT_CADENCE_OFFSET_MINUTE는 "
            f"{expected_offset}으로 고정됩니다"
        )
    return ExperimentCollectionConfig(
        enabled=True,
        window_start=start,
        window_end=end,
        expected_offset_minute=offset,
    )


def load_config(
    config_path: str = "config.yaml",
    job_name: str = "default",
    env_path: Optional[str] = None,
    simulation_mode: Optional[bool] = None,
    yes_only_mode: Optional[bool] = None,
) -> BotConfig:
    """Load one frozen arm and reject every live-mode request.

    OOS promotion gates failed.  There is intentionally no environment variable
    capable of enabling live execution; a reviewed source change is required.
    """
    load_dotenv(env_path) if env_path else load_dotenv()

    project_root = PROJECT_ROOT
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
    else:
        cfg = {}
    requested_simulation = (
        cfg.get("simulation_mode", True)
        if simulation_mode is None
        else simulation_mode
    )
    if not isinstance(requested_simulation, bool):
        raise ValueError("simulation_mode must be a boolean")
    if not requested_simulation:
        raise ValueError(
            "Golden Kiwi is research/simulation-only because its frozen OOS "
            "promotion gate failed; live mode requires a reviewed code change"
        )
    trading_cfg = get_trading_config_mapping(cfg)
    entry_cfg = trading_cfg.get("entry", {})
    archive_cfg = trading_cfg.get("archive", {})
    if not isinstance(entry_cfg, dict) or not isinstance(archive_cfg, dict):
        raise ValueError("trading.entry and trading.archive must be mappings")

    entry = MicroCascadeEntryConfig(
        confirmation_steps=_get_config_value(
            "POLYBOT_CONFIRMATION_STEPS",
            entry_cfg.get("confirmation_steps"),
            3,
            int,
        ),
        min_cumulative_move=_get_config_value(
            "POLYBOT_MIN_CUMULATIVE_MOVE",
            entry_cfg.get("min_cumulative_move"),
            0.02,
        ),
        min_step_move=_get_config_value(
            "POLYBOT_MIN_STEP_MOVE", entry_cfg.get("min_step_move"), 0.0
        ),
        max_step_move=_get_config_value(
            "POLYBOT_MAX_STEP_MOVE", entry_cfg.get("max_step_move"), 0.02
        ),
        max_cumulative_move=_get_config_value(
            "POLYBOT_MAX_CUMULATIVE_MOVE",
            entry_cfg.get("max_cumulative_move"),
            0.04,
        ),
        min_snapshot_gap_minutes=_get_config_value(
            "POLYBOT_MIN_SNAPSHOT_GAP_MINUTES",
            entry_cfg.get("min_snapshot_gap_minutes"),
            3.0,
        ),
        max_snapshot_gap_minutes=_get_config_value(
            "POLYBOT_MAX_SNAPSHOT_GAP_MINUTES",
            entry_cfg.get("max_snapshot_gap_minutes"),
            10.0,
        ),
        prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN", entry_cfg.get("prob_min"), 0.20
        ),
        prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX", entry_cfg.get("prob_max"), 0.80
        ),
        min_hours_to_resolution=_get_config_value(
            "POLYBOT_MIN_HOURS_TO_RESOLUTION",
            entry_cfg.get("min_hours_to_resolution"),
            6.0,
        ),
        hold_minutes=_get_config_value(
            "POLYBOT_HOLD_MINUTES", entry_cfg.get("hold_minutes"), 60.0
        ),
        max_exit_delay_minutes=_get_config_value(
            "POLYBOT_MAX_EXIT_DELAY_MINUTES",
            entry_cfg.get("max_exit_delay_minutes"),
            15.0,
        ),
    )
    archive = ArchiveConfig(
        prob_min=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MIN", archive_cfg.get("prob_min"), 0.16
        ),
        prob_max=_get_config_value(
            "POLYBOT_ARCHIVE_PROB_MAX", archive_cfg.get("prob_max"), 0.84
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
        max_drawdown_stop=_get_config_value(
            "POLYBOT_MAX_DRAWDOWN_STOP",
            trading_cfg.get("max_drawdown_stop"),
            0.20,
        ),
        experiment_capital_usdc=_get_config_value(
            "POLYBOT_EXPERIMENT_CAPITAL_USDC",
            trading_cfg.get("experiment_capital_usdc"),
            100.0,
        ),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT",
            trading_cfg.get("buy_amount_usdc"),
            DEFAULT_BUY_AMOUNT_USDC,
        ),
        max_buy_amount_usdc=_get_config_value(
            "POLYBOT_MAX_BUY_AMOUNT_USDC",
            trading_cfg.get("max_buy_amount_usdc"),
            5.0,
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY", trading_cfg.get("min_liquidity"), 20_000.0
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H", trading_cfg.get("min_volume_24h"), 10_000.0
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
        max_open_notional_usdc=_get_config_value(
            "POLYBOT_MAX_OPEN_NOTIONAL_USDC",
            trading_cfg.get("max_open_notional_usdc"),
            15.0,
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
            6.0,
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
            "POLYBOT_MAX_SPREAD", trading_cfg.get("max_spread"), 0.02
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
        excluded_categories=_get_list_config_value(
            "POLYBOT_EXCLUDED_CATEGORIES",
            trading_cfg.get("excluded_categories"),
            list(REQUIRED_EXCLUDED_CATEGORIES),
        ),
    )
    validate_yaml_config_shape(cfg, trading)

    # Kiwi is permanently simulation-only in this version.  Do not even copy
    # ambient Jenkins wallet credentials into process configuration.
    api = ApiConfig(
        private_key="",
        funder_address="",
        signature_type=1,
    )
    _validate_config(trading, api)
    _validate_canonical_job_arm(job_name, trading)
    experiment = _load_experiment_collection(job_name)

    db_dir = (project_root / "data" / job_name).resolve()
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "trades_sim.db"
    _validate_existing_db_arm(db_path, job_name, trading)
    return BotConfig(
        trading=trading,
        api=api,
        db_path=db_path,
        simulation_mode=True,
        job_name=job_name,
        experiment=experiment,
    )
