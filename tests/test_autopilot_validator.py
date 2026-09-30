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
