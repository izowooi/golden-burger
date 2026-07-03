"""Configuration management for the trading bot (Bottom Fisher)."""
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
    """Bottom Fisher 전략 파라미터."""
    lookback_days: float = 20.0          # 롤링 최저가 룩백 (일)
    exclude_recent_hours: float = 24.0   # 최저가 산출 시 제외할 최근 구간 (시간)
    hold_hours: float = 120.0            # calendar exit 보유 시간 (5일)
    prob_min: float = 0.03               # 진입 가능한 YES 가격 하한
    prob_max: float = 0.50               # 진입 가능한 YES 가격 상한


@dataclass
class TimeBasedConfig:
    """시간 기반 진입/청산 설정."""
    entry_hours_min: int = 720  # 해결 720시간(30일) 이상 남아야 진입 (장기 시장만)
    exit_hours: int = 24        # 해결 24시간 이내면 청산


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 10000.0
    min_volume_24h: float = 0.0            # 0 = 비활성 (얇은 장기 tail 시장이 대상)
    max_positions: int = -1                # -1 means unlimited
    take_profit_percent: float = 0.30      # 익절 안전판 +30% (조기 행운 익절)
    stop_loss_percent: float = -0.30       # 손절 안전판 -30%
    reentry_cooldown_hours: float = 168.0  # 재진입 쿨다운 7일 (최저가 부근 연속 재진입 방지)
    history_backfill: bool = True          # prices-history 백필 (20일 룩백의 생명선)
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

    # Parse strategy config (Bottom Fisher)
    strategy_cfg = trading_cfg.get("strategy", {})
    strategy = StrategyConfig(
        lookback_days=_get_config_value(
            "POLYBOT_LOOKBACK_DAYS",
            strategy_cfg.get("lookback_days"),
            20.0,
            float
        ),
        exclude_recent_hours=_get_config_value(
            "POLYBOT_EXCLUDE_RECENT_HOURS",
            strategy_cfg.get("exclude_recent_hours"),
            24.0,
            float
        ),
        hold_hours=_get_config_value(
            "POLYBOT_HOLD_HOURS",
            strategy_cfg.get("hold_hours"),
            120.0,
            float
        ),
        prob_min=_get_config_value(
            "POLYBOT_PROB_MIN",
            strategy_cfg.get("prob_min"),
            0.03,
            float
        ),
        prob_max=_get_config_value(
            "POLYBOT_PROB_MAX",
            strategy_cfg.get("prob_max"),
            0.50,
            float
        ),
    )

    # Parse time-based config
    time_based_cfg = trading_cfg.get("time_based", {})
    time_based = TimeBasedConfig(
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN",
            time_based_cfg.get("entry_hours_min"),
            720,
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
            10000.0,
            float
        ),
        min_volume_24h=_get_config_value(
            "POLYBOT_MIN_VOLUME_24H",
            trading_cfg.get("min_volume_24h"),
            0.0,
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
            0.30,
            float
        ),
        stop_loss_percent=_get_config_value(
            "POLYBOT_STOP_LOSS",
            trading_cfg.get("stop_loss_percent"),
            -0.30,
            float
        ),
        reentry_cooldown_hours=_get_config_value(
            "POLYBOT_REENTRY_COOLDOWN_HOURS",
            trading_cfg.get("reentry_cooldown_hours"),
            168.0,
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
