"""Audited retries for public JSON endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import time
from typing import Any, Callable
from uuid import uuid4

import requests
from requests import Response
from requests.exceptions import ChunkedEncodingError, RequestException


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class JsonResponse:
    payload: Any
    raw: bytes
    request_id: str
    started_at: str
    received_at: str
    response_sha256: str
    logical_request_id: str | None = None


class PublicApiError(RuntimeError):
    """Public source failed after bounded retries."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


ReceiptSink = Callable[[dict[str, Any]], None]
BeforeFirstAttempt = Callable[[str, str], None]


class CycleBudgetExceeded(RuntimeError):
    """The cooperative cycle deadline cannot afford the next operation."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        elapsed_seconds: float,
        cooperative_remaining_seconds: float,
        network_remaining_seconds: float,
        hard_remaining_seconds: float,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.elapsed_seconds = elapsed_seconds
        self.cooperative_remaining_seconds = cooperative_remaining_seconds
        self.network_remaining_seconds = network_remaining_seconds
        self.hard_remaining_seconds = hard_remaining_seconds

    def evidence(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "cooperative_remaining_seconds": round(
                self.cooperative_remaining_seconds, 6
            ),
            "network_remaining_seconds": round(self.network_remaining_seconds, 6),
            "hard_remaining_seconds": round(self.hard_remaining_seconds, 6),
            "reason": str(self),
        }


class CycleBudget:
    """Shared monotonic budget for one accepted Queue Echo slot."""

    MINIMUM_NETWORK_ATTEMPT_SECONDS = 0.05

    def __init__(
        self,
        *,
        cooperative_seconds: float,
        hard_limit_seconds: float,
        network_stop_margin_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        started_clock: float | None = None,
    ) -> None:
        if not (
            0 < network_stop_margin_seconds
            < cooperative_seconds
            < hard_limit_seconds
        ):
            raise ValueError("cycle budget ordering is invalid")
        self.cooperative_seconds = float(cooperative_seconds)
        self.hard_limit_seconds = float(hard_limit_seconds)
        self.network_stop_margin_seconds = float(network_stop_margin_seconds)
        self.clock = clock
        self.started_clock = clock() if started_clock is None else started_clock

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.clock() - self.started_clock)

    @property
    def cooperative_remaining_seconds(self) -> float:
        return max(0.0, self.cooperative_seconds - self.elapsed_seconds)

    @property
    def hard_remaining_seconds(self) -> float:
        return max(0.0, self.hard_limit_seconds - self.elapsed_seconds)

    @property
    def network_remaining_seconds(self) -> float:
        network_deadline = self.cooperative_seconds - self.network_stop_margin_seconds
        return max(0.0, network_deadline - self.elapsed_seconds)

    def _exceeded(self, phase: str, message: str) -> CycleBudgetExceeded:
        return CycleBudgetExceeded(
            message,
            phase=phase,
            elapsed_seconds=self.elapsed_seconds,
            cooperative_remaining_seconds=self.cooperative_remaining_seconds,
            network_remaining_seconds=self.network_remaining_seconds,
            hard_remaining_seconds=self.hard_remaining_seconds,
        )

    def checkpoint(self, phase: str, *, reserve_seconds: float = 0.0) -> None:
        if self.hard_remaining_seconds <= 0:
            raise self._exceeded(phase, "hard cycle limit reached")
        if self.cooperative_remaining_seconds <= max(0.0, reserve_seconds):
            raise self._exceeded(phase, "cooperative cycle budget exhausted")

    def request_timeout(
        self,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        *,
        phase: str,
    ) -> tuple[tuple[float, float], float]:
        available = self.network_remaining_seconds
        minimum = self.MINIMUM_NETWORK_ATTEMPT_SECONDS
        if available <= minimum:
            raise self._exceeded(phase, "network stop margin reached")
        connect = min(float(connect_timeout_seconds), available / 2)
        connect = max(0.001, connect)
        read = min(float(read_timeout_seconds), available - connect)
        read = max(0.001, read)
        if connect + read > available:
            read = max(0.001, available - connect)
        if connect + read > available + 1e-9:
            raise self._exceeded(phase, "remaining budget cannot fund HTTP timeout")
        return (connect, read), available

    def sleep_before_retry(
        self,
        delay_seconds: float,
        *,
        phase: str,
        sleep: Callable[[float], None],
    ) -> None:
        delay = max(0.0, float(delay_seconds))
        affordable = (
            self.network_remaining_seconds
            - self.MINIMUM_NETWORK_ATTEMPT_SECONDS
        )
        if delay > affordable:
            raise self._exceeded(
                phase,
                "retry delay or Retry-After exceeds remaining network budget",
            )
        sleep(delay)

    def terminal_evidence(self, *, status: str, phase: str) -> dict[str, Any]:
        return {
            "terminal_status": status,
            "terminal_phase": phase,
            "duration_seconds": round(self.elapsed_seconds, 6),
            "cooperative_cycle_budget_seconds": self.cooperative_seconds,
            "hard_cycle_limit_seconds": self.hard_limit_seconds,
            "network_stop_margin_seconds": self.network_stop_margin_seconds,
            "cooperative_remaining_seconds": round(
                self.cooperative_remaining_seconds, 6
            ),
            "hard_remaining_seconds": round(self.hard_remaining_seconds, 6),
        }


def _retry_after(response: Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        parsed = max(0.0, (retry_at.astimezone(timezone.utc) - utc_now()).total_seconds())
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


class PublicJsonTransport:
    """A credential-free HTTP transport with one immutable receipt per attempt."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_retries: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        receipt_sink: ReceiptSink,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        budget: CycleBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = (connect_timeout_seconds, read_timeout_seconds)
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.receipt_sink = receipt_sink
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "golden-raspberry-queue-echo/0.3",
            }
        )
        self.sleep = sleep
        self.budget = budget
        self.clock = clock

    def request_json(
        self,
        method: str,
        url: str,
        *,
        request_kind: str,
        run_id: str,
        page_number: int | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        logical_request_id: str | None = None,
        before_first_attempt: BeforeFirstAttempt | None = None,
    ) -> JsonResponse:
        method = method.upper()
        logical_id = logical_request_id or uuid4().hex
        body_bytes = (
            json.dumps(json_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if json_body is not None
            else None
        )
        last_error: BaseException | None = None
        last_status: int | None = None
        for attempt in range(1, self.max_retries + 2):
            timeout = self.timeout
            budget_before = None
            if self.budget is not None:
                timeout, budget_before = self.budget.request_timeout(
                    self.timeout[0],
                    self.timeout[1],
                    phase=f"{request_kind}:attempt:{attempt}",
                )
            request_id = uuid4().hex
            started_at = iso_utc()
            if attempt == 1 and before_first_attempt is not None:
                before_first_attempt(logical_id, started_at)
                if self.budget is not None:
                    timeout, budget_before = self.budget.request_timeout(
                        self.timeout[0],
                        self.timeout[1],
                        phase=f"{request_kind}:attempt:{attempt}:after_claim",
                    )
            started_clock = self.clock()
            response: Response | None = None
            raw = b""
            status = "ERROR"
            error_type: str | None = None
            error_message: str | None = None
            retryable = False
            retry_after = None
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    data=body_bytes,
                    headers={"Content-Type": "application/json"}
                    if body_bytes is not None
                    else None,
                    timeout=timeout,
                )
                last_status = response.status_code
                raw = response.content
                response.raise_for_status()
                payload = response.json()
                status = "SUCCESS"
                completed_at = iso_utc()
                digest = hashlib.sha256(raw).hexdigest()
                self.receipt_sink(
                    {
                        "request_id": request_id,
                        "logical_request_id": logical_id,
                        "run_id": run_id,
                        "request_kind": request_kind,
                        "page_number": page_number,
                        "attempt_number": attempt,
                        "method": method,
                        "url": url,
                        "params_json": json.dumps(params or {}, sort_keys=True, separators=(",", ":")),
                        "body_sha256": hashlib.sha256(body_bytes).hexdigest() if body_bytes is not None else None,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "elapsed_ms": round((self.clock() - started_clock) * 1000, 3),
                        "timeout_connect_seconds": timeout[0],
                        "timeout_read_seconds": timeout[1],
                        "budget_remaining_before_seconds": budget_before,
                        "status": status,
                        "http_status": response.status_code,
                        "retryable": 0,
                        "retry_after_seconds": None,
                        "response_sha256": digest,
                        "response_bytes": len(raw),
                        "error_type": None,
                        "error_message": None,
                    }
                )
                return JsonResponse(
                    payload,
                    raw,
                    request_id,
                    started_at,
                    completed_at,
                    digest,
                    logical_id,
                )
            except (RequestException, ChunkedEncodingError, ValueError) as error:
                last_error = error
                error_type = type(error).__name__
                error_message = str(error)[:500]
                code = response.status_code if response is not None else None
                retryable = code is None or code == 429 or 500 <= code < 600
                retry_after = _retry_after(response)
            finally:
                if status != "SUCCESS":
                    self.receipt_sink(
                        {
                            "request_id": request_id,
                            "logical_request_id": logical_id,
                            "run_id": run_id,
                            "request_kind": request_kind,
                            "page_number": page_number,
                            "attempt_number": attempt,
                            "method": method,
                            "url": url,
                            "params_json": json.dumps(params or {}, sort_keys=True, separators=(",", ":")),
                            "body_sha256": hashlib.sha256(body_bytes).hexdigest() if body_bytes is not None else None,
                            "started_at": started_at,
                            "completed_at": iso_utc(),
                            "elapsed_ms": round((self.clock() - started_clock) * 1000, 3),
                            "timeout_connect_seconds": timeout[0],
                            "timeout_read_seconds": timeout[1],
                            "budget_remaining_before_seconds": budget_before,
                            "status": "ERROR",
                            "http_status": response.status_code if response is not None else None,
                            "retryable": int(retryable),
                            "retry_after_seconds": retry_after,
                            "response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                            "response_bytes": len(raw),
                            "error_type": error_type,
                            "error_message": error_message,
                        }
                    )
            if not retryable or attempt > self.max_retries:
                break
            delay = retry_after
            if delay is None:
                delay = min(
                    self.retry_max_seconds,
                    self.retry_base_seconds * (2 ** (attempt - 1)),
                )
            if self.budget is None:
                self.sleep(delay)
            else:
                self.budget.sleep_before_retry(
                    delay,
                    phase=f"{request_kind}:retry_sleep:{attempt}",
                    sleep=self.sleep,
                )
        raise PublicApiError(
            f"{request_kind} failed after bounded retries: {type(last_error).__name__ if last_error else 'unknown'}",
            http_status=last_status,
        ) from last_error


__all__ = [
    "JsonResponse",
    "CycleBudget",
    "CycleBudgetExceeded",
    "PublicApiError",
    "PublicJsonTransport",
    "iso_utc",
    "utc_now",
]
