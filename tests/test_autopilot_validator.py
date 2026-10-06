from polylab.autopilot.validator import Context, Facts, Rules, apply_to_variant, validate
from polylab.registry import Variant

NOW = 1_790_000_000
DAY = 86400


def variant(vid="wm-cat", mode="live", stake=5.0, account="cat", **params):
    return Variant(id=vid, family="watermelon", hypothesis="h", account=account, mode=mode, sports=["soccer"],
                   stake_usdc=stake, params={"prob_min": 0.92, "tp": 0.99, "n": 3, **params},
                   bounds={"prob_min": [0.88, 0.97, 0.01], "tp": [0.95, 0.995], "n": [1, 6, 1]})


def ctx(*variants, facts=None, aliases=("cat", "dog", "owl")):
    vs = {v.id: v for v in variants} if variants else {"wm-cat": variant()}
    f = facts or {vid: Facts(trades_at_version=30, last_param_change_ts=NOW - 5 * DAY,
                             last_stake_change_ts=NOW - 5 * DAY, promote_ok=False) for vid in vs}
    return Context(variants=vs, facts=f, now=NOW, families={"watermelon"}, known_aliases=set(aliases))


def change(kind, values, vid="wm-cat"):
    return {"variant_id": vid, "change": kind, "values": values, "rationale": "because", "evidence": {"n": 30}}


def one(proposal_changes, c=None, rules=None, source="ai"):
    return validate({"schema": "polylab.proposal/v1", "changes": proposal_changes}, c or ctx(), rules or Rules(), source)


def test_param_within_bounds_and_step_accepted():
    (d,) = one([change("params", {"prob_min": 0.93})])
    assert d.accepted and "prob_min 0.92→0.93" in d.summary


def test_reject_out_of_bounds_and_big_step_and_unknown_param():
    assert "outside bounds" in one([change("params", {"prob_min": 0.99})])[0].reason
    assert "max_step" in one([change("params", {"prob_min": 0.95})])[0].reason
    assert "not tunable" in one([change("params", {"stop_loss": 0.5})])[0].reason
    assert "integer" in one([change("params", {"n": 3.5})])[0].reason
    # bounds without max_step -> 10% of range
    assert one([change("params", {"tp": 0.99 - 0.0045})])[0].accepted
    assert not one([change("params", {"tp": 0.97})])[0].accepted


def test_min_sample_and_cooldown():
    c = ctx(facts={"wm-cat": Facts(trades_at_version=5)})
    assert "settled trades" in one([change("params", {"prob_min": 0.93})], c)[0].reason
    c = ctx(facts={"wm-cat": Facts(trades_at_version=50, last_param_change_ts=NOW - DAY)})
    assert "cooldown" in one([change("params", {"prob_min": 0.93})], c)[0].reason


def test_stake_jump_without_gate_rejected_and_demotion_allowed():
    assert "gate" in one([change("stake", {"stake_usdc": 10})])[0].reason
    c = ctx(variant(stake=10.0))
    c.facts["wm-cat"].promote_ok = True
    assert "one ladder step" in one([change("stake", {"stake_usdc": 50})], c)[0].reason
    assert one([change("stake", {"stake_usdc": 25})], c)[0].accepted
    assert "ladder" in one([change("stake", {"stake_usdc": 200})], c)[0].reason
    # demotion: always allowed, even inside cooldown and without gate
    c = ctx(variant(stake=25.0), facts={"wm-cat": Facts(last_stake_change_ts=NOW - 60, promote_ok=None)})
    assert one([change("stake", {"stake_usdc": 10})], c)[0].accepted


def test_gate_passed_but_cooldown_blocks_promotion():
    c = ctx(facts={"wm-cat": Facts(promote_ok=True, last_stake_change_ts=NOW - DAY)})
    assert "cooldown" in one([change("stake", {"stake_usdc": 10})], c)[0].reason


def test_mode_only_toward_safety():
    assert one([change("mode", {"mode": "paper"})])[0].accepted
    c = ctx(variant(mode="paper", account=None))
    assert "not allowed" in one([change("mode", {"mode": "live"})], c)[0].reason


