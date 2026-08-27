"""Frozen research and strategy-source provenance."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EPOCH_CATALOG = "research/EPOCHS.json"
FROZEN_DIRECTORY = "research/frozen-2026-08-28-v5"
ACTIVE_PREREGISTRATION = f"{FROZEN_DIRECTORY}/PREREGISTRATION.md"
ACTIVE_DATA_CONTRACT = f"{FROZEN_DIRECTORY}/DATA_CONTRACT.md"
ACTIVE_REGISTRY = f"{FROZEN_DIRECTORY}/SPORTS_REGISTRY.json"
ACTIVE_MANIFEST = f"{FROZEN_DIRECTORY}/MANIFEST.sha256"

SOURCE_PATHS = (
    "AGENTS.md",
    "pyproject.toml",
    "uv.lock",
    "config.yaml",
    "README.md",
    "STRATEGY.md",
    "OPERATIONS.md",
    "Jenkinsfile",
    EPOCH_CATALOG,
    ACTIVE_PREREGISTRATION,
    ACTIVE_DATA_CONTRACT,
    ACTIVE_REGISTRY,
    "scripts/verify_external_workspace.py",
    "src/polybot/main.py",
    "src/polybot/config.py",
    "src/polybot/registry.py",
    "src/polybot/classifier.py",
    "src/polybot/lifecycle.py",
    "src/polybot/crossings.py",
    "src/polybot/analyzer.py",
    "src/polybot/bot.py",
    "src/polybot/collector.py",
    "src/polybot/run_audit.py",
    "src/polybot/source_digest.py",
    "src/polybot/api/transport.py",
    "src/polybot/api/gamma_client.py",
    "src/polybot/api/clob_client.py",
    "src/polybot/api/sports_client.py",
    "src/polybot/db/repository.py",
    "src/polybot/db/migrations/0005_major_sports_lifecycle_v5.sql",
    "tests/fixtures/major_sports_lifecycle_cases.json",
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


def sports_registry_sha256(root: Path = PROJECT_ROOT) -> str:
    return sha256_file(root / ACTIVE_REGISTRY)


def verify_frozen_manifest(root: Path = PROJECT_ROOT) -> None:
    manifest = root / ACTIVE_MANIFEST
    base = manifest.parent
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("active frozen manifest is empty")
    verified: set[Path] = set()
    for line in lines:
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64:
            raise ValueError("active frozen manifest contains an invalid line")
        try:
            int(digest, 16)
        except ValueError as error:
            raise ValueError("active frozen manifest digest is not hexadecimal") from error
        path = (base / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("active frozen manifest target escapes the project") from error
        if path in verified or path.is_symlink() or not path.is_file():
            raise ValueError("active frozen manifest target is missing, duplicate, or symlinked")
        if sha256_file(path) != digest:
            raise ValueError(f"active frozen manifest mismatch: {relative}")
        verified.add(path)
    expected = {
        (root / relative).resolve()
        for relative in SOURCE_PATHS
        if relative != ACTIVE_MANIFEST
    }
    if verified != expected:
        missing = sorted(path.relative_to(root).as_posix() for path in expected - verified)
        extra = sorted(path.relative_to(root).as_posix() for path in verified - expected)
        raise ValueError(
            "active frozen manifest target coverage mismatch: "
            f"missing={missing!r} extra={extra!r}"
        )
