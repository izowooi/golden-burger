from __future__ import annotations

import json
import sys
from datetime import datetime
from types import SimpleNamespace

from polybot_observability import ExecutionLedger
from polybot_observability.intent_probe import (
    authenticated_clob_session_from_environment,
    probe_unresolved_intent,
)

FUNDER = "0x" + "1" * 40


def test_authenticated_session_derives_existing_key_without_create(monkeypatch):
    captured = {}
    credentials = object()

    class Client:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def derive_api_key(self):
            captured["derived"] = True
            return credentials

        def set_api_creds(self, value):
            captured["credentials_set"] = value

    monkeypatch.setitem(
        sys.modules,
        "py_clob_client_v2",
        SimpleNamespace(ClobClient=Client),
    )
    session = authenticated_clob_session_from_environment(
        {
            "POLYMARKET_PRIVATE_KEY": "0xprivate-key-placeholder",
            "POLYMARKET_FUNDER_ADDRESS": FUNDER,
            "POLYMARKET_SIGNATURE_TYPE": "3",
        }
    )

    assert session.funder_address == FUNDER
    assert captured == {
        "init": {
            "host": "https://clob.polymarket.com",
            "key": "private-key-placeholder",
            "chain_id": 137,
            "signature_type": 3,
            "funder": FUNDER,
        },
        "derived": True,
        "credentials_set": credentials,
    }


def _uncertain_intent(tmp_path, *, requested_size=12.5):
    db_path = tmp_path / "trades.db"
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_intent(
        token_id="token-1",
        side="BUY",
        requested_price=0.8,
        requested_size=requested_size,
        simulation=False,
    )
    [intent] = ledger.unresolved_submission_outcomes()
    submitted_at = datetime.fromisoformat(intent["submitted_at"])
    return db_path, submission_id, int(submitted_at.timestamp())


class _EmptyClient:
    def get_open_orders(self, params, only_first_page=False):
        assert params.asset_id == "token-1"
        assert only_first_page is False
        return []

    def get_pre_migration_orders(self, only_first_page=False):
        assert only_first_page is False
        return []

    def get_trades(self, params, only_first_page=False):
        assert params.asset_id == "token-1"
        assert params.after < params.before
        assert only_first_page is False
        return []


def test_probe_finds_exact_current_order_without_mutating_resolution(tmp_path):
    db_path, submission_id, submitted_at = _uncertain_intent(tmp_path)

    class Client(_EmptyClient):
        def get_open_orders(self, params, only_first_page=False):
            super().get_open_orders(params, only_first_page)
            return [
                {
                    "id": "venue-order-1",
                    "status": "ORDER_STATUS_LIVE",
                    "maker_address": FUNDER.upper(),
                    "asset_id": "token-1",
                    "side": "BUY",
                    "original_size": "12500000",
                    "size_matched": "0",
                    "price": "0.8",
                    "created_at": str(submitted_at + 1),
                    "private_key": "must-not-be-copied",
                }
            ]

    result = probe_unresolved_intent(
        db_path,
        strategy_name="golden-test",
        submission_id=submission_id,
        client=Client(),
        funder_address=FUNDER,
    )

    assert result["unique_candidate_order_id"] == "venue-order-1"
    assert result["resolution_evidence"] == {
        "resolution": "ORDER_ID_LINKED",
        "order_id": "venue-order-1",
        "confirmation": f"LINK_{submission_id}_TO_venue-order-1",
        "reason": (
            "authenticated CLOB order/trade history exact match around "
            "submission timestamp"
        ),
    }
    assert result["order_candidates"][0]["strong_match"] is True
    assert "must-not-be-copied" not in json.dumps(result)
    assert ExecutionLedger(
        db_path, strategy_name="golden-test"
    ).unresolved_submission_outcomes()


