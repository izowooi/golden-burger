"""Fail-closed event and whole-game moneyline identity classifiers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .registry import SportFamily, SportsRegistry


_MINOR_PATTERNS = re.compile(
    r"\b(?:milb|minor league|g league|development league|ahl|echl|ncaa|college|"
    r"u-?2[013]|reserve|academy)\b",
    re.IGNORECASE,
)
_NON_WHOLE_GAME_MARKET = re.compile(
    r"\b(?:first|1st|second|2nd|third|3rd|fourth|4th)\s+"
    r"(?:half|quarter|period|inning)|\b(?:spread|handicap|total|over/under|"
    r"draw no bet|advance(?:ment)?|qualify|penalt(?:y|ies)|extra time|corners?|"
    r"shots?|goalscorer|touchdowns?|runs?|puck line|run line|futures?|"
    r"season[- ]long|championship winner|conference winner|division winner|"
    r"win (?:the )?(?:championship|league|division|conference))\b",
    re.IGNORECASE,
)
_WHOLE_GAME_PERIOD_VALUES = {
    "",
    "0",
    "full",
    "full game",
    "full match",
    "full time",
    "game",
    "match",
    "regular time",
    "whole game",
    "whole match",
}


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


def _integer_id(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _ids(values: Sequence[Any]) -> tuple[str, ...]:
    result = {_integer_id(value) for value in values}
    result.discard(None)
    return tuple(sorted(result, key=int))


def _tag_data(event: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], list[dict[str, Any]]]:
    raw = event.get("tags")
    tags = [dict(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    ids = _ids([item.get("id") for item in tags])
    slugs = tuple(
        sorted(
            {
                str(item.get("slug") or "").strip().casefold()
                for item in tags
                if str(item.get("slug") or "").strip()
            }
        )
    )
    return ids, slugs, tags


def _series_data(event: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], list[dict[str, Any]]]:
    raw = event.get("series")
    series = [dict(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    ids = _ids([item.get("id") for item in series])
    slugs = tuple(
        sorted(
            {
                str(item.get("slug") or "").strip()
                for item in series
                if str(item.get("slug") or "").strip()
            }
        )
    )
    return ids, slugs, series


def _sport_tags(sport: Mapping[str, Any]) -> tuple[str, ...]:
    raw = sport.get("tags")
    if isinstance(raw, str):
        return _ids([value.strip() for value in raw.split(",")])
    if isinstance(raw, list):
        return _ids(raw)
    return ()


def _normalize(value: Any) -> str:
    return " ".join(
        "".join(character if character.isalnum() else " " for character in str(value or ""))
        .casefold()
        .split()
    )


def _team_forms(team: Mapping[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            _normalize(team.get("name")),
            _normalize(team.get("alias")),
            _normalize(team.get("abbreviation")),
        )
        if normalized
    }


def classify_season_phase(event: Mapping[str, Any], family: str) -> str:
    """Keep official US preseason, regular, and postseason strata separate."""

    if family == "soccer":
        return "NOT_APPLICABLE"
    explicit = " ".join(
        str(event.get(key) or "")
        for key in ("seasonPhase", "season_phase", "seasonType", "season_type")
    ).casefold()
    _, tag_slugs, _ = _tag_data(event)
    text = " ".join(
        (
            explicit,
            *tag_slugs,
            str(event.get("title") or "").casefold(),
            str(event.get("slug") or "").casefold(),
        )
    )
    if "preseason" in text or "pre-season" in text:
        return "PRESEASON"
    if any(
        marker in text
        for marker in ("postseason", "post-season", "playoff", "wild card", "championship")
    ):
        return "POSTSEASON"
    if "regular" in text:
        return "REGULAR"
    return "UNKNOWN"


@dataclass(frozen=True)
class EventClassification:
    status: str
    family: str
    competition_code: str | None
    competition_name: str | None
    reasons: tuple[str, ...]
    cluster_id: str
    evidence: Mapping[str, Any]

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPTED"


@dataclass(frozen=True)
class MarketClassification:
    eligible: bool
    family: str
    structure: str
    result_kind: str | None
    eligible_indices: tuple[int, ...]
    labels: tuple[str, ...]
    token_ids: tuple[str, ...]
    probabilities: tuple[float | None, ...]
    reasons: tuple[str, ...]
    evidence: Mapping[str, Any]


def _base_event_evidence(event: Mapping[str, Any]) -> dict[str, Any]:
    sport = dict(event.get("sport")) if isinstance(event.get("sport"), Mapping) else {}
    tag_ids, tag_slugs, _ = _tag_data(event)
    series_ids, series_slugs, _ = _series_data(event)
    teams = [dict(item) for item in event.get("teams", []) if isinstance(item, Mapping)] if isinstance(event.get("teams"), list) else []
    return {
        "event_id": str(event.get("id") or "") or None,
        "game_id": str(event.get("gameId") or event.get("game_id") or "") or None,
        "sport_id": _integer_id(sport.get("id")),
        "sport_code": str(sport.get("sport") or "") or None,
        "sport_name": str(sport.get("name") or "") or None,
        "sport_primary_tag_id": _integer_id(sport.get("primaryTagId")),
        "sport_root_id": _integer_id(sport.get("series")),
        "sport_tag_ids": list(_sport_tags(sport)),
        "tag_ids": list(tag_ids),
        "tag_slugs": list(tag_slugs),
        "series_ids": list(series_ids),
        "series_slugs": list(series_slugs),
        "series_slug": str(event.get("seriesSlug") or "") or None,
        "team_count": len(teams),
        "team_leagues": [str(team.get("league") or "") for team in teams],
    }


def _negative_event_reasons(event: Mapping[str, Any], evidence: Mapping[str, Any], registry: SportsRegistry) -> list[str]:
    shared = registry.raw.get("shared", {})
    esports_id = str(shared.get("esports_tag_id", 64))
    if esports_id in evidence["tag_ids"] or {"esports", "e-sports"} & set(evidence["tag_slugs"]):
        return ["ESPORTS_EXCLUDED"]
    text = " ".join(
        str(event.get(field) or "") for field in ("title", "slug", "description")
    )
    if _MINOR_PATTERNS.search(text):
        return ["MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED"]
    if event.get("parentEventId") not in (None, ""):
        return ["CHILD_EVENT_EXCLUDED"]
    return []


def _classify_us_event(
    event: Mapping[str, Any], family: SportFamily, evidence: dict[str, Any]
) -> tuple[str, str | None, str | None, list[str]]:
    payload = family.payload
    identity = payload.get("sport")
    if not isinstance(identity, Mapping):
        return "DRIFT", family.code, family.code.upper(), ["REGISTRY_SPORT_IDENTITY_MISSING"]
    expected = {
        "sport_id": str(identity["id"]),
        "sport_code": str(identity["code"]),
        "sport_name": str(identity["name"]),
        "sport_primary_tag_id": str(identity["primary_tag_id"]),
        "sport_root_id": str(identity["root_id"]),
    }
    reasons = [
        f"{key.upper()}_MISMATCH"
        for key, expected_value in expected.items()
        if str(evidence.get(key) or "") != expected_value
    ]
    required_tags = {str(value) for value in payload.get("required_event_tag_ids", [])}
    if not required_tags <= set(evidence["tag_ids"]):
        reasons.append("EVENT_REQUIRED_TAG_IDS_MISSING")
    if not required_tags <= set(evidence["sport_tag_ids"]):
        reasons.append("SPORT_REQUIRED_TAG_IDS_MISSING")
    if tuple(evidence["series_ids"]) != (str(identity["root_id"]),):
        reasons.append("EVENT_ROOT_RELATION_MISMATCH")
    if evidence["team_count"] != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    if reasons:
        identity_fields = {
            "SPORT_ID_MISMATCH",
            "SPORT_CODE_MISMATCH",
            "SPORT_NAME_MISMATCH",
            "SPORT_PRIMARY_TAG_ID_MISMATCH",
            "SPORT_ROOT_ID_MISMATCH",
        }
        status = "REJECTED" if identity_fields & set(reasons) else "DRIFT"
        return status, family.code, str(identity["name"]), reasons
    return "ACCEPTED", family.code, str(identity["name"]), []


def _classify_soccer_event(
    event: Mapping[str, Any], family: SportFamily, evidence: dict[str, Any]
) -> tuple[str, str | None, str | None, list[str]]:
    payload = family.payload
    common = {str(value) for value in payload.get("required_common_tag_ids", [])}
    tags = set(evidence["tag_ids"])
    series_ids = tuple(evidence["series_ids"])
    series_slugs = tuple(evidence["series_slugs"])
    cups = [
        item for item in payload.get("uefa_competitions", [])
        if isinstance(item, Mapping) and str(item.get("tag_id")) in tags
    ]
    if len(cups) > 1:
        return "DRIFT", None, None, ["UEFA_IDENTITY_AMBIGUOUS"]
    if cups:
        cup = cups[0]
        reasons: list[str] = []
        if not common | {str(cup["tag_id"])} <= tags:
            reasons.append("EVENT_REQUIRED_TAG_IDS_MISSING")
        if series_ids != (str(cup["series_id"]),):
            reasons.append("EVENT_SERIES_ID_MISMATCH")
        if series_slugs != (str(cup["series_slug"]),):
            reasons.append("EVENT_SERIES_SLUG_MISMATCH")
        if str(evidence.get("series_slug") or "") != str(cup["series_slug"]):
            reasons.append("EVENT_PRIMARY_SERIES_SLUG_MISMATCH")
        if not str(event.get("slug") or "").startswith(str(cup["event_slug_prefix"])):
            reasons.append("EVENT_COMPETITION_SLUG_PREFIX_MISMATCH")
        source_host = (urlsplit(str(event.get("resolutionSource") or "")).hostname or "").casefold()
        if source_host != str(cup["resolution_source_host"]).casefold():
            reasons.append("EVENT_RESOLUTION_SOURCE_MISMATCH")
        if evidence["team_count"] != 2:
            reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
        return (
            "DRIFT" if reasons else "ACCEPTED",
            str(cup["code"]),
            str(cup["name"]),
            reasons,
        )
    leagues = {
        str(item["code"]): item
        for item in payload.get("domestic_leagues", [])
        if isinstance(item, Mapping)
    }
    sport_code = str(evidence.get("sport_code") or "")
    league = leagues.get(sport_code)
    if league is None:
        return "REJECTED", None, None, ["SOCCER_COMPETITION_NOT_ALLOWED"]
    required = common | {str(value) for value in league["required_tag_ids"]}
    reasons = []
    if str(evidence.get("sport_id") or "") != str(league["sport_id"]):
        reasons.append("SPORT_ID_MISMATCH")
    if str(evidence.get("sport_name") or "") != str(league["name"]):
        reasons.append("SPORT_NAME_MISMATCH")
    if str(evidence.get("sport_primary_tag_id") or "") != str(league["primary_tag_id"]):
        reasons.append("SPORT_PRIMARY_TAG_ID_MISMATCH")
    if str(evidence.get("sport_root_id") or "") != str(league["series_id"]):
        reasons.append("SPORT_ROOT_ID_MISMATCH")
    if not required <= tags or not required <= set(evidence["sport_tag_ids"]):
        reasons.append("REQUIRED_TAG_IDS_MISSING")
    if series_ids != (str(league["series_id"]),) or series_slugs != (str(league["series_slug"]),):
        reasons.append("EVENT_SERIES_IDENTITY_MISMATCH")
    if str(evidence.get("series_slug") or "") != str(league["series_slug"]):
        reasons.append("EVENT_PRIMARY_SERIES_SLUG_MISMATCH")
    if evidence["team_count"] != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    if evidence["team_count"] == 2 and evidence["team_leagues"] != [league["team_league"], league["team_league"]]:
        reasons.append("TEAM_LEAGUE_MISMATCH")
    return (
        "DRIFT" if reasons else "ACCEPTED",
        str(league["code"]),
        str(league["name"]),
        reasons,
    )


def classify_event(
    event: Mapping[str, Any], family: SportFamily, registry: SportsRegistry
) -> EventClassification:
    evidence = _base_event_evidence(event)
    evidence["season_phase"] = classify_season_phase(event, family.code)
    event_id = str(event.get("id") or "MISSING")
    game_id = str(event.get("gameId") or event.get("game_id") or "")
    cluster_id = f"{family.code}:{game_id or event_id}"
    negative = _negative_event_reasons(event, evidence, registry)
    if negative:
        return EventClassification("REJECTED", family.code, None, None, tuple(negative), cluster_id, evidence)
    if event.get("live") is not True or event.get("ended") is not False:
        return EventClassification("REJECTED", family.code, None, None, ("EVENT_NOT_EXPLICITLY_IN_PLAY",), cluster_id, evidence)
    if not event_id or event_id == "MISSING":
        return EventClassification("DRIFT", family.code, None, None, ("EVENT_ID_MISSING",), cluster_id, evidence)
    if family.code == "soccer":
        status, code, name, reasons = _classify_soccer_event(event, family, evidence)
    else:
        status, code, name, reasons = _classify_us_event(event, family, evidence)
    return EventClassification(status, family.code, code, name, tuple(reasons), cluster_id, evidence)


def _probabilities(value: Any) -> tuple[float | None, ...]:
    raw = _array(value)
    if raw is None:
        return ()
    result: list[float | None] = []
    for item in raw:
        try:
            number = float(item)
        except (TypeError, ValueError):
            number = float("nan")
        result.append(number if 0 <= number <= 1 else None)
    return tuple(result)


def classify_market(
    event: Mapping[str, Any],
    market: Mapping[str, Any],
    event_classification: EventClassification,
) -> MarketClassification:
    family = event_classification.family
    labels = tuple(str(item).strip() for item in (_array(market.get("outcomes")) or []))
    tokens = tuple(str(item).strip() for item in (_array(market.get("clobTokenIds")) or []))
    probabilities = _probabilities(market.get("outcomePrices"))
    reasons: list[str] = []
    sports_type = market.get("sportsMarketType")
    if sports_type != "moneyline":
        reasons.append("NOT_EXACT_TOP_LEVEL_MONEYLINE")
    if market.get("parentMarketId") not in (None, "") or market.get("childMarkets") not in (None, [], ""):
        reasons.append("CHILD_MARKET_EXCLUDED")
    if any(
        market.get(key) is True
        for key in ("isFuture", "future", "isProp", "prop", "isAdvancement", "advancement")
    ):
        reasons.append("PROP_FUTURE_OR_ADVANCEMENT_EXCLUDED")
    period_values = [
        market.get(key)
        for key in ("period", "periodType", "gamePeriod", "inning", "quarter", "half")
        if key in market and market.get(key) is not None
    ]
    if any(_normalize(value) not in _WHOLE_GAME_PERIOD_VALUES for value in period_values):
        reasons.append("NON_WHOLE_GAME_PERIOD_EXCLUDED")
    identity_text = " ".join(
        str(market.get(field) or "") for field in ("question", "groupItemTitle", "slug")
    )
    if _NON_WHOLE_GAME_MARKET.search(identity_text):
        reasons.append("NON_WHOLE_GAME_OR_PROP_EXCLUDED")
    if event_classification.accepted is not True:
        reasons.append("EVENT_IDENTITY_NOT_ACCEPTED")
    if market.get("active") is not True or market.get("closed") is not False:
        reasons.append("MARKET_NOT_OPEN")
    if market.get("enableOrderBook") is not True or market.get("acceptingOrders") is not True:
        reasons.append("PUBLIC_BOOK_NOT_ENABLED")
    if not (
        len(labels) == len(tokens) == len(probabilities) == 2
        and all(tokens)
        and len(set(tokens)) == 2
        and all(value is not None for value in probabilities)
    ):
        reasons.append("TWO_OUTCOME_ALIGNMENT_REQUIRED")
    teams = [dict(item) for item in event.get("teams", []) if isinstance(item, Mapping)] if isinstance(event.get("teams"), list) else []
    forms = [_team_forms(team) for team in teams]
    eligible_indices: tuple[int, ...] = ()
    result_kind: str | None = None
    structure = "REJECTED"
    neg_risk = market.get("negRisk") if isinstance(market.get("negRisk"), bool) else None
    if family == "soccer":
        if labels != ("Yes", "No"):
            reasons.append("SOCCER_YES_NO_STRUCTURE_REQUIRED")
        if neg_risk is not True:
            reasons.append("SOCCER_NEGRISK_REQUIRED")
        descriptor = _normalize(market.get("groupItemTitle"))
        if not descriptor:
            reasons.append("SOCCER_RESULT_DESCRIPTOR_MISSING")
        elif descriptor in {"draw", "tie"}:
            result_kind = "DRAW"
        else:
            matches = [index for index, candidates in enumerate(forms) if descriptor in candidates]
            if len(matches) != 1:
                reasons.append("SOCCER_RESULT_DESCRIPTOR_NOT_EXACT")
            else:
                result_kind = "HOME" if matches[0] == 0 else "AWAY"
        if not reasons:
            structure = "SOCCER_RESULT_YES_NO_NEGRISK"
            eligible_indices = (0,)
    else:
        if neg_risk is not False:
            reasons.append("US_DIRECT_NEGRISK_FALSE_REQUIRED")
        if labels == ("Yes", "No"):
            reasons.append("US_DIRECT_TEAM_LABELS_REQUIRED")
        matched: list[int] = []
        if len(forms) == 2 and len(labels) == 2:
            for label in labels:
                normalized = _normalize(label)
                candidates = [index for index, values in enumerate(forms) if normalized in values]
                matched.append(candidates[0] if len(candidates) == 1 else -1)
        if sorted(matched) != [0, 1]:
            reasons.append("US_DIRECT_OUTCOMES_NOT_EXACT_TEAMS")
        if not reasons:
            structure = "US_DIRECT_TWO_TEAM_NON_NEGRISK"
            eligible_indices = (0, 1)
            result_kind = "DIRECT_TWO_TEAM"
    evidence = {
        "sports_market_type": sports_type,
        "neg_risk": neg_risk,
        "labels": list(labels),
        "token_ids": list(tokens),
        "result_kind": result_kind,
        "event_cluster_id": event_classification.cluster_id,
        "competition_code": event_classification.competition_code,
    }
    return MarketClassification(
        eligible=not reasons,
        family=family,
        structure=structure,
        result_kind=result_kind,
        eligible_indices=eligible_indices,
        labels=labels,
        token_ids=tokens,
        probabilities=probabilities,
        reasons=tuple(dict.fromkeys(reasons)),
        evidence=evidence,
    )
