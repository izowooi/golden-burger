from __future__ import annotations

import html
import re
from typing import Any

from slack_data_collector.collector import CollectedMessage
from slack_data_collector.time_range import TimeRange


_LINK_RE = re.compile(r"<(https?://[^>|]+|mailto:[^>|]+)(?:\|([^>]+))?>")
_USER_RE = re.compile(r"<@([A-Z0-9]+)>")
_CHANNEL_RE = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]+))?>")
_SPECIAL_RE = re.compile(r"<![^>|]+(?:\|([^>]+))?>")


def slack_text_to_plain(text: str) -> str:
    """Convert common Slack mrkdwn entities to stable, readable text."""

    text = _LINK_RE.sub(lambda match: match.group(2) or match.group(1), text)
    text = _USER_RE.sub(lambda match: f"@{match.group(1)}", text)
    text = _CHANNEL_RE.sub(lambda match: f"#{match.group(2) or match.group(1)}", text)
    text = _SPECIAL_RE.sub(lambda match: match.group(1) or "", text)
    return html.unescape(text)


def normalize_message(
    collected: CollectedMessage,
    *,
    workspace_id: str,
    channel_id: str,
    time_range: TimeRange,
    collected_at: str,
) -> dict[str, Any]:
    message = collected.message
    ts = _string(message.get("ts"))
    thread_ts = _string(message.get("thread_ts"))
    parent_ts = thread_ts or ts
    text = _string(message.get("text"))
    edited = message.get("edited") if isinstance(message.get("edited"), dict) else {}

    return {
        "schema_version": 1,
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "message_ts": ts,
        "message_datetime_utc": time_range.to_utc_iso(ts),
        "message_date_local": time_range.to_local_date(ts),
        "thread_ts": parent_ts,
        "is_thread_reply": bool(thread_ts and thread_ts != ts),
        "source_method": collected.source_method,
        "message_type": _string(message.get("type")) or "message",
        "subtype": _nullable_string(message.get("subtype")),
        "client_msg_id": _nullable_string(message.get("client_msg_id")),
        "user_id": _nullable_string(message.get("user")),
        "bot_id": _nullable_string(message.get("bot_id")),
        "app_id": _nullable_string(message.get("app_id")),
        "username": _nullable_string(message.get("username")),
        "text": text,
        "text_plain": slack_text_to_plain(text),
        "edited_ts": _nullable_string(edited.get("ts")),
        "reply_count": _integer(message.get("reply_count")),
        "reply_users": _list(message.get("reply_users")),
        "blocks": _list(message.get("blocks")),
        "attachments": _list(message.get("attachments")),
        "files": _list(message.get("files")),
        "reactions": _list(message.get("reactions")),
        "metadata": message.get("metadata")
        if isinstance(message.get("metadata"), dict)
        else None,
        "collected_at": collected_at,
    }


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _nullable_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
