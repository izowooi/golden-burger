"""The research-only context payload must not alter quotes or live evidence."""
import json
from copy import deepcopy

import pytest

from polybot.db.models import MarketSnapshot
from polybot.strategy.scanner import _with_source_sport_context
from tests.test_scanner import NOW, _event, _scanner, _triad


@pytest.mark.parametrize("simulation", [False, True])
def test_context_is_persisted_only_for_simulation(tmp_path, simulation):
    event = _event()
    event.update(score="1-0", updatedAt="2026-09-07T09:00:00Z")
    markets = _triad(event=event)
    fixture = _scanner(tmp_path, markets)
    session, scanner = fixture[0], fixture[2]
    scanner.clob.simulation_mode = simulation
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
