from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from polybot.config import CANONICAL_JOBS, PROJECT_ROOT
from scripts.analyze_experiment import (
    EXPECTED_RUNTIME_IDENTITIES,
    FROZEN_WINDOW_END,
    FROZEN_WINDOW_START,
)


ACTIVE_DOCS = (PROJECT_ROOT / "README.md", PROJECT_ROOT / "OPERATIONS.md")
FROZEN_WINDOW = "[2026-08-23T20:00:00Z, 2026-09-22T20:00:00Z)"
SCHEDULES = (
    "0-59/5 * * * *",
    "1-59/5 * * * *",
    "2-59/5 * * * *",
)
LEGACY_DEPLOYMENT_FRAGMENTS = (
    "RUNTIME_JOB=raspberry-do-shard-0",
    "RUNTIME_JOB=raspberry-re-shard-1",
    "RUNTIME_JOB=raspberry-mi-shard-2",
    "--job raspberry-do-shard-0",
    "--job raspberry-re-shard-1",
    "--job raspberry-mi-shard-2",
    "POLYBOT_EXPERIMENT_START_UTC=2026-08-13T12:00:00Z",
    "POLYBOT_EXPERIMENT_END_UTC=2026-09-12T12:00:00Z",
    "frozen-2026-08-13-external-v2 && shasum",
)


@pytest.mark.parametrize("document", ACTIVE_DOCS, ids=lambda path: path.name)
def test_active_operations_documents_pin_v3_deployment_contract(document: Path):
    text = document.read_text(encoding="utf-8")

    assert "queue-echo-v3" in text
    assert FROZEN_WINDOW in text
    assert "research/frozen-2026-08-23-v3" in text
    assert "shasum -a 256 -c MANIFEST.sha256" in text
    assert "external-v2" in text
    assert "migration" in text

    for runtime in CANONICAL_JOBS:
        assert runtime in text
        assert f"uv run polybot config --simulate --job {runtime}" in text
    for schedule in SCHEDULES:
        assert schedule in text

    for term in (
        "225",
        "240",
        "STARTED",
        "FAILED",
        "UNIVERSE",
        "FOLLOWUP_ONLY",
        "EMPTY_BOOK",
        "same-request",
        "duplicate",
        "late",
    ):
        assert term in text

    for legacy_fragment in LEGACY_DEPLOYMENT_FRAGMENTS:
        assert legacy_fragment not in text


def test_frozen_v3_manifest_is_complete_and_valid():
    frozen = PROJECT_ROOT / "research" / "frozen-2026-08-23-v3"
    entries: dict[str, str] = {}
    for line in (frozen / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        entries[relative] = digest

    assert set(entries) == {"PREREGISTRATION.md", "DATA_CONTRACT.md"}
    for relative, expected in entries.items():
        assert hashlib.sha256((frozen / relative).read_bytes()).hexdigest() == expected


def test_analyzer_runtime_and_window_identity_match_the_runtime_config():
    assert EXPECTED_RUNTIME_IDENTITIES == CANONICAL_JOBS
    assert FROZEN_WINDOW_START == "2026-08-23T20:00:00Z"
    assert FROZEN_WINDOW_END == "2026-09-22T20:00:00Z"
