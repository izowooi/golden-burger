"""Golden Pomegranate accountless research collector."""

from .config import BotConfig, TradingConfig, load_config
from .collector import ResearchCollector

__all__ = ["BotConfig", "ResearchCollector", "TradingConfig", "load_config"]
