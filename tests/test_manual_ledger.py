"""Track 2 watch-only manual ledger: ingest, linking, settlement, idempotency (fixtures, no network)."""

import json
import sqlite3

import pytest

from polylab import settings
from polylab.db import CORE_SCHEMA, connect
from polylab.manual import ledger, sync

ADDR = "0x" + "ab" * 20
T0 = 1_790_000_000
KICK = T0 + 3600


def cid(c: str) -> str:
    return "0x" + c * 64


def tok(c: str, i: int) -> str:
    return f"{ord(c)}{i:03d}" * 4


def trade(c, i, side, size, price, usdc, ts, outcome="Over", title=None):
    return {"proxy_wallet": ADDR, "name": "someone", "type": "TRADE", "side": side, "condition_id": cid(c),
            "token_id": tok(c, i), "outcome_index": i, "outcome": outcome, "size": size, "price": price,
            "usdc_size": usdc, "timestamp": ts, "transaction_hash": f"0xtx{c}{ts}", "title": title or f"M{c}",
            "slug": f"slug-{c}", "event_slug": f"ev-{c}"}


def redeem(c, i, size, usdc, ts):
    return {"proxy_wallet": ADDR, "type": "REDEEM", "side": "", "condition_id": cid(c), "token_id": tok(c, i),
            "outcome_index": i, "outcome": "Over", "size": size, "usdc_size": usdc, "price": 0.0, "timestamp": ts,
            "transaction_hash": f"0xrd{c}{ts}", "title": f"M{c}"}


ACTIVITY = [
    # A: Greece-Netherlands Over 0.5 (core.db, resolved Over), redeemed at 1.0
    trade("a", 0, "BUY", 800.0, 0.95, 761.9, T0 + 100, title="Greece vs. Netherlands: O/U 0.5"),
    {**redeem("a", 0, 800.0, 800.0, T0 + 9000), "token_id": ""},      # v2 REDEEM rows can lack token_id
    # B: Korea-Uruguay Over 0.5 (not in core -> Gamma, resolved Over, not redeemed yet)
    trade("b", 0, "BUY", 543.915, 0.9192612816, 502.01846, T0 + 200, title="Korea Republic vs. Uruguay: O/U 0.5"),
    # C: Over 0.5 loss, no REDEEM, resolved through /v2/resolutions payouts
    trade("c", 0, "BUY", 100.0, 0.9, 90.45, T0 + 300, title="Spain vs. Italy: O/U 0.5"),
    # D: maker round trip on a non-sports market (fee 0 both ways)
    trade("d", 0, "BUY", 10.0, 0.5, 5.0, T0 + 400, outcome="Yes"),
    trade("d", 0, "SELL", 10.0, 0.6, 6.0, T0 + 500, outcome="Yes"),
    # E: two identical same-second sell fills must both survive
    trade("e", 0, "BUY", 4.0, 0.9, 3.6, T0 + 600, outcome="Yes"),
    trade("e", 0, "SELL", 2.0, 0.93, 1.86, T0 + 700, outcome="Yes"),
    trade("e", 0, "SELL", 2.0, 0.93, 1.86, T0 + 700, outcome="Yes"),
    # F: SPLIT on the condition -> quarantined
    trade("f", 0, "BUY", 5.0, 0.5, 2.5, T0 + 800, outcome="Yes"),
    {"type": "SPLIT", "condition_id": cid("f"), "size": 3.0, "usdc_size": 3.0, "timestamp": T0 + 900,
     "transaction_hash": "0xsplit"},
    # G: losing REDEEM with 0 cash (Gamma link, soccer)
    trade("g", 0, "BUY", 50.0, 0.8, 50 * 0.8 + 50 * 0.05 * 0.8 * 0.2, T0 + 1000, title="Chile vs. Peru: O/U 0.5"),
    redeem("g", 0, 50.0, 0.0, T0 + 8000),
    # H: open Over 0.5, unresolved
    trade("h", 0, "BUY", 100.0, 0.92, 92.0 + 100 * 0.05 * 0.92 * 0.08, T0 + 1100, title="Wales vs. Iceland: O/U 0.5"),
    # account credits / flows
    {"type": "MAKER_REBATE", "usdc_size": 0.5, "timestamp": T0 + 1200, "transaction_hash": "0xreb"},
    {"type": "DEPOSIT", "usdc_size": 1000.0, "timestamp": T0 + 50, "transaction_hash": "0xdep"},
]


