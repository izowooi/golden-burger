from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from polybot.api.clob_client import (
    BookAttempt,
    BookCollection,
    RawPayload,
    ResolutionResult,
)
from polybot.api.gamma_client import EventPage, EventSweep
from polybot.collector import (
    Collector,
    classify_match_winner,
    classify_soccer_league,
)
from polybot.config import load_config
from polybot.db.repository import ResearchRepository


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 22, 16, 16, tzinfo=timezone.utc)


def event(*, live=True, ended=False):
    return {
        "id": "event-1",
        "title": "Team A vs Team B",
        "live": live,
        "ended": ended,
        "gameStatus": "2H",
        "sport": {"id": 2, "sport": "epl", "name": "Premier League"},
        "seriesSlug": "premier-league-2026",
        "tags": [
            {"slug": "sports"},
            {"slug": "soccer"},
            {"slug": "premier-league"},
            {"slug": "EPL"},
        ],
        "teams": [
            {"name": "Team A", "abbreviation": "A", "league": "epl"},
            {"name": "Team B", "abbreviation": "B", "league": "epl"},
        ],
    }


def market(**overrides):
    result = {
        "id": "market-1",
        "conditionId": "condition-1",
        "question": "Team A vs Team B",
        "groupItemTitle": "",
        "sportsMarketType": "moneyline",
        "outcomes": '["Team A","Team B"]',
        "clobTokenIds": '["team-a","team-b"]',
        "outcomePrices": '["0.97","0.03"]',
        "endDate": "2026-08-22T20:00:00Z",
        "gameStartTime": "2026-08-22T15:00:00Z",
        "liquidityNum": 25,
        "volumeNum": 0,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "feesEnabled": True,
        "negRisk": False,
        "feeSchedule": {"rate": 0.05, "takerOnly": True},
        "events": [event()],
    }
    result.update(overrides)
    return result


class FakeGamma:
    def __init__(self, source=None):
        self.source = source or market()

    def fetch_live_events(self, run_id, *, observed_at):
        source_event = event()
        source_event["markets"] = [
            {key: value for key, value in self.source.items() if key != "events"}
        ]
        if self.source.get("events"):
            source_event.update(self.source["events"][0])
            source_event["markets"] = [
                {key: value for key, value in self.source.items() if key != "events"}
            ]
        page = EventPage(
            1,
            "gamma-request",
            observed_at.isoformat().replace("+00:00", "Z"),
            "a" * 64,
            b"{}",
            (source_event,),
            None,
            None,
        )
        return EventSweep((page,), True)


class IncompleteGamma(FakeGamma):
    def fetch_live_events(self, run_id, *, observed_at):
        complete = super().fetch_live_events(
            run_id, observed_at=observed_at
        )
        return EventSweep(complete.pages, False)


class FakeClob:
    def __init__(self, *, ask=0.97, bid=0.96, bid_levels=None):
        self.ask = ask
        self.bid = bid
        self.bid_levels = bid_levels

    def fetch_books(self, run_id, token_ids):
        requested = list(dict.fromkeys(token_ids))
        books = {}
        for token in requested:
            if token == "team-a":
                bids = self.bid_levels or [{"price": str(self.bid), "size": "20"}]
                asks = [{"price": str(self.ask), "size": "20"}]
            else:
                bids = [{"price": "0.02", "size": "20"}]
                asks = [{"price": "0.03", "size": "20"}]
            books[token] = {"asset_id": token, "bids": bids, "asks": asks}
        attempts = {
            token: BookAttempt(
                token, "OBSERVED", "book-request", "2026-08-22T15:31:02Z"
            )
            for token in requested
        }
        payload = RawPayload(
            "book-request", "2026-08-22T15:31:02Z", "b" * 64, b"[]"
        )
        return BookCollection(books, attempts, (payload,))

    def fetch_resolution(self, run_id, condition_id):
        return ResolutionResult(
            condition_id,
            "OPEN",
            "2026-08-22T15:31:03Z",
            "resolution-request",
            None,
            {"closed": False},
            None,
        )


def configured(tmp_path, *, compact_grid=False):
    config = load_config(ROOT / "config.yaml", "watermelon-white-1m-v3")
    experiment = replace(
        config.trading.experiment,
        start_utc=NOW.replace(minute=15),
        entry_end_utc=NOW.replace(day=23),
        followup_end_utc=NOW.replace(day=24),
    )
    config = replace(
        config,
        trading=replace(config.trading, experiment=experiment),
    )
    if compact_grid:
        experiment = replace(
            config.trading.experiment,
            entry_thresholds=(0.97,),
            stop_levels=(0.80,),
        )
        config = replace(
            config,
            trading=replace(config.trading, experiment=experiment),
        )
    return replace(config, db_path=tmp_path / "trades_sim.db")


def repository_for(config):
    return ResearchRepository(
        config.db_path,
        busy_timeout_ms=1000,
        data_contract=config.trading.data_contract,
    )


