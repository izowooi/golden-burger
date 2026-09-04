"""Bounded credential-free HTTP transport with attempt evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from threading import Lock
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

import requests
from requests.exceptions import ChunkedEncodingError, RequestException


def iso_utc(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class PublicApiError(RuntimeError):
    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class NetworkBudgetExceeded(PublicApiError):
    """No new network work may begin inside the persistence reserve."""


class CycleBudgetExceeded(RuntimeError):
    """The cooperative cycle target was exceeded at a safe boundary."""


class CycleBudget:
    """Thread-safe cooperative 42s network / 50s cycle boundary.

    It never sends a process signal or kills a worker.  Requests receive finite
    timeouts bounded by the shared network remainder; once exhausted, later
    work records an explicit incomplete receipt and fails closed.
    """

    def __init__(
        self,
        started_monotonic: float,
        *,
        network_seconds: float = 42.0,
        cycle_seconds: float = 50.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not math.isfinite(network_seconds)
            or not math.isfinite(cycle_seconds)
            or network_seconds <= 0
            or cycle_seconds <= network_seconds
        ):
            raise ValueError("cycle budget must satisfy 0 < network < cycle")
        self.started_monotonic = started_monotonic
        self.network_seconds = network_seconds
        self.cycle_seconds = cycle_seconds
        self.monotonic = monotonic
        self._lock = Lock()
        self._incomplete_reasons: list[str] = []

    @classmethod
    def start(
        cls,
        *,
        network_seconds: float = 42.0,
        cycle_seconds: float = 50.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> "CycleBudget":
        return cls(
            monotonic(),
            network_seconds=network_seconds,
            cycle_seconds=cycle_seconds,
            monotonic=monotonic,
        )

    @property
    def network_deadline(self) -> float:
        return self.started_monotonic + self.network_seconds

    @property
    def cycle_deadline(self) -> float:
        return self.started_monotonic + self.cycle_seconds

    @property
    def network_remaining_seconds(self) -> float:
        return max(0.0, self.network_deadline - self.monotonic())

    @property
    def cycle_remaining_seconds(self) -> float:
        return max(0.0, self.cycle_deadline - self.monotonic())

    def mark_incomplete(self, reason: str) -> None:
        with self._lock:
            if reason not in self._incomplete_reasons:
                self._incomplete_reasons.append(reason)

    @property
    def incomplete_reasons(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._incomplete_reasons)

    def ensure_can_start_network(self, phase: str) -> None:
        if self.network_remaining_seconds < 0.1:
            reason = f"network_budget_exhausted_before:{phase}"
            self.mark_incomplete(reason)
            raise NetworkBudgetExceeded(reason)

    def request_timeouts(self, connect: float, read: float) -> tuple[float, float]:
        self.ensure_can_start_network("HTTP")
        remaining = self.network_remaining_seconds
        connect_timeout = min(float(connect), max(0.05, remaining / 2))
        read_timeout = min(
            float(read), max(0.05, remaining - connect_timeout)
        )
        return connect_timeout, read_timeout

    def assert_cycle_available(self, phase: str) -> None:
        if self.cycle_remaining_seconds <= 0:
            reason = f"cycle_budget_exhausted_at:{phase}"
            self.mark_incomplete(reason)
            raise CycleBudgetExceeded(reason)

    def evidence(self) -> dict[str, Any]:
        return {
            "network_budget_seconds": self.network_seconds,
            "cycle_budget_seconds": self.cycle_seconds,
            "elapsed_seconds": max(
                0.0, self.monotonic() - self.started_monotonic
            ),
            "network_remaining_seconds": self.network_remaining_seconds,
            "cycle_remaining_seconds": self.cycle_remaining_seconds,
            "incomplete_reasons": list(self.incomplete_reasons),
        }


@dataclass(frozen=True)
class JsonResponse:
    payload: Any
    raw: bytes
    request_id: str
    started_at: str
    received_at: str
    response_sha256: str


class PublicJsonTransport:
    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        receipt_sink: Callable[[Mapping[str, Any]], None],
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        budget: CycleBudget | None = None,
    ) -> None:
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.receipt_sink = receipt_sink
        self.sleep = sleep
        self.budget = budget
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Accept": "application/json", "User-Agent": "golden-watermelon/0.1"})

    def fork(self) -> "PublicJsonTransport":
        """Create one isolated requests session sharing only durable sinks/budget."""
        return PublicJsonTransport(
            connect_timeout_seconds=self.connect_timeout_seconds,
            read_timeout_seconds=self.read_timeout_seconds,
            max_retries=self.max_retries,
            retry_base_seconds=self.retry_base_seconds,
            retry_max_seconds=self.retry_max_seconds,
            receipt_sink=self.receipt_sink,
            sleep=self.sleep,
            budget=self.budget,
        )

    def close(self) -> None:
        self.session.close()

    def _record_budget_skip(
        self,
        *,
        request_id: str,
        run_id: str,
        request_kind: str,
        page_number: int | None,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        json_body: Any | None,
        error: NetworkBudgetExceeded,
    ) -> None:
        observed = iso_utc()
        self.receipt_sink({
            "request_id": request_id,
            "run_id": run_id,
            "request_kind": request_kind,
            "page_number": page_number,
            "attempt_number": 0,
            "method": method.upper(),
            "url": url,
            "params_json": canonical_json(dict(params or {})),
            "body_sha256": (
                hashlib.sha256(canonical_json(json_body).encode()).hexdigest()
                if json_body is not None else None
            ),
            "started_at": observed,
            "completed_at": observed,
            "elapsed_ms": 0.0,
            "status": "SKIPPED_NETWORK_BUDGET",
            "http_status": None,
            "response_sha256": None,
            "response_bytes": 0,
            "error_type": type(error).__name__,
            "error_message": str(error)[:500],
        })

    def request_json(
        self,
        method: str,
        url: str,
        *,
        request_kind: str,
        run_id: str,
        page_number: int | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> JsonResponse:
        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 2):
            request_id = uuid4().hex
            started = iso_utc()
            started_clock = time.monotonic()
            response = None
            raw = b""
            if self.budget is not None:
                try:
                    self.budget.ensure_can_start_network(request_kind)
                except NetworkBudgetExceeded as error:
                    self._record_budget_skip(
                        request_id=request_id,
                        run_id=run_id,
                        request_kind=request_kind,
                        page_number=page_number,
                        method=method,
                        url=url,
                        params=params,
                        json_body=json_body,
                        error=error,
                    )
                    raise
            try:
                timeout = (
                    self.budget.request_timeouts(
                        self.connect_timeout_seconds,
                        self.read_timeout_seconds,
                    )
                    if self.budget is not None
                    else (
                        self.connect_timeout_seconds,
                        self.read_timeout_seconds,
                    )
                )
                response = self.session.request(
                    method.upper(), url, params=dict(params or {}), json=json_body,
                    timeout=timeout,
                )
                raw = response.content
                response.raise_for_status()
                payload = json.loads(raw.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                received = iso_utc()
                digest = hashlib.sha256(raw).hexdigest()
                self.receipt_sink({
                    "request_id": request_id, "run_id": run_id, "request_kind": request_kind,
                    "page_number": page_number, "attempt_number": attempt, "method": method.upper(),
                    "url": url, "params_json": canonical_json(dict(params or {})),
                    "body_sha256": hashlib.sha256(canonical_json(json_body).encode()).hexdigest() if json_body is not None else None,
                    "started_at": started, "completed_at": received,
                    "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                    "status": "SUCCESS", "http_status": response.status_code,
                    "response_sha256": digest, "response_bytes": len(raw),
                    "error_type": None, "error_message": None,
                })
                if (
                    self.budget is not None
                    and self.budget.network_remaining_seconds <= 0
                ):
                    self.budget.mark_incomplete(
                        f"network_budget_exhausted_during:{request_kind}"
                    )
                return JsonResponse(payload, raw, request_id, started, received, digest)
            except (ChunkedEncodingError, RequestException, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                last_error = error
                status = response.status_code if response is not None else None
                retryable = status is None or status == 429 or (status is not None and 500 <= status < 600) or isinstance(error, (ChunkedEncodingError, ValueError))
                completed = iso_utc()
                self.receipt_sink({
                    "request_id": request_id, "run_id": run_id, "request_kind": request_kind,
                    "page_number": page_number, "attempt_number": attempt, "method": method.upper(),
                    "url": url, "params_json": canonical_json(dict(params or {})),
                    "body_sha256": hashlib.sha256(canonical_json(json_body).encode()).hexdigest() if json_body is not None else None,
                    "started_at": started, "completed_at": completed,
                    "elapsed_ms": round((time.monotonic() - started_clock) * 1000, 3),
                    "status": "FAILED", "http_status": status,
                    "response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                    "response_bytes": len(raw), "error_type": type(error).__name__,
                    "error_message": " ".join(str(error).splitlines())[:500],
                })
                if not retryable or attempt > self.max_retries:
                    break
                delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** (attempt - 1)))
                if (
                    self.budget is not None
                    and self.budget.network_remaining_seconds <= delay + 0.1
                ):
                    reason = f"network_budget_exhausted_before_retry:{request_kind}"
                    self.budget.mark_incomplete(reason)
                    raise NetworkBudgetExceeded(reason)
                self.sleep(delay)
        raise PublicApiError(f"public request failed: {type(last_error).__name__}: {last_error}")
