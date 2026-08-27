"""Bounded, credential-free HTTP transport with durable attempt receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

import requests


def iso_utc(value: datetime | None = None) -> str:
    current = (value or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class CycleBudget:
    started_monotonic: float
    cooperative_seconds: float = 225.0
    stop_margin_seconds: float = 30.0
    hard_seconds: float = 240.0
    monotonic: Callable[[], float] = time.monotonic

    @property
    def cooperative_deadline(self) -> float:
        return self.started_monotonic + self.cooperative_seconds

    @property
    def request_stop_at(self) -> float:
        return self.cooperative_deadline - self.stop_margin_seconds

    @property
    def hard_deadline(self) -> float:
        return self.started_monotonic + self.hard_seconds

    def elapsed(self) -> float:
        return max(0.0, self.monotonic() - self.started_monotonic)

    def ensure_can_start_request(self, phase: str) -> None:
        now = self.monotonic()
        if now >= self.hard_deadline:
            raise DeadlineExceeded(f"hard cycle deadline exceeded before {phase}")
        if now >= self.request_stop_at:
            raise DeadlineExceeded(f"cooperative request stop reached before {phase}")

    def timeout(self, connect: float, read: float) -> tuple[float, float]:
        self.ensure_can_start_request("HTTP")
        remaining = max(0.05, self.cooperative_deadline - self.monotonic())
        connect_timeout = max(0.05, min(connect, remaining))
        read_timeout = max(0.05, min(read, remaining - min(connect_timeout, remaining / 2)))
        return connect_timeout, read_timeout

    def assert_within_hard_deadline(self) -> None:
        if self.monotonic() > self.hard_deadline:
            raise DeadlineExceeded("hard cycle deadline exceeded")


class DeadlineExceeded(RuntimeError):
    """The bounded Jenkins cycle no longer has a safe request window."""


class PublicApiError(RuntimeError):
    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


@dataclass(frozen=True)
class JsonResponse:
    request_id: str
    received_at: str
    response_sha256: str
    raw: bytes
    payload: Any
    http_status: int


def _bounded_error(error: BaseException) -> str:
    return str(error).replace("\n", " ")[:500]


class PublicJsonTransport:
    """A requests Session that explicitly ignores proxy/auth environment state."""

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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.receipt_sink = receipt_sink
        self.sleep = sleep
        self.monotonic = monotonic
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.clear()
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": "golden-coconut/0.1"}
        )

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("public transport requires a credential-free HTTPS URL")

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            result = float(raw)
        except ValueError:
            return None
        return result if math.isfinite(result) and result >= 0 else None

    def request_json(
        self,
        method: str,
        url: str,
        *,
        request_kind: str,
        run_id: str,
        budget: CycleBudget,
        family: str | None = None,
        page_number: int | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> JsonResponse:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("public transport supports GET and POST only")
        self._validate_url(url)
        body_json = canonical_json(json_body) if json_body is not None else None
        body_sha256 = (
            hashlib.sha256(body_json.encode("utf-8")).hexdigest()
            if body_json is not None
            else None
        )
        logical_request_id = uuid4().hex
        last_error: BaseException | None = None
        for attempt_number in range(1, self.max_retries + 2):
            budget.ensure_can_start_request(request_kind)
            attempt_id = uuid4().hex
            started_at = iso_utc()
            started_clock = self.monotonic()
            response: requests.Response | None = None
            raw = b""
            status = "ERROR"
            error_type: str | None = None
            error_message: str | None = None
            response_sha256: str | None = None
            try:
                response = self.session.request(
                    method,
                    url,
                    params=dict(params or {}),
                    json=json_body,
                    timeout=budget.timeout(
                        self.connect_timeout_seconds, self.read_timeout_seconds
                    ),
                    allow_redirects=False,
                )
                raw = bytes(response.content)
                response_sha256 = hashlib.sha256(raw).hexdigest()
                if 300 <= response.status_code < 400:
                    raise PublicApiError("public endpoint redirect is forbidden", request_id=logical_request_id)
                if response.status_code >= 400:
                    raise PublicApiError(
                        f"public endpoint returned HTTP {response.status_code}",
                        request_id=logical_request_id,
                    )
                payload = json.loads(raw.decode("utf-8"))
                status = "SUCCESS"
            except (requests.RequestException, UnicodeDecodeError, json.JSONDecodeError, PublicApiError) as error:
                last_error = error
                error_type = type(error).__name__
                error_message = _bounded_error(error)
                payload = None
            completed_at = iso_utc()
            self.receipt_sink(
                {
                    "api_attempt_id": attempt_id,
                    "logical_request_id": logical_request_id,
                    "run_id": run_id,
                    "request_kind": request_kind,
                    "sport_family": family,
                    "page_number": page_number,
                    "attempt_number": attempt_number,
                    "method": method,
                    "url": url,
                    "params_json": canonical_json(dict(params or {})),
                    "body_sha256": body_sha256,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "elapsed_ms": max(0.0, (self.monotonic() - started_clock) * 1000),
                    "status": status,
                    "http_status": response.status_code if response is not None else None,
                    "response_sha256": response_sha256,
                    "response_bytes": len(raw),
                    "error_type": error_type,
                    "error_message": error_message,
                }
            )
            if status == "SUCCESS":
                return JsonResponse(
                    request_id=logical_request_id,
                    received_at=completed_at,
                    response_sha256=str(response_sha256),
                    raw=raw,
                    payload=payload,
                    http_status=int(response.status_code),
                )
            retryable = response is None or response.status_code in {408, 425, 429, 500, 502, 503, 504}
            if not retryable or attempt_number > self.max_retries:
                break
            retry_after = self._retry_after(response) if response is not None else None
            delay = min(
                self.retry_max_seconds,
                retry_after if retry_after is not None else self.retry_base_seconds * 2 ** (attempt_number - 1),
            )
            if self.monotonic() + delay >= budget.request_stop_at:
                break
            self.sleep(delay)
        raise PublicApiError(
            f"{request_kind} failed after bounded retries: {_bounded_error(last_error or RuntimeError('unknown'))}",
            request_id=logical_request_id,
        )


def receipt_skew_seconds(values: list[str]) -> float:
    parsed: list[datetime] = []
    for value in values:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("receipt timestamps must include a timezone")
        parsed.append(timestamp.astimezone(timezone.utc))
    if len(parsed) < 2:
        return 0.0
    return max(0.0, (max(parsed) - min(parsed)).total_seconds())
