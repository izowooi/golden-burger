"""World Series match winners must not admit series-winner futures."""
import pytest

from polybot.api.gamma_client import GammaClient
from tests.test_api_contracts import _Session, _direct_sport_event, _market


@pytest.mark.parametrize("postseason", [False, True])
def test_world_series_whole_game_accepts_root_and_season_identity(postseason):
    market = _market("world-series-game-1")
    event = _direct_sport_event("mlb", [market], postseason=postseason)
    event["title"] = "World Series Game 1: Home Club vs Away Club"
    market["question"] = "Home Club vs Away Club - World Series Game 1"
    client = GammaClient(sport_family="mlb")
    client.session = _Session([{"events": [event]}])

    observed = client.get_all_tradable_markets(0, 0)

    assert len(observed) == 1
    assert observed[0]["sportFamily"] == "mlb"


def test_win_world_series_game_wording_is_not_a_series_winner():
    market = _market("world-series-game-7")
    event = _direct_sport_event("mlb", [market], postseason=True)
    market["question"] = "Will Home Club win the World Series Game 7?"
    client = GammaClient(sport_family="mlb")
    client.session = _Session([{"events": [event]}])
    assert len(client.get_all_tradable_markets(0, 0)) == 1


@pytest.mark.parametrize("kind", ["explicit_future", "series_market_type", "title_only"])
def test_world_series_winner_is_not_a_whole_game(kind):
    market = _market("world-series-winner")
    event = _direct_sport_event("mlb", [market], postseason=True)
    market["question"] = "Home Club vs Away Club - World Series winner"
    market["groupItemTitle"] = "Series Winner"
    if kind == "explicit_future":
        market["isFuture"] = True
    elif kind == "series_market_type":
        market["sportsMarketType"] = "series_winner"
    # The title-only case deliberately retains an incorrect moneyline label:
    # a contradictory explicit series-winner scope must still fail closed.
    client = GammaClient(sport_family="mlb")
    client.session = _Session([{"events": [event]}])

    assert client.get_all_tradable_markets(0, 0) == []
