"""Stable source and preregistration digests for the follow-up v2a epoch."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .source_digest import PROJECT_ROOT, sha256_file


FOLLOWUP_PREREGISTRATION = (
    "research/frozen-2026-08-24-followup-v2a/PREREGISTRATION.md"
)
FOLLOWUP_SOURCE_PATHS = (
    "AGENTS.md",
    "README.md",
    "OPERATIONS.md",
    "STRATEGY.md",
    "pyproject.toml",
    "uv.lock",
    "config.followup-v2a.yaml",
    FOLLOWUP_PREREGISTRATION,
    "scripts/verify_external_workspace.py",
    "scripts/attest_source_reattachment.py",
    "research/amendment-2026-09-05-device-reattachment/OPERATIONS_AMENDMENT.md",
    "src/polybot/analyzer.py",
    "src/polybot/api/clob_client.py",
    "src/polybot/api/gamma_client.py",
    "src/polybot/api/sampling_client.py",
    "src/polybot/bot.py",
    "src/polybot/collector.py",
    "src/polybot/config.py",
    "src/polybot/followup_analyzer.py",
    "src/polybot/followup_bot.py",
    "src/polybot/followup_collector.py",
    "src/polybot/followup_config.py",
    "src/polybot/followup_main.py",
    "src/polybot/followup_run_audit.py",
    "src/polybot/followup_source_digest.py",
    "src/polybot/main.py",
    "src/polybot/run_audit.py",
    "src/polybot/v1_source.py",
    "src/polybot/source_reattachment.py",
    "src/polybot/db/followup_repository.py",
    "src/polybot/utils/retry.py",
)


def compute_followup_source_digest(root: Path = PROJECT_ROOT) -> str:
    digest = hashlib.sha256()
    for relative in FOLLOWUP_SOURCE_PATHS:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def followup_preregistration_sha256(root: Path = PROJECT_ROOT) -> str:
    return sha256_file(root / FOLLOWUP_PREREGISTRATION)


__all__ = [
    "FOLLOWUP_PREREGISTRATION",
    "FOLLOWUP_SOURCE_PATHS",
    "compute_followup_source_digest",
    "followup_preregistration_sha256",
]
