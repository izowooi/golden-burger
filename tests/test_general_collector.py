"""polylab.general discover / poll / backfill against a temp POLYLAB_ROOT with a fake HTTP client (no network)."""

from __future__ import annotations

import json

import pytest

from polylab import db as dbmod
from polylab.api import gamma
from polylab.general import backfill, discover, poll, store
from polylab.settings import Paths

NOW = 1791158400  # 2026-10-05T00:00:00Z
H, DAY = 3600, 86400


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


def gm(cid, *, end, tags=("politics",), closed=False, prices=("0.92", "0.08"), volume=20000, game_start=None,
       start=None, fee_type="politics_fees"):
    m = {"id": f"m{cid}", "conditionId": cid, "question": f"Will {cid}?", "slug": cid,
         "outcomes": json.dumps(["Yes", "No"]), "clobTokenIds": json.dumps([f"{cid}-y", f"{cid}-n"]),
         "outcomePrices": json.dumps(list(prices)), "closed": closed, "negRisk": True,
         "umaResolutionStatus": "resolved" if closed else None, "closedTime": gamma.iso(end + 600) if closed else None,
         "createdAt": gamma.iso(start or end - 10 * DAY), "startDate": gamma.iso(start or end - 10 * DAY),
         "endDate": gamma.iso(end), "volumeNum": volume, "liquidityNum": 5000, "feeType": fee_type,
         "feesEnabled": True, "feeSchedule": {"rate": 0.04, "exponent": 1, "takerOnly": True},
         "tags": [{"slug": t} for t in tags], "events": [{"id": 77, "slug": "ev"}]}
    if game_start:
        m["gameStartTime"] = gamma.iso(game_start).replace("T", " ").replace("Z", "+00")
    return m


def handler(open_markets=(), closed_markets=(), books=None, history=None):
    def h(method, url, p):
        if url.endswith("/markets/keyset"):
            if p.get("condition_ids"):
                return {"markets": [m for m in closed_markets if m["conditionId"] in p["condition_ids"]]}
            if p.get("closed") == "true":
                lo, hi = gamma.parse_time(p["end_date_min"]), gamma.parse_time(p["end_date_max"])
                return {"markets": [m for m in closed_markets if lo <= gamma.parse_time(m["endDate"]) < hi]}
            return {"markets": list(open_markets)}
        if url.endswith("/books"):
            return [books[x["token_id"]] for x in p if x["token_id"] in (books or {})]
        if url.endswith("/batch-prices-history"):
            return {"history": {t: (history or {}).get(t, []) for t in p["markets"]}}
        if "/v2/resolutions" in url:
            return {"data": []}
        raise AssertionError(url)
    return h


def test_category_and_end_ref():
    assert store.category(["sports", "soccer", "games"]) == "sports"
    assert store.category(["esports", "counter-strike-2", "games"]) == "esports"
    assert store.category(["brazil", "politics", "elections"]) == "politics"
    assert store.category(["world", "iran", "politics"]) == "world"
    assert store.category(["bitcoin", "weekly"]) == "crypto"
    assert store.category([], "finance_prices_fees") == "economy"
    assert store.category([]) == "other"
    assert store.end_ref(1000, 99999) == 1000 + store.NOMINAL_GAME_S and store.end_ref(None, 5) == 5


def test_discover_horizon_core_flag_and_metrics(paths):
    core = dbmod.core(paths)
    core.execute("INSERT INTO markets(condition_id, market_type, updated_at) VALUES('sp', 'moneyline', 0)")
    core.commit()
    core.close()
    ms = [gm("a", end=NOW + 2 * DAY),
          gm("far", end=NOW + 9 * DAY),                                        # beyond 4 days
          gm("sp", end=NOW + 8 * DAY, game_start=NOW + DAY, tags=("sports", "soccer"), fee_type="sports_fees_v3")]
    reg = store.registry(paths)
    out = discover.discover(reg, paths, FakeClient(handler(ms)), NOW)
    rows = {r["condition_id"]: r for r in reg.execute("SELECT * FROM gen_markets")}
    assert set(rows) == {"a", "sp"} and out["by_category"] == {"politics": 1, "sports": 1}
    assert rows["sp"]["in_core"] == 1 and rows["sp"]["end_ref"] == NOW + DAY + store.NOMINAL_GAME_S
    assert rows["a"]["yes_token"] == "a-y" and json.loads(rows["a"]["fee_json"])["feeSchedule"]["rate"] == 0.04
    assert reg.execute("SELECT COUNT(*) FROM gen_metrics").fetchone()[0] == 2
    # closing: 'a' leaves the listing and is resolved YES
    closed = [gm("a", end=NOW + 2 * DAY, closed=True, prices=("1", "0"))]
    out = discover.discover(reg, paths, FakeClient(handler([ms[2]], closed)), NOW + H)
    a = reg.execute("SELECT * FROM gen_markets WHERE condition_id='a'").fetchone()
    assert a["closed"] == 1 and a["resolved_index"] == 0 and out["closing"]["resolved"] == 1
    reg.close()


