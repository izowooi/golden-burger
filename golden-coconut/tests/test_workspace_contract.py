from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "coconut_workspace", ROOT / "scripts" / "verify_external_workspace.py"
)
assert SPEC and SPEC.loader
workspace_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace_module)


def test_marker_has_exact_three_daily_rsync_keys(tmp_path):
    marker = workspace_module._write_marker(tmp_path)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "job": "polybot-gold",
        "workspace": "/Volumes/t7/jenkins/polybot-gold",
    }
    assert set(payload) == {"schema_version", "job", "workspace"}


def test_workspace_report_uses_current_runtime_without_changing_marker_schema():
    assert workspace_module.RUNTIME_JOB == "coconut-major-sports-lifecycle-5m-v5"
    assert workspace_module.JENKINS_JOB == "polybot-gold"


def test_wrong_jenkins_job_fails_before_mount_access(tmp_path):
    with pytest.raises(workspace_module.WorkspaceVerificationError, match="polybot-gold"):
        workspace_module.verify_external_workspace(
            mount_root=tmp_path / "absent",
            workspace=tmp_path / "workspace",
            expected_workspace=tmp_path / "workspace",
            job="wrong-job",
            sentinel=tmp_path / "sentinel",
            host_uuid_pin=tmp_path / "pin",
            write_marker=False,
        )


def test_jenkinsfile_checks_exact_marker_after_checkout():
    source = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    assert source.index("checkout scm") < source.index("--write-daily-rsync-marker")
    assert '"schema_version": 1' in source
    assert '"job": "polybot-gold"' in source
    assert '"workspace": "/Volumes/t7/jenkins/polybot-gold"' in source
    assert "if payload != expected or set(payload) != set(expected)" in source
