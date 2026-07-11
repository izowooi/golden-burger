from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import pytest

from polybot_observability import (
    ClobResponseContractError,
    ClobResponseUnavailableError,
    ExecutionLedger,
    SubmissionEvidenceError,
    UnresolvedSubmissionOutcomeError,
    normalize_clob_response,
    normalize_clob_response_list,
    safe_clob_response_shape,
)


@dataclass
class TypedSubmission:
    success: bool
    order_id: str
    status: str
    making_amount: str
    taking_amount: str
    api_secret: str = "must-not-be-copied"


class PydanticMakerOrder:
    def model_dump(self):
        return {
            "orderId": "typed-order",
            "matchedAmount": "10000000",
            "price": "0.42",
            "side": "BUY",
            "feeRateBps": "0",
            "private_key": "must-not-be-copied",
        }


class ToDictTrade:
    def to_dict(self):
        return {
            "tradeId": "typed-trade",
            "status": "TRADE_STATUS_CONFIRMED",
            "makerOrders": [PydanticMakerOrder()],
            "traderSide": "MAKER",
            "bucketIndex": 0,
            "matchTime": "1700000000",
            "transactionHash": "0xtyped",
            "api_secret": "must-not-be-copied",
        }


class AttributeOrder:
    __slots__ = (
        "status",
        "originalSize",
        "sizeMatched",
        "price",
        "associatedTrades",
        "password",
    )

    def __init__(self):
        self.status = "ORDER_STATUS_MATCHED"
        self.originalSize = "10000000"
        self.sizeMatched = "10000000"
        self.price = "0.42"
        self.associatedTrades = [AttributeTradeId("typed-trade")]
        self.password = "must-not-be-copied"


@dataclass
class AttributeTradeId:
    trade_id: str


class PydanticTradePage:
    def model_dump(self):
        return [ToDictTrade()]


class PydanticV1Root:
    def __init__(self, root):
        self.__root__ = root

    def dict(self):
        return {"__root__": self.__root__}


def test_records_submission_status_and_confirmed_fill(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token-yes",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={
            "success": True,
            "orderID": "order-1",
            "status": "live",
            "makingAmount": "4200000",
            "takingAmount": "10000000",
        },
        simulation=False,
    )

    pending = ledger.pending_submissions()
    assert pending[0]["submission_id"] == submission_id
    trade_ids = ledger.record_order_status(
        submission_id,
        {
            "status": "ORDER_STATUS_MATCHED",
            "original_size": "10000000",
            "size_matched": "10000000",
            "price": "0.42",
            "associate_trades": ["trade-1"],
        },
    )
    assert trade_ids == ["trade-1"]
    ledger.record_fill(
        submission_id,
        "order-1",
        {
            "id": "trade-1",
            "status": "CONFIRMED",
            "side": "BUY",
            "size": "10000000",
            "price": "0.419",
            "fee_rate_bps": "50",
            "fee_amount_usdc": "10000",
            "taker_order_id": "order-1",
            "match_time": "1700000000",
        },
    )
    assert ledger.finish_reconciliation(submission_id) is True
    assert ledger.pending_submissions() == []

    with sqlite3.connect(db_path) as connection:
        fill = connection.execute(
            "SELECT status, size, price, liquidity_role, fee_rate_bps, "
            "fee_amount_usdc, matched_at FROM order_fills"
        ).fetchone()
        amounts = connection.execute(
            "SELECT making_amount, taking_amount FROM order_submissions"
        ).fetchone()
    assert fill == (
        "CONFIRMED", 10.0, 0.419, "TAKER", 50.0, 0.01,
        "1700000000",
    )
    assert amounts == (4.2, 10.0)


def test_sdk_normalized_human_quantity_is_not_divided_by_fixed_scale_again(
    tmp_path,
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "human-units", "status": "live"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED",
            "original_size": "10",
            "size_matched": "10",
            "price": "0.42",
            "associate_trades": ["human-trade"],
        },
    )
    ledger.record_fill(
        submission_id,
        "human-units",
        {
            "id": "human-trade",
            "status": "CONFIRMED",
            "size": "10",
            "price": "0.42",
            "taker_order_id": "human-units",
            "trader_side": "TAKER",
            "fee_rate_bps": "0",
        },
    )

    assert ledger.finish_reconciliation(submission_id) is True
    with sqlite3.connect(db_path) as connection:
        submission = connection.execute(
            "SELECT latest_size_matched, quantity_scale, needs_reconciliation "
            "FROM order_submissions"
        ).fetchone()
        fill = connection.execute("SELECT size FROM order_fills").fetchone()
    assert submission == (10.0, 1.0, 0)
    assert fill == (10.0,)