def test_classifier_accepts_only_whole_match_winners() -> None:
    source = market()
    labels = ["Team A", "Team B"]
    tokens = ["a", "b"]
    probabilities = [0.97, 0.03]
    match_class, eligible, _, reasons = classify_match_winner(
        event(), source, labels, tokens, probabilities
    )
    assert match_class == "ALIGNED_TWO_TEAM_MONEYLINE"
    assert eligible == (0, 1)
    assert reasons == []

    for market_type in ("child_moneyline", "spreads", "totals"):
        rejected = {**source, "sportsMarketType": market_type}
        _, indices, _, rejected_reasons = classify_match_winner(
            event(), rejected, labels, tokens, probabilities
        )
        assert indices == ()
        assert "NOT_TOP_LEVEL_MONEYLINE" in rejected_reasons


def test_soccer_league_classifier_rejects_esports_and_non_allowlisted_leagues(
    tmp_path,
) -> None:
    gamma = configured(tmp_path).trading.gamma
    accepted, reasons = classify_soccer_league(event(), gamma)
    assert accepted["league_code"] == "epl"
    assert accepted["sport_family"] == "soccer"
    assert reasons == []

    esports = {
        **event(),
        "sport": {"sport": "lol", "name": "League of Legends"},
        "tags": [{"slug": "esports"}],
        "teams": [
            {"name": "Team A", "league": "lol"},
            {"name": "Team B", "league": "lol"},
        ],
    }
    _, esports_reasons = classify_soccer_league(esports, gamma)
    assert "ESPORTS_EXCLUDED" in esports_reasons
    assert "LEAGUE_NOT_ALLOWED" in esports_reasons
    assert "SOCCER_TAG_MISSING" in esports_reasons

    championship = {
        **event(),
        "sport": {"sport": "efl", "name": "Championship"},
        "teams": [
            {"name": "Team A", "league": "efl"},
            {"name": "Team B", "league": "efl"},
        ],
    }
    _, championship_reasons = classify_soccer_league(championship, gamma)
    assert championship_reasons == ["LEAGUE_NOT_ALLOWED"]


def test_classifier_keeps_only_yes_for_negrisk_team_and_excludes_draw() -> None:
    team_market = market(
        question="Will Team A win on 2026-08-22?",
        groupItemTitle="Team A",
        outcomes='["Yes","No"]',
        clobTokenIds='["yes","no"]',
        outcomePrices='["0.97","0.03"]',
        negRisk=True,
    )
    result = classify_match_winner(
        event(), team_market, ["Yes", "No"], ["yes", "no"], [0.97, 0.03]
    )
    assert result[0] == "NEGRISK_TEAM_WIN_YES"
    assert result[1] == (0,)
    assert result[3] == []

    draw_market = {**team_market, "groupItemTitle": "Draw", "question": "Draw?"}
    result = classify_match_winner(
        event(), draw_market, ["Yes", "No"], ["yes", "no"], [0.20, 0.80]
    )
    assert result[1] == ()
    assert "DRAW_OUTCOME_EXCLUDED" in result[3]


def test_low_volume_is_not_an_entry_exclusion_and_initial_grid_opens(
    tmp_path,
) -> None:
    config = configured(tmp_path)
    repository = repository_for(config)
    result = Collector(config, repository, FakeGamma(), FakeClob()).collect(
        "run-1", now=NOW
    )
    assert result["eligible_markets"] == 1
    assert result["eligible_outcomes"] == 2
    assert result["episodes_opened"] == 3
    assert result["stop_attempts"] == 0
    assert result["stop_exits"] == 0
    with repository.connect() as connection:
        market_row = connection.execute(
            "SELECT sports_market_type,match_winner_class,liquidity,volume_total,"
            "sport_family,league_code,league_name "
            "FROM market_observations WHERE eligible=1"
        ).fetchone()
        episodes = connection.execute(
            "SELECT threshold,entry_provenance,cadence_arm "
            "FROM hypothetical_episodes ORDER BY threshold"
        ).fetchall()
        policies = connection.execute(
            "SELECT COUNT(*) FROM counterfactual_exit_policies"
        ).fetchone()[0]
        entry_cycle_paths = connection.execute(
            "SELECT COUNT(*) FROM episode_path_observations"
        ).fetchone()[0]
    assert tuple(market_row) == (
        "moneyline", "ALIGNED_TWO_TEAM_MONEYLINE", 25, 0,
        "soccer", "epl", "Premier League",
    )
    assert [row[0] for row in episodes] == [0.95, 0.96, 0.97]
    assert {row[1] for row in episodes} == {"FIRST_FULL_DEPTH_ABOVE"}
    assert {row[2] for row in episodes} == {"FAST_1M"}
    assert policies == 3 * 7
    assert entry_cycle_paths == 0


