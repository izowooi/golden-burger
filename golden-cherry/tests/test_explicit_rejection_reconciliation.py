"""Cherry must reuse exact rejection proofs, never age/open-order absence."""

import sqlite3

import httpx
import pytest
from py_clob_client_v2.exceptions import PolyApiException

from tests.test_signed_limit_submission import wrapper


POST_ONLY = (
    "PolyApiException[status_code=503, error_message={'error': 'post-only mode: "
    "only post-only orders and cancels are allowed', 'code': 'post_only_mode', "
    "'retry_after_seconds': 96}]"
)
TRADING_DISABLED = (
    "PolyApiException[status_code=503, error_message={'error': 'trading is disabled'}]"
)
REQUEST_UNKNOWN = "PolyApiException[status_code=None, error_message=Request exception!]"
DEADLINE_UNKNOWN = (
    "PolyApiException[status_code=500, error_message={'error': "
    "'rpc error: code = DeadlineExceeded desc = context deadline exceeded'}]"
)


class NoVenueLookup:
    def __getattr__(self, name):
        raise AssertionError(f"stored rejection proof must not query venue: {name}")


def seed_old_unknown(client, path, index, message):
    sid = client.execution_ledger.record_intent(
        token_id=f"historical-token-{index}",
        side="BUY",
        requested_price=0.8,
        requested_size=6.25,
        simulation=False,
    )
    with sqlite3.connect(path) as c:
        c.execute(
            "UPDATE order_submissions SET response_status='SUBMIT_OUTCOME_UNKNOWN',"
            "needs_reconciliation=0,error_type='PolyApiException',error_message=?,"
            "submitted_at='2020-01-01T00:00:00Z' WHERE submission_id=?",
            (message, sid),
        )
    return sid


def test_seven_explicit_rejections_resolve_and_seven_unknowns_stay_unchanged(tmp_path):
    client, _sdk, path = wrapper(tmp_path)
    client._client = NoVenueLookup()
    errors = (
        [POST_ONLY] * 2
        + [TRADING_DISABLED] * 5
        + [REQUEST_UNKNOWN] * 5
        + [DEADLINE_UNKNOWN] * 2
    )
    ids = [seed_old_unknown(client, path, i, error) for i, error in enumerate(errors)]
    with sqlite3.connect(path) as c:
        unknown_before = [
            c.execute(
                "SELECT * FROM order_submissions WHERE submission_id=?", (sid,)
            ).fetchone()
            for sid in ids[7:]
        ]

    stats = client.reconcile_order_ledger()

    assert stats["intent_autoresolved"] == 7
    assert stats["explicit_rejections_checked"] == 14
    assert stats["intents_without_rejection_proof"] == 7
    assert stats["checked"] == stats["errors"] == 0
    with sqlite3.connect(path) as c:
        for sid in ids[:7]:
            row = c.execute(
                "SELECT order_id,outcome_resolution,outcome_resolution_reason FROM order_submissions WHERE submission_id=?",
                (sid,),
            ).fetchone()
            assert row[0] is None and row[1] == "NO_ORDER_CREATED"
            assert "venue explicitly rejected" in row[2]
        unknown_after = [
            c.execute(
                "SELECT * FROM order_submissions WHERE submission_id=?", (sid,)
            ).fetchone()
            for sid in ids[7:]
        ]
    assert unknown_after == unknown_before
    assert client.execution_ledger.unresolved_submission_count(side="BUY") == 7
    again = client.reconcile_order_ledger()
    assert again["intent_autoresolved"] == 0
    assert again["intents_without_rejection_proof"] == 7


def test_simulation_does_not_apply_live_rejection_resolution(tmp_path):
    client, _sdk, path = wrapper(tmp_path)
    sid = seed_old_unknown(client, path, 1, TRADING_DISABLED)
    client.simulation_mode = True
    assert client.reconcile_order_ledger()["intent_autoresolved"] == 0
    with sqlite3.connect(path) as c:
        assert (
            c.execute(
                "SELECT outcome_resolution FROM order_submissions WHERE submission_id=?",
                (sid,),
            ).fetchone()[0]
            is None
        )


def test_associated_trade_evidence_prevents_rejection_release(tmp_path):
    client, _sdk, path = wrapper(tmp_path)
    client._client = NoVenueLookup()
    sid = seed_old_unknown(client, path, 1, TRADING_DISABLED)
    with sqlite3.connect(path) as c:
        c.execute(
            "UPDATE order_submissions SET associated_trade_ids_json='[\"known-trade\"]' WHERE submission_id=?",
            (sid,),
        )
    stats = client.reconcile_order_ledger()
    assert stats["intent_autoresolved"] == 0
    assert stats["intents_without_rejection_proof"] == 1


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (
            503,
            {
                "error": "post-only mode: only post-only orders and cancels are allowed",
                "code": "post_only_mode",
            },
            "FAILED",
        ),
        (503, {"error": "trading is disabled"}, "FAILED"),
        (
            500,
            {
                "error": "rpc error: code = DeadlineExceeded desc = context deadline exceeded"
            },
            "SUBMIT_OUTCOME_UNKNOWN",
        ),
        (None, "Request exception!", "SUBMIT_OUTCOME_UNKNOWN"),
    ],
)
def test_new_post_already_uses_shared_rejection_classification(
    tmp_path, status, payload, expected
):
    client, sdk, path = wrapper(tmp_path)
    error = (
        PolyApiException(error_msg=payload)
        if status is None
        else PolyApiException(httpx.Response(status, json=payload))
    )

    def post(_signed, _kind):
        sdk.post_count += 1
        raise error

    sdk.post_order = post
    result = client.place_limit_order("test-token", 0.99, 6.13, "SELL")
    assert result["success"] is False and sdk.post_count == 1
    with sqlite3.connect(path) as c:
        row = c.execute(
            "SELECT response_status,order_id FROM order_submissions"
        ).fetchone()
    assert row == (expected, None)
    assert bool(result.get("submission_outcome_unknown")) == (
        expected == "SUBMIT_OUTCOME_UNKNOWN"
    )
