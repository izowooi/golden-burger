"""In-play match-winner exact-book counterfactual collector."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping
from uuid import uuid4

from .api.clob_client import (
    ClobClient,
    normalized_levels,
    walk_asks,
    walk_bids,
    walk_bids_partial,
)
from .api.gamma_client import GammaClient
from .api.sports_client import SportsClockClient, SportsClockUpdate
from .config import BotConfig, GammaConfig
from .db.repository import ResearchRepository
from .league_classifier import LeagueClassification, classify_sports_event
from .utils.retry import canonical_json, iso_utc


# Stable event-level rejection vocabulary used by reports and the repository
# contract verifier: ESPORTS_EXCLUDED, LEAGUE_NOT_ALLOWED.
REGULATION_SCOPE_CLAUSE = (
    "this market refers only to the outcome within the first 90 minutes "
    "of regular play plus stoppage time"
)
MAX_IN_PLAY_HOURS = {"soccer": 4.0, "mlb": 8.0, "nhl": 5.0}
_NON_WHOLE_GAME_MARKET = re.compile(
    r"\b(?:first|1st|second|2nd|third|3rd|fourth|4th)\s+"
    r"(?:half|quarter|period|inning)|\b(?:spread|handicap|total|over/under|"
    r"draw no bet|advance(?:ment)?|qualify|penalt(?:y|ies)|corners?|shots?|"
    r"goalscorer|touchdowns?|runs?|puck line|run line|futures?|season[- ]long|"
    r"championship winner|conference winner|division winner)\b",
    re.IGNORECASE,
)
# Read-only analyzers and repository discovery still identify this immutable
# v3a rejection reason. v3b never emits it because explicit Draw YES is eligible.
LEGACY_DRAW_EXCLUSION_REASON = "DRAW_OUTCOME_EXCLUDED"
LEGACY_ALIGNED_TWO_TEAM_CLASS = "ALIGNED_TWO_TEAM_MONEYLINE"


def _array(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, list) else None
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _source_elapsed(payload: Mapping[str, Any]) -> tuple[Any | None, str | None]:
    """Extract only source-explicit clock values without wall-time inference."""
    for field in ("elapsed", "clock"):
        raw = payload.get(field)
        if raw in (None, ""):
            continue
        if not isinstance(raw, Mapping):
            return raw, field
        for nested in ("elapsed", "display", "time", "value"):
            value = raw.get(nested)
            if value not in (None, ""):
                return value, f"{field}.{nested}"
        minute = raw.get("minute", raw.get("minutes"))
        second = raw.get("second", raw.get("seconds"))
        if minute not in (None, "") and second not in (None, ""):
            try:
                seconds = float(second)
            except (TypeError, ValueError):
                continue
            if math.isfinite(seconds) and 0 <= seconds < 60:
                return f"{minute}:{seconds:02.0f}", f"{field}.minute_second"
    return None, None


def _result_triad_gap(
    rows: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return exact HOME/DRAW/AWAY identity gaps for one whole-match event."""
    expected = ("HOME", "DRAW", "AWAY")
    counts = Counter(str(row.get("result_kind") or "") for row in rows)
    slot_counts = {slot: counts.get(slot, 0) for slot in sorted(expected)}
    conditions = {
        str(row.get("condition_id") or "").strip()
        for row in rows
        if str(row.get("condition_id") or "").strip()
    }
    tokens = {
        str(row.get("token_id") or "").strip()
        for row in rows
        if str(row.get("token_id") or "").strip()
    }
    complete = (
        slot_counts == {"AWAY": 1, "DRAW": 1, "HOME": 1}
        and len(rows) == 3
        and len(conditions) == 3
        and len(tokens) == 3
    )
    if complete:
        return None
    return {
        "slot_counts": slot_counts,
        "row_count": len(rows),
        "distinct_condition_count": len(conditions),
        "distinct_token_count": len(tokens),
    }


def _result_identity_gap(
    rows: list[Mapping[str, Any]], sport_family: str
) -> dict[str, Any] | None:
    if sport_family == "soccer":
        return _result_triad_gap(rows)
    counts = Counter(str(row.get("result_kind") or "") for row in rows)
    conditions = {
        str(row.get("condition_id") or "").strip()
        for row in rows
        if str(row.get("condition_id") or "").strip()
    }
    tokens = {
        str(row.get("token_id") or "").strip()
        for row in rows
        if str(row.get("token_id") or "").strip()
    }
    complete = (
        sport_family in {"mlb", "nhl"}
        and counts == {"HOME": 1, "AWAY": 1}
        and len(rows) == 2
        and len(conditions) == 1
        and len(tokens) == 2
    )
    if complete:
        return None
    return {
        "sport_family": sport_family,
        "slot_counts": dict(sorted(counts.items())),
        "row_count": len(rows),
        "distinct_condition_count": len(conditions),
        "distinct_token_count": len(tokens),
    }