def gamma_market(c, title, smt="totals", line=0.5, outcomes=("Over", "Under"), prices=None, closed=False,
                 league="fif", start=KICK):
    return {"conditionId": cid(c), "question": f"{title}: O/U {line}" if smt == "totals" else title,
            "sportsMarketType": smt, "line": line, "gameStartTime": f"2026-09-21 {((start // 3600) % 24):02d}:00:00+00",
            "closed": closed, "umaResolutionStatus": "resolved" if prices else None,
            "outcomes": json.dumps(list(outcomes)), "outcomePrices": json.dumps(prices or ["0.5", "0.5"]),
            "clobTokenIds": json.dumps([tok(c, 0), tok(c, 1)]), "closedTime": "2026-09-21 12:00:00+00" if closed else None,
            "slug": f"{league}-x-y-total-0pt5",
            "events": [{"id": f"ev{c}", "title": f"{title} - More Markets", "slug": f"{league}-x-y-more-markets",
                        "startTime": "2026-09-21T11:00:00Z"}]}


GAMMA = {
    cid("b"): gamma_market("b", "Korea Republic vs. Uruguay", prices=["1", "0"], closed=True),
    cid("c"): gamma_market("c", "Spain vs. Italy", league="unl"),
    cid("d"): {**gamma_market("d", "Will BTC be above 80k?", smt=None, line=None, outcomes=("Yes", "No")),
               "gameStartTime": None, "events": [{"id": "evd", "title": "BTC above?", "slug": "btc-above"}]},
    cid("e"): {**gamma_market("e", "Will ETH be above 3k?", smt=None, line=None, outcomes=("Yes", "No")),
               "gameStartTime": None, "events": [{"id": "eve", "title": "ETH above?", "slug": "eth-above"}]},
    cid("g"): gamma_market("g", "Chile vs. Peru", prices=["0", "1"], closed=True),
    cid("h"): gamma_market("h", "Wales vs. Iceland"),
}
RESOLUTIONS = {cid("c"): {"condition_id": cid("c"), "status": "resolved", "payouts": [0, 1000000],
                          "resolved_at": "2026-09-21T16:00:00Z"},
               cid("h"): {"condition_id": cid("h"), "status": "proposed"}}
POSITIONS = [{"proxy_wallet": ADDR, "token_id": tok("h", 0), "condition_id": cid("h"), "status": "OPEN",
              "current_size": 100.0, "current_price": 0.95, "current_value": 95.0}]


class FakeClient:
    """Routes Data API / Gamma GETs to fixtures; activity is served newest-first in pages of 4."""

    def __init__(self, activity=ACTIVITY):
        self.activity = activity
        self.calls = []

    def get(self, url, *, bucket, params=None, allow_404=False):
        params = dict(params or {})
        self.calls.append((url, params))
        if url.endswith("/v2/activity"):
            assert params["user"] == ADDR and params["exclude_deposits_withdrawals"] == "false"
            rows = sorted([r for r in self.activity if r["timestamp"] >= params["start"]],
                          key=lambda r: -r["timestamp"])
            off = int(params.get("cursor") or 0)
            page = rows[off:off + 4]
            nxt = str(off + 4) if off + 4 < len(rows) else None
            return {"data": page, "pagination": {"next_cursor": nxt}}
        if url.endswith("/v2/positions"):
            return {"data": POSITIONS, "pagination": {}}
        if url.endswith("/v2/resolutions"):
            ids = params["condition_id"].split(",")
            return {"data": [RESOLUTIONS[i] for i in ids if i in RESOLUTIONS]}
        if url.endswith("/markets/keyset"):
            ids = params["condition_ids"]
            closed = params["closed"] == "true"
            return {"markets": [m for c, m in GAMMA.items() if c in ids and bool(m["closed"]) == closed]}
        raise AssertionError(url)


