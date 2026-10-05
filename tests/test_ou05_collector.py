"""polylab.ou05 discover / poll / backfill against a temp POLYLAB_ROOT with a fake HTTP client (no network)."""

from __future__ import annotations

import json

import pytest

from polylab import db as dbmod
from polylab.api import gamma
from polylab.ou05 import backfill, discover, poll, store
from polylab.settings import Paths

NOW = 1791158400  # 2026-10-05T00:00:00Z (minute aligned)
KICK = NOW + 3 * 86400


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


def gm(cid, line=0.5, outcomes=("Over", "Under"), closed=False, prices=("0.9", "0.1"), league="epl", kick=KICK,
       parent=900, smt="totals"):
    toks = [f"{cid}-{o.lower()}" for o in outcomes]
    return {"id": f"m{cid}", "conditionId": cid, "question": f"Home {cid} vs. Away {cid}: O/U {line}",
            "slug": f"{league}-x-y-total-0pt5", "sportsMarketType": smt, "line": line,
            "outcomes": json.dumps(list(outcomes)), "clobTokenIds": json.dumps(toks),
            "outcomePrices": json.dumps(list(prices)), "closed": closed,
            "umaResolutionStatus": "resolved" if closed else None, "closedTime": gamma.iso(kick + 7200) if closed else None,
            "createdAt": gamma.iso(kick - 13 * 86400), "gameStartTime": gamma.iso(kick).replace("T", " ").replace("Z", "+00"),
            "endDate": gamma.iso(kick), "volumeNum": 12000, "liquidityNum": 3000,
            "events": [{"id": 1000 + parent, "parentEventId": parent, "slug": f"{league}-x-y-more-markets",
                        "title": f"Home {cid} vs. Away {cid} - More Markets"}]}


def book(token, bid, ask, bsz=10, asz=20, last=""):
    return {"asset_id": token, "bids": [{"price": str(bid), "size": str(bsz)}] if bid is not None else [],
            "asks": [{"price": str(ask), "size": str(asz)}] if ask is not None else [], "last_trade_price": last}


def handler(open_markets, closed_markets=(), books=None, history=None, events=()):
    def h(method, url, p):
        if url.endswith("/markets/keyset"):
            if p.get("condition_ids"):
                return {"markets": [m for m in closed_markets if m["conditionId"] in p["condition_ids"]]}
            if p.get("closed") == "true":
                return {"markets": list(closed_markets)}
            return {"markets": list(open_markets)}
        if url.endswith("/events/keyset"):
            return {"events": [e for e in events if str(e["id"]) in p.get("id", [])]}
        if url.endswith("/books"):
            return [books[x["token_id"]] for x in p if x["token_id"] in (books or {})]
        if url.endswith("/batch-prices-history"):
            return {"history": {t: (history or {}).get(t, []) for t in p["markets"]}}
        if "/v2/resolutions" in url:
            return {"data": []}
        raise AssertionError(url)
    return h


def test_market_row_maps_sides_by_label_and_filters_lines():
    m = gm("a", outcomes=("Under", "Over"))           # reversed order: never trust index 0
    row = discover.market_row(m, ts=NOW, source="discover")
    assert row["over_token"] == "a-over" and row["under_token"] == "a-under"
    assert row["game_key"] == "900" and row["league"] == "epl" and row["home"] == "Home a"
    assert discover.is_ou05(m) and not discover.is_ou05(gm("b", line=1.5))
    assert not discover.is_ou05(gm("c", smt="soccer_team_totals"))
    closed = gm("d", outcomes=("Under", "Over"), closed=True, prices=("1", "0"))
    assert discover.market_row(closed, ts=NOW, source="x")["resolved_over"] == 0   # Under won (index 0)


def test_discover_registers_all_leagues_refreshes_kickoff_and_resolves(paths):
    reg = store.registry(paths)
    opens = [gm("a"), gm("b", league="kor2"), gm("c", line=2.5)]
    out = discover.discover(reg, FakeClient(handler(opens)), NOW)
    assert out["open_ou05"] == 2 and out["new"] == 2
    assert {r[0] for r in reg.execute("SELECT league FROM ou05_markets")} == {"epl", "kor2"}
    # postponement: kickoff moves; market b closes and resolves Over with a final score
    moved = gm("a", kick=KICK + 7 * 86400)
    closed_b = dict(gm("b", league="kor2", closed=True, prices=("1", "0")), volumeNum=45000)
    ev = {"id": "900", "title": "Home b vs. Away b", "score": "2-1", "ended": True, "closed": True,
          "teams": [{"name": "Home b", "ordering": "home"}, {"name": "Away b", "ordering": "away"}]}
    out = discover.discover(reg, FakeClient(handler([moved], [closed_b], events=[ev])), NOW + 3600)
    assert out["closing"]["closed"] == 1 and out["closing"]["resolved"] == 1
    a = reg.execute("SELECT game_start, closed FROM ou05_markets WHERE condition_id='a'").fetchone()
    assert a["game_start"] == KICK + 7 * 86400 and a["closed"] == 0
    b = reg.execute("SELECT closed, resolved_over, final_score, volume FROM ou05_markets WHERE condition_id='b'").fetchone()
    assert tuple(b) == (1, 1, "2-1", 45000)                     # final volume from the closed payload
    assert reg.execute("SELECT COUNT(*) FROM ou05_metrics").fetchone()[0] == 4


