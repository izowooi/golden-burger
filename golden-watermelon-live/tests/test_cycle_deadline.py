import pytest

from polybot.api.clob_client import ClobClientWrapper
from polybot.config import ApiConfig
from polybot.utils.deadline import CycleBudget, CycleDeadlineExceeded


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_cycle_budget_stops_new_network_before_hard_deadline() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(monotonic=clock)

    clock.value = 41.9
    budget.ensure_can_start_request("last safe request")
    assert budget.network_remaining_seconds == pytest.approx(0.1)

    clock.value = 42.0
    with pytest.raises(CycleDeadlineExceeded, match="request stop"):
        budget.ensure_can_start_request("late request")

    clock.value = 50.0
    with pytest.raises(CycleDeadlineExceeded, match="hard cycle deadline"):
        budget.assert_within_hard_deadline("completion")


def test_cycle_budget_clamps_socket_timeout_to_remaining_hard_window() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(monotonic=clock)
    clock.value = 41.8

    connect, read = budget.request_timeouts(2.0, 5.0, context="Gamma")

    assert connect == pytest.approx(2.0)
    assert read == pytest.approx(5.0)
    assert connect + read < budget.hard_remaining_seconds


def test_clob_client_rejects_initialization_after_request_stop() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(monotonic=clock)
    clock.value = 42.0
    wrapper = ClobClientWrapper(
        ApiConfig("key", "funder"),
        simulation_mode=False,
        cycle_budget=budget,
    )

    with pytest.raises(CycleDeadlineExceeded, match="request stop"):
        _ = wrapper.client

    assert wrapper._initialized is False


@pytest.mark.parametrize(
    ("hard_limit", "margin"),
    [(0, 1), (50, 0), (50, 50), (float("nan"), 8)],
)
def test_cycle_budget_rejects_invalid_contract(
    hard_limit: float, margin: float
) -> None:
    with pytest.raises(ValueError, match="deadline ordering"):
        CycleBudget(
            started_monotonic=0,
            hard_limit_seconds=hard_limit,
            network_stop_margin_seconds=margin,
        )
