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
    """환경변수 > yaml > active 순서로 봇 수명주기 모드를 로드."""
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


@dataclass
class TrailingStopConfig:
    """트레일링 스탑 설정."""
    enabled: bool = True
    percent: float = 0.05  # 최고점 대비 5% 하락 시 청산


@dataclass
class TimeBasedConfig:
    """시간 기반 진입/청산 설정."""
    enabled: bool = True
    entry_hours_max: int = 120  # 기준시각 120시간 이내 진입
    entry_hours_min: int = 0    # 기준시각 전 모든 양수 시간 허용
    exit_hours: int = 0         # endDate 기반 시간 청산 비활성화


@dataclass
class GameStartConfig:
    """스포츠 시장의 경기 시작시각 기반 진입 안전장치."""
    enabled: bool = True
    entry_buffer_minutes: int = 5
    reject_sports_without_game_start: bool = True


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_threshold: float = 0.75           # 75% 이상
    sell_threshold: float = 0.92          # 92% 이하
    buy_amount_usdc: float = 5.0
    max_buy_amount_usdc: float = 100.0
    min_liquidity: float = 50000.0
    max_order_liquidity_ratio: float = 0.002  # 주문금액 <= 유동성의 0.2%
    max_positions: int = 100
    max_open_notional_usdc: float = 5000.0
    max_new_positions_per_cycle: int = 5
    take_profit_percent: float = 0.15     # 이익실현 +15%
    stop_loss_percent: float = -0.08      # 손절 -8%
    trailing_stop: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    time_based: TimeBasedConfig = field(default_factory=TimeBasedConfig)
    game_start: GameStartConfig = field(default_factory=GameStartConfig)
    excluded_categories: List[str] = field(default_factory=lambda: [
        "Sports", "sports", "NFL", "NBA", "MLB", "NHL",
        "Soccer", "Football", "Basketball", "Baseball"
    ])
    yes_only_mode: bool = False           # True: Yes(1위) 포지션만 매수, No 제외
    lifecycle_mode: str = "active"

    @property
    def effective_min_liquidity(self) -> float:
        """Return the stricter static/dynamic liquidity requirement."""
        return max(
            self.min_liquidity,
            self.buy_amount_usdc / self.max_order_liquidity_ratio,
        )


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
    trailing = trading.trailing_stop
    timing = trading.time_based
    game_start = trading.game_start
    numeric = {
        "buy_threshold": trading.buy_threshold,
        "sell_threshold": trading.sell_threshold,
        "buy_amount_usdc": trading.buy_amount_usdc,
        "max_buy_amount_usdc": trading.max_buy_amount_usdc,
        "min_liquidity": trading.min_liquidity,
        "max_order_liquidity_ratio": trading.max_order_liquidity_ratio,
        "max_positions": trading.max_positions,
        "max_open_notional_usdc": trading.max_open_notional_usdc,
        "max_new_positions_per_cycle": trading.max_new_positions_per_cycle,
        "take_profit_percent": trading.take_profit_percent,
        "stop_loss_percent": trading.stop_loss_percent,
        "trailing_stop.percent": trailing.percent,
        "entry_hours_min": timing.entry_hours_min,
        "entry_hours_max": timing.entry_hours_max,
        "exit_hours": timing.exit_hours,
        "game_start.entry_buffer_minutes": game_start.entry_buffer_minutes,
    }
    for name, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if not 0 < trading.buy_threshold < trading.sell_threshold < 1:
        raise ValueError("buy_threshold must be < sell_threshold and both must be between 0 and 1")
    if trading.buy_amount_usdc <= 0:
        raise ValueError("buy_amount_usdc must be > 0")
    if trading.max_buy_amount_usdc <= 0:
        raise ValueError("max_buy_amount_usdc must be > 0")
    if trading.buy_amount_usdc > trading.max_buy_amount_usdc:
        raise ValueError(
            "buy_amount_usdc must be <= max_buy_amount_usdc; "
            "raise both values explicitly for a deliberate scale-up"
        )
    if trading.min_liquidity < 0:
        raise ValueError("min_liquidity must be >= 0")
    if not 0 < trading.max_order_liquidity_ratio <= 1:
        raise ValueError("max_order_liquidity_ratio must be > 0 and <= 1")
    if trading.max_positions <= 0:
        raise ValueError("max_positions must be a positive integer; unlimited is unsafe")
    if trading.max_open_notional_usdc < trading.buy_amount_usdc:
        raise ValueError(
            "max_open_notional_usdc must be >= buy_amount_usdc"
        )
    if not 0 < trading.max_new_positions_per_cycle <= trading.max_positions:
        raise ValueError(
            "max_new_positions_per_cycle must be > 0 and <= max_positions"
        )
    if not 0 < trading.take_profit_percent <= 10:
        raise ValueError("take_profit_percent must be > 0 and <= 10")
    if not -1 < trading.stop_loss_percent < 0:
        raise ValueError("stop_loss_percent must be between -1 and 0")
    if not 0 < trailing.percent < 1:
        raise ValueError("trailing_stop.percent must be between 0 and 1")
    if timing.enabled and not (
        0 <= timing.entry_hours_min < timing.entry_hours_max
        and 0 <= timing.exit_hours < timing.entry_hours_max
    ):
        raise ValueError(
            "enabled time_based windows must satisfy "
            "0 <= entry_hours_min < entry_hours_max and "
            "0 <= exit_hours < entry_hours_max"
        )
    if not 0 <= game_start.entry_buffer_minutes <= 1440:
        raise ValueError(
            "game_start.entry_buffer_minutes must be between 0 and 1440"
        )
    if not isinstance(trading.excluded_categories, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in trading.excluded_categories
    ):
        raise ValueError("excluded_categories must be a list of non-empty strings")
    if trading.lifecycle_mode not in LIFECYCLE_MODES:
        raise ValueError(
            "lifecycle_mode must be one of: active, close_only, archive_only"
        )
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

    # Parse time-based config
    time_based_cfg = trading_cfg.get("time_based", {})
    time_based = TimeBasedConfig(
        enabled=_get_bool_config_value(
            "POLYBOT_TIME_BASED_ENABLED",
            time_based_cfg.get("enabled"),
            True
        ),
        entry_hours_max=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MAX",
            time_based_cfg.get("entry_hours_max"),
            120,
            int
        ),
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN",
            time_based_cfg.get("entry_hours_min"),
            0,
            int
        ),
        exit_hours=_get_config_value(
            "POLYBOT_EXIT_HOURS",
            time_based_cfg.get("exit_hours"),
            0,
            int
        ),
    )

    # Parse sports game-start guard. Sports use gameStartTime as their entry
    # deadline because Gamma endDate can be a later settlement/catalog date.
    game_start_cfg = trading_cfg.get("game_start", {})
    game_start = GameStartConfig(
        enabled=_get_bool_config_value(
            "POLYBOT_GAME_START_FILTER_ENABLED",
            game_start_cfg.get("enabled"),
            True,
        ),
        entry_buffer_minutes=_get_config_value(
            "POLYBOT_GAME_START_BUFFER_MINUTES",
            game_start_cfg.get("entry_buffer_minutes"),
            5,
            int,
        ),
        reject_sports_without_game_start=_get_bool_config_value(
            "POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START",
            game_start_cfg.get("reject_sports_without_game_start"),
            True,
        ),
    )

    trading = TradingConfig(
        buy_threshold=_get_config_value(
            "POLYBOT_BUY_THRESHOLD",
            trading_cfg.get("buy_threshold"),
            0.75,
            float
        ),
        sell_threshold=_get_config_value(
            "POLYBOT_SELL_THRESHOLD",
            trading_cfg.get("sell_threshold"),
            0.92,
            float
        ),
        buy_amount_usdc=_get_config_value(
            "POLYBOT_BUY_AMOUNT",
            trading_cfg.get("buy_amount_usdc"),
            5.0,
            float
        ),
        max_buy_amount_usdc=_get_config_value(
            "POLYBOT_MAX_BUY_AMOUNT_USDC",
            trading_cfg.get("max_buy_amount_usdc"),
            100.0,
            float,
        ),
        min_liquidity=_get_config_value(
            "POLYBOT_MIN_LIQUIDITY",
            trading_cfg.get("min_liquidity"),
            50000.0,
            float
        ),
        max_order_liquidity_ratio=_get_config_value(
            "POLYBOT_MAX_ORDER_LIQUIDITY_RATIO",
            trading_cfg.get("max_order_liquidity_ratio"),
            0.002,
            float,
        ),
        max_positions=_get_config_value(
            "POLYBOT_MAX_POSITIONS",
            trading_cfg.get("max_positions"),
            100,
            int
        ),
        max_open_notional_usdc=_get_config_value(
            "POLYBOT_MAX_OPEN_NOTIONAL_USDC",
            trading_cfg.get("max_open_notional_usdc"),
            5000.0,
            float,
        ),
        max_new_positions_per_cycle=_get_config_value(
            "POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE",
            trading_cfg.get("max_new_positions_per_cycle"),
            5,
            int,
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
        trailing_stop=trailing_stop,
        time_based=time_based,
        game_start=game_start,
        excluded_categories=trading_cfg.get("excluded_categories", [
            "Sports", "sports", "NFL", "NBA", "MLB", "NHL",
            "Soccer", "Football", "Basketball", "Baseball"
        ]),
        yes_only_mode=yes_only_mode if yes_only_mode is not None else _get_bool_config_value(
            "POLYBOT_YES_ONLY",
            trading_cfg.get("yes_only_mode"),
            False
        ),
        lifecycle_mode=_get_lifecycle_mode(
            trading_cfg.get("lifecycle_mode")
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
