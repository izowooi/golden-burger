from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
import requests

from polybot_observability import SubmissionEvidenceError
from polybot.api.clob_client import ClobClientWrapper
from polybot.api.gamma_client import GammaClient
from polybot.utils.retry import MAX_RETRY_DELAY_SECONDS, rate_limit_handler


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class KeysetSession:
    def __init__(self):
        self.calls = []
        self.pages = [
            {
                "markets": [
                    {
                        "conditionId": "one",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                        "outcomes": '["Yes", "No"]',
                    },
                    {
                        "conditionId": "closed",
                        "active": True,
                        "closed": False,
                        "acceptingOrders": False,
                        "liquidity": "50000",
                    },
                ],
                "next_cursor": "cursor-1",
            },
            {
                "markets": [
                    {
                        "conditionId": "one",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "20000",
                    },
                    {
                        "conditionId": "two",
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "liquidity": "30000",
                    },
                    {
                        "conditionId": "missing-tradability-fields",
                        "active": True,
                        "liquidity": "30000",
                    },
                ]
            },
        ]

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return Response(self.pages.pop(0))


def test_gamma_uses_keyset_cursor_and_deduplicates_conditions():
    client = GammaClient()
    client.session = KeysetSession()

    markets = client.get_all_tradable_markets(min_liquidity=10_000)

    assert [market["conditionId"] for market in markets] == ["one", "two"]
    assert client.session.calls[0][0].endswith("/markets/keyset")
    assert client.session.calls[0][1]["include_tag"] == "true"
    assert client.session.calls[1][1]["after_cursor"] == "cursor-1"
    assert all(call[2] == (3.05, 20.0) for call in client.session.calls)

    attestation = client.last_sweep_attestation
    assert attestation["cursor_complete"] is True
    assert attestation["pages"] == 2
    assert attestation["raw_market_count"] == 5
    assert attestation["unique_condition_count"] == 4
    assert attestation["qualified_market_count"] == 2
    assert attestation["duplicate_raw_count"] == 1
    assert len(attestation["membership_digest_sha256"]) == 64
    membership = {
        item["condition_id"]: item for item in attestation["memberships"]
    }
    assert membership["one"] == {
        "condition_id": "one",
        "raw_seen_count": 2,
        "qualified": True,
        "qualification_reason": "qualified",
    }
    assert membership["closed"]["qualification_reason"] == "order_book_disabled_or_missing"
    assert membership["missing-tradability-fields"]["qualified"] is False


class TimeoutSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        raise requests.exceptions.Timeout("read timed out")


def test_gamma_sweep_timeout_retries_are_bounded(monkeypatch):
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = TimeoutSession()

    with pytest.raises(requests.exceptions.Timeout):
        client.get_all_tradable_markets(min_liquidity=10_000)

    assert len(client.session.calls) == 3
    assert all(call[2] == (3.05, 20.0) for call in client.session.calls)
    assert sleeps == [2.0, 4.0]
    assert client.last_sweep_attestation is None


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        ("999999999", MAX_RETRY_DELAY_SECONDS),
        ("Wed, 31 Dec 2099 23:59:59 GMT", MAX_RETRY_DELAY_SECONDS),
        ("not-a-valid-retry-after", 2.0),
    ],
)
def test_retry_after_is_defensive_capped_and_skips_final_sleep(
    monkeypatch, retry_after, expected_delay
):
    attempts = []
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    response = SimpleNamespace(
        status_code=429,
        headers={"Retry-After": retry_after},
    )

    @rate_limit_handler(max_retries=3, base_delay=2.0)
    def always_rate_limited():
        attempts.append(1)
        raise requests.exceptions.HTTPError("rate limited", response=response)

    with pytest.raises(requests.exceptions.HTTPError):
        always_rate_limited()

    assert len(attempts) == 3
    assert sleeps == [expected_delay, expected_delay]


