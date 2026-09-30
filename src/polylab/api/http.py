"""Shared HTTP plumbing for the public Polymarket REST APIs.

- one `requests.Session` per process (connection reuse), honouring HTTPS_PROXY/HTTP_PROXY/ALL_PROXY
  (socks5h:// proxies need PySocks, i.e. `requests[socks]`; only the MacBook tunnel uses that)
- polite client-side rate limiting per endpoint bucket, set to half of the documented
  Cloudflare limits (docs/research/api-sources.md "Rate limits": requests per 10 s)
- retries with exponential backoff on connection errors, timeouts, 429 and 5xx (Retry-After honoured)

Tests replace `default_client()` / `Client.request` via monkeypatch; nothing here does IO at import.
"""

from __future__ import annotations

import os
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"

USER_AGENT = "polylab-collector/0.1 (+research; read-only)"

# bucket -> documented requests per 10 s (api-sources.md). We use half of it.
DOCUMENTED_LIMITS_PER_10S = {
    "gamma": 4000,
    "gamma_events": 500,
    "gamma_markets": 300,
    "data": 800,
    "data_trades": 300,
    "data_prices_history": 200,
    "clob": 9000,
    "clob_book": 1500,
    "clob_books": 500,
    "clob_midpoint": 1500,
    "clob_prices_history": 1000,
}
POLITE_FRACTION = 0.5
RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}


class ApiError(RuntimeError):
    def __init__(self, status: int | None, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body


class RateLimiter:
    """Sliding-window limiter: at most `limit` calls in any `window` seconds."""

    def __init__(self, limit: int, window: float = 10.0):
        self.limit = max(1, int(limit))
        self.window = window
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.calls and now - self.calls[0] > self.window:
                    self.calls.popleft()
                if len(self.calls) < self.limit:
                    self.calls.append(now)
                    return
                wait = self.window - (now - self.calls[0]) + 0.01
            time.sleep(max(wait, 0.01))


def _proxies_from_env() -> dict[str, str] | None:
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"))
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


@dataclass
class Client:
    timeout: tuple[float, float] = (5.0, 25.0)
    max_retries: int = 4
    backoff_base: float = 0.5
    session: requests.Session = field(default_factory=requests.Session)
    limiters: dict[str, RateLimiter] = field(default_factory=dict)
    calls: int = 0
    bytes_in: int = 0

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        proxies = _proxies_from_env()
        if proxies:
            self.session.proxies.update(proxies)
        for key, per10 in DOCUMENTED_LIMITS_PER_10S.items():
            self.limiters.setdefault(key, RateLimiter(int(per10 * POLITE_FRACTION)))

    def request(self, method: str, url: str, *, bucket: str, params: Any = None,
                json_body: Any = None, allow_404: bool = False) -> Any:
        """Return decoded JSON (or None for an allowed 404). Raises ApiError after retries."""
        limiter = self.limiters.get(bucket) or self.limiters["gamma"]
        attempt = 0
        while True:
            limiter.acquire()
            try:
                resp = self.session.request(method, url, params=params, json=json_body, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    raise ApiError(None, url, repr(exc)) from exc
                self._sleep(attempt, None)
                attempt += 1
                continue
            self.calls += 1
            self.bytes_in += len(resp.content or b"")
            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                self._sleep(attempt, resp.headers.get("Retry-After"))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise ApiError(resp.status_code, resp.url, resp.text)
            try:
                return resp.json()
            except ValueError as exc:
                raise ApiError(resp.status_code, resp.url, "non-json body: " + resp.text[:200]) from exc

    def get(self, url: str, *, bucket: str, params: Any = None, allow_404: bool = False) -> Any:
        return self.request("GET", url, bucket=bucket, params=params, allow_404=allow_404)

    def post(self, url: str, *, bucket: str, json_body: Any, allow_404: bool = False) -> Any:
        return self.request("POST", url, bucket=bucket, json_body=json_body, allow_404=allow_404)

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff_base * (2 ** attempt) + random.uniform(0, 0.25)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(min(delay, 30.0))


_default: Client | None = None
_default_lock = threading.Lock()


def default_client() -> Client:
    global _default
    with _default_lock:
        if _default is None:
            _default = Client()
        return _default


def set_default_client(client: Client | None) -> None:
    """Tests inject a fake client here."""
    global _default
    with _default_lock:
        _default = client
