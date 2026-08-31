from pathlib import Path

from polybot.source_digest import (
    ACTIVE_PREREGISTRATION,
    compute_strategy_source_digest,
    preregistration_sha256,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _source_tree(tmp_path: Path) -> Path:
    project_root = tmp_path / "repo" / "golden-plum"
    observability_root = tmp_path / "repo" / "polybot-observability"
    for relative in ("config.yaml", "pyproject.toml", "uv.lock", "STRATEGY.md"):
        _write(project_root / relative, relative)
    _write(project_root / ACTIVE_PREREGISTRATION, "frozen protocol")
    _write(
        project_root / "scripts/verify_external_workspace.py",
        "def inspect_workspace(path): return path",
    )
    _write(project_root / "src/polybot/config.py", "PARAMETER = 1")
    _write(observability_root / "pyproject.toml", "observability")
    _write(
        observability_root / "src/polybot_observability/run_audit.py",
        "SCHEMA_VERSION = 1",
    )
    return project_root


def test_source_digest_ignores_unrelated_monorepo_files(tmp_path: Path) -> None:
    project_root = _source_tree(tmp_path)
    before = compute_strategy_source_digest(project_root)

    _write(project_root.parent / "daily-rsync/README.md", "unrelated change")

    assert compute_strategy_source_digest(project_root) == before


def test_source_digest_changes_with_runtime_or_shared_observability(
    tmp_path: Path,
) -> None:
    project_root = _source_tree(tmp_path)
    before = compute_strategy_source_digest(project_root)

    _write(project_root / "src/polybot/config.py", "PARAMETER = 2")
    runtime_changed = compute_strategy_source_digest(project_root)
    assert runtime_changed != before

    _write(
        project_root.parent
        / "polybot-observability/src/polybot_observability/run_audit.py",
        "SCHEMA_VERSION = 2",
    )
    assert compute_strategy_source_digest(project_root) != runtime_changed


def test_preregistration_digest_tracks_only_frozen_protocol(tmp_path: Path) -> None:
    project_root = _source_tree(tmp_path)
    before = preregistration_sha256(project_root)

    _write(project_root / ACTIVE_PREREGISTRATION, "revised frozen protocol")

    assert preregistration_sha256(project_root) != before