def make_core(paths):
    conn = connect(paths.core_db, CORE_SCHEMA)
    conn.execute("INSERT INTO games(game_key, sport, league, title, home_team, away_team, start_time, status, "
                 "first_seen, updated_at, source) VALUES('g1','soccer','unl','Greece vs. Netherlands','Greece',"
                 "'Netherlands',?, 'ended', 0, 0, 'discover')", (KICK,))
    conn.execute("INSERT INTO markets(condition_id, game_key, market_type, line, question, closed, "
                 "resolved_outcome_index, resolved_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (cid("a"), "g1", "total", 0.5, "Greece vs. Netherlands: O/U 0.5", 1, 0, KICK + 7200, 0))
    for i, (label, side) in enumerate((("Over", "over"), ("Under", "under"))):
        conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (tok("a", i), cid("a"), i, label, side))
    conn.commit()
    conn.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYLAB_ROOT", str(tmp_path / "rt"))
    paths = settings.paths()
    make_core(paths)
    acct = settings.WatchAccount(alias="owner", address=ADDR, label="연구자A", bankroll_usdc=1000.0)
    return paths, acct


def positions(paths, alias="owner"):
    conn = sqlite3.connect(ledger.db_path(paths, alias))
    conn.row_factory = sqlite3.Row
    rows = {r["condition_id"][2]: dict(r) for r in conn.execute(
        "SELECT p.*, m.result, m.track2, m.implied_p00, m.unrealized_pnl, m.game_title, m.market_type, m.line, "
        "m.side, m.quarantine_reason, m.link_source FROM positions p JOIN position_meta m USING(position_id)")}
    conn.close()
    return rows


def test_sync_reconstructs_positions(world):
    paths, acct = world
    out = sync.sync_account(acct, paths, client=FakeClient(), now=T0 + 10_000)
    assert out["fetched"] == len(ACTIVITY) and out["new"] == len(ACTIVITY)
    p = {k: v for k, v in positions(paths).items()}
    a, b, c, d, e, f, g, h = (p[x] for x in "abcdefgh")
    assert a["result"] == "redeemed" and a["link_source"] == "core" and a["track2"] == 1
    assert a["realized_pnl"] == pytest.approx(800 - 761.9) and a["entry_fee_usdc"] == pytest.approx(1.9)
    assert a["implied_p00"] == pytest.approx(0.05) and a["mode"] == "manual" and a["stake_usdc"] == 761.9
    assert b["result"] == "resolved_win" and b["link_source"] == "gamma" and b["track2"] == 1
    assert b["realized_pnl"] == pytest.approx(543.915 - 502.01846) and b["sport"] == "soccer"
    assert b["game_title"] == "Korea Republic vs. Uruguay" and b["market_type"] == "total" and b["side"] == "over"
    assert c["result"] == "resolved_loss" and c["realized_pnl"] == pytest.approx(-90.45)
    assert d["result"] == "closed_sell" and d["realized_pnl"] == pytest.approx(1.0) and d["track2"] == 0
    assert d["exit_fee_usdc"] == 0.0 and d["entry_fee_usdc"] == 0.0
    assert e["result"] == "closed_sell" and e["realized_pnl"] == pytest.approx(0.12)   # both identical sells kept
    assert f["result"] == "quarantined" and f["realized_pnl"] is None and f["quarantine_reason"] == "split"
    assert g["result"] == "resolved_loss" and g["realized_pnl"] == pytest.approx(-40.4) and g["redeemed"] == 1
    assert h["result"] == "open" and h["realized_pnl"] is None
    assert h["unrealized_pnl"] == pytest.approx(100 * 0.95 - 92.368)