@pytest.mark.parametrize(
    ("fill_is_scaled", "expected_mode"),
    [
        (True, "ORDER_AND_FILL_X1000000"),
        (False, "ORDER_ONLY_X1000000"),
    ],
)
def test_operator_repairs_proven_double_scaled_terminal_fill_set(
    tmp_path, fill_is_scaled, expected_mode
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "double-scaled", "status": "live"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED",
            "original_size": "10",
            "size_matched": "10",
            "price": "0.42",
            "associate_trades": ["scaled-trade"],
        },
    )
    ledger.record_fill(
        submission_id,
        "double-scaled",
        {
            "id": "scaled-trade",
            "status": "CONFIRMED",
            "size": "10",
            "price": "0.42",
            "taker_order_id": "double-scaled",
            "trader_side": "TAKER",
            "fee_rate_bps": "0",
        },
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_submissions SET latest_size_matched = "
            "latest_size_matched / 1000000, quantity_scale = NULL"
        )
        connection.execute(
            "UPDATE order_status_events SET original_size = original_size / 1000000, "
            "size_matched = size_matched / 1000000"
        )
        if fill_is_scaled:
            connection.execute("UPDATE order_fills SET size = size / 1000000")
        connection.execute(
            "UPDATE order_fills SET domain_error = 'quantity_scale_missing'"
        )

    diagnostics = ledger.quantity_scale_diagnostics()
    assert len(diagnostics) == 1
    assert diagnostics[0]["repair_eligible"] is True
    assert diagnostics[0]["rejection_reasons"] == []
    assert diagnostics[0]["repair_mode"] == expected_mode
    assert diagnostics[0]["fill_domain_errors"] == ["quantity_scale_missing"]

    candidates = ledger.quantity_scale_repair_candidates()
    assert len(candidates) == 1
    assert candidates[0]["submission_id"] == submission_id
    assert candidates[0]["repair_mode"] == expected_mode
    with pytest.raises(ValueError, match="REPAIR_1_CLOB_QUANTITIES_X1000000"):
        ledger.repair_quantity_scale(
            expected_count=1,
            confirmation="wrong",
            reason="runtime scale mismatch reviewed",
        )

    result = ledger.repair_quantity_scale(
        expected_count=1,
        confirmation="REPAIR_1_CLOB_QUANTITIES_X1000000",
        reason="runtime scale mismatch reviewed",
    )

    assert result == {"repaired": 1, "completed": 1, "pending": 0}
    with sqlite3.connect(db_path) as connection:
        submission = connection.execute(
            "SELECT latest_size_matched, quantity_scale, needs_reconciliation "
            "FROM order_submissions"
        ).fetchone()
        fill = connection.execute(
            "SELECT size, domain_error FROM order_fills"
        ).fetchone()
        repair = connection.execute(
            "SELECT multiplier, reason, before_json, after_json "
            "FROM quantity_scale_repairs"
        ).fetchone()
    assert submission == (10.0, 1.0, 0)
    assert fill == (10.0, None)
    assert repair[0:2] == (1000000.0, "runtime scale mismatch reviewed")
    assert json.loads(repair[2])["submission"][0] == pytest.approx(0.00001)
    assert json.loads(repair[3])["submission"][0] == pytest.approx(10.0)


def test_quantity_scale_repair_rejects_unrelated_fill_domain_error(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token-yes",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "unsafe-scale", "status": "live"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED",
            "original_size": "10",
            "size_matched": "10",
            "price": "0.42",
            "associate_trades": ["unsafe-trade"],
        },
    )
    ledger.record_fill(
        submission_id,
        "unsafe-scale",
        {
            "id": "unsafe-trade",
            "status": "CONFIRMED",
            "size": "10",
            "price": "0.42",
            "taker_order_id": "unsafe-scale",
            "trader_side": "TAKER",
        },
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_submissions SET latest_size_matched = "
            "latest_size_matched / 1000000, quantity_scale = NULL"
        )
        connection.execute(
            "UPDATE order_fills SET size = size / 1000000, "
            "domain_error = 'quantity_scale_missing,confirmed_price_invalid'"
        )

    diagnostics = ledger.quantity_scale_diagnostics()
    assert diagnostics[0]["repair_eligible"] is False
    assert diagnostics[0]["rejection_reasons"] == [
        "unsupported_fill_domain_errors_present"
    ]
    assert ledger.quantity_scale_repair_candidates() == []