def test_poll_change_heartbeat_skip_core_and_every(paths):
    reg = store.registry(paths)
    discover.discover(reg, paths, FakeClient(handler([gm("a", end=NOW + 2 * DAY)])), NOW)
    reg.close()
    books = {"a-y": {"asset_id": "a-y", "bids": [{"price": "0.91", "size": "100"}],
                     "asks": [{"price": "0.93", "size": "50"}], "last_trade_price": "0.92"}}
    c = FakeClient(handler(books=books))
    assert poll.run_once(paths, client=c, ts=NOW)["rows"] == 1
    assert poll.run_once(paths, client=c, ts=NOW + 60)["unchanged"] == 1
    assert poll.run_once(paths, client=c, ts=NOW + 600)["heartbeat"] == 1
    conn = store.shard(paths, dbmod.year_month(NOW), readonly=True)
    r = conn.execute("SELECT * FROM gen_quotes ORDER BY ts").fetchone()
    assert (r["bid"], r["ask"], r["p"], r["src"]) == (0.91, 0.93, 0.92, store.SRC_POLL)
    conn.close()
    assert poll.run_once(paths, client=c, ts=NOW + 60, every=5)["skipped"]


def test_backfill_enumerates_days_and_fetches_window(paths):
    end = NOW - 3 * DAY
    closed = [gm("h", end=end, closed=True, prices=("0", "1")),
              gm("young", end=end, closed=True, start=end - DAY),                 # listed 1 day before end
              gm("thin", end=end, closed=True, volume=500)]
    hist = {"h-y": [(end - 5 * DAY, 0.5), (end - 3 * DAY, 0.9), (end - 3 * DAY + 300, 0.9), (end - DAY, 0.1),
                    (end + 5 * DAY, 0.0)]}

    def h(method, url, p):
        if url.endswith("/markets/keyset") and p.get("closed") == "true" and not p.get("condition_ids"):
            lo, hi = gamma.parse_time(p["end_date_min"]), gamma.parse_time(p["end_date_max"])
            smax = gamma.parse_time(p["start_date_max"])
            return {"markets": [m for m in closed if lo <= gamma.parse_time(m["endDate"]) < hi
                                and gamma.parse_time(m["startDate"]) <= smax
                                and m["volumeNum"] >= float(p["volume_num_min"])]}
        if url.endswith("/batch-prices-history"):
            return {"history": {t: [{"t": a, "p": b} for a, b in hist.get(t, [])] for t in p["markets"]}}
        raise AssertionError(url)
    client = FakeClient(h)
    out = backfill.run(paths, client, NOW, 60, end - 2 * DAY, min_volume=10000)
    assert out["enumeration"]["done"] and out["markets_done"] == 1
    reg = store.registry(paths)
    m = reg.execute("SELECT * FROM gen_markets WHERE condition_id='h'").fetchone()
    assert m["hist_status"] == "done" and m["resolved_index"] == 1 and m["source"] == "history_enum"
    assert reg.execute("SELECT COUNT(*) FROM gen_markets").fetchone()[0] == 1
    reg.close()
    pts = []
    for sp in store.shard_paths(paths):
        conn = dbmod.connect(sp, readonly=True)
        pts += [(r["ts"], r["p"]) for r in conn.execute("SELECT ts, p FROM gen_quotes ORDER BY ts")]
        conn.close()
    pts.sort()
    # window [end-4.5d, close+1h]: the -5d point is outside, the unchanged 5-minute repeat is dropped, +5d outside
    assert pts == [(end - 3 * DAY, 0.9), (end - DAY, 0.1)]
    # resumable: a second run has nothing left
    out2 = backfill.run(paths, client, NOW, 60, end - 2 * DAY, min_volume=10000)
    assert out2["markets_done"] == 0
