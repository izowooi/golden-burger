"""Pure snapshot tests plus real Broker/FakeSDK/temporary SQLite integration."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from itertools import combinations

import pytest

from polybot.position_state import Limits, PositionStateError, reduce_positions

D = Decimal
LIMITS = Limits(max_positions=20, max_notional_usdc=D(100), max_event_positions=2,
                max_event_notional_usdc=D(10), max_cycle_positions=2,
                max_cycle_notional_usdc=D(10), max_order_notional_usdc=D(5))


def snapshot(sid="b1", side="BUY", *, event="e1", token="t1", condition="c1",
             quantity="5", price="0.5", actual=None, fill_price=None, complete=False,
             fee="0.1", status=None, no_post=False, zero=False, trade_status="CONFIRMED"):
    q, p = D(quantity), D(price)
    shares = (q / p).quantize(D("0.0001"), rounding="ROUND_FLOOR") if side == "BUY" else q.quantize(D("0.01"), rounding="ROUND_FLOOR")
    residual = D(0) if side == "BUY" else q - shares
    maker, taker = (q, shares) if side == "BUY" else (shares, shares * p)
    env = dict(strategy_name="golden-guava", schema="guava-signed-envelope-v1", order_type="FOK",
               event_id=event, condition_id=condition, token_id=token, decision_id="d-" + sid,
               side=side, requested_quantity=str(q), requested_limit_price=str(p), limit_price=str(p),
               tick_size="0.01", maker_amount=str(int(maker * 1000000)), taker_amount=str(int(taker * 1000000)),
               signed_shares=str(shares), sell_residual_shares=str(residual),
               fee_preflight={}, book_timestamp="1788670000000")
    digest = hashlib.sha256(json.dumps(env, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    complete = complete or zero
    status = status or ("INTENT" if no_post else "CANCELED" if zero else "MATCHED" if complete else "DELAYED")
    fills = []
    if actual is not None:
        fills = [dict(trade_id="trade-" + sid, bucket_index=0, status=trade_status, side=side,
                      size=str(actual), price=fill_price or price, liquidity_role="TAKER",
                      fee_amount_usdc=fee, transaction_hash="tx-" + sid, domain_error=None)]
    confirmed = actual is not None and trade_status == "CONFIRMED"
    actual_q = D(actual) if confirmed else D(0)
    gross = actual_q * D(fill_price or price)
    fee_known = (confirmed and fee is not None) or zero
    partial = complete and actual_q < shares - D("0.0001") and not zero
    unknown = not (no_post or (complete and fee_known))
    reservation = dict(submission_id=sid, event_id=event, condition_id=condition, token_id=token,
                       decision_id=env["decision_id"], side=side, envelope_sha256=digest,
                       buy_notional_cap_usdc=str(q) if side == "BUY" else None,
                       sell_shares_cap=str(shares) if side == "SELL" else None, signed_shares=str(shares),
                       no_post_proven=no_post, reservation_required=not (no_post or zero),
                       runner_must_account=True, portfolio_approval=False, fee_budget_included=False,
                       fee_status="KNOWN" if fee_known else "UNKNOWN", execution_complete=complete,
                       event_or_token_blocked=unknown)
    return dict(submission_id=sid, order_id=None if no_post else "order-" + sid, status=status,
                phase="NO_POST" if no_post else "RECONCILED" if complete else "POST_RETURNED",
                envelope=env, envelope_sha256=digest, reservation=reservation,
                signed_maker_amount=env["maker_amount"], signed_taker_amount=env["taker_amount"],
                signed_shares=str(shares), sell_residual_shares=str(residual), no_post=no_post,
                no_post_proven=no_post, zero_fill_proven=zero, submission_outcome_unknown=False,
                confirmed_shares=str(actual_q) if confirmed or zero else None,
                confirmed_notional_usdc=str(gross) if confirmed or zero else None,
                confirmed_vwap=str(gross / actual_q) if actual_q else None,
                fee_usdc=str(D(fee)) if fee_known and not zero else "0" if zero else None,
                fee_status=reservation["fee_status"], fills=fills, execution_complete=complete,
                partial_terminal=partial, remaining_order_shares=str(max(D(0), shares - actual_q)) if complete else None,
                exposure_unknown=not no_post and not complete, new_risk_allowed=not unknown,
                risk_permission_scope="EVENT_OR_TOKEN_ONLY_RUNNER_MUST_ACCOUNT_ALL_RESERVATIONS")


def reduce(rows, links=None, **kwargs):
    return reduce_positions(rows, sell_to_buy=links or {}, expected_submission_ids={r["submission_id"] for r in rows},
                            inventory_complete=True, limits=kwargs.pop("limits", LIMITS), **kwargs)


def bought(sid="b1", **kwargs):
    return snapshot(sid, actual="10", complete=True, **kwargs)


def dust_pair(index=1):
    kw = dict(event=f"e{index}", condition=f"c{index}", token=f"t{index}")
    return (snapshot(f"b{index}", actual="10.005", fill_price="0.49975", complete=True, **kw),
            snapshot(f"s{index}", "SELL", quantity="10.005", actual="10", complete=True, **kw))


def test_complete_buy_one_slot_no_duplicate_reservation():
    row = bought()
    state = reduce([row, deepcopy(row)])
    assert state.active_position_count == state.open_economic_count == 1
    assert state.notional_usdc == 5
    assert state.holdings[("e1", "c1", "t1")] == 10
    assert state.capacity("e2", "t2").fits(D(5))


def test_unknown_matched_and_fee_gap_keep_cap_and_local_block():
    for row in (snapshot(), snapshot(actual="4", trade_status="MINED"),
                snapshot(actual="4", complete=False), snapshot(actual="4", complete=True, status="CANCELED", fee=None)):
        state = reduce([row])
        assert state.notional_usdc == 5 and state.active_position_count == 1
        assert not state.capacity("e1", "other-token").fits(D(5))
        assert not state.capacity("other-event", "t1").fits(D(5))
        assert state.capacity("e2", "t2").fits(D(5))
        assert state.lots[0].fee_usdc is None
    assert reduce([row]).lots[0].owned_shares == 4  # fees unknown != ownership unknown


def test_terminal_partial_buy_uses_actual_owned_cost():
    state = reduce([snapshot(actual="4", complete=True, status="CANCELED")])
    assert state.lots[0].owned_shares == 4
    assert state.notional_usdc == 2 and state.active_position_count == 1


def test_pending_sell_reduces_only_confirmed_and_reserves_remaining():
    buy = bought()
    sell = snapshot("s1", "SELL", quantity="6", actual="2", status="LIVE", fee=None)
    state = reduce([buy, sell], {"s1": "b1"})
    lot = state.lots[0]
    assert lot.owned_shares == 8 and lot.sell_reserved_shares == 4
    assert state.notional_usdc == 5 and state.active_position_count == 1
    assert state.capacity("e2", "t2").fits(D(5))


def test_terminal_partial_sell_releases_quantity_even_when_fee_unknown():
    sell = snapshot("s1", "SELL", quantity="10", actual="4", complete=True, status="CANCELED")
    state = reduce([bought(), sell], {"s1": "b1"})
    assert state.lots[0].owned_shares == 6 and state.lots[0].sell_reserved_shares == 0
    assert state.notional_usdc == 3
    sell = snapshot("s1", "SELL", quantity="10", actual="4", complete=True, status="CANCELED", fee=None)
    state = reduce([bought(), sell], {"s1": "b1"})
    assert state.lots[0].sell_reserved_shares == 0 and state.notional_usdc == 5
    assert state.lots[0].protected_sell_available_shares == 6
    assert state.lots[0].fee_usdc is None and state.active_position_count == 1
    assert not state.capacity("e1", "t1").fits(D(5))
    assert state.capacity("e2", "t2").fits(D(5))


def test_fee_unknown_terminal_sell_does_not_block_later_protective_quantity():
    first = snapshot("s1", "SELL", quantity="10", actual="4", complete=True, status="CANCELED", fee=None)
    protective = snapshot("s2", "SELL", quantity="6")
    state = reduce([bought(), first, protective], {"s1": "b1", "s2": "b1"})
    assert state.lots[0].owned_shares == 6 and state.lots[0].sell_reserved_shares == 6
    assert state.lots[0].protected_sell_available_shares == 0 and state.notional_usdc == 5
    nonterminal = snapshot("s1", "SELL", quantity="10", actual="4", status="LIVE", fee=None)
    with pytest.raises(PositionStateError, match="overSELL"):
        reduce([bought(), nonterminal, protective], {"s1": "b1", "s2": "b1"})


@pytest.mark.parametrize("px", ["0.50000004", "0.5000001"])
@pytest.mark.parametrize("complete", [False, True])
def test_one_micro_buy_tolerance_preserves_observed_notional_and_rounds_cost_up(px, complete):
    row = snapshot(actual="10", fill_price=px, complete=complete)
    state = reduce([row], limits=replace(LIMITS, max_notional_usdc=D(10)), cycle_buy_ids={"b1"})
    lot = state.lots[0]
    assert lot.observed_buy_notional_usdc == D(10) * D(px)
    assert lot.buy_rounding_excess_usdc == D(10) * D(px) - D(5)
    assert lot.remaining_cost_usdc == (D("5.000001") if complete else None)
    assert state.notional_usdc == state.cycle_notional_usdc == D("5.000001")
    assert state.capacity("e2", "t2").notional_usdc == D("4.99")
    with pytest.raises(PositionStateError, match="tolerance"):
        reduce([snapshot(actual="10", fill_price="0.500000101", complete=True)])


def test_complete_sell_closes_without_fake_pnl_or_capacity_credit():
    state = reduce([bought(), snapshot("s1", "SELL", quantity="10", actual="10", complete=True)], {"s1": "b1"}, cycle_buy_ids={"b1"})
    assert state.active_position_count == state.open_economic_count == 0
    assert state.lots[0].label == "CLOSED" and state.notional_usdc == 0
    assert state.cycle_positions == 1 and state.cycle_notional_usdc == 5
    assert not hasattr(state, "pnl")


def test_dust_exact_terminal_sell_retains_cost_but_not_slot():
    buy, sell = dust_pair()
    state = reduce([sell, buy], {"s1": "b1"})
    lot = state.lots[0]
    assert lot.label == "DUST" and lot.owned_shares == D("0.005")
    assert lot.remaining_cost_usdc > 0 and state.notional_usdc > 0
    assert state.dust_count == state.open_economic_count == 1
    assert state.active_position_count == 0


def test_twenty_closed_and_twenty_dust_do_not_freeze_new_entries():
    rows, links = [], {}
    for i in range(1, 41):
        if i <= 20:
            buy, sell = dust_pair(i)
        else:
            kw = dict(event=f"e{i}", condition=f"c{i}", token=f"t{i}")
            buy, sell = bought(f"b{i}", **kw), snapshot(f"s{i}", "SELL", quantity="10", actual="10", complete=True, **kw)
        rows.extend([buy, sell]); links[f"s{i}"] = f"b{i}"
    state = reduce(rows, links)
    assert state.active_position_count == 0 and state.dust_count == state.open_economic_count == 20
    assert D(0) < state.notional_usdc < D(1)
    assert state.capacity("fresh", "fresh-token").fits(D(5))


def test_small_position_without_exact_end_sell_is_not_dust():
    state = reduce([snapshot(actual="0.005", complete=True, status="CANCELED")])
    assert state.lots[0].label == "HOLDING" and state.active_position_count == 1
    buy, sell = dust_pair()
    sell["envelope"]["sell_residual_shares"] = "0.006"
    with pytest.raises(PositionStateError):
        reduce([buy, sell], {"s1": "b1"})


@pytest.mark.parametrize("gap", ["buy-fee", "sell-fee", "sell-pending"])
def test_uncertain_dust_sized_remainder_keeps_slot_and_full_cap(gap):
    buy, sell = dust_pair()
    if gap == "buy-fee":
        buy = snapshot(actual="10.005", fill_price="0.49975", complete=True, fee=None)
    else:
        sell = snapshot("s1", "SELL", quantity="10.005", actual="10", complete=gap != "sell-pending", fee=None)
    state = reduce([buy, sell], {"s1": "b1"})
    assert state.lots[0].owned_shares == D("0.005")
    assert state.active_position_count == 1 and state.dust_count == 0 and state.notional_usdc == 5


def test_180_minutes_changes_only_label_never_economic_state():
    row = snapshot()
    before = reduce([row], unresolved_age_minutes={"b1": D("179.999")})
    after = reduce([row], unresolved_age_minutes={"b1": D(180)})
    assert before.lots[0].label == "PENDING" and after.lots[0].label == "QUARANTINED"
    assert before.notional_usdc == after.notional_usdc == 5
    assert before.active_position_count == after.active_position_count == 1
    assert after.open_economic_count == 1 and after.lots[0].owned_shares is None


@pytest.mark.parametrize("links", [{}, {"s1": "missing"}, {"s1": "s1"}, {"s1": "b1", "other": "b1"}])
def test_explicit_sell_parent_required_and_unambiguous(links):
    with pytest.raises(PositionStateError):
        reduce([bought(), snapshot("s1", "SELL", quantity="10")], links)


def test_over_sell_and_concurrent_over_reservation_are_global_errors():
    cases = [[snapshot("s1", "SELL", quantity="11")],
             [snapshot("s1", "SELL", quantity="6"), snapshot("s2", "SELL", quantity="6")],
             [snapshot("s1", "SELL", quantity="6", actual="6", complete=True), snapshot("s2", "SELL", quantity="5")]]
    for sells in cases:
        with pytest.raises(PositionStateError, match="overSELL"):
            reduce([bought(), *sells], {s["submission_id"]: "b1" for s in sells})


@pytest.mark.parametrize("change", [{"event": "wrong"}, {"condition": "wrong"}, {"token": "wrong"}])
def test_sell_parent_identity_must_match_all_three_dimensions(change):
    with pytest.raises(PositionStateError):
        reduce([bought(), snapshot("s1", "SELL", quantity="10", **change)], {"s1": "b1"})


def test_completeness_orphan_reservation_only_and_conflicting_duplicate_fail_closed():
    row = bought()
    with pytest.raises(PositionStateError):
        reduce_positions([row], sell_to_buy={}, expected_submission_ids={"b1", "lost"}, inventory_complete=True, limits=LIMITS)
    with pytest.raises(PositionStateError):
        reduce_positions([row], sell_to_buy={}, expected_submission_ids={"b1"}, inventory_complete=False, limits=LIMITS)
    with pytest.raises(PositionStateError):
        reduce_positions([row["reservation"]], sell_to_buy={}, expected_submission_ids={"b1"}, inventory_complete=True, limits=LIMITS)
    with pytest.raises(PositionStateError):
        reduce([row, snapshot("b1")])
    with pytest.raises(PositionStateError):
        reduce_positions([], sell_to_buy={}, expected_submission_ids={"orphan"}, inventory_complete=True, limits=LIMITS)


def test_limits_apply_total_event_cycle_and_order_without_float_math():
    limits = replace(LIMITS, max_notional_usdc=D("9.99"))
    state = reduce([bought()], limits=limits)
    assert state.capacity("e2", "t2").notional_usdc == D("4.99")
    assert not state.capacity("e2", "t2").fits(D(5))
    state = reduce([bought()], cycle_buy_ids={"b1"}, limits=replace(LIMITS, max_cycle_positions=1))
    assert not state.capacity("e2", "t2").fits(D(5))
    assert reduce([bought()], limits=replace(LIMITS, max_positions=1)).capacity("e2", "t2").position_slots == 0


def test_input_unchanged_and_decimal_context_independent():
    rows = list(dust_pair()); saved = deepcopy(rows)
    with localcontext() as ctx:
        ctx.prec = 6
        a = reduce(rows, {"s1": "b1"})
    with localcontext() as ctx:
        ctx.prec = 50
        b = reduce(rows, {"s1": "b1"})
    assert a == b and rows == saved


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "-1"])
def test_invalid_finite_caps_and_limits_fail_closed(value):
    row = bought(); row["envelope"]["maker_amount"] = value
    with pytest.raises(PositionStateError):
        reduce([row])
    with pytest.raises(PositionStateError):
        replace(LIMITS, max_notional_usdc=value)


def test_proven_no_post_and_zero_fill_release_but_unproven_absence_does_not():
    for row in (snapshot(no_post=True), snapshot(zero=True)):
        state = reduce([row])
        assert state.notional_usdc == 0 and state.active_position_count == 0
    row = snapshot(no_post=True)
    row.update(no_post_proven=False, new_risk_allowed=False)
    row["reservation"].update(no_post_proven=False, reservation_required=True, event_or_token_blocked=True)
    assert reduce([row]).notional_usdc == 5


def rehash(row):
    digest = hashlib.sha256(json.dumps(row["envelope"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    row["envelope_sha256"] = row["reservation"]["envelope_sha256"] = digest


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_unrelated_extra_metadata_is_not_a_contract_break(side):
    row = bought() if side == "BUY" else snapshot("s1", "SELL", quantity="10", actual="10", complete=True)
    row["envelope"]["signer_tick_size"] = "0.01"
    rehash(row)
    state = reduce([row]) if side == "BUY" else reduce([bought(), row], {"s1": "b1"})
    assert state.holdings[("e1", "c1", "t1")] == (10 if side == "BUY" else 0)


BUY_METADATA = dict(buy_amount_contract="guava-decimal-buy-v1", native_tick="0.01", signer_tick="0.01",
                    buy_quantity_precision=4, minimum_taker_amount="10000000")


@pytest.mark.parametrize("selected", [keys for n in range(1, 5) for keys in combinations(BUY_METADATA, n)])
def test_every_nonempty_partial_buy_metadata_contract_is_rejected(selected):
    row = bought()
    row["envelope"].update({k: BUY_METADATA[k] for k in selected})
    rehash(row)
    with pytest.raises(PositionStateError, match="BUY.*metadata"):
        reduce([row])


@pytest.mark.parametrize("selected", [*( (k,) for k in BUY_METADATA), tuple(BUY_METADATA)])
def test_sell_cannot_carry_any_buy_arithmetic_fields(selected):
    sell = snapshot("s1", "SELL", quantity="10")
    sell["envelope"].update({k: BUY_METADATA[k] for k in selected})
    rehash(sell)
    with pytest.raises(PositionStateError, match="SELL.*BUY"):
        reduce([bought(), sell], {"s1": "b1"})


def test_import_and_reduction_need_no_broker_sdk_db_network_or_clock():
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('py_clob', 'eth_', 'sqlite3', 'requests', 'httpx', 'socket', 'polybot.execution')):
        raise AssertionError('forbidden reducer dependency')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from decimal import Decimal as D
from polybot.position_state import Limits, reduce_positions
limits = Limits(20,D(100),1,D(5),1,D(5),D(5))
state = reduce_positions([],sell_to_buy={},expected_submission_ids=set(),inventory_complete=True,limits=limits)
assert state.capacity('event','token').fits(D(5))
"""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-B", "-c", script], env=dict(os.environ, PYTHONPATH=str(root / "src")), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_missing_transaction_does_not_reduce_owned_shares():
    sell = snapshot("s1", "SELL", quantity="6", actual="2", status="LIVE")
    sell["fills"][0]["transaction_hash"] = None
    state = reduce([bought(), sell], {"s1": "b1"})
    assert state.lots[0].owned_shares == 10
    assert state.lots[0].sell_reserved_shares == 6 and state.notional_usdc == 5


