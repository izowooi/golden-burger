"""Daily Rsync uses the same independent RAW proof as native source readers."""

from polybot_observability.market_data_raw_transition import (
    _logical_fingerprint,
    verify_raw_transition,
)

__all__ = ["verify_raw_transition"]