def test_typed_sdk_models_are_normalized_without_copying_unknown_fields(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token-yes",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result=TypedSubmission(
            success=True,
            order_id="typed-order",
            status="live",
            making_amount="4200000",
            taking_amount="10000000",
        ),
        simulation=False,
    )

    assert ledger.record_order_status(submission_id, AttributeOrder()) == [
        "typed-trade"
    ]
    ledger.record_fill(
        submission_id,
        "typed-order",
        normalize_clob_response_list(
            PydanticTradePage(), response_type="trade"
        )[0],
    )

    assert ledger.finish_reconciliation(submission_id) is True
    normalized = normalize_clob_response(
        TypedSubmission(True, "typed-order", "live", "4200000", "10000000"),
        response_type="submission",
    )
    assert normalized == {
        "success": True,
        "orderID": "typed-order",
        "status": "live",
        "makingAmount": "4200000",
        "takingAmount": "10000000",
    }
    assert "api_secret" not in normalized
    with sqlite3.connect(db_path) as connection:
        fill = connection.execute(
            "SELECT trade_id, status, size, price, liquidity_role, "
            "transaction_hash FROM order_fills"
        ).fetchone()
    assert fill == (
        "typed-trade",
        "CONFIRMED",
        10.0,
        0.42,
        "MAKER",
        "0xtyped",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "order-shape", "status": "MATCHED"},
        {"order": {"id": "order-shape", "status": "MATCHED"}},
        {"data": {"id": "order-shape", "status": "MATCHED"}},
        {"result": [{"id": "order-shape", "status": "MATCHED"}]},
        [{"id": "order-shape", "status": "MATCHED"}],
        json.dumps(
            {"data": [{"id": "order-shape", "status": "MATCHED"}]}
        ),
        PydanticV1Root([{"id": "order-shape", "status": "MATCHED"}]),
    ],
)
def test_order_response_shapes_are_unwrapped_with_minimum_contract(payload):
    normalized = normalize_clob_response(payload, response_type="order")
    assert normalized["id"] == "order-shape"
    assert normalized["status"] == "MATCHED"


@pytest.mark.parametrize(
    "payload",
    [None, [], "not-json", {}, {"data": []}, {"unknown": "secret-value"}],
)
def test_unavailable_order_shapes_fail_explicitly(payload):
    with pytest.raises(ClobResponseUnavailableError):
        normalize_clob_response(payload, response_type="order")


def test_ambiguous_or_incomplete_response_contracts_are_rejected():
    with pytest.raises(ClobResponseContractError, match="2건"):
        normalize_clob_response(
            [{"status": "LIVE"}, {"status": "MATCHED"}],
            response_type="order",
        )
    with pytest.raises(ClobResponseContractError, match="status"):
        normalize_clob_response({"id": "missing-status"}, response_type="order")
    with pytest.raises(ClobResponseContractError, match="id"):
        normalize_clob_response({"status": "CONFIRMED"}, response_type="trade")
    with pytest.raises(ClobResponseUnavailableError):
        normalize_clob_response({}, response_type="cancellation")
    assert normalize_clob_response({}, response_type="submission") == {}


def test_v1_root_trade_collection_and_safe_shape_do_not_expose_values():
    page = PydanticV1Root(
        [{"id": "trade-root", "status": "CONFIRMED", "api_secret": "hidden"}]
    )
    assert normalize_clob_response_list(page, response_type="trade") == [
        {"id": "trade-root", "status": "CONFIRMED"}
    ]
    shape = safe_clob_response_shape(
        {"data": [{"status": "MATCHED"}], "api_secret": "do-not-log"}
    )
    assert shape == "mapping(count=2,known=data)"
    assert "secret" not in shape
    assert "MATCHED" not in shape


def test_unreadable_post_response_is_persisted_as_unknown_outcome(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")

    with pytest.raises(SubmissionEvidenceError, match="representation"):
        ledger.submit_and_record(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=1,
            submit=lambda: object(),
        )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT response_status, success, needs_reconciliation "
            "FROM order_submissions"
        ).fetchone()
    assert row == ("SUBMIT_OUTCOME_UNKNOWN", 0, 0)
    with pytest.raises(UnresolvedSubmissionOutcomeError):
        ExecutionLedger(db_path, strategy_name="golden-test").assert_execution_ready()


def test_unreadable_post_unknown_write_failure_stays_wrapped_and_blocks_restart(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    monkeypatch.setattr(
        ledger,
        "record_submission_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("disk unavailable")
        ),
    )

    with pytest.raises(SubmissionEvidenceError, match="기록하지 못했습니다") as captured:
        ledger.submit_and_record(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=1,
            submit=lambda: object(),
        )
    assert isinstance(captured.value.__cause__, sqlite3.OperationalError)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT response_status FROM order_submissions"
        ).fetchone() == ("INTENT",)
    with pytest.raises(UnresolvedSubmissionOutcomeError):
        ExecutionLedger(db_path, strategy_name="golden-test").assert_execution_ready()


