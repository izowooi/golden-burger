"""Polymarket taker fee math (pure).

fee = size * rate * (p * (1 - p)) ** exponent, charged when the fill is TAKER or the
schedule is not taker-only, quantized to 5 dp ROUND_HALF_UP (platform precision).
An unknown schedule returns None — callers must never treat that as zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass(frozen=True)
class FeeSchedule:
    rate: float
    exponent: float
    taker_only: bool
    source: str

    @property
    def is_zero(self) -> bool:
        return self.rate == 0


ZERO = FeeSchedule(0.0, 1.0, True, "fees_disabled")


def _bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def parse_fee_schedule(raw: Any) -> FeeSchedule | None:
    """Parse the fee info we store/see (Gamma market fields or CLOB market-info `fd`).

    Accepted shapes:
      {"feesEnabled": false, ...}                                  -> zero (proven)
      {"feesEnabled": true, "feeSchedule": {"rate","exponent","takerOnly"}}
      {"rate": .., "exponent": .., "takerOnly": ..}
      {"fd": {"r": .., "e": .., "to": ..}}  /  {"r": .., "e": .., "to": ..}
    Anything else (including feesEnabled true without a schedule) -> None (unknown).
    """
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    if "fd" in raw and isinstance(raw["fd"], dict):
        return parse_fee_schedule(raw["fd"])
    enabled = _bool(raw.get("feesEnabled", raw.get("fees_enabled")))
    sched = raw.get("feeSchedule") or raw.get("fee_schedule")
    if isinstance(sched, dict):
        parsed = parse_fee_schedule(sched)
        if parsed is not None:
            return parsed
    if enabled is False:
        return ZERO
    rate = raw.get("rate", raw.get("r"))
    exponent = raw.get("exponent", raw.get("e"))
    taker_only = _bool(raw.get("takerOnly", raw.get("taker_only", raw.get("to"))))
    if rate is None or exponent is None or taker_only is None:
        return None
    try:
        return FeeSchedule(float(rate), float(exponent), taker_only, "schedule")
    except (TypeError, ValueError):
        return None


def fee_usdc(schedule: FeeSchedule | None, shares: float, price: float, taker: bool = True) -> float | None:
    if schedule is None:
        return None
    if not taker and schedule.taker_only:
        return 0.0
    size, p = Decimal(str(shares)), Decimal(str(price))
    fee = size * Decimal(str(schedule.rate)) * (p * (Decimal(1) - p)) ** int(schedule.exponent) \
        if float(schedule.exponent).is_integer() else \
        size * Decimal(str(schedule.rate)) * Decimal(str(float(p * (1 - p)) ** schedule.exponent))
    return float(fee.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))