def test_incomplete_event_cursor_preserves_raw_failure_evidence(tmp_path) -> None:
    config = configured(tmp_path)
    repository = repository_for(config)
    with pytest.raises(RuntimeError, match="event keyset"):
        Collector(config, repository, IncompleteGamma(), FakeClob()).collect(
            "run-incomplete", now=NOW
        )
    with repository.connect() as connection:
        sweep = connection.execute(
            "SELECT page_count,event_count,market_count,cursor_complete "
            "FROM market_sweeps WHERE run_id='run-incomplete'"
        ).fetchone()
        payloads = connection.execute(
            "SELECT COUNT(*) FROM raw_payloads WHERE run_id='run-incomplete'"
        ).fetchone()[0]
        issue = connection.execute(
            "SELECT severity,issue_type FROM data_quality_issues "
            "WHERE run_id='run-incomplete'"
        ).fetchone()
    assert tuple(sweep) == (1, 1, 1, 0)
    assert payloads == 1
    assert tuple(issue) == ("CRITICAL", "GAMMA_CURSOR_INCOMPLETE")


def test_upward_cross_is_distinguished_from_first_observation(tmp_path) -> None:
    config = configured(tmp_path)
    repository = repository_for(config)
    first = Collector(
        config, repository, FakeGamma(), FakeClob(ask=0.94, bid=0.93)
    ).collect("run-1", now=NOW)
    assert first["episodes_opened"] == 0
    second = Collector(
        config, repository, FakeGamma(), FakeClob(ask=0.97, bid=0.96)
    ).collect(
        "run-2",
        now=datetime(2026, 8, 22, 16, 17, tzinfo=timezone.utc),
    )
    assert second["episodes_opened"] == 3
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT threshold,prior_entry_vwap,entry_provenance "
            "FROM signal_decisions WHERE run_id='run-2' AND episode_id IS NOT NULL "
            "ORDER BY threshold"
        ).fetchall()
    assert [row[0] for row in rows] == [0.95, 0.96, 0.97]
    assert all(row[1] == pytest.approx(0.94) for row in rows)
    assert {row[2] for row in rows} == {"UPWARD_CROSS"}


def test_pre_game_and_finished_markets_are_excluded(tmp_path) -> None:
    config = configured(tmp_path)
    for index, source in enumerate(
        (
            market(gameStartTime="2026-08-22T17:00:00Z"),
            market(events=[event(live=False, ended=True)]),
            market(events=[{**event(), "teams": []}]),
        )
    ):
        path = tmp_path / str(index)
        adjusted = replace(config, db_path=path / "trades_sim.db")
        repository = repository_for(adjusted)
        result = Collector(
            adjusted, repository, FakeGamma(source), FakeClob()
        ).collect(f"run-{index}", now=NOW)
        assert result["eligible_markets"] == 0
        assert result["episodes_opened"] == 0


def test_stop_trigger_records_exact_depth_gap(tmp_path) -> None:
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    Collector(config, repository, FakeGamma(), FakeClob()).collect(
        "run-1", now=NOW
    )
    result = Collector(
        config,
        repository,
        FakeGamma(),
        FakeClob(
            ask=0.97,
            bid_levels=[
                {"price": "0.79", "size": "2"},
                {"price": "0.78", "size": "20"},
            ],
        ),
    ).collect(
        "run-2", now=datetime(2026, 8, 22, 16, 17, tzinfo=timezone.utc)
    )
    assert result["stop_attempts"] == 1
    assert result["stop_exits"] == 1
    with repository.connect() as connection:
        attempt = connection.execute(
            "SELECT stop_price,trigger_best_bid,exit_vwap,status,drop_from_prior "
            "FROM stop_execution_attempts"
        ).fetchone()
    assert tuple(attempt[:2]) == pytest.approx((0.80, 0.79))
    assert attempt[2] < 0.79
    assert attempt[3] == "FULL_EXIT"
    # The entry-cycle bid is intentionally not treated as a prior path point.
    assert attempt[4] is None


def test_partial_stop_retries_only_remaining_shares(tmp_path) -> None:
    config = configured(tmp_path, compact_grid=True)
    repository = repository_for(config)
    Collector(config, repository, FakeGamma(), FakeClob()).collect(
        "run-1", now=NOW
    )
    partial = Collector(
        config,
        repository,
        FakeGamma(),
        FakeClob(
            ask=0.97,
            bid_levels=[{"price": "0.79", "size": "2"}],
        ),
    ).collect(
        "run-2", now=datetime(2026, 8, 22, 16, 17, tzinfo=timezone.utc)
    )
    assert partial["stop_attempts"] == 1
    assert partial["stop_exits"] == 0
    completed = Collector(
        config,
        repository,
        FakeGamma(),
        FakeClob(
            ask=0.97,
            bid_levels=[{"price": "0.77", "size": "20"}],
        ),
    ).collect(
        "run-3", now=datetime(2026, 8, 22, 16, 18, tzinfo=timezone.utc)
    )
    assert completed["stop_attempts"] == 1
    assert completed["stop_exits"] == 1
    with repository.connect() as connection:
        attempts = connection.execute(
            "SELECT requested_shares,filled_shares,remaining_shares,status "
            "FROM stop_execution_attempts ORDER BY observed_at"
        ).fetchall()
    assert attempts[0][1] == 2.0
    assert attempts[0][3] == "PARTIAL_FILL"
    assert attempts[1][0] == pytest.approx(attempts[0][2])
    assert attempts[1][2] == pytest.approx(0)
    assert attempts[1][3] == "FULL_EXIT"
