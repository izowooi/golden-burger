from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import verify_external_workspace as external


VOLUME_UUID = "10524A4E-097F-4E31-B4A9-800952269E5F"


def _layout(tmp_path: Path):
    mount = tmp_path / "t7"
    workspace = mount / "jenkins" / "polybot-do"
    workspace.mkdir(parents=True)
    sentinel = mount / ".golden-raspberry-volume"
    sentinel.write_text(
        f"profile={external.VOLUME_PROFILE}\nvolume_uuid={VOLUME_UUID}\n",
        encoding="utf-8",
    )
    host_pin = tmp_path / "host.uuid"
    host_pin.write_text(f"{VOLUME_UUID}\n", encoding="utf-8")
    return mount, workspace, sentinel, host_pin


def _mock_identity(monkeypatch, mount: Path, host_pin: Path):
    monkeypatch.setattr(
        external,
        "_diskutil_info",
        lambda _path: {
            "FilesystemType": "apfs",
            "MountPoint": str(mount),
            "Internal": False,
            "VolumeUUID": VOLUME_UUID,
        },
    )
    monkeypatch.setattr(
        external,
        "_device_id",
        lambda path: 20 if path == host_pin else 10,
    )


def test_external_workspace_writes_exact_daily_rsync_marker(monkeypatch, tmp_path):
    mount, workspace, sentinel, host_pin = _layout(tmp_path)
    _mock_identity(monkeypatch, mount, host_pin)

    result = external.verify_external_workspace(
        mount_root=mount,
        workspace=workspace,
        expected_workspace=workspace,
        job="polybot-do",
        sentinel=sentinel,
        host_uuid_pin=host_pin,
        write_marker=True,
    )

    assert result["status"] == "ok"
    assert json.loads((workspace / external.MARKER_NAME).read_text()) == {
        "schema_version": 1,
        "job": "polybot-do",
        "workspace": str(workspace),
    }


def test_external_workspace_rejects_shared_or_mismatched_job_path(monkeypatch, tmp_path):
    mount, workspace, sentinel, host_pin = _layout(tmp_path)
    _mock_identity(monkeypatch, mount, host_pin)

    with pytest.raises(external.WorkspaceVerificationError, match="canonical job path"):
        external.verify_external_workspace(
            mount_root=mount,
            workspace=workspace,
            expected_workspace=mount / "jenkins" / "golden-raspberry",
            job="polybot-do",
            sentinel=sentinel,
            host_uuid_pin=host_pin,
            write_marker=False,
        )


def test_external_workspace_rejects_uuid_mismatch(monkeypatch, tmp_path):
    mount, workspace, sentinel, host_pin = _layout(tmp_path)
    host_pin.write_text("00000000-0000-0000-0000-000000000000\n", encoding="utf-8")
    _mock_identity(monkeypatch, mount, host_pin)

    with pytest.raises(external.WorkspaceVerificationError, match="does not match both pins"):
        external.verify_external_workspace(
            mount_root=mount,
            workspace=workspace,
            expected_workspace=workspace,
            job="polybot-do",
            sentinel=sentinel,
            host_uuid_pin=host_pin,
            write_marker=False,
        )


def test_external_workspace_rejects_host_pin_on_external_device(monkeypatch, tmp_path):
    mount, workspace, sentinel, host_pin = _layout(tmp_path)
    _mock_identity(monkeypatch, mount, host_pin)
    monkeypatch.setattr(external, "_device_id", lambda _path: 10)

    with pytest.raises(external.WorkspaceVerificationError, match="not stored off-volume"):
        external.verify_external_workspace(
            mount_root=mount,
            workspace=workspace,
            expected_workspace=workspace,
            job="polybot-do",
            sentinel=sentinel,
            host_uuid_pin=host_pin,
            write_marker=False,
        )
