from __future__ import annotations

import pytest

from polybot.api.clob_client import ClobClientWrapper
from polybot.config import ApiConfig
from polybot_observability import SubmissionEvidenceError


class FakeClient:
    def __init__(self, *, cancellation, detail):
        self.cancellation = cancellation
        self.detail = detail
        self.cancel_payloads = []
        self.order_ids = []

    def cancel_orders(self, order_ids):
        self.cancel_payloads.append(order_ids)
        return self.cancellation

    def get_order(self, order_id):
        self.order_ids.append(order_id)
        return self.detail


def make_wrapper(client):
    wrapper = ClobClientWrapper(
        ApiConfig(private_key="dummy", funder_address="dummy")
    )
    wrapper._client = client
    wrapper._initialized = True
    return wrapper


@pytest.mark.parametrize(
    "cancellation",
    [
        {"canceled": ["0xORDER"], "not_canceled": {}},
        {
            "canceled": [],
            "not_canceled": {"0xORDER": "Order not found or already canceled"},
        },
    ],
)
def test_cancel_accepts_only_authoritative_terminal_zero_fill(cancellation):
    client = FakeClient(
        cancellation=cancellation,
        detail={
            "id": "0xORDER",
            "status": "ORDER_STATUS_CANCELED",
            "size_matched": "0",
        },
    )

    result = make_wrapper(client).cancel_order("0xORDER")

    assert client.cancel_payloads == [["0xORDER"]]
    assert client.order_ids == ["0xORDER"]
    assert result["verified_order_status"] == "CANCELED"
    assert result["verified_size_matched"] == 0.0


@pytest.mark.parametrize(
    "detail",
    [
        {"id": "0xOTHER", "status": "CANCELED", "size_matched": "0"},
        {"id": "0xORDER", "status": "MATCHED", "size_matched": "0"},
        {"id": "0xORDER", "status": "CANCELED", "size_matched": "0.1"},
        {"id": "0xORDER", "status": "CANCELED", "size_matched": None},
    ],
)
def test_cancel_rejects_unproved_or_filled_order(detail):
    client = FakeClient(
        cancellation={"canceled": ["0xORDER"], "not_canceled": {}},
        detail=detail,
    )

    with pytest.raises(SubmissionEvidenceError):
        make_wrapper(client).cancel_order("0xORDER")