def test_multiple_partial_then_exact_end_sell_can_prove_dust():
    buy, _ = dust_pair()
    first = snapshot("s1", "SELL", quantity="10.005", actual="4", complete=True, status="CANCELED")
    final = snapshot("s2", "SELL", quantity="6.005", actual="6", complete=True)
    state = reduce([final, first, buy], {"s1": "b1", "s2": "b1"})
    assert state.dust_count == 1 and state.active_position_count == 0
    assert state.lots[0].owned_shares == D("0.005")


def test_ambiguous_end_sell_or_large_residual_does_not_release_slot():
    buy, _ = dust_pair()
    first = snapshot("s1", "SELL", quantity="4.005", actual="4", complete=True)
    second = snapshot("s2", "SELL", quantity="6.005", actual="6", complete=True)
    state = reduce([buy, first, second], {"s1": "b1", "s2": "b1"})
    assert state.dust_count == 0 and state.active_position_count == 1
    buy = snapshot(actual="10.02", fill_price="0.499", complete=True)
    sell = snapshot("s1", "SELL", quantity="10.02", actual="10", complete=True, status="CANCELED")
    state = reduce([buy, sell], {"s1": "b1"})
    assert state.lots[0].owned_shares == D("0.02") and state.notional_usdc > 0
    assert state.active_position_count == 1 and state.dust_count == 0


