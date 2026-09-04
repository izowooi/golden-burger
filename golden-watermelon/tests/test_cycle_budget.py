from __future__ import annotations

import pytest

from polybot.utils.retry import (
    CycleBudget,
    CycleBudgetExceeded,
    NetworkBudgetExceeded,
    PublicJsonTransport,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class NoNetworkSession:
    def __init__(self) -> None:
        self.trust_env = True
        self.headers = {}
        self.calls = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("network must not start after the cooperative cutoff")

    def close(self) -> None:
        pass


def test_network_cutoff_records_explicit_incomplete_receipt_without_http() -> None:
    clock = Clock()
    budget = CycleBudget(0, network_seconds=42, cycle_seconds=50, monotonic=clock)
    session = NoNetworkSession()
    receipts = []
    transport = PublicJsonTransport(
        connect_timeout_seconds=3,
        read_timeout_seconds=30,
        max_retries=0,
        retry_base_seconds=0,
        retry_max_seconds=0,
        receipt_sink=receipts.append,
        session=session,
        budget=budget,
    )
    clock.value = 42

    with pytest.raises(NetworkBudgetExceeded):
        transport.request_json(
            "GET",
            "https://gamma-api.polymarket.com/events/keyset",
            request_kind="gamma_live_events_keyset:nba",
            run_id="run",
        )

    assert session.calls == 0
    assert receipts[0]["status"] == "SKIPPED_NETWORK_BUDGET"
    assert receipts[0]["error_type"] == "NetworkBudgetExceeded"
    assert budget.incomplete_reasons


def test_cycle_budget_is_cooperative_and_has_eight_second_persistence_reserve() -> None:
    clock = Clock()
    budget = CycleBudget(0, network_seconds=42, cycle_seconds=50, monotonic=clock)
    clock.value = 41
    connect, read = budget.request_timeouts(3, 30)
    assert connect + read <= 1.0 + 1e-9
    clock.value = 49.9
    budget.assert_cycle_available("persistence")
    clock.value = 50
    with pytest.raises(CycleBudgetExceeded):
        budget.assert_cycle_available("completion")
