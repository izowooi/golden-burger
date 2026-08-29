"""Runtime telemetry and per-request socket bounds for one live cycle.

Elapsed wall time is observability, not a trading signal.  A previous version
stopped all network work around second 42 so Jenkins could finish before its
next one-minute trigger.  That can suppress reconciliation or an otherwise
valid order merely because a prior read was slow.  Concurrency is now handled
by the job lock; each HTTP request still has its own finite socket timeout.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import time
from typing import Callable, Iterator


HARD_CYCLE_LIMIT_SECONDS = 50.0
NETWORK_STOP_MARGIN_SECONDS = 8.0
_MINIMUM_HEADROOM_SECONDS = 0.05


class CycleDeadlineExceeded(RuntimeError):
    """Compatibility exception retained for older evidence readers."""


@dataclass(frozen=True)
class CycleBudget:
    """One monotonic runtime observer shared by the complete cycle."""

    started_monotonic: float
    hard_limit_seconds: float = HARD_CYCLE_LIMIT_SECONDS
    network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.started_monotonic)
            or not math.isfinite(self.hard_limit_seconds)
            or not math.isfinite(self.network_stop_margin_seconds)
            or self.hard_limit_seconds <= 0
            or not 0 < self.network_stop_margin_seconds < self.hard_limit_seconds
        ):
            raise ValueError("cycle deadline ordering is invalid")

    @classmethod
    def start(
        cls,
        *,
        hard_limit_seconds: float = HARD_CYCLE_LIMIT_SECONDS,
        network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> "CycleBudget":
        return cls(
            started_monotonic=monotonic(),
            hard_limit_seconds=hard_limit_seconds,
            network_stop_margin_seconds=network_stop_margin_seconds,
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

    def ensure_can_start_request(self, context: str) -> None:
        """Never reject a request based on elapsed cycle time.

        ``context`` remains part of the public API so old callers and evidence
        formats stay compatible.
        """
        _ = context

    def request_timeouts(
        self,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        *,
        context: str,
    ) -> tuple[float, float]:
        """Return fixed finite socket timeouts, independent of cycle age."""
        self.ensure_can_start_request(context)
        connect = float(connect_timeout_seconds)
        read = float(read_timeout_seconds)
        if (
            not math.isfinite(connect)
            or not math.isfinite(read)
            or connect <= 0
            or read <= 0
        ):
            raise ValueError("request timeouts must be finite and positive")
        return connect, read

    def assert_within_hard_deadline(self, context: str) -> None:
        """Compatibility no-op; cycle age cannot change trading decisions."""
        _ = context

    def evidence(self) -> dict[str, float | bool]:
        return {
            "hard_limit_seconds": self.hard_limit_seconds,
            "network_stop_margin_seconds": self.network_stop_margin_seconds,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "network_remaining_seconds": round(
                self.network_remaining_seconds, 6
            ),
            "hard_remaining_seconds": round(self.hard_remaining_seconds, 6),
            "target_exceeded": self.elapsed_seconds > self.hard_limit_seconds,
            "over_target_seconds": round(
                max(0.0, self.elapsed_seconds - self.hard_limit_seconds), 6
            ),
            "elapsed_time_can_suppress_requests": False,
        }


@contextmanager
def enforced_cycle_deadline(
    *,
    hard_limit_seconds: float = HARD_CYCLE_LIMIT_SECONDS,
    network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[CycleBudget]:
    """Expose runtime evidence without installing or adopting a process alarm."""
    hard_limit = float(hard_limit_seconds)
    margin = float(network_stop_margin_seconds)
    if (
        not math.isfinite(hard_limit)
        or not math.isfinite(margin)
        or hard_limit <= 0
        or not 0 < margin < hard_limit
    ):
        raise ValueError("cycle deadline ordering is invalid")

    budget = CycleBudget.start(
        hard_limit_seconds=hard_limit,
        network_stop_margin_seconds=margin,
        monotonic=monotonic,
    )
    yield budget
