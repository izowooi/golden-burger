"""The research-only context payload must not alter quotes or live evidence."""
import json
from copy import deepcopy

import pytest

from polybot.db.models import MarketSnapshot
from polybot.strategy.scanner import _with_source_sport_context
from tests.test_scanner import NOW, _event, _scanner, _triad


@pytest.mark.parametrize("simulation", [False, True])
def test_context_is_persisted_only_for_simulation(tmp_path, simulation, monkeypatch):
    event = _event()
    event.update(score="1-0", updatedAt="2026-09-07T09:00:00Z")
    markets = _triad(event=event)
    fixture = _scanner(tmp_path, markets, enable_research_raw=simulation)
    session, scanner = fixture[0], fixture[2]
    scanner.clob.simulation_mode = simulation
    if simulation:
        from sqlalchemy import text
        from polybot.strategy import research_raw
        session.execute(text("CREATE TABLE run_audits(run_id TEXT PRIMARY KEY, config_hash TEXT, job_name TEXT, mode TEXT)"))
        session.execute(text("INSERT INTO run_audits VALUES('raw-run','cfg','shadow','sim')"))
        session.commit()
        monkeypatch.setattr(research_raw, "current_run_id", lambda: "raw-run")
        scanner.config.strategy_source_digest = "a" * 64
        scanner.clob.get_cached_research_observation = lambda token: {"status": "FULL", "reason": "fixture", "book_json": scanner.clob.get_cached_book_evidence(token), "requested_at": NOW, "received_at": NOW}
    before = scanner.clob.get_cached_book_evidence("yes-HOME")
    assert scanner.save_market_snapshots(markets, now=NOW) == 6
    rows = session.query(MarketSnapshot).all()
    selected = next(row for row in rows if row.token_id == "yes-HOME")
    actual = json.loads(selected.book_json)
    assert actual["asks"] == json.loads(before)["asks"]
    assert actual["bids"] == json.loads(before)["bids"]
    assert selected.probability == scanner.clob.walks["yes-HOME"].vwap
    assert ("source_sport_context" in actual) is simulation
    if simulation:
        context = actual["source_sport_context"]
        assert context["fields"]["score"] == "1-0"
        assert context["fields"]["period"] == "1H"
        assert context["market_fields"]["volumeNum"] == 20_000
        assert context["market_fields"]["volume24hr"] == 3_000
        assert context["market_fields"]["liquidityNum"] == 10_000
        assert context["observation_basis"] == "CYCLE_REFERENCE_UTC_NOT_SOURCE_RECEIPT"
    assert json.loads(scanner.clob.get_cached_book_evidence("yes-HOME")) == json.loads(before)
    session.close()


@pytest.mark.parametrize("family,period,elapsed", [
    ("mlb", "Top 7th", None),
    ("nfl", "Q3", "12:24"),
    ("nba", "Q4", {"display": "01:04", "private": "omit"}),
])
def test_native_context_retains_raw_fields_without_converting_minutes(family, period, elapsed):
    event = {"id": "event", "period": period, "elapsed": elapsed, "score": "3-2", "live": True, "ended": False, "unrelated": "omit"}
    original = deepcopy(event)
    result = json.loads(_with_source_sport_context('{"asks":[],"bids":[]}', event, {}, family, NOW))
    context = result["source_sport_context"]
    assert context["sport_family"] == family
    assert context["fields"]["period"] == period
    assert context["fields"]["elapsed"] == ({"display": "01:04"} if isinstance(elapsed, dict) else elapsed)
    assert "minutes" not in context
    assert "unrelated" not in context["fields"]
    assert event == original



def test_structured_score_keeps_only_public_flat_fields():
    event = {"score": {"home": 3, "away": 2, "homeScore": "3", "awayScore": "2", "unrelated": "omit"}}
    result = json.loads(_with_source_sport_context('{"asks":[],"bids":[]}', event, {}, "mlb", NOW))
    assert result["source_sport_context"]["fields"]["score"] == {"home": 3, "away": 2, "homeScore": "3", "awayScore": "2"}


def test_market_gate_context_keeps_observed_values_and_missing_fields():
    market = {
        "volume": "25001.50", "volumeNum": 25001.5, "volume24hr": 101.2,
        "liquidityNum": 6000.25, "active": True, "closed": False,
        "acceptingOrders": True, "enableOrderBook": True,
        "sportsMarketType": "moneyline", "negRisk": False,
        "gameStartTime": "2026-09-07T20:10:00Z",
        "feesEnabled": True, "fee_rate_bps": None,
        "unrelated": "omit",
    }
    original = deepcopy(market)
    result = json.loads(_with_source_sport_context('{"asks":[],"bids":[]}', {}, market, "mlb", NOW))
    fields = result["source_sport_context"]["market_fields"]
    assert fields == {key: value for key, value in market.items() if key != "unrelated"} | {
        "liquidity": None, "feeRate": None, "feeExponent": None, "feeTakerOnly": None,
    }
    assert fields["fee_rate_bps"] is None
    assert result["source_sport_context"]["fields"]["elapsed"] is None
    assert market == original
