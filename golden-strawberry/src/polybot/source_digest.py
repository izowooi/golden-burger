"""Stable source and preregistration digests for Last Mile cohort identity."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_PREREGISTRATION = "research/frozen-2026-08-15-clob/PREREGISTRATION.md"
SOURCE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "config.yaml",
    "STRATEGY.md",
    ACTIVE_PREREGISTRATION,
    "scripts/analyze_experiment.py",
    "scripts/verify_external_workspace.py",
    "src/polybot/main.py",
    "src/polybot/analyzer.py",
    "src/polybot/bot.py",
    "src/polybot/config.py",
    "src/polybot/run_audit.py",
    "src/polybot/source_digest.py",
    "src/polybot/api/sampling_client.py",
    "src/polybot/api/gamma_client.py",
    "src/polybot/api/clob_client.py",
    "src/polybot/collector.py",
    "src/polybot/db/repository.py",
    "src/polybot/utils/retry.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_strategy_source_digest(root: Path = PROJECT_ROOT) -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_PATHS:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def preregistration_sha256(root: Path = PROJECT_ROOT) -> str:
    return sha256_file(root / ACTIVE_PREREGISTRATION)


__all__ = [
    "ACTIVE_PREREGISTRATION",
    "PROJECT_ROOT",
    "SOURCE_PATHS",
    "compute_strategy_source_digest",
    "preregistration_sha256",
    "sha256_file",
]
