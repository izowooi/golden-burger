"""Configuration management for the trading bot (Conviction Ladder)."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union
import math
import os
import yaml
from dotenv import load_dotenv
from polybot_observability.config_contract import (
    get_trading_config_mapping,
    validate_yaml_config_shape,
)


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


def _get_list_config_value(
    env_key: str,
    yaml_value,
    default: List[str]
) -> List[str]:
    """환경변수(comma 구분) > yaml > 기본값 순서로 리스트 설정값 로드.

    env가 빈 문자열이면 빈 리스트 (= 필터 비활성화).
    """
    env_val = os.getenv(env_key)
    if env_val is not None:
        return [item.strip() for item in env_val.split(",") if item.strip()]
    if yaml_value is not None:
        if not isinstance(yaml_value, list) or any(
            not isinstance(item, str) for item in yaml_value
        ):
            raise ValueError(f"{env_key} YAML value must be a list of strings")
        return [item.strip() for item in yaml_value if item.strip()]
    return list(default)


@dataclass
class TrailingStopConfig:
    """트레일링 스탑 설정."""
    enabled: bool = True
    percent: float = 0.05  # 최고점 대비 5% 하락 시 청산


@dataclass
class LadderConfig:
    """시간 사다리 설정: 잔여 시간이 적을수록 높은 확신(가격)을 요구한다.

    6 < h <= h1  : [band1_min, band1_max]
    h1 < h <= h2 : [band2_min, band2_max]
    h2 < h <= h3 : [band3_min, band3_max]
    """
    entry_hours_min: int = 6    # 이 시간 이하 잔여는 진입 금지 (너무 늦음)
    h1: int = 24
    band1_min: float = 0.80
    band1_max: float = 0.95
    h2: int = 72
    band2_min: float = 0.75
    band2_max: float = 0.92
    h3: int = 168
    band3_min: float = 0.70
    band3_max: float = 0.88

    def rungs(self) -> List[Tuple[float, float, float]]:
        """(max_hours, band_min, band_max) 리스트 (잔여 시간 오름차순)."""
        return [
            (float(self.h1), self.band1_min, self.band1_max),
            (float(self.h2), self.band2_min, self.band2_max),
            (float(self.h3), self.band3_min, self.band3_max),
        ]


@dataclass
class MomentumGateConfig:
    """모멘텀 게이트: 하락 추세 favorite 진입 배제."""
    lookback_hours: int = 6
    min_change: float = -0.01  # favorite 가격 변화가 이 값 이상이어야 진입


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 15000.0
    min_volume_24h: float = 5000.0
    take_profit_percent: float = 0.12     # 익절 +12% (목표가 0.99 캡)
    stop_loss_percent: float = -0.08      # 손절 -8%
    max_positions: int = -1               # -1 means unlimited
    reentry_cooldown_hours: float = 24.0  # 재진입 쿨다운
    history_backfill: bool = True         # CLOB prices-history 백필
    exit_hours: int = 2                   # 해결 2시간 이내 청산
    trailing_stop: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    ladder: LadderConfig = field(default_factory=LadderConfig)
    momentum_gate: MomentumGateConfig = field(default_factory=MomentumGateConfig)
    excluded_categories: List[str] = field(default_factory=list)  # 기본 빈 리스트 = 필터 비활성
    yes_only_mode: bool = False           # True: Yes(index 0) 포지션만 매수, No 제외


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
    ladder = trading.ladder
    gate = trading.momentum_gate
    trailing = trading.trailing_stop
    numeric = {
        "buy_amount_usdc": trading.buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "min_volume_24h": trading.min_volume_24h,
        "take_profit_percent": trading.take_profit_percent,
        "stop_loss_percent": trading.stop_loss_percent,
        "max_positions": trading.max_positions,
        "reentry_cooldown_hours": trading.reentry_cooldown_hours,
        "exit_hours": trading.exit_hours,
        "trailing_stop.percent": trailing.percent,
        "ladder.entry_hours_min": ladder.entry_hours_min,
        "ladder.h1": ladder.h1,
        "ladder.h2": ladder.h2,
        "ladder.h3": ladder.h3,
        "ladder.band1_min": ladder.band1_min,
        "ladder.band1_max": ladder.band1_max,
        "ladder.band2_min": ladder.band2_min,
        "ladder.band2_max": ladder.band2_max,
        "ladder.band3_min": ladder.band3_min,
        "ladder.band3_max": ladder.band3_max,
        "momentum_gate.lookback_hours": gate.lookback_hours,
        "momentum_gate.min_change": gate.min_change,
    }
    for name, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
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
    if not 0 < trailing.percent < 1:
        raise ValueError("trailing_stop.percent must be between 0 and 1")
    if not 0 < ladder.entry_hours_min < ladder.h1 < ladder.h2 < ladder.h3:
        raise ValueError("ladder hours must satisfy 0 < entry_hours_min < h1 < h2 < h3")
    if not 0 < trading.exit_hours <= ladder.entry_hours_min:
        raise ValueError("exit_hours must satisfy 0 < exit_hours <= ladder.entry_hours_min")
    bands = [
        (ladder.band1_min, ladder.band1_max),
        (ladder.band2_min, ladder.band2_max),
        (ladder.band3_min, ladder.band3_max),
    ]
    if any(not 0 < lower < upper < 1 for lower, upper in bands):
        raise ValueError("each ladder band must satisfy 0 < band_min < band_max < 1")
    if not (
        ladder.band1_min > ladder.band2_min > ladder.band3_min
        and ladder.band1_max > ladder.band2_max > ladder.band3_max
    ):
        raise ValueError("ladder bands must require decreasing confidence as time increases")
    if gate.lookback_hours <= 0:
        raise ValueError("momentum_gate.lookback_hours must be > 0")
    if not -1 <= gate.min_change <= 1:
        raise ValueError("momentum_gate.min_change must be between -1 and 1")
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
    yes_only_mode: Optional[bool] = None,
) -> BotConfig:
    """Load configuration from YAML file and environment variables.

    Args:
        config_path: Path to config.yaml file
        job_name: Jenkins job name (used for DB path separation)
        env_path: Optional path to .env file
        simulation_mode: Override simulation mode (CLI --simulate flag)
        yes_only_mode: Override yes-only mode (CLI --yes-only flag)

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
            0.05,
            float
        ),
    )

    # Parse ladder config
    ladder_cfg = trading_cfg.get("ladder", {})
    ladder = LadderConfig(
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN", ladder_cfg.get("entry_hours_min"), 6, int
        ),
        h1=_get_config_value("POLYBOT_LADDER_H1", ladder_cfg.get("h1"), 24, int),
        band1_min=_get_config_value("POLYBOT_BAND1_MIN", ladder_cfg.get("band1_min"), 0.80, float),
        band1_max=_get_config_value("POLYBOT_BAND1_MAX", ladder_cfg.get("band1_max"), 0.95, float),
        h2=_get_config_value("POLYBOT_LADDER_H2", ladder_cfg.get("h2"), 72, int),
        band2_min=_get_config_value("POLYBOT_BAND2_MIN", ladder_cfg.get("band2_min"), 0.75, float),
        band2_max=_get_config_value("POLYBOT_BAND2_MAX", ladder_cfg.get("band2_max"), 0.92, float),
        h3=_get_config_value("POLYBOT_LADDER_H3", ladder_cfg.get("h3"), 168, int),
        band3_min=_get_config_value("POLYBOT_BAND3_MIN", ladder_cfg.get("band3_min"), 0.70, float),
        band3_max=_get_config_value("POLYBOT_BAND3_MAX", ladder_cfg.get("band3_max"), 0.88, float),
    )

    # Parse momentum gate config
    momentum_cfg = trading_cfg.get("momentum_gate", {})
    momentum_gate = MomentumGateConfig(
        lookback_hours=_get_config_value(
            "POLYBOT_MOMENTUM_LOOKBACK_HOURS", momentum_cfg.get("lookback_hours"), 6, int
        ),
        min_change=_get_config_value(
            "POLYBOT_MOMENTUM_MIN_CHANGE", momentum_cfg.get("min_change"), -0.01, float
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
            5000.0,
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
        exit_hours=_get_config_value(
            "POLYBOT_EXIT_HOURS",
            trading_cfg.get("exit_hours"),
            2,
            int
        ),
        trailing_stop=trailing_stop,
        ladder=ladder,
        momentum_gate=momentum_gate,
        excluded_categories=_get_list_config_value(
            "POLYBOT_EXCLUDED_CATEGORIES",
            trading_cfg.get("excluded_categories"),
            []
        ),
        yes_only_mode=yes_only_mode if yes_only_mode is not None else _get_bool_config_value(
            "POLYBOT_YES_ONLY",
            trading_cfg.get("yes_only_mode"),
            False
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
