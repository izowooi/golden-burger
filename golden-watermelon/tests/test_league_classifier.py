from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from polybot.config import load_config
from polybot.league_classifier import classify_soccer_event, classify_sports_event


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/gamma_event_identity_cases.json"


@pytest.fixture(scope="module")
def cases() -> list[dict[str, object]]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert document["provenance"]["source_template"].startswith(
        "https://gamma-api.polymarket.com/events/"
    )
    return document["cases"]


@pytest.fixture(scope="module")
def gamma():
    return load_config(
        ROOT / "config.yaml", "watermelon-white-1m-v4b"
    ).trading.gamma


def test_real_gamma_positive_and_negative_identity_cases(cases, gamma) -> None:
    for case in cases:
        result = classify_soccer_event(case["event"], gamma)
        assert result.status == case["expected_status"], case["name"]
        if result.accepted:
            assert result.league_code == case["expected_league_code"]
            assert result.reasons == ()
        else:
            assert case["expected_reason"] in result.reasons


def test_numeric_identity_fields_accept_equivalent_string_or_integer(cases, gamma) -> None:
    event = deepcopy(next(case["event"] for case in cases if case["name"] == "epl"))
    event["sport"]["id"] = "2"
    event["sport"]["primaryTagId"] = "306"
    event["sport"]["series"] = 10188
    for tag in event["tags"]:
        tag["id"] = int(tag["id"])
    event["series"][0]["id"] = 10188
    assert classify_soccer_event(event, gamma).status == "ACCEPTED"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("sport_id", "SPORT_ID_MISMATCH"),
        ("sport_code", "SPORT_CODE_MISMATCH"),
        ("sport_name", "SPORT_NAME_MISMATCH"),
        ("primary_tag", "PRIMARY_TAG_ID_MISMATCH"),
        ("sport_series", "SPORT_SERIES_ID_MISMATCH"),
        ("sport_common_tag", "SPORT_REQUIRED_TAG_IDS_MISSING"),
        ("event_common_tag", "EVENT_REQUIRED_TAG_IDS_MISSING"),
        ("series_id", "EVENT_SERIES_ID_MISMATCH"),
        ("series_relation_slug", "EVENT_SERIES_RELATION_SLUG_MISMATCH"),
        ("series_slug", "EVENT_SERIES_SLUG_MISMATCH"),
        ("team_league", "TEAM_LEAGUE_MISSING_OR_MISMATCH"),
        ("team_count", "EXACTLY_TWO_TEAMS_REQUIRED"),
    ],
)
def test_allowlisted_code_authority_drift_fails_closed(
    cases, gamma, mutation: str, reason: str
) -> None:
    event = deepcopy(next(case["event"] for case in cases if case["name"] == "epl"))
    if mutation == "sport_id":
        event["sport"]["id"] = 999
    elif mutation == "sport_code":
        event["sport"]["sport"] = "EPL"
    elif mutation == "sport_name":
        event["sport"]["name"] = "Premier League Renamed"
    elif mutation == "primary_tag":
        event["sport"]["primaryTagId"] = 999
    elif mutation == "sport_series":
        event["sport"]["series"] = "999"
    elif mutation == "sport_common_tag":
        event["sport"]["tags"] = "82,306,100639,100350"
    elif mutation == "event_common_tag":
        event["tags"] = [tag for tag in event["tags"] if str(tag["id"]) != "1"]
    elif mutation == "series_id":
        event["series"][0]["id"] = "999"
    elif mutation == "series_relation_slug":
        event["series"][0]["slug"] = "premier-league-2026"
    elif mutation == "series_slug":
        event["seriesSlug"] = "premier-league-2026"
    elif mutation == "team_league":
        event["teams"][1]["league"] = "efl"
    elif mutation == "team_count":
        event["teams"] = event["teams"][:1]
    result = classify_soccer_event(event, gamma)
    assert result.status == "DRIFT"
    assert result.league_code == "epl"
    assert reason in result.reasons


def test_missing_sport_authority_is_drift_not_an_inferred_league(cases, gamma) -> None:
    event = deepcopy(next(case["event"] for case in cases if case["name"] == "epl"))
    event["sport"] = None
    result = classify_soccer_event(event, gamma)
    assert result.status == "DRIFT"
    assert result.league_code is None
    assert result.reasons == ("SPORT_METADATA_MISSING",)


