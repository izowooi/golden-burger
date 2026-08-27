#!/usr/bin/env python3
"""Verify the exact trusted T7 Jenkins workspace and write its routing marker."""

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
JENKINS_JOB = "polybot-gold"
RUNTIME_JOB = "coconut-major-sports-lifecycle-5m-v3"
DEFAULT_MOUNT_ROOT = Path("/Volumes/t7")
DEFAULT_EXPECTED_WORKSPACE = Path("/Volumes/t7/jenkins/polybot-gold")
# Coconut reuses the already-pinned Raspberry T7 identity read-only.  The
# three Raspberry shards and Strawberry collector already prove this exact
# trust anchor on the Jenkins host.
DEFAULT_SENTINEL = Path("/Volumes/t7/.golden-raspberry-volume")
DEFAULT_HOST_UUID_PIN = Path(
    "/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid"
)
VOLUME_PROFILE = "golden-raspberry-apfs-v1"
_UUID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


class WorkspaceVerificationError(RuntimeError):
    """The external workspace identity could not be proven."""


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise WorkspaceVerificationError(f"{label} is absent or unsafe")


def _key_values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key not in result:
            result[key] = value
    return result


def _diskutil_info(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", str(path)],
        check=True,
        capture_output=True,
    )
    payload = plistlib.loads(process.stdout)
    if not isinstance(payload, dict):
        raise WorkspaceVerificationError("diskutil returned an invalid payload")
    return payload


def _write_marker(workspace: Path) -> Path:
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
            # daily-rsync requires exactly these three keys and values.
            json.dump(
                {
                    "schema_version": 1,
                    "job": "polybot-gold",
                    "workspace": "/Volumes/t7/jenkins/polybot-gold",
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
    if job != JENKINS_JOB:
        raise WorkspaceVerificationError(f"Jenkins job must be {JENKINS_JOB}")
    if mount_root != DEFAULT_MOUNT_ROOT:
        raise WorkspaceVerificationError("mount root must be /Volumes/t7")
    if mount_root.is_symlink() or not mount_root.is_dir():
        raise WorkspaceVerificationError("external mount root is absent or unsafe")
    if mount_root.resolve(strict=True) != mount_root:
        raise WorkspaceVerificationError("external mount root is not canonical")
    if expected_workspace != DEFAULT_EXPECTED_WORKSPACE:
        raise WorkspaceVerificationError("expected workspace differs from the frozen path")
    if workspace != expected_workspace:
        raise WorkspaceVerificationError("Jenkins WORKSPACE differs from the approved path")
    if workspace.is_symlink() or not workspace.is_dir():
        raise WorkspaceVerificationError("Jenkins workspace is absent or unsafe")
    if workspace.resolve(strict=True) != expected_workspace:
        raise WorkspaceVerificationError("Jenkins workspace canonical path differs")

    info = _diskutil_info(mount_root)
    filesystem = str(info.get("FilesystemType", "")).casefold()
    mount_point = str(info.get("MountPoint", ""))
    internal = info.get("Internal")
    volume_uuid = str(info.get("VolumeUUID", ""))
    if filesystem != "apfs":
        raise WorkspaceVerificationError("approved volume is not APFS")
    if mount_point != str(mount_root) or internal is not False:
        raise WorkspaceVerificationError("approved path is not the exact external mount")
    if not _UUID.fullmatch(volume_uuid):
        raise WorkspaceVerificationError("external volume UUID is malformed")

    _regular_file(sentinel, "shared trusted-volume sentinel")
    _regular_file(host_uuid_pin, "shared off-volume UUID pin")
    sentinel_values = _key_values(sentinel)
    host_uuid = host_uuid_pin.read_text(encoding="utf-8").strip()
    if sentinel_values.get("profile") != VOLUME_PROFILE:
        raise WorkspaceVerificationError("shared sentinel profile differs")
    if sentinel_values.get("volume_uuid") != volume_uuid or host_uuid != volume_uuid:
        raise WorkspaceVerificationError("external volume UUID differs from both pins")
    mount_device = mount_root.stat().st_dev
    if workspace.stat().st_dev != mount_device:
        raise WorkspaceVerificationError("workspace is not on the trusted volume")
    if host_uuid_pin.stat().st_dev == mount_device:
        raise WorkspaceVerificationError("trusted UUID pin is not off-volume")

    marker = _write_marker(workspace) if write_marker else workspace / MARKER_NAME
    return {
        "status": "ok",
        "filesystem": filesystem,
        "job": JENKINS_JOB,
        "runtime_job": RUNTIME_JOB,
        "workspace": str(workspace),
        "marker": str(marker),
        "trust_anchor_reused_from": "golden-raspberry",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount-root", type=Path, default=DEFAULT_MOUNT_ROOT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--expected-workspace", type=Path, default=DEFAULT_EXPECTED_WORKSPACE
    )
    parser.add_argument("--job", default=JENKINS_JOB)
    parser.add_argument("--sentinel", type=Path, default=DEFAULT_SENTINEL)
    parser.add_argument("--host-uuid-pin", type=Path, default=DEFAULT_HOST_UUID_PIN)
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
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        WorkspaceVerificationError,
    ) as error:
        print(f"External workspace verification failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
