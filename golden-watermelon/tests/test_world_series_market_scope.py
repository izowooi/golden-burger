"""A league identity cannot turn a series future into an individual game."""

import pytest

from polybot.collector import classify_match_winner
from polybot.config import load_config
from polybot.league_classifier import classify_sports_event
from test_league_classifier import direct_event


def classify(question, *, flag=None, field="question"):
    event = {"teams": [{"name": "HomeClub"}, {"name": "AwayClub"}]}
    market = {"sportsMarketType": "moneyline", "negRisk": False,
              "question": "HomeClub vs AwayClub", "groupItemTitle": ""}
    market[field] = question
    if flag is not None:
        market["isFuture"] = flag
    return classify_match_winner(
        event, market, ["HomeClub", "AwayClub"], ["home", "away"], [.6, .4], "mlb"
    )


@pytest.mark.parametrize("flag", [None, False])
@pytest.mark.parametrize("question", [
    "Series winner: HomeClub or AwayClub",
    "2026 World Series winner",
    "World Series 2026 champion",
    "Will HomeClub win World Series?",
    "Will HomeClub win the World Series?",
    "Will HomeClub win the 2026 World Series?",
    "Winner of the 2026 World Series",
])
def test_explicit_series_future_is_rejected_without_future_flag(question, flag):
    result = classify(question, flag=flag)
    assert result[1] == ()
    assert "SERIES_WINNER_NOT_INDIVIDUAL_GAME" in result[3]


@pytest.mark.parametrize("field", ["question", "groupItemTitle", "slug"])
def test_series_winner_rejection_checks_each_market_identity_field(field):
    assert "SERIES_WINNER_NOT_INDIVIDUAL_GAME" in classify("world-series-winner", field=field)[3]


@pytest.mark.parametrize("question", [
    "Will HomeClub win the World Series Game 1?",
    "Will HomeClub win the World Series Game1?",
    "Will HomeClub win the World Series Game 7?",
    "World Series Game 7: HomeClub vs AwayClub",
    "World Series: HomeClub vs AwayClub",
    "Will HomeClub win the WorldSeries Game1?",
])
def test_individual_world_series_game_remains_eligible(question):
    result = classify(question)
    assert result[0] == "DIRECT_TWO_TEAM_MONEYLINE"
    assert result[1] == (0, 1)
    assert result[3] == []


def test_world_series_game_title_cannot_bypass_exact_mlb_root():
    from pathlib import Path
    config = load_config(Path(__file__).resolve().parents[1] / "config.yaml", "watermelon-white-1m-v4b")
    event = direct_event("mlb", title="World Series Game 1: A vs B")
    assert classify_sports_event(event, config.trading.gamma, "mlb").accepted
    event["sport"]["series"] = "not-mlb"
    assert not classify_sports_event(event, config.trading.gamma, "mlb").accepted
