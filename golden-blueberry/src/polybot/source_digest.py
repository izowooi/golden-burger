"""Runtime-source identity for the Golden Blueberry experiment.

The monorepo Git commit remains useful provenance, but unrelated changes in
other projects must not split a Blueberry cohort. This digest covers only the
strategy runtime, frozen replay logic, lockfile, and shared observability code.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def _files(project_root: Path) -> list[Path]:
    repository_root = project_root.parent
    observability_root = repository_root / "polybot-observability"
    required = [
        project_root / "config.yaml",
        project_root / "pyproject.toml",
        project_root / "uv.lock",
        project_root / "scripts" / "backtest.py",
        project_root / "scripts" / "analyze_experiment.py",
        observability_root / "pyproject.toml",
    ]
    discovered = [
        *sorted((project_root / "src" / "polybot").rglob("*.py")),
        *sorted(
            (observability_root / "src" / "polybot_observability").rglob("*.py")
        ),
    ]
    files = required + discovered
    missing = [path for path in files if not path.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise RuntimeError(f"strategy source digest 입력 파일이 없습니다: {names}")
    return sorted(
        set(files), key=lambda path: path.relative_to(repository_root).as_posix()
    )


def compute_strategy_source_digest(project_root: Path) -> str:
    """Return SHA-256 over strategy-relevant paths and exact bytes."""
    repository_root = project_root.parent.resolve()
    digest = hashlib.sha256()
    for path in _files(project_root.resolve()):
        relative = path.resolve().relative_to(repository_root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
