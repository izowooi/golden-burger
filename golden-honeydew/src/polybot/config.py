"""Configuration management for the trading bot (Night Watch)."""
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


def _get_str_config_value(env_key: str, yaml_value, default: str) -> str:
    """환경변수 > yaml > 기본값 순서로 문자열 설정값 로드."""
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val
    if yaml_value is not None:
        return str(yaml_value)
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
    """제외 카테고리 로드 (§3.7: POLYBOT_EXCLUDED_CATEGORIES env 오버라이드).

    env는 comma 구분 문자열. 기본은 "" (필터 비활성) —
    filters.py의 SPORTS_KEYWORDS 과차단 문제 때문에 기본 비활성 유지.
    """
    env_val = os.getenv("POLYBOT_EXCLUDED_CATEGORIES")
    if env_val is not None:
        return [c.strip() for c in env_val.split(",") if c.strip()]
    if yaml_value is not None:
        return list(yaml_value)
    return []


@dataclass
class QuietTimeConfig:
    """한산 시간대 설정 (진입 허용 시간)."""
    hours_utc: str = "6-13"     # UTC 기준, 자정 넘는 "22-4"도 가능
    start_hour: int = 6
    end_hour: int = 13
    weekends: bool = True       # 주말 전체를 한산 시간대로 취급


@dataclass
class SignalConfig:
    """Night Watch 진입 시그널 설정."""
    median_lookback_hours: int = 24   # median 계산 윈도우
    dev_min: float = 0.05             # 최소 편차 |현재가 - median|
    vol_spike_block: float = 1.5      # 거래량 급증 배수 (>= 이면 진입 금지)
    entry_prob_min: float = 0.30      # 매수 토큰 가격 하한
    entry_prob_max: float = 0.90      # 매수 토큰 가격 상한


@dataclass
class TimeBasedConfig:
    """시간 기반 진입/청산 설정."""
    entry_hours_min: int = 24     # 해결까지 최소 24h 남아야 진입
    exit_hours: int = 12          # 해결 12h 전 청산
    max_holding_hours: int = 24   # 최대 보유 24h (복원 실패 시 회전)


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 15000.0
    min_volume_24h: float = 0.0           # 0 = 비활성
    max_positions: int = -1               # -1 means unlimited
    take_profit_percent: float = 0.06     # 이익실현 +6%
    stop_loss_percent: float = -0.06      # 손절 -6%
    reentry_cooldown_hours: float = 24.0  # §3.3 재진입 쿨다운
    history_backfill: bool = True         # §3.6 prices-history 백필
    quiet: QuietTimeConfig = field(default_factory=QuietTimeConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
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
    # 함수 내부 import: strategy/__init__ → scanner → config 순환 import 방지
    from .strategy.signals import parse_quiet_hours

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

    # Parse quiet time config
    quiet_cfg = trading_cfg.get("quiet", {})
    quiet_hours_str = _get_str_config_value(
        "POLYBOT_QUIET_HOURS_UTC",
        quiet_cfg.get("hours_utc"),
        "6-13"
    )
    start_hour, end_hour = parse_quiet_hours(quiet_hours_str)
    quiet = QuietTimeConfig(
        hours_utc=quiet_hours_str,
        start_hour=start_hour,
        end_hour=end_hour,
        weekends=_get_bool_config_value(
            "POLYBOT_QUIET_WEEKENDS",
            quiet_cfg.get("weekends"),
            True
        ),
    )

    # Parse signal config
    signal_cfg = trading_cfg.get("signal", {})
    signal = SignalConfig(
        median_lookback_hours=_get_config_value(
            "POLYBOT_MEDIAN_LOOKBACK_HOURS",
            signal_cfg.get("median_lookback_hours"),
            24,
            int
        ),
        dev_min=_get_config_value(
            "POLYBOT_DEV_MIN",
            signal_cfg.get("dev_min"),
            0.05,
            float
        ),
        vol_spike_block=_get_config_value(
            "POLYBOT_VOL_SPIKE_BLOCK",
            signal_cfg.get("vol_spike_block"),
            1.5,
            float
        ),
        entry_prob_min=_get_config_value(
            "POLYBOT_ENTRY_PROB_MIN",
            signal_cfg.get("entry_prob_min"),
            0.30,
            float
        ),
        entry_prob_max=_get_config_value(
            "POLYBOT_ENTRY_PROB_MAX",
            signal_cfg.get("entry_prob_max"),
            0.90,
            float
        ),
    )

    # Parse time-based config
    time_based_cfg = trading_cfg.get("time_based", {})
    time_based = TimeBasedConfig(
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
        max_holding_hours=_get_config_value(
            "POLYBOT_MAX_HOLDING_HOURS",
            time_based_cfg.get("max_holding_hours"),
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
            0.06,
            float
        ),
        stop_loss_percent=_get_config_value(
            "POLYBOT_STOP_LOSS",
            trading_cfg.get("stop_loss_percent"),
            -0.06,
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
        quiet=quiet,
        signal=signal,
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
