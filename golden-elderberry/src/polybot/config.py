"""Configuration management for the trading bot (Panic Fade)."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union
import os
import yaml
from dotenv import load_dotenv


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
    if yaml_value is not None:
        return value_type(yaml_value)
    return default


def _get_bool_config_value(
    env_key: str,
    yaml_value,
    default: bool
) -> bool:
    """환경변수 > yaml > 기본값 순서로 bool 설정값 로드."""
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes")
    if yaml_value is not None:
        return bool(yaml_value)
    return default


def _get_excluded_categories(yaml_value) -> List[str]:
    """제외 카테고리 로드 (POLYBOT_EXCLUDED_CATEGORIES env 오버라이드).

    env는 comma 구분 문자열, 기본 "" = 필터 비활성화.
    filters.py의 SPORTS_KEYWORDS 과차단 문제가 있으므로 기본은 비활성 유지.
    """
    env_val = os.getenv("POLYBOT_EXCLUDED_CATEGORIES")
    if env_val is not None:
        return [c.strip() for c in env_val.split(",") if c.strip()]
    if yaml_value is not None:
        return list(yaml_value)
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
    signature_type: int = 1  # 1 for Magic.Link (email wallet)
    chain_id: int = 137  # Polygon Mainnet


@dataclass
class BotConfig:
    """Complete bot configuration."""
    trading: TradingConfig
    api: ApiConfig
    db_path: Path
    simulation_mode: bool = False
    job_name: str = "default"


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
    trading_cfg = cfg.get("trading", {})

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
    )

    # Simulation mode (CLI flag overrides config file)
    if simulation_mode is None:
        simulation_mode = cfg.get("simulation_mode", False)

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
