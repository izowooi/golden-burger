from __future__ import annotations

import pytest

from polybot.classifier import classify_event, classify_market
from polybot.registry import FAMILY_ORDER


def test_registry_contains_exact_five_family_tags(config):
    assert tuple(config.registry.by_code) == FAMILY_ORDER
    assert {code: item.tag_id for code, item in config.registry.by_code.items()} == {
        "soccer": 100350,
        "mlb": 100381,
        "nba": 745,
        "nfl": 450,
        "nhl": 899,
    }


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