def test_simulated_submission_never_requires_reconciliation(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    ledger.record_submission(
        token_id="token",
        side="SELL",
        requested_price=0.8,
        requested_size=5,
        result={"success": True, "orderID": "SIM_SELL"},
        simulation=True,
    )
    assert ledger.pending_submissions() == []


def test_bootstraps_recent_legacy_order_ids(tmp_path):
    db_path = tmp_path / "trades.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                token_id TEXT, buy_order_id TEXT, buy_price REAL,
                buy_shares REAL, buy_timestamp TEXT, sell_order_id TEXT,
                sell_price REAL, sell_shares REAL, sell_timestamp TEXT,
                status TEXT
            );
            INSERT INTO trades VALUES (
                'token', 'legacy-buy', 0.4, 10, datetime('now'),
                NULL, NULL, NULL, NULL, 'HOLDING'
            );
            """
        )

    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    pending = ledger.pending_submissions()

    assert len(pending) == 1
    assert pending[0]["order_id"] == "legacy-buy"
    assert pending[0]["response_status"] == "LEGACY_ASSUMED"

    ledger.mark_legacy_unavailable(pending[0]["submission_id"])
    assert ledger.pending_submissions() == []
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT response_status, needs_reconciliation, reconciliation_error "
            "FROM order_submissions"
        ).fetchone()
    assert row == (
        "LEGACY_UNAVAILABLE",
        0,
        "legacy order unavailable; fill evidence gap remains",
    )


def test_does_not_close_matched_order_until_every_trade_is_terminal(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=10,
        result={"success": True, "orderID": "order-multi"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED",
            "size_matched": "10000000",
            "associate_trades": ["trade-one", "trade-two"],
        },
    )
    ledger.record_fill(
        submission_id,
        "order-multi",
        {
            "id": "trade-one",
            "status": "CONFIRMED",
            "size": "5000000",
            "price": "0.4",
            "taker_order_id": "order-multi",
            "fee_rate_bps": 0,
            "fee_amount_usdc": 0,
        },
    )

    assert ledger.finish_reconciliation(submission_id) is False
    assert len(ledger.pending_submissions()) == 1


def test_canceled_partial_or_unknown_order_stays_pending(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    for order_id, matched_size in (("partial", "2000000"), ("unknown", None)):
        submission_id = ledger.record_submission(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=10,
            result={"success": True, "orderID": order_id},
            simulation=False,
        )
        ledger.record_order_status(
            submission_id,
            {"status": "CANCELED", "size_matched": matched_size},
        )
        assert ledger.finish_reconciliation(submission_id) is False


def test_canceled_explicitly_unfilled_order_can_close(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="SELL",
        requested_price=0.8,
        requested_size=5,
        result={"success": True, "orderID": "unfilled"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {"status": "CANCELED", "size_matched": 0},
    )

    assert ledger.finish_reconciliation(submission_id) is True
    assert ledger.pending_submissions() == []


def test_bucketed_fixed_math_fills_must_cover_matched_size(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="SELL",
        requested_price=0.6,
        requested_size=10,
        result={"success": True, "orderID": "bucketed"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "ORDER_STATUS_MATCHED",
            "original_size": "10000000",
            "size_matched": "10000000",
            "associate_trades": ["logical-trade"],
        },
    )
    ledger.record_fill(
        submission_id,
        "bucketed",
        {
            "id": "logical-trade",
            "bucket_index": 0,
            "status": "TRADE_STATUS_CONFIRMED",
            "size": "5000000",
            "price": "0.6",
            "taker_order_id": "bucketed",
            "fee_rate_bps": "0",
        },
    )
    assert ledger.finish_reconciliation(submission_id) is False

    ledger.record_fill(
        submission_id,
        "bucketed",
        {
            "id": "logical-trade",
            "bucket_index": 1,
            "status": "TRADE_STATUS_CONFIRMED",
            "size": "5000000",
            "price": "0.6",
            "taker_order_id": "bucketed",
            "fee_rate_bps": "0",
        },
    )

    assert ledger.finish_reconciliation(submission_id) is True
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT bucket_index, size FROM order_fills ORDER BY bucket_index"
        ).fetchall()
    assert rows == [(0, 5.0), (1, 5.0)]


def test_sparse_fill_update_does_not_erase_execution_fields(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=5,
        result={"success": True, "orderID": "sparse"},
        simulation=False,
    )
    ledger.record_fill(
        submission_id,
        "sparse",
        {
            "id": "trade-sparse",
            "status": "CONFIRMED",
            "size": "5000000",
            "price": "0.4",
            "taker_order_id": "overfilled",
            "fee_rate_bps": "0",
            "trader_side": "TAKER",
            "match_time": "1700000000",
        },
    )
    ledger.record_fill(
        submission_id,
        "sparse",
        {"id": "trade-sparse", "status": "CONFIRMED"},
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT size, price, liquidity_role, fee_rate_bps, matched_at "
            "FROM order_fills"
        ).fetchone()
    assert row == (5.0, 0.4, "TAKER", 0.0, "1700000000")


def test_sdk_request_exception_is_preserved_as_unknown_outcome(tmp_path):
    class PolyApiException(Exception):
        status_code = None

    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")

    with pytest.raises(SubmissionEvidenceError) as captured:
        ledger.submit_and_record(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=5,
            submit=lambda: (_ for _ in ()).throw(
                PolyApiException("Request exception!")
            ),
        )
    assert isinstance(captured.value.__cause__, PolyApiException)

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT response_status, success, needs_reconciliation "
            "FROM order_submissions"
        ).fetchone()
    assert row == ("SUBMIT_OUTCOME_UNKNOWN", 0, 0)

    restarted = ExecutionLedger(db_path, strategy_name="golden-test")
    unresolved = restarted.unresolved_submission_outcomes()
    assert len(unresolved) == 1
    assert unresolved[0]["response_status"] == "SUBMIT_OUTCOME_UNKNOWN"
    with pytest.raises(UnresolvedSubmissionOutcomeError) as gate:
        restarted.pending_submissions()
    assert gate.value.count == 1
    with pytest.raises(UnresolvedSubmissionOutcomeError):
        restarted.assert_execution_ready()

    restarted.resolve_uncertain_submission(
        unresolved[0]["submission_id"],
        resolution="NO_ORDER_CREATED",
        reason="venue support confirmed no order",
    )
    restarted.assert_execution_ready()
    assert restarted.pending_submissions() == []
    with sqlite3.connect(db_path) as connection:
        resolved = connection.execute(
            "SELECT response_status, outcome_resolution, outcome_resolved_at, "
            "outcome_resolution_reason FROM order_submissions"
        ).fetchone()
    assert resolved[0] == "SUBMIT_OUTCOME_UNKNOWN"
    assert resolved[1] == "NO_ORDER_CREATED"
    assert resolved[2]
    assert resolved[3] == "venue support confirmed no order"


def test_operator_can_link_discovered_order_id_for_normal_reconciliation(tmp_path):
    class ConnectTimeout(Exception):
        pass

    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    with pytest.raises(SubmissionEvidenceError):
        ledger.submit_and_record(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=5,
            submit=lambda: (_ for _ in ()).throw(ConnectTimeout("timeout")),
        )
    submission_id = ledger.unresolved_submission_outcomes()[0]["submission_id"]

    with pytest.raises(ValueError, match="order_id"):
        ledger.resolve_uncertain_submission(
            submission_id,
            resolution="ORDER_ID_LINKED",
            reason="found in venue history",
        )
    synthetic_github_token = "ghp_" + "Q" * 36
    ledger.resolve_uncertain_submission(
        submission_id,
        resolution="ORDER_ID_LINKED",
        reason=f"api_key=do-not-store proof={synthetic_github_token}",
        order_id="venue-order-1",
    )

    restarted = ExecutionLedger(db_path, strategy_name="golden-test")
    restarted.assert_execution_ready()
    assert restarted.pending_submissions()[0]["order_id"] == "venue-order-1"
    with pytest.raises(ValueError, match="이미"):
        restarted.resolve_uncertain_submission(
            submission_id,
            resolution="NO_ORDER_CREATED",
            reason="attempted overwrite",
        )
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT response_status, outcome_resolution, "
            "outcome_resolution_reason, success, needs_reconciliation "
            "FROM order_submissions"
        ).fetchone()
    assert row == (
        "SUBMIT_OUTCOME_UNKNOWN",
        "ORDER_ID_LINKED",
        "api_key=<redacted> proof=<redacted-secret>",
        1,
        1,
    )


def test_crashed_intent_and_unlinked_evidence_failure_block_restart(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    live_intent = ledger.record_intent(
        token_id="live", side="BUY", requested_price=0.4,
        requested_size=1, simulation=False
    )
    ledger.record_intent(
        token_id="sim", side="BUY", requested_price=0.4,
        requested_size=1, simulation=True
    )
    evidence_intent = ledger.record_intent(
        token_id="evidence", side="SELL", requested_price=0.6,
        requested_size=1, simulation=False
    )
    ledger.mark_evidence_write_failure(
        evidence_intent, order_id="", error=OSError("disk")
    )

    restarted = ExecutionLedger(db_path, strategy_name="golden-test")
    unresolved = restarted.unresolved_submission_outcomes()
    assert {row["submission_id"] for row in unresolved} == {
        live_intent,
        evidence_intent,
    }
    with pytest.raises(UnresolvedSubmissionOutcomeError) as gate:
        restarted.pending_submissions()
    assert gate.value.count == 2


def test_reconciliation_queue_rotates_past_erroring_old_orders(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    for index in range(3):
        ledger.record_submission(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=5,
            result={"success": True, "orderID": f"order-{index}"},
            simulation=False,
        )

    first_batch = ledger.pending_submissions(limit=2)
    assert [row["order_id"] for row in first_batch] == ["order-0", "order-1"]
    for row in first_batch:
        ledger.record_reconciliation_error(
            row["submission_id"], RuntimeError("temporary")
        )

    second_batch = ledger.pending_submissions(limit=2)
    assert second_batch[0]["order_id"] == "order-2"


def test_operator_quarantines_only_exact_catalog_missing_gap_set(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_ids = []
    for index in range(2):
        submission_id = ledger.record_submission(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=5,
            result={"success": True, "orderID": f"missing-{index}"},
            simulation=False,
        )
        ledger.record_reconciliation_error(
            submission_id,
            RuntimeError(
                "phase=match_authoritative_order_catalogs "
                "error=ClobResponseUnavailableError "
                "response_shape=sequence(len=0,item_type=none)"
            ),
        )
        submission_ids.append(submission_id)

    assert {
        row["submission_id"] for row in ledger.catalog_missing_submissions()
    } == set(submission_ids)
    with pytest.raises(ValueError, match="확인 문구"):
        ledger.resolve_catalog_missing_submissions(
            expected_count=2,
            confirmation="wrong",
            reason="catalog proof reviewed",
        )
    with pytest.raises(RuntimeError, match="건수가 예상과 다릅니다"):
        ledger.resolve_catalog_missing_submissions(
            expected_count=1,
            confirmation="ACKNOWLEDGE_1_CLOB_EVIDENCE_GAPS",
            reason="catalog proof reviewed",
        )

    resolved = ledger.resolve_catalog_missing_submissions(
        expected_count=2,
        confirmation="ACKNOWLEDGE_2_CLOB_EVIDENCE_GAPS",
        reason="api_key=must-not-persist catalog proof reviewed",
    )

    assert resolved == 2
    assert ledger.catalog_missing_submissions() == []
    assert ledger.pending_submissions() == []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT response_status, needs_reconciliation, outcome_resolution, "
            "outcome_resolution_reason, reconciliation_error "
            "FROM order_submissions ORDER BY order_id"
        ).fetchall()
    assert rows == [
        (
            "OPERATOR_EVIDENCE_GAP",
            0,
            "EVIDENCE_GAP_ACCEPTED",
            "api_key=<redacted> catalog proof reviewed",
            "operator accepted catalog-missing CLOB fill evidence gap",
        ),
        (
            "OPERATOR_EVIDENCE_GAP",
            0,
            "EVIDENCE_GAP_ACCEPTED",
            "api_key=<redacted> catalog proof reviewed",
            "operator accepted catalog-missing CLOB fill evidence gap",
        ),
    ]


def test_catalog_gap_operator_path_excludes_matched_or_trade_linked_orders(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    for result in (
        {"success": True, "orderID": "matched", "status": "matched"},
        {
            "success": True,
            "orderID": "trade-linked",
            "status": "live",
            "tradeIDs": ["trade-1"],
        },
    ):
        submission_id = ledger.record_submission(
            token_id="token",
            side="BUY",
            requested_price=0.4,
            requested_size=5,
            result=result,
            simulation=False,
        )
        ledger.record_reconciliation_error(
            submission_id,
            RuntimeError(
                "phase=match_authoritative_order_catalogs "
                "error=ClobResponseUnavailableError"
            ),
        )

    assert ledger.catalog_missing_submissions() == []
    linked = ledger.catalog_missing_submissions(include_evidence_linked=True)
    assert {row["order_id"] for row in linked} == {"matched", "trade-linked"}
    assert {row["fill_count"] for row in linked} == {0}
    with pytest.raises(ValueError, match="WITH_LINKED_EVIDENCE"):
        ledger.resolve_catalog_missing_submissions(
            expected_count=2,
            confirmation="ACKNOWLEDGE_2_CLOB_EVIDENCE_GAPS",
            reason="linked evidence reviewed",
            include_evidence_linked=True,
        )

    assert ledger.resolve_catalog_missing_submissions(
        expected_count=2,
        confirmation=(
            "ACKNOWLEDGE_2_CLOB_EVIDENCE_GAPS_WITH_LINKED_EVIDENCE"
        ),
        reason="linked evidence reviewed",
        include_evidence_linked=True,
    ) == 2
    with sqlite3.connect(ledger.db_path) as connection:
        rows = connection.execute(
            "SELECT response_status, outcome_resolution, reconciliation_error "
            "FROM order_submissions ORDER BY order_id"
        ).fetchall()
    assert rows == [
        (
            "OPERATOR_EVIDENCE_GAP",
            "EVIDENCE_GAP_WITH_LINKED_EVIDENCE_ACCEPTED",
            "operator accepted catalog-missing CLOB fill evidence gap "
            "with linked evidence",
        ),
        (
            "OPERATOR_EVIDENCE_GAP",
            "EVIDENCE_GAP_WITH_LINKED_EVIDENCE_ACCEPTED",
            "operator accepted catalog-missing CLOB fill evidence gap "
            "with linked evidence",
        ),
    ]


def test_reconciliation_error_path_redacts_bare_and_dsn_credentials(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token", side="BUY", requested_price=0.4, requested_size=1,
        result={"success": True, "orderID": "order-redaction"}, simulation=False,
    )
    synthetic_openai = "sk-svcacct-" + "R" * 28
    synthetic_dsn = "mongodb://fake_user:fake_password@db.invalid/fake"
    ledger.record_reconciliation_error(
        submission_id,
        RuntimeError(f"key={synthetic_openai} connection={synthetic_dsn}"),
    )

    with sqlite3.connect(db_path) as connection:
        stored = connection.execute(
            "SELECT reconciliation_error FROM order_submissions"
        ).fetchone()[0]
    assert synthetic_openai not in stored
    assert synthetic_dsn not in stored
    assert "<redacted-secret>" in stored
    assert "<redacted-dsn>" in stored


def test_confirmed_fill_overflow_never_finishes_reconciliation(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=10,
        result={"success": True, "orderID": "overfilled"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED",
            "size_matched": "10000000",
            "associate_trades": ["fill-over"],
        },
    )
    ledger.record_fill(
        submission_id,
        "overfilled",
        {
            "id": "fill-over",
            "status": "CONFIRMED",
            "size": "10000002",
            "price": "0.4",
            "taker_order_id": "overfilled",
            "fee_rate_bps": 0,
        },
    )

    assert ledger.finish_reconciliation(submission_id) is False
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT needs_reconciliation, reconciliation_error "
            "FROM order_submissions WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
    assert row == (1, "confirmed fill quantity exceeds latest_size_matched")


@pytest.mark.parametrize(
    "fills",
    [
        [
            {"id": "missing", "size": None, "bucket_index": 0},
            {"id": "valid", "size": "10000000", "bucket_index": 0},
        ],
        [
            {"id": "offset", "size": "-5000000", "bucket_index": 0},
            {"id": "offset", "size": "15000000", "bucket_index": 1},
        ],
    ],
)
def test_invalid_confirmed_size_domains_cannot_cancel_out_to_matched(
    tmp_path, fills
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token", side="BUY", requested_price=0.4,
        requested_size=10, result={"success": True, "orderID": "domain"},
        simulation=False,
    )
    trade_ids = list(dict.fromkeys(fill["id"] for fill in fills))
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED", "original_size": "10000000",
            "size_matched": "10000000", "associate_trades": trade_ids,
        },
    )
    for fill in fills:
        payload = {
            "status": "CONFIRMED", "price": "0.4",
            "taker_order_id": "domain", "fee_rate_bps": 0,
            **fill,
        }
        ledger.record_fill(submission_id, "domain", payload)

    assert ledger.finish_reconciliation(submission_id) is False
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT reconciliation_error FROM order_submissions"
        ).fetchone()
    assert row == ("confirmed fill domain invalid",)


def test_invalid_fill_correlation_bucket_and_fee_are_persisted_and_blocking(
    tmp_path,
):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token", side="BUY", requested_price=0.4,
        requested_size=10, result={"success": True, "orderID": "our-order"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED", "size_matched": "10000000",
            "associate_trades": ["bad-correlation"],
        },
    )
    ledger.record_fill(
        submission_id,
        "our-order",
        {
            "id": "bad-correlation", "status": "CONFIRMED",
            "trader_side": "MAKER",
            "maker_orders": [
                {"order_id": "someone-else", "matched_amount": "10000000", "price": "0.4"}
            ],
            "size": "10000000", "price": "0.4", "bucket_index": -1,
            "fee_rate_bps": -1, "fee_amount_usdc": "-1",
        },
    )

    assert ledger.finish_reconciliation(submission_id) is False
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT bucket_index, liquidity_role, domain_error FROM order_fills"
        ).fetchone()
    assert row[0] == -1
    assert row[1] == "UNKNOWN"
    assert "order_fill_correlation_invalid" in row[2]
    assert "bucket_index_invalid" in row[2]
    assert "fee_rate_invalid" in row[2]
    assert "fee_amount_invalid" in row[2]


def test_submission_and_order_detail_numeric_domains_fail_closed(tmp_path):
    ledger = ExecutionLedger(tmp_path / "trades.db", strategy_name="golden-test")
    for price, size in ((float("inf"), 1), (0.4, 0), (1.0, 1)):
        with pytest.raises(ValueError):
            ledger.record_intent(
                token_id="token", side="BUY", requested_price=price,
                requested_size=size, simulation=False,
            )

    submission_id = ledger.record_submission(
        token_id="token", side="BUY", requested_price=0.4,
        requested_size=10, result={"success": True, "orderID": "bad-status"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED", "original_size": "5000000",
            "size_matched": "10000000", "associate_trades": ["fill"],
        },
    )
    assert ledger.finish_reconciliation(submission_id) is False
    with sqlite3.connect(ledger.db_path) as connection:
        row = connection.execute(
            "SELECT latest_status_domain_error, reconciliation_error "
            "FROM order_submissions"
        ).fetchone()
    assert "size_matched_exceeds_original" in row[0]
    assert row[1] == "order/submission domain invalid"


@pytest.mark.parametrize(
    "result",
    [
        {"success": True, "status": "accepted"},
        {"success": False, "orderID": "contradictory", "status": "rejected"},
    ],
)
def test_live_response_contract_anomalies_fail_and_block_restart(tmp_path, result):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    with pytest.raises(SubmissionEvidenceError):
        ledger.record_submission(
            token_id="token", side="BUY", requested_price=0.4,
            requested_size=1, result=result, simulation=False,
        )

    restarted = ExecutionLedger(db_path, strategy_name="golden-test")
    with pytest.raises(UnresolvedSubmissionOutcomeError):
        restarted.assert_execution_ready()


def test_submit_and_record_preserves_unknown_response_and_cancels_known_id(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    canceled = []
    with pytest.raises(SubmissionEvidenceError):
        ledger.submit_and_record(
            token_id="token", side="BUY", requested_price=0.4, requested_size=1,
            submit=lambda: {"success": False, "orderID": "contradictory"},
            cancel=lambda order_id: canceled.append(order_id) or {
                "canceled": [order_id], "not_canceled": {}
            },
        )
    assert canceled == ["contradictory"]
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT response_status, order_id, outcome_resolution "
            "FROM order_submissions"
        ).fetchone()
    assert row == ("SUBMIT_OUTCOME_UNKNOWN", "contradictory", None)
    with pytest.raises(UnresolvedSubmissionOutcomeError):
        ExecutionLedger(db_path, strategy_name="golden-test").assert_execution_ready()


def _replace_fill_table_with_legacy_primary_key(db_path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            DROP INDEX IF EXISTS order_fills_order_idx;
            ALTER TABLE order_fills RENAME TO order_fills_current;
            CREATE TABLE order_fills (
                submission_id TEXT NOT NULL REFERENCES order_submissions(submission_id),
                order_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                bucket_index INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                side TEXT,
                size REAL,
                price REAL,
                liquidity_role TEXT,
                fee_rate_bps REAL,
                fee_amount_usdc REAL,
                matched_at TEXT,
                last_update TEXT,
                transaction_hash TEXT,
                domain_error TEXT,
                PRIMARY KEY(submission_id, trade_id)
            );
            INSERT INTO order_fills SELECT * FROM order_fills_current;
            DROP TABLE order_fills_current;
            """
        )


