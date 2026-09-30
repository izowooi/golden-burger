"""Market classification, side mapping, league filter, book metrics, minute bucketing, game clock."""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from polylab.collector import common as C
from polylab.collector.backfill import history_rows

FX = Path(__file__).parent / "fixtures" / "polymarket_api"


def nba_event():
    return json.loads((FX / "gamma_events_keyset_nba_closed.json").read_text())["events"][0]


def soccer_event():
    """Shape observed 2026-09-30 (Gamma /events/keyset, MLS main event)."""
    def ml(q, git, tok):
        return {"conditionId": "0x" + tok, "id": tok, "question": q, "groupItemTitle": git, "sportsMarketType": "moneyline",
                "outcomes": '["Yes", "No"]', "clobTokenIds": json.dumps([tok + "1", tok + "0"]), "volumeNum": 1000.0,
                "outcomePrices": '["0.3", "0.7"]', "closed": False, "gameStartTime": "2026-09-30 23:30:00+00"}
    return {
        "id": "1017455", "title": "New York Red Bulls vs. St. Louis City SC", "slug": "mls-nyr-stl-2026-09-26",
        "gameId": 90104476, "startTime": "2026-09-30T23:30:00Z", "live": False, "ended": False, "score": "0-0",
        "period": "POST", "seriesSlug": "mls-2025",
        "teams": [{"name": "New York Red Bulls", "alias": "Red Bulls", "abbreviation": "nyr", "ordering": "home", "league": "mls"},
                  {"name": "St. Louis City SC", "alias": "St. Louis", "abbreviation": "stl", "ordering": "away", "league": "mls"}],
        "markets": [ml("Will New York Red Bulls win on 2026-09-26?", "New York Red Bulls", "h"),
                    ml("Will New York Red Bulls vs. St. Louis City SC end in a draw?", "Draw (New York Red Bulls vs. St. Louis City SC)", "d"),
                    ml("Will St. Louis City SC win on 2026-09-26?", "St. Louis City SC", "a")],
    }


def test_nba_teams_and_score_order():
    ev = nba_event()
    t = C.teams_of(ev, "nba")
    assert t.first_is_home is False                     # 'Wizards vs. Nets': away listed first
    assert "Nets" in t.home_aliases and "Wizards" in t.away_aliases
    # Gamma score is in title order: 113-127, Nets (home) won per outcomePrices ["0","1"]
    assert C.parse_score(ev["score"], t.first_is_home) == (127, 113)
    row = C.game_row(ev, "nba", source="discover")
    assert row["status"] == "ended" and row["home_score"] == 127 and row["away_score"] == 113
    assert row["league"] == "nba" and row["polymarket_game_id"] == "20023212"
    assert row["ended_at"] == 1770502903


def test_nba_market_types_and_sides():
    ev = nba_event()
    teams = C.teams_of(ev, "nba")
    by_type = {}
    for m in ev["markets"]:
        by_type.setdefault(C.market_type_of(m), []).append(m)
    assert set(by_type) == {"moneyline", "spread", "total", None}     # player prop -> None (excluded)
    ml = C.token_rows(by_type["moneyline"][0], "moneyline", teams)
    assert [(t["outcome_label"], t["side"]) for t in ml] == [("Wizards", "away"), ("Nets", "home")]
    sp = C.token_rows(by_type["spread"][0], "spread", teams)
    assert [t["side"] for t in sp] == ["home", "away"]                # ["Nets", "Wizards"]
    tot = C.token_rows(by_type["total"][0], "total", teams)
    assert [t["side"] for t in tot] == ["over", "under"]
    assert C.gamma_winner(by_type["moneyline"][0]) == 1


def test_soccer_three_way_sides():
    ev = soccer_event()
    teams = C.teams_of(ev, "soccer")
    assert teams.first_is_home and teams.home == "New York Red Bulls"
    sides = {}
    for m in ev["markets"]:
        mt = C.market_type_of(m)
        rows = C.token_rows(m, mt, teams)
        sides[(mt, rows[0]["side"])] = rows[1]["side"]
    assert sides == {("moneyline", "home"): "no", ("draw", "draw"): "no", ("moneyline", "away"): "no"}
    assert C.league_code(ev) == "mls"
    assert C.game_row(ev, "soccer", source="discover")["home_score"] is None   # '0-0' pre-game is not a score