def test_unknown_fee_after_all_shares_sold_still_reserves_full_cap():
    sell = snapshot("s1", "SELL", quantity="10", actual="10", complete=True, fee=None)
    state = reduce([bought(), sell], {"s1": "b1"}, unresolved_age_minutes={"s1": D(180)})
    assert state.lots[0].owned_shares == 0 and state.lots[0].fee_usdc is None
    assert state.lots[0].label == "QUARANTINED"
    assert state.active_position_count == state.open_economic_count == 1 and state.notional_usdc == 5


def test_unreconciled_buy_cannot_authorize_sell_ownership():
    with pytest.raises(PositionStateError, match="reconciled owned BUY"):
        reduce([snapshot(actual="4"), snapshot("s1", "SELL", quantity="4")], {"s1": "b1"})


def test_event_and_order_caps_and_dust_cost_still_bind():
    state = reduce([bought()], limits=replace(LIMITS, max_event_positions=1))
    assert not state.capacity("e1", "other-token").fits(D(5))
    assert state.capacity("e2", "t2").notional_usdc == 5
    assert not state.capacity("e2", "t2").fits(D(6))
    buy, sell = dust_pair()
    state = reduce([buy, sell], {"s1": "b1"}, limits=replace(LIMITS, max_notional_usdc=D("5.001")))
    assert state.active_position_count == 0
    assert not state.capacity("e2", "t2").fits(D(5))


