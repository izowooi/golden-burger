from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from slack_data_collector.client import JsonObject, SlackPage
from slack_data_collector.time_range import TimeRange


class SlackMessageSource(Protocol):
    def history_pages(
        self,
        channel_id: str,
        *,
        oldest: str | None,
        latest: str,
        limit: int = 200,
    ) -> Any: ...

    def replies_pages(
        self,
        channel_id: str,
        thread_ts: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 200,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CollectedMessage:
    source_method: str
    message: JsonObject


@dataclass(slots=True)
class CollectionStats:
    history_pages: int = 0
    history_messages_scanned: int = 0
    thread_parents_scanned: int = 0
    reply_pages: int = 0
    replies_scanned: int = 0
    messages_collected: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "history_pages": self.history_pages,
            "history_messages_scanned": self.history_messages_scanned,
            "thread_parents_scanned": self.thread_parents_scanned,
            "reply_pages": self.reply_pages,
            "replies_scanned": self.replies_scanned,
            "messages_collected": self.messages_collected,
        }


@dataclass(slots=True)
class CollectionResult:
    messages: list[CollectedMessage] = field(default_factory=list)
    stats: CollectionStats = field(default_factory=CollectionStats)


class SlackChannelCollector:
    def __init__(self, client: SlackMessageSource) -> None:
        self._client = client

    def collect(
        self,
        channel_id: str,
        time_range: TimeRange,
        *,
        include_threads: bool = True,
        complete_thread_scan: bool = True,
    ) -> CollectionResult:
        result = CollectionResult()
        by_timestamp: dict[str, CollectedMessage] = {}
        thread_parents: dict[str, JsonObject] = {}

        history_oldest = (
            None if include_threads and complete_thread_scan else time_range.oldest
        )
        for page in self._client.history_pages(
            channel_id,
            oldest=history_oldest,
            latest=time_range.latest,
        ):
            self._process_history_page(
                page, time_range, by_timestamp, thread_parents, result.stats
            )

        if include_threads:
            for thread_ts in sorted(thread_parents, key=self._timestamp_sort_key):
                result.stats.thread_parents_scanned += 1
                for page in self._client.replies_pages(
                    channel_id,
                    thread_ts,
                    oldest=time_range.oldest,
                    latest=time_range.latest,
                ):
                    result.stats.reply_pages += 1
                    result.stats.replies_scanned += len(page.messages)
                    for message in page.messages:
                        ts = self._message_ts(message)
                        if ts and time_range.contains_ts(ts):
                            by_timestamp.setdefault(
                                ts,
                                CollectedMessage(
                                    source_method="conversations.replies",
                                    message=message,
                                ),
                            )

        result.messages = sorted(
            by_timestamp.values(),
            key=lambda item: self._timestamp_sort_key(self._message_ts(item.message)),
        )
        result.stats.messages_collected = len(result.messages)
        return result

    @staticmethod
    def _process_history_page(
        page: SlackPage,
        time_range: TimeRange,
        by_timestamp: dict[str, CollectedMessage],
        thread_parents: dict[str, JsonObject],
        stats: CollectionStats,
    ) -> None:
        stats.history_pages += 1
        stats.history_messages_scanned += len(page.messages)
        for message in page.messages:
            ts = SlackChannelCollector._message_ts(message)
            if not ts:
                continue
            if time_range.contains_ts(ts):
                by_timestamp.setdefault(
                    ts,
                    CollectedMessage(
                        source_method="conversations.history", message=message
                    ),
                )
            if SlackChannelCollector._thread_may_overlap(message, time_range):
                thread_parents[ts] = message

    @staticmethod
    def _thread_may_overlap(message: JsonObject, time_range: TimeRange) -> bool:
        if not message.get("reply_count"):
            return False
        latest_reply = message.get("latest_reply")
        if not isinstance(latest_reply, str):
            return True
        try:
            return Decimal(latest_reply) >= Decimal(time_range.oldest)
        except InvalidOperation:
            return True

    @staticmethod
    def _message_ts(message: JsonObject) -> str:
        value = message.get("ts")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _timestamp_sort_key(timestamp: str) -> Decimal:
        try:
            return Decimal(timestamp)
        except InvalidOperation:
            return Decimal(0)
