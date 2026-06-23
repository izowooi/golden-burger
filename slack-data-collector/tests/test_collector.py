from __future__ import annotations

import unittest

from slack_data_collector.client import SlackPage
from slack_data_collector.collector import SlackChannelCollector
from slack_data_collector.time_range import TimeRange


class FakeClient:
    def __init__(self) -> None:
        self.history_oldest: str | None = "unset"
        self.reply_threads: list[str] = []

    def history_pages(
        self, channel_id: str, *, oldest: str | None, latest: str, limit: int = 200
    ):
        self.history_oldest = oldest
        yield SlackPage(
            method="conversations.history",
            messages=(
                {
                    "type": "message",
                    "ts": "1704060000.000001",
                    "reply_count": 1,
                    "latest_reply": "1704153600.000001",
                    "text": "old parent",
                },
                {"type": "message", "ts": "1704153600.000002", "text": "in range"},
            ),
            response_metadata={},
        )

    def replies_pages(
        self,
        channel_id: str,
        thread_ts: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 200,
    ):
        self.reply_threads.append(thread_ts)
        yield SlackPage(
            method="conversations.replies",
            messages=(
                {"type": "message", "ts": thread_ts, "text": "old parent"},
                {
                    "type": "message",
                    "ts": "1704153600.000001",
                    "thread_ts": thread_ts,
                    "text": "reply in range",
                },
            ),
            response_metadata={},
        )


class SlackChannelCollectorTests(unittest.TestCase):
    def test_complete_scan_collects_reply_on_parent_before_period(self) -> None:
        client = FakeClient()
        collector = SlackChannelCollector(client)
        time_range = TimeRange.from_dates("2024-01-02", "2024-01-02", "UTC")

        result = collector.collect("C123", time_range)

        self.assertIsNone(client.history_oldest)
        self.assertEqual(client.reply_threads, ["1704060000.000001"])
        self.assertEqual(
            [item.message["text"] for item in result.messages],
            ["reply in range", "in range"],
        )
        self.assertEqual(result.stats.messages_collected, 2)

    def test_no_threads_limits_history_to_requested_period(self) -> None:
        client = FakeClient()
        collector = SlackChannelCollector(client)
        time_range = TimeRange.from_dates("2024-01-02", "2024-01-02", "UTC")

        collector.collect("C123", time_range, include_threads=False)

        self.assertEqual(client.history_oldest, time_range.oldest)
        self.assertEqual(client.reply_threads, [])