def test_new_variant_rules():
    rules = Rules.for_kind("weekly")
    nv = change("new_variant", {"based_on": "wm-cat", "hypothesis": "late soccer", "params": {"prob_min": 0.95},
                                "account": None}, vid="wm-late")
    (d,) = one([nv], rules=rules)
    assert d.accepted
    v = apply_to_variant(d, ctx())
    assert v.mode == "paper" and v.stake_usdc == 5.0 and v.params["prob_min"] == 0.95 and v.account is None
    assert "weekly" in one([nv])[0].reason  # daily retro cannot create variants
    used = change("new_variant", {"based_on": "wm-cat", "account": "cat"}, vid="wm-x")
    assert "unused" in one([used], rules=rules)[0].reason
    free = change("new_variant", {"based_on": "wm-cat", "account": "owl"}, vid="wm-y")
    assert one([free], rules=rules)[0].accepted
    bad = change("new_variant", {"based_on": "wm-cat", "params": {"prob_min": 0.5}}, vid="wm-z")
    assert "outside bounds" in one([bad], rules=rules)[0].reason
    assert "exists" in one([change("new_variant", {"based_on": "wm-cat"}, vid="wm-cat")], rules=rules)[0].reason


def test_budget_one_change_per_variant_and_strict_parsing():
    a, b = variant("a-1"), variant("b-1", account="dog")
    c = ctx(a, b, variant("c-1", account="owl"))
    ds = one([change("mode", {"mode": "paper"}, "a-1"), change("mode", {"mode": "paper"}, "a-1"),
              change("mode", {"mode": "paper"}, "b-1"), change("mode", {"mode": "paper"}, "c-1")], c)
    assert [d.accepted for d in ds] == [True, False, True, False]
    assert "one change per variant" in ds[1].reason and "max 2" in ds[3].reason
    assert "unknown fields" in one([{**change("retire", {}), "extra": 1}])[0].reason
    assert "variant_id" in one([change("retire", {}, vid="../../etc")])[0].reason
    assert "unknown change" in one([change("delete", {})])[0].reason
    assert not validate({"changes": "x"}, ctx(), Rules())[0].accepted
    assert "rationale" in one([{**change("retire", {}), "rationale": ""}])[0].reason


def test_ladder_source_not_counted_in_budget():
    c = ctx(variant("a-1"), variant("b-1", account="dog"), variant("c-1", account="owl"))
    state = {"count": 0, "touched": set(), "new_variants": 0}
    validate({"changes": [change("mode", {"mode": "paper"}, "a-1")]}, c, Rules(), "ladder", state)
    ds = validate({"changes": [change("retire", {}, "b-1"), change("retire", {}, "c-1")]}, c, Rules(), "ai", state)
    assert all(d.accepted for d in ds)


def test_stale_inbox_rejected():
    p = {"schema": "polylab.proposal/v1", "context_generated_at": "2026-01-01T00:00:00Z",
         "changes": [change("retire", {})]}
    (d,) = validate(p, ctx(), Rules(), "inbox:x.json")
    assert not d.accepted and "stale" in d.reason


def test_dotted_sport_override_params():
    v = variant(sport_overrides={"nfl": {"prob_min": 0.94}})
    v.bounds["sport_overrides.nfl.prob_min"] = [0.9, 0.98, 0.01]
    c = ctx(v)
    (d,) = one([change("params", {"sport_overrides.nfl.prob_min": 0.95})], c)
    assert d.accepted, d.reason
    new = apply_to_variant(d, c)
    assert new.params["sport_overrides"]["nfl"]["prob_min"] == 0.95 and v.params["sport_overrides"]["nfl"]["prob_min"] == 0.94
    assert "max_step" in one([change("params", {"sport_overrides.nfl.prob_min": 0.97})], c)[0].reason


def test_stake_change_scales_absolute_limits_and_new_variant_ignores_ai_limits():
    v = variant(stake=10.0)
    v.limits = {"max_open_usdc": 200, "daily_loss_stop_usdc": 40, "max_positions": 20}
    c = ctx(v)
    (d,) = one([change("stake", {"stake_usdc": 5})], c)
    new = apply_to_variant(d, c)
    assert new.limits == {"max_open_usdc": 100.0, "daily_loss_stop_usdc": 20.0, "max_positions": 20}
    nv = change("new_variant", {"based_on": "wm-cat", "limits": {"max_open_usdc": 99999}}, vid="wm-n")
    (d,) = one([nv], c, rules=Rules.for_kind("weekly"))
    assert apply_to_variant(d, c).limits["max_open_usdc"] == 100.0


# ------------------------------------------------------------ backtest-backed retune

from polylab.autopilot.validator import backtest_candidates, backtest_gate, evidence_key  # noqa: E402


