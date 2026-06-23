from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from slack_data_collector.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_loads_ignored_local_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "SLACK_BOT_TOKEN='xoxb-placeholder'\nSLACK_CHANNEL_ID=C123\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_environment(env_file)

        self.assertEqual(settings.bot_token, "xoxb-placeholder")
        self.assertEqual(settings.channel_id, "C123")

    def test_rejects_non_bot_token(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {"SLACK_BOT_TOKEN": "xapp-placeholder", "SLACK_CHANNEL_ID": "C123"},
                clear=True,
            ),
        ):
            with self.assertRaises(ConfigurationError):
                Settings.from_environment(Path(directory) / ".env")
