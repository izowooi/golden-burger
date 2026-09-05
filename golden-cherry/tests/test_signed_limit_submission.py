"""New Cherry orders must retain the real signed amount envelope."""

import json
import logging
import sqlite3
from types import SimpleNamespace

import pytest

from polybot.api.clob_client import ClobClientWrapper
from polybot.config import ApiConfig
from polybot_observability import SubmissionEvidenceError


SIGNATURE_SENTINEL = "NEVER_PERSIST_OR_LOG_THIS_SIGNATURE"


class SDK:
    def __init__(self, path, *, making="6130000", taking="6068700", reply=None):
        self.path = path
        self.signed = SimpleNamespace(
            makerAmount=making,
            takerAmount=taking,
            signature=SIGNATURE_SENTINEL,
        )
        self.reply = reply or {
            "success": True,
            "orderID": "new-order",
            "status": "delayed",
            "makingAmount": None,
            "takingAmount": None,
        }
        self.sign_count = 0
        self.post_count = 0

    def create_order(self, _args):
        self.sign_count += 1
        return self.signed

    def post_order(self, signed, _order_type):
        assert signed is self.signed
        # A separate connection must already see the committed intent at POST.
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT order_id,response_status FROM order_submissions"
            ).fetchall()
        assert rows == [(None, "INTENT")]
        self.post_count += 1
        return self.reply

    def cancel_orders(self, ids):
        return {"canceled": ids, "not_canceled": {}}


def wrapper(tmp_path, **sdk_options):
    path = tmp_path / "execution.db"
    client = ClobClientWrapper(
        ApiConfig(private_key="", funder_address=""),
        audit_db_path=path,
        strategy_name="golden-cherry",
    )
    sdk = SDK(path, **sdk_options)
    client._client = sdk
    client._initialized = True
    return client, sdk, path


@pytest.mark.parametrize(
    "side,price,size,making,taking,expected_making,expected_taking",
    [
        ("SELL", 0.99, 6.13, "6130000", "6068700", 6.13, 6.0687),
        ("BUY", 0.82, 6.134969325, "5026600", "6130000", 5.0266, 6.13),
    ],
)
def test_delayed_response_keeps_signed_amounts_atomically(
    tmp_path,
    caplog,
    side,
    price,
    size,
    making,
    taking,
    expected_making,
    expected_taking,
):
    client, sdk, path = wrapper(tmp_path, making=making, taking=taking)
    caplog.set_level(logging.INFO)

    result = client.place_limit_order("test-token", price, size, side)

    assert result["success"] and result["requested_size"] == pytest.approx(6.13)
    assert sdk.post_count == 1
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT order_id,response_status,requested_size,making_amount,taking_amount "
            "FROM order_submissions"
        ).fetchone()
    assert row[:2] == ("new-order", "DELAYED")
    assert row[2:] == pytest.approx((6.13, expected_making, expected_taking))
    assert SIGNATURE_SENTINEL not in caplog.text
    assert SIGNATURE_SENTINEL.encode() not in path.read_bytes()


@pytest.mark.parametrize("field", ["makerAmount", "takerAmount"])
@pytest.mark.parametrize(
    "bad", [None, "", "NaN", "6.13", -1, 0, True, 1.5, 2**256, SIGNATURE_SENTINEL]
)
def test_missing_or_invalid_signed_amount_stops_before_intent_and_post(
    tmp_path, caplog, field, bad
):
    client, sdk, path = wrapper(tmp_path)
    setattr(sdk.signed, field, bad)

    result = client.place_limit_order("test-token", 0.99, 6.13, "SELL")

    assert result["success"] is False
    assert sdk.post_count == 0
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM order_submissions").fetchone()[0]
            == 0
        )
    assert SIGNATURE_SENTINEL not in caplog.text
    assert SIGNATURE_SENTINEL not in result["error"]


@pytest.mark.parametrize(
    "making,taking",
    [("6200000", "6138000"), ("6000000", "5940000"), ("6130000", "7000000")],
)
def test_signed_quantity_or_price_drift_is_not_submitted(tmp_path, making, taking):
    client, sdk, _path = wrapper(tmp_path, making=making, taking=taking)
    assert (
        client.place_limit_order("test-token", 0.99, 6.13, "SELL")["success"] is False
    )
    assert sdk.post_count == 0


