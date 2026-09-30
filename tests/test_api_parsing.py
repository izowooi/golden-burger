"""API layer: parsing, pagination, windowing, retries — no network (fixtures + fake client)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polylab.api import clob_public, data_api, gamma, http, ws_market, ws_sports

FX = Path(__file__).parent / "fixtures" / "polymarket_api"


def load(name: str):
    return json.loads((FX / name).read_text())


class FakeClient:
    """Routes get/post to a handler(method, url, params|body) -> json."""

    def __init__(self, handler):
        self.handler = handler
        self.log: list[tuple] = []
        self.calls = 0
        self.bytes_in = 0

    def get(self, url, *, bucket, params=None, allow_404=False):
        self.calls += 1
        self.log.append(("GET", url, params))
        return self.handler("GET", url, params)

    def post(self, url, *, bucket, json_body, allow_404=False):
        self.calls += 1
        self.log.append(("POST", url, json_body))
        return self.handler("POST", url, json_body)


def test_parse_time_variants():
    assert gamma.parse_time("2026-02-07T20:00:00Z") == 1770494400
    assert gamma.parse_time("2026-02-07 20:00:00+00") == 1770494400
    assert gamma.parse_time("2026-02-01T15:03:04.014731Z") == 1769958184
    assert gamma.parse_time("1770511027") == 1770511027
    assert gamma.parse_time(1770511027000) == 1770511027
    assert gamma.parse_time("") is None and gamma.parse_time(None) is None
    assert gamma.iso(1770494400) == "2026-02-07T20:00:00Z"


def test_json_list_and_fixture_market():
    m = load("gamma_markets_keyset_moneyline_nba.json")["markets"][0]
    assert gamma.json_list(m["outcomes"]) == ["Wizards", "Nets"]
    assert len(gamma.json_list(m["clobTokenIds"])) == 2
    assert gamma.json_list("not json") == [] and gamma.json_list(None) == []


def test_markets_keyset_follows_cursor():
    pages = {None: {"markets": [{"id": 1}], "next_cursor": "c1"}, "c1": {"markets": [{"id": 2}], "next_cursor": None}}
    fc = FakeClient(lambda m, u, p: pages[p.get("after_cursor")])
    rows = gamma.markets_keyset({"tag_id": 745}, fc)
    assert [r["id"] for r in rows] == [1, 2]
    assert fc.log[0][2]["limit"] == 100 and fc.log[1][2]["after_cursor"] == "c1"


def test_clob_windows_respect_15_days():
    w = clob_public.windows(0, 40 * 86400)
    assert all(e - s < 15 * 86400 for s, e in w)
    assert w[0][0] == 0 and w[-1][1] == 40 * 86400 and len(w) == 3


def test_batch_prices_history_chunks_and_parses():
    fx = load("clob_batch_prices_history.json")
    tokens = [f"t{i}" for i in range(25)] + fx["request"]["markets"]

    def handler(method, url, body):
        assert method == "POST" and url.endswith("/batch-prices-history")
        assert len(body["markets"]) <= 20 and body["end_ts"] - body["start_ts"] < 15 * 86400
        return {"history": {k: v for k, v in fx["response_trimmed"]["history"].items() if k in body["markets"]}}

    fc = FakeClient(handler)
    out = clob_public.batch_prices_history(tokens, fx["request"]["start_ts"], fx["request"]["end_ts"], client=fc)
    assert fc.calls == 2
    tok = fx["request"]["markets"][0]
    assert out[tok][0] == (1772665225, 0.465)
    assert out["t0"] == []


def test_books_maps_by_asset():
    book = load("ws_market_capture.json")["samples"][0]
    fc = FakeClient(lambda m, u, b: [dict(book, asset_id=x["token_id"]) for x in b if x["token_id"] != "gone"])
    out = clob_public.books(["a", "b", "gone"], fc)
    assert set(out) == {"a", "b"}


def test_trades_cursor_pagination():
    fx = load("data_v2_trades.json")
    pages = {None: {"data": fx["data"][:2], "pagination": {"next_cursor": "n2"}},
             "n2": {"data": fx["data"][2:], "pagination": {"next_cursor": None}}}
    fc = FakeClient(lambda m, u, p: pages[p.get("cursor")])
    got = [t for page in data_api.trades("0xabc", client=fc) for t in page]
    assert len(got) == len(fx["data"])
    assert fc.log[0][2]["taker_only"] == "true" and fc.log[0][1].endswith("/v2/trades")


def test_resolution_winner():
    recent = load("data_v2_resolutions_recent.json")["data"][0]
    old = load("data_v2_resolutions.json")["data"][0]
    assert data_api.winner_from_resolution(recent) == 1
    assert data_api.winner_from_resolution(old) is None          # Feb-2026 rows have no payouts
    assert data_api.winner_from_resolution({"status": "resolved", "payouts": [500000, 500000]}) is None


def test_oi_and_live_volume_parse():
    oi = load("data_v2_oi.json")
    lv = load("data_v2_live_volume.json")
    fc = FakeClient(lambda m, u, p: oi if u.endswith("/v2/oi") else lv)
    assert list(data_api.open_interest(["x"], fc).values()) == [17.505855]
    vols = data_api.live_volume("123", fc)
    assert vols["_total"] == pytest.approx(42265185.946402) and len(vols) == 4


def test_ws_market_frames():
    cap = load("ws_market_capture.json")
    evs = [e for s in cap["samples"] for e in ws_market.parse_frame(json.dumps(s))]
    types = {e.get("event_type") for e in evs}
    assert {"book", "price_change", "best_bid_ask", "last_trade_price"} <= types
    pc = next(e for e in evs if e.get("event_type") == "price_change")
    assert len(ws_market.event_assets(pc)) == 2
    assert ws_market.parse_frame("PONG") == []
    assert len(ws_market.parse_frame(json.dumps([cap["samples"][0], cap["samples"][1]]))) == 2
    assert json.loads(ws_market.subscribe_frame({"b", "a"})) == {
        "type": "market", "assets_ids": ["a", "b"], "custom_feature_enabled": True}
    assert json.loads(ws_market.update_frame("unsubscribe", ["a"]))["operation"] == "unsubscribe"


def test_ws_sports_frames():
    cap = load("ws_sports_capture.json")
    fr = ws_sports.parse_frame(json.dumps(cap["samples"][0]))[0]
    assert ws_sports.game_id_of(fr) == "1712948"
    assert ws_sports.game_id_of({"metadataGameId": 55}) == "55"
    assert ws_sports.parse_frame("ping") == []


def test_http_client_retries_then_succeeds(monkeypatch):
    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body, self.headers, self.url = code, body, {}, "u"
            self.content = json.dumps(body).encode()
            self.text = self.content.decode()

        def json(self):
            return self._body

    seq = [Resp(503, {}), Resp(429, {}), Resp(200, {"ok": 1})]
    c = http.Client()
    monkeypatch.setattr(c.session, "request", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(c, "_sleep", lambda attempt, ra: None)
    assert c.get("https://x", bucket="gamma") == {"ok": 1}
    assert c.calls == 3


def test_http_client_404_allowed_and_error(monkeypatch):
    class Resp:
        status_code, headers, url, content, text = 404, {}, "u", b"{}", "{}"

        def json(self):
            return {}

    c = http.Client()
    monkeypatch.setattr(c.session, "request", lambda *a, **k: Resp())
    assert c.get("https://x", bucket="clob_book", allow_404=True) is None
    with pytest.raises(http.ApiError):
        c.get("https://x", bucket="clob_book")


def test_proxy_env_honoured(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "socks5h://127.0.0.1:11080")
    c = http.Client()
    assert c.session.proxies["https"] == "socks5h://127.0.0.1:11080"
