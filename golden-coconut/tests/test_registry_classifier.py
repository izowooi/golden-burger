from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from polybot.classifier import classify_event, classify_market
from polybot.registry import FAMILY_ORDER


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/major_sports_lifecycle_cases.json").read_text(
        encoding="utf-8"
    )
)


def test_active_registry_is_v6_and_uses_v6_profiles():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads(
        (root / "research/frozen-2026-08-28-v6/SPORTS_REGISTRY.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["schema_version"] == 6
    assert registry["registry_profile"].endswith("-v6")
    assert registry["classifier_version"].endswith("-v6")


def test_registry_contains_exact_five_family_tags(config):
    assert tuple(config.registry.by_code) == FAMILY_ORDER
    assert {code: item.tag_id for code, item in config.registry.by_code.items()} == {
        "soccer": 100350,
        "mlb": 100381,
        "nba": 745,
        "nfl": 450,
        "nhl": 899,
    }
    assert config.registry.by_code["soccer"].query_tag_ids == (
        306,
        1494,
        102070,
        780,
        100100,
        101962,
        100977,
        101787,
    )
    for family in ("mlb", "nba", "nfl", "nhl"):
        entry = config.registry.by_code[family]
        assert entry.query_tag_ids == (entry.tag_id,)
        assert entry.payload["sport"]["team_league"] == family
        assert entry.payload["event_series_identity"][
            "allowed_schedule_year_lags"
        ] == [0, 1]


@pytest.mark.parametrize("family", ["mlb", "nba", "nfl", "nhl"])
def test_positive_exact_us_major_identity(config, make_us_event, family):
    result = classify_event(
        make_us_event(family), config.registry.by_code[family], config.registry
    )
    assert result.accepted
    assert result.competition_code == family


@pytest.mark.parametrize("family", ["mlb", "nba", "nfl", "nhl"])
def test_official_major_preseason_is_accepted_and_stratified(
    config, make_us_event, family
):
    result = classify_event(
        make_us_event(family, phase="PRESEASON"),
        config.registry.by_code[family],
        config.registry,
    )
    assert result.accepted
    assert result.evidence["season_phase"] == "PRESEASON"


@pytest.mark.parametrize("family", ["nba", "nhl"])
def test_minor_leagues_are_rejected(config, make_us_event, family):
    result = classify_event(
        make_us_event(family, minor=True),
        config.registry.by_code[family],
        config.registry,
    )
    assert result.status == "REJECTED"
    assert "MINOR" in result.reasons[0]


def test_esports_is_rejected_before_identity(config, make_us_event):
    event = make_us_event("nba")
    event["tags"].append({"id": 64, "slug": "esports"})
    result = classify_event(event, config.registry.by_code["nba"], config.registry)
    assert result.reasons == ("ESPORTS_EXCLUDED",)


def test_wrong_root_is_rejected(config, make_us_event):
    event = make_us_event("nfl")
    event["sport"]["series"] = 999
    result = classify_event(event, config.registry.by_code["nfl"], config.registry)
    assert not result.accepted
    assert "SPORT_ROOT_ID_MISMATCH" in result.reasons


def test_nfl_production_season_series_is_not_equated_with_sport_root(
    config, make_us_event
):
    event = make_us_event("nfl")
    event["startTime"] = "2026-08-27T23:00:00Z"
    event["series"] = [
        {
            "id": 12185,
            "ticker": "nfl-2026",
            "slug": "nfl-2026",
            "title": "NFL 2026",
            "seriesType": "single",
            "recurrence": "daily",
        }
    ]
    event["seriesSlug"] = "nfl-2026"
    result = classify_event(
        event, config.registry.by_code["nfl"], config.registry
    )
    assert result.accepted, result.reasons


def test_us_season_series_must_match_scheduled_year_window(config, make_us_event):
    event = make_us_event("nfl")
    event["startTime"] = "2026-08-27T23:00:00Z"
    event["series"] = [
        {
            "id": 12185,
            "ticker": "nfl-2024",
            "slug": "nfl-2024",
            "title": "NFL 2024",
            "seriesType": "single",
            "recurrence": "daily",
        }
    ]
    event["seriesSlug"] = "nfl-2024"
    result = classify_event(
        event, config.registry.by_code["nfl"], config.registry
    )
    assert not result.accepted
    assert "EVENT_SEASON_SERIES_SCHEDULE_YEAR_MISMATCH" in result.reasons


def test_us_exact_team_league_is_required(config, make_us_event):
    event = make_us_event("nhl")
    event["teams"][1]["league"] = "ahl"
    result = classify_event(
        event, config.registry.by_code["nhl"], config.registry
    )
    assert not result.accepted
    assert "TEAM_LEAGUE_MISMATCH" in result.reasons


def test_soccer_domestic_exact_identity(config, make_soccer_event):
    result = classify_event(
        make_soccer_event(), config.registry.by_code["soccer"], config.registry
    )
    assert result.accepted
    assert result.competition_code == "epl"


def test_soccer_result_specific_market(config, make_soccer_event, make_soccer_market):
    event = make_soccer_event()
    event_result = classify_event(event, config.registry.by_code["soccer"], config.registry)
    home = classify_market(event, make_soccer_market("Home FC"), event_result)
    draw = classify_market(event, make_soccer_market("Draw", 2), event_result)
    assert home.eligible and home.eligible_indices == (0,) and home.result_kind == "HOME"
    assert draw.eligible and draw.result_kind == "DRAW"


def test_soccer_draw_parenthetical_must_equal_exact_event_title(
    config, make_soccer_event, make_soccer_market
):
    event = make_soccer_event()
    event_result = classify_event(
        event, config.registry.by_code["soccer"], config.registry
    )
    draw = make_soccer_market("Draw", 2)
    draw["groupItemTitle"] = "Draw (Different FC vs Other FC)"
    result = classify_market(event, draw, event_result)
    assert not result.eligible
    assert "SOCCER_RESULT_DESCRIPTOR_NOT_EXACT" in result.reasons


def test_us_direct_market_structure(config, make_us_event, make_us_market):
    event = make_us_event("mlb")
    event_result = classify_event(event, config.registry.by_code["mlb"], config.registry)
    market = classify_market(event, make_us_market("mlb"), event_result)
    assert market.eligible
    assert market.eligible_indices == (0, 1)
    assert market.structure == "US_DIRECT_TWO_TEAM_NON_NEGRISK"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sportsMarketType", "spread"),
        ("question", "Team A first half moneyline"),
        ("question", "Will Team A win the championship?"),
        ("question", "Will Team A advance?"),
        ("period", "first_half"),
        ("isFuture", True),
        ("isProp", True),
        ("isAdvancement", True),
        ("parentMarketId", "parent-market"),
        ("negRisk", True),
    ],
)
def test_us_non_whole_or_wrong_structure_is_rejected(
    config, make_us_event, make_us_market, field, value
):
    event = make_us_event("nfl")
    event_result = classify_event(event, config.registry.by_code["nfl"], config.registry)
    source = make_us_market("nfl")
    source[field] = value
    assert not classify_market(event, source, event_result).eligible


