"""Fail-closed Gamma event identity classifier for Golden Peach sports families."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .config import (
    CLASSIFIER_VERSION,
    DIRECT_SPORT_IDENTITIES,
    ESPORTS_TAG_ID,
    FROZEN_CUP_IDENTITIES,
    FROZEN_LEAGUE_IDENTITIES,
    LEAGUE_MAPPING_SHA256,
    CupIdentity,
    LeagueIdentity,
    REQUIRED_COMMON_TAG_IDS,
)


_MINOR_PATTERNS = re.compile(
    r"\b(?:milb|minor league|g league|summer league|ahl|echl|ncaa|college|"
    r"u-?2[013]|reserve|academy)\b",
    re.IGNORECASE,
)


def _decimal_id(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _id_tuple(values: Sequence[Any]) -> tuple[str, ...]:
    normalized = {_decimal_id(value) for value in values}
    normalized.discard(None)
    return tuple(sorted(normalized, key=int))


def _tag_metadata(event: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], list[dict[str, Any]]]:
    tags = [
        dict(item) for item in event.get("tags", [])
        if isinstance(item, Mapping)
    ] if isinstance(event.get("tags"), list) else []
    ids = _id_tuple([item.get("id") for item in tags])
    slugs = tuple(sorted({
        str(item.get("slug") or "").strip().casefold()
        for item in tags
        if str(item.get("slug") or "").strip()
    }))
    return ids, slugs, tags


def _series_metadata(event: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], list[dict[str, Any]]]:
    series = [
        dict(item) for item in event.get("series", [])
        if isinstance(item, Mapping)
    ] if isinstance(event.get("series"), list) else []
    ids = _id_tuple([item.get("id") for item in series])
    slugs = tuple(sorted({
        str(item.get("slug") or "").strip()
        for item in series
        if str(item.get("slug") or "").strip()
    }))
    return ids, slugs, series


def _sport_tag_ids(sport: Mapping[str, Any]) -> tuple[str, ...]:
    value = sport.get("tags")
    if not isinstance(value, str):
        return ()
    return _id_tuple([item.strip() for item in value.split(",")])


@dataclass(frozen=True)
class LeagueClassification:
    status: str
    league_code: str | None
    league_name: str | None
    reasons: tuple[str, ...]
    evidence: dict[str, Any]

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPTED"

    def event_row_fields(self) -> dict[str, Any]:
        evidence = self.evidence
        return {
            "sport_id": evidence["sport_id"],
            "sport_code": evidence["sport_code"],
            "sport_name": evidence["sport_name"],
            "sport_primary_tag_id": evidence["sport_primary_tag_id"],
            "sport_series_id": evidence["sport_series_id"],
            "series_slug": evidence["series_slug"],
            "tag_ids_json": json.dumps(evidence["tag_ids"], separators=(",", ":")),
            "tag_slugs_json": json.dumps(evidence["tag_slugs"], separators=(",", ":")),
            "series_ids_json": json.dumps(evidence["series_ids"], separators=(",", ":")),
            "series_slugs_json": json.dumps(evidence["series_slugs"], separators=(",", ":")),
            "team_leagues_json": json.dumps(evidence["team_leagues"], separators=(",", ":")),
            "classifier_version": CLASSIFIER_VERSION,
            "league_mapping_sha256": LEAGUE_MAPPING_SHA256,
            "league_code": self.league_code,
            "league_name": self.league_name,
            "classification_status": self.status,
            "rejection_reason": "ELIGIBLE" if not self.reasons else ";".join(self.reasons),
            "classification_evidence_json": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        }


def _source_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _classify_direct_sport_event(
    event: Mapping[str, Any], sport_family: str
) -> LeagueClassification:
    """Accept only exact major-league, two-team direct-moneyline events.

    Postseason games remain in the same MLB/NHL season series, so World Series
    and Stanley Cup Final games are included without matching title text.
    Futures, props, minor leagues, and child events are rejected elsewhere.
    """
    identity = DIRECT_SPORT_IDENTITIES[sport_family]
    raw_sport = event.get("sport")
    sport = dict(raw_sport) if isinstance(raw_sport, Mapping) else {}
    tag_ids, tag_slugs, _tags = _tag_metadata(event)
    series_ids, series_slugs, series = _series_metadata(event)
    series_slug = str(event.get("seriesSlug") or "").strip() or None
    teams = [
        dict(item) for item in event.get("teams", [])
        if isinstance(item, Mapping)
    ] if isinstance(event.get("teams"), list) else []
    team_leagues = tuple(str(team.get("league") or "").strip() for team in teams)
    sport_tag_ids = _sport_tag_ids(sport)
    common = {"1", "100639", str(identity.primary_tag_id)}
    evidence = {
        "sport_family": sport_family,
        "sport_id": _decimal_id(sport.get("id")),
        "sport_code": str(sport.get("sport") or "").strip() or None,
        "sport_name": str(sport.get("name") or "").strip() or None,
        "sport_primary_tag_id": _decimal_id(sport.get("primaryTagId")),
        "sport_series_id": _decimal_id(sport.get("series")),
        "sport_tag_ids": list(sport_tag_ids),
        "series_slug": series_slug,
        "tag_ids": list(tag_ids),
        "tag_slugs": list(tag_slugs),
        "series_ids": list(series_ids),
        "series_slugs": list(series_slugs),
        "team_leagues": list(team_leagues),
        "team_count": len(teams),
        "identity_kind": "US_DIRECT_TWO_OUTCOME",
        "required_common_tag_ids": sorted(common, key=int),
        "classifier_version": CLASSIFIER_VERSION,
        "league_mapping_sha256": LEAGUE_MAPPING_SHA256,
    }
    text = " ".join(
        str(event.get(field) or "") for field in ("title", "slug", "description")
    )
    reasons: list[str] = []
    if str(ESPORTS_TAG_ID) in tag_ids or {"esports", "e-sports"} & set(tag_slugs):
        reasons.append("ESPORTS_EXCLUDED")
    if _MINOR_PATTERNS.search(text):
        reasons.append("MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED")
    if _decimal_id(sport.get("id")) != str(identity.sport_id):
        reasons.append("SPORT_ID_MISMATCH")
    if str(sport.get("sport") or "").strip() != identity.code:
        reasons.append("SPORT_CODE_MISMATCH")
    if str(sport.get("name") or "").strip() != identity.name:
        reasons.append("SPORT_NAME_MISMATCH")
    if _decimal_id(sport.get("primaryTagId")) != str(identity.primary_tag_id):
        reasons.append("PRIMARY_TAG_ID_MISMATCH")
    if _decimal_id(sport.get("series")) != str(identity.root_series_id):
        reasons.append("SPORT_SERIES_ID_MISMATCH")
    if not common <= set(tag_ids) or not common <= set(sport_tag_ids):
        reasons.append("REQUIRED_TAG_IDS_MISSING")
    if len(series) != 1 or len(series_ids) != 1 or len(series_slugs) != 1:
        reasons.append("EVENT_SERIES_EXACTLY_ONE_REQUIRED")
    else:
        item = series[0]
        slug = str(item.get("slug") or "").strip()
        title = str(item.get("title") or "").strip()
        ticker = str(item.get("ticker") or "").strip()
        if series_slug != slug:
            reasons.append("EVENT_PRIMARY_SERIES_SLUG_MISMATCH")
        if str(item.get("seriesType") or "").strip() != "single":
            reasons.append("EVENT_SERIES_TYPE_MISMATCH")
        if str(item.get("recurrence") or "").strip() != "daily":
            reasons.append("EVENT_SERIES_RECURRENCE_MISMATCH")
        if slug == identity.code:
            if (
                series_ids != (str(identity.root_series_id),)
                or ticker != identity.code
                or title != identity.name
            ):
                reasons.append("EVENT_ROOT_SERIES_IDENTITY_MISMATCH")
        else:
            match = re.fullmatch(re.escape(identity.code) + r"-([0-9]{4})", slug)
            scheduled = _source_utc(
                event.get("startTime")
                or event.get("eventDate")
                or event.get("startDate")
                or event.get("eventStartTime")
                or event.get("gameStartTime")
            )
            if match is None:
                reasons.append("EVENT_SEASON_SERIES_SLUG_MISMATCH")
            else:
                season_year = int(match.group(1))
                if ticker != slug or title != f"{identity.name} {season_year}":
                    reasons.append("EVENT_SEASON_SERIES_METADATA_MISMATCH")
                if scheduled is None or scheduled.year - season_year not in {0, 1}:
                    reasons.append("EVENT_SEASON_SERIES_SCHEDULE_YEAR_MISMATCH")
    if len(teams) != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    elif team_leagues != (identity.team_league, identity.team_league):
        reasons.append("TEAM_LEAGUE_MISSING_OR_MISMATCH")
    identity_mismatch = any(
        reason.startswith("SPORT_") or reason == "PRIMARY_TAG_ID_MISMATCH"
        for reason in reasons
    )
    status = "REJECTED" if identity_mismatch or any(
        reason.endswith("EXCLUDED") for reason in reasons
    ) else "DRIFT"
    if not reasons:
        status = "ACCEPTED"
    return LeagueClassification(
        status,
        identity.code,
        identity.name,
        tuple(dict.fromkeys(reasons)),
        evidence,
    )


def _identity_mismatch_reasons(
    identity: LeagueIdentity,
    *,
    sport: Mapping[str, Any],
    sport_tag_ids: tuple[str, ...],
    tag_ids: tuple[str, ...],
    series_ids: tuple[str, ...],
    series_slugs: tuple[str, ...],
    series_slug: str | None,
    team_leagues: tuple[str, ...],
    team_count: int,
    common_tag_ids: tuple[str, ...],
) -> list[str]:
    reasons: list[str] = []
    expected_required_tags = {
        *(str(value) for value in identity.required_tag_ids),
        *common_tag_ids,
    }
    if _decimal_id(sport.get("id")) != str(identity.sport_id):
        reasons.append("SPORT_ID_MISMATCH")
    if str(sport.get("name") or "").strip() != identity.name:
        reasons.append("SPORT_NAME_MISMATCH")
    if _decimal_id(sport.get("primaryTagId")) != str(identity.primary_tag_id):
        reasons.append("PRIMARY_TAG_ID_MISMATCH")
    if _decimal_id(sport.get("series")) != identity.series_id:
        reasons.append("SPORT_SERIES_ID_MISMATCH")
    if not expected_required_tags <= set(sport_tag_ids):
        reasons.append("SPORT_REQUIRED_TAG_IDS_MISSING")
    if not expected_required_tags <= set(tag_ids):
        reasons.append("EVENT_REQUIRED_TAG_IDS_MISSING")
    if series_ids != (identity.series_id,):
        reasons.append("EVENT_SERIES_ID_MISMATCH")
    if series_slugs != (identity.series_slug,):
        reasons.append("EVENT_SERIES_RELATION_SLUG_MISMATCH")
    if series_slug != identity.series_slug:
        reasons.append("EVENT_SERIES_SLUG_MISMATCH")
    if team_count != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    if team_count == 2 and team_leagues != (identity.team_league, identity.team_league):
        reasons.append("TEAM_LEAGUE_MISSING_OR_MISMATCH")
    return reasons


def _cup_identity_mismatch_reasons(
    identity: CupIdentity,
    *,
    event: Mapping[str, Any],
    tag_ids: tuple[str, ...],
    series_ids: tuple[str, ...],
    series_slugs: tuple[str, ...],
    series_slug: str | None,
    team_count: int,
    common_tag_ids: tuple[str, ...],
) -> list[str]:
    reasons: list[str] = []
    if not {*common_tag_ids, str(identity.tag_id)} <= set(tag_ids):
        reasons.append("EVENT_REQUIRED_TAG_IDS_MISSING")
    if series_ids != (identity.series_id,):
        reasons.append("EVENT_SERIES_ID_MISMATCH")
    if series_slugs != (identity.series_slug,):
        reasons.append("EVENT_SERIES_RELATION_SLUG_MISMATCH")
    if series_slug != identity.series_slug:
        reasons.append("EVENT_SERIES_SLUG_MISMATCH")
    if not str(event.get("slug") or "").strip().startswith(
        identity.event_slug_prefix
    ):
        reasons.append("EVENT_COMPETITION_SLUG_PREFIX_MISMATCH")
    source_host = (
        urlsplit(str(event.get("resolutionSource") or "").strip()).hostname or ""
    ).casefold()
    if source_host != identity.resolution_source_host:
        reasons.append("EVENT_RESOLUTION_SOURCE_MISMATCH")
    if team_count != 2:
        reasons.append("EXACTLY_TWO_TEAMS_REQUIRED")
    return reasons


def classify_soccer_event(event: Mapping[str, Any]) -> LeagueClassification:
    """Classify one source event using frozen numeric league/cup authority."""
    raw_sport = event.get("sport")
    sport = dict(raw_sport) if isinstance(raw_sport, Mapping) else {}
    sport_code = str(sport.get("sport") or "").strip() or None
    sport_name = str(sport.get("name") or "").strip() or None
    tag_ids, tag_slugs, tags = _tag_metadata(event)
    series_ids, series_slugs, series = _series_metadata(event)
    series_slug = str(event.get("seriesSlug") or "").strip() or None
    teams = [
        dict(item) for item in event.get("teams", [])
        if isinstance(item, Mapping)
    ] if isinstance(event.get("teams"), list) else []
    team_leagues = tuple(
        str(team.get("league") or "").strip() for team in teams
    )
    sport_tag_ids = _sport_tag_ids(sport)
    common_tag_ids = tuple(str(value) for value in REQUIRED_COMMON_TAG_IDS)
    evidence = {
        "sport_id": _decimal_id(sport.get("id")),
        "sport_code": sport_code,
        "sport_name": sport_name,
        "sport_primary_tag_id": _decimal_id(sport.get("primaryTagId")),
        "sport_series_id": _decimal_id(sport.get("series")),
        "sport_tag_ids": list(sport_tag_ids),
        "series_slug": series_slug,
        "tag_ids": list(tag_ids),
        "tag_slugs": list(tag_slugs),
        "series_ids": list(series_ids),
        "series_slugs": list(series_slugs),
        "team_leagues": list(team_leagues),
        "team_count": len(teams),
        "identity_kind": None,
        "required_common_tag_ids": list(common_tag_ids),
        "classifier_version": CLASSIFIER_VERSION,
        "league_mapping_sha256": LEAGUE_MAPPING_SHA256,
    }

    if str(ESPORTS_TAG_ID) in tag_ids or {"esports", "e-sports"} & set(tag_slugs):
        return LeagueClassification("REJECTED", None, None, ("ESPORTS_EXCLUDED",), evidence)

    cup_candidates = [
        identity for identity in FROZEN_CUP_IDENTITIES
        if str(identity.tag_id) in tag_ids
    ]
    if len(cup_candidates) > 1:
        evidence["identity_kind"] = "UEFA_CUP"
        return LeagueClassification(
            "DRIFT", None, None, ("CUP_IDENTITY_AMBIGUOUS",), evidence
        )
    if len(cup_candidates) == 1:
        identity = cup_candidates[0]
        evidence["identity_kind"] = "UEFA_CUP"
        reasons = _cup_identity_mismatch_reasons(
            identity,
            event=event,
            tag_ids=tag_ids,
            series_ids=series_ids,
            series_slugs=series_slugs,
            series_slug=series_slug,
            team_count=len(teams),
            common_tag_ids=common_tag_ids,
        )
        if reasons:
            return LeagueClassification(
                "DRIFT", identity.code, identity.name, tuple(reasons), evidence
            )
        return LeagueClassification(
            "ACCEPTED", identity.code, identity.name, (), evidence
        )

    if not sport or not sport_code:
        return LeagueClassification("DRIFT", None, None, ("SPORT_METADATA_MISSING",), evidence)
    identities_by_code = {
        identity.code: identity for identity in FROZEN_LEAGUE_IDENTITIES
    }
    identity = identities_by_code.get(sport_code)
    if identity is None and sport_code.casefold() in identities_by_code:
        identity = identities_by_code[sport_code.casefold()]
        return LeagueClassification(
            "DRIFT", identity.code, identity.name, ("SPORT_CODE_MISMATCH",), evidence
        )
    if identity is None:
        return LeagueClassification("REJECTED", None, None, ("LEAGUE_NOT_ALLOWED",), evidence)

    evidence["identity_kind"] = "DOMESTIC_LEAGUE"

    reasons = _identity_mismatch_reasons(
        identity,
        sport=sport,
        sport_tag_ids=sport_tag_ids,
        tag_ids=tag_ids,
        series_ids=series_ids,
        series_slugs=series_slugs,
        series_slug=series_slug,
        team_leagues=team_leagues,
        team_count=len(teams),
        common_tag_ids=common_tag_ids,
    )
    if reasons:
        return LeagueClassification("DRIFT", identity.code, identity.name, tuple(reasons), evidence)
    return LeagueClassification("ACCEPTED", identity.code, identity.name, (), evidence)


def classify_sports_event(
    event: Mapping[str, Any], sport_family: str
) -> LeagueClassification:
    """Route one event through the exact identity contract for its job."""
    normalized = str(sport_family or "").strip().lower()
    if normalized == "soccer":
        result = classify_soccer_event(event)
        result.evidence.setdefault("sport_family", "soccer")
        return result
    if normalized in DIRECT_SPORT_IDENTITIES:
        return _classify_direct_sport_event(event, normalized)
    raise ValueError(f"unsupported sport family: {normalized or '<empty>'}")