def test_order_fill_migration_rolls_back_mid_copy_and_retries(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=1,
        result={"success": True, "orderID": "legacy-order"},
        simulation=True,
    )
    ledger.record_fill(
        submission_id,
        "legacy-order",
        {
            "id": "legacy-fill", "status": "CONFIRMED", "size": "1000000",
            "price": "0.4", "taker_order_id": "legacy-order",
        },
    )
    _replace_fill_table_with_legacy_primary_key(db_path)

    def fail_after_copy(stage: str) -> None:
        assert stage == "after_order_fills_copy"
        raise RuntimeError("injected migration failure")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(RuntimeError, match="injected migration failure"):
            ExecutionLedger._ensure_schema(
                connection, migration_hook=fail_after_copy
            )
        assert connection.in_transaction is False
        primary_key = [
            row[1]
            for row in sorted(
                connection.execute("PRAGMA table_info(order_fills)"),
                key=lambda value: value[5],
            )
            if row[5]
        ]
        assert primary_key == ["submission_id", "trade_id"]
        assert connection.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name = 'order_fills_v2'"
        ).fetchone()[0] == 0

        ExecutionLedger._ensure_schema(connection)
        primary_key = [
            row[1]
            for row in sorted(
                connection.execute("PRAGMA table_info(order_fills)"),
                key=lambda value: value[5],
            )
            if row[5]
        ]
        assert primary_key == ["submission_id", "trade_id", "bucket_index"]
        assert connection.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 1


def test_order_fill_migration_recovers_stale_v2_only_table(tmp_path):
    db_path = tmp_path / "trades.db"
    ExecutionLedger(db_path, strategy_name="golden-test")
    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE order_fills RENAME TO order_fills_v2")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        ExecutionLedger._ensure_schema(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "order_fills" in tables
        assert "order_fills_v2" not in tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_order_fill_migration_replaces_stale_v2_beside_legacy_source(tmp_path):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=1,
        result={"success": True, "orderID": "authoritative"},
        simulation=True,
    )
    ledger.record_fill(
        submission_id,
        "authoritative",
        {
            "id": "source-fill", "status": "CONFIRMED", "size": "1000000",
            "price": "0.4", "taker_order_id": "authoritative",
        },
    )
    _replace_fill_table_with_legacy_primary_key(db_path)
    with sqlite3.connect(db_path) as connection:
        ExecutionLedger._create_order_fills_table(connection, "order_fills_v2")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        ExecutionLedger._ensure_schema(connection)
        rows = connection.execute(
            "SELECT order_id, trade_id FROM order_fills"
        ).fetchall()
        assert rows == [("authoritative", "source-fill")]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name = 'order_fills_v2'"
        ).fetchone()[0] == 0
