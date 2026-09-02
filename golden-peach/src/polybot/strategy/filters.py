"""Fail-closed whole-game soccer result-market filters for Golden Peach."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Mapping, Optional


REGULATION_SCOPE_CLAUSE = (
    "this market refers only to the outcome within the first 90 minutes "
    "of regular play plus stoppage time"
)
_NON_WHOLE_GAME_MARKET = re.compile(
    r"\b(?:first|1st|second|2nd|third|3rd|fourth|4th)\s+"
    r"(?:half|quarter|period|inning)|\b(?:spread|handicap|total|over/under|"
    r"draw no bet|advance(?:ment)?|qualify|penalt(?:y|ies)|corners?|shots?|"
    r"goalscorer|touchdowns?|runs?|puck line|run line|futures?|season[- ]long|"
    r"championship winner|conference winner|division winner|"
    r"win (?:the )?(?:championship|league|division|conference))\b",
    re.IGNORECASE,
)


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


def _normalized_name(value: Any) -> str:
    if value in (None, ""):
        return ""
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in str(value)
        )
        .casefold()
        .split()
    )


def _team_forms(team: Mapping[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            _normalized_name(team.get("name")),
            _normalized_name(team.get("alias")),
            _normalized_name(team.get("abbreviation")),
        )
        if normalized
    }


def aligned_binary_reason(market: Dict[str, Any]) -> str:
    """Validate exact two-token alignment for the configured sport family."""
    outcomes = _list_value(market.get("outcomes"))
    prices = _list_value(market.get("outcomePrices"))
    token_ids = _list_value(market.get("clobTokenIds"))
    labels = [str(item).strip() for item in outcomes] if outcomes is not None else []
    if len(labels) != 2 or any(not label for label in labels):
        return "not_two_nonempty_outcome_labels"
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
    family = str(market.get("sportFamily") or "soccer").strip().lower()
    if family == "soccer":
        if labels != ["Yes", "No"]:
            return "not_exact_yes_no_labels"
        if market.get("negRisk") is not True:
            return "not_explicit_negrisk_result_market"
    elif family in {"mlb", "nba", "nfl", "nhl"}:
        if labels == ["Yes", "No"]:
            return "direct_team_labels_required"
        if market.get("negRisk") is not False:
            return "direct_moneyline_negrisk_false_required"
    else:
        return "unsupported_sport_family"
    return "ok"


def get_event(market: Dict[str, Any]) -> Dict[str, Any]:
    events = market.get("events") or []
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], dict):
        return {}
    return events[0]


def settlement_scope_reason(market: Mapping[str, Any]) -> str:
    """Prove that extra time and shoot-outs are outside the payout contract."""
    description = " ".join(
        str(market.get("description") or "").casefold().split()
    )
    if not description:
        return "settlement_description_missing"
    if REGULATION_SCOPE_CLAUSE not in description:
        return "settlement_scope_unproven"
    scope_groups = (
        ("extra time",),
        ("penalty shoot-outs", "penalty shoot-out", "penalty shootouts", "penalty shootout", "penalties", "penalty"),
    )
    for clause in re.split(r"[.;]", description):
        mentioned_groups = [
            aliases for aliases in scope_groups if any(alias in clause for alias in aliases)
        ]
        if not mentioned_groups:
            continue
        # A negated exclusion (for example ``extra time is not excluded``) is
        # inclusion evidence, not an exclusion.  Reject it before looking for
        # otherwise valid exclusion phrases.
        if re.search(
            r"\b(?:not|never)\s+(?:explicitly\s+)?"
            r"(?:exclude|excluded|excluding|outside)\b",
            clause,
        ):
            return "settlement_scope_contradictory"
        masked = clause
        for negative in (
            "does not include",
            "do not include",
            "not included",
            "does not count",
            "do not count",
            "not count",
            "does not apply",
            "do not apply",
        ):
            masked = masked.replace(negative, " excluded ")
        if re.search(
            r"\b(include|includes|included|including|count|counts|counted|"
            r"counting|apply|applies|applied)\b",
            masked,
        ):
            return "settlement_scope_contradictory"
        for aliases in mentioned_groups:
            alias_pattern = "(?:" + "|".join(
                re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
            ) + ")"
            explicitly_excluded = any(
                re.search(pattern, clause)
                for pattern in (
                    rf"\b{alias_pattern}(?:\s+(?:and|or)\s+[a-z -]+)?\s+"
                    rf"(?:is|are|will be|shall be)\s+(?:explicitly\s+)?excluded\b",
                    rf"\b{alias_pattern}(?:\s+(?:and|or)\s+[a-z -]+)?\s+"
                    rf"(?:does|do|will|shall)\s+not\s+(?:count|apply)\b",
                    rf"\b{alias_pattern}\s+(?:is|are)\s+not\s+included\b",
                    rf"\b{alias_pattern}\s+(?:is|are)\s+outside\b",
                    rf"\b(?:excluding|exclude|excludes)\b[^.;]{{0,80}}\b{alias_pattern}\b",
                    rf"\b(?:does|do)\s+not\s+include\b[^.;]{{0,80}}\b{alias_pattern}\b",
                    rf"\bwithout\b[^.;]{{0,80}}\b{alias_pattern}\b",
                    rf"\bneither\b[^.;]{{0,80}}\b{alias_pattern}\b[^.;]{{0,80}}\b"
                    rf"(?:counts?|applies?|is included|are included)\b",
                )
            )
            if not explicitly_excluded:
                return "settlement_scope_contradictory"
    return "ok"


def _is_exact_draw_descriptor(
    descriptor: str,
    home_forms: set[str],
    away_forms: set[str],
) -> bool:
    if descriptor in {"draw", "tie"}:
        return True
    exact = {
        f"{prefix} {home} {separator} {away}"
        for prefix in ("draw", "tie")
        for separator in ("vs", "v")
        for home in home_forms
        for away in away_forms
    }
    return descriptor in exact


def match_result_reason(market: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """Return ``(ok, HOME|DRAW|AWAY)`` only for whole-match result propositions."""
    reason = aligned_binary_reason(market)
    if reason != "ok":
        return reason, None
    if str(market.get("sportsMarketType") or "").strip() != "moneyline":
        return "not_top_level_moneyline", None
    if market.get("parentMarketId") not in (None, ""):
        return "child_market_excluded", None
    if any(
        market.get(key) is True
        for key in (
            "isFuture", "future", "isProp", "prop", "isAdvancement", "advancement"
        )
    ):
        return "prop_future_or_advancement_excluded", None
    identity_text = " ".join(
        _normalized_name(market.get(field))
        for field in ("groupItemTitle", "question", "slug")
    )
    if "draw no bet" in identity_text or re.search(r"\bdnb\b", identity_text):
        return "draw_no_bet_excluded", None
    if _NON_WHOLE_GAME_MARKET.search(identity_text) or re.search(r"\bdnb\b", identity_text):
        return "non_whole_game_or_prop_excluded", None
    event = get_event(market)
    if not event:
        return "event_relation_not_unique", None
    if event.get("parentEventId") not in (None, ""):
        return "child_event_not_whole_match", None
    raw_teams = event.get("teams")
    teams = (
        [dict(item) for item in raw_teams if isinstance(item, Mapping)]
        if isinstance(raw_teams, list)
        else []
    )
    if len(teams) != 2:
        return "exactly_two_teams_required", None
    home_forms, away_forms = _team_forms(teams[0]), _team_forms(teams[1])
    if not home_forms or not away_forms or home_forms & away_forms:
        return "team_identity_ambiguous", None
    family = str(market.get("sportFamily") or "soccer").strip().lower()
    if family in {"mlb", "nba", "nfl", "nhl"}:
        labels = [
            _normalized_name(item)
            for item in (_list_value(market.get("outcomes")) or [])
        ]
        matched = []
        for label in labels:
            matches = [
                index
                for index, forms in enumerate((home_forms, away_forms))
                if label in forms
            ]
            matched.append(matches[0] if len(matches) == 1 else -1)
        if sorted(matched) != [0, 1]:
            return "direct_outcomes_not_exact_teams", None
        return "ok", "DIRECT_TWO_TEAM"
    if family != "soccer":
        return "unsupported_sport_family", None
    scope_reason = settlement_scope_reason(market)
    if scope_reason != "ok":
        return scope_reason, None
    descriptor = _normalized_name(market.get("groupItemTitle"))
    if not descriptor:
        return "group_item_title_missing", None
    if _is_exact_draw_descriptor(descriptor, home_forms, away_forms):
        return "ok", "DRAW"
    if descriptor in home_forms:
        return "ok", "HOME"
    if descriptor in away_forms:
        return "ok", "AWAY"
    return "result_proposition_not_identified", None


def get_match_result_yes(market: Dict[str, Any]) -> Dict[str, Any]:
    reason, result_kind = match_result_reason(market)
    if reason != "ok" or result_kind is None:
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
        "result_kind": result_kind,
    }


def get_match_result_outcomes(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compatibility helper returning the eligible direct winner side."""
    reason, result_kind = match_result_reason(market)
    if reason != "ok" or result_kind is None:
        return []
    family = str(market.get("sportFamily") or "soccer").strip().lower()
    if family == "soccer":
        selected = get_match_result_yes(market)
        return [selected] if selected else []
    labels = _list_value(market.get("outcomes")) or []
    prices = _list_value(market.get("outcomePrices")) or []
    token_ids = _list_value(market.get("clobTokenIds")) or []
    event = get_event(market)
    teams = [
        dict(item) for item in event.get("teams", []) if isinstance(item, Mapping)
    ] if isinstance(event.get("teams"), list) else []
    forms = [_team_forms(team) for team in teams]
    selected: List[Dict[str, Any]] = []
    for index, (label, price, token_id) in enumerate(zip(labels, prices, token_ids)):
        normalized = _normalized_name(label)
        matches = [team_index for team_index, values in enumerate(forms) if normalized in values]
        if len(matches) != 1:
            return []
        selected.append(
            {
                "outcome": str(label).strip(),
                "probability": float(price),
                "token_id": str(token_id).strip(),
                "token_index": index,
                "result_kind": "HOME" if matches[0] == 0 else "AWAY",
            }
        )
    return selected


