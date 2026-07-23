"""Fail-closed market filters for the Crown Momentum strategy."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional


def _list_value(value: Any) -> Optional[list]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def strict_binary_reason(market: Dict[str, Any]) -> str:
    """Return ``ok`` only for an exact standard binary Yes/No market."""
    outcomes = _list_value(market.get("outcomes"))
    prices = _list_value(market.get("outcomePrices"))
    token_ids = _list_value(market.get("clobTokenIds"))
    if outcomes != ["Yes", "No"]:
        return "not_standard_yes_no"
    if prices is None or len(prices) != 2:
        return "not_two_outcome_prices"
    if token_ids is None or len(token_ids) != 2:
        return "not_two_token_ids"
    normalized_tokens = [str(token or "").strip() for token in token_ids]
    if any(not token for token in normalized_tokens):
        return "empty_token_id"
    if len(set(normalized_tokens)) != 2:
        return "non_distinct_token_ids"
    try:
        normalized_prices = [float(price) for price in prices]
    except (TypeError, ValueError):
        return "invalid_outcome_price"
    if any(
        not math.isfinite(price) or not 0 <= price <= 1
        for price in normalized_prices
    ):
        return "invalid_outcome_price"
    # Missing negRisk is not treated as False.  The contract must be explicit.
    if market.get("negRisk") is not False:
        return "neg_risk_or_unknown"
    return "ok"


def get_strict_binary_yes(market: Dict[str, Any]) -> Dict[str, Any]:
    """Return canonical YES-side data, or an empty dict when ineligible.

    The shape is intentionally stable and test-friendly.  It never infers the
    YES token from array position unless the full strict binary contract first
    succeeds.
    """
    if strict_binary_reason(market) != "ok":
        return {}
    prices = _list_value(market.get("outcomePrices")) or []
    token_ids = _list_value(market.get("clobTokenIds")) or []
    return {
        "outcome": "Yes",
        "probability": float(prices[0]),
        "token_id": str(token_ids[0]).strip(),
        "token_index": 0,
        "no_probability": float(prices[1]),
        "no_token_id": str(token_ids[1]).strip(),
    }


def get_event_metadata(market: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract stable event metadata without inventing a cross-market event."""
    events = market.get("events") or []
    event = (
        events[0]
        if isinstance(events, list)
        and events
        and isinstance(events[0], dict)
        else {}
    )
    event_id = event.get("id") or market.get("eventId")
    event_slug = event.get("slug") or market.get("eventSlug")
    return {
        "event_id": str(event_id).strip() if event_id not in (None, "") else None,
        "event_slug": str(event_slug).strip() if event_slug not in (None, "") else None,
    }


def get_proven_resolution(
    market: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return payout evidence only for closed markets with final 0/1 prices."""
    if not market or market.get("closed") is not True:
        return None
    yes = get_strict_binary_yes(market)
    if not yes:
        return None
    yes_price = yes["probability"]
    no_price = yes["no_probability"]
    if yes_price == 1.0 and no_price == 0.0:
        outcome, value = "Yes", 1.0
    elif yes_price == 0.0 and no_price == 1.0:
        outcome, value = "No", 0.0
    elif yes_price == 0.5 and no_price == 0.5:
        # Polymarket can settle rare ambiguous/invalid resolutions at 0.5.
        outcome, value = "Ambiguous", 0.5
    else:
        return None
    return {
        "outcome": outcome,
        "yes_payout": value,
        "status": str(market.get("umaResolutionStatus") or "closed_final_prices"),
        "evidence": "gamma_closed_final_outcome_prices",
    }


def passes_liquidity_filter(market: Dict[str, Any], minimum: float) -> bool:
    if "liquidity" not in market:
        return False
    raw = market.get("liquidity")
    if (
        raw is None
        or isinstance(raw, bool)
        or (isinstance(raw, str) and not raw.strip())
    ):
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value >= 0 and value >= minimum


def passes_volume_filter(market: Dict[str, Any], minimum: float) -> bool:
    if "volume24hr" not in market:
        return False
    raw = market.get("volume24hr")
    if (
        raw is None
        or isinstance(raw, bool)
        or (isinstance(raw, str) and not raw.strip())
    ):
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value >= 0 and value >= minimum


def is_excluded_market(market: Dict[str, Any], categories: List[str]) -> bool:
    """Apply only explicitly configured category/tag exclusions."""
    if not categories:
        return False
    excluded = {category.strip().lower() for category in categories}
    tags = market.get("tags") or []
    for tag in tags if isinstance(tags, list) else []:
        if isinstance(tag, dict):
            candidates = {
                str(tag.get("slug") or "").lower(),
                str(tag.get("label") or "").lower(),
            }
        else:
            candidates = {str(tag).lower()}
        if excluded.intersection(candidates):
            return True
    return False


def get_high_probability_outcome(
    market: Dict[str, Any], yes_only: bool = True
) -> Dict[str, Any]:
    """Compatibility wrapper that still enforces Queen's YES-only contract."""
    if not yes_only:
        return {}
    return get_strict_binary_yes(market)
