"""Configuration management for the trading bot (Panic Fade)."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union
import math
import os
import yaml
from dotenv import load_dotenv
from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)


LIFECYCLE_MODES = frozenset({"active", "close_only", "archive_only"})


def _get_config_value(
    env_key: str,
    yaml_value,
    default,
    value_type: type = float
) -> Union[float, int]:
    """환경변수 > yaml > 기본값 순서로 설정값 로드.

    Args:
        env_key: 환경변수 이름
        yaml_value: config.yaml에서 읽은 값
        default: 기본값
        value_type: 변환할 타입 (float 또는 int)

    Returns:
        우선순위에 따른 설정값
    """
    env_val = os.getenv(env_key)
    if env_val is not None:
        return value_type(env_val)
    if yaml_value is None:
        return default
    if isinstance(yaml_value, bool) or not isinstance(yaml_value, (int, float)):
        raise ValueError(f"{env_key} YAML value must be numeric")
    if value_type is int and not isinstance(yaml_value, int):
        raise ValueError(f"{env_key} YAML value must be an integer")
    return value_type(yaml_value)


def _get_bool_config_value(
    env_key: str,
    yaml_value,
    default: bool
) -> bool:
    """환경변수 > yaml > 기본값 순서로 bool 설정값 로드."""
    env_val = os.getenv(env_key)
    value = env_val if env_val is not None else yaml_value
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


def _get_lifecycle_mode(yaml_value) -> str:
    """환경변수 > yaml > 기본값 순서로 봇 수명주기 모드를 로드."""
    env_val = os.getenv("POLYBOT_LIFECYCLE_MODE")
    value = env_val if env_val is not None else yaml_value
    if value is None:
        return "active"
    if not isinstance(value, str):
        raise ValueError(
            "POLYBOT_LIFECYCLE_MODE must be one of: "
            "active, close_only, archive_only"
        )
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in LIFECYCLE_MODES:
        raise ValueError(
            "POLYBOT_LIFECYCLE_MODE must be one of: "
            "active, close_only, archive_only"
        )
    return normalized


def _get_excluded_categories(yaml_value) -> List[str]:
    """제외 카테고리 로드 (POLYBOT_EXCLUDED_CATEGORIES env 오버라이드).

    env는 comma 구분 문자열, 기본 "" = 필터 비활성화.
    filters.py의 SPORTS_KEYWORDS 과차단 문제가 있으므로 기본은 비활성 유지.
    """
    env_val = os.getenv("POLYBOT_EXCLUDED_CATEGORIES")
    if env_val is not None:
        return [c.strip() for c in env_val.split(",") if c.strip()]
    if yaml_value is not None:
        if not isinstance(yaml_value, list) or any(
            not isinstance(item, str) for item in yaml_value
        ):
            raise ValueError("excluded_categories must be a list of strings")
        return [item.strip() for item in yaml_value if item.strip()]
    return []


@dataclass
class StrategyConfig:
    """Panic Fade 전략 파라미터."""
    ref_window_hours: float = 48.0          # 기준가 산출 윈도우
    ref_exclude_recent_hours: float = 3.0   # ref 산출 시 제외할 최근 구간
    ref_min: float = 0.70                   # ref 최소값 (원래 favorite)
    drop_min: float = 0.12                  # 최소 낙폭
    current_min: float = 0.35               # 붕괴 배제 하한
    current_max: float = 0.75               # 붕괴 배제 상한
    stab_window_minutes: float = 45.0       # 바닥 안정화 확인 윈도우
    stab_max_std: float = 0.02              # 안정화 최대 std
    max_holding_hours: float = 48.0         # 최대 보유 시간 (반등 실패 청산)


@dataclass
class TimeBasedConfig:
    """시간 기반 진입/청산 설정."""
    entry_hours_min: int = 48   # 해결 48시간 이상 남아야 진입
    exit_hours: int = 24        # 해결 24시간 이내면 청산


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    lifecycle_mode: str = "active"
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 20000.0
    min_volume_24h: float = 10000.0
    max_positions: int = -1                # -1 means unlimited
    take_profit_percent: float = 0.10      # 이익실현 +10%
    stop_loss_percent: float = -0.10       # 손절 -10%
    reentry_cooldown_hours: float = 24.0   # 재진입 쿨다운
    history_backfill: bool = True          # prices-history 백필
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    time_based: TimeBasedConfig = field(default_factory=TimeBasedConfig)
    excluded_categories: List[str] = field(default_factory=list)


@dataclass
class ApiConfig:
    """API authentication configuration."""
    private_key: str
    funder_address: str
    # 1=POLY_PROXY (구형 이메일 계정), 3=POLY_1271 (2026+ 신규 계정의 스마트 지갑).
    # 신규 계정을 1로 서명하면 CLOB이 "maker address not allowed"로 거절한다.
    signature_type: int = 1
    chain_id: int = 137  # Polygon Mainnet


@dataclass
class BotConfig:
    """Complete bot configuration."""
    trading: TradingConfig
    api: ApiConfig
    db_path: Path
    simulation_mode: bool = False
    job_name: str = "default"


def _validate_config(trading: TradingConfig, api: ApiConfig) -> None:
    """Reject unsafe or internally inconsistent resolved configuration."""
    strategy = trading.strategy
    timing = trading.time_based
    numeric = {
        "buy_amount_usdc": trading.buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "min_volume_24h": trading.min_volume_24h,
        "max_positions": trading.max_positions,
        "take_profit_percent": trading.take_profit_percent,
        "stop_loss_percent": trading.stop_loss_percent,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "strategy.ref_window_hours": strategy.ref_window_hours,
        "strategy.ref_exclude_recent_hours": strategy.ref_exclude_recent_hours,
        "strategy.ref_min": strategy.ref_min,
        "strategy.drop_min": strategy.drop_min,
        "strategy.current_min": strategy.current_min,
        "strategy.current_max": strategy.current_max,
        "strategy.stab_window_minutes": strategy.stab_window_minutes,
        "strategy.stab_max_std": strategy.stab_max_std,
        "strategy.max_holding_hours": strategy.max_holding_hours,
        "time_based.entry_hours_min": timing.entry_hours_min,
        "time_based.exit_hours": timing.exit_hours,
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
    if trading.min_liquidity < 0 or trading.min_volume_24h < 0:
        raise ValueError("min_liquidity and min_volume_24h must be >= 0")
    if trading.max_positions != -1 and trading.max_positions <= 0:
        raise ValueError("max_positions must be -1 or a positive integer")
    if not 0 < trading.take_profit_percent <= 10:
        raise ValueError("take_profit_percent must be > 0 and <= 10")
    if not -1 < trading.stop_loss_percent < 0:
        raise ValueError("stop_loss_percent must be between -1 and 0")
    if trading.reentry_cooldown_hours <= 0:
        raise ValueError("reentry_cooldown_hours must be > 0")
    if strategy.ref_window_hours <= 0:
        raise ValueError("strategy.ref_window_hours must be > 0")
    if not 0 < strategy.ref_exclude_recent_hours < strategy.ref_window_hours:
        raise ValueError("strategy.ref_exclude_recent_hours must be > 0 and shorter than ref_window_hours")
    if not 0.5 <= strategy.ref_min < 1:
        raise ValueError("strategy.ref_min must be between 0.5 and 1")
    for name, value in {
        "drop_min": strategy.drop_min,
        "current_min": strategy.current_min,
        "current_max": strategy.current_max,
    }.items():
        if not 0 < value < 1:
            raise ValueError(f"strategy.{name} must be between 0 and 1")
    if not 0 <= strategy.stab_max_std < 1:
        raise ValueError("strategy.stab_max_std must be between 0 and 1")
    if strategy.current_min >= strategy.current_max:
        raise ValueError("strategy.current_min must be < current_max")
    if strategy.drop_min >= strategy.ref_min:
        raise ValueError("strategy.drop_min must be < ref_min")
    if strategy.stab_window_minutes <= 0 or strategy.max_holding_hours <= 0:
        raise ValueError("strategy stabilization and holding windows must be > 0")
    if strategy.stab_window_minutes > strategy.ref_exclude_recent_hours * 60:
        raise ValueError("strategy.stab_window_minutes must fit inside ref_exclude_recent_hours")
    if not 0 < timing.exit_hours <= timing.entry_hours_min:
        raise ValueError("time_based hours must satisfy 0 < exit_hours <= entry_hours_min")
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
) -> BotConfig:
    """Load configuration from YAML file and environment variables.

    Args:
        config_path: Path to config.yaml file
        job_name: Jenkins job name (used for DB path separation)
        env_path: Optional path to .env file
        simulation_mode: Override simulation mode (CLI --simulate flag)

    Returns:
        BotConfig instance with all settings

    Raises:
        ValueError: If required environment variables are missing
    """
    # Load environment variables
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    # Load YAML config
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    # Parse trading config (환경변수 > yaml > 기본값)
    trading_cfg = get_trading_config_mapping(cfg)

    # Parse strategy config (Panic Fade)
    strategy_cfg = trading_cfg.get("strategy", {})
    strategy = StrategyConfig(
        ref_window_hours=_get_config_value(
            "POLYBOT_REF_WINDOW_HOURS",
            strategy_cfg.get("ref_window_hours"),
            48.0,
            float
        ),
        ref_exclude_recent_hours=_get_config_value(
            "POLYBOT_REF_EXCLUDE_RECENT_HOURS",
            strategy_cfg.get("ref_exclude_recent_hours"),
            3.0,
            float
        ),
        ref_min=_get_config_value(
            "POLYBOT_REF_MIN",
            strategy_cfg.get("ref_min"),
            0.70,
            float
        ),
        drop_min=_get_config_value(
            "POLYBOT_DROP_MIN",
            strategy_cfg.get("drop_min"),
            0.12,
            float
        ),
        current_min=_get_config_value(
            "POLYBOT_CURRENT_MIN",
            strategy_cfg.get("current_min"),
            0.35,
            float
        ),
        current_max=_get_config_value(
            "POLYBOT_CURRENT_MAX",
            strategy_cfg.get("current_max"),
            0.75,
            float
        ),
        stab_window_minutes=_get_config_value(
            "POLYBOT_STAB_WINDOW_MINUTES",
            strategy_cfg.get("stab_window_minutes"),
            45.0,
            float
        ),
        stab_max_std=_get_config_value(
            "POLYBOT_STAB_MAX_STD",
            strategy_cfg.get("stab_max_std"),
            0.02,
            float
        ),
        max_holding_hours=_get_config_value(
            "POLYBOT_MAX_HOLDING_HOURS",
            strategy_cfg.get("max_holding_hours"),
            48.0,
            float
        ),
    )

    # Parse time-based config
    time_based_cfg = trading_cfg.get("time_based", {})
    time_based = TimeBasedConfig(
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN",
            time_based_cfg.get("entry_hours_min"),
            48,
            int
        ),
        exit_hours=_get_config_value(
            "POLYBOT_EXIT_HOURS",
            time_based_cfg.get("exit_hours"),
            24,
            int
        ),
    )

    trading = TradingConfig(
        lifecycle_mode=_get_lifecycle_mode(
            trading_cfg.get("lifecycle_mode")
        ),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT",
            trading_cfg.get("buy_amount_usdc"),
            5.0,
            float
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY",
            trading_cfg.get("min_liquidity"),
            20000.0,
            float
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H",
            trading_cfg.get("min_volume_24h"),
            10000.0,
            float
        ),
        max_positions=_get_config_value(
            "POLYBOT_MAX_POSITIONS",
            trading_cfg.get("max_positions"),
            -1,
            int
        ),
        take_profit_percent=_get_config_value(
            "POLYBOT_TAKE_PROFIT",
            trading_cfg.get("take_profit_percent"),
            0.10,
            float
        ),
        stop_loss_percent=_get_config_value(
            "POLYBOT_STOP_LOSS",
            trading_cfg.get("stop_loss_percent"),
            -0.10,
            float
        ),
        reentry_cooldown_hours=_get_config_value(
            "POLYBOT_REENTRY_COOLDOWN_HOURS",
            trading_cfg.get("reentry_cooldown_hours"),
            24.0,
            float
        ),
        history_backfill=_get_bool_config_value(
            "POLYBOT_HISTORY_BACKFILL",
            trading_cfg.get("history_backfill"),
            True
        ),
        strategy=strategy,
        time_based=time_based,
        excluded_categories=_get_excluded_categories(
            trading_cfg.get("excluded_categories")
        ),
    )

    validate_yaml_config_shape(cfg, trading)

    # Parse API config from environment variables
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS")

    if not private_key:
        raise ValueError("POLYMARKET_PRIVATE_KEY environment variable is required")
    if not funder_address:
        raise ValueError("POLYMARKET_FUNDER_ADDRESS environment variable is required")

    # Remove 0x prefix if present (py-clob-client handles this)
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    api = ApiConfig(
        private_key=private_key,
        funder_address=funder_address,
        signature_type=int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1")),
    )

    _validate_config(trading, api)

    # Simulation mode (CLI flag overrides config file)
    if simulation_mode is None:
        simulation_mode = cfg.get("simulation_mode", False)
    if not isinstance(simulation_mode, bool):
        raise ValueError("simulation_mode must be a boolean")

    # Set up database path (per job, separate for simulation)
    db_dir = Path("data") / job_name
    db_dir.mkdir(parents=True, exist_ok=True)
    if simulation_mode:
        db_path = db_dir / "trades_sim.db"
    else:
        db_path = db_dir / "trades.db"

    return BotConfig(
        trading=trading,
        api=api,
        db_path=db_path,
        simulation_mode=simulation_mode,
        job_name=job_name,
    )
