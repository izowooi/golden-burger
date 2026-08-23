from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import verify_external_workspace as verifier


UUID = "11111111-2222-3333-4444-555555555555"


def _layout(tmp_path):
    mount = tmp_path / "t7"
    workspace = mount / "jenkins" / verifier.JENKINS_JOB
    workspace.mkdir(parents=True)
    sentinel = mount / ".golden-raspberry-volume"
    sentinel.write_text(
        f"profile={verifier.VOLUME_PROFILE}\nvolume_uuid={UUID}\n",
        encoding="utf-8",
    )
    pin = tmp_path / "host" / "golden-raspberry-volume.uuid"
    pin.parent.mkdir()
    pin.write_text(UUID + "\n", encoding="utf-8")
    return mount, workspace, sentinel, pin


def _patch_identity(monkeypatch, mount, workspace, pin, *, uuid=UUID):
    monkeypatch.setattr(
        verifier,
        "_diskutil_info",
        lambda path: {
            "FilesystemType": "apfs",
            "MountPoint": str(mount),
            "Internal": False,
            "VolumeUUID": uuid,
        },
    )
    monkeypatch.setattr(
        verifier,
        "_device_id",
        lambda path: 20 if Path(path) == pin else 10,
    )


def test_reuses_existing_raspberry_anchors_and_writes_exact_marker(
    monkeypatch, tmp_path
):
    mount, workspace, sentinel, pin = _layout(tmp_path)
    _patch_identity(monkeypatch, mount, workspace, pin)
    result = verifier.verify_external_workspace(
        mount_root=mount,
        workspace=workspace,
        expected_workspace=workspace,
        job=verifier.JENKINS_JOB,
        sentinel=sentinel,
        host_uuid_pin=pin,
        write_marker=True,
    )
    marker = workspace / verifier.MARKER_NAME
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "job": "polybot-shadow-one",
        "workspace": str(workspace),
    }
    assert result["runtime_job"] == "strawberry-shadow-one-followup-v2a"
    assert result["trust_anchor_reused_from"] == "golden-raspberry"
    assert sentinel.exists() and pin.exists()


def test_wrong_uuid_fails_before_marker(monkeypatch, tmp_path):
    mount, workspace, sentinel, pin = _layout(tmp_path)
    _patch_identity(
        monkeypatch,
        mount,
        workspace,
        pin,
        uuid="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    )
    with pytest.raises(verifier.WorkspaceVerificationError, match="both trust anchors"):
        verifier.verify_external_workspace(
            mount_root=mount,
            workspace=workspace,
            expected_workspace=workspace,
            job=verifier.JENKINS_JOB,
            sentinel=sentinel,
            host_uuid_pin=pin,
            write_marker=True,
        )
    assert not (workspace / verifier.MARKER_NAME).exists()


def test_workspace_and_job_are_strawberry_distinct(monkeypatch, tmp_path):
    mount, workspace, sentinel, pin = _layout(tmp_path)
    _patch_identity(monkeypatch, mount, workspace, pin)
    with pytest.raises(verifier.WorkspaceVerificationError, match="Jenkins job"):
        verifier.verify_external_workspace(
            mount_root=mount,
            workspace=workspace,
            expected_workspace=workspace,
            job="polybot-do",
            sentinel=sentinel,
            host_uuid_pin=pin,
            write_marker=False,
        )


def test_shared_pin_must_be_off_volume(monkeypatch, tmp_path):
    mount, workspace, sentinel, pin = _layout(tmp_path)
    _patch_identity(monkeypatch, mount, workspace, pin)
    monkeypatch.setattr(verifier, "_device_id", lambda path: 10)
    with pytest.raises(verifier.WorkspaceVerificationError, match="off-volume"):
        verifier.verify_external_workspace(
            mount_root=mount,
            workspace=workspace,
            expected_workspace=workspace,
            job=verifier.JENKINS_JOB,
            sentinel=sentinel,
            host_uuid_pin=pin,
            write_marker=False,
        )