def test_sync_is_idempotent_and_incremental(world):
    paths, acct = world
    client = FakeClient()
    sync.sync_account(acct, paths, client=client, now=T0 + 10_000)
    before = positions(paths)
    again = sync.sync_account(acct, paths, client=client, now=T0 + 10_100)
    assert again["new"] == 0
    starts = [c[1]["start"] for c in client.calls if c[0].endswith("/v2/activity")]
    assert starts[0] == 1 and starts[-1] == T0 + 9000 - sync.REFETCH_LOOKBACK_S   # re-read the last day
    assert positions(paths) == before
    conn = sqlite3.connect(ledger.db_path(paths, "owner"))
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == len(ACTIVITY)
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 11
    assert conn.execute("SELECT COUNT(*) FROM fills WHERE status='CONFIRMED'").fetchone()[0] == 11
    conn.close()


def test_new_same_second_fill_after_sync_is_added(world):
    paths, acct = world
    first = [r for r in ACTIVITY if r is not ACTIVITY[8]]                 # second identical sell arrives later
    sync.sync_account(acct, paths, client=FakeClient(first), now=T0 + 10_000)
    assert positions(paths)["e"]["result"] == "open"
    # a late-indexed row inside the refetch lookback is picked up without --full; ordinals keep both sells
    out = sync.sync_account(acct, paths, client=FakeClient(), now=T0 + 10_100)
    assert out["new"] == 1 and positions(paths)["e"]["result"] == "closed_sell"


def test_address_never_stored(world):
    paths, acct = world
    sync.sync_account(acct, paths, client=FakeClient(), now=T0 + 10_000)
    blob = ledger.db_path(paths, "owner").read_bytes().lower()
    assert ADDR[2:].encode() not in blob
    assert b"someone" not in blob


def test_since_window_quarantines_orphans(world):
    paths, acct = world
    acct = settings.WatchAccount(alias="owner", address=ADDR, since=T0 + 1050)
    sync.sync_account(acct, paths, client=FakeClient(), now=T0 + 10_000)
    p = positions(paths)
    assert p["g"]["result"] == "quarantined"                     # REDEEM without its BUY in window
    assert p["g"]["quarantine_reason"] == "redeem_without_buy_in_window" and p["g"]["realized_pnl"] is None
    assert p["h"]["result"] == "open" and "b" not in p


def test_fee_unknown_when_implausible():
    assert ledger._fill_fee("BUY", 10, 0.5, 4.0) is None            # paid less than size x price
    assert ledger._fill_fee("SELL", 10, 0.5, 5.0) == 0.0
    assert ledger._fill_fee("BUY", 10, 0.5, None) is None


def test_activity_uids_number_identical_rows():
    r = ACTIVITY[7]
    a, b = ledger.activity_uids([r, dict(r)])
    assert a != b and ledger.activity_uids([r])[0] == a


def test_watch_accounts_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SECRETS_DIR", tmp_path)
    assert settings.watch_accounts() == []
    (tmp_path / "watch.env").write_text(
        f"WATCH_WOLF__ADDRESS={ADDR}\nWATCH_WOLF__LABEL=트랙2-A\nWATCH_WOLF__SINCE=2026-09-28\n"
        "WATCH_WOLF__BANKROLL_USDC=1000\nWATCH_BAD__ADDRESS=0x123\nWATCH_PREDICTIONS__ADDRESS=" + ADDR + "\n")
    accts = settings.watch_accounts()
    assert [a.alias for a in accts] == ["wolf"]
    w = accts[0]
    assert w.label == "트랙2-A" and w.bankroll_usdc == 1000.0 and w.since == 1790553600 and w.display == "트랙2-A"
    assert ADDR not in repr(w)
    assert settings.watch_addresses() == [ADDR]
