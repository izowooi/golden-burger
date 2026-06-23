from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from slack_data_collector.collector import CollectedMessage, CollectionResult
from slack_data_collector.storage import store_collection
from slack_data_collector.time_range import TimeRange


class StorageTests(unittest.TestCase):
    def test_writes_raw_normalized_and_manifest_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CollectionResult(
                messages=[
                    CollectedMessage(
                        source_method="conversations.history",
                        message={
                            "type": "message",
                            "ts": "1704153600.000001",
                            "text": "hello",
                        },
                    )
                ]
            )
            result.stats.messages_collected = 1
            stored = store_collection(
                result,
                output_root=Path(directory),
                workspace_id="T123",
                channel_id="C123",
                channel_name="daily-report",
                time_range=TimeRange.from_dates("2024-01-02", "2024-01-02", "UTC"),
                complete_thread_scan=True,
            )

            raw = json.loads(stored.raw_messages.read_text(encoding="utf-8"))
            normalized = json.loads(
                stored.normalized_messages.read_text(encoding="utf-8")
            )
            manifest_text = stored.manifest.read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)

        self.assertEqual(raw["message"]["text"], "hello")
        self.assertEqual(normalized["message_ts"], "1704153600.000001")
        self.assertEqual(manifest["stats"]["messages_collected"], 1)
        self.assertNotIn("token", manifest_text)
