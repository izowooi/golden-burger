"""Every imported shared storage dependency participates in source provenance."""
from pathlib import Path

import pytest

from polybot_observability.market_data_source_digest import MARKET_DATA_SOURCE_FILES
from polybot.shadow.source_digest import SOURCE_PATHS, compute_strategy_source_digest


def layout(tmp_path):
    project = tmp_path / "golden-cherry"
    for relative in set(SOURCE_PATHS):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n")
    shared = tmp_path / "polybot-observability" / "src" / "polybot_observability"
    shared.mkdir(parents=True)
    for name in (*MARKET_DATA_SOURCE_FILES, "config_contract.py", "execution_ledger.py", "run_audit.py"):
        (shared / name).write_text(name + "\n")
    return project, shared


def digests(project):
    return (compute_strategy_source_digest(project),)


@pytest.mark.parametrize("name", MARKET_DATA_SOURCE_FILES)
def test_shared_runtime_change_splits_each_source_cohort(tmp_path, name):
    project, shared = layout(tmp_path)
    before = digests(project)
    (shared / name).write_text("changed runtime bytes\n")
    assert all(old != new for old, new in zip(before, digests(project)))


def test_unrelated_file_does_not_change_cohort(tmp_path):
    project, shared = layout(tmp_path)
    before = digests(project)
    (shared / "unrelated_dashboard.py").write_text("not a runtime dependency\n")
    assert digests(project) == before


def test_missing_shared_runtime_cannot_claim_a_complete_source_digest(tmp_path):
    project, shared = layout(tmp_path)
    (shared / "market_data_levels.py").unlink()
    with pytest.raises(ValueError, match="missing.*market_data_levels"):
        digests(project)
