"""World Series individual games use the MLB identity, never a title allowlist."""
from datetime import datetime, timezone

import pytest

from polybot.api.gamma_client import GammaClient
from tests.test_api_contracts import _Session, _direct_sport_event, _market


@pytest.mark.parametrize('season_series', [False, True])
def test_world_series_game_is_discovered_with_root_or_season_identity(season_series):
    market = _market('world-series-game-4')
    event = _direct_sport_event('mlb', [market], postseason=True)
    event['title'] = 'World Series Game 4: Home Club vs. Away Club'
    if season_series:
        year = datetime.now(timezone.utc).year
        event['seriesSlug'] = f'mlb-{year}'
        event['series'] = [{
            'id': '20003', 'ticker': f'mlb-{year}', 'slug': f'mlb-{year}',
            'title': f'MLB {year}', 'seriesType': 'single', 'recurrence': 'daily',
        }]
    client = GammaClient(sport_family='mlb')
    client.session = _Session([{'events': [event]}])
    markets = client.get_all_tradable_markets(5000, 5000)
    assert len(markets) == 1
    assert markets[0]['sportFamily'] == 'mlb'
    assert markets[0]['leagueCode'] == 'mlb'


def test_world_series_title_does_not_override_future_or_minor_league_scope():
    for future, minor in [(True, False), (False, True)]:
        market = _market('not-a-major-individual-game')
        event = _direct_sport_event('mlb', [market], postseason=True)
        if future:
            market['isFuture'] = True
        if minor:
            event['teams'][0]['league'] = 'milb'
        client = GammaClient(sport_family='mlb')
        client.session = _Session([{'events': [event]}])
        assert client.get_all_tradable_markets(5000, 5000) == []


@pytest.mark.parametrize('question', [
    'Which team will win the 2026 World Series?',
    'World Series winner',
    'Winner of the World Series',
])
def test_series_winner_text_is_rejected_without_future_flag(question):
    market = _market('series-winner-no-flag')
    event = _direct_sport_event('mlb', [market], postseason=True)
    market['question'] = question
    client = GammaClient(sport_family='mlb')
    client.session = _Session([{'events': [event]}])
    assert client.get_all_tradable_markets(5000, 5000) == []


def test_win_world_series_game_wording_remains_an_individual_game():
    market = _market('world-series-game-7')
    event = _direct_sport_event('mlb', [market], postseason=True)
    market['question'] = 'Will Home Club win the World Series Game 7?'
    client = GammaClient(sport_family='mlb')
    client.session = _Session([{'events': [event]}])
    assert len(client.get_all_tradable_markets(5000, 5000)) == 1
