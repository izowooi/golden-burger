from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
import requests

from polybot_observability import ExecutionLedger, SubmissionEvidenceError
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


def test_gamma_uses_keyset_cursor_and_deduplicates_conditions(monkeypatch):
    page_sleeps = []
    monkeypatch.setattr("polybot.api.gamma_client.time.sleep", page_sleeps.append)
    client = GammaClient()
    client.session = KeysetSession()

    markets = client.get_all_tradable_markets(min_liquidity=10_000)

    assert [market["conditionId"] for market in markets] == ["one", "two"]
    assert client.session.calls[0][0].endswith("/markets/keyset")
    assert client.session.calls[0][1]["include_tag"] == "true"
    assert client.session.calls[1][1]["after_cursor"] == "cursor-1"
    assert all(call[2] == (3.05, 20.0) for call in client.session.calls)
    assert page_sleeps == [client.KEYSET_PAGE_INTERVAL_SECONDS]

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


class RateLimitedResponse:
    status_code = 429
    headers = {"Retry-After": "0"}

    def raise_for_status(self):
        raise requests.exceptions.HTTPError("rate limited", response=self)


class MidSweepRateLimitSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        cursor = params.get("after_cursor")
        if cursor is None:
            return Response({"markets": [], "next_cursor": "cursor-1"})
        if len(self.calls) == 2:
            return RateLimitedResponse()
        return Response({"markets": []})


def test_gamma_retries_only_the_rate_limited_page(monkeypatch):
    sleeps = []
    monkeypatch.setattr("polybot.utils.retry.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("polybot.utils.retry.time.sleep", sleeps.append)
    client = GammaClient()
    client.session = MidSweepRateLimitSession()

    assert client.get_all_tradable_markets() == []

    cursors = [call[1].get("after_cursor") for call in client.session.calls]
    assert cursors == [None, "cursor-1", "cursor-1"]
    assert sleeps == [client.KEYSET_PAGE_INTERVAL_SECONDS, 2.0]
    assert client.last_sweep_attestation["pages"] == 2


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

    assert len(client.session.calls) == 6
    assert all(call[2] == (3.05, 20.0) for call in client.session.calls)
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert client.last_sweep_attestation is None


@pytest.mark.parametrize(
    ("retry_after", "expected_delays"),
    [
        ("999999999", [MAX_RETRY_DELAY_SECONDS, MAX_RETRY_DELAY_SECONDS]),
        ("Wed, 31 Dec 2099 23:59:59 GMT", [MAX_RETRY_DELAY_SECONDS, MAX_RETRY_DELAY_SECONDS]),
        ("not-a-valid-retry-after", [2.0, 4.0]),
        ("0", [2.0, 4.0]),
    ],
)
def test_retry_after_is_defensive_capped_and_skips_final_sleep(
    monkeypatch, retry_after, expected_delays
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
    assert sleeps == expected_delays


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


class TradeQueryClient:
    def __init__(self, trades):
        self.trades = trades

    def get_order(self, order_id):
        assert order_id == "trade-query-order"
        return {
            "status": "MATCHED",
            "original_size": "10000000",
            "size_matched": "10000000",
            "associate_trades": ["requested-trade"],
        }

    def get_trades(self, params, only_first_page=False):
        assert params.id == "requested-trade"
        assert only_first_page is True
        return self.trades


def _trade_query_wrapper(db_path, trades):
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-date"
    )
    wrapper._client = TradeQueryClient(trades)
    wrapper._initialized = True
    wrapper.execution_ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "trade-query-order"},
        simulation=False,
    )
    return wrapper


class ModelResponse:
    def __init__(self, **values):
        self.values = values

    def model_dump(self):
        return dict(self.values)


class AttributeTradePage:
    def __init__(self, data):
        self.data = data


class TypedReconcileClient:
    def get_order(self, order_id):
        assert order_id == "typed-order"
        return SimpleNamespace(
            status="ORDER_STATUS_MATCHED",
            originalSize="10000000",
            sizeMatched="10000000",
            price="0.42",
            associateTrades=[SimpleNamespace(id="typed-trade")],
            api_secret="must-not-be-copied",
        )

    def get_trades(self, params, only_first_page=False):
        assert params.id == "typed-trade"
        assert only_first_page is True
        return AttributeTradePage(
            [
                ModelResponse(
                    tradeId="typed-trade",
                    status="CONFIRMED",
                    size="10000000",
                    price="0.42",
                    side="BUY",
                    feeRateBps="0",
                    takerOrderId="typed-order",
                    traderSide="TAKER",
                    matchTime="1700000000",
                    private_key="must-not-be-copied",
                )
            ]
        )