def test_probe_finds_full_taker_fill_but_rejects_partial_fill_as_resolution(tmp_path):
    db_path, submission_id, submitted_at = _uncertain_intent(tmp_path)

    class FullFillClient(_EmptyClient):
        def get_trades(self, params, only_first_page=False):
            super().get_trades(params, only_first_page)
            return [
                {
                    "id": "trade-1",
                    "status": "TRADE_STATUS_CONFIRMED",
                    "asset_id": "token-1",
                    "side": "BUY",
                    "size": "12500000",
                    "price": "0.79",
                    "match_time": str(submitted_at + 2),
                    "trader_side": "TAKER",
                    "taker_order_id": "venue-order-2",
                    "maker_orders": [],
                }
            ]

    full = probe_unresolved_intent(
        db_path,
        strategy_name="golden-test",
        submission_id=submission_id,
        client=FullFillClient(),
        funder_address=FUNDER,
    )
    assert full["unique_candidate_order_id"] == "venue-order-2"
    assert full["trade_order_candidates"][0]["observed_matched_size"] == 12.5
    assert full["trade_order_candidates"][0]["size_matches_requested"] is True

    class PartialFillClient(FullFillClient):
        def get_trades(self, params, only_first_page=False):
            trades = super().get_trades(params, only_first_page)
            trades[0]["size"] = "5000000"
            return trades

    partial = probe_unresolved_intent(
        db_path,
        strategy_name="golden-test",
        submission_id=submission_id,
        client=PartialFillClient(),
        funder_address=FUNDER,
    )
    assert partial["candidate_order_ids"] == ["venue-order-2"]
    assert partial["unique_candidate_order_id"] is None
    assert partial["resolution_evidence"] is None
    assert partial["trade_order_candidates"][0]["size_matches_requested"] is False


def test_probe_correlates_maker_fill_only_to_configured_funder(tmp_path):
    db_path, submission_id, submitted_at = _uncertain_intent(tmp_path)

    class Client(_EmptyClient):
        def get_trades(self, params, only_first_page=False):
            super().get_trades(params, only_first_page)
            return [
                {
                    "id": "trade-maker",
                    "status": "CONFIRMED",
                    "asset_id": "token-1",
                    "side": "SELL",
                    "size": "12500000",
                    "price": "0.8",
                    "match_time": str(submitted_at + 2),
                    "trader_side": "MAKER",
                    "taker_order_id": "other-taker",
                    "maker_orders": [
                        {
                            "order_id": "other-maker",
                            "maker_address": "0x" + "2" * 40,
                            "asset_id": "token-1",
                            "side": "BUY",
                            "matched_amount": "12500000",
                            "price": "0.8",
                        },
                        {
                            "order_id": "our-maker",
                            "maker_address": FUNDER,
                            "asset_id": "token-1",
                            "side": "BUY",
                            "matched_amount": "12500000",
                            "price": "0.8",
                        },
                    ],
                }
            ]

    result = probe_unresolved_intent(
        db_path,
        strategy_name="golden-test",
        submission_id=submission_id,
        client=Client(),
        funder_address=FUNDER,
    )

    assert result["candidate_order_ids"] == ["our-maker"]
    assert result["unique_candidate_order_id"] == "our-maker"
    assert result["trade_order_candidates"][0]["strong_match"] is True


def test_probe_query_failure_is_secret_safe_and_never_proves_no_order(tmp_path):
    db_path, submission_id, _ = _uncertain_intent(tmp_path)

    class Client(_EmptyClient):
        def get_open_orders(self, params, only_first_page=False):
            raise RuntimeError("api_secret=should-never-appear")

    result = probe_unresolved_intent(
        db_path,
        strategy_name="golden-test",
        submission_id=submission_id,
        client=Client(),
        funder_address=FUNDER,
    )

    assert result["query_errors"] == [
        {"source": "current_orders", "error_type": "RuntimeError"}
    ]
    assert result["candidate_order_ids"] == []
    assert result["no_order_created_proven"] is False
    assert "should-never-appear" not in json.dumps(result)