def get_match_result_sides(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return every directly tradable whole-game winner token.

    Soccer contributes direct YES and NO books for each HOME/DRAW/AWAY
    proposition. MLB/NBA/NFL/NHL contribute the two team-labelled tokens from
    their single top-level moneyline. No synthetic complement is created.
    """
    reason, result_kind = match_result_reason(market)
    if reason != "ok" or result_kind is None:
        return []
    family = str(market.get("sportFamily") or "soccer").strip().lower()
    if family != "soccer":
        direct = get_match_result_outcomes(market)
        if len(direct) != 2 or {
            str(item.get("result_kind")) for item in direct
        } != {"HOME", "AWAY"}:
            return []
        return [
            {
                **item,
                "outcome_side": "DIRECT",
                "candidate_kind": f"DIRECT_{item['result_kind']}",
                "yes_probability": float(item["probability"]),
            }
            for item in direct
        ]
    if result_kind not in {"HOME", "DRAW", "AWAY"}:
        return []
    aligned = get_aligned_binary_outcomes(market)
    if [item.get("outcome") for item in aligned] != ["Yes", "No"]:
        return []
    yes_probability = float(aligned[0]["probability"])
    return [
        {
            **item,
            "outcome_side": str(item["outcome"]).upper(),
            "result_kind": result_kind,
            "candidate_kind": f"{str(item['outcome']).upper()}_{result_kind}",
            "yes_probability": yes_probability,
        }
        for item in aligned
    ]


def get_strict_binary_yes(market: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility alias with the stricter whole-match result contract."""
    return get_match_result_yes(market)


def get_aligned_binary_outcomes(market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return both exact aligned payout paths for settlement proof."""
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
    event = get_event(market)
    event_id = event.get("id") or market.get("eventId")
    event_slug = event.get("slug") or market.get("eventSlug")
    return {
        "event_id": str(event_id).strip() if event_id not in (None, "") else None,
        "event_slug": str(event_slug).strip() if event_slug not in (None, "") else None,
    }


def get_proven_resolution(
    market: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return payout evidence only for a closed exact Yes/No market."""
    if not market or market.get("closed") is not True:
        return None
    outcomes = get_aligned_binary_outcomes(market)
    if len(outcomes) != 2:
        return None
    labels = [str(item["outcome"]) for item in outcomes]
    prices = [float(item["probability"]) for item in outcomes]
    payouts = dict(zip(labels, prices))
    if prices == [1.0, 0.0]:
        outcome, winner_index = labels[0], 0
    elif prices == [0.0, 1.0]:
        outcome, winner_index = labels[1], 1
    else:
        # A closed market with 0.5/0.5 (or any non-one-hot pair) is not a
        # terminal payout.  Recording it as RESOLVED would prematurely release
        # capacity and invent a settlement value.
        return None
    return {
        "outcome": outcome,
        "winner_index": winner_index,
        "first_outcome_payout": prices[0],
        "yes_payout": prices[0],
        "payouts_by_outcome": payouts,
        "status": str(market.get("umaResolutionStatus") or "closed_final_prices"),
        "evidence": "gamma_closed_final_outcome_prices",
    }


def passes_liquidity_filter(market: Dict[str, Any], minimum: float) -> bool:
    raw = market.get("liquidity")
    if raw is None or isinstance(raw, bool):
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value >= minimum


def passes_volume_filter(market: Dict[str, Any], minimum: float) -> bool:
    raw = market.get("volume24hr")
    if raw is None or isinstance(raw, bool):
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value >= minimum


def is_excluded_market(market: Dict[str, Any], categories: List[str]) -> bool:
    return bool(categories)


is_sports_market = is_excluded_market


def is_sports_category(tags: List, excluded_categories: List[str]) -> bool:
    return bool(excluded_categories)


def get_high_probability_outcome(
    market: Dict[str, Any], yes_only: bool = True
) -> Dict[str, Any]:
    outcomes = get_match_result_outcomes(market) if yes_only else []
    return max(outcomes, key=lambda item: item["probability"]) if outcomes else {}


strict_binary_reason = aligned_binary_reason
get_strict_binary_outcomes = get_aligned_binary_outcomes