class LegacyCatalogClient:
    def __init__(
        self,
        catalog=None,
        *,
        catalog_error=None,
        normal_response=None,
        current_catalog=None,
    ):
        self.catalog = [] if catalog is None else catalog
        self.catalog_error = catalog_error
        self.normal_response = normal_response
        self.current_catalog = [] if current_catalog is None else current_catalog
        self.current_catalog_calls = []
        self.pre_migration_calls = 0

    def get_order(self, _order_id):
        return self.normal_response

    def get_open_orders(self, params, only_first_page=False):
        assert only_first_page is True
        self.current_catalog_calls.append(params.id)
        return self.current_catalog

    def get_pre_migration_orders(self):
        self.pre_migration_calls += 1
        if self.catalog_error is not None:
            raise self.catalog_error
        return self.catalog

    def get_trades(self, *_args, **_kwargs):
        raise AssertionError("unfilled canceled legacy orders have no trade IDs")


def _bootstrap_legacy_orders(db_path, order_ids):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE trades (
                token_id TEXT,
                buy_order_id TEXT,
                buy_price REAL,
                buy_shares REAL,
                buy_timestamp TEXT,
                status TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, 'HOLDING')",
            [
                ("token", order_id, 0.42, 10, "2026-07-10T00:00:00+00:00")
                for order_id in order_ids
            ],
        )
    # First process bootstraps pre-ledger rows. The wrapper below creates a new
    # ledger instance against the same DB, reproducing a Jenkins restart.
    ExecutionLedger(db_path, strategy_name="golden-date")


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

    assert stats == {
        "checked": 1,
        "fills": 1,
        "completed": 1,
        "legacy_unavailable": 0,
        "errors": 0,
    }
    with sqlite3.connect(db_path) as connection:
        fill = connection.execute(
            "SELECT status, size, price, fee_rate_bps FROM order_fills"
        ).fetchone()
    assert fill == ("CONFIRMED", 10.0, 0.42, 0.0)


@pytest.mark.parametrize(
    "trades",
    [
        [],
        [{"id": "different-trade", "status": "CONFIRMED"}],
    ],
)
def test_associated_trade_query_empty_or_mismatch_stays_fail_closed(
    tmp_path, trades
):
    wrapper = _trade_query_wrapper(tmp_path / "trades.db", trades)

    stats = wrapper.reconcile_order_ledger()

    assert stats["errors"] == 1
    assert stats["completed"] == 0
    assert len(wrapper.execution_ledger.pending_submissions()) == 1
    with sqlite3.connect(wrapper.execution_ledger.db_path) as connection:
        error = connection.execute(
            "SELECT reconciliation_error FROM order_submissions"
        ).fetchone()[0]
    assert error.startswith("phase=validate_trades error=ClobResponseContractError")


def test_associated_trade_query_allows_multiple_buckets_for_exact_trade_id(tmp_path):
    trades = [
        {
            "id": "requested-trade",
            "bucket_index": bucket,
            "status": "CONFIRMED",
            "size": "5000000",
            "price": "0.42",
            "side": "BUY",
            "fee_rate_bps": 0,
            "taker_order_id": "trade-query-order",
        }
        for bucket in (0, 1)
    ]
    wrapper = _trade_query_wrapper(tmp_path / "trades.db", trades)

    stats = wrapper.reconcile_order_ledger()

    assert stats == {
        "checked": 1,
        "fills": 2,
        "completed": 1,
        "legacy_unavailable": 0,
        "errors": 0,
    }
    with sqlite3.connect(wrapper.execution_ledger.db_path) as connection:
        rows = connection.execute(
            "SELECT trade_id, bucket_index, size FROM order_fills "
            "ORDER BY bucket_index"
        ).fetchall()
    assert rows == [
        ("requested-trade", 0, 5.0),
        ("requested-trade", 1, 5.0),
    ]


