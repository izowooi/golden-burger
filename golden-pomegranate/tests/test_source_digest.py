"""Source cohort digest includes only runtime-relevant shared code."""

from __future__ import annotations

from pathlib import Path

from polybot.source_digest import _source_files, compute_strategy_source_digest


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "golden-pomegranate"
    source = project / "src" / "polybot"
    shared = tmp_path / "polybot-observability" / "src" / "polybot_observability"
    source.mkdir(parents=True)
    shared.mkdir(parents=True)
    (project / "config.yaml").write_text("value: 1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (source / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    config_contract = shared / "config_contract.py"
    config_contract.write_text("CONTRACT = 1\n", encoding="utf-8")
    unrelated = shared / "execution_ledger.py"
    unrelated.write_text("UNRELATED = 1\n", encoding="utf-8")
    return project, config_contract, unrelated


def test_digest_scope_has_exact_shared_config_contract_dependency(tmp_path):
    project, config_contract, unrelated = _layout(tmp_path)

    paths = _source_files(project)

    assert config_contract in paths
    assert unrelated not in paths


def test_unrelated_observability_change_does_not_split_cohort(tmp_path):
    project, config_contract, unrelated = _layout(tmp_path)
    baseline = compute_strategy_source_digest(project)

    unrelated.write_text("UNRELATED = 2\n", encoding="utf-8")
    assert compute_strategy_source_digest(project) == baseline

    config_contract.write_text("CONTRACT = 2\n", encoding="utf-8")
    assert compute_strategy_source_digest(project) != baseline
