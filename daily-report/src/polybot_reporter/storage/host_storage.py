"""Collect mounted filesystem capacity and persist one daily Supabase snapshot."""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from supabase import create_client

from polybot_reporter.contracts import safe_error_message
from polybot_reporter.storage.supabase_writer import (
    SupabaseConfigurationError,
    SupabasePortfolioWriter,
)

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "pb-storage/v1"
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


class HostStorageError(RuntimeError):
    """Raised when a host capacity snapshot is unsafe or invalid."""


class HostStorageWriteError(RuntimeError):
    """Raised when Supabase cannot persist a complete host snapshot."""


class DiskUsage(NamedTuple):
    """Portable subset returned by ``shutil.disk_usage``."""

    total: int
    used: int
    free: int


@dataclass(frozen=True)
class MountSpec:
    """One explicitly named mount point to monitor."""

    mount_id: str
    path: Path
    label: str


@dataclass(frozen=True)
class DiskSnapshot:
    """Capacity evidence collected from one mounted filesystem."""

    mount_id: str
    mount_label: str
    mount_path: str
    total_bytes: int
    used_bytes: int
    available_bytes: int

    @property
    def utilization_percent(self) -> float:
        return self.used_bytes / self.total_bytes * 100

    def as_rpc_payload(self) -> dict[str, object]:
        return {
            "mount_id": self.mount_id,
            "mount_label": self.mount_label,
            "mount_path": self.mount_path,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "available_bytes": self.available_bytes,
        }


@dataclass(frozen=True)
class StorageWriteResult:
    """Validated summary returned by the atomic storage writer RPC."""

    report_date: str
    host_id: str
    mount_count: int
    reported_at: str


