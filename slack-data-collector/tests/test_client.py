from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError

from slack_data_collector.client import SlackWebClient


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class SlackWebClientTests(unittest.TestCase):
    def test_history_follows_cursor_without_putting_token_in_url(self) -> None:
        requests = []
        responses = iter(
            [
                FakeResponse(
                    {
                        "ok": True,
                        "messages": [{"ts": "1.0"}],
                        "response_metadata": {"next_cursor": "next"},
                    }
                ),
                FakeResponse(
                    {
                        "ok": True,
                        "messages": [{"ts": "2.0"}],
                        "response_metadata": {"next_cursor": ""},
                    }
                ),
            ]
        )

        def opener(request: object, **_: object) -> FakeResponse:
            requests.append(request)
            return next(responses)

        client = SlackWebClient("xoxb-test", opener=opener)
        pages = list(client.history_pages("C123", oldest="1", latest="3"))

        self.assertEqual(
            [message["ts"] for page in pages for message in page.messages],
            ["1.0", "2.0"],
        )
        self.assertIn("cursor=next", requests[1].full_url)
        self.assertNotIn("xoxb-test", requests[0].full_url)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer xoxb-test")

    def test_rate_limit_honors_retry_after(self) -> None:
        waits: list[float] = []
        calls = 0

        def opener(request: object, **_: object) -> FakeResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "2"},
                    io.BytesIO(b"{}"),
                )
            return FakeResponse({"ok": True})

        client = SlackWebClient("xoxb-test", opener=opener, sleeper=waits.append)
        payload = client.auth_test()

        self.assertTrue(payload["ok"])
        self.assertEqual(waits, [2.0])