def test_clob_reconciliation_accepts_typed_sdk_response_models(tmp_path):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(),
        audit_db_path=db_path,
        strategy_name="golden-date",
    )
    wrapper._client = TypedReconcileClient()
    wrapper._initialized = True
    wrapper.execution_ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result=ModelResponse(success=True, order_id="typed-order", status="live"),
        simulation=False,
    )

    stats = wrapper.reconcile_order_ledger()

    assert stats == {
        "checked": 1,
        "fills": 1,
        "completed": 1,
        "legacy_unavailable": 0,
        "errors": 0,
    }
    with sqlite3.connect(db_path) as connection:
        fill = connection.execute(
            "SELECT trade_id, status, size, price, liquidity_role FROM order_fills"
        ).fetchone()
    assert fill == ("typed-trade", "CONFIRMED", 10.0, 0.42, "TAKER")


def test_legacy_bootstrap_restart_uses_one_catalog_fetch_and_closes_missing_gap(
    tmp_path,
):
    db_path = tmp_path / "trades.db"
    _bootstrap_legacy_orders(db_path, ["legacy-exact", "legacy-missing"])
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-date"
    )
    client = LegacyCatalogClient(
        catalog=[
            {
                "id": "legacy-exact",
                "status": "CANCELED",
                "original_size": "10000000",
                "size_matched": "0",
                "price": "0.42",
            }
        ]
    )
    wrapper._client = client
    wrapper._initialized = True

    stats = wrapper.reconcile_order_ledger()

    assert stats == {
        "checked": 2,
        "fills": 0,
        "completed": 1,
        "legacy_unavailable": 1,
        "errors": 0,
    }
    assert client.pre_migration_calls == 1
    assert client.current_catalog_calls == ["legacy-exact", "legacy-missing"]
    assert wrapper.execution_ledger.pending_submissions() == []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT order_id, response_status, needs_reconciliation, "
            "reconciliation_error FROM order_submissions ORDER BY order_id"
        ).fetchall()
    assert rows == [
        ("legacy-exact", "LEGACY_ASSUMED", 0, None),
        (
            "legacy-missing",
            "LEGACY_UNAVAILABLE",
            0,
            "legacy order unavailable; fill evidence gap remains",
        ),
    ]


def test_duplicate_pre_migration_exact_id_stays_fail_closed(tmp_path):
    db_path = tmp_path / "trades.db"
    _bootstrap_legacy_orders(db_path, ["legacy-duplicate"])
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-date"
    )
    duplicate = {"id": "legacy-duplicate", "status": "CANCELED"}
    client = LegacyCatalogClient(catalog=[duplicate, dict(duplicate)])
    wrapper._client = client
    wrapper._initialized = True

    stats = wrapper.reconcile_order_ledger()

    assert stats == {
        "checked": 1,
        "fills": 0,
        "completed": 0,
        "legacy_unavailable": 0,
        "errors": 1,
    }
    assert client.pre_migration_calls == 1
    pending = wrapper.execution_ledger.pending_submissions()
    assert pending[0]["response_status"] == "LEGACY_ASSUMED"
    with sqlite3.connect(db_path) as connection:
        error = connection.execute(
            "SELECT reconciliation_error FROM order_submissions"
        ).fetchone()[0]
    assert error.startswith(
        "phase=match_pre_migration_order error=ClobResponseContractError "
    )


def test_pre_migration_catalog_fetch_failure_stays_fail_closed(tmp_path):
    db_path = tmp_path / "trades.db"
    _bootstrap_legacy_orders(db_path, ["legacy-fetch-fail"])
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-date"
    )
    client = LegacyCatalogClient(catalog_error=RuntimeError("transient"))
    wrapper._client = client
    wrapper._initialized = True

    stats = wrapper.reconcile_order_ledger()

    assert stats["errors"] == 1
    assert stats["completed"] == 0
    assert client.pre_migration_calls == 1
    assert len(wrapper.execution_ledger.pending_submissions()) == 1
    with sqlite3.connect(db_path) as connection:
        error = connection.execute(
            "SELECT reconciliation_error FROM order_submissions"
        ).fetchone()[0]
    assert error == (
        "phase=fetch_pre_migration_orders error=RuntimeError "
        "response_shape=sequence(len=0,item_type=none)"
    )


