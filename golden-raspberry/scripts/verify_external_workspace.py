#!/usr/bin/env python3
"""Fail closed unless Jenkins is running in the approved external workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from typing import Any


MARKER_NAME = ".daily-rsync-workspace.json"
VOLUME_PROFILE = "golden-raspberry-apfs-v1"
_UUID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


class WorkspaceVerificationError(RuntimeError):
    """Raised when the external workspace identity cannot be proven."""


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise WorkspaceVerificationError(f"{label} is absent or unsafe")


def _read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in values:
            values[key] = value
    return values


def _diskutil_info(mount_root: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", str(mount_root)],
        check=True,
        capture_output=True,
    )
    payload = plistlib.loads(process.stdout)
    if not isinstance(payload, dict):
        raise WorkspaceVerificationError("diskutil returned an invalid payload")
    return payload


def _device_id(path: Path) -> int:
    return path.stat().st_dev


def _write_marker(workspace: Path, *, job: str) -> Path:
    target = workspace / MARKER_NAME
    if target.is_symlink():
        raise WorkspaceVerificationError("daily-rsync marker target is a symlink")
    temporary = workspace / f"{MARKER_NAME}.tmp.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "job": job,
                    "workspace": str(workspace),
                },
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory = os.open(workspace, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


def verify_external_workspace(
    *,
    mount_root: Path,
    workspace: Path,
    expected_workspace: Path,
    job: str,
    sentinel: Path,
    host_uuid_pin: Path,
    write_marker: bool,
) -> dict[str, Any]:
    if not job or "/" in job or job in {".", ".."}:
        raise WorkspaceVerificationError("Jenkins job name is invalid")
    if mount_root.is_symlink() or not mount_root.is_dir():
        raise WorkspaceVerificationError("external mount root is absent or unsafe")
    if mount_root.resolve(strict=True) != mount_root:
        raise WorkspaceVerificationError("external mount root is not canonical")

    canonical_job_root = mount_root / "jenkins"
    if expected_workspace != canonical_job_root / job:
        raise WorkspaceVerificationError("expected workspace is not the canonical job path")
    if workspace != expected_workspace:
        raise WorkspaceVerificationError("Jenkins WORKSPACE does not match the approved path")
    if workspace.is_symlink() or not workspace.is_dir():
        raise WorkspaceVerificationError("Jenkins workspace is absent or unsafe")
    if workspace.resolve(strict=True) != expected_workspace:
        raise WorkspaceVerificationError("Jenkins workspace canonical path does not match")

    info = _diskutil_info(mount_root)
    filesystem = str(info.get("FilesystemType", "")).lower()
    mount_point = str(info.get("MountPoint", ""))
    internal = info.get("Internal")
    volume_uuid = str(info.get("VolumeUUID", ""))
    if filesystem != "apfs":
        raise WorkspaceVerificationError("approved data volume is not APFS")
    if mount_point != str(mount_root) or internal is not False:
        raise WorkspaceVerificationError("approved path is not the exact external mount")
    if not _UUID.fullmatch(volume_uuid):
        raise WorkspaceVerificationError("external volume UUID is missing or malformed")

    _require_regular_file(sentinel, "external volume sentinel")
    _require_regular_file(host_uuid_pin, "off-volume UUID pin")
    sentinel_values = _read_key_values(sentinel)
    host_uuid = host_uuid_pin.read_text(encoding="utf-8").strip()
    if sentinel_values.get("profile") != VOLUME_PROFILE:
        raise WorkspaceVerificationError("external volume sentinel profile is invalid")
    if sentinel_values.get("volume_uuid") != volume_uuid or host_uuid != volume_uuid:
        raise WorkspaceVerificationError("external volume UUID does not match both pins")

    mount_device = _device_id(mount_root)
    if _device_id(workspace) != mount_device:
        raise WorkspaceVerificationError("Jenkins workspace is not on the external volume")
    if _device_id(host_uuid_pin) == mount_device:
        raise WorkspaceVerificationError("trusted UUID pin is not stored off-volume")

    marker = _write_marker(workspace, job=job) if write_marker else workspace / MARKER_NAME
    return {
        "status": "ok",
        "filesystem": filesystem,
        "job": job,
        "mount_root": str(mount_root),
        "workspace": str(workspace),
        "marker": str(marker),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--expected-workspace", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--sentinel", type=Path, required=True)
    parser.add_argument("--host-uuid-pin", type=Path, required=True)
    parser.add_argument("--write-daily-rsync-marker", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = verify_external_workspace(
            mount_root=args.mount_root,
            workspace=args.workspace,
            expected_workspace=args.expected_workspace,
            job=args.job,
            sentinel=args.sentinel,
            host_uuid_pin=args.host_uuid_pin,
            write_marker=args.write_daily_rsync_marker,
        )
    except (OSError, subprocess.SubprocessError, ValueError, WorkspaceVerificationError) as error:
        print(f"External workspace verification failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
