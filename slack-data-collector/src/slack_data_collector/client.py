from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


JsonObject = dict[str, Any]


class SlackApiError(RuntimeError):
    """Raised when Slack rejects or cannot complete an API request."""

    def __init__(self, method: str, error: str, detail: str | None = None) -> None:
        message = f"Slack API {method} 실패: {error}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.method = method
        self.error = error
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SlackPage:
    method: str
    messages: tuple[JsonObject, ...]
    response_metadata: JsonObject


class SlackWebClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://slack.com/api",
        timeout_seconds: float = 30.0,
        max_retries: int = 5,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._opener = opener
        self._sleeper = sleeper

    def auth_test(self) -> JsonObject:
        return self._call("auth.test")

    def conversation_info(self, channel_id: str) -> JsonObject:
        return self._call("conversations.info", {"channel": channel_id})

    def history_pages(
        self,
        channel_id: str,
        *,
        oldest: str | None,
        latest: str,
        limit: int = 200,
    ) -> Iterator[SlackPage]:
        params: dict[str, str | int | bool] = {
            "channel": channel_id,
            "latest": latest,
            "inclusive": True,
            "limit": limit,
            "include_all_metadata": True,
        }
        if oldest is not None:
            params["oldest"] = oldest
        yield from self._message_pages("conversations.history", params)

    def replies_pages(
        self,
        channel_id: str,
        thread_ts: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 200,
    ) -> Iterator[SlackPage]:
        params: dict[str, str | int | bool] = {
            "channel": channel_id,
            "ts": thread_ts,
            "oldest": oldest,
            "latest": latest,
            "inclusive": True,
            "limit": limit,
            "include_all_metadata": True,
        }
        yield from self._message_pages("conversations.replies", params)

    def _message_pages(
        self, method: str, initial_params: Mapping[str, str | int | bool]
    ) -> Iterator[SlackPage]:
        cursor = ""
        while True:
            params = dict(initial_params)
            if cursor:
                params["cursor"] = cursor

            payload = self._call(method, params)
            raw_messages = payload.get("messages", [])
            if not isinstance(raw_messages, list):
                raise SlackApiError(
                    method, "invalid_response", "messages가 배열이 아닙니다"
                )
            messages = tuple(
                message for message in raw_messages if isinstance(message, dict)
            )
            metadata = payload.get("response_metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            yield SlackPage(
                method=method, messages=messages, response_metadata=metadata
            )

            cursor = str(metadata.get("next_cursor") or "").strip()
            if not cursor:
                return

    def _call(
        self, method: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        query = urlencode(params or {})
        url = f"{self._base_url}/{method}"
        if query:
            url = f"{url}?{query}"

        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "slack-data-collector/0.1.0",
            },
            method="GET",
        )

        for attempt in range(self._max_retries + 1):
            try:
                with self._opener(request, timeout=self._timeout_seconds) as response:
                    payload = self._decode_json(method, response.read())
            except HTTPError as exc:
                if exc.code == 429 and attempt < self._max_retries:
                    self._sleeper(self._retry_after(exc.headers, attempt))
                    continue
                if 500 <= exc.code < 600 and attempt < self._max_retries:
                    self._sleeper(self._backoff(attempt))
                    continue
                raise SlackApiError(method, f"http_{exc.code}") from exc
            except (URLError, TimeoutError) as exc:
                if attempt < self._max_retries:
                    self._sleeper(self._backoff(attempt))
                    continue
                detail = getattr(exc, "reason", exc)
                raise SlackApiError(method, "network_error", str(detail)) from exc

            if payload.get("ok") is True:
                return payload

            error = str(payload.get("error") or "unknown_error")
            if error == "ratelimited" and attempt < self._max_retries:
                self._sleeper(self._backoff(attempt))
                continue
            detail = payload.get("needed") or payload.get("warning")
            raise SlackApiError(method, error, str(detail) if detail else None)

        raise SlackApiError(method, "retry_exhausted")

    @staticmethod
    def _decode_json(method: str, raw_body: bytes) -> JsonObject:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SlackApiError(method, "invalid_json") from exc
        if not isinstance(payload, dict):
            raise SlackApiError(
                method, "invalid_response", "최상위 응답이 객체가 아닙니다"
            )
        return payload

    @staticmethod
    def _retry_after(headers: Mapping[str, str], attempt: int) -> float:
        try:
            return max(float(headers.get("Retry-After", "0")), 0.0)
        except ValueError:
            return SlackWebClient._backoff(attempt)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2**attempt + random.random(), 30.0)
