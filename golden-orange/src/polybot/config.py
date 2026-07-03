"""Configuration management for the trading bot (env > yaml > 기본값)."""
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


def _get_list_config_value(
    env_key: str,
    yaml_value,
    default: List[str]
) -> List[str]:
    """환경변수(comma 구분) > yaml > 기본값 순서로 리스트 설정값 로드.

    env가 빈 문자열이면 [] (필터 비활성화)를 의미한다.
    """
    env_val = os.getenv(env_key)
    if env_val is not None:
        return [item.strip() for item in env_val.split(",") if item.strip()]
    if yaml_value is not None:
        return list(yaml_value)
    return default


@dataclass
class StrategyConfig:
    """Fear Spike Fade 전략 시그널 설정."""
    base_window_days: float = 7.0            # base 계산 윈도우 (일)
    base_exclude_recent_hours: float = 6.0   # base 계산에서 제외할 최근 시간
    base_max: float = 0.15                   # base 상한 (평시 tail 시장만)
    jump_min: float = 0.10                   # 스파이크 최소 상승폭 (yes_now - base)
    yes_max: float = 0.30                    # 스파이크 후 YES 상한 (NO >= 0.70)
    spike_wait_minutes: float = 90.0         # 스파이크 시작 후 대기 시간 (분)
    stall_window_minutes: float = 45.0       # 신고가 부재 확인 윈도우 (분)
    vol_mult_min: float = 2.0                # volume24h >= 윈도우 평균 x 배수
    retrace_ratio: float = 0.5               # retrace 익절: YES <= base + ratio*(peak-base)
    max_holding_hours: float = 72.0          # 최대 보유 시간 (되돌림 실패 청산)


@dataclass
class TimeBasedConfig:
    """시간 기반 진입/청산 설정."""
    entry_hours_min: int = 72    # 해결까지 최소 72시간 남아야 진입 (마감 임박 스파이크 배제)
    exit_hours: int = 24         # 해결 24시간 전 청산


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 15000.0
    min_volume_24h: float = 0.0           # 0이면 거래량 필터 비활성화
    max_positions: int = -1               # -1 means unlimited
    take_profit_percent: float = 0.08     # 익절 +8% (보조 - 주 청산은 retrace_target)
    stop_loss_percent: float = -0.10      # 손절 -10% (YES 계속 상승 = 진짜 정보)
    reentry_cooldown_hours: float = 24.0  # 재진입 쿨다운
    history_backfill: bool = True         # CLOB /prices-history 백필
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    time_based: TimeBasedConfig = field(default_factory=TimeBasedConfig)
    excluded_categories: List[str] = field(default_factory=list)  # 기본 비활성


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

    # Parse strategy config
    strategy_cfg = trading_cfg.get("strategy", {})
    strategy = StrategyConfig(
        base_window_days=_get_config_value(
            "POLYBOT_BASE_WINDOW_DAYS",
            strategy_cfg.get("base_window_days"),
            7.0,
            float
        ),
        base_exclude_recent_hours=_get_config_value(
            "POLYBOT_BASE_EXCLUDE_RECENT_HOURS",
            strategy_cfg.get("base_exclude_recent_hours"),
            6.0,
            float
        ),
        base_max=_get_config_value(
            "POLYBOT_BASE_MAX",
            strategy_cfg.get("base_max"),
            0.15,
            float
        ),
        jump_min=_get_config_value(
            "POLYBOT_JUMP_MIN",
            strategy_cfg.get("jump_min"),
            0.10,
            float
        ),
        yes_max=_get_config_value(
            "POLYBOT_YES_MAX",
            strategy_cfg.get("yes_max"),
            0.30,
            float
        ),
        spike_wait_minutes=_get_config_value(
            "POLYBOT_SPIKE_WAIT_MINUTES",
            strategy_cfg.get("spike_wait_minutes"),
            90.0,
            float
        ),
        stall_window_minutes=_get_config_value(
            "POLYBOT_STALL_WINDOW_MINUTES",
            strategy_cfg.get("stall_window_minutes"),
            45.0,
            float
        ),
        vol_mult_min=_get_config_value(
            "POLYBOT_VOL_MULT_MIN",
            strategy_cfg.get("vol_mult_min"),
            2.0,
            float
        ),
        retrace_ratio=_get_config_value(
            "POLYBOT_RETRACE_RATIO",
            strategy_cfg.get("retrace_ratio"),
            0.5,
            float
        ),
        max_holding_hours=_get_config_value(
            "POLYBOT_MAX_HOLDING_HOURS",
            strategy_cfg.get("max_holding_hours"),
            72.0,
            float
        ),
    )

    # Parse time-based config
    time_based_cfg = trading_cfg.get("time_based", {})
    time_based = TimeBasedConfig(
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN",
            time_based_cfg.get("entry_hours_min"),
            72,
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
            15000.0,
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
            0.08,
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
        excluded_categories=_get_list_config_value(
            "POLYBOT_EXCLUDED_CATEGORIES",
            trading_cfg.get("excluded_categories"),
            []
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
