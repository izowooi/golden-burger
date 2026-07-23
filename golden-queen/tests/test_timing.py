"""Sports inclusion and deterministic entry-clock contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polybot.config import SportsConfig, TradingConfig
from polybot.strategy.filters import is_excluded_market
from polybot.strategy.timing import evaluate_entry_clock


NOW = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)


def test_sports_are_included_by_default_and_can_be_explicitly_excluded():
    market = {"tags": [{"slug": "sports", "label": "Sports"}]}
    config = TradingConfig()

    assert config.excluded_categories == []
    assert is_excluded_market(market, config.excluded_categories) is False
    assert is_excluded_market(market, ["sports"]) is True


def test_category_exclusion_is_exact_tag_matching_not_a_sports_wildcard():
    nba_market = {"tags": [{"slug": "nba", "label": "NBA"}]}

    assert is_excluded_market(nba_market, ["sports"]) is False
    assert is_excluded_market(nba_market, ["nba"]) is True


def test_non_sports_uses_end_date_clock():
    market = {"endDate": (NOW + timedelta(hours=12)).isoformat()}

    clock = evaluate_entry_clock(market, SportsConfig(), NOW)

    assert clock.valid is True
    assert clock.is_sports is False
    assert clock.phase == "scheduled"
    assert clock.reference == "end_date"
    assert clock.hours_left == 12


def test_sports_pregame_uses_game_start_instead_of_later_end_date():
    market = {
        "gameStartTime": (NOW + timedelta(minutes=30)).isoformat(),
        "endDate": (NOW + timedelta(days=7)).isoformat(),
        "sportsMarketType": "moneyline",
    }

    clock = evaluate_entry_clock(market, SportsConfig(), NOW)

    assert clock.valid is True
    assert clock.is_sports is True
    assert clock.phase == "pregame"
    assert clock.reference == "game_start_time"
    assert clock.hours_left == 0.5
    assert clock.minutes_until_game_start == 30


def test_sports_remains_eligible_during_configured_in_play_window():
    market = {
        "gameStartTime": (NOW - timedelta(minutes=90)).isoformat(),
        "sportsMarketType": "spread",
    }

    clock = evaluate_entry_clock(market, SportsConfig(), NOW)

    assert clock.valid is True
    assert clock.phase == "in_play"
    assert clock.hours_left == -1.5
    assert clock.minutes_until_game_start == -90


def test_old_in_play_is_rejected_but_missing_game_start_falls_back_by_default():
    old = {
        "gameStartTime": (NOW - timedelta(minutes=361)).isoformat(),
        "sportsMarketType": "moneyline",
    }
    missing = {
        "endDate": (NOW + timedelta(hours=2)).isoformat(),
        "tags": [{"slug": "sports"}],
    }

    assert evaluate_entry_clock(old, SportsConfig(), NOW).reason == (
        "game_in_play_too_old"
    )
    fallback = evaluate_entry_clock(missing, SportsConfig(), NOW)
    assert fallback.valid is True
    assert fallback.is_sports is True
    assert fallback.reference == "end_date"
    assert fallback.hours_left == 2


def test_missing_sports_game_start_is_rejected_only_when_explicitly_enabled():
    missing = {
        "endDate": (NOW + timedelta(hours=2)).isoformat(),
        "tags": [{"slug": "sports"}],
    }
    strict = SportsConfig(reject_without_game_start=True)

    assert evaluate_entry_clock(missing, strict, NOW).reason == (
        "sports_missing_game_start"
    )
