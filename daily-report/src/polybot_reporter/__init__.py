"""Polymarket Daily Portfolio Reporter.

A system for generating daily portfolio reports from multiple
Polymarket trading accounts and sending them to Slack.
"""

__version__ = "0.1.0"
__author__ = "izowooi"
__email__ = "izowooi@hotmail.com"

from .api.data_api_client import DataAPIClient
from .notifications.slack_notifier import SlackNotifier

__all__ = [
    "DataAPIClient",
    "SlackNotifier",
    "__version__",
]
