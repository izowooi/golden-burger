"""LTS (late threshold stability, 2026-10-10 `hypothesis:lts`): strategy entry/cancel, maker 'below' pricing, the
bar trade-through maker backtest model, the evaluator, and the account-collision guard."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polylab import registry
from polylab.analysis import backtest as bt
from polylab.analysis import lts as L
from polylab.autopilot.validator import Context, Facts, Rules, promotion_key, validate
from polylab.execution import maker
from polylab.execution.fees import FeeSchedule
from polylab.execution.ledger import StrategyLedger
from polylab.marketview import Book
from polylab.risk import promotion
from polylab.strategies import build
from polylab.strategies.base import EntryIntent, Ledger, PositionView
from polylab.strategies.lts import Lts
from test_per_sport import write
from test_promotion import _trades
from test_reports_build import NOW
from test_strategy_fixtures import T0, Env, level_book

DAY = 86400


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path / "root")


def strat(sport="nfl", style="maker", **params):
    base = {"order_style": style, "min_game_volume_usd": 20000, "min_wall_minute": 100, "trigger_price": 0.80,
            "max_wait_minutes": 30, "hours_max": 6.0}
    base.update(params)
    v = SimpleNamespace(id="lts-t", family="lts", sports=[sport], params=base, limits={}, stake_usdc=5.0,
                        mode="paper", account=None)
    return build(v)


def at(env, minute, home, key="g"):
    ts = T0 + minute * 60
    env.bar(f"{key}-h", ts, home)
    env.bar(f"{key}-a", ts, round(1 - home, 4))
    level_book(env, f"{key}-h", ts, round(home - 0.005, 4), round(home + 0.005, 4))
    level_book(env, f"{key}-a", ts, round(1 - home - 0.005, 4), round(1 - home + 0.005, 4))
    return ts


def test_family_registered_with_maker_bar_replay():
    s = strat()
    assert isinstance(s, Lts) and s.family == "lts" and s.backtest_fill_model == "maker_bars"
    assert s.p("nfl")["band_width"] == 0.03 and s.p("nfl")["floor_c"] == 0.05


def test_entry_after_T_inside_band_and_maker_only(env):
    env.us_game("g", "nfl", T0)
    s = strat()
    now = at(env, 90, 0.81)                                    # before T
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    now = at(env, 101, 0.84)                                   # above the band [0.80, 0.83]
    assert s.entry_signals(env.view(now), now, Ledger()) == []
    now = at(env, 102, 0.82)
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.token_id == "g-h" and it.features["maker_price_rule"] == "below"
    assert it.exit_rules["hold_to_resolution"] and it.exit_rules["max_wait_minutes"] == 30
    assert (it.min_price, it.max_price) == (0.01, 0.999)       # wide: a rising price never cancels via re-quote
    assert not s.confirm_entry(it, {}, 5.0).ok                 # the taker path is refused
    taker = strat(style="taker")
    assert taker.entry_signals(env.view(now), now, Ledger()) == []
    assert ("nfl", "maker_only_strategy") in taker.skips


def test_one_attempt_per_game_counts_unfilled(env):
    env.us_game("g", "nfl", T0)
    now = at(env, 102, 0.81)
    unfilled = Ledger([PositionView("p", "unfilled", "g-h", "c-g", "g", "nfl", now - 600, None, 0, 0)])
    assert strat().entry_signals(env.view(now), now, unfilled) == []


def test_soccer_buys_team_yes_never_draw(env):
    env.soccer_game("s", T0, league="epl")
    for t in ("s-H-Y", "s-D-Y", "s-A-Y"):
        env.core.execute("UPDATE markets SET volume=30000 WHERE condition_id IN (SELECT condition_id FROM tokens "
                         "WHERE token_id=?)", (t,))
    env.core.commit()
    now = T0 + 96 * 60
    for tok, p in (("s-H-Y", 0.12), ("s-D-Y", 0.15), ("s-A-Y", 0.72)):
        env.bar(tok, now, p)
        level_book(env, tok, now, p - 0.005, p + 0.005)
    s = strat("soccer", min_wall_minute=95, trigger_price=0.70, leagues=["epl"])
    [it] = s.entry_signals(env.view(now), now, Ledger())
    assert it.token_id == "s-A-Y" and it.features["kind"] == "AWAY"


def test_maker_entry_open_wait_game_end_and_floor(env):
    env.us_game("g", "nfl", T0, ended_at=T0 + 200 * 60)
    s = strat()
    now = at(env, 102, 0.81)
    pos = PositionView("p", "pending", "g-h", "c-g", "g", "nfl", now, None, 0, 0,
                       exit_rules={"trigger_price": 0.80, "floor_c": 0.05, "max_wait_minutes": 30, "hours_max": 6.0})
    assert s.maker_entry_open(env.view(now + 60), now + 60, pos)
    later = at(env, 140, 0.81)
    assert not s.maker_entry_open(env.view(later), later, pos)                       # waited 30 min
    patient = PositionView(**{**pos.__dict__, "exit_rules": {**pos.exit_rules, "max_wait_minutes": None}})
    assert s.maker_entry_open(env.view(later), later, patient)
    low = at(env, 141, 0.74)                                                          # below Y - c
    assert not s.maker_entry_open(env.view(low), low, patient)
    end = at(env, 201, 0.81)
    assert not s.maker_entry_open(env.view(end), end, patient)                       # game over


def test_entry_price_below_rule_and_synthetic_tick():
    real = Book("t", 0, [(0.81, 1e6)], [(0.82, 1e6)])
    assert maker.entry_price(real, 0.01, 0.01, 0.999, "below") == pytest.approx(0.80)   # one tick below the bid
    b = Book("t", 0, [(0.815, 1e6)], [(0.825, 1e6)], "synthetic", True)
    # a synthetic bid on a half cent: 0.805 rounds down to the cent, as entry_price does in the engine replay
    assert maker.entry_price(b, maker.synthetic_tick(b.mid), 0.01, 0.999, "below") == pytest.approx(0.80)
    assert maker.synthetic_tick(0.97) == 0.001 and maker.synthetic_tick(0.5) == 0.01
    assert L.limit_price(0.82) == pytest.approx(0.80) and L.limit_price(0.815) == pytest.approx(0.80)
    assert L.limit_price(0.98) == pytest.approx(0.974)         # 0.001 tick near 1


class _View:
    def __init__(self, book):
        self.b = book

    def book(self, token_id, now, max_age_s=180):
        return self.b


def _order(tmp_path, price=0.81, created=1000):
    from polylab import db
    from polylab.settings import Paths

    paths = Paths(tmp_path / "v").ensure()
    led = StrategyLedger(db.strategy(paths, "p"), "p")
    intent = EntryIntent("t", "c", "g", "nfl", "nfl", "Home", 0.82, 0.01, 0.999, "t", {})
    pid = led.open_position(intent, "paper", 1, 5.0, created)
    _, iid = maker.PaperVenue(None, lambda c: None).post(led, position_id=pid, side="BUY", token_id="t",
                                                         condition_id="c", price=price, shares=6.17, now=created,
                                                         purpose="entry")
    return led.order(iid)


def test_paper_venue_bar_trade_through_modes(tmp_path):
    order = _order(tmp_path)
    sched = lambda c: FeeSchedule(0.05, 1, True, "override_0.05")     # noqa: E731  (backtest --fee-rate)
    synth = lambda p, ts=1060: _View(Book("t", ts, [(p - 0.005, 1e6)], [(p + 0.005, 1e6)], "synthetic", True))  # noqa: E731
    assert maker.PaperVenue(synth(0.80), sched).poll(order, 1060).state == "live"            # live paper: never
    assert maker.PaperVenue(synth(0.81), sched, "mid").poll(order, 1060).state == "live"     # touch is not a fill
    assert maker.PaperVenue(synth(0.80, ts=1000), sched, "mid").poll(order, 1000).state == "live"   # same minute
    poll = maker.PaperVenue(synth(0.80), sched, "mid").poll(order, 1060)
    [f] = poll.fills
    assert poll.state == "done" and f.price == 0.81 and f.shares == 6.17 and f.liquidity == "maker"
    assert f.fee == 0.0 and f.taker_fee_est > 0                 # forced fee rate charges taker fills only
    assert maker.PaperVenue(synth(0.807), sched, "ask").poll(order, 1060).state == "live"   # ask 0.812 not < 0.81
    assert maker.PaperVenue(synth(0.80), sched, "ask").poll(order, 1060).fills             # ask 0.805 < 0.81
    with pytest.raises(ValueError):
        maker.PaperVenue(None, sched, "bogus")


def _variant(tmp_path, wait=None):
    p = write(tmp_path / "reg", "lts-bt", {
        "family": "lts", "mode": "paper", "sports": {"nfl": {"mode": "paper", "stake_usdc": 5}},
        "params": {"order_style": "maker", "maker_price_rule": "below", "maker_ttl_minutes": 600,
                   "maker_reprice_ticks": 100, "min_game_volume_usd": 20000,
                   "sport_overrides": {"nfl": {"min_wall_minute": 100, "trigger_price": 0.80, "max_wait_minutes": wait,
                                               "hours_max": 6.0}}},
        "limits": {"max_positions": 10, "max_open_usdc": 100, "daily_loss_stop_usdc": 50, "max_new_per_cycle": 5}})
    return registry.load_variant(p)


def _history(env, dip: float, win: int):
    env.us_game("g", "nfl", T0, status="ended", ended_at=T0 + 200 * 60)
    for m in range(0, 200):
        p = 0.70 if m < 101 else 0.815 if m < 110 else dip if m == 110 else 0.86
        env.bar("g-h", T0 + m * 60, p, source="history")
        env.bar("g-a", T0 + m * 60, round(1 - p, 4), source="history")
    env.resolve("c-g", win, T0 + 210 * 60)


def test_backtest_auto_uses_maker_bars_fill_at_limit_fee_free(tmp_path):
    env = Env(tmp_path / "root")
    _history(env, dip=0.79, win=0)                              # signal at 0.815 -> limit 0.80; bar 0.79 trades through
    out = bt.backtest(env.paths, _variant(tmp_path), T0 - 3600, T0 + 86400, fee_rate=0.05)
    assert out["fill_model"] == "maker_bars_mid"
    [t] = out["trades"]
    assert t["entry_price"] == pytest.approx(0.80) and t["cost"] == pytest.approx(6.25 * 0.80)
    assert t["exit_reason"] == "resolution_win" and t["pnl"] == pytest.approx(6.25 - 5.0)
    strict = bt.backtest(env.paths, _variant(tmp_path), T0 - 3600, T0 + 86400, fee_rate=0.05,
                         fill_model="maker_bars", maker_through="ask")
    assert strict["summary"]["n"] == 1                          # 0.79 + 0.005 < 0.80 as well
    proxy = bt.backtest(env.paths, _variant(tmp_path), T0 - 3600, T0 + 86400, fee_rate=0.05, fill_model="taker")
    assert proxy["fill_model"] == "taker_proxy"


def test_backtest_no_trade_through_means_unfilled(tmp_path):
    env = Env(tmp_path / "root")
    _history(env, dip=0.80, win=0)                              # touches the limit, never below it
    out = bt.backtest(env.paths, _variant(tmp_path), T0 - 3600, T0 + 86400, fee_rate=0.05)
    assert out["summary"]["n"] == 0 and out["summary"]["unfilled"] == 1


def test_backtest_wait_cancels_before_late_dip(tmp_path):
    env = Env(tmp_path / "root")
    _history(env, dip=0.79, win=0)                              # dip 9 minutes after the signal
    out = bt.backtest(env.paths, _variant(tmp_path, wait=5), T0 - 3600, T0 + 86400, fee_rate=0.05)
    assert out["summary"]["n"] == 0 and out["summary"]["unfilled"] == 1


def test_evaluator_matches_engine_rules(env):
    _history(env, dip=0.79, win=0)
    env.bar("g-h", T0 + 120 * 60, 0.837, source="history")     # one later dip below the T60%/Y0.85 limit (0.84)
    env.bar("g-a", T0 + 120 * 60, 0.163, source="history")
    [g] = L.load_games(env.core, "nfl", T0 + 86400)
    assert g["tokens"][0] == ("g-a", False) and g["tokens"][1] == ("g-h", True)   # sorted by side
    sigs = {(s["progress"], s["y"]): s for s in L.game_signals(env.core, "nfl", g)}
    s = sigs[(0.6, 0.85)]                                      # T60% = 113 min: first in-band minute, price 0.86
    assert s["p"] == 0.86 and s["ts"] == T0 + 113 * 60 and s["win"]
    lim, fill = s["fills"][("main", None)]
    # bid 0.855 - 0.01 = 0.845 rounds to the 0.01 tick like the engine's entry_price (0.84)
    assert lim == pytest.approx(0.84) and fill == (T0 + 120 * 60, 0.837)          # strictly below, later bar
    assert s["fills"][("strict", None)][1] is None              # 0.837 is not below 0.84 - 0.005
    assert s["fills"][("main", 5)][1] is None                   # cancelled 5 minutes after posting
    assert (0.6, 0.80) not in sigs                              # 0.86 is above the [0.80, 0.83] band
    assert L.wall_minute("nfl", 0.9) == 170 and L.band(0.99) == (0.99, 0.995)
    pnl, cost = L.maker_trade(0.80, True)
    assert cost == pytest.approx(5.0) and pnl == pytest.approx(1.25)
    tp, tc = L.taker_trade(0.80, True)
    assert tc > 5.0 and tp < pnl                                  # taker pays the spread and the fee


def test_rules_and_selection_on_synthetic_grid():
    def cell(prog, y, roi, n=60):
        h = {"n": n // 2, "pnl": roi * 5 * n / 2, "cost": 5.0 * n / 2, "roi": roi}
        main = {"n": n, "pnl": roi * 5 * n, "cost": 5.0 * n, "roi": roi, "h1": dict(h), "h2": dict(h),
                "lo80": roi / 2, "lo95": roi / 4, "seasons": {"2025": {"n": n, "roi": roi}}}
        stress = {"roi": roi}
        return {"progress": prog, "y": y, "wait": "end", "t_min": 0, "main": main, "spread02": stress,
                "strict": stress}
    cells = {(p, y, "end"): cell(p, y, 0.05 if (p, y) in ((0.9, 0.85), (0.9, 0.9), (0.8, 0.85)) else -0.02)
             for p in L.PROGRESS for y in L.THRESHOLDS}
    rule = promotion.sample_rule("nfl")
    rec = L.rule_record(cells, cells[(0.9, 0.85, "end")], rule)
    assert rec["R1"] and rec["R3"] and rec["R4"] and rec["R6"]
    assert rec["neighbors"] == 3 and rec["neighbors_ok"] == 2 and rec["R2"]
    sel = L.select_arm(cells, "nfl", "king", rule)
    assert (sel["progress"], sel["y"]) in ((0.9, 0.85), (0.9, 0.9))
    queen = L.select_arm(cells, "nfl", "queen", rule)
    assert queen["progress"] in (0.6, 0.7)
    mc = L.mc_check(list(cells.values()), rule)
    assert mc["top"] == 20 and mc["h2_nonneg"] == 3


# ------------------------------------------------------------------ account collision guard

def _pair(tmp_path, other_mode="live"):
    d = tmp_path / "reg"
    live = registry.load_variant(write(d, "lts-king", {"account": "king", "mode": other_mode,
                                                       "sports": {"nfl": {"mode": other_mode}}}))
    plum = registry.load_variant(write(d, "plum-king", {"account": "king", "mode": "paper",
                                                        "sports": {"nfl": {"mode": "paper"},
                                                                   "nba": {"mode": "paper"}}}))
    return live, plum


def test_promotion_refuses_second_live_variant_on_account(tmp_path):
    live, plum = _pair(tmp_path)
    gate = promotion.evaluate(plum, "nfl", _trades(40), NOW, None, variants=[live, plum])
    assert not gate.ok and gate.stage == "eligibility" and "already held by live variant" in gate.reason
    direct = promotion.evaluate_direct(plum, "nfl", _trades(40), NOW, None, None, variants=[live, plum])
    assert direct.stage == "eligibility" and "lts-king" in direct.reason
    assert "already held" not in promotion.evaluate(plum, "nfl", _trades(40), NOW, None).reason   # no registry given
    paper_live, paper_plum = _pair(tmp_path / "b", other_mode="paper")
    assert promotion.account_conflict(paper_plum, [paper_live, paper_plum]) is None


def test_validator_refuses_live_flip_on_shared_account(tmp_path):
    live, plum = _pair(tmp_path)
    ctx = Context({"lts-king": live, "plum-king": plum},
                  {"plum-king": Facts(40, by_sport={"nfl": Facts(40), "nba": Facts(40)})}, now=NOW)
    ctx.promotions[promotion_key("plum-king", "nfl")] = {"ok": True}
    ch = {"variant_id": "plum-king", "sport": "nfl", "change": "mode", "values": {"mode": "live"}, "rationale": "g"}
    d = validate({"changes": [ch]}, ctx, Rules(), "promotion")[0]
    assert not d.accepted and "already held by live variant(s) ['lts-king']" in d.reason


def test_validator_refuses_two_paper_masters_going_live_in_one_retro(tmp_path):
    a, b = _pair(tmp_path, other_mode="paper")
    ctx = Context({"lts-king": a, "plum-king": b},
                  {k: Facts(40, by_sport={"nfl": Facts(40), "nba": Facts(40)}) for k in ("lts-king", "plum-king")},
                  now=NOW)
    for vid in ("lts-king", "plum-king"):
        ctx.promotions[promotion_key(vid, "nfl")] = {"ok": True}
    changes = [{"variant_id": v, "sport": "nfl", "change": "mode", "values": {"mode": "live"}, "rationale": "g"}
               for v in ("lts-king", "plum-king")]
    first, second = validate({"changes": changes}, ctx, Rules(), "promotion")
    assert first.accepted and not second.accepted and "lts-king" in second.reason


def test_alias_variants_prefers_live(tmp_path, monkeypatch):
    from polylab.execution import accounts

    live, plum = _pair(tmp_path)
    monkeypatch.setattr(accounts.registry, "load_all", lambda include_off=True: [plum, live])
    assert accounts.alias_variants()["king"] == "lts-king"
    paper_live, paper_plum = _pair(tmp_path / "b", other_mode="paper")
    monkeypatch.setattr(accounts.registry, "load_all", lambda include_off=True: [paper_live, paper_plum])
    assert accounts.alias_variants()["king"] == "plum-king"     # none live: unchanged (last by id)


def test_real_lts_yaml_is_maker_below_paper_with_bounds():
    for vid, acct in (("lts-king", "king"), ("lts-queen", "queen")):
        v = registry.load_variant(registry.REGISTRY_DIR / f"{vid}.yaml")
        s = build(v)
        assert v.family == "lts" and v.account == acct and v.stake_usdc == 5.0
        for sport in v.sports:
            assert s.order_style(sport) == "maker" and s.execution(sport)["maker_price_rule"] == "below"
            assert v.sport_stake(sport) == 5.0
            assert f"sport_overrides.{sport}.trigger_price" in v.bounds


def test_lts_off_the_ai_direct_live_path():
    from polylab.autopilot.validator import OWNER_LOCKED, OWNER_NO_DIRECT_LIVE

    assert {"lts-king", "lts-queen"} <= set(OWNER_NO_DIRECT_LIVE)
    assert not {"lts-king", "lts-queen"} & set(OWNER_LOCKED)          # params stay tunable by the retro


def test_lts_reminders_are_open_from_the_study_day():
    from polylab.reports import reminders

    day = 1_791_590_400 + 3600                                         # 2026-10-10 01:00 UTC
    ids = {r["id"] for r in reminders.due({"variants": []}, day)}
    assert "decision:lts-nfl-king-live" in ids
    assert "decision:lts-nfl-king-live" not in {r["id"] for r in reminders.due({"variants": []}, day - 2 * 86400)}