def test_same_key_multiple_buys_are_distinct_lots_not_ambiguous_sell_parents():
    rows = [bought("b1"), bought("b2"), snapshot("s1", "SELL", quantity="10", actual="10", complete=True)]
    state = reduce(rows, {"s1": "b1"})
    assert state.holdings[("e1", "c1", "t1")] == 10
    assert state.active_position_count == 1 and state.notional_usdc == 5


@pytest.mark.parametrize("mutate", [lambda row: row["fills"].append(deepcopy(row["fills"][0])),
                                   lambda row: row.update(confirmed_shares="9"),
                                   lambda row: row.update(fee_usdc=None),
                                   lambda row: row["reservation"].update(buy_notional_cap_usdc="0"),
                                   lambda row: row.update(execution_complete="true"),
                                   lambda row: row.pop("envelope")])
def test_ui_summaries_or_missing_evidence_never_create_successful_state(mutate):
    row = bought(); mutate(row)
    with pytest.raises(PositionStateError):
        reduce([row])


def test_invalid_age_and_cycle_inventory_are_not_silently_ignored():
    for kwargs in ({"unresolved_age_minutes": []}, {"unresolved_age_minutes": {"b1": True}},
                   {"unresolved_age_minutes": {"other": 180}}, {"cycle_buy_ids": {"missing"}}):
        with pytest.raises(PositionStateError):
            reduce([bought()], **kwargs)