class ReconcileClient:
    def get_order(self, order_id):
        assert order_id == "order-1"
        return {
            "status": "ORDER_STATUS_MATCHED",
            "original_size": "10000000",
            "size_matched": "10000000",
            "price": "0.42",
            "associate_trades": ["trade-1"],
        }

    def get_trades(self, params, only_first_page=False):
        assert params.id == "trade-1"
        assert only_first_page is True
        return [
            {
                "id": "trade-1",
                "status": "CONFIRMED",
                "size": "10000000",
                "price": "0.42",
                "side": "BUY",
                "fee_rate_bps": "0",
                "taker_order_id": "order-1",
                "trader_side": "TAKER",
                "match_time": "1700000000",
            }
        ]


def test_clob_reconciliation_persists_confirmed_fill(tmp_path):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(),
        audit_db_path=db_path,
        strategy_name="golden-date",
    )
    wrapper._client = ReconcileClient()
    wrapper._initialized = True
    wrapper.execution_ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "order-1", "status": "live"},
        simulation=False,
    )

    stats = wrapper.reconcile_order_ledger()

    assert stats == {"checked": 1, "fills": 1, "completed": 1, "errors": 0}
    with sqlite3.connect(db_path) as connection:
        fill = connection.execute(
            "SELECT status, size, price, fee_rate_bps FROM order_fills"
        ).fetchone()
    assert fill == ("CONFIRMED", 10.0, 0.42, 0.0)


class PlacementClient:
    def __init__(self, db_path):
        self.db_path = db_path
        self.cancelled = []
        self.cancel_response = None

    def create_order(self, order_args):
        with sqlite3.connect(self.db_path) as connection:
            statuses = connection.execute(
                "SELECT response_status FROM order_submissions"
            ).fetchall()
        assert statuses == [("INTENT",)]
        return {"signed": True, "args": order_args}

    def post_order(self, signed_order, order_type):
        assert signed_order["signed"] is True
        return {
            "success": True,
            "orderID": "placed-order",
            "status": "live",
            "makingAmount": "4000000",
            "takingAmount": "10000000",
        }

    def cancel_orders(self, order_ids):
        self.cancelled.extend(order_ids)
        return self.cancel_response or {"canceled": order_ids, "not_canceled": {}}


def test_limit_order_intent_is_durable_before_post(tmp_path):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(),
        audit_db_path=db_path,
        strategy_name="golden-date",
    )
    client = PlacementClient(db_path)
    wrapper._client = client
    wrapper._initialized = True

    result = wrapper.place_limit_order("token", 0.4, 10, "BUY")

    assert result["orderID"] == "placed-order"
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT order_id, response_status, making_amount, taking_amount, "
            "needs_reconciliation FROM order_submissions"
        ).fetchone()
    assert row == ("placed-order", "LIVE", 4.0, 10.0, 1)


def test_accepted_order_is_canceled_if_response_evidence_write_fails(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(),
        audit_db_path=db_path,
        strategy_name="golden-date",
    )
    client = PlacementClient(db_path)
    wrapper._client = client
    wrapper._initialized = True
    monkeypatch.setattr(
        wrapper.execution_ledger,
        "record_submission_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    with pytest.raises(SubmissionEvidenceError):
        wrapper.place_limit_order("token", 0.4, 10, "BUY")

    assert client.cancelled == ["placed-order"]
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT order_id, response_status, needs_reconciliation "
            "FROM order_submissions"
        ).fetchone()
    assert row == ("placed-order", "EVIDENCE_WRITE_FAILED", 1)


def test_not_canceled_response_is_reported_as_evidence_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(),
        audit_db_path=db_path,
        strategy_name="golden-date",
    )
    client = PlacementClient(db_path)
    client.cancel_response = {
        "canceled": [],
        "not_canceled": {"placed-order": "already matched"},
    }
    wrapper._client = client
    wrapper._initialized = True
    monkeypatch.setattr(
        wrapper.execution_ledger,
        "record_submission_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    with pytest.raises(SubmissionEvidenceError, match="cancellation도 실패"):
        wrapper.place_limit_order("token", 0.4, 10, "BUY")
