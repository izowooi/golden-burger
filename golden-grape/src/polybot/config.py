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
    """제외 카테고리 로드 (env > yaml > 기본 빈 리스트).

    POLYBOT_EXCLUDED_CATEGORIES는 comma 구분 문자열. 기본은 "" = 필터 비활성.
    (filters.py SPORTS_KEYWORDS의 과차단 문제가 있어 기본은 비활성 유지.)
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
class CascadeConfig:
    """Cascade Rider 전략 파라미터 (진입 시그널)."""
    prob_min: float = 0.40            # 매수 토큰 가격 하한
    prob_max: float = 0.80            # 매수 토큰 가격 상한 (러닝룸 확보)
    drift_lookback_hours: int = 24    # 드리프트 판정 윈도우
    drift_min: float = 0.04           # 24h 드리프트 하한 (+4%p)
    drift_max: float = 0.10           # 24h 드리프트 상한 (mean-revert 영역 배제)
    bucket_hours: int = 4             # 일관성 버킷 크기 (24h / 4h = 6 버킷)
    consistency_min: float = 0.70     # 비음(>= 0) 변화 버킷 비율 하한
    vol_accel_min: float = 1.2        # 거래량 가속 배수 하한
    death_window_hours: int = 6       # 드리프트 소멸 판정 윈도우


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 20000.0
    min_volume_24h: float = 10000.0
    take_profit_percent: float = 0.15     # 이익실현 +15% (목표가 0.99 캡)
    stop_loss_percent: float = -0.08      # 손절 -8%
    max_positions: int = -1               # -1 means unlimited
    reentry_cooldown_hours: int = 24      # 재진입 쿨다운
    history_backfill: bool = True         # CLOB /prices-history 백필
    entry_hours_min: int = 48             # 해결까지 최소 48시간 남아야 진입
    exit_hours: int = 24                  # 해결 24시간 전 청산
    trailing_stop: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    cascade: CascadeConfig = field(default_factory=CascadeConfig)
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

    # Parse cascade strategy config
    cascade_cfg = trading_cfg.get("cascade", {})
    cascade = CascadeConfig(
        prob_min=_get_config_value(
            "POLYBOT_PROB_MIN", cascade_cfg.get("prob_min"), 0.40, float
        ),
        prob_max=_get_config_value(
            "POLYBOT_PROB_MAX", cascade_cfg.get("prob_max"), 0.80, float
        ),
        drift_lookback_hours=_get_config_value(
            "POLYBOT_DRIFT_LOOKBACK_HOURS",
            cascade_cfg.get("drift_lookback_hours"),
            24,
            int
        ),
        drift_min=_get_config_value(
            "POLYBOT_DRIFT_MIN", cascade_cfg.get("drift_min"), 0.04, float
        ),
        drift_max=_get_config_value(
            "POLYBOT_DRIFT_MAX", cascade_cfg.get("drift_max"), 0.10, float
        ),
        bucket_hours=_get_config_value(
            "POLYBOT_BUCKET_HOURS", cascade_cfg.get("bucket_hours"), 4, int
        ),
        consistency_min=_get_config_value(
            "POLYBOT_CONSISTENCY_MIN",
            cascade_cfg.get("consistency_min"),
            0.70,
            float
        ),
        vol_accel_min=_get_config_value(
            "POLYBOT_VOL_ACCEL_MIN", cascade_cfg.get("vol_accel_min"), 1.2, float
        ),
        death_window_hours=_get_config_value(
            "POLYBOT_DEATH_WINDOW_HOURS",
            cascade_cfg.get("death_window_hours"),
            6,
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
        take_profit_percent=_get_config_value(
            "POLYBOT_TAKE_PROFIT",
            trading_cfg.get("take_profit_percent"),
            0.15,
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
            24,
            int
        ),
        history_backfill=_get_bool_config_value(
            "POLYBOT_HISTORY_BACKFILL",
            trading_cfg.get("history_backfill"),
            True
        ),
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN",
            trading_cfg.get("entry_hours_min"),
            48,
            int
        ),
        exit_hours=_get_config_value(
            "POLYBOT_EXIT_HOURS",
            trading_cfg.get("exit_hours"),
            24,
            int
        ),
        trailing_stop=trailing_stop,
        cascade=cascade,
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
