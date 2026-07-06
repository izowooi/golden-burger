"""Configuration management for the trading bot."""
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
    """POLYBOT_EXCLUDED_CATEGORIES(comma 구분) > yaml > 기본 [] (필터 비활성).

    SPORTS_KEYWORDS 과차단 문제가 있어 기본은 비활성 유지.
    """
    env_val = os.getenv("POLYBOT_EXCLUDED_CATEGORIES")
    if env_val is not None:
        return [c.strip() for c in env_val.split(",") if c.strip()]
    if yaml_value is not None:
        return list(yaml_value)
    return []


@dataclass
class TrailingStopConfig:
    """트레일링 스탑 설정."""
    enabled: bool = True
    percent: float = 0.06  # 최고점 대비 6% 하락 시 청산


@dataclass
class TimeConfig:
    """시간 기반 진입/청산 설정."""
    entry_hours_min: int = 24  # 해결까지 24시간 이상 남아야 진입
    exit_hours: int = 12       # 해결 12시간 이내 청산


@dataclass
class ShockConfig:
    """Shock Follow 전략 파라미터."""
    jump_window_hours: float = 6.0     # 점프 감지 윈도우
    jump_min: float = 0.10             # 윈도우 내 최저가 대비 최소 상승폭
    base_min: float = 0.15             # 점프 시작 기준가 하한
    base_max: float = 0.70             # 점프 시작 기준가 상한
    current_max: float = 0.85          # 현재가 상한 (러닝룸)
    hold_window_minutes: float = 60.0  # 고점 유지 확인 윈도우
    max_pullback: float = 0.02         # 고점 대비 최대 되돌림
    vol_mult_min: float = 2.0          # 거래량 확인 배수
    death_window_hours: float = 3.0    # 모멘텀 사망 판정 윈도우 (청산)


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 20000.0
    min_volume_24h: float = 10000.0
    take_profit_percent: float = 0.12   # 이익실현 +12% (목표가 0.99 캡)
    stop_loss_percent: float = -0.08    # 손절 -8%
    max_positions: int = -1             # -1 means unlimited
    reentry_cooldown_hours: float = 24.0
    history_backfill: bool = True
    trailing_stop: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    time_based: TimeConfig = field(default_factory=TimeConfig)
    shock: ShockConfig = field(default_factory=ShockConfig)
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

    # Parse trailing stop config
    trailing_stop_cfg = trading_cfg.get("trailing_stop", {})
    trailing_stop = TrailingStopConfig(
        enabled=_get_bool_config_value(
            "POLYBOT_TRAILING_STOP_ENABLED",
            trailing_stop_cfg.get("enabled"),
            True
        ),
        percent=_get_config_value(
            "POLYBOT_TRAILING_STOP_PERCENT",
            trailing_stop_cfg.get("percent"),
            0.06,
            float
        ),
    )

    # Parse time-based config
    time_based_cfg = trading_cfg.get("time_based", {})
    time_based = TimeConfig(
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN",
            time_based_cfg.get("entry_hours_min"),
            24,
            int
        ),
        exit_hours=_get_config_value(
            "POLYBOT_EXIT_HOURS",
            time_based_cfg.get("exit_hours"),
            12,
            int
        ),
    )

    # Parse shock strategy config
    shock_cfg = trading_cfg.get("shock", {})
    shock = ShockConfig(
        jump_window_hours=_get_config_value(
            "POLYBOT_JUMP_WINDOW_HOURS", shock_cfg.get("jump_window_hours"), 6.0, float
        ),
        jump_min=_get_config_value(
            "POLYBOT_JUMP_MIN", shock_cfg.get("jump_min"), 0.10, float
        ),
        base_min=_get_config_value(
            "POLYBOT_BASE_MIN", shock_cfg.get("base_min"), 0.15, float
        ),
        base_max=_get_config_value(
            "POLYBOT_BASE_MAX", shock_cfg.get("base_max"), 0.70, float
        ),
        current_max=_get_config_value(
            "POLYBOT_CURRENT_MAX", shock_cfg.get("current_max"), 0.85, float
        ),
        hold_window_minutes=_get_config_value(
            "POLYBOT_HOLD_WINDOW_MINUTES", shock_cfg.get("hold_window_minutes"), 60.0, float
        ),
        max_pullback=_get_config_value(
            "POLYBOT_MAX_PULLBACK", shock_cfg.get("max_pullback"), 0.02, float
        ),
        vol_mult_min=_get_config_value(
            "POLYBOT_VOL_MULT_MIN", shock_cfg.get("vol_mult_min"), 2.0, float
        ),
        death_window_hours=_get_config_value(
            "POLYBOT_DEATH_WINDOW_HOURS", shock_cfg.get("death_window_hours"), 3.0, float
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
        take_profit_percent=_get_config_value(
            "POLYBOT_TAKE_PROFIT",
            trading_cfg.get("take_profit_percent"),
            0.12,
            float
        ),
        stop_loss_percent=_get_config_value(
            "POLYBOT_STOP_LOSS",
            trading_cfg.get("stop_loss_percent"),
            -0.08,
            float
        ),
        max_positions=_get_config_value(
            "POLYBOT_MAX_POSITIONS",
            trading_cfg.get("max_positions"),
            -1,
            int
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
        trailing_stop=trailing_stop,
        time_based=time_based,
        shock=shock,
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
        signature_type=int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1")),
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
