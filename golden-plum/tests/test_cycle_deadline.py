import pytest

from polybot.api.clob_client import ClobClientWrapper
from polybot.config import ApiConfig
from polybot.utils.deadline import (
    CycleBudget,
    CycleDeadlineExceeded,
    enforced_cycle_deadline,
)


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_simulation_budget_blocks_new_network_at_42_seconds() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(enforce_deadline=True, monotonic=clock)

    clock.value = 41.0
    connect, read = budget.request_timeouts(2.0, 5.0, context="Gamma")
    assert connect + read == pytest.approx(0.99)
    assert connect > 0
    assert read > 0

    clock.value = 42.0
    with pytest.raises(CycleDeadlineExceeded, match="network request"):
        budget.ensure_can_start_request("Gamma next page")


def test_simulation_budget_fails_at_hard_50_second_boundary() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(enforce_deadline=True, monotonic=clock)

    clock.value = 49.999
    budget.assert_within_hard_deadline("before audit success")
    clock.value = 50.0
    with pytest.raises(CycleDeadlineExceeded, match="hard deadline"):
        budget.assert_within_hard_deadline("audit success")

    evidence = budget.evidence()
    assert evidence["deadline_enforced"] is True
    assert evidence["network_stop_after_seconds"] == pytest.approx(42.0)
    assert evidence["target_exceeded"] is True


def test_live_budget_never_suppresses_reconciliation_or_requests() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(enforce_deadline=False, monotonic=clock)
    clock.value = 75.0

    budget.ensure_can_start_request("live reconciliation")
    budget.assert_within_hard_deadline("live exit")
    assert budget.request_timeouts(2.0, 5.0, context="live Gamma") == (2.0, 5.0)
    evidence = budget.evidence()
    assert evidence["target_exceeded"] is True
    assert evidence["elapsed_time_can_suppress_requests"] is False
    assert evidence["deadline_enforced"] is False


def test_clob_client_blocks_simulation_dispatch_after_network_boundary() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(enforce_deadline=True, monotonic=clock)
    wrapper = ClobClientWrapper(
        ApiConfig("", ""), simulation_mode=True, cycle_budget=budget
    )
    wrapper._client = object()
    wrapper._initialized = True
    clock.value = 42.0

    with pytest.raises(CycleDeadlineExceeded, match="CLOB client request"):
        _ = wrapper.client


def test_clob_client_remains_available_to_live_after_50_seconds() -> None:
    clock = FakeClock()
    budget = CycleBudget.start(enforce_deadline=False, monotonic=clock)
    wrapper = ClobClientWrapper(
        ApiConfig("", ""), simulation_mode=False, cycle_budget=budget
    )
    wrapper._client = object()
    wrapper._initialized = True
    clock.value = 75.0

    assert wrapper.client is wrapper._client


def test_context_manager_enforces_completion_boundary() -> None:
    clock = FakeClock()
    with pytest.raises(CycleDeadlineExceeded, match="cycle completion"):
        with enforced_cycle_deadline(
            enforce_deadline=True,
            monotonic=clock,
        ):
            clock.value = 50.0


def test_context_manager_accepts_live_none_without_enforcement() -> None:
    clock = FakeClock()
    with enforced_cycle_deadline(
        hard_limit_seconds=None,
        enforce_deadline=False,
        monotonic=clock,
    ) as budget:
        clock.value = 75.0
        budget.ensure_can_start_request("live reconciliation")
    assert budget.evidence()["deadline_enforced"] is False


@pytest.mark.parametrize(
    ("hard_limit", "margin", "enforced"),
    [
        (0, 1, True),
        (50, 0, True),
        (50, 50, True),
        (float("nan"), 8, True),
        (50, 8, "yes"),
    ],
)
def test_cycle_budget_rejects_invalid_contract(
    hard_limit: float, margin: float, enforced: bool
) -> None:
    with pytest.raises(ValueError, match="deadline ordering"):
        CycleBudget(
            started_monotonic=0,
            hard_limit_seconds=hard_limit,
            network_stop_margin_seconds=margin,
            enforce_deadline=enforced,
        )