def arm(n, roi, dd, halves):
    return {"n": n, "roi": roi, "max_dd": dd, "halves": [{"n": h[0], "roi": h[1], "max_dd": 0.0} for h in halves]}


def evidence(cur=None, new=None):
    return {"range": [NOW - 120 * DAY, NOW], "split_ts": NOW - 60 * DAY,
            "current": cur or arm(60, -0.010, 10.0, [(30, -0.010), (30, -0.010)]),
            "proposed": new or arm(50, -0.002, 9.0, [(25, -0.004), (25, 0.001)])}


def bt_ctx(trades=0, idle=False, last_change=NOW - 5 * DAY, ev=None, values=None):
    c = ctx(facts={"wm-cat": Facts(trades_at_version=trades, last_param_change_ts=last_change, idle=idle)})
    if ev is not None:
        c.backtests[evidence_key("wm-cat", values or {"prob_min": 0.93})] = ev
    return c


WEEKLY = Rules.for_kind("weekly")


def test_backtest_path_accepts_only_retro_evidence_for_exact_values():
    ch = change("params", {"prob_min": 0.93})
    ch["evidence"] = {"n": 400, "roi": 0.05, "both_halves_better": True}  # AI-asserted numbers are ignored
    (d,) = one([ch], bt_ctx(), rules=WEEKLY)
    assert not d.accepted and "no retro-run backtest" in d.reason
    (d,) = one([ch], bt_ctx(ev=evidence(), values={"prob_min": 0.94}), rules=WEEKLY)  # evidence for other values
    assert not d.accepted and "no retro-run backtest" in d.reason
    (d,) = one([ch], bt_ctx(ev=evidence()), rules=WEEKLY)
    assert d.accepted, d.reason
    assert "backtest:" in d.summary and d.backtest["proposed"]["n"] == 50
    assert d.as_dict()["backtest"]["split_ts"] == NOW - 60 * DAY
    # int/float spelling of the same value maps to the same evidence
    assert evidence_key("x", {"a": 95}) == evidence_key("x", {"a": 95.0})


def test_backtest_path_disabled_without_retune_rules_and_daily_only_for_idle():
    ch = change("params", {"prob_min": 0.93})
    assert "settled trades" in one([ch], bt_ctx(ev=evidence()), rules=Rules.for_kind("monthly"))[0].reason
    daily = Rules.for_kind("daily")
    (d,) = one([ch], bt_ctx(ev=evidence()), rules=daily)
    assert not d.accepted and "idle" in d.reason
    assert one([ch], bt_ctx(ev=evidence(), idle=True), rules=daily)[0].accepted


def test_backtest_path_allows_up_to_twice_max_step():
    # enough live trades: a 1-step change needs no backtest, 2 steps need one, 3 steps never pass
    assert one([change("params", {"prob_min": 0.93})], bt_ctx(trades=30), rules=WEEKLY)[0].accepted
    two = change("params", {"prob_min": 0.94})
    assert "no retro-run backtest" in one([two], bt_ctx(trades=30), rules=WEEKLY)[0].reason
    assert "max_step" in one([two], bt_ctx(trades=30), rules=Rules())[0].reason  # plain path unchanged
    c = bt_ctx(trades=30, ev=evidence(), values={"prob_min": 0.94})
    assert one([two], c, rules=WEEKLY)[0].accepted
    three = change("params", {"prob_min": 0.95})
    c = bt_ctx(trades=30, ev=evidence(), values={"prob_min": 0.95})
    assert "2x max_step" in one([three], c, rules=WEEKLY)[0].reason