@pytest.mark.parametrize(
    ("code", "tag_id", "series_id", "series_slug", "name"),
    [
        ("ucl", "100977", "10204", "ucl-2025", "UEFA Champions League"),
        ("uel", "101787", "10209", "uel-2025", "UEFA Europa League"),
    ],
)
def test_cross_league_uefa_competitions_use_numeric_tag_and_series_authority(
    gamma, code, tag_id, series_id, series_slug, name
) -> None:
    event = {
        "id": "uefa-event",
        "slug": f"{code}-aaa-bbb-2026-08-26",
        "resolutionSource": "https://www.uefa.com/",
        "seriesSlug": series_slug,
        "sport": None,
        "tags": [
            {"id": "1", "slug": "sports"},
            {"id": "100639", "slug": "games"},
            {"id": "100350", "slug": "soccer"},
            {"id": tag_id, "slug": code},
        ],
        "series": [{"id": series_id, "slug": series_slug}],
        "teams": [
            {"name": "A", "league": "epl"},
            {"name": "B", "league": "lal"},
        ],
    }
    result = classify_soccer_event(event, gamma)
    assert result.status == "ACCEPTED"
    assert (result.league_code, result.league_name) == (code, name)
    assert result.evidence["identity_kind"] == "UEFA_CUP"

    drifted = deepcopy(event)
    drifted["resolutionSource"] = "https://example.com/"
    result = classify_soccer_event(drifted, gamma)
    assert result.status == "DRIFT"
    assert "EVENT_RESOLUTION_SOURCE_MISMATCH" in result.reasons


def direct_event(family: str, *, title: str | None = None) -> dict[str, object]:
    identity = {
        "mlb": {"sport_id": 8, "tag_id": 100381, "root": 3, "name": "MLB"},
        "nba": {"sport_id": 34, "tag_id": 745, "root": 10345, "name": "NBA"},
        "nfl": {"sport_id": 10, "tag_id": 450, "root": 10187, "name": "NFL"},
        "nhl": {"sport_id": 35, "tag_id": 899, "root": 10346, "name": "NHL"},
    }[family]
    year = 2026
    series_slug = f"{family}-{year}"
    return {
        "id": f"{family}-event",
        "title": title or f"A vs B ({family.upper()})",
        "slug": f"{family}-a-b-{year}-08-29",
        "seriesSlug": series_slug,
        "startTime": "2026-08-29T00:00:00Z",
        "sport": {
            "id": identity["sport_id"],
            "sport": family,
            "name": identity["name"],
            "primaryTagId": identity["tag_id"],
            "series": identity["root"],
            "tags": f"1,100639,{identity['tag_id']}",
        },
        "tags": [
            {"id": 1, "slug": "sports"},
            {"id": 100639, "slug": "games"},
            {"id": identity["tag_id"], "slug": family},
        ],
        "series": [
            {
                "id": f"{identity['root']}01",
                "slug": series_slug,
                "ticker": series_slug,
                "title": f"{identity['name']} {year}",
                "seriesType": "single",
                "recurrence": "daily",
            }
        ],
        "teams": [
            {"name": "A", "league": family},
            {"name": "B", "league": family},
        ],
    }


@pytest.mark.parametrize(
    ("family", "title"),
    [
        ("mlb", "World Series: A vs B"),
        ("nba", "NBA Finals: A vs B"),
        ("nfl", "Super Bowl: A vs B"),
        ("nhl", "Stanley Cup Final: A vs B"),
    ],
)
def test_major_postseason_identity_is_accepted_without_title_allowlisting(
    gamma, family: str, title: str
) -> None:
    result = classify_sports_event(direct_event(family, title=title), gamma, family)
    assert result.status == "ACCEPTED"
    assert result.league_code == family
    assert result.evidence["identity_kind"] == "US_DIRECT_TWO_OUTCOME"


@pytest.mark.parametrize(
    ("family", "title", "league", "expected_reason"),
    [
        ("mlb", "MiLB Durham vs Norfolk", "milb", "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED"),
        ("nba", "NCAA Duke vs UNC", "ncaa", "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED"),
        ("nfl", "College Alabama vs Georgia", "ncaa", "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED"),
        ("nhl", "AHL Hershey vs Providence", "ahl", "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED"),
    ],
)
def test_minor_leagues_fail_closed(
    gamma, family: str, title: str, league: str, expected_reason: str
) -> None:
    event = direct_event(family, title=title)
    for team in event["teams"]:
        team["league"] = league
    result = classify_sports_event(event, gamma, family)
    assert result.status == "REJECTED"
    assert expected_reason in result.reasons


@pytest.mark.parametrize(
    "title",
    [
        "NBA G League: Austin Spurs vs Texas Legends",
        "NBA G-League Ignite vs College Park Skyhawks",
        "NBA Summer League: Lakers vs Celtics",
    ],
)
def test_nba_g_league_and_summer_league_are_excluded_despite_nba_authority(
    gamma, title: str
) -> None:
    event = direct_event("nba", title=title)
    result = classify_sports_event(event, gamma, "nba")
    assert result.status == "REJECTED"
    assert "MINOR_OR_NON_MAJOR_COMPETITION_EXCLUDED" in result.reasons
