"""Outcome-changing counterfactual checks, with no live client imports."""
import importlib.util
from pathlib import Path
import sys
import pytest
import gzip
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("catdog_replay", ROOT / "tools/catdog_takeprofit_replay.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def snapshot(time, token="yes", ask=.95, bid=.94, valid=True):
    return dict(time=time, timestamp=f"2026-09-07T00:{time//60:02}:00Z", token_id=token,
        event_id="one-event", condition_id="one-condition", title="One game", outcome=token,
        result_kind="HOME" if token == "yes" else "AWAY",
        sport_family="mlb", valid=valid, entry_eligible=True, open_at_observation=True,
        ask_state="PRESENT_VALID",
        spread=ask-bid, asks=[(ask,100)], bids=[(bid,100)])


def group(time, ask=.95, bid=.94):
    return [snapshot(time, ask=ask,bid=bid),snapshot(time,token="other",ask=.05,bid=.04)]


def run(groups, target=.99, failures=(), fee=25):
    return replay.replay_event(groups, {}, failures, .95, target, fee, 90)


def test_tp_changes_a_later_loss_without_using_the_resolution_as_entry_information():
    groups=[group(0),group(60,ask=.999,bid=.99),group(120,ask=.03,bid=.02)]
    take=run(groups)
    hold=run(groups,target=None)
    assert take["exit_reason"]=="TP" and take["modeled_net_usdc"]>0
    assert hold["exit_reason"]=="SL" and hold["modeled_net_usdc"]<0


def test_gap_hiding_a_tp_is_censored_not_a_zero_or_a_later_loss():
    result=run([group(0),group(180,ask=.03,bid=.02)])
    assert result["exit_reason"]=="path_gap_or_failed_run"
    assert result["modeled_net_usdc"] is None


def test_failed_run_inside_apparently_one_minute_gap_is_censored():
    result=run([group(0),group(60,ask=.999,bid=.99)],failures=[(30,45)])
    assert result["modeled_net_usdc"] is None


def test_best_bid_tp_without_full_depth_does_not_fabricate_a_sell():
    groups=[group(0),group(60,ask=.999,bid=.99)]
    groups[1][0]["bids"]=[(.99,.01),(.98,100)]
    result=run(groups)
    assert result["exit_reason"]=="right_censored"
    assert result["modeled_net_usdc"] is None


def test_high_cost_scenario_can_remove_an_apparent_tp():
    groups=[group(0,ask=.988,bid=.98),group(60,ask=.999,bid=.99)]
    assert run(groups,fee=0)["exit_reason"]=="TP"
    assert run(groups,fee=100)["modeled_net_usdc"] is None


def test_even_zero_fees_do_not_allow_tp_when_sdk_dust_prevents_whole_cost_recovery():
    groups=[group(0,ask=.989,bid=.98),group(60,ask=.999,bid=.99)]
    assert run(groups,fee=0)["modeled_net_usdc"] is None


@pytest.mark.parametrize("opened,spread", [(False,.009),(True,.11),(True,-.01),(True,float('nan')),(True,None)])
def test_tp_needs_open_lifecycle_and_valid_spread_even_with_full_99_bid(opened, spread):
    groups=[group(0),group(60,ask=.999,bid=.99)]
    groups[1][0]["open_at_observation"]=opened
    groups[1][0]["spread"]=spread
    result=run(groups)
    assert result["exit_reason"] == "right_censored"
    assert result["modeled_net_usdc"] is None


def test_tp_accepts_valid_empty_asks_without_relaxing_the_stop_guard():
    groups=[group(0),group(60,ask=.999,bid=.99)]
    groups[1][0].update(asks=[],ask_state=replay.ask_side_state({"asks":[]}),spread=None)
    assert run(groups)["exit_reason"] == "TP"
    groups[1][0]["bids"]=[(.69,100)]
    assert run(groups)["modeled_net_usdc"] is None  # stop still needs spread


@pytest.mark.parametrize("asks", [None,{},["bad"],[{"price":float('nan'),"size":20}],[{"price":.995,"size":-1}]])
def test_replay_rejects_malformed_asks_instead_of_calling_them_bid_only(asks):
    state=replay.ask_side_state({"asks":asks})
    assert state == "INVALID"
    groups=[group(0),group(60,ask=.999,bid=.99)]
    groups[1][0].update(asks=[],ask_state=state,spread=None)
    assert run(groups)["modeled_net_usdc"] is None


@pytest.mark.parametrize("asks,state", [([],"EMPTY_VALID"),([{"price":"NaN","size":"20"}],"INVALID")])
def test_original_batch_preserves_empty_vs_malformed_even_if_normalized_levels_are_empty(asks,state):
    raw=json.dumps([{"asset_id":"token","asks":asks,"bids":[{"price":".99","size":"20"}]}]).encode()
    books=replay.decode_original_book_asks(gzip.compress(raw),hashlib.sha256(raw).hexdigest(),len(raw))
    assert books["token"][1] == state


def test_original_batch_checksum_mismatch_never_becomes_an_empty_ask_proof():
    raw=b'[{"asset_id":"token","asks":[]}]'
    with pytest.raises(ValueError,match="checksum"):
        replay.decode_original_book_asks(gzip.compress(raw),'0'*64,len(raw))


def test_no_synthetic_entry_when_ask_has_no_exact_five_dollar_depth():
    groups=[group(0)]
    groups[0][0]["asks"]=[(.95,.1)]
    assert run(groups) is None