def test_backtest_gate_rules():
    r = WEEKLY
    assert backtest_gate(evidence(), r, 5.0)[0]
    assert "n=39" in backtest_gate(evidence(new=arm(39, 0.01, 1.0, [(20, 0.01), (19, 0.01)])), r, 5.0)[1]
    worse_h2 = arm(50, 0.0, 9.0, [(25, 0.01), (25, -0.02)])
    assert "H2 ROI" in backtest_gate(evidence(new=worse_h2), r, 5.0)[1]
    empty_half = arm(50, 0.01, 1.0, [(50, 0.01), (0, None)])
    assert "H2 proposed n=0" in backtest_gate(evidence(new=empty_half), r, 5.0)[1]
    thin_half = arm(50, 0.01, 1.0, [(45, 0.01), (5, 0.02)])
    assert "H2 proposed n=5" in backtest_gate(evidence(new=thin_half), r, 5.0)[1]
    deeper = arm(50, 0.0, 12.5, [(25, 0.0), (25, 0.0)])  # MDD 12.5 > 10 x 1.2
    assert "MDD" in backtest_gate(evidence(new=deeper), r, 5.0)[1]
    assert backtest_gate(evidence(new=arm(50, 0.0, 12.0, [(25, 0.0), (25, 0.0)])), r, 5.0)[0]
    # a current arm that never trades: its ROI counts as 0 and its MDD 0 allows one stake of drawdown
    idle_cur = arm(0, None, 0.0, [(0, None), (0, None)])
    assert backtest_gate(evidence(cur=idle_cur, new=arm(45, 0.004, 5.0, [(20, 0.002), (25, 0.006)])), r, 5.0)[0]
    assert "H1 ROI" in backtest_gate(evidence(cur=idle_cur, new=arm(45, 0.0, 1.0, [(20, -0.001), (25, 0.01)])),
                                     r, 5.0)[1]
    assert "MDD" in backtest_gate(evidence(cur=idle_cur, new=arm(45, 0.01, 5.5, [(20, 0.01), (25, 0.01)])), r, 5.0)[1]
    assert not backtest_gate({"error": "timeout"}, r, 5.0)[0]
    assert not backtest_gate({"current": {}, "proposed": {}}, r, 5.0)[0]


def test_backtest_path_respects_cooldown_bounds_and_owner_fixed_params():
    ch = change("params", {"prob_min": 0.93})
    assert "cooldown" in one([ch], bt_ctx(ev=evidence(), last_change=NOW - DAY), rules=WEEKLY)[0].reason
    out = change("params", {"prob_min": 0.99})
    assert "outside bounds" in one([out], bt_ctx(ev=evidence(), values={"prob_min": 0.99}), rules=WEEKLY)[0].reason
    v = variant("goal-over-all", account="cat")
    v.params["stop_loss_pct"] = 0.1
    v.bounds["stop_loss_pct"] = [0.05, 0.3, 0.05]
    c = ctx(v, facts={"goal-over-all": Facts(trades_at_version=99)})
    (d,) = one([change("params", {"stop_loss_pct": 0.15}, vid="goal-over-all")], c, rules=WEEKLY)
    assert not d.accepted and "owner-fixed" in d.reason
    assert one([change("params", {"prob_min": 0.93}, vid="goal-over-all")], c, rules=WEEKLY)[0].accepted


def test_backtest_candidates_lists_only_changes_missing_evidence():
    c = bt_ctx()
    prop = {"changes": [change("params", {"prob_min": 0.93}), change("params", {"prob_min": 0.99}),
                        change("retire", {})]}
    assert backtest_candidates(prop, c, WEEKLY) == [("wm-cat", {"prob_min": 0.93})]
    assert backtest_candidates(prop, c, Rules.for_kind("daily")) == []  # not idle
    c.backtests[evidence_key("wm-cat", {"prob_min": 0.93})] = evidence()
    assert backtest_candidates(prop, c, WEEKLY) == []


def test_backtest_gate_low_frequency_sport_rule():
    """2026-10-06 `manual:nfl-promotion-window`: NFL retunes need n >= 20 (halves >= 5) over a 365-day replay."""
    from polylab.risk.promotion import sample_rule
    nfl = sample_rule("nfl")
    small = arm(22, 0.01, 1.0, [(11, 0.01), (11, 0.01)])
    year = {**evidence(new=small), "range": [NOW - 365 * DAY, NOW]}
    assert not backtest_gate(year, WEEKLY, 5.0)[0]                     # default rule: n=22 < 40
    assert backtest_gate(year, WEEKLY, 5.0, nfl)[0]
    assert "window 120d" in backtest_gate(evidence(new=small), WEEKLY, 5.0, nfl)[1]   # 120-day evidence refused
    c = bt_ctx(trades=16, ev=year)
    c.facts["wm-cat"].by_sport["nfl"] = Facts(trades_at_version=16, last_param_change_ts=NOW - 5 * DAY)
    from polylab.autopilot.validator import min_trades_for, promotion_key
    c.sample_rules[promotion_key("wm-cat", "nfl")] = nfl
    assert min_trades_for({"variant_id": "wm-cat", "sport": "nfl"}, c, WEEKLY) == 15
    assert min_trades_for({"variant_id": "wm-cat", "sport": "nba"}, c, WEEKLY) == 20
