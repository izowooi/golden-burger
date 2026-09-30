"""Extra game-level markets (soccer totals / btts / team_to_score, US totals / spreads): classification, sides,
volume + line gates, discover join, post-game and historical backfill. Fixture: real Gamma payload
(EPL Arsenal vs Leeds 'More Markets', probed 2026-10-01), no network."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from polylab import db as dbmod
from polylab.api import gamma
from polylab.collector import backfill, discover
from polylab.collector import common as C
from polylab.settings import Paths

FX = Path(__file__).parent / "fixtures" / "polymarket_api"
NOW = 1790800000
START = NOW + 3 * 3600


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path))
    return Paths(tmp_path).ensure()


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.log: list[tuple] = []
        self.calls = 0
        self.bytes_in = 0

    def get(self, url, *, bucket, params=None, allow_404=False):
        self.calls += 1
        self.log.append(("GET", url, params))
        return self.handler("GET", url, params or {})

    def post(self, url, *, bucket, json_body, allow_404=False):
        self.calls += 1
        self.log.append(("POST", url, json_body))
        return self.handler("POST", url, json_body)


def epl_events(volume: float = 50_000.0, start: int = START) -> tuple[dict, dict]:
    raw = json.loads((FX / "gamma_events_keyset_epl_more_markets.json").read_text())["events"]
    main, more = copy.deepcopy(raw[0]), copy.deepcopy(raw[1])
    for ev in (main, more):
        ev["startTime"] = gamma.iso(start)
        for m in ev["markets"]:
            m["gameStartTime"] = gamma.iso(start).replace("T", " ").replace("Z", "+00")
            m["volumeNum"] = volume
            m["events"] = [{k: ev.get(k) for k in ("id", "slug", "gameId", "parentEventId", "startTime") if ev.get(k)}]
    return main, more


def by_key(more: dict) -> dict:
    return {(m["sportsMarketType"], m.get("line"), m["groupItemTitle"]): m for m in more["markets"]}


def test_classification_and_sides_from_real_payload():
    main, more = epl_events()
    teams = C.teams_of(main, "soccer")
    ms = by_key(more)
    got = {k: C.market_type_of(m, "soccer") for k, m in ms.items()}
    assert got[("totals", 0.5, "O/U 0.5")] == "total"
    assert got[("both_teams_to_score", None, "Both Teams to Score")] == "btts"
    assert got[("soccer_team_totals", 0.5, "Arsenal FC O/U 0.5")] == "team_to_score"
    assert got[("soccer_team_totals", 1.5, "Arsenal FC O/U 1.5")] is None          # only the 0.5 line = "to score"
    assert got[("spreads", -1.5, "Arsenal FC (-1.5)")] is None                       # soccer spreads: out of scope
    assert got[("both_teams_to_score_first_half", None, "Both Teams to Score in First Half")] is None
    assert got[("first_half_totals", 0.5, "1st Half O/U 0.5")] is None
    assert C.market_type_of(ms[("spreads", -1.5, "Arsenal FC (-1.5)")]) == "spread"  # US sports keep spreads

    def sides(key):
        m = ms[key]
        return [(t["outcome_label"], t["side"]) for t in C.token_rows(m, C.market_type_of(m, "soccer"), teams)]
    assert sides(("totals", 2.5, "O/U 2.5")) == [("Over", "over"), ("Under", "under")]
    assert sides(("both_teams_to_score", None, "Both Teams to Score")) == [("Yes", "yes"), ("No", "no")]
    assert sides(("soccer_team_totals", 0.5, "Arsenal FC O/U 0.5")) == [("Over", "home"), ("Under", "no")]
    assert sides(("soccer_team_totals", 0.5, "Leeds United FC O/U 0.5")) == [("Over", "away"), ("Under", "no")]


def test_volume_floor_and_line_gate():
    _, more = epl_events()
    ms = by_key(more)
    cfg = C.CollectorConfig()
    assert cfg.goal_min_volume == 1_000 and cfg.soccer_total_lines == (0.5, 1.5, 2.5, 3.5)

    def inc(key, vol):
        m = dict(ms[key], volumeNum=vol)
        return C.extra_market_included(m, C.market_type_of(m, "soccer"), "soccer", cfg)
    assert inc(("totals", 0.5, "O/U 0.5"), 1_000) and not inc(("totals", 0.5, "O/U 0.5"), 999)
    assert not inc(("totals", 4.5, "O/U 4.5"), 10**6)                              # line outside the set
    assert inc(("both_teams_to_score", None, "Both Teams to Score"), 12_000)
    assert inc(("soccer_team_totals", 0.5, "Leeds United FC O/U 0.5"), 12_000)
    assert not inc(("spreads", -1.5, "Arsenal FC (-1.5)"), 10**6)
    us = dict(ms[("totals", 2.5, "O/U 2.5")], volumeNum=49_999)
    assert not C.extra_market_included(us, "total", "nba", cfg)
    assert C.extra_market_included(dict(us, volumeNum=50_000), "total", "nba", cfg)


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("POLYLAB_GOAL_MIN_VOLUME", "2500")
    monkeypatch.setenv("POLYLAB_SOCCER_TOTAL_LINES", "2.5,0.5")
    cfg = C.load_config()
    assert cfg.goal_min_volume == 2500 and cfg.soccer_total_lines == (0.5, 2.5)


def soccer_handler(main, more, minor=None, closed_extras=(), requested=None):
    def handler(method, url, p):
        if url.endswith("/markets/keyset"):
            smt = p.get("sports_market_types")
            if p.get("tag_id") != 100350:
                return {"markets": []}
            if p.get("closed") == "true":
                if "condition_ids" in p:
                    return {"markets": []}
                return {"markets": list(closed_extras)}
            if smt == "moneyline":
                return {"markets": main["markets"] + (minor["markets"] if minor else [])}
            assert sorted(smt) == ["both_teams_to_score", "soccer_team_totals", "totals"]
            return {"markets": [m for m in more["markets"] if m["sportsMarketType"] in smt
                                and float(m["volumeNum"]) >= p["volume_num_min"]]}
        if url.endswith("/events/keyset"):
            if "game_id" in p:
                return {"events": [main, more] if str(p["game_id"]) == str(main["gameId"]) else []}
            evs = [main] + ([minor] if minor else [])
            return {"events": [e for e in evs if e["id"] in p.get("id", [])]}
        if url.endswith("/batch-prices-history"):
            if requested is not None:
                requested.extend(p["markets"])
            return {"history": {t: [{"t": p["start_ts"] + 60, "p": 0.4}] for t in p["markets"]}}
        if "/v2/oi" in url:
            return {"data": []}
        if "/v2/resolutions" in url:
            return {"data": []}
        if "/v2/trades" in url:
            raise AssertionError("extras must not walk trades: " + str(p))
        raise AssertionError(url)
    return handler


def test_discover_stores_major_extras_above_floor(paths):
    main, more = epl_events(volume=50_000.0)
    for m in more["markets"]:
        if m.get("line") == 2.5 and m["sportsMarketType"] == "totals":
            m["volumeNum"] = 900.0                                        # below the 1k floor
    minor = copy.deepcopy(main)
    minor.update(id="999", slug="col2-aaa-bbb-2026-10-10", gameId=1)
    for t in minor["teams"]:
        t["league"] = "col2"
    minor["markets"] = [dict(m, conditionId=m["conditionId"] + "x", volumeNum=10**7,
                             events=[{"id": "999", "slug": minor["slug"], "gameId": 1}]) for m in minor["markets"]]
    conn = dbmod.core(paths)
    out = discover.discover(conn, C.CollectorConfig(sports=("soccer",)), FakeClient(soccer_handler(main, more, minor)), NOW)
    s = out["sports"]["soccer"]
    assert s["games"] == 1 and s["skipped_by_league_or_volume"] == 1         # minor league skipped despite volume
    rows = conn.execute("SELECT market_type, line, group_item_title FROM markets ORDER BY market_type, line").fetchall()
    types = [(r[0], r[1]) for r in rows]
    assert types == [("btts", None), ("draw", None), ("moneyline", None), ("moneyline", None),
                     ("team_to_score", 0.5), ("team_to_score", 0.5), ("total", 0.5)]
    assert [r[0] for r in conn.execute("SELECT DISTINCT game_key FROM markets")] == [main["id"]]
    sides = sorted(r[0] for r in conn.execute("SELECT t.side FROM tokens t JOIN markets m USING(condition_id) "
                                              "WHERE m.market_type='team_to_score'"))
    assert sides == ["away", "home", "no", "no"]
    assert conn.execute("SELECT COUNT(*) FROM tokens WHERE side IS NULL").fetchone()[0] == 0
    # poll / stream select every open market of an active game, extras included
    toks = C.active_tokens(conn, START - 60)
    assert {r["market_type"] for r in toks} == {"moneyline", "draw", "total", "btts", "team_to_score"}


def _ended_game(conn, main):
    row = C.game_row(dict(main, ended=True, closed=True, score="2-0", period="VFT"), "soccer", source="discover")
    row["start_time"] = NOW - 5 * 3600
    row["ended_at"] = NOW - 3 * 3600
    C.upsert_game(conn, row, NOW)
    teams = C.teams_of(main, "soccer")
    for m in main["markets"]:
        mt = C.market_type_of(m, "soccer")
        C.upsert_market(conn, dict(C.market_row(m, game_key=main["id"], market_type=mt), closed=1), NOW)
        C.upsert_tokens(conn, m["conditionId"], C.token_rows(m, mt, teams))
    conn.commit()


def test_recent_backfill_adds_extras_by_final_volume(paths):
    main, more = epl_events(volume=20_000.0, start=NOW - 5 * 3600)
    conn = dbmod.core(paths)
    _ended_game(conn, main)
    requested: list[str] = []
    fc = FakeClient(soccer_handler(main, more, requested=requested))
    out = backfill.run_recent(conn, fc, NOW, trades=False, cfg=C.CollectorConfig(sports=("soccer",)))
    assert out["games_done"] == 1 and out["extras"] == 5          # O/U 0.5, 2.5, btts, 2x team_to_score
    extra_tokens = {r[0]: r[1] for r in conn.execute(
        "SELECT t.token_id, t.outcome_index FROM tokens t JOIN markets m USING(condition_id) "
        "WHERE m.market_type NOT IN ('moneyline','draw')")}
    assert len(extra_tokens) == 10
    fetched_extras = [t for t in requested if t in extra_tokens]
    assert sorted(extra_tokens[t] for t in fetched_extras) == [0] * 5  # outcome 0 only
    st = dict(conn.execute("SELECT m.market_type || ':' || COALESCE(m.line, ''), b.status FROM backfill_status b "
                           "JOIN markets m USING(condition_id) WHERE b.kind='prices' AND m.market_type='total'"))
    assert st == {"total:0.5": "done", "total:2.5": "done"}


def test_recent_backfill_skips_extras_for_minor_league(paths):
    main, more = epl_events(volume=10**6, start=NOW - 5 * 3600)
    for t in main["teams"]:
        t["league"] = "arg"
    conn = dbmod.core(paths)
    _ended_game(conn, main)
    fc = FakeClient(soccer_handler(main, more))
    out = backfill.run_recent(conn, fc, NOW, trades=False, cfg=C.CollectorConfig(sports=("soccer",)))
    assert out["extras"] == 0
    assert not [c for c in fc.log if c[1].endswith("/events/keyset") and "game_id" in (c[2] or {})]


def test_historical_extras_staged_then_attached(paths):
    main, more = epl_events(volume=30_000.0, start=NOW - 30 * 86400)
    closed = []
    for m in more["markets"]:
        cm = dict(m, closed=True)
        closed.append(cm)
    # one child event without gameId (observed on ~half of closed soccer child events): parentEventId still joins
    closed[0] = dict(closed[0], events=[{k: v for k, v in closed[0]["events"][0].items() if k != "gameId"}])
    minor = dict(closed[1], conditionId="0xminor", events=[dict(closed[1]["events"][0], slug="arg-aaa-bbb-more-markets",
                                                                 parentEventId="4242")])
    requested: list[str] = []
    conn = dbmod.core(paths)
    backfill.ensure_hist_schema(conn)
    cfg = C.CollectorConfig(sports=("soccer",))
    fc = FakeClient(soccer_handler(main, more, closed_extras=closed + [minor], requested=requested))
    st = backfill.enumerate_extras(conn, "soccer", NOW - 60 * 86400, cfg, fc, float("inf"))
    assert st["done"] and st["staged"] == 5                        # minor league + out-of-scope types dropped
    stats = backfill.process_extras(conn, cfg, fc, NOW, float("inf"))
    assert stats["extras_stored"] == 0 and stats["extras_waiting"] == 5   # game not stored yet: rows wait
    _ended_game(conn, main)
    conn.execute("UPDATE games SET start_time=?, ended_at=?", (NOW - 30 * 86400, NOW - 30 * 86400 + 7200))
    conn.commit()
    stats = backfill.process_extras(conn, cfg, fc, NOW, float("inf"))
    assert stats["extras_stored"] == 5 and stats["extras_waiting"] == 0 and stats["extras_bars"] == 5
    assert len(requested) == 5
    assert conn.execute("SELECT COUNT(*) FROM markets WHERE game_key=? AND market_type IN "
                        "('total','btts','team_to_score')", (main["id"],)).fetchone()[0] == 5
    # the moneyline enumeration checkpoint is untouched by the extras walk
    assert dbmod.get_checkpoint(conn, "backfill.hist.enum.soccer") is None


def test_teams_from_db_uses_abbreviations_for_nfl_spreads(paths):
    """Historical extras resolve sides from the stored game; NFL spread outcomes are abbreviations ("LA", "NYG")."""
    conn = dbmod.core(paths)
    ev = {"id": "g1", "title": "Giants vs. Rams", "slug": "nfl-nyg-la-2026-09-20", "startTime": "2026-09-20T20:25:00Z",
          "teams": [{"name": "Los Angeles Rams", "abbreviation": "la", "ordering": "home"},
                    {"name": "New York Giants", "abbreviation": "nyg", "ordering": "away"}]}
    C.upsert_game(conn, C.game_row(ev, "nfl", source="discover"), NOW)
    teams = discover._teams_from_db(conn, "g1", "nfl")
    m = {"sportsMarketType": "spreads", "outcomes": '["LA", "NYG"]', "clobTokenIds": '["t0", "t1"]'}
    assert [t["side"] for t in C.token_rows(m, "spread", teams)] == ["home", "away"]
