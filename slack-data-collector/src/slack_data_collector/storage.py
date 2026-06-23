from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from slack_data_collector.collector import CollectionResult
from slack_data_collector.normalizer import normalize_message
from slack_data_collector.time_range import TimeRange


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_directory: Path
    raw_messages: Path
    normalized_messages: Path
    manifest: Path


def store_collection(
    result: CollectionResult,
    *,
    output_root: Path,
    workspace_id: str,
    channel_id: str,
    channel_name: str | None,
    time_range: TimeRange,
    complete_thread_scan: bool,
) -> StoredRun:
    now = datetime.now(UTC)
    collected_at = now.isoformat()
    run_name = (
        f"{time_range.start_date}_{time_range.end_date}_"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}"
    )
    run_directory = output_root / run_name
    raw_path = run_directory / "raw" / "messages.jsonl"
    normalized_path = run_directory / "normalized" / "messages.jsonl"
    manifest_path = run_directory / "manifest.json"
    raw_path.parent.mkdir(parents=True, exist_ok=False)
    normalized_path.parent.mkdir(parents=True, exist_ok=False)

    with (
        raw_path.open("w", encoding="utf-8") as raw_file,
        normalized_path.open("w", encoding="utf-8") as normalized_file,
    ):
        for collected in result.messages:
            raw_record = {
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "source_method": collected.source_method,
                "collected_at": collected_at,
                "message": collected.message,
            }
            normalized_record = normalize_message(
                collected,
                workspace_id=workspace_id,
                channel_id=channel_id,
                time_range=time_range,
                collected_at=collected_at,
            )
            raw_file.write(_json_line(raw_record))
            normalized_file.write(_json_line(normalized_record))

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "collected_at": collected_at,
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "period": {
            "start_date": time_range.start_date,
            "end_date": time_range.end_date,
            "timezone": time_range.timezone_name,
            "oldest": time_range.oldest,
            "latest_exclusive": time_range.latest,
        },
        "complete_thread_scan": complete_thread_scan,
        "stats": result.stats.to_dict(),
        "files": {
            "raw_messages": str(raw_path.relative_to(run_directory)),
            "normalized_messages": str(normalized_path.relative_to(run_directory)),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return StoredRun(
        run_directory=run_directory,
        raw_messages=raw_path,
        normalized_messages=normalized_path,
        manifest=manifest_path,
    )


def _json_line(value: dict[str, Any]) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    )
