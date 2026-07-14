"""Configuration management for the trading bot."""
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
    """제외 카테고리 로드 (env > yaml > 기본 빈 리스트).

    POLYBOT_EXCLUDED_CATEGORIES는 comma 구분 문자열. 기본은 "" = 필터 비활성.
    (filters.py SPORTS_KEYWORDS의 과차단 문제가 있어 기본은 비활성 유지.)
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
    death_window_min_points: int = 3  # 청산 판정 최소 스냅샷 수
    death_window_min_coverage: float = 0.5  # 청산 윈도우 최소 시간 커버리지


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    lifecycle_mode: str = "active"
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
    cascade = trading.cascade
    trailing = trading.trailing_stop
    numeric = {
        "buy_amount_usdc": trading.buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "min_volume_24h": trading.min_volume_24h,
        "take_profit_percent": trading.take_profit_percent,
        "stop_loss_percent": trading.stop_loss_percent,
        "max_positions": trading.max_positions,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "entry_hours_min": trading.entry_hours_min,
        "exit_hours": trading.exit_hours,
        "trailing_stop.percent": trailing.percent,
        "cascade.prob_min": cascade.prob_min,
        "cascade.prob_max": cascade.prob_max,
        "cascade.drift_lookback_hours": cascade.drift_lookback_hours,
        "cascade.drift_min": cascade.drift_min,
        "cascade.drift_max": cascade.drift_max,
        "cascade.bucket_hours": cascade.bucket_hours,
        "cascade.consistency_min": cascade.consistency_min,
        "cascade.vol_accel_min": cascade.vol_accel_min,
        "cascade.death_window_hours": cascade.death_window_hours,
        "cascade.death_window_min_points": cascade.death_window_min_points,
        "cascade.death_window_min_coverage": cascade.death_window_min_coverage,
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
    if not 0 < trading.exit_hours <= trading.entry_hours_min:
        raise ValueError("entry/exit hours must satisfy 0 < exit_hours <= entry_hours_min")
    if not 0 < trailing.percent < 1:
        raise ValueError("trailing_stop.percent must be between 0 and 1")
    if not 0 < cascade.prob_min < cascade.prob_max < 1:
        raise ValueError("cascade probability bounds must satisfy 0 < prob_min < prob_max < 1")
    if not 0 < cascade.drift_min < cascade.drift_max < 1:
        raise ValueError("cascade drift bounds must satisfy 0 < drift_min < drift_max < 1")
    if cascade.drift_lookback_hours <= 0 or cascade.bucket_hours <= 0:
        raise ValueError("cascade drift lookback and bucket hours must be > 0")
    if cascade.bucket_hours * 2 > cascade.drift_lookback_hours:
        raise ValueError("cascade drift lookback must contain at least two buckets")
    if not 0 < cascade.consistency_min <= 1:
        raise ValueError("cascade.consistency_min must be between 0 and 1")
    if cascade.vol_accel_min < 1:
        raise ValueError("cascade.vol_accel_min must be >= 1")
    if cascade.death_window_hours <= 0 or cascade.death_window_min_points < 2:
        raise ValueError("cascade death window must be > 0 with at least 2 points")
    if not 0 < cascade.death_window_min_coverage <= 1:
        raise ValueError("cascade.death_window_min_coverage must be between 0 and 1")
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
        death_window_min_points=_get_config_value(
            "POLYBOT_DEATH_WINDOW_MIN_POINTS",
            cascade_cfg.get("death_window_min_points"),
            3,
            int
        ),
        death_window_min_coverage=_get_config_value(
            "POLYBOT_DEATH_WINDOW_MIN_COVERAGE",
            cascade_cfg.get("death_window_min_coverage"),
            0.5,
            float
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
