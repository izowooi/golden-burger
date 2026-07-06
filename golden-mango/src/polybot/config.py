"""Configuration management for the trading bot (Patience Premium)."""
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

    env가 빈 문자열이면 빈 리스트 (= 필터 비활성화).
    """
    env_val = os.getenv(env_key)
    if env_val is not None:
        return [item.strip() for item in env_val.split(",") if item.strip()]
    if yaml_value is not None:
        return list(yaml_value)
    return list(default)


@dataclass
class CarryConfig:
    """캐리 진입 설정: y = ((1-p)/p) * (8760/h) >= yield_min.

    시간·확률 밴드는 수식이 실질 frontier를 형성하도록 넓게 두고,
    허들(yield_min)이 유일한 실질 필터가 되게 한다.
    """
    yield_min: float = 2.0          # 연환산 캐리 허들 (2.0 = 연 200%)
    prob_min: float = 0.85          # favorite 가격 하한
    prob_max: float = 0.985         # favorite 가격 상한 (스프레드/수수료 여유)
    entry_hours_min: int = 6        # 이 시간 이하 잔여는 진입 금지 (너무 늦음)
    entry_hours_max: int = 336      # 이 시간 초과 잔여는 진입 금지 (14일)


@dataclass
class MomentumGateConfig:
    """모멘텀 가드: 급락 중인 favorite 진입 배제 (새 정보 신호일 수 있음)."""
    lookback_hours: int = 6
    min_change: float = -0.02  # favorite 가격 변화가 이 값 이상이어야 진입


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_amount_usdc: float = 5.0
    min_liquidity: float = 20000.0
    min_volume_24h: float = 0.0           # 0 = 거래량 필터 비활성 (캐리는 유동성만 필수)
    take_profit_percent: float = 9.99     # 사실상 미사용 - 목표가는 0.99 캡으로 고정
    stop_loss_percent: float = -0.06      # 손절 -6% (수렴 실패 신호)
    max_positions: int = -1               # -1 means unlimited
    reentry_cooldown_hours: float = 24.0  # 재진입 쿨다운
    history_backfill: bool = True         # CLOB prices-history 백필
    exit_hours: int = 2                   # 해결 2시간 이내 청산
    carry: CarryConfig = field(default_factory=CarryConfig)
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
    trading_cfg = cfg.get("trading", {})

    # Parse carry config
    carry_cfg = trading_cfg.get("carry", {})
    carry = CarryConfig(
        yield_min=_get_config_value(
            "POLYBOT_YIELD_MIN", carry_cfg.get("yield_min"), 2.0, float
        ),
        prob_min=_get_config_value(
            "POLYBOT_PROB_MIN", carry_cfg.get("prob_min"), 0.85, float
        ),
        prob_max=_get_config_value(
            "POLYBOT_PROB_MAX", carry_cfg.get("prob_max"), 0.985, float
        ),
        entry_hours_min=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MIN", carry_cfg.get("entry_hours_min"), 6, int
        ),
        entry_hours_max=_get_config_value(
            "POLYBOT_ENTRY_HOURS_MAX", carry_cfg.get("entry_hours_max"), 336, int
        ),
    )

    # Parse momentum gate config
    momentum_cfg = trading_cfg.get("momentum_gate", {})
    momentum_gate = MomentumGateConfig(
        lookback_hours=_get_config_value(
            "POLYBOT_MOMENTUM_LOOKBACK_HOURS", momentum_cfg.get("lookback_hours"), 6, int
        ),
        min_change=_get_config_value(
            "POLYBOT_MOMENTUM_MIN_CHANGE", momentum_cfg.get("min_change"), -0.02, float
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
            0.0,
            float
        ),
        take_profit_percent=_get_config_value(
            "POLYBOT_TAKE_PROFIT",
            trading_cfg.get("take_profit_percent"),
            9.99,
            float
        ),
        stop_loss_percent=_get_config_value(
            "POLYBOT_STOP_LOSS",
            trading_cfg.get("stop_loss_percent"),
            -0.06,
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
        carry=carry,
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
