"""Cohort identity for the Golden Pomegranate research runtime."""

from __future__ import annotations

import hashlib
from pathlib import Path


def _source_files(project_root: Path) -> list[Path]:
    project_root = project_root.resolve()
    repository_root = project_root.parent
    observability_root = repository_root / "polybot-observability"
    required = [project_root / "config.yaml", project_root / "pyproject.toml"]
    discovered = [
        *sorted((project_root / "src" / "polybot").rglob("*.py")),
        observability_root / "src" / "polybot_observability" / "config_contract.py",
    ]
    files = sorted(
        set(required + discovered),
        key=lambda path: path.resolve().relative_to(repository_root).as_posix(),
    )
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise RuntimeError(
            "strategy source digest inputs are missing: "
            + ", ".join(str(path) for path in missing)
        )
    return files


def compute_strategy_source_digest(project_root: Path) -> str:
    """Hash exact relevant paths and bytes independently of monorepo churn."""
    project_root = project_root.resolve()
    repository_root = project_root.parent
    digest = hashlib.sha256()
    for path in _source_files(project_root):
        relative = path.resolve().relative_to(repository_root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