def _source_clock_evidence(
    event: Mapping[str, Any],
    sports_clock: SportsClockUpdate | None,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    """Prefer WSS time fields, then use explicit same-cycle Gamma fields.

    Both sources are public Polymarket evidence already preserved as raw payloads.
    This deliberately never estimates match time from kickoff wall time.
    """
    websocket_payload = dict(sports_clock.payload) if sports_clock else {}
    websocket_elapsed, websocket_elapsed_field = _source_elapsed(websocket_payload)
    gamma_payload = dict(event)
    gamma_elapsed, gamma_elapsed_field = _source_elapsed(gamma_payload)
    gamma_has_clock = gamma_elapsed is not None or any(
        gamma_payload.get(field) not in (None, "")
        for field in ("period", "score", "gameStatus")
    )

    if sports_clock is not None and websocket_elapsed is not None:
        payload = websocket_payload
        source = "POLYMARKET_SPORTS_WEBSOCKET"
        elapsed_raw = websocket_elapsed
        elapsed_source_field = websocket_elapsed_field
        received_at = sports_clock.received_at
        selected_slug = sports_clock.slug
        selected_game_id = sports_clock.game_id
    elif gamma_has_clock:
        payload = gamma_payload
        source = "POLYMARKET_GAMMA_EVENT"
        elapsed_raw = gamma_elapsed
        elapsed_source_field = gamma_elapsed_field
        received_at = iso_utc(observed_at)
        selected_slug = str(event.get("slug") or "") or None
        selected_game_id = str(
            event.get("gameId") or event.get("game_id") or ""
        ) or None
    elif sports_clock is not None:
        payload = websocket_payload
        source = "POLYMARKET_SPORTS_WEBSOCKET"
        elapsed_raw = websocket_elapsed
        elapsed_source_field = websocket_elapsed_field
        received_at = sports_clock.received_at
        selected_slug = sports_clock.slug
        selected_game_id = sports_clock.game_id
    else:
        payload = {}
        source = None
        elapsed_raw = None
        elapsed_source_field = None
        received_at = None
        selected_slug = None
        selected_game_id = None

    return {
        "join_status": "OBSERVED" if source else "NOT_OBSERVED",
        "source": source,
        "received_at": received_at,
        "slug": selected_slug,
        "source_slug": str(payload.get("slug") or "") or None,
        "game_id": selected_game_id,
        "league_abbreviation": str(
            payload.get("leagueAbbreviation") or ""
        ) or None,
        "status": str(payload.get("status") or payload.get("gameStatus") or "")
        or None,
        "live": payload.get("live")
        if isinstance(payload.get("live"), bool)
        else None,
        "ended": payload.get("ended")
        if isinstance(payload.get("ended"), bool)
        else None,
        "score": str(payload.get("score") or "") or None,
        "period": str(payload.get("period") or "") or None,
        "elapsed_raw": elapsed_raw,
        "elapsed_source_field": elapsed_source_field,
        "clock_raw": payload.get("clock"),
        "last_update": str(
            payload.get("last_update")
            or payload.get("updatedAt")
            or payload.get("updated_at")
            or ""
        )
        or None,
        "websocket_join_status": (
            "OBSERVED" if sports_clock is not None else "NOT_OBSERVED"
        ),
        "websocket_elapsed_raw": websocket_elapsed,
        "websocket_elapsed_source_field": websocket_elapsed_field,
        "gamma_clock_available": gamma_has_clock,
        "gamma_elapsed_raw": gamma_elapsed,
        "gamma_elapsed_source_field": gamma_elapsed_field,
    }


def _utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _normalized_name(value: Any) -> str:
    if value is None or value == "":
        return ""
    return " ".join(
        "".join(character if character.isalnum() else " " for character in str(value))
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


def _settlement_scope_reason(market: Mapping[str, Any]) -> str | None:
    """Require an explicit regular-time settlement rule before using a market."""
    description = " ".join(
        str(market.get("description") or "").casefold().split()
    )
    if not description:
        return "SETTLEMENT_DESCRIPTION_MISSING"
    if REGULATION_SCOPE_CLAUSE not in description:
        return "SETTLEMENT_SCOPE_UNPROVEN"
    # The canonical ``only ... 90 minutes ... stoppage`` clause is sufficient
    # when extra time is not mentioned.  If it is mentioned elsewhere, every
    # such clause must explicitly exclude it.  This rejects internally
    # contradictory descriptions instead of accepting the first matching
    # substring.
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
        if re.search(
            r"\b(?:not|never)\s+(?:explicitly\s+)?"
            r"(?:exclude|excluded|excluding|outside)\b",
            clause,
        ):
            return "SETTLEMENT_SCOPE_CONTRADICTORY"
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
            return "SETTLEMENT_SCOPE_CONTRADICTORY"
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
                return "SETTLEMENT_SCOPE_CONTRADICTORY"
    return None


def _is_exact_draw_descriptor(
    descriptor: str,
    team_forms: list[set[str]],
) -> bool:
    if descriptor in {"draw", "tie"}:
        return True
    if len(team_forms) != 2:
        return False
    exact = {
        f"{prefix} {home} {separator} {away}"
        for prefix in ("draw", "tie")
        for separator in ("vs", "v")
        for home in team_forms[0]
        for away in team_forms[1]
    }
    return descriptor in exact


def _select_event(
    market: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[str], int]:
    events = [
        item
        for item in (_array(market.get("events")) or [])
        if isinstance(item, Mapping)
    ]
    if len(events) == 1:
        return events[0], [], 1
    return (events[0] if events else {}), ["EVENT_RELATION_NOT_UNIQUE"], len(events)


def classify_soccer_league(
    event: Mapping[str, Any], gamma: GammaConfig
) -> tuple[dict[str, Any], list[str]]:
    """Compatibility wrapper around the exact numeric identity classifier."""
    result = classify_sports_event(event, gamma, "soccer")
    return {
        "sport_family": gamma.sport_family if result.accepted else None,
        "league_code": result.league_code,
        "league_name": result.league_name,
        "series_slug": result.evidence["series_slug"],
        "event_tag_slugs": result.evidence["tag_slugs"],
        "team_leagues": result.evidence["team_leagues"],
        "classification_status": result.status,
        "league_mapping_sha256": result.evidence["league_mapping_sha256"],
    }, list(result.reasons)


def classify_match_winner(
    event: Mapping[str, Any],
    market: Mapping[str, Any],
    labels: list[str],
    tokens: list[str],
    probabilities: list[float | None],
    sport_family: str = "soccer",
) -> tuple[str, tuple[int, ...], dict[str, Any], list[str]]:
    """Fail-closed classifier for whole-match winners, never child games/props."""
    reasons: list[str] = []
    sports_market_type = str(market.get("sportsMarketType") or "").strip()
    if sports_market_type != "moneyline":
        reasons.append("NOT_TOP_LEVEL_MONEYLINE")
    if market.get("parentMarketId") not in (None, ""):
        reasons.append("CHILD_MARKET_EXCLUDED")
    if any(
        market.get(key) is True
        for key in (
            "isFuture", "future", "isProp", "prop", "isAdvancement", "advancement"
        )
    ):
        reasons.append("PROP_FUTURE_OR_ADVANCEMENT_EXCLUDED")
    identity_text = " ".join(
        _normalized_name(market.get(field))
        for field in ("groupItemTitle", "question", "slug")
    )
    if "draw no bet" in identity_text or re.search(r"\bdnb\b", identity_text):
        reasons.append("DRAW_NO_BET_EXCLUDED")
    if _NON_WHOLE_GAME_MARKET.search(identity_text):
        reasons.append("NON_WHOLE_GAME_OR_PROP_EXCLUDED")
    settlement_reason = (
        _settlement_scope_reason(market) if sport_family == "soccer" else None
    )
    if sport_family == "soccer" and settlement_reason is not None:
        reasons.append(settlement_reason)
    aligned = (
        len(labels) == len(tokens) == len(probabilities) == 2
        and all(tokens)
        and len(set(tokens)) == 2
        and all(value is not None and 0 <= value <= 1 for value in probabilities)
    )
    if not aligned:
        reasons.append("NOT_ALIGNED_TWO_OUTCOME")

    raw_teams = event.get("teams")
    teams = (
        [dict(item) for item in raw_teams if isinstance(item, Mapping)]
        if isinstance(raw_teams, list)
        else []
    )
    if len(teams) != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    forms = [_team_forms(team) for team in teams]
    if len(forms) == 2 and (not forms[0] or not forms[1] or forms[0] & forms[1]):
        reasons.append("TEAM_IDENTITY_AMBIGUOUS")

    neg_risk = market.get("negRisk") if isinstance(market.get("negRisk"), bool) else None
    match_class = "REJECTED"
    eligible_indices: tuple[int, ...] = ()
    selected_team_index: int | None = None
    result_kind: str | None = None
    result_kinds_by_index: list[str | None] = [None, None]
    if sport_family == "soccer":
        if labels != ["Yes", "No"]:
            reasons.append("SOCCER_YES_NO_STRUCTURE_REQUIRED")
        if neg_risk is not True:
            reasons.append("NOT_EXPLICIT_NEGRISK_RESULT_MARKET")
    elif sport_family in {"mlb", "nhl"}:
        if labels == ["Yes", "No"]:
            reasons.append("DIRECT_TEAM_LABELS_REQUIRED")
        if neg_risk is not False:
            reasons.append("DIRECT_MONEYLINE_NEGRISK_FALSE_REQUIRED")
    else:
        reasons.append("UNSUPPORTED_SPORT_FAMILY")

    if not reasons and sport_family == "soccer":
        descriptor = _normalized_name(market.get("groupItemTitle"))
        if not descriptor:
            reasons.append("GROUP_ITEM_TITLE_MISSING")
        elif _is_exact_draw_descriptor(descriptor, forms):
            match_class = "NEGRISK_DRAW_YES"
            eligible_indices = (0,)
            result_kind = "DRAW"
        else:
            matched_teams = [
                index for index, candidates in enumerate(forms)
                if descriptor in candidates
            ]
            if len(matched_teams) != 1:
                reasons.append("TEAM_PROPOSITION_NOT_IDENTIFIED")
            else:
                selected_team_index = matched_teams[0]
                match_class = "NEGRISK_TEAM_WIN_YES"
                eligible_indices = (0,)
                result_kind = "HOME" if selected_team_index == 0 else "AWAY"
        if result_kind is not None:
            result_kinds_by_index[0] = result_kind
    elif not reasons:
        matched_indices: list[int] = []
        for index, label in enumerate(labels):
            normalized = _normalized_name(label)
            matches = [
                team_index
                for team_index, candidates in enumerate(forms)
                if normalized in candidates
            ]
            if len(matches) != 1:
                reasons.append("DIRECT_OUTCOMES_NOT_EXACT_TEAMS")
                break
            matched_indices.append(matches[0])
            result_kinds_by_index[index] = "HOME" if matches[0] == 0 else "AWAY"
        if not reasons and sorted(matched_indices) == [0, 1]:
            match_class = "DIRECT_TWO_TEAM_MONEYLINE"
            eligible_indices = (0, 1)
        elif not reasons:
            reasons.append("DIRECT_OUTCOME_TEAM_ALIGNMENT_AMBIGUOUS")

    evidence = {
        "sports_market_type": sports_market_type,
        "neg_risk": neg_risk,
        "group_item_title": str(market.get("groupItemTitle") or "") or None,
        "team_names": [str(team.get("name") or "") for team in teams],
        "team_aliases": [str(team.get("alias") or "") or None for team in teams],
        "outcome_labels": labels,
        "selected_team_index": selected_team_index,
        "result_kind": result_kind,
        "result_kinds_by_index": result_kinds_by_index,
        "sport_family": sport_family,
        "eligible_outcome_indices": list(eligible_indices),
        "settlement_scope": (
            "REGULATION_90_PLUS_STOPPAGE"
            if sport_family == "soccer" and settlement_reason is None
            else ("WHOLE_GAME_DIRECT" if sport_family != "soccer" else "UNPROVEN")
        ),
        "description_sha256": hashlib.sha256(
            str(market.get("description") or "").encode("utf-8")
        ).hexdigest(),
    }
    return match_class, eligible_indices, evidence, reasons


def _fee_rate(market: Mapping[str, Any], fallback: float) -> tuple[float, dict[str, Any]]:
    enabled = _boolean(market.get("feesEnabled"))
    schedule = market.get("feeSchedule")
    schedule_dict = dict(schedule) if isinstance(schedule, Mapping) else {}
    configured = _number(schedule_dict.get("rate"))
    if enabled is False:
        return 0.0, schedule_dict
    if configured is not None and 0 <= configured <= 1:
        return configured, schedule_dict
    return fallback, schedule_dict


def _execution_fee(shares: float, price: float, fee_rate: float) -> float:
    if shares <= 0 or price <= 0 or fee_rate <= 0:
        return 0.0
    return shares * fee_rate * price * (1 - price)


def _stop_policy_key(stop: float) -> str:
    return f"STOP_{stop:.2f}"


def _parse_market(
    market: Mapping[str, Any],
    *,
    event: Mapping[str, Any],
    event_observation_id: str,
    league: LeagueClassification,
    sweep_id: str,
    run_id: str,
    observed_at: datetime,
    config: BotConfig,
    sports_clock: SportsClockUpdate | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    event_id = str(event.get("id") or "")
    condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
    labels = _array(market.get("outcomes")) or []
    tokens = _array(market.get("clobTokenIds") or market.get("clob_token_ids")) or []
    probabilities = _array(market.get("outcomePrices") or market.get("outcome_prices")) or []
    labels = [str(value).strip() for value in labels]
    tokens = [str(value).strip() for value in tokens]
    probability_values = [_number(value) for value in probabilities]
    sport_family = str(league.evidence.get("sport_family") or "soccer")
    match_class, eligible_indices, classification, classification_reasons = (
        classify_match_winner(
            event,
            market,
            labels,
            tokens,
            probability_values,
            sport_family,
        )
    )
    end_date = _utc(market.get("endDate") or market.get("end_date") or event.get("endDate"))
    game_start = _utc(
        market.get("gameStartTime")
        or event.get("startTime")
        or event.get("eventDate")
        or event.get("startDate")
        or event.get("eventStartTime")
        or event.get("gameStartTime")
    )
    hours_until_end = (end_date - observed_at).total_seconds() / 3600 if end_date else None
    liquidity = _number(market.get("liquidityNum", market.get("liquidity")))
    volume = _number(market.get("volumeNum", market.get("volume")))
    active = market.get("active") if isinstance(market.get("active"), bool) else None
    closed = market.get("closed") if isinstance(market.get("closed"), bool) else None
    accepting = (
        market.get("acceptingOrders")
        if isinstance(market.get("acceptingOrders"), bool)
        else None
    )
    book_enabled = (
        market.get("enableOrderBook")
        if isinstance(market.get("enableOrderBook"), bool)
        else None
    )
    neg_risk = market.get("negRisk") if isinstance(market.get("negRisk"), bool) else None
    event_live = event.get("live") if isinstance(event.get("live"), bool) else None
    event_ended = event.get("ended") if isinstance(event.get("ended"), bool) else None
    event_active = event.get("active") if isinstance(event.get("active"), bool) else None
    event_closed = event.get("closed") if isinstance(event.get("closed"), bool) else None
    event_game_status = str(event.get("gameStatus") or "") or None
    clock_evidence = _source_clock_evidence(
        event, sports_clock, observed_at=observed_at
    )
    reasons: list[str] = [*classification_reasons]
    if not league.accepted:
        reasons.append("EVENT_LEAGUE_NOT_ACCEPTED")
    if not event_id or not condition_id:
        reasons.append("MISSING_ID")
    if event.get("parentEventId") not in (None, ""):
        reasons.append("CHILD_EVENT_NOT_WHOLE_MATCH")
    if event_active is not True or event_closed is not False:
        reasons.append("EVENT_NOT_OPEN")
    if active is not True or closed is not False or accepting is not True or book_enabled is not True:
        reasons.append("NOT_OPEN_TRADABLE")
    phase = "UNKNOWN"
    if game_start is None:
        reasons.append("GAME_START_TIME_MISSING")
    elif observed_at < game_start:
        phase = "PRE_GAME"
        reasons.append("NOT_IN_PLAY")
    elif event_live is not True or event_ended is not False:
        phase = "FINISHED" if event_ended is True else "NOT_EXPLICITLY_LIVE"
        reasons.append("EVENT_NOT_EXPLICITLY_IN_PLAY")
    elif (
        (observed_at - game_start).total_seconds() / 3600
        > MAX_IN_PLAY_HOURS[sport_family]
    ):
        phase = "OUTSIDE_IN_PLAY_WINDOW"
        reasons.append("OUTSIDE_IN_PLAY_WINDOW")
    else:
        phase = "IN_PLAY_EXPLICIT"
    fee_rate, fee_schedule = _fee_rate(market, config.trading.experiment.fee_rate_fallback)
    observation_id = uuid4().hex
    market_row = {
        "observation_id": observation_id,
        "event_observation_id": event_observation_id,
        "sweep_id": sweep_id, "run_id": run_id,
        "event_id": event_id, "event_title": str(event.get("title") or "") or None,
        "condition_id": condition_id or None, "market_id": str(market.get("id") or "") or None,
        "question": str(market.get("question") or "") or None,
        "group_item_title": str(market.get("groupItemTitle") or "") or None,
        "sports_market_type": str(market.get("sportsMarketType") or "") or None,
        "observed_at": iso_utc(observed_at),
        "end_date": iso_utc(end_date) if end_date else None,
        "game_start_time": iso_utc(game_start) if game_start else None,
        "hours_until_end": hours_until_end, "sports_phase": phase,
        "event_live": int(event_live) if event_live is not None else None,
        "event_ended": int(event_ended) if event_ended is not None else None,
        "event_game_status": event_game_status,
        "liquidity": liquidity, "volume_total": volume,
        "active": int(active) if active is not None else None,
        "closed": int(closed) if closed is not None else None,
        "accepting_orders": int(accepting) if accepting is not None else None,
        "enable_order_book": int(book_enabled) if book_enabled is not None else None,
        "neg_risk": int(neg_risk) if neg_risk is not None else None,
        "match_winner_class": match_class,
        "eligible_outcome_indices_json": canonical_json(list(eligible_indices)),
        "classification_evidence_json": canonical_json(classification),
        "cadence_arm": config.trading.cadence_arm,
        "fee_rate": fee_rate, "fee_schedule_json": canonical_json(fee_schedule),
        "outcome_labels_json": canonical_json(labels), "token_ids_json": canonical_json(tokens),
        "outcome_prices_json": canonical_json(probability_values),
        "eligible": int(not reasons), "exclusion_reason": "ELIGIBLE" if not reasons else ";".join(reasons),
        "normalized_json": canonical_json({
            "event_id": event_id, "condition_id": condition_id, "labels": labels,
            "tokens": tokens, "probabilities": probability_values,
            "end_date": iso_utc(end_date) if end_date else None,
            "game_start_time": iso_utc(game_start) if game_start else None,
            "liquidity": liquidity, "volume_total": volume,
            "neg_risk": neg_risk, "fee_rate": fee_rate,
            "sports_market_type": market.get("sportsMarketType"),
            "match_winner_class": match_class,
            "result_kind": classification.get("result_kind"),
            "eligible_outcome_indices": list(eligible_indices),
            "event_live": event_live, "event_ended": event_ended,
            "event_game_status": event_game_status,
            "sports_clock": clock_evidence,
        }),
    }
    outcome_rows: list[dict[str, Any]] = []
    if len(labels) == len(tokens) == len(probability_values) == 2 and condition_id and event_id:
        for index, (label, token, probability) in enumerate(zip(labels, tokens, probability_values)):
            outcome_rows.append({
                "outcome_observation_id": uuid4().hex, "market_observation_id": observation_id,
                "sweep_id": sweep_id, "run_id": run_id, "condition_id": condition_id,
                "event_id": event_id, "token_id": token, "outcome_index": index,
                "outcome_label": label, "gamma_probability": probability,
                "entry_eligible": int(index in eligible_indices and not reasons),
                "observed_at": iso_utc(observed_at),
            })
    contextual_outcomes = [
        {
            **row,
            "result_kind": (
                classification.get("result_kinds_by_index", [None, None])[index]
                if index < len(classification.get("result_kinds_by_index", []))
                else None
            ),
        }
        for index, row in enumerate(outcome_rows)
    ]
    context = {
        "market_row": market_row, "outcomes": outcome_rows, "eligible": not reasons,
        "eligible_indices": eligible_indices,
        "match_winner_class": match_class,
        "result_kind": classification.get("result_kind"),
        "sport_family": sport_family,
        "event": event, "market": market, "labels": labels, "tokens": tokens,
        "probabilities": probability_values, "fee_rate": fee_rate,
        "end_date": end_date, "game_start": game_start, "phase": phase,
        "liquidity": liquidity, "volume": volume,
        "event_observation_id": event_observation_id,
        "league_code": league.league_code,
        "league_name": league.league_name,
        "classifier_version": config.trading.classifier_version,
        "league_mapping_sha256": config.trading.league_mapping_sha256,
        "sports_clock": clock_evidence,
    }
    context["outcomes"] = contextual_outcomes
    return market_row, outcome_rows, context


def _event_observation_row(
    event: Mapping[str, Any],
    classification: LeagueClassification,
    *,
    event_observation_id: str,
    sweep_id: str,
    run_id: str,
    source_payload_id: str,
    page_number: int,
    request_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    canonical_event = canonical_json(event)
    fields = classification.event_row_fields()
    return {
        "event_observation_id": event_observation_id,
        "sweep_id": sweep_id,
        "run_id": run_id,
        "source_payload_id": source_payload_id,
        "page_number": page_number,
        "request_id": request_id,
        "observed_at": iso_utc(observed_at),
        "event_id": str(event.get("id") or "MISSING:" + hashlib.sha256(canonical_event.encode()).hexdigest()),
        "event_title": str(event.get("title") or "") or None,
        "event_slug": str(event.get("slug") or "") or None,
        "canonical_event_sha256": hashlib.sha256(canonical_event.encode()).hexdigest(),
        **fields,
        "sport_json": canonical_json(event.get("sport") if isinstance(event.get("sport"), Mapping) else {}),
        "tags_json": canonical_json(event.get("tags") if isinstance(event.get("tags"), list) else []),
        "series_json": canonical_json(event.get("series") if isinstance(event.get("series"), list) else []),
        "teams_json": canonical_json(event.get("teams") if isinstance(event.get("teams"), list) else []),
    }


def _request_envelope(config: BotConfig) -> dict[str, Any]:
    gamma = config.trading.gamma
    return {
        "closed": False,
        "live": True,
        "tag_id": gamma.tag_id,
        "sport_families": list(gamma.sport_families),
        "family_tag_ids": gamma.family_tags,
        "related_tags": gamma.related_tags,
        "sport_family": gamma.sport_family,
        "required_common_tag_ids": list(gamma.required_common_tag_ids),
        "competition_codes": list(gamma.competition_codes),
        "league_mapping_sha256": config.trading.league_mapping_sha256,
        "classifier_version": config.trading.classifier_version,
        "client_sports_market_types": list(gamma.sports_market_types),
        "liquidity_filter": None,
        "volume_filter": None,
        "page_size": gamma.page_size,
        "sports_clock_websocket": config.trading.sports_feed.websocket_url,
        "sports_clock_receive_window_seconds": (
            config.trading.sports_feed.receive_window_seconds
        ),
        "late_entry_minute_floors": list(
            config.trading.experiment.late_entry_minute_floors
        ),
        "notional_ladder_usdc": list(
            config.trading.experiment.notional_ladder_usdc
        ),
    }


class Collector:
    def __init__(
        self,
        config: BotConfig,
        repository: ResearchRepository,
        gamma: GammaClient,
        clob: ClobClient,
        sports_clock: SportsClockClient,
    ) -> None:
        self.config = config
        self.repository = repository
        self.gamma = gamma
        self.clob = clob
        self.sports_clock = sports_clock

    def collect(self, run_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        sweep_id = uuid4().hex
        multi_family_fetch = getattr(self.gamma, "fetch_live_families", None)
        sweep = (
            multi_family_fetch(run_id, observed_at=now)
            if callable(multi_family_fetch)
            else self.gamma.fetch_live_events(run_id, observed_at=now)
        )
        payloads: list[dict[str, Any]] = []
        payload_by_request: dict[str, dict[str, Any]] = {}
        for page in sweep.pages:
            payload = self.repository.payload_row(
                run_id=run_id,
                kind="GAMMA_EVENT_PAGE",
                request_id=page.request_id,
                observed_at=page.received_at,
                raw=page.raw,
            )
            payloads.append(payload)
            payload_by_request[page.request_id] = payload
        source_event_count = sum(len(page.events) for page in sweep.pages)
        source_market_count = sum(
            len(markets)
            for page in sweep.pages
            for event in page.events
            if isinstance((markets := event.get("markets")), list)
        )
        event_rows: list[dict[str, Any]] = []
        classified_events: list[
            tuple[Mapping[str, Any], datetime, LeagueClassification, str]
        ] = []
        for page in sweep.pages:
            observed = _utc(page.received_at) or now
            for event in page.events:
                classification = classify_sports_event(
                    event,
                    self.config.trading.gamma,
                    page.sport_family,
                )
                if not str(event.get("id") or "") and classification.accepted:
                    classification = LeagueClassification(
                        "DRIFT",
                        classification.league_code,
                        classification.league_name,
                        ("EVENT_ID_MISSING",),
                        classification.evidence,
                    )
                event_observation_id = uuid4().hex
                event_rows.append(
                    _event_observation_row(
                        event,
                        classification,
                        event_observation_id=event_observation_id,
                        sweep_id=sweep_id,
                        run_id=run_id,
                        source_payload_id=str(
                            payload_by_request[page.request_id]["payload_id"]
                        ),
                        page_number=page.page_number,
                        request_id=page.request_id,
                        observed_at=observed,
                    )
                )
                classified_events.append(
                    (event, observed, classification, event_observation_id)
                )
        accepted_event_count = sum(row["classification_status"] == "ACCEPTED" for row in event_rows)
        rejected_event_count = sum(row["classification_status"] == "REJECTED" for row in event_rows)
        drift_event_count = sum(row["classification_status"] == "DRIFT" for row in event_rows)
        if not sweep.cursor_complete:
            self.repository.record_collection(
                sweep={
                    "sweep_id": sweep_id,
                    "run_id": run_id,
                    "started_at": iso_utc(now),
                    "completed_at": iso_utc(),
                    "page_count": len(sweep.pages),
                    "event_count": source_event_count,
                    "accepted_event_count": accepted_event_count,
                    "rejected_event_count": rejected_event_count,
                    "drift_event_count": drift_event_count,
                    "source_market_count": source_market_count,
                    "market_count": 0,
                    "eligible_market_count": 0,
                    "eligible_outcome_count": 0,
                    "cursor_complete": 0,
                    "request_envelope_json": canonical_json(_request_envelope(self.config)),
                },
                payloads=payloads,
                events=event_rows,
                markets=(),
                outcomes=(),
                attempts=(),
                snapshots=(),
                levels=(),
                decisions=(),
                episodes=(),
                policies=(),
                paths=(),
                stop_attempts=(),
                stop_exits=(),
            )
            self.repository.record_issue(
                run_id=run_id,
                severity="CRITICAL",
                issue_type="GAMMA_CURSOR_INCOMPLETE",
                detail={"pages": len(sweep.pages)},
            )
            raise RuntimeError(
                "Gamma live sports event keyset sweep exceeded the frozen page cap"
            )

        clock_expected_slugs: set[str] = set()
        clock_expected_soccer_slugs: set[str] = set()
        clock_expected_event_ids: dict[str, str] = {}
        clock_target_games: dict[str, str] = {}
        clock_missing_game_ids: list[str] = []
        clock_game_id_conflicts: list[dict[str, str]] = []
        for event, _observed, classification, _observation_id in classified_events:
            slug = str(event.get("slug") or "").strip()
            if not (
                classification.accepted
                and event.get("parentEventId") in (None, "")
                and event.get("live") is True
                and event.get("ended") is False
                and slug
            ):
                continue
            clock_expected_slugs.add(slug)
            if classification.evidence.get("sport_family") == "soccer":
                clock_expected_soccer_slugs.add(slug)
            clock_expected_event_ids[slug] = str(event.get("id") or "")
            game_id = str(
                event.get("gameId") or event.get("game_id") or ""
            ).strip()
            if not game_id:
                clock_missing_game_ids.append(slug)
                continue
            prior_slug = clock_target_games.get(game_id)
            if prior_slug is not None and prior_slug != slug:
                clock_game_id_conflicts.append(
                    {"game_id": game_id, "first_slug": prior_slug, "slug": slug}
                )
                continue
            clock_target_games[game_id] = slug
        clock_batch = self.sports_clock.collect(run_id, clock_target_games)
        seen_clock_payloads: set[str] = set()
        for raw in clock_batch.matched_raw_messages:
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen_clock_payloads:
                continue
            seen_clock_payloads.add(digest)
            payloads.append(
                self.repository.payload_row(
                    run_id=run_id,
                    kind="SPORTS_CLOCK_UPDATE",
                    request_id=clock_batch.request_id,
                    observed_at=clock_batch.completed_at,
                    raw=raw,
                )
            )

        market_rows: list[dict[str, Any]] = []
        outcome_rows: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        for event, observed, classification, event_observation_id in classified_events:
            if not classification.accepted:
                continue
            event_markets = event.get("markets")
            if not isinstance(event_markets, list):
                continue
            event_relation = dict(event)
            event_relation.pop("markets", None)
            for source_market in event_markets:
                if not isinstance(source_market, Mapping):
                    continue
                row, outcomes, context = _parse_market(
                    dict(source_market),
                    event=event_relation,
                    event_observation_id=event_observation_id,
                    league=classification,
                    sweep_id=sweep_id,
                    run_id=run_id,
                    observed_at=observed,
                    config=self.config,
                    sports_clock=clock_batch.updates.get(
                        str(event_relation.get("slug") or "").strip()
                    ),
                )
                market_rows.append(row)
                outcome_rows.extend(outcomes)
                contexts.append(context)

        source_clock_by_slug: dict[str, dict[str, Any]] = {}
        result_rows_by_event: dict[str, list[dict[str, Any]]] = {}
        sport_family_by_event: dict[str, str] = {}
        for context in contexts:
            slug = str(context["event"].get("slug") or "").strip()
            clock = dict(context["sports_clock"])
            if slug and clock.get("join_status") == "OBSERVED":
                source_clock_by_slug[slug] = clock
            if not context["eligible"]:
                continue
            event_id = str(context["market_row"]["event_id"] or "")
            sport_family_by_event[event_id] = str(context["sport_family"])
            condition_id = str(context["market_row"]["condition_id"] or "")
            for outcome in context["outcomes"]:
                if not outcome["entry_eligible"] or not outcome.get("result_kind"):
                    continue
                result_rows_by_event.setdefault(event_id, []).append(
                    {
                        "result_kind": outcome["result_kind"],
                        "condition_id": condition_id,
                        "token_id": str(outcome["token_id"]),
                    }
                )
        result_triad_gaps = {
            event_id: gap
            for event_id in clock_expected_event_ids.values()
            if (
                gap := _result_identity_gap(
                    result_rows_by_event.get(event_id, []),
                    sport_family_by_event.get(event_id, "unknown"),
                )
            )
            is not None
        }

        open_before = self.repository.open_episodes()
        active_stop_before = self.repository.active_stop_policies()
        tokens = [
            context["tokens"][index]
            for context in contexts
            if context["eligible"]
            for index in context["eligible_indices"]
        ]
        tokens.extend(str(row["token_id"]) for row in open_before)
        books = self.clob.fetch_books(run_id, tokens)
        payloads.extend(
            self.repository.payload_row(run_id=run_id, kind="CLOB_BOOK_BATCH", request_id=item.request_id, observed_at=item.received_at, raw=item.raw)
            for item in books.raw_payloads
        )

        attempt_rows: list[dict[str, Any]] = []
        snapshot_rows: list[dict[str, Any]] = []
        level_rows: list[dict[str, Any]] = []
        snapshot_by_token: dict[str, dict[str, Any]] = {}
        for token, attempt in books.attempts.items():
            attempt_rows.append({
                "attempt_id": uuid4().hex, "run_id": run_id, "token_id": token,
                "status": attempt.status, "request_id": attempt.request_id,
                "observed_at": attempt.received_at, "error_type": attempt.error_type,
                "error_message": attempt.error_message,
            })
        for token, book in books.books.items():
            attempt = books.attempts[token]
            bids = normalized_levels(book, "bids")
            asks = normalized_levels(book, "asks")
            snapshot_id = uuid4().hex
            raw_hash = hashlib.sha256(canonical_json(book).encode()).hexdigest()
            row = {
                "snapshot_id": snapshot_id, "run_id": run_id, "token_id": token,
                "request_id": str(attempt.request_id), "observed_at": str(attempt.received_at),
                "raw_book_sha256": raw_hash,
                "best_bid": bids[0][0] if bids else None, "best_ask": asks[0][0] if asks else None,
                "bid_level_count": len(bids), "ask_level_count": len(asks),
                "source_timestamp": str(book.get("timestamp") or "") or None,
                "tick_size": _number(book.get("tick_size")),
                "min_order_size": _number(book.get("min_order_size")),
            }
            snapshot_rows.append(row)
            snapshot_by_token[token] = row
            for side, levels_for_side in (("BID", bids), ("ASK", asks)):
                for index, (price, size) in enumerate(levels_for_side):
                    level_rows.append({
                        "level_id": uuid4().hex, "snapshot_id": snapshot_id,
                        "side": side, "level_index": index, "price": price, "size": size,
                    })

        existing = self.repository.existing_episode_keys()
        prior_vwaps = self.repository.latest_entry_vwaps()
        decisions: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        policies: list[dict[str, Any]] = []
        experiment = self.config.trading.experiment
        for context in contexts:
            if not context["eligible"]:
                continue
            market_row = context["market_row"]
            for outcome in context["outcomes"]:
                if not outcome["entry_eligible"]:
                    continue
                token = outcome["token_id"]
                snapshot = snapshot_by_token.get(token)
                book = books.books.get(token)
                walk = walk_asks(book, experiment.simulated_notional_usdc) if book else None
                prior_vwap = prior_vwaps.get(str(token))
                for threshold in experiment.entry_thresholds:
                    decision_id = uuid4().hex
                    episode_id: str | None = None
                    provenance: str | None = None
                    status = "NO_FULL_5_USDC_DEPTH"
                    if walk is not None:
                        if now < experiment.start_utc or now >= experiment.entry_end_utc:
                            status = "OUTSIDE_ENTRY_PERIOD"
                        elif (str(market_row["condition_id"]), token, threshold) in existing:
                            status = "EPISODE_ALREADY_EXISTS"
                        elif walk.vwap < threshold:
                            status = "BELOW_ENTRY_THRESHOLD"
                        elif prior_vwap is None:
                            status = "OPENED_FIRST_FULL_DEPTH_ABOVE"
                            provenance = "FIRST_FULL_DEPTH_ABOVE"
                            episode_id = uuid4().hex
                            existing.add((str(market_row["condition_id"]), token, threshold))
                        elif prior_vwap < threshold <= walk.vwap:
                            status = "OPENED_UPWARD_CROSS"
                            provenance = "UPWARD_CROSS"
                            episode_id = uuid4().hex
                            existing.add((str(market_row["condition_id"]), token, threshold))
                        else:
                            status = "ABOVE_WITHOUT_NEW_CROSS"
                    decision = {
                        "decision_id": decision_id, "run_id": run_id,
                        "market_observation_id": market_row["observation_id"],
                        "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                        "condition_id": market_row["condition_id"], "event_id": market_row["event_id"],
                        "token_id": token, "outcome_index": outcome["outcome_index"],
                        "threshold": threshold, "decided_at": iso_utc(now),
                        "best_ask": walk.best_ask if walk else (snapshot["best_ask"] if snapshot else None),
                        "entry_vwap": walk.vwap if walk else None,
                        "entry_shares": walk.shares if walk else None,
                        "entry_cost": walk.cost if walk else None,
                        "prior_entry_vwap": prior_vwap,
                        "entry_provenance": provenance,
                        "decision_status": status,
                        "details_json": canonical_json({
                            "rule": "first full-depth observation or upward threshold cross",
                            "levels_used": walk.levels_used if walk else None,
                            "cadence_arm": self.config.trading.cadence_arm,
                            "sport_family": context["sport_family"],
                            "result_kind": outcome.get("result_kind"),
                            "sports_clock": context["sports_clock"],
                            "late_entry_minute_floors": list(
                                experiment.late_entry_minute_floors
                            ),
                            "notional_ladder_usdc": list(
                                experiment.notional_ladder_usdc
                            ),
                        }),
                        "episode_id": episode_id,
                    }
                    decisions.append(decision)
                    if episode_id and walk and context["end_date"]:
                        episode = {
                            "episode_id": episode_id, "decision_id": decision_id, "run_id": run_id,
                            "event_observation_id": context["event_observation_id"],
                            "condition_id": market_row["condition_id"], "event_id": market_row["event_id"],
                            "event_title": market_row["event_title"], "question": market_row["question"],
                            "token_id": token, "outcome_index": outcome["outcome_index"],
                            "outcome_label": outcome["outcome_label"], "threshold": threshold,
                            "cadence_arm": self.config.trading.cadence_arm,
                            "match_winner_class": context["match_winner_class"],
                            "league_code": context["league_code"],
                            "league_name": context["league_name"],
                            "classifier_version": context["classifier_version"],
                            "league_mapping_sha256": context["league_mapping_sha256"],
                            "entry_provenance": str(provenance),
                            "entered_at": iso_utc(now), "end_date": iso_utc(context["end_date"]),
                            "game_start_time": iso_utc(context["game_start"]) if context["game_start"] else None,
                            "sports_phase": context["phase"], "liquidity": context["liquidity"],
                            "volume_total": context["volume"], "fee_rate": context["fee_rate"],
                            "entry_best_ask": walk.best_ask, "entry_vwap": walk.vwap,
                            "entry_shares": walk.shares, "entry_cost": walk.cost,
                        }
                        episodes.append(episode)
                        policies.append({
                            "policy_id": uuid4().hex, "episode_id": episode_id,
                            "created_run_id": run_id, "policy_key": "HOLD_TO_RESOLUTION",
                            "stop_price": None, "created_at": iso_utc(now),
                        })
                        for stop in experiment.stop_levels:
                            policy = {
                                "policy_id": uuid4().hex, "episode_id": episode_id,
                                "created_run_id": run_id,
                                "policy_key": _stop_policy_key(stop),
                                "stop_price": stop, "created_at": iso_utc(now),
                            }
                            policies.append(policy)

        paths: list[dict[str, Any]] = []
        # Entry and exit cannot use the same displayed book. The entry ask and
        # contemporaneous bid encode spread, not a post-entry price move. New
        # episodes therefore receive their first path/stop observation only on
        # the next natural cadence cycle.
        for episode in open_before:
            token = str(episode["token_id"])
            snapshot = snapshot_by_token.get(token)
            book = books.books.get(token)
            exit_walk = walk_bids(book, float(episode["entry_shares"])) if book else None
            paths.append({
                "path_id": uuid4().hex, "episode_id": episode["episode_id"], "run_id": run_id,
                "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                "observed_at": iso_utc(now), "best_bid": exit_walk.best_ask if exit_walk else (snapshot["best_bid"] if snapshot else None),
                "executable_bid_vwap": exit_walk.vwap if exit_walk else None,
                "executable_proceeds": exit_walk.cost if exit_walk else None,
                "status": "OBSERVED" if exit_walk else "INSUFFICIENT_BID_DEPTH",
            })

        stop_attempts: list[dict[str, Any]] = []
        stop_exits: list[dict[str, Any]] = []
        for policy in active_stop_before:
            token = str(policy["token_id"])
            snapshot = snapshot_by_token.get(token)
            book = books.books.get(token)
            stop_price = float(policy["stop_price"])
            prior_attempt_count = int(policy["prior_attempt_count"] or 0)
            prior_filled = float(policy["prior_filled_shares"] or 0)
            entry_shares = float(policy["entry_shares"])
            remaining_before = max(0.0, entry_shares - prior_filled)
            if remaining_before <= 1e-7:
                continue
            current_walk = walk_bids_partial(book, remaining_before) if book else None
            current_best_bid = (
                current_walk.best_bid
                if current_walk is not None
                else (snapshot["best_bid"] if snapshot else None)
            )
            already_triggered = prior_attempt_count > 0
            if not already_triggered and (
                current_best_bid is None or float(current_best_bid) > stop_price + 1e-9
            ):
                continue

            attempt_id = uuid4().hex
            fee_rate = float(policy["fee_rate"])
            if current_walk is None:
                filled = 0.0
                remaining_after = remaining_before
                exit_vwap = None
                gross = 0.0
                fee = 0.0
                levels_used = 0
                status = "NO_BID_DEPTH"
            else:
                filled = current_walk.filled_shares
                remaining_after = current_walk.remaining_shares
                exit_vwap = current_walk.vwap
                gross = current_walk.proceeds
                fee = _execution_fee(filled, exit_vwap, fee_rate)
                levels_used = current_walk.levels_used
                status = "FULL_EXIT" if current_walk.complete else "PARTIAL_FILL"
            net = gross - fee
            prior_best_bid = _number(policy.get("prior_best_bid"))
            gap_from_stop = stop_price - exit_vwap if exit_vwap is not None else None
            drop_from_prior = (
                prior_best_bid - float(current_best_bid)
                if prior_best_bid is not None and current_best_bid is not None
                else None
            )
            attempt = {
                "attempt_id": attempt_id, "policy_id": policy["policy_id"],
                "episode_id": policy["episode_id"], "run_id": run_id,
                "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
                "observed_at": iso_utc(now), "stop_price": stop_price,
                "prior_best_bid": prior_best_bid, "trigger_best_bid": current_best_bid,
                "requested_shares": remaining_before, "filled_shares": filled,
                "remaining_shares": remaining_after, "exit_vwap": exit_vwap,
                "gross_proceeds": gross, "fee_rate": fee_rate,
                "estimated_fee": fee, "net_proceeds": net,
                "levels_used": levels_used, "status": status,
                "gap_from_stop": gap_from_stop, "drop_from_prior": drop_from_prior,
            }
            stop_attempts.append(attempt)

            total_filled = prior_filled + filled
            if total_filled + 1e-7 < entry_shares:
                continue
            total_gross = float(policy["prior_gross_proceeds"] or 0) + gross
            total_fee = float(policy["prior_estimated_fee"] or 0) + fee
            total_net = float(policy["prior_net_proceeds"] or 0) + net
            exit_vwap_total = total_gross / total_filled
            first_triggered_at = str(policy.get("first_triggered_at") or iso_utc(now))
            first_trigger_best_bid = policy.get("first_trigger_best_bid")
            if first_trigger_best_bid is None:
                first_trigger_best_bid = current_best_bid
            stop_exits.append({
                "exit_id": uuid4().hex, "policy_id": policy["policy_id"],
                "episode_id": policy["episode_id"], "completed_run_id": run_id,
                "completed_attempt_id": attempt_id,
                "first_triggered_at": first_triggered_at, "completed_at": iso_utc(now),
                "stop_price": stop_price,
                "first_trigger_best_bid": first_trigger_best_bid,
                "exit_vwap": exit_vwap_total, "requested_shares": entry_shares,
                "filled_shares": total_filled, "gross_proceeds": total_gross,
                "estimated_fee": total_fee, "net_proceeds": total_net,
                "attempt_count": prior_attempt_count + 1,
                "gap_from_stop": stop_price - exit_vwap_total,
            })

        event_count = source_event_count
        eligible_outcome_count = sum(
            int(row["entry_eligible"]) for row in outcome_rows
        )
        exclusion_counts = Counter(
            reason
            for row in market_rows
            if not row["eligible"]
            for reason in str(row["exclusion_reason"]).split(";")
        )
        self.repository.record_collection(
            sweep={
                "sweep_id": sweep_id, "run_id": run_id, "started_at": iso_utc(now),
                "completed_at": iso_utc(), "page_count": len(sweep.pages), "event_count": event_count,
                "accepted_event_count": accepted_event_count,
                "rejected_event_count": rejected_event_count,
                "drift_event_count": drift_event_count,
                "source_market_count": source_market_count,
                "market_count": len(market_rows), "eligible_market_count": sum(row["eligible"] for row in market_rows),
                "eligible_outcome_count": eligible_outcome_count,
                "cursor_complete": 1,
                "request_envelope_json": canonical_json(_request_envelope(self.config)),
            },
            payloads=payloads, events=event_rows, markets=market_rows, outcomes=outcome_rows,
            attempts=attempt_rows, snapshots=snapshot_rows, levels=level_rows,
            decisions=decisions, episodes=episodes, policies=policies, paths=paths,
            stop_attempts=stop_attempts, stop_exits=stop_exits,
        )
        for event_row in event_rows:
            if event_row["classification_status"] == "DRIFT":
                self.repository.record_issue(
                    run_id=run_id,
                    severity="HIGH",
                    issue_type="LEAGUE_IDENTITY_DRIFT",
                    detail={
                        "event_id": event_row["event_id"],
                        "sport_code": event_row["sport_code"],
                        "reason": event_row["rejection_reason"],
                        "league_mapping_sha256": event_row["league_mapping_sha256"],
                    },
                )
        for event_id, gap in sorted(result_triad_gaps.items()):
            self.repository.record_issue(
                run_id=run_id,
                severity="HIGH",
                issue_type="RESULT_TRIAD_COVERAGE_GAP",
                detail={"event_id": event_id, **gap},
            )
        if clock_expected_slugs and (
            clock_batch.status != "OBSERVED"
            or clock_missing_game_ids
            or clock_game_id_conflicts
            or set(clock_batch.updates) != clock_expected_slugs
        ):
            self.repository.record_issue(
                run_id=run_id,
                severity="MEDIUM",
                issue_type="SPORTS_WEBSOCKET_COVERAGE_GAP",
                detail={
                    "status": clock_batch.status,
                    "expected_count": len(clock_expected_slugs),
                    "target_count": clock_batch.target_count,
                    "matched_count": clock_batch.matched_count,
                    "message_count": clock_batch.message_count,
                    "error_type": clock_batch.error_type,
                    "missing_game_id_slugs": clock_missing_game_ids,
                    "game_id_conflicts": clock_game_id_conflicts,
                    "unmatched_slugs": sorted(
                        clock_expected_slugs - set(clock_batch.updates)
                    ),
                },
            )
        source_clock_gaps = sorted(
            clock_expected_slugs - set(source_clock_by_slug)
        )
        if source_clock_gaps:
            self.repository.record_issue(
                run_id=run_id,
                severity="HIGH",
                issue_type="SOURCE_CLOCK_COVERAGE_GAP",
                detail={
                    "expected_count": len(clock_expected_slugs),
                    "observed_count": len(source_clock_by_slug),
                    "unmatched_slugs": source_clock_gaps,
                },
            )
        clock_minute_field_gaps = sorted(
            slug
            for slug in clock_expected_soccer_slugs
            if (
                slug in source_clock_by_slug
                and source_clock_by_slug[slug].get("elapsed_raw") in (None, "")
            )
        )
        if clock_minute_field_gaps:
            self.repository.record_issue(
                run_id=run_id,
                severity="HIGH",
                issue_type="SOURCE_CLOCK_MINUTE_FIELD_GAP",
                detail={"slugs": clock_minute_field_gaps},
            )

        resolved = 0
        open_by_condition: dict[str, dict[str, Any]] = {}
        for episode in self.repository.open_episodes():
            open_by_condition.setdefault(str(episode["condition_id"]), episode)
        for condition_id, episode in open_by_condition.items():
            game_start = _utc(episode["game_start_time"])
            if (
                game_start is None
                or now < game_start
                or not self.repository.resolution_due(condition_id, now=now)
            ):
                continue
            result = self.clob.fetch_resolution(run_id, condition_id)
            attempt = {
                "attempt_id": uuid4().hex, "run_id": run_id,
                "condition_id": result.condition_id, "attempted_at": iso_utc(now),
                "status": result.status, "request_id": result.request_id,
                "winner_index": result.winner_index, "error_type": result.error_type,
                "error_message": result.error_message,
            }
            raw_payload = None
            resolution = None
            if result.raw_payload is not None:
                raw_payload = self.repository.payload_row(
                    run_id=run_id, kind="CLOB_MARKET_RESOLUTION",
                    request_id=result.raw_payload.request_id,
                    observed_at=result.raw_payload.received_at, raw=result.raw_payload.raw,
                )
            if result.status == "RESOLVED" and result.winner_index is not None and result.market is not None and result.raw_payload is not None:
                resolution = {
                    "resolution_id": uuid4().hex, "run_id": run_id,
                    "condition_id": result.condition_id, "observed_at": str(result.observed_at),
                    "winner_index": result.winner_index, "request_id": str(result.request_id),
                    "raw_market_sha256": result.raw_payload.response_sha256,
                    "evidence_json": canonical_json({"closed": result.market.get("closed"), "tokens": result.market.get("tokens")}),
                }
                resolved += 1
            self.repository.record_resolution(attempt=attempt, resolution=resolution, payload=raw_payload)

        return {
            "events": event_count, "markets": len(market_rows),
            "accepted_events": accepted_event_count,
            "rejected_events": rejected_event_count,
            "drift_events": drift_event_count,
            "source_markets": source_market_count,
            "eligible_markets": sum(row["eligible"] for row in market_rows),
            "eligible_outcomes": eligible_outcome_count,
            "exclusions": dict(sorted(exclusion_counts.items())),
            "cadence_arm": self.config.trading.cadence_arm,
            "book_tokens": len(books.attempts), "episodes_opened": len(episodes),
            "stop_attempts": len(stop_attempts), "stop_exits": len(stop_exits),
            "resolutions_added": resolved, "pages": len(sweep.pages), "cursor_complete": True,
            "sports_clock_status": clock_batch.status,
            "sports_clock_expected": len(clock_expected_slugs),
            "sports_clock_targets": clock_batch.target_count,
            "sports_clock_matched": clock_batch.matched_count,
            "source_clock_observed": len(source_clock_by_slug),
            "result_triad_gaps": len(result_triad_gaps),
        }
