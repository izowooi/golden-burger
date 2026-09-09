import importlib.util
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("s_price", Path(__file__).parents[1]/"sports_price_normalization.py")
N = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(N)


def fixture():
    tokens, observations = [], []
    for role, ask, bid in [("HOME", ".99", ".98"), ("DRAW", ".02", ".01"), ("AWAY", ".02", ".01")]:
        for side in ("YES", "NO"):
            tokens.append({"token_id": role+side, "condition_id": role, "result_kind": role,
                           "outcome_side": side, "label": role+side})
        observations.append({"token_id": role+"YES", "t": 1000, "run_id": "r", "valid": True,
                             "market_open": True, "fee_market": {"feesEnabled": False},
                             "book": {"asset_id": role+"YES", "market": role,
                                      "asks": [{"price": ask, "size": "100"}],
                                      "bids": [{"price": bid, "size": "100"}]}})
    return tokens, observations


def test_example_is_normalized_quote_and_six_tokens_are_not_summed():
    tokens, observations = fixture()
    out = N.summarize_group(tokens, observations, "soccer")
    assert out["valid"] and out["signal_eligible"]
    assert Decimal(out["s_ask"]) == Decimal("1.03")
    assert Decimal(out["s_bid"]) == 1
    assert Decimal(out["s_mid"]) == Decimal("1.015")
    assert float(out["normalized"]["HOMEYES"]["ask"]) == pytest.approx(.99/1.03)
    assert float(out["no_reference"]["HOMENO"]["ask"]) == pytest.approx(1-.99/1.03)
    assert len(out["members"]) == 3
    assert Decimal(out["ask_excess_from_mid"]) == Decimal(".015")
    assert Decimal(out["ask_excess_from_spread"]) == Decimal(".015")
    assert Decimal(out["s_best_ask"]) - 1 == (Decimal(out["ask_excess_from_mid"])
                                             + Decimal(out["ask_excess_from_spread"]))


def test_same_share_bundle_differs_from_top_ask_and_individual_notional_walks():
    tokens, observations = fixture()
    observations[0]["book"]["asks"] = [{"price": ".90", "size": "1"}, {"price": ".99", "size": "10"}]
    observations[0]["book"]["bids"] = [{"price": ".89", "size": "100"}]
    out = N.summarize_group(tokens, observations, "soccer", common_shares=5)
    assert Decimal(out["s_best_ask"]) == Decimal(".94")
    assert Decimal(out["s_ask"]) == Decimal("1.012")


@pytest.mark.parametrize("change", ["missing", "duplicate", "skew", "run", "event", "invalid", "wrong_token", "crossed"])
def test_bad_denominator_evidence_is_not_normalized(change):
    tokens, observations = fixture()
    if change == "missing": observations.pop()
    elif change == "duplicate": observations.append(deepcopy(observations[0]))
    elif change == "skew": observations[0]["t"] = 1003
    elif change == "run": observations[0]["run_id"] = "other"
    elif change == "event": observations[0]["event_id"] = "other"
    elif change == "invalid": observations[0]["valid"] = False
    elif change == "wrong_token": observations[0]["book"]["asset_id"] = "other"
    else: observations[0]["book"]["bids"][0]["price"] = "1"
    out = N.summarize_group(tokens, observations, "soccer")
    assert not out["valid"] and not out["signal_eligible"] and out["s_ask"] is None


def test_missing_bid_or_depth_does_not_become_zero():
    tokens, observations = fixture()
    observations[0]["book"]["bids"] = []
    observations[1]["book"]["asks"][0]["size"] = "1"
    out = N.summarize_group(tokens, observations, "soccer")
    assert out["valid"] and not out["signal_eligible"]
    assert out["s_bid"] is None and out["s_mid"] is None and out["s_ask"] is None
    assert Decimal(out["s_best_ask"]) == Decimal("1.03")


def test_unknown_fees_or_open_status_are_not_zero_or_tradeable():
    tokens, observations = fixture()
    observations[0]["fee_market"] = {}
    observations[0]["market_open"] = None
    out = N.summarize_group(tokens, observations, "soccer")
    assert out["s_ask_with_fees"] is None and out["s_bid_after_fees"] is None
    assert out["market_open_unknown"] and not out["signal_eligible"]


def test_two_team_and_partition_without_venue_ordering():
    tokens, observations = fixture()
    for t in tokens:
        t["partition_role"] = {"HOME": "TEAM_A", "DRAW": "DRAW", "AWAY": "TEAM_B"}[t["result_kind"]]
        t["result_kind"] = None
    assert N.summarize_group(tokens, observations, "soccer")["valid"]
    tokens = [{"token_id": "a", "condition_id": "c", "outcome_side": "DIRECT", "label": "A"},
              {"token_id": "b", "condition_id": "c", "outcome_side": "DIRECT", "label": "B"}]
    observations = [{"token_id": t["token_id"], "t": 1000, "run_id": "r", "valid": True,
                     "market_open": True, "book": {"asks": [{"price": p, "size": 10}],
                                                     "bids": [{"price": str(Decimal(p)-Decimal('.01')), "size": 10}]}}
                    for t, p in zip(tokens, (".6", ".42"))]
    out = N.summarize_group(tokens, observations, "mlb")
    assert out["valid"] and Decimal(out["s_ask"]) == Decimal("1.02")
    assert len(out["normalized"]) == 2 and out["no_reference"] == {}


def bbo_fixture():
    tokens, observations = fixture()
    for o in observations:
        book = o["book"]
        o["book"] = {"asset_id": book["asset_id"], "market": book["market"],
                     "best_ask": book["asks"][0]["price"], "best_bid": book["bids"][0]["price"]}
    return tokens, observations


def test_bbo_prices_normalize_without_fabricating_size_or_cost():
    tokens, observations = bbo_fixture()
    out = N.summarize_group(tokens, observations, "soccer", bbo_only=True)
    assert out["valid"] and Decimal(out["s_best_ask"]) == Decimal("1.03")
    assert out["quote_depth_basis"] == "BBO_ONLY_NO_SIZE"
    assert not out["signal_eligible"] and not out["cost_evidence_eligible"]
    assert not out["fee_evidence_complete"]
    for field in ("common_shares", "s_ask", "s_bid", "s_ask_with_fees", "s_bid_after_fees"):
        assert out[field] is None
    assert out["normalized"]["HOMEYES"]["depth_ask"] is None


@pytest.mark.parametrize("problem", ["token", "crossed", "levels", "not_finite"])
def test_bbo_does_not_bypass_identity_or_price_validation(problem):
    tokens, observations = bbo_fixture()
    book = observations[0]["book"]
    if problem == "token": book["asset_id"] = "wrong"
    elif problem == "crossed": book["best_bid"] = "1"
    elif problem == "levels": book["asks"] = [{"price": ".99", "size": 100}]
    else: book["best_ask"] = "NaN"
    out = N.summarize_group(tokens, observations, "soccer", bbo_only=True)
    assert not out["valid"] and out["s_best_ask"] is None


def test_bbo_missing_bid_stays_unknown_and_default_mode_rejects_bbo():
    tokens, observations = bbo_fixture()
    observations[0]["book"]["best_bid"] = None
    out = N.summarize_group(tokens, observations, "soccer", bbo_only=True)
    assert out["valid"] and out["s_mid"] is None and out["s_best_bid"] is None
    assert Decimal(out["s_best_ask"]) == Decimal("1.03")
    assert not N.summarize_group(tokens, observations, "soccer")["valid"]
