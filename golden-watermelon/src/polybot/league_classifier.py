"""Fail-closed Gamma event identity classifier for the frozen soccer cohort."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from .config import (
    CLASSIFIER_VERSION,
    ESPORTS_TAG_ID,
    GammaConfig,
    LEAGUE_MAPPING_SHA256,
    LeagueIdentity,
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


def classify_soccer_event(event: Mapping[str, Any], gamma: GammaConfig) -> LeagueClassification:
    """Classify one source event without consulting titles, slugs, or market text."""
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
    common_tag_ids = tuple(str(value) for value in gamma.required_common_tag_ids)
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
        "required_common_tag_ids": list(common_tag_ids),
        "classifier_version": CLASSIFIER_VERSION,
        "league_mapping_sha256": LEAGUE_MAPPING_SHA256,
    }

    if str(ESPORTS_TAG_ID) in tag_ids or {"esports", "e-sports"} & set(tag_slugs):
        return LeagueClassification("REJECTED", None, None, ("ESPORTS_EXCLUDED",), evidence)
    if not sport or not sport_code:
        return LeagueClassification("DRIFT", None, None, ("SPORT_METADATA_MISSING",), evidence)
    identity = gamma.identities_by_code.get(sport_code)
    if identity is None and sport_code.casefold() in gamma.identities_by_code:
        identity = gamma.identities_by_code[sport_code.casefold()]
        return LeagueClassification(
            "DRIFT", identity.code, identity.name, ("SPORT_CODE_MISMATCH",), evidence
        )
    if identity is None:
        return LeagueClassification("REJECTED", None, None, ("LEAGUE_NOT_ALLOWED",), evidence)

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
