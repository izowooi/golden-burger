from __future__ import annotations

from dataclasses import replace

from polybot.api.clob_client import ClobBookClient
from polybot.config import PROJECT_ROOT, load_config
from polybot.utils.retry import JsonResponse


class FakeTransport:
    def __init__(self):
        self.request_bodies = []

    def request_json(self, method, url, **kwargs):
        tokens = [item["token_id"] for item in kwargs["json_body"]]
        self.request_bodies.append(tokens)
        payload = [
            {
                "asset_id": token,
                "market": "condition",
                "timestamp": "1",
                "hash": token,
                "bids": [{"price": "0.49", "size": "10"}],
                "asks": [{"price": "0.50", "size": "10"}],
                "tick_size": "0.01",
                "min_order_size": "5",
            }
            for token in tokens[:-1]
        ]
        return JsonResponse(payload, b"[]", "request", "2026-08-13T00:00:00Z", "2026-08-13T00:00:01Z", "0" * 64)


def test_batch_books_records_each_missing_token(monkeypatch):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
    result = ClobBookClient(config.trading.orderbook, FakeTransport()).fetch_books(
        "run", ["yes", "no"]
    )
    assert result.attempts["yes"].status == "OBSERVED"
    assert result.attempts["no"].status == "MISSING"
    assert len(result.raw_payloads) == 1


class CompleteTransport(FakeTransport):
    def request_json(self, method, url, **kwargs):
        tokens = [item["token_id"] for item in kwargs["json_body"]]
        self.request_bodies.append(tokens)
        payload = [
            {
                "asset_id": token,
                "market": "condition",
                "timestamp": "1",
                "hash": token,
                "bids": [{"price": "0.49", "size": "10"}],
                "asks": [{"price": "0.50", "size": "10"}],
                "tick_size": "0.01",
                "min_order_size": "5",
            }
            for token in tokens
        ]
        return JsonResponse(
            payload,
            b"[]",
            f"request-{len(self.request_bodies)}",
            "2026-08-13T00:00:00Z",
            "2026-08-13T00:00:01Z",
            "0" * 64,
        )


def test_atomic_yes_no_pairs_never_split_at_batch_boundary(monkeypatch):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
    orderbook = replace(config.trading.orderbook, batch_token_limit=3)
    transport = CompleteTransport()

    result = ClobBookClient(orderbook, transport).fetch_books(
        "run",
        ["yes-a", "no-a", "yes-b", "no-b", "followup"],
        atomic_pairs=[("yes-a", "no-a"), ("yes-b", "no-b")],
    )

    assert len(result.books) == 5
    assert all(
        {"yes-a", "no-a"}.issubset(body)
        or {"yes-a", "no-a"}.isdisjoint(body)
        for body in transport.request_bodies
    )
    assert all(
        {"yes-b", "no-b"}.issubset(body)
        or {"yes-b", "no-b"}.isdisjoint(body)
        for body in transport.request_bodies
    )
