"""World Series game scope must not admit series-winner futures.

The missing-future-flag case is a synthetic source-contract regression, not a
claim that an actual future has been collected or traded.
"""
import pytest

from polybot.api.gamma_client import GammaClient
from tests.test_api_contracts import _Session, _direct_sport_event, _market


def _collect(*, question, is_future=None, team_league="mlb", root_series=3):
    market = _market("world-series-scope")
    event = _direct_sport_event("mlb", [market], postseason=True)
    market["question"] = question
    market["slug"] = question.casefold().replace(" ", "-")
    if is_future is not None:
        market["isFuture"] = is_future
    event["sport"]["series"] = root_series
    event["teams"][0]["league"] = team_league
    event["teams"][1]["league"] = team_league
    client = GammaClient(sport_family="mlb")
    client.session = _Session([{"events": [event]}])
    return client.get_all_tradable_markets(0, 0)


def test_world_series_individual_game_is_eligible_with_exact_mlb_identity():
    assert len(_collect(question="Home Club vs Away Club (World Series Game 1)")) == 1
    assert len(_collect(question="Will Home Club win the World Series Game 1?")) == 1


def test_explicit_world_series_future_is_rejected():
    assert not _collect(question="Which team will win the 2026 World Series?", is_future=True)


@pytest.mark.parametrize("future_flag", [None, False])
def test_series_winner_text_is_rejected_even_if_future_flag_is_missing(future_flag):
    assert not _collect(question="Which team will win the 2026 World Series?", is_future=future_flag)


@pytest.mark.parametrize("changes", [{"team_league": "aaa"}, {"root_series": 999}])
def test_world_series_title_cannot_override_wrong_league_identity(changes):
    assert not _collect(question="Home Club vs Away Club (World Series Game 1)", **changes)
