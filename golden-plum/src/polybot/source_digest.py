"""Stable Golden Plum strategy-source and preregistration identity."""

from __future__ import annotations

import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_PREREGISTRATION = (
    "research/frozen-2026-08-31-full-match-no-time-exit-v2/PREREGISTRATION.md"
)


def _runtime_files(project_root: Path) -> list[Path]:
    project_root = project_root.resolve()
    repository_root = project_root.parent
    observability_root = repository_root / "polybot-observability"
    required = [
        project_root / "config.yaml",
        project_root / "pyproject.toml",
        project_root / "uv.lock",
        project_root / "STRATEGY.md",
        project_root / ACTIVE_PREREGISTRATION,
        project_root / "scripts" / "verify_external_workspace.py",
        observability_root / "pyproject.toml",
    ]
    discovered = [
        *sorted((project_root / "src" / "polybot").rglob("*.py")),
        *sorted(
            (observability_root / "src" / "polybot_observability").rglob("*.py")
        ),
    ]
    files = sorted(
        set(required + discovered),
        key=lambda path: path.relative_to(repository_root).as_posix(),
    )
    missing = [path for path in files if not path.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise RuntimeError(f"strategy source digest input is missing: {names}")
    return files


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_strategy_source_digest(project_root: Path = PROJECT_ROOT) -> str:
    """Hash exact runtime-relevant bytes without unrelated monorepo commits."""
    project_root = project_root.resolve()
    repository_root = project_root.parent
    digest = hashlib.sha256()
    for path in _runtime_files(project_root):
        relative = path.relative_to(repository_root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def preregistration_sha256(project_root: Path = PROJECT_ROOT) -> str:
    return sha256_file(project_root.resolve() / ACTIVE_PREREGISTRATION)