@pytest.mark.parametrize("family", list(FAMILY_ORDER))
def test_sanitized_production_shaped_positive_fixtures(config, family):
    event = deepcopy(FIXTURE["positive"][family])
    event_result = classify_event(
        event, config.registry.by_code[family], config.registry
    )
    assert event_result.accepted, event_result.reasons
    market_results = [
        classify_market(event, market, event_result) for market in event["markets"]
    ]
    assert all(result.eligible for result in market_results)
    assert (
        {result.result_kind for result in market_results}
        == {"HOME", "DRAW", "AWAY"}
        if family == "soccer"
        else all(result.structure == "US_DIRECT_TWO_TEAM_NON_NEGRISK" for result in market_results)
    )


@pytest.mark.parametrize("case", FIXTURE["negative"], ids=lambda case: case["name"])
def test_sanitized_production_shaped_negative_fixtures(config, case):
    family = case["base_family"]
    event = deepcopy(FIXTURE["positive"][family])
    if case["target"] == "event":
        event.update(case["patch"])
    elif case["target"] == "event_add_tag":
        event["tags"].append(case["patch"])
    event_result = classify_event(
        event, config.registry.by_code[family], config.registry
    )
    if case["target"].startswith("event"):
        assert not event_result.accepted
        return
    assert event_result.accepted
    market = deepcopy(event["markets"][0])
    market.update(case["patch"])
    assert not classify_market(event, market, event_result).eligible
