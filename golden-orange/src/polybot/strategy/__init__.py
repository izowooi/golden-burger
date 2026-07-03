"""Trading strategy modules (Fear Spike Fade)."""
from .scanner import MarketScanner
from .trader import Trader
from .filters import (
    is_sports_market,
    passes_liquidity_filter,
    get_no_side,
)
from .signals import (
    PricePoint,
    SignalParams,
    EntrySignal,
    ExitSignal,
    get_window,
    is_window_valid,
    merge_price_points,
    to_price_points,
    compute_base,
    find_spike_start,
    spike_peak_price,
    is_spike_stalled,
    volume_multiple,
    evaluate_entry,
    evaluate_exit,
    take_profit_target,
    retrace_target_price,
)

__all__ = [
    "MarketScanner",
    "Trader",
    "is_sports_market",
    "passes_liquidity_filter",
    "get_no_side",
    "PricePoint",
    "SignalParams",
    "EntrySignal",
    "ExitSignal",
    "get_window",
    "is_window_valid",
    "merge_price_points",
    "to_price_points",
    "compute_base",
    "find_spike_start",
    "spike_peak_price",
    "is_spike_stalled",
    "volume_multiple",
    "evaluate_entry",
    "evaluate_exit",
    "take_profit_target",
    "retrace_target_price",
]
