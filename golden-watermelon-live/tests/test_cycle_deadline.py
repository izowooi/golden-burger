import pytest

from polybot.api.clob_client import ClobClientWrapper
from polybot.config import ApiConfig
from polybot.utils.deadline import CycleBudget


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_cycle_runtime_never_suppresses_network_after_target() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(monotonic=clock)

    clock.value = 41.9
    budget.ensure_can_start_request("last safe request")
    assert budget.network_remaining_seconds == pytest.approx(0.1)

    clock.value = 42.0
    budget.ensure_can_start_request("late request")

    clock.value = 55.0
    budget.assert_within_hard_deadline("completion")
    evidence = budget.evidence()
    assert evidence["target_exceeded"] is True
    assert evidence["over_target_seconds"] == pytest.approx(5.0)
    assert evidence["elapsed_time_can_suppress_requests"] is False


def test_cycle_runtime_keeps_fixed_socket_timeout_after_target() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(monotonic=clock)
    clock.value = 75.0

    connect, read = budget.request_timeouts(2.0, 5.0, context="Gamma")

    assert connect == pytest.approx(2.0)
    assert read == pytest.approx(5.0)
    assert (connect, read) == (2.0, 5.0)


def test_clob_client_allows_initialized_request_after_runtime_target() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(monotonic=clock)
    clock.value = 42.0
    wrapper = ClobClientWrapper(
        ApiConfig("", ""), simulation_mode=True, cycle_budget=budget
    )
    wrapper._client = object()
    wrapper._initialized = True

    assert wrapper.client is wrapper._client
    assert wrapper._initialized is True


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
