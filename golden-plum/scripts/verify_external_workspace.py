#!/usr/bin/env python3
"""Fail closed unless a Golden Plum collector uses its trusted T7 workspace."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
from typing import Any


MARKER_NAME = ".daily-rsync-workspace.json"
DEFAULT_MOUNT_ROOT = Path("/Volumes/t7")
DEFAULT_SENTINEL = Path("/Volumes/t7/.golden-raspberry-volume")
DEFAULT_HOST_UUID_PIN = Path(
    "/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid"
)
VOLUME_PROFILE = "golden-raspberry-apfs-v1"
MIN_FREE_GIB = 50.0
_GIB = 1024**3
_UUID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


@dataclass(frozen=True)
class WorkspaceSpec:
    jenkins_job: str
    runtime_job: str
    workspace: Path
    additional_runtime_jobs: tuple[str, ...] = ()

    @property
    def database(self) -> Path:
        """Backward-compatible primary database for this Jenkins job."""
        return self.database_for_runtime(self.runtime_job)

    @property
    def runtime_jobs(self) -> tuple[str, ...]:
        return (self.runtime_job, *self.additional_runtime_jobs)

    @property
    def databases(self) -> tuple[Path, ...]:
        return tuple(self.database_for_runtime(job) for job in self.runtime_jobs)

    def database_for_runtime(self, runtime_job: str) -> Path:
        return (
            self.workspace
            / "golden-plum"
            / "data"
            / runtime_job
            / "trades_sim.db"
        )

    def runtime_for_database(self, database: Path) -> str | None:
        for runtime_job in self.runtime_jobs:
            if database == self.database_for_runtime(runtime_job):
                return runtime_job
        return None


WORKSPACE_SPECS = {
    "polybot-gold": WorkspaceSpec(
        jenkins_job="polybot-gold",
        runtime_job="plum-shadow-gold-mlb-1m-v1",
        workspace=Path("/Volumes/t7/jenkins/polybot-gold"),
        additional_runtime_jobs=(
            "plum-shadow-gold-nfl-1m-v1",
            "plum-shadow-gold-nba-1m-v1",
        ),
    ),
    "polybot-silver": WorkspaceSpec(
        jenkins_job="polybot-silver",
        runtime_job="plum-shadow-silver-1m-v1",
        workspace=Path("/Volumes/t7/jenkins/polybot-silver"),
    ),
}


class WorkspaceVerificationError(RuntimeError):
    """The external workspace identity or capacity could not be proven."""


def _assert_no_symlink_ancestors(path: Path) -> None:
    """Reject a symlink at any existing component, including the leaf."""
    if not path.is_absolute():
        raise WorkspaceVerificationError("trusted paths must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise WorkspaceVerificationError(
                f"trusted path contains a symlink component: {current}"
            )
        if not current.exists():
            break


def _regular_file(path: Path, label: str) -> None:
    _assert_no_symlink_ancestors(path)
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
        timeout=5,
    )
    payload = plistlib.loads(process.stdout)
    if not isinstance(payload, dict):
        raise WorkspaceVerificationError("diskutil returned an invalid payload")
    return payload


def _device_id(path: Path) -> int:
    return int(path.stat().st_dev)


def _marker_payload(spec: WorkspaceSpec) -> dict[str, object]:
    return {
        "schema_version": 1,
        "job": spec.jenkins_job,
        "workspace": str(spec.workspace),
    }


def _write_marker(workspace: Path, spec: WorkspaceSpec) -> Path:
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
                _marker_payload(spec),
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


def _validate_marker(marker: Path, spec: WorkspaceSpec) -> None:
    _regular_file(marker, "daily-rsync marker")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise WorkspaceVerificationError("daily-rsync marker is invalid") from error
    expected = _marker_payload(spec)
    if payload != expected or set(payload) != set(expected):
        raise WorkspaceVerificationError("daily-rsync marker identity differs")


def _validate_database_path(
    *, database: Path, spec: WorkspaceSpec, mount_device: int
) -> None:
    if spec.runtime_for_database(database) is None:
        raise WorkspaceVerificationError(
            "database path differs from the frozen runtime path"
        )
    _assert_no_symlink_ancestors(database)
    workspace_resolved = spec.workspace.resolve(strict=True)
    database_resolved = database.resolve(strict=False)
    try:
        database_resolved.relative_to(workspace_resolved)
    except ValueError as error:
        raise WorkspaceVerificationError(
            "database path escapes the trusted workspace"
        ) from error

    existing = database
    while not existing.exists() and existing != spec.workspace:
        existing = existing.parent
    if not existing.exists() or _device_id(existing) != mount_device:
        raise WorkspaceVerificationError(
            "database parent is not contained on the trusted volume"
        )
    if database.exists() and (database.is_symlink() or not database.is_file()):
        raise WorkspaceVerificationError("database target is not a safe regular file")


def verify_external_workspace(
    *,
    job: str,
    workspace: Path,
    database: Path,
    write_marker: bool,
    min_free_gib: float = MIN_FREE_GIB,
) -> dict[str, Any]:
    """Verify identity, capacity, marker, and DB routing without creating paths."""
    spec = WORKSPACE_SPECS.get(str(job))
    if spec is None:
        raise WorkspaceVerificationError("unsupported Golden Plum collector job")
    if not isinstance(min_free_gib, (int, float)) or isinstance(min_free_gib, bool):
        raise WorkspaceVerificationError("minimum free space must be numeric")
    if not math.isfinite(float(min_free_gib)) or float(min_free_gib) < MIN_FREE_GIB:
        raise WorkspaceVerificationError(
            f"minimum free space cannot be lower than {MIN_FREE_GIB:g} GiB"
        )
    if workspace != spec.workspace:
        raise WorkspaceVerificationError(
            "Jenkins WORKSPACE differs from the approved exact path"
        )

    for trusted_path in (
        DEFAULT_MOUNT_ROOT,
        workspace,
        DEFAULT_SENTINEL,
        DEFAULT_HOST_UUID_PIN,
    ):
        _assert_no_symlink_ancestors(trusted_path)
    if not DEFAULT_MOUNT_ROOT.is_dir():
        raise WorkspaceVerificationError("external mount root is absent")
    if DEFAULT_MOUNT_ROOT.resolve(strict=True) != DEFAULT_MOUNT_ROOT:
        raise WorkspaceVerificationError("external mount root is not canonical")
    if not workspace.is_dir():
        raise WorkspaceVerificationError(
            "Jenkins workspace is absent; verification never creates it"
        )
    if workspace.resolve(strict=True) != spec.workspace:
        raise WorkspaceVerificationError("Jenkins workspace canonical path differs")

    info = _diskutil_info(DEFAULT_MOUNT_ROOT)
    filesystem = str(info.get("FilesystemType", "")).casefold()
    mount_point = str(info.get("MountPoint", ""))
    internal = info.get("Internal")
    volume_uuid = str(info.get("VolumeUUID", ""))
    if filesystem != "apfs":
        raise WorkspaceVerificationError("approved volume is not APFS")
    if mount_point != str(DEFAULT_MOUNT_ROOT) or internal is not False:
        raise WorkspaceVerificationError("approved path is not the exact external mount")
    if not _UUID.fullmatch(volume_uuid):
        raise WorkspaceVerificationError("external volume UUID is malformed")

    _regular_file(DEFAULT_SENTINEL, "shared trusted-volume sentinel")
    _regular_file(DEFAULT_HOST_UUID_PIN, "shared off-volume UUID pin")
    sentinel_values = _key_values(DEFAULT_SENTINEL)
    host_uuid = DEFAULT_HOST_UUID_PIN.read_text(encoding="utf-8").strip()
    if sentinel_values.get("profile") != VOLUME_PROFILE:
        raise WorkspaceVerificationError("shared sentinel profile differs")
    if sentinel_values.get("volume_uuid") != volume_uuid or host_uuid != volume_uuid:
        raise WorkspaceVerificationError("external volume UUID differs from both pins")

    mount_device = _device_id(DEFAULT_MOUNT_ROOT)
    if _device_id(workspace) != mount_device:
        raise WorkspaceVerificationError("workspace is not on the trusted volume")
    if _device_id(Path("/")) == mount_device:
        raise WorkspaceVerificationError("trusted volume resolves to the internal root")
    if _device_id(DEFAULT_HOST_UUID_PIN) == mount_device:
        raise WorkspaceVerificationError("trusted UUID pin is not off-volume")

    usage = shutil.disk_usage(workspace)
    required_free_bytes = int(float(min_free_gib) * _GIB)
    if int(usage.free) < required_free_bytes:
        raise WorkspaceVerificationError(
            f"workspace has less than {float(min_free_gib):g} GiB free"
        )
    _validate_database_path(
        database=database,
        spec=spec,
        mount_device=mount_device,
    )

    marker = workspace / MARKER_NAME
    if write_marker:
        marker = _write_marker(workspace, spec)
    _validate_marker(marker, spec)
    runtime_job = spec.runtime_for_database(database)
    if runtime_job is None:  # Kept explicit after the earlier fail-closed check.
        raise WorkspaceVerificationError(
            "database path differs from the frozen runtime path"
        )
    return {
        "status": "ok",
        "filesystem": filesystem,
        "job": spec.jenkins_job,
        "runtime_job": runtime_job,
        "workspace": str(spec.workspace),
        "database": str(database),
        "marker": str(marker),
        "free_bytes": int(usage.free),
        "required_free_bytes": required_free_bytes,
        "volume_profile": VOLUME_PROFILE,
    }


def inspect_workspace(
    path: Path, min_free_gib: float = MIN_FREE_GIB
) -> dict[str, Any]:
    """Backward-compatible strict entry point for an exact approved workspace."""
    workspace = Path(path)
    for spec in WORKSPACE_SPECS.values():
        if workspace == spec.workspace:
            return verify_external_workspace(
                job=spec.jenkins_job,
                workspace=workspace,
                database=spec.database,
                write_marker=False,
                min_free_gib=min_free_gib,
            )
    raise WorkspaceVerificationError(
        "workspace must be an approved exact path below /Volumes/t7"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", choices=sorted(WORKSPACE_SPECS), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--min-free-gib", type=float, default=MIN_FREE_GIB)
    parser.add_argument("--write-daily-rsync-marker", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = verify_external_workspace(
            job=args.job,
            workspace=args.workspace,
            database=args.database,
            write_marker=args.write_daily_rsync_marker,
            min_free_gib=args.min_free_gib,
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
