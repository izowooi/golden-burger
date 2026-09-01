"""Cooperative cycle budget for credential-free one-minute collectors.

Simulation collectors stop starting Gamma/CLOB work eight seconds before the
50-second hard boundary.  Their finite socket timeouts are then reduced to the
remaining request budget.  Live King/Queen cycles keep the historical
behavior: elapsed wall time is telemetry only and never suppresses wallet
reconciliation, exits, or orders.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import time
from typing import Callable, Iterator


HARD_CYCLE_LIMIT_SECONDS = 50.0
NETWORK_STOP_MARGIN_SECONDS = 8.0
_MINIMUM_SOCKET_TIMEOUT_SECONDS = 0.05
_TIMEOUT_ROUNDING_HEADROOM_SECONDS = 0.01


class CycleDeadlineExceeded(RuntimeError):
    """A simulation collector exhausted its cooperative cycle budget."""


@dataclass(frozen=True)
class CycleBudget:
    """One monotonic runtime budget shared by a complete cycle."""

    started_monotonic: float
    hard_limit_seconds: float = HARD_CYCLE_LIMIT_SECONDS
    network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS
    enforce_deadline: bool = False
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.started_monotonic)
            or not math.isfinite(self.hard_limit_seconds)
            or not math.isfinite(self.network_stop_margin_seconds)
            or self.hard_limit_seconds <= 0
            or not 0 < self.network_stop_margin_seconds < self.hard_limit_seconds
            or not isinstance(self.enforce_deadline, bool)
        ):
            raise ValueError("cycle deadline ordering is invalid")

    @classmethod
    def start(
        cls,
        *,
        hard_limit_seconds: float = HARD_CYCLE_LIMIT_SECONDS,
        network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS,
        enforce_deadline: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> "CycleBudget":
        return cls(
            started_monotonic=monotonic(),
            hard_limit_seconds=hard_limit_seconds,
            network_stop_margin_seconds=network_stop_margin_seconds,
            enforce_deadline=enforce_deadline,
            monotonic=monotonic,
        )

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.monotonic() - self.started_monotonic)

    @property
    def hard_remaining_seconds(self) -> float:
        return max(0.0, self.hard_limit_seconds - self.elapsed_seconds)

    @property
    def network_remaining_seconds(self) -> float:
        return max(
            0.0,
            self.hard_limit_seconds
            - self.network_stop_margin_seconds
            - self.elapsed_seconds,
        )

    def _deadline_error(self, boundary: str, context: str) -> CycleDeadlineExceeded:
        return CycleDeadlineExceeded(
            "simulation collector cycle deadline exhausted "
            f"before {boundary}: {context}; elapsed={self.elapsed_seconds:.3f}s"
        )

    def ensure_can_start_request(self, context: str) -> None:
        """Reject new simulation HTTP work after the 42-second start boundary."""
        if not self.enforce_deadline:
            return
        if self.network_remaining_seconds <= 0:
            raise self._deadline_error("network request", context)

    def request_timeouts(
        self,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        *,
        context: str,
    ) -> tuple[float, float]:
        """Return finite timeouts bounded by the simulation request budget."""
        connect = float(connect_timeout_seconds)
        read = float(read_timeout_seconds)
        if (
            not math.isfinite(connect)
            or not math.isfinite(read)
            or connect <= 0
            or read <= 0
        ):
            raise ValueError("request timeouts must be finite and positive")
        self.ensure_can_start_request(context)
        if not self.enforce_deadline:
            return connect, read

        available = max(
            0.0,
            self.network_remaining_seconds - _TIMEOUT_ROUNDING_HEADROOM_SECONDS,
        )
        requested = connect + read
        scale = min(1.0, available / requested) if requested else 0.0
        bounded_connect = connect * scale
        bounded_read = read * scale
        if (
            bounded_connect < _MINIMUM_SOCKET_TIMEOUT_SECONDS
            or bounded_read < _MINIMUM_SOCKET_TIMEOUT_SECONDS
        ):
            raise self._deadline_error("bounded network request", context)
        return bounded_connect, bounded_read

    def assert_within_hard_deadline(self, context: str) -> None:
        """Fail a simulation cycle that reaches the 50-second hard boundary."""
        if not self.enforce_deadline:
            return
        if self.hard_remaining_seconds <= 0:
            raise self._deadline_error("hard deadline", context)

    def evidence(self) -> dict[str, float | bool]:
        elapsed = self.elapsed_seconds
        return {
            "hard_limit_seconds": self.hard_limit_seconds,
            "network_stop_margin_seconds": self.network_stop_margin_seconds,
            "network_stop_after_seconds": (
                self.hard_limit_seconds - self.network_stop_margin_seconds
            ),
            "elapsed_seconds": round(elapsed, 6),
            "network_remaining_seconds": round(
                self.network_remaining_seconds, 6
            ),
            "hard_remaining_seconds": round(self.hard_remaining_seconds, 6),
            "target_exceeded": elapsed >= self.hard_limit_seconds,
            "over_target_seconds": round(
                max(0.0, elapsed - self.hard_limit_seconds), 6
            ),
            "elapsed_time_can_suppress_requests": self.enforce_deadline,
            "deadline_enforced": self.enforce_deadline,
        }


@contextmanager
def enforced_cycle_deadline(
    *,
    hard_limit_seconds: float | None = HARD_CYCLE_LIMIT_SECONDS,
    network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS,
    enforce_deadline: bool = True,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[CycleBudget]:
    """Yield a budget and enforce its hard boundary on normal completion."""
    resolved_hard_limit = (
        HARD_CYCLE_LIMIT_SECONDS
        if hard_limit_seconds is None
        else float(hard_limit_seconds)
    )
    budget = CycleBudget.start(
        hard_limit_seconds=resolved_hard_limit,
        network_stop_margin_seconds=float(network_stop_margin_seconds),
        enforce_deadline=enforce_deadline,
        monotonic=monotonic,
    )
    try:
        yield budget
    except BaseException:
        raise
    else:
        budget.assert_within_hard_deadline("cycle completion")
