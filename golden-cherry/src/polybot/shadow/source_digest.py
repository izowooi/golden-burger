"""Stable source and preregistration identity for Cherry shadow v2."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PREREGISTRATION_PATH = "docs/CHERRY_SHADOW_RESOLUTION_V2_PREREGISTRATION.md"
EXPECTED_PREREGISTRATION_SHA256 = (
    "72d87684fa9ec7145b64fb8614afee60c9a7391a518bd0a867df7d19c9f95ee7"
)
SOURCE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "main.py",
    "shadow_config.yaml",
    PREREGISTRATION_PATH,
    "src/polybot/main.py",
    "src/polybot/utils/process_lock.py",
    "src/polybot/shadow/__init__.py",
    "src/polybot/shadow/safety.py",
    "src/polybot/shadow/source_digest.py",
    "src/polybot/shadow/config.py",
    "src/polybot/shadow/transport.py",
    "src/polybot/shadow/clients.py",
    "src/polybot/shadow/db.py",
    "src/polybot/shadow/collector.py",
    "src/polybot/shadow/analyzer.py",
    "src/polybot/shadow/runtime.py",
    "src/polybot/shadow/cli.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preregistration_sha256(root: Path = PROJECT_ROOT) -> str:
    return sha256_file(root / PREREGISTRATION_PATH)


def verify_preregistration(root: Path = PROJECT_ROOT) -> str:
    actual = preregistration_sha256(root)
    if actual != EXPECTED_PREREGISTRATION_SHA256:
        raise ValueError(
            "Cherry shadow preregistration digest changed; create a new experiment identity"
        )
    return actual


def compute_strategy_source_digest(root: Path = PROJECT_ROOT) -> str:
    digest = hashlib.sha256()
    for relative in SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"shadow source manifest entry is missing: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()
