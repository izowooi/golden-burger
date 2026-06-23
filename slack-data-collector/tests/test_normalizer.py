from __future__ import annotations

import unittest

from slack_data_collector.collector import CollectedMessage
from slack_data_collector.normalizer import normalize_message, slack_text_to_plain
from slack_data_collector.time_range import TimeRange


class NormalizerTests(unittest.TestCase):
    def test_converts_common_slack_entities(self) -> None:
        text = "<@U123> see <https://example.com|report> in <#C123|daily-report> &amp; review"
        self.assertEqual(
            slack_text_to_plain(text),
            "@U123 see report in #daily-report & review",
        )

    def test_normalizes_thread_reply(self) -> None:
        time_range = TimeRange.from_dates("2024-01-02", "2024-01-02", "UTC")
        collected = CollectedMessage(
            source_method="conversations.replies",
            message={
                "type": "message",
                "ts": "1704153600.000001",
                "thread_ts": "1704140000.000001",
                "user": "U123",
                "text": "hello",
            },
        )

        normalized = normalize_message(
            collected,
            workspace_id="T123",
            channel_id="C123",
            time_range=time_range,
            collected_at="2024-01-03T00:00:00+00:00",
        )

        self.assertTrue(normalized["is_thread_reply"])
        self.assertEqual(normalized["thread_ts"], "1704140000.000001")
        self.assertEqual(normalized["message_date_local"], "2024-01-02")
