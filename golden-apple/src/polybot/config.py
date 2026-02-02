"""Configuration management for the trading bot."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import os
import yaml
from dotenv import load_dotenv


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    buy_threshold: float = 0.80
    sell_threshold: float = 0.90
    buy_amount_usdc: float = 10.0
    min_liquidity: float = 100000.0
    max_positions: int = -1  # -1 means unlimited
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

    # Parse trading config
    trading_cfg = cfg.get("trading", {})
    trading = TradingConfig(
        buy_threshold=trading_cfg.get("buy_threshold", 0.80),
        sell_threshold=trading_cfg.get("sell_threshold", 0.90),
        buy_amount_usdc=trading_cfg.get("buy_amount_usdc", 10.0),
        min_liquidity=trading_cfg.get("min_liquidity", 100000.0),
        max_positions=trading_cfg.get("max_positions", -1),
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