class SupabaseHostStorageWriter:
    """Write one complete host/day snapshot through a versioned Supabase RPC."""

    PREFLIGHT_RPC = "pb_storage_writer_preflight_v1"
    SNAPSHOT_RPC = "pb_write_host_storage_snapshot_v1"

    def __init__(
        self,
        url: str | None = None,
        secret_key: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self.client = client
            return

        resolved_url = url or os.getenv("SUPABASE_URL")
        resolved_key = secret_key or os.getenv("SUPABASE_SECRET_KEY")
        if not resolved_url or not resolved_key:
            missing = [
                name
                for name, value in (
                    ("SUPABASE_URL", resolved_url),
                    ("SUPABASE_SECRET_KEY", resolved_key),
                )
                if not value
            ]
            raise SupabaseConfigurationError(
                f"필수 Supabase 환경변수가 없습니다: {', '.join(missing)}"
            )

        validated_key = SupabasePortfolioWriter._validate_server_key(resolved_key)
        try:
            self.client = create_client(resolved_url, validated_key)
        except Exception as exc:
            raise SupabaseConfigurationError(
                "Supabase client 초기화 실패: " + safe_error_message(exc)
            ) from exc

    def check_connection(self) -> str:
        """Verify that the versioned table/RPC contract is installed."""
        try:
            response = self.client.rpc(self.PREFLIGHT_RPC, {}).execute()
        except Exception as exc:
            raise self._write_error("Supabase storage preflight 실패", exc) from exc
        payload = self._rpc_object(response.data, "storage preflight")
        if payload.get("contract_version") != SCHEMA_VERSION:
            raise HostStorageWriteError("Supabase storage contract version이 일치하지 않습니다")
        return SCHEMA_VERSION

    def write_snapshot(
        self,
        *,
        report_date: str,
        reported_at: datetime,
        host_id: str,
        snapshots: Sequence[DiskSnapshot],
    ) -> StorageWriteResult:
        """Atomically upsert every requested mount for one host/day."""
        validate_identifier(host_id, "host_id")
        if not snapshots:
            raise HostStorageWriteError("적재할 mount snapshot이 없습니다")
        if reported_at.tzinfo is None:
            raise HostStorageWriteError("reported_at은 timezone-aware 값이어야 합니다")

        self.check_connection()
        params = {
            "p_report_date": report_date,
            "p_reported_at": reported_at.astimezone(timezone.utc).isoformat(),
            "p_host_id": host_id,
            "p_mounts": [snapshot.as_rpc_payload() for snapshot in snapshots],
        }
        try:
            response = self.client.rpc(self.SNAPSHOT_RPC, params).execute()
        except Exception as exc:
            raise self._write_error("Supabase host storage snapshot 적재 실패", exc) from exc

        payload = self._rpc_object(response.data, "host storage snapshot")
        try:
            result = StorageWriteResult(
                report_date=str(payload["report_date"]),
                host_id=str(payload["host_id"]),
                mount_count=int(payload["mount_count"]),
                reported_at=str(payload["reported_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HostStorageWriteError("Supabase storage RPC 응답이 불완전합니다") from exc
        if (
            payload.get("contract_version") != SCHEMA_VERSION
            or result.report_date != report_date
            or result.host_id != host_id
            or result.mount_count != len(snapshots)
        ):
            raise HostStorageWriteError("Supabase storage RPC 대사 결과가 요청과 다릅니다")
        return result

    @staticmethod
    def _rpc_object(value: Any, context: str) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return value[0]
        raise HostStorageWriteError(f"{context} 응답이 object가 아닙니다")

    @staticmethod
    def _write_error(prefix: str, exc: Exception) -> HostStorageWriteError:
        code = SupabasePortfolioWriter._api_error_code(exc)
        if code == "PGRST202":
            return HostStorageWriteError(
                f"{prefix}(PGRST202). slack-data-collector/sql/"
                "pb_host_storage_v1.sql migration을 먼저 적용하세요."
            )
        if code == "42501":
            return HostStorageWriteError(
                f"{prefix}. sb_secret_... 서버 전용 Secret key 권한을 확인하세요."
            )
        return HostStorageWriteError(f"{prefix}: {safe_error_message(exc)}")


def validate_identifier(value: str, label: str) -> str:
    """Validate stable identifiers before filesystem or network access."""
    normalized = value.strip().lower()
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise HostStorageError(
            f"{label}은 영문 소문자/숫자로 시작하고 ._-만 포함한 1~63자여야 합니다"
        )
    return normalized


def parse_mount_specs(values: Sequence[str], labels: Sequence[str] = ()) -> list[MountSpec]:
    """Parse repeatable ``mount-id=/absolute/path`` CLI arguments."""
    label_map = _parse_assignments(labels, "label", value_is_path=False)
    assignments = _parse_assignments(values, "mount", value_is_path=True)
    unknown_labels = set(label_map) - set(assignments)
    if unknown_labels:
        raise HostStorageError(f"mount가 없는 label ID입니다: {sorted(unknown_labels)}")

    specs = []
    for mount_id, path in assignments.items():
        label = label_map.get(mount_id, mount_id)
        if not label.strip() or len(label.strip()) > 100:
            raise HostStorageError("mount label은 1~100자여야 합니다")
        specs.append(MountSpec(mount_id, Path(path), label.strip()))
    return specs


def _parse_assignments(
    values: Sequence[str], label: str, *, value_is_path: bool
) -> dict[str, str]:
    if label == "mount" and not values:
        raise HostStorageError("최소 한 개의 --mount ID=/absolute/path가 필요합니다")
    result: dict[str, str] = {}
    for raw in values:
        key, separator, value = raw.partition("=")
        if not separator or not value.strip():
            raise HostStorageError(f"--{label} 값은 ID=VALUE 형식이어야 합니다")
        mount_id = validate_identifier(key, f"{label} ID")
        if mount_id in result:
            raise HostStorageError(f"중복 {label} ID입니다: {mount_id}")
        normalized_value = value.strip()
        if value_is_path and not Path(normalized_value).expanduser().is_absolute():
            raise HostStorageError(f"mount path는 절대경로여야 합니다: {normalized_value}")
        result[mount_id] = normalized_value
    return result


def collect_disk_snapshots(
    specs: Sequence[MountSpec],
    *,
    mount_root: Callable[[str], str] = lambda path: filesystem_mount_root(path),
    disk_usage: Callable[[str], DiskUsage] = shutil.disk_usage,
) -> list[DiskSnapshot]:
    """Collect all requested mounts, failing before any remote write on a gap."""
    snapshots: list[DiskSnapshot] = []
    resolved_paths: set[str] = set()
    for spec in specs:
        try:
            resolved = spec.path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise HostStorageError(
                f"mount path를 찾을 수 없습니다: {spec.path}"
            ) from exc
        if not resolved.is_dir():
            raise HostStorageError(f"mount path가 directory가 아닙니다: {resolved}")
        resolved_string = os.fspath(resolved)
        if "\n" in resolved_string or "\r" in resolved_string:
            raise HostStorageError("mount path에는 줄바꿈을 포함할 수 없습니다")
        if resolved_string in resolved_paths:
            raise HostStorageError(f"동일 mount path가 중복 지정되었습니다: {resolved}")
        try:
            actual_mount = Path(mount_root(resolved_string)).resolve(strict=True)
        except (OSError, HostStorageError) as exc:
            raise HostStorageError(
                f"filesystem mount identity를 확인할 수 없습니다: {resolved}"
            ) from exc
        if actual_mount != resolved:
            raise HostStorageError(
                f"지정 경로가 현재 mount point가 아닙니다: {resolved}. "
                f"실제 mount point={actual_mount}. 외장 디스크 unmount 여부를 확인하세요."
            )
        usage = disk_usage(resolved_string)
        total = int(usage.total)
        used = int(usage.used)
        available = int(usage.free)
        if (
            total <= 0
            or min(used, available) < 0
            or used > total
            or available > total
        ):
            raise HostStorageError(f"비정상 filesystem capacity 값입니다: {resolved}")
        snapshots.append(
            DiskSnapshot(
                mount_id=spec.mount_id,
                mount_label=spec.label,
                mount_path=resolved_string,
                total_bytes=total,
                used_bytes=used,
                available_bytes=available,
            )
        )
        resolved_paths.add(resolved_string)
    return snapshots


def filesystem_mount_root(path: str) -> str:
    """Return the OS mount root reported by POSIX ``df``.

    ``os.path.ismount`` returns false for macOS APFS Data firmlinks even though
    `/System/Volumes/Data` is the writable mounted filesystem. ``df -P`` exposes
    the authoritative mount column while keeping each filesystem on one line.
    """
    try:
        result = subprocess.run(
            ["df", "-P", path],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostStorageError(f"df로 mount point를 확인하지 못했습니다: {path}") from exc
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise HostStorageError(f"df 응답에 filesystem 행이 없습니다: {path}")
    fields = lines[-1].split(maxsplit=5)
    if len(fields) != 6 or not fields[-1].startswith("/"):
        raise HostStorageError(f"df mount point 응답을 해석할 수 없습니다: {path}")
    return fields[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mac/Jenkins host disk capacity monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "check-supabase", help="Supabase storage table/RPC contract를 읽기 전용으로 확인"
    )

    collect = subparsers.add_parser("collect", help="mount capacity를 수집해 일별 upsert")
    collect.add_argument(
        "--host-id",
        default=os.getenv("STORAGE_MONITOR_HOST_ID"),
        help="stable host ID (또는 STORAGE_MONITOR_HOST_ID)",
    )
    collect.add_argument(
        "--mount",
        action="append",
        default=[],
        metavar="ID=/ABSOLUTE/PATH",
        help="monitor할 실제 mount point; 여러 번 지정 가능",
    )
    collect.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="ID=DISPLAY_NAME",
        help="선택적인 dashboard 표시 이름",
    )
    collect.add_argument(
        "--timezone",
        default=(
            os.getenv("STORAGE_MONITOR_TIMEZONE")
            or os.getenv("REPORT_TIMEZONE")
            or "Asia/Seoul"
        ),
        help="report_date 기준 IANA timezone",
    )
    collect.add_argument(
        "--simulate",
        action="store_true",
        help="filesystem만 검증하고 Supabase에는 쓰지 않음",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check-supabase":
            contract = SupabaseHostStorageWriter().check_connection()
            LOGGER.info("Supabase storage contract 확인 성공 - %s", contract)
            return 0

        if not args.host_id:
            raise HostStorageError("--host-id 또는 STORAGE_MONITOR_HOST_ID가 필요합니다")
        host_id = validate_identifier(args.host_id, "host_id")
        specs = parse_mount_specs(args.mount, args.label)
        try:
            report_timezone = ZoneInfo(args.timezone)
        except ZoneInfoNotFoundError as exc:
            raise HostStorageError(f"알 수 없는 timezone입니다: {args.timezone}") from exc

        reported_at = datetime.now(timezone.utc)
        snapshots = collect_disk_snapshots(specs)
        report_date = reported_at.astimezone(report_timezone).date().isoformat()
        for snapshot in snapshots:
            LOGGER.info(
                "filesystem 수집 - host=%s mount=%s path=%s used=%.2f%% "
                "available=%d total=%d",
                host_id,
                snapshot.mount_id,
                snapshot.mount_path,
                snapshot.utilization_percent,
                snapshot.available_bytes,
                snapshot.total_bytes,
            )

        if args.simulate:
            LOGGER.info(
                "SIMULATE - Supabase 적재 생략, report_date=%s mounts=%d",
                report_date,
                len(snapshots),
            )
            return 0

        result = SupabaseHostStorageWriter().write_snapshot(
            report_date=report_date,
            reported_at=reported_at,
            host_id=host_id,
            snapshots=snapshots,
        )
        LOGGER.info(
            "Supabase storage 적재 성공 - date=%s host=%s mounts=%d",
            result.report_date,
            result.host_id,
            result.mount_count,
        )
        return 0
    except (HostStorageError, HostStorageWriteError, SupabaseConfigurationError) as exc:
        LOGGER.error("storage monitor 실패: %s", safe_error_message(exc))
        return 1