def _rows(paths):
    conn = store.shard(paths, dbmod.year_month(NOW))
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ou05_quotes ORDER BY ts, condition_id")]
    finally:
        conn.close()


def test_poll_change_or_heartbeat_dedupe_and_gap(paths):
    reg = store.registry(paths)
    discover.discover(reg, FakeClient(handler([gm("a"), gm("b")])), NOW)
    reg.close()
    books = {"a-over": book("a-over", 0.93, 0.99, last="0.95"), "a-under": book("a-under", 0.01, 0.07),
             "b-over": book("b-over", 0.5, None), "b-under": book("b-under", None, 0.5)}
    fc = FakeClient(handler([], books=books))
    o1 = poll.run_once(paths, client=fc, ts=NOW)
    assert o1["rows"] == 2 and o1["mirror_break"] == 0 and o1["one_sided"] == 1
    r = _rows(paths)[0]
    assert r["sum_ask"] == pytest.approx(1.06) and r["sum_bid"] == pytest.approx(0.94)
    assert r["over_mid"] == pytest.approx(0.96) and r["over_last"] == 0.95 and r["over_ask_sz"] == 20
    assert r["minutes_to_kickoff"] == pytest.approx(3 * 1440) and r["game_status"] == "pre" and r["source"] == "poll"
    # unchanged quotes: no rows for 9 minutes, then the 10-minute heartbeat
    for k in range(1, 10):
        assert poll.run_once(paths, client=fc, ts=NOW + 60 * k)["rows"] == 0
    o = poll.run_once(paths, client=fc, ts=NOW + 600)
    assert o["rows"] == 2 and o["heartbeat"] == 2
    # a size change is a quote change
    books["a-over"] = book("a-over", 0.93, 0.99, asz=21, last="0.95")
    assert poll.run_once(paths, client=fc, ts=NOW + 660)["rows"] == 1
    # a 5-minute hole in the runs is a recorded gap
    poll.run_once(paths, client=fc, ts=NOW + 960)
    reg = store.registry(paths)
    kinds = [r[0] for r in reg.execute("SELECT kind FROM quality_events")]
    assert "ou05_poll_gap" in kinds


def test_poll_flags_mirror_break_and_skips_stale(paths):
    reg = store.registry(paths)
    discover.discover(reg, FakeClient(handler([gm("a"), gm("old", kick=NOW - 2 * 86400)])), NOW)
    reg.close()
    books = {"a-over": book("a-over", 0.90, 0.99), "a-under": book("a-under", 0.02, 0.07),
             "old-over": book("old-over", 0.9, 0.99), "old-under": book("old-under", 0.01, 0.1)}
    o = poll.run_once(paths, client=FakeClient(handler([], books=books)), ts=NOW)
    assert o["markets"] == 1 and o["mirror_break"] == 1


def test_history_rows_bucket_sides_independently_never_carry():
    over = [(NOW + 5, 0.9), (NOW + 30, 0.91), (NOW + 60, 0.91), (NOW + 120, 0.92)] + \
           [(NOW + 180 + 60 * k, 0.92) for k in range(12)]
    under = [(NOW + 70, 0.09)]
    rows = backfill.history_quote_rows("c", over, under, KICK)
    cols = store.QUOTE_COLS
    d = [dict(zip(cols, r)) for r in rows]
    assert [(r["ts"] - NOW, r["over_mid"], r["under_mid"]) for r in d[:3]] == [(0, 0.91, None), (60, 0.91, 0.09),
                                                                               (120, 0.92, None)]
    assert all(r["sum_ask"] is None and r["over_bid"] is None and r["source"] == "history" for r in d)
    # unchanged 0.92 for 12 more minutes -> one heartbeat row 10 minutes after the last stored row
    assert [r["ts"] - NOW for r in d[3:]] == [720]


def test_backfill_enumerates_closed_and_poll_rows_win(paths):
    closed = gm("z", closed=True, prices=("0", "1"), kick=NOW - 86400)
    lo = NOW - 86400 - 13 * 86400
    hist = {"z-over": [(lo + 60 * k, 0.5) for k in range(3)] + [(NOW - 86400, 0.8)],
            "z-under": [(lo + 60 * k, 0.5) for k in range(3)] + [(NOW - 86400, 0.2)]}
    fc = FakeClient(handler([], [closed], history={k: [{"t": t, "p": p} for t, p in v] for k, v in hist.items()}))
    since = NOW - 30 * 86400
    out = backfill.run(paths, fc, NOW, 60, since)
    assert out["enumeration"]["staged"] == 1 and out["markets_done"] == 1 and out["rows"] == 2
    reg = store.registry(paths)
    z = reg.execute("SELECT hist_status, resolved_over, closed FROM ou05_markets").fetchone()
    assert tuple(z) == ("done", 0, 1)
    # same minute as an existing poll row: the poll row is kept
    row = {k: None for k in store.QUOTE_COLS}
    row.update(condition_id="z", ts=NOW - 86400, sum_ask=1.02, source="poll")
    store.write_quotes(paths, [tuple(row[k] for k in store.QUOTE_COLS)])
    n = store.write_quotes(paths, backfill.history_quote_rows("z", [(NOW - 86400, 0.8)], [], KICK))
    assert n == 0
