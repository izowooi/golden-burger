"""Fail-closed market filters for the Sports Resolution Hold Live strategy."""

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


def aligned_binary_reason(market: Dict[str, Any]) -> str:
    """Return ``ok`` for an explicit, aligned two-outcome sports market.

    Sports moneyline markets commonly use team names while proposition markets
    use ``Yes``/``No`` and ``negRisk=true``.  Both are binary payout paths.  We
    require two distinct labels, prices and token IDs, plus an explicit boolean
    ``negRisk`` value, but do not infer that labels must literally be Yes/No.
    """
    outcomes = _list_value(market.get("outcomes"))
    prices = _list_value(market.get("outcomePrices"))
    token_ids = _list_value(market.get("clobTokenIds"))
    if outcomes is None or len(outcomes) != 2:
        return "not_two_outcome_labels"
    normalized_outcomes = [str(outcome or "").strip() for outcome in outcomes]
    if any(not outcome for outcome in normalized_outcomes):
        return "empty_outcome_label"
    if len(set(normalized_outcomes)) != 2:
        return "non_distinct_outcome_labels"
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
    if not isinstance(market.get("negRisk"), bool):
        return "neg_risk_unknown"
    return "ok"


def get_strict_binary_yes(market: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility helper for callers that specifically require Yes/No.

    The shape is intentionally stable and test-friendly.  It never infers the
    YES token from array position unless the full strict binary contract first
    succeeds.
    """
    if aligned_binary_reason(market) != "ok":
        return {}
    labels = [str(value).strip() for value in (_list_value(market.get("outcomes")) or [])]
    if labels != ["Yes", "No"]:
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


def get_aligned_binary_outcomes(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return both explicitly aligned labels, prices and CLOB token IDs."""
    if aligned_binary_reason(market) != "ok":
        return []
    labels = _list_value(market.get("outcomes")) or []
    prices = _list_value(market.get("outcomePrices")) or []
    token_ids = _list_value(market.get("clobTokenIds")) or []
    return [
        {
            "outcome": str(label).strip(),
            "probability": float(price),
            "token_id": str(token_id).strip(),
            "token_index": index,
        }
        for index, (label, price, token_id) in enumerate(
            zip(labels, prices, token_ids)
        )
    ]


def get_event_metadata(market: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract stable event metadata without inventing a cross-market event."""
    events = market.get("events") or []
    event = events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else {}
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
    outcomes = get_aligned_binary_outcomes(market)
    if len(outcomes) != 2:
        return None
    labels = [str(item["outcome"]) for item in outcomes]
    prices = [float(item["probability"]) for item in outcomes]
    payouts = dict(zip(labels, prices))
    if prices == [1.0, 0.0]:
        outcome, winner_index, settlement_kind = labels[0], 0, "ONE_HOT"
    elif prices == [0.0, 1.0]:
        outcome, winner_index, settlement_kind = labels[1], 1, "ONE_HOT"
    elif prices == [0.5, 0.5]:
        # Polymarket can settle rare ambiguous/invalid resolutions at 0.5.
        outcome, winner_index, settlement_kind = "VOID", -1, "VOID"
    else:
        return None
    return {
        "outcome": outcome,
        "winner_index": winner_index,
        "settlement_kind": settlement_kind,
        "first_outcome_payout": prices[0],
        # Legacy DB columns are named yes_price_*; this alias means outcome[0].
        "yes_payout": prices[0],
        "payouts_by_outcome": payouts,
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
            candidates = {str(tag.get("slug") or "").lower(), str(tag.get("label") or "").lower()}
        else:
            candidates = {str(tag).lower()}
        if excluded.intersection(candidates):
            return True
    return False


_EXACT_ESPORTS_IDENTITIES = frozenset(
    {"esports", "e-sports", "esport", "e-sport"}
)


def is_exact_esports_market(market: Dict[str, Any]) -> bool:
    """Match e-sports only by explicit normalized identity, never keywords.

    A title containing words such as "sports" is not evidence.  The opt-in
    capability examines exact tag id/slug/label values and explicit category
    or sport identity fields from the market and its parent event.
    """

    identities: set[str] = set()

    def add(value: Any) -> None:
        normalized = str(value or "").strip().lower()
        if normalized:
            identities.add(normalized)

    raw_events = market.get("events") or []
    sources = [market]
    if isinstance(raw_events, list) and raw_events and isinstance(raw_events[0], dict):
        sources.append(raw_events[0])
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("category", "sport", "sportType", "sportsMarketType"):
            add(source.get(key))
        tags = source.get("tags") or []
        for tag in tags if isinstance(tags, list) else []:
            if isinstance(tag, dict):
                for key in ("id", "slug", "label"):
                    add(tag.get(key))
            else:
                add(tag)
    return bool(identities.intersection(_EXACT_ESPORTS_IDENTITIES))


# Sibling-strategy compatibility names.  Papaya does not use keyword heuristics.
is_sports_market = is_excluded_market


def is_sports_category(tags: List, excluded_categories: List[str]) -> bool:
    return is_excluded_market({"tags": tags}, excluded_categories)


def get_high_probability_outcome(
    market: Dict[str, Any], yes_only: bool = True
) -> Dict[str, Any]:
    """Compatibility wrapper that still enforces Papaya's YES-only contract."""
    if not yes_only:
        return {}
    return get_strict_binary_yes(market)


# Compatibility aliases retained for copied tests/callers.  New Tangerine code
# uses the precise aligned-two-outcome names above.
strict_binary_reason = aligned_binary_reason
get_strict_binary_outcomes = get_aligned_binary_outcomes