def test_no_live_post_without_execution_ledger(tmp_path):
    client, sdk, _path = wrapper(tmp_path)
    client.execution_ledger = None
    with pytest.raises(SubmissionEvidenceError, match="durable execution ledger"):
        client.place_limit_order("test-token", 0.99, 6.13, "SELL")
    assert sdk.sign_count == sdk.post_count == 0


def test_intent_write_failure_cannot_reach_post(tmp_path, monkeypatch):
    client, sdk, _path = wrapper(tmp_path)

    def fail_intent(**_kwargs):
        raise RuntimeError("intent persistence unavailable")

    monkeypatch.setattr(client.execution_ledger, "record_intent", fail_intent)
    assert (
        client.place_limit_order("test-token", 0.99, 6.13, "SELL")["success"] is False
    )
    assert sdk.post_count == 0


def test_simulation_does_not_require_or_create_signed_payload(tmp_path):
    client, sdk, _path = wrapper(tmp_path)
    client.simulation_mode = True
    client.execution_ledger = None
    assert client.place_limit_order("test-token", 0.99, 6.13, "SELL")["simulated"]
    assert sdk.sign_count == sdk.post_count == 0


def test_uncertain_post_remains_unknown_without_retry_or_false_release(tmp_path):
    from requests.exceptions import ReadTimeout

    client, sdk, path = wrapper(tmp_path)

    def uncertain_post(_signed, _order_type):
        sdk.post_count += 1
        raise ReadTimeout("POST delivery uncertain")

    sdk.post_order = uncertain_post
    result = client.place_limit_order("test-token", 0.99, 6.13, "SELL")
    assert result["submission_outcome_unknown"] is True
    assert sdk.post_count == 1
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT order_id,response_status,outcome_resolution FROM order_submissions"
        ).fetchone()
    assert row == (None, "SUBMIT_OUTCOME_UNKNOWN", None)


def test_new_sell_can_finish_from_exact_trades_when_order_detail_is_gone(tmp_path):
    client, _sdk, path = wrapper(tmp_path)
    client.place_limit_order("test-token", 0.99, 6.13, "SELL")
    with sqlite3.connect(path) as connection:
        submission_id = connection.execute(
            "SELECT submission_id FROM order_submissions WHERE order_id='new-order'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE order_submissions SET associated_trade_ids_json=?, "
            "reconciliation_proof='AUTHENTICATED_TOKEN_TRADE_CATALOG_EXACT_IDS' "
            "WHERE submission_id=?",
            (json.dumps(["exact-fill"]), submission_id),
        )
        connection.execute(
            "INSERT INTO order_fills(submission_id,order_id,trade_id,bucket_index,status,side,size,price,liquidity_role,fee_rate_bps,fee_amount_usdc) "
            "VALUES (?, 'new-order','exact-fill',0,'CONFIRMED','SELL',6.13,0.99,'TAKER',0,0)",
            (submission_id,),
        )

    assert client.execution_ledger.finish_reconciliation(submission_id) is True
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT needs_reconciliation,reconciliation_proof FROM order_submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
    assert row == (0, "AUTHENTICATED_TOKEN_TRADE_CATALOG_FULL_FILL")


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("size", [6.134969325, 9.02, 9248.5549])
def test_current_sdk_limit_rounding_is_accepted_and_preserved(tmp_path, side, size):
    from py_clob_client_v2.order_builder.builder import OrderBuilder, ROUNDING_CONFIG

    _side, making, taking = OrderBuilder(None).get_order_amounts(
        side, size, 0.82, ROUNDING_CONFIG["0.01"]
    )
    client, sdk, path = wrapper(tmp_path, making=str(making), taking=str(taking))
    result = client.place_limit_order("test-token", 0.82, size, side)
    assert result["success"] and sdk.post_count == 1
    expected_size = (taking if side == "BUY" else making) / 1_000_000
    assert result["requested_size"] == expected_size
    with sqlite3.connect(path) as connection:
        actual = connection.execute(
            "SELECT making_amount,taking_amount FROM order_submissions"
        ).fetchone()
    assert actual == pytest.approx((making / 1_000_000, taking / 1_000_000))
