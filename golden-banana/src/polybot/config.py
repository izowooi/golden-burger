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


@dataclass
class MomentumConfig:
    """모멘텀 전략 설정."""
    enabled: bool = True
    short_window: int = 3      # 15분 = 3 스냅샷 (5분 주기)
    long_window: int = 72      # 6시간 = 72 스냅샷 (5분 주기)
    golden_cross_threshold: float = 0.02   # 골든크로스 임계값 (2%)
    dead_cross_threshold: float = -0.02    # 데드크로스 임계값 (-2%)
    require_positive_long_momentum: bool = True  # 장기 모멘텀 양수 필수


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_threshold: float = 0.85           # 변경: 0.80 → 0.85
    sell_threshold: float = 0.97          # 변경: 0.90 → 0.97
    buy_amount_usdc: float = 10.0
    min_liquidity: float = 50000.0        # 변경: 100000 → 50000
    max_positions: int = -1               # -1 means unlimited
    take_profit_percent: float = 0.07     # 신규: 이익실현 +7%
    stop_loss_percent: float = -0.10      # 신규: 손절 -10%
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    excluded_categories: List[str] = field(default_factory=lambda: [
        "Sports", "sports", "NFL", "NBA", "MLB", "NHL",
        "Soccer", "Football", "Basketball", "Baseball"
    ])


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
    momentum_cfg = trading_cfg.get("momentum", {})

    # Parse momentum config
    momentum = MomentumConfig(
        enabled=_get_bool_config_value(
            "POLYBOT_MOMENTUM_ENABLED",
            momentum_cfg.get("enabled"),
            True
        ),
        short_window=_get_config_value(
            "POLYBOT_MOMENTUM_SHORT_WINDOW",
            momentum_cfg.get("short_window"),
            3,
            int
        ),
        long_window=_get_config_value(
            "POLYBOT_MOMENTUM_LONG_WINDOW",
            momentum_cfg.get("long_window"),
            72,
            int
        ),
        golden_cross_threshold=_get_config_value(
            "POLYBOT_GOLDEN_CROSS_THRESHOLD",
            momentum_cfg.get("golden_cross_threshold"),
            0.02,
            float
        ),
        dead_cross_threshold=_get_config_value(
            "POLYBOT_DEAD_CROSS_THRESHOLD",
            momentum_cfg.get("dead_cross_threshold"),
            -0.02,
            float
        ),
        require_positive_long_momentum=_get_bool_config_value(
            "POLYBOT_REQUIRE_POSITIVE_LONG_MOMENTUM",
            momentum_cfg.get("require_positive_long_momentum"),
            True
        ),
    )

    trading = TradingConfig(
        buy_threshold=_get_config_value(
            "POLYBOT_BUY_THRESHOLD",
            trading_cfg.get("buy_threshold"),
            0.85,
            float
        ),
        sell_threshold=_get_config_value(
            "POLYBOT_SELL_THRESHOLD",
            trading_cfg.get("sell_threshold"),
            0.97,
            float
        ),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT",
            trading_cfg.get("buy_amount_usdc"),
            10.0,
            float
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY",
            trading_cfg.get("min_liquidity"),
            50000.0,
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
            0.07,
            float
        ),
        stop_loss_percent=_get_config_value(
            "POLYBOT_STOP_LOSS",
            trading_cfg.get("stop_loss_percent"),
            -0.10,
            float
        ),
        momentum=momentum,
        excluded_categories=trading_cfg.get("excluded_categories", [
            "Sports", "sports", "NFL", "NBA", "MLB", "NHL",
            "Soccer", "Football", "Basketball", "Baseball"
        ]),
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
