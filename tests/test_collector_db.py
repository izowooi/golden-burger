"""discover / poll / backfill against a temp POLYLAB_ROOT with a fake HTTP client (no network)."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from polylab import db as dbmod
from polylab.api import gamma
from polylab.collector import backfill, discover, poll
from polylab.collector import common as C
from polylab.settings import Paths

FX = Path(__file__).parent / "fixtures" / "polymarket_api"
NOW = 1790800000  # 2026-10-01T~07:06Z


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


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path))
    return Paths(tmp_path).ensure()


def live_nba_event(start=NOW - 1800):
    ev = copy.deepcopy(json.loads((FX / "gamma_events_keyset_nba_closed.json").read_text())["events"][0])
    ev.update(closed=False, ended=False, live=True, score="50-48", period="Q3", elapsed="5:30",
              startTime=gamma.iso(start), finishedTimestamp=None)
    for m in ev["markets"]:
        m.update(closed=False, outcomePrices='["0.5", "0.5"]', umaResolutionStatus=None,
                 gameStartTime=ev["startTime"].replace("T", " ").replace("Z", "+00"))
        m["events"] = [{"id": ev["id"], "gameId": ev["gameId"], "startTime": ev["startTime"], "slug": ev["slug"]}]
    return ev


def gamma_handler(ev, closed_markets=()):
    def handler(method, url, p):
        if url.endswith("/markets/keyset"):
            if p.get("closed") == "true":
                return {"markets": [m for m in closed_markets if m["conditionId"] in p.get("condition_ids", [])]}
            smt = p.get("sports_market_types")
            if p.get("tag_id") != 745:
                return {"markets": []}
            if smt == "moneyline":
                return {"markets": [m for m in ev["markets"] if m["sportsMarketType"] == "moneyline"]}
            return {"markets": [m for m in ev["markets"] if m["sportsMarketType"] in smt
                                and float(m["volumeNum"]) >= p["volume_num_min"]]}
        if url.endswith("/events/keyset"):
            return {"events": [ev] if ev["id"] in p.get("id", []) else []}
        if url.endswith("/books"):
            return [{"asset_id": x["token_id"], "timestamp": str(NOW * 1000),
                     "bids": [{"price": "0.48", "size": "100"}], "asks": [{"price": "0.50", "size": "80"}]}
                    for x in p][:-1]      # drop the last token -> missing_book quality event
        if "/v2/live-volume" in url:
            return {"data": {"taker_volume_total": 1.0, "conditions": []}}
        if "/v2/resolutions" in url:
            return {"data": []}
        raise AssertionError(url)
    return handler


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("games", "markets", "tokens")}


def test_discover_idempotent_and_line_floor(paths):
    ev = live_nba_event()
    fc = FakeClient(gamma_handler(ev))
    conn = dbmod.core(paths)
    cfg = C.CollectorConfig(sports=("nba",), line_min_volume=100_000)
    s1 = discover.discover(conn, cfg, fc, NOW)
    c1 = counts(conn)
    s2 = discover.discover(conn, cfg, fc, NOW + 60)
    assert counts(conn) == c1
    # moneyline + totals (224k) but not the 69k spread nor the player prop
    types = sorted(r[0] for r in conn.execute("SELECT market_type FROM markets"))
    assert types == ["moneyline", "total"] and c1 == {"games": 1, "markets": 2, "tokens": 4}
    g = conn.execute("SELECT * FROM games").fetchone()
    assert g["status"] == "live" and g["home_team"] and g["home_score"] == 48 and g["away_score"] == 50
    sides = dict(conn.execute("SELECT outcome_label, side FROM tokens"))
    assert sides == {"Wizards": "away", "Nets": "home", "Over": "over", "Under": "under"}
    assert s1["sports"]["nba"]["games"] == 1 and s2["sports"]["nba"]["lines"] == 1


def test_discover_marks_closed_and_resolved(paths):
    ev = live_nba_event(start=NOW - 4 * 3600)
    fc = FakeClient(gamma_handler(ev))
    conn = dbmod.core(paths)
    cfg = C.CollectorConfig(sports=("nba",), line_min_volume=100_000)
    discover.discover(conn, cfg, fc, NOW)
    closed = []
    for m in ev["markets"]:
        cm = dict(m, closed=True, umaResolutionStatus="resolved", outcomePrices='["0", "1"]', closedTime="2026-10-01 06:00:00+00")
        closed.append(cm)
    ev2 = dict(ev, live=False, ended=True, closed=True, score="100-110", period="VFT",
               finishedTimestamp="2026-10-01T05:50:00Z")
    fc2 = FakeClient(gamma_handler(ev2, closed_markets=closed))
    discover.discover(conn, cfg, fc2, NOW + 600)
    g = conn.execute("SELECT status, home_score, away_score, ended_at FROM games").fetchone()
    assert tuple(g)[:3] == ("ended", 110, 100) and g["ended_at"] is not None
    rows = conn.execute("SELECT market_type, closed, resolved_outcome_index, resolution_source FROM markets").fetchall()
    assert all(r["closed"] == 1 and r["resolved_outcome_index"] == 1 for r in rows)
    assert {r["resolution_source"] for r in rows} == {"gamma_outcome_prices"}


def test_poll_run_once_writes_books_bars_states(paths):
    ev = live_nba_event()
    fc = FakeClient(gamma_handler(ev))
    conn = dbmod.core(paths)
    discover.discover(conn, C.CollectorConfig(sports=("nba",), line_min_volume=100_000), fc, NOW)
    conn.close()
    out = poll.run_once(paths, client=fc, ts=NOW)
    assert out["ok"] and out["tokens"] == 4 and out["books"] == 3 and out["bars"] == 3 and out["gamma_states"] == 1
    conn = dbmod.core(paths)
    bars = conn.execute("SELECT ts, source, price FROM price_bars").fetchall()
    assert {tuple(b) for b in bars} == {(C.minute(C.now()), "poll_mid", 0.49)} or all(b["price"] == 0.49 for b in bars)
    kinds = [r[0] for r in conn.execute("SELECT kind FROM quality_events")]
    assert kinds == ["missing_book"]
    st = conn.execute("SELECT source, period, game_minute, home_score FROM game_states").fetchone()
    assert tuple(st) == ("gamma", "Q3", pytest.approx(30.5), 48)
    ym = dbmod.year_month(NOW)
    b = sqlite3.connect(paths.books_db(ym)).execute("SELECT COUNT(*), MIN(imb_l1) FROM book_snapshots").fetchone()
    assert b[0] == 3 and b[1] == pytest.approx((100 - 80) / 180)
    # unchanged gamma state is not rewritten
    out2 = poll.run_once(paths, client=fc, ts=NOW + 60)
    assert out2["gamma_states"] == 0


def test_poll_no_active_games_is_cheap(paths):
    fc = FakeClient(lambda *a: pytest.fail("no HTTP expected"))
    out = poll.run_once(paths, client=fc, ts=NOW)
    assert out["ok"] and out["tokens"] == 0 and fc.calls == 0


def market_page(i: int, eid: str, start: str):
    return {"conditionId": f"0xc{i}", "id": str(i), "question": "A vs. B", "sportsMarketType": "moneyline",
            "outcomes": '["A", "B"]', "clobTokenIds": json.dumps([f"t{i}a", f"t{i}b"]), "outcomePrices": '["1", "0"]',
            "closed": True, "umaResolutionStatus": "resolved", "volumeNum": 5000, "gameStartTime": start,
            "events": [{"id": eid, "title": "A vs. B", "slug": f"nhl-a-b-{i}", "startTime": start.replace(" ", "T")[:19] + "Z"}]}


def test_historical_enumeration_resumes(paths, monkeypatch):
    pages = {None: {"markets": [market_page(1, "e1", "2026-03-01 00:00:00+00")], "next_cursor": "p2"},
             "p2": {"markets": [market_page(2, "e2", "2026-03-02 00:00:00+00"),
                                market_page(3, "e3", "2026-01-02 00:00:00+00")], "next_cursor": "p3"},
             "p3": {"markets": [market_page(4, "e4", "2026-03-04 00:00:00+00")], "next_cursor": None}}
    seen = []

    def handler(method, url, p):
        if url.endswith("/markets/keyset"):
            seen.append(p.get("after_cursor"))
            return pages[p.get("after_cursor")]
        if url.endswith("/events/keyset"):
            return {"events": []}
        if url.endswith("/batch-prices-history"):
            return {"history": {t: [{"t": body_t, "p": 0.5} for body_t in (p["start_ts"] + 5, p["start_ts"] + 65)]
                                for t in p["markets"]}}
        raise AssertionError(url)

    fc = FakeClient(handler)
    conn = dbmod.core(paths)
    backfill.ensure_hist_schema(conn)
    since = 1769904000
    st = backfill.enumerate_sport(conn, "nhl", since, fc, deadline=0)       # budget exhausted after one page
    assert st["pages"] == 1 and not st["done"] and st["cursor"] == "p2"
    st = backfill.enumerate_sport(conn, "nhl", since, fc, deadline=float("inf"))
    assert st["done"] and st["pages"] == 3 and st["staged"] == 3            # e3 is before --since
    assert seen == [None, "p2", "p3"]                                       # resumed, no page refetched
    st = backfill.enumerate_sport(conn, "nhl", since, fc, deadline=float("inf"))
    assert seen == [None, "p2", "p3"]                                       # done: no more calls
    # a changed query restarts the walk
    st = backfill.enumerate_sport(conn, "nhl", since + 86400, fc, deadline=0)
    assert st["pages"] == 1 and seen[-1] is None

    cfg = C.CollectorConfig(sports=("nhl",))
    dbmod.set_checkpoint(conn, "backfill.hist.enum.nhl", json.dumps({**json.loads(
        dbmod.get_checkpoint(conn, "backfill.hist.enum.nhl")), "done": True}))
    conn.commit()
    stats = backfill.process_events(conn, cfg, fc, NOW, deadline=float("inf"), workers=2)
    assert stats["events_stored"] == 3 and stats["bars"] == 3 * 2 * 2
    assert conn.execute("SELECT COUNT(*) FROM games WHERE source='history_backfill' AND status='ended'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM markets WHERE resolved_outcome_index=0").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM backfill_status WHERE kind='prices' AND status='done'").fetchone()[0] == 3
    again = backfill.process_events(conn, cfg, fc, NOW, deadline=float("inf"))
    assert again["events_stored"] == 0


def test_historical_soccer_skips_no_tokens_and_minor_leagues(paths):
    def sm(i, eid, league, subj, vol):
        return {"conditionId": f"0x{eid}{i}", "id": f"{eid}{i}", "question": f"Will {subj} win on 2026-03-01?",
                "groupItemTitle": subj, "sportsMarketType": "moneyline", "outcomes": '["Yes", "No"]',
                "clobTokenIds": json.dumps([f"{eid}{i}y", f"{eid}{i}n"]), "outcomePrices": '["0", "1"]', "closed": True,
                "umaResolutionStatus": "resolved", "volumeNum": vol, "gameStartTime": "2026-03-01 15:00:00+00",
                "events": [{"id": eid, "title": "Home FC vs. Away FC", "slug": f"{league}-hom-awa-2026-03-01",
                            "startTime": "2026-03-01T15:00:00Z"}]}
    big = [sm(1, "g1", "epl", "Home FC", 5000), sm(2, "g1", "epl", "Away FC", 5000)]
    minor = [sm(1, "g2", "col2", "Home FC", 100), sm(2, "g2", "col2", "Away FC", 100)]
    requested = []

    def handler(method, url, p):
        if url.endswith("/events/keyset"):
            return {"events": []}
        if url.endswith("/batch-prices-history"):
            requested.extend(p["markets"])
            return {"history": {t: [{"t": p["start_ts"] + 1, "p": 0.4}] for t in p["markets"]}}
        raise AssertionError(url)

    conn = dbmod.core(paths)
    backfill.ensure_hist_schema(conn)
    for m in big + minor:
        conn.execute("INSERT INTO collector_hist_markets(condition_id, event_id, sport, game_start, payload, updated_at) "
                     "VALUES(?,?,?,?,?,?)", (m["conditionId"], m["events"][0]["id"], "soccer", 1772377200,
                                             json.dumps(m), NOW))
    dbmod.set_checkpoint(conn, "backfill.hist.enum.soccer", json.dumps({"done": True}))
    conn.commit()
    stats = backfill.process_events(conn, C.CollectorConfig(sports=("soccer",)), FakeClient(handler), NOW,
                                    deadline=float("inf"))
    assert stats["events_stored"] == 1 and stats["events_filtered"] == 1
    assert sorted(requested) == ["g11y", "g12y"]
    sides = dict(conn.execute("SELECT token_id, side FROM tokens"))
    assert sides == {"g11y": "home", "g11n": "no", "g12y": "away", "g12n": "no"}


def test_recent_backfill_trades_validation_and_skip(paths):
    ev = live_nba_event(start=NOW - 5 * 3600)
    conn = dbmod.core(paths)
    discover.discover(conn, C.CollectorConfig(sports=("nba",), line_min_volume=10**12), FakeClient(gamma_handler(ev)), NOW)
    conn.execute("UPDATE games SET status='ended', ended_at=?", (NOW - 3 * 3600,))
    tok_away, tok_home = [r[0] for r in conn.execute("SELECT token_id FROM tokens ORDER BY outcome_index")]
    start = NOW - 5 * 3600
    live_ts = C.minute(start + 600)
    C.write_price_bars(conn, [(tok_home, live_ts, "poll_mid", 0.60, live_ts)])
    conn.commit()
    trades = json.loads((FX / "data_v2_trades.json").read_text())["data"]

    def handler(method, url, p):
        if url.endswith("/batch-prices-history"):
            return {"history": {tok_home: [{"t": live_ts + 10, "p": 0.50}], tok_away: [{"t": live_ts + 10, "p": 0.50}]}}
        if "/v2/trades" in url:
            return {"data": trades, "pagination": {"next_cursor": None}}
        if "/v2/oi" in url:
            assert isinstance(p["condition_id"], str)
            return {"data": [{"condition_id": c, "value": 12.5} for c in p["condition_id"].split(",")]}
        if url.endswith("/markets/keyset"):
            return {"markets": [dict(m, closed=True, umaResolutionStatus="resolved", outcomePrices='["0", "1"]')
                                for m in ev["markets"] if m["conditionId"] in p["condition_ids"]]}
        raise AssertionError(url)

    fc = FakeClient(handler)
    out = backfill.run_recent(conn, fc, NOW)
    assert out["games_done"] == 1 and out["bars"] == 2 and out["trades"] == len(trades) and out["resolved"] == 1
    kinds = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT kind, detail FROM quality_events")}
    assert kinds["live_history_mismatch"]["count"] == 1 and kinds["live_history_mismatch"]["max"] == pytest.approx(0.10)
    assert "history_gap" in kinds and "live_gap" in kinds
    assert conn.execute("SELECT open_interest FROM market_metrics WHERE open_interest IS NOT NULL").fetchone()[0] == 12.5
    calls = fc.calls
    out2 = backfill.run_recent(conn, fc, NOW + 60)                       # everything done: no work
    assert out2["games_candidates"] == 0 and fc.calls == calls
    assert backfill.store_trades(conn, conn.execute("SELECT condition_id FROM markets").fetchone()[0], fc) == len(trades)
    assert conn.execute("SELECT COUNT(*) FROM public_trades").fetchone()[0] == len(trades)   # INSERT OR IGNORE dedupe


def test_poll_pregame_pass_is_throttled(paths):
    ev = live_nba_event(start=NOW + 3 * 3600)
    ev.update(live=False, score=None, period=None)
    fc = FakeClient(gamma_handler(ev))
    conn = dbmod.core(paths)
    discover.discover(conn, C.CollectorConfig(sports=("nba",), line_min_volume=100_000), fc, NOW)
    conn.close()
    out = poll.run_once(paths, client=fc, ts=NOW)
    assert out["tokens"] == 0 and out["pregame_tokens"] == 2          # moneyline only, totals excluded
    assert out["pregame_books"] == 1 and out["pregame_bars"] == 1     # fake /books drops the last token
    posts = sum(1 for c in fc.log if c[0] == "POST")
    out = poll.run_once(paths, client=fc, ts=NOW + 60)
    assert "pregame_tokens" not in out and sum(1 for c in fc.log if c[0] == "POST") == posts
    out = poll.run_once(paths, client=fc, ts=NOW + 600)
    assert out["pregame_tokens"] == 2


def test_recent_backfill_open_window_is_partial(paths):
    ev = live_nba_event(start=NOW - 3 * 3600)
    conn = dbmod.core(paths)
    discover.discover(conn, C.CollectorConfig(sports=("nba",), line_min_volume=10**12), FakeClient(gamma_handler(ev)), NOW)
    conn.execute("UPDATE games SET status='ended', ended_at=?", (NOW - 600,))     # ended 10 min ago
    conn.commit()

    def handler(method, url, p):
        if url.endswith("/batch-prices-history"):
            return {"history": {t: [{"t": NOW - 700, "p": 0.9}] for t in p["markets"]}}
        if "/v2/trades" in url:
            return {"data": [], "pagination": {"next_cursor": None}}
        if "/v2/oi" in url:
            return {"data": []}
        if url.endswith("/markets/keyset"):
            return {"markets": []}
        if "/v2/resolutions" in url:
            return {"data": []}
        raise AssertionError(url)

    out = backfill.run_recent(conn, FakeClient(handler), NOW)
    assert out["games_done"] == 1
    st = dict(conn.execute("SELECT kind, status FROM backfill_status WHERE kind IN ('prices','trades')").fetchall())
    assert st == {"prices": "partial", "trades": "partial"}
    assert len(backfill.recent_games(conn, NOW + 60)) == 1                        # retried next run
    backfill.run_recent(conn, FakeClient(handler), NOW + 3600)
    st = dict(conn.execute("SELECT kind, status FROM backfill_status WHERE kind IN ('prices','trades')").fetchall())
    assert st == {"prices": "done", "trades": "empty"}
    assert backfill.recent_games(conn, NOW + 3700) == []
