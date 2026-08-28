"""Hard and cooperative runtime limits for one live Jenkins cycle."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import signal
import time
from typing import Callable, Iterator


HARD_CYCLE_LIMIT_SECONDS = 50.0
NETWORK_STOP_MARGIN_SECONDS = 8.0
_MINIMUM_HEADROOM_SECONDS = 0.05


class CycleDeadlineExceeded(RuntimeError):
    """The live cycle no longer has enough time to operate safely."""


@dataclass(frozen=True)
class CycleBudget:
    """One monotonic budget shared by discovery, reconciliation, and orders."""

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
        if self.hard_remaining_seconds <= _MINIMUM_HEADROOM_SECONDS:
            raise CycleDeadlineExceeded(
                f"hard cycle deadline exhausted before {context}"
            )
        if self.network_remaining_seconds <= _MINIMUM_HEADROOM_SECONDS:
            raise CycleDeadlineExceeded(
                f"network request stop reached before {context}"
            )

    def request_timeouts(
        self,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        *,
        context: str,
    ) -> tuple[float, float]:
        """Fit one requests timeout tuple inside the remaining hard boundary."""
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
        available = self.hard_remaining_seconds - _MINIMUM_HEADROOM_SECONDS
        minimum = _MINIMUM_HEADROOM_SECONDS
        if available <= minimum * 2:
            raise CycleDeadlineExceeded(
                f"hard cycle deadline has no socket headroom for {context}"
            )
        bounded_connect = max(minimum, min(connect, available - minimum))
        bounded_read = max(minimum, min(read, available - bounded_connect))
        return bounded_connect, bounded_read

    def assert_within_hard_deadline(self, context: str) -> None:
        if self.hard_remaining_seconds <= 0:
            raise CycleDeadlineExceeded(
                f"hard cycle deadline exceeded during {context}"
            )

    def evidence(self) -> dict[str, float]:
        return {
            "hard_limit_seconds": self.hard_limit_seconds,
            "network_stop_margin_seconds": self.network_stop_margin_seconds,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "network_remaining_seconds": round(
                self.network_remaining_seconds, 6
            ),
            "hard_remaining_seconds": round(self.hard_remaining_seconds, 6),
        }


@contextmanager
def enforced_cycle_deadline(
    *,
    hard_limit_seconds: float = HARD_CYCLE_LIMIT_SECONDS,
    network_stop_margin_seconds: float = NETWORK_STOP_MARGIN_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[CycleBudget]:
    """Adopt a launcher alarm, or install one, and expose its remaining budget.

    Jenkins starts the Python entry point through a 50-second POSIX alarm so
    interpreter/import contention is bounded too. Once Python reaches this
    context, the same inherited timer raises a normal exception. That lets the
    execution ledger quarantine an order whose POST outcome became uncertain.
    """
    if not all(
        hasattr(signal, name)
        for name in ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
    ):
        raise RuntimeError("POSIX hard cycle deadline support is required")
    hard_limit = float(hard_limit_seconds)
    margin = float(network_stop_margin_seconds)
    if (
        not math.isfinite(hard_limit)
        or not math.isfinite(margin)
        or hard_limit <= 0
        or not 0 < margin < hard_limit
    ):
        raise ValueError("cycle deadline ordering is invalid")

    inherited_remaining, _ = signal.getitimer(signal.ITIMER_REAL)
    effective_hard_limit = (
        min(hard_limit, inherited_remaining)
        if inherited_remaining > 0
        else hard_limit
    )
    if effective_hard_limit <= margin + _MINIMUM_HEADROOM_SECONDS:
        raise CycleDeadlineExceeded(
            "launcher deadline has no safe network-request window"
        )

    budget = CycleBudget.start(
        hard_limit_seconds=effective_hard_limit,
        network_stop_margin_seconds=margin,
        monotonic=monotonic,
    )
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _deadline_handler(_signum, _frame) -> None:
        raise CycleDeadlineExceeded(
            f"hard {effective_hard_limit:.3f}-second cycle deadline exceeded"
        )

    signal.signal(signal.SIGALRM, _deadline_handler)
    signal.setitimer(signal.ITIMER_REAL, effective_hard_limit)
    try:
        yield budget
        budget.assert_within_hard_deadline("cycle completion")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
