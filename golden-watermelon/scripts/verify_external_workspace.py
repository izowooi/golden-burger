#!/usr/bin/env python3
"""Fail closed when a Jenkins workspace is not on a capacious external volume."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def inspect_workspace(path: Path, min_free_gib: float = 50) -> dict[str, object]:
    resolved = path.resolve()
    if not str(resolved).startswith("/Volumes/"):
        raise RuntimeError("workspace must resolve below /Volumes")
    if path.is_symlink():
        raise RuntimeError("workspace path must not be a symlink")
    resolved.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(resolved)
    root_usage = shutil.disk_usage("/")
    if resolved.stat().st_dev == Path("/").stat().st_dev:
        raise RuntimeError("workspace is on the internal root device")
    if usage.free < min_free_gib * 1024**3:
        raise RuntimeError("workspace has less than the required free space")
    return {
        "workspace": str(resolved), "device_id": resolved.stat().st_dev,
        "root_device_id": Path("/").stat().st_dev, "free_bytes": usage.free,
        "total_bytes": usage.total, "root_total_bytes": root_usage.total,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--min-free-gib", type=float, default=50)
    args = parser.parse_args()
    print(json.dumps(inspect_workspace(args.workspace, args.min_free_gib), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