@pytest.mark.parametrize(
    "normal_response",
    [
        None,
        {"private_key": "raw-secret-value"},
    ],
)
def test_new_order_unavailable_stays_fail_closed_when_catalogs_have_no_exact_id(
    tmp_path, caplog, normal_response
):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-date"
    )
    client = LegacyCatalogClient(catalog=[], normal_response=normal_response)
    wrapper._client = client
    wrapper._initialized = True
    wrapper.execution_ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "accepted-new"},
        simulation=False,
    )

    stats = wrapper.reconcile_order_ledger()

    assert stats == {
        "checked": 1,
        "fills": 0,
        "completed": 0,
        "legacy_unavailable": 0,
        "errors": 1,
    }
    assert client.current_catalog_calls == ["accepted-new"]
    assert client.pre_migration_calls == 1
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT response_status, needs_reconciliation, reconciliation_error "
            "FROM order_submissions"
        ).fetchone()
    assert row == (
        "ACCEPTED",
        1,
        "phase=match_authoritative_order_catalogs "
        "error=ClobResponseUnavailableError "
        "response_shape=sequence(len=0,item_type=none)",
    )
    assert "raw-secret-value" not in caplog.text
    assert "private_key" not in caplog.text


@pytest.mark.parametrize("catalog_name", ["current", "pre_migration"])
def test_new_order_null_response_recovers_from_exact_authoritative_catalog(
    tmp_path, catalog_name
):
    exact_order = {
        "id": "accepted-new",
        "status": "CANCELED",
        "original_size": "10000000",
        "size_matched": "0",
        "price": "0.42",
    }
    client = LegacyCatalogClient(
        current_catalog=[exact_order] if catalog_name == "current" else [],
        catalog=[exact_order] if catalog_name == "pre_migration" else [],
    )
    wrapper = ClobClientWrapper(
        SimpleNamespace(),
        audit_db_path=tmp_path / "trades.db",
        strategy_name="golden-date",
    )
    wrapper._client = client
    wrapper._initialized = True
    wrapper.execution_ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "accepted-new"},
        simulation=False,
    )

    stats = wrapper.reconcile_order_ledger()

    assert stats == {
        "checked": 1,
        "fills": 0,
        "completed": 1,
        "legacy_unavailable": 0,
        "errors": 0,
    }
    assert client.current_catalog_calls == ["accepted-new"]
    assert client.pre_migration_calls == (catalog_name == "pre_migration")
    assert wrapper.execution_ledger.pending_submissions() == []


def test_normal_order_response_id_mismatch_stays_fail_closed(tmp_path):
    db_path = tmp_path / "trades.db"
    wrapper = ClobClientWrapper(
        SimpleNamespace(), audit_db_path=db_path, strategy_name="golden-date"
    )
    client = LegacyCatalogClient(
        normal_response={"id": "different-order", "status": "MATCHED"}
    )
    wrapper._client = client
    wrapper._initialized = True
    wrapper.execution_ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.42,
        requested_size=10,
        result={"success": True, "orderID": "accepted-new"},
        simulation=False,
    )

    stats = wrapper.reconcile_order_ledger()

    assert stats["errors"] == 1
    assert stats["completed"] == 0
    assert client.pre_migration_calls == 0
    with sqlite3.connect(db_path) as connection:
        error = connection.execute(
            "SELECT reconciliation_error FROM order_submissions"
        ).fetchone()[0]
    assert error.startswith(
        "phase=validate_order_identity error=ClobResponseContractError"
    )


@pytest.mark.parametrize(
    "result",
    [
        {"price": "0.41"},
        SimpleNamespace(price="0.41"),
    ],
)
def test_clob_price_response_supports_mapping_and_attribute_models(result):
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = SimpleNamespace(get_price=lambda *_args, **_kwargs: result)
    wrapper._initialized = True

    assert wrapper.get_best_bid("token") == 0.41


class CancelClient:
    def __init__(self, response):
        self.response = response

    def cancel_orders(self, order_ids):
        assert order_ids == ["cancel-me"]
        return self.response


def test_public_cancel_requires_exact_canceled_order_id():
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = CancelClient(
        ModelResponse(canceled=["cancel-me"], not_canceled={})
    )
    wrapper._initialized = True

    assert wrapper.cancel_order("cancel-me")["canceled"] == ["cancel-me"]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"canceled": [], "not_canceled": {"cancel-me": "already matched"}},
        {"canceled": ["different-order"], "not_canceled": {}},
        SimpleNamespace(unexpected="typed-unreadable"),
    ],
)
def test_public_cancel_unproved_result_raises_submission_evidence_error(response):
    wrapper = ClobClientWrapper(SimpleNamespace())
    wrapper._client = CancelClient(response)
    wrapper._initialized = True

    with pytest.raises(SubmissionEvidenceError):
        wrapper.cancel_order("cancel-me")


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