@pytest.mark.parametrize("price,tick,precision", [("0.3", "0.1", 3), ("0.29", "0.01", 4), ("0.965", "0.001", 4)])
def test_current_decimal_buy_contract_and_historical_precision_coexist(price, tick, precision):
    row = snapshot(price=price)
    shares = (D(5) / D(price)).quantize(D(1).scaleb(-precision), rounding="ROUND_FLOOR")
    amount = str(int(shares * 1000000))
    row["envelope"].update(tick_size=tick, buy_amount_contract="guava-decimal-buy-v1", native_tick=tick,
                           signer_tick=tick, buy_quantity_precision=precision, minimum_taker_amount=amount,
                           signed_shares=str(shares), taker_amount=amount)
    row.update(signed_shares=str(shares), signed_taker_amount=amount)
    row["reservation"]["signed_shares"] = str(shares)
    digest = hashlib.sha256(json.dumps(row["envelope"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    row["envelope_sha256"] = row["reservation"]["envelope_sha256"] = digest
    assert reduce([row]).notional_usdc == 5
    assert reduce([bought()]).notional_usdc == 5


@pytest.fixture
def real_broker(tmp_path, monkeypatch):
    import socket
    from test_execution import FakeSDK
    from polybot.execution import Broker, BUY_AMOUNT_FIELDS
    from polybot.budget import Budget
    from polybot_observability import ExecutionLedger
    assert BUY_AMOUNT_FIELDS == set(BUY_METADATA)
    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden in broker/reducer integration")
    for obj, name in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
        monkeypatch.setattr(obj, name, no_network)
    path = tmp_path / "trades.db"
    ledger, sdk = ExecutionLedger(path, strategy_name="golden-guava"), FakeSDK()
    broker = Broker(ledger, path, Budget(), sdk_client=sdk)
    try:
        yield broker, sdk
    finally:
        broker.close()


def inventory_state(broker, sdk, expected_ids, links):
    # Fail if the inventory path authenticates/calls the fake SDK. Do NOT rebuild
    # amounts, hashes, proofs or snapshot fields in a second fixture generator.
    before = (len(sdk.posts), len(sdk.signs), len(sdk.cancels), sdk.trade_calls)
    sdk.auth_failure = True
    try:
        rows = broker.execution_inventory()
    finally:
        sdk.auth_failure = False
    state = reduce_positions(rows, sell_to_buy=links, expected_submission_ids=expected_ids,
                             inventory_complete=True, limits=LIMITS, cycle_buy_ids=())
    assert before == (len(sdk.posts), len(sdk.signs), len(sdk.cancels), sdk.trade_calls)
    return state


@pytest.mark.parametrize("case", ["closed", "dust", "terminal_fee_gap"])
def test_real_broker_inventory_to_reducer_owned_sell_lifecycle(real_broker, case):
    from test_execution import buy, confirmed, CONTEXT
    broker, sdk = real_broker
    entry = buy(broker); bid = entry["submission_id"]
    pending = inventory_state(broker, sdk, {bid}, {})
    assert pending.notional_usdc == 5 and pending.lots[0].owned_shares is None
    actual = "10.005" if case == "dust" else "10"
    sdk.orders[entry["order_id"]]["size_matched"] = actual
    trade = confirmed(sdk, entry["order_id"], size=actual)
    if case == "dust":
        trade["price"] = "0.49975"  # actual exchange fixture, not signed-amount arithmetic
    assert broker.reconcile([bid])[bid]["execution_complete"]
    owned = inventory_state(broker, sdk, {bid}, {})
    assert owned.lots[0].owned_shares == D(actual) and owned.active_position_count == 1
    exit_ = broker.sell_fok("token-1", actual, "0.5", dict(CONTEXT, decision_id="sell-1"))
    sid, oid = exit_["submission_id"], exit_["order_id"]
    size = "4" if case == "terminal_fee_gap" else "10"
    sdk.orders[oid].update(size_matched=size, status="CANCELED" if case == "terminal_fee_gap" else "MATCHED")
    confirmed(sdk, oid, size=size, fee=None if case == "terminal_fee_gap" else "150000")
    assert broker.reconcile([sid])[sid]["execution_complete"]
    state = inventory_state(broker, sdk, {bid, sid}, {sid: bid})
    lot = state.lots[0]
    if case == "closed":
        assert lot.label == "CLOSED" and state.active_position_count == state.open_economic_count == 0
    elif case == "dust":
        assert lot.owned_shares == D("0.005") and lot.remaining_cost_usdc > 0
        assert state.dust_count == state.open_economic_count == 1 and state.active_position_count == 0
    else:
        assert lot.fee_usdc is None and lot.notional_usdc == 5 and lot.sell_reserved_shares == 0
        assert lot.protected_sell_available_shares == 6 and state.active_position_count == 1
    assert len(sdk.posts) == len(sdk.signs) == 2


def test_real_broker_unknown_post_inventory_is_reserved_and_event_local(real_broker):
    from test_execution import buy, buy_event_b
    broker, sdk = real_broker
    sdk.callback = lambda signed: (_ for _ in ()).throw(TimeoutError())
    first = buy(broker)
    assert first["submission_outcome_unknown"]
    sdk.callback = None
    second = buy_event_b(broker, sdk)
    ids = {first["submission_id"], second["submission_id"]}
    state = inventory_state(broker, sdk, ids, {})
    assert state.notional_usdc == 10 and state.active_position_count == 2
    assert not state.capacity("event-1", "fresh-token").fits(D(5))
    assert state.capacity("event-3", "token-3").fits(D(5))
    assert buy(broker)["duplicate"] and len(sdk.posts) == 2
    with pytest.raises(PositionStateError, match="missing inventory"):
        inventory_state(broker, sdk, ids | {"missing-parent"}, {})