def test_league_filter_and_env(monkeypatch):
    cfg = C.CollectorConfig()
    assert C.soccer_game_included("epl", 5_000, cfg)
    assert not C.soccer_game_included("epl", 10, cfg)
    assert not C.soccer_game_included("col2", 20_000, cfg)          # Primera B (Colombia): minor
    assert not C.soccer_game_included("col2", 10**9, cfg)           # minor leagues are out of scope by default
    assert not C.soccer_game_included("arg", 10**6, cfg) and not C.soccer_game_included("fif", 10**6, cfg)
    assert C.MAJOR_SOCCER_LEAGUES == {"epl", "lal", "bun", "sea", "fl1", "mls", "ucl", "uel", "fifwc", "euc", "unl"}
    monkeypatch.setenv("POLYLAB_SOCCER_MIN_VOLUME_OTHER", "100000")
    assert C.soccer_game_included("col2", 150_000, C.load_config())  # env override re-enables big minor games
    monkeypatch.setenv("POLYLAB_SOCCER_LEAGUES", "+col2")
    monkeypatch.setenv("POLYLAB_SOCCER_MIN_VOLUME_OTHER", "1e9")
    cfg2 = C.load_config()
    assert "col2" in cfg2.soccer_leagues and "epl" in cfg2.soccer_leagues and cfg2.soccer_min_volume_other == 1e9
    monkeypatch.setenv("POLYLAB_SOCCER_LEAGUES", "epl")
    assert C.load_config().soccer_leagues == frozenset({"epl"})


def test_league_code_from_slug_when_no_teams():
    assert C.league_code({"slug": "fif-sey-sri-2026-09-30"}) == "fif"


def test_book_metrics_and_imbalance():
    book = {"timestamp": "1790779105520",
            "bids": [{"price": "0.40", "size": "100"}, {"price": "0.45", "size": "50"}, {"price": "0.44", "size": "0"}],
            "asks": [{"price": "0.50", "size": "30"}, {"price": "0.47", "size": "10"}]}
    m = C.book_metrics(book)
    assert m["best_bid"] == 0.45 and m["best_ask"] == 0.47
    assert m["mid"] == pytest.approx(0.46) and m["spread"] == pytest.approx(0.02)
    assert m["imb_l1"] == pytest.approx((50 - 10) / 60)
    assert m["imb_l5"] == pytest.approx((150 - 40) / 190)
    assert m["bid_depth_usd"] == pytest.approx(0.45 * 50 + 0.40 * 100)
    lv = json.loads(zlib.decompress(m["levels_z"]))
    assert lv["b"][0] == [0.45, 50.0] and lv["a"][0] == [0.47, 10.0] and len(lv["b"]) == 2
    assert m["exchange_ts"] == 1790779105 and not m["crossed"] and C.mid_is_price(m)


def test_placeholder_and_crossed_books_are_not_prices():
    wide = C.book_metrics({"bids": [{"price": "0.01", "size": "1"}], "asks": [{"price": "0.99", "size": "1"}]})
    assert wide["mid"] == 0.5 and not C.mid_is_price(wide)
    crossed = C.book_metrics({"bids": [{"price": "0.6", "size": "1"}], "asks": [{"price": "0.5", "size": "1"}]})
    assert crossed["crossed"] and not C.mid_is_price(crossed)
    empty = C.book_metrics({"bids": [], "asks": []})
    assert empty["mid"] is None and empty["imb_l1"] is None


def test_minute_bucketing_last_point_wins():
    assert C.minute(1790779105.9) == 1790779080
    rows = history_rows([(1772665237, 0.5), (1772665255, 0.52), (1772665225, 0.49), (1772665291, 0.6)], "tok")
    assert rows == [("tok", 1772665200, "history", 0.52, None), ("tok", 1772665260, "history", 0.6, None)]


@pytest.mark.parametrize("sport,period,elapsed,expected", [
    ("soccer", "2H", "81", 81.0),
    ("soccer", "1H", "45+2", 47.0),
    ("soccer", "HT", "", 45.0),
    ("soccer", "FT", "90", None),
    ("nba", "Q3", "5:30", 24 + 6.5),
    ("nba", "OT", "2:00", 48 + 3.0),
    ("nba", "Q1", None, 0.0),
    ("nfl", "Q4", "15:00", 45.0),
    ("nfl", "HT", None, 30.0),
    ("nhl", "P2", "10:00", 30.0),
    ("nhl", "3", "00:00", 60.0),
    ("mlb", "Top 3", None, 2.0),
    ("mlb", "Bot 9", None, 8.5),
    ("mlb", "End 5", None, 5.0),
    ("mlb", "VFT", None, None),
])
def test_game_minute(sport, period, elapsed, expected):
    got = C.game_minute(sport, period, elapsed)
    assert got == (pytest.approx(expected) if expected is not None else None)


def test_upsert_game_never_downgrades_status(tmp_path):
    from polylab import db as dbmod
    conn = dbmod.connect(tmp_path / "c.db", dbmod.CORE_SCHEMA)
    base = {"game_key": "g", "sport": "nba", "source": "discover"}
    C.upsert_game(conn, {**base, "status": "live"})
    C.upsert_game(conn, {**base, "status": "scheduled"})
    assert conn.execute("SELECT status FROM games").fetchone()[0] == "live"
    C.upsert_game(conn, {**base, "status": "ended"})
    C.upsert_game(conn, {**base, "status": "live"})
    assert conn.execute("SELECT status FROM games").fetchone()[0] == "ended"
