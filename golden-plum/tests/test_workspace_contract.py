from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from polybot.config import RUNTIME_SPECS


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "plum_workspace", ROOT / "scripts" / "verify_external_workspace.py"
)
assert SPEC and SPEC.loader
workspace_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = workspace_module
SPEC.loader.exec_module(workspace_module)


def test_workspace_specs_match_atomic_runtime_registry() -> None:
    registered_external_runtimes: set[str] = set()
    for job, workspace_spec in workspace_module.WORKSPACE_SPECS.items():
        for runtime_job in workspace_spec.runtime_jobs:
            registered_external_runtimes.add(runtime_job)
            runtime_spec = RUNTIME_SPECS[runtime_job]
            assert runtime_spec.jenkins_job == job
            assert runtime_spec.simulation_mode is True
            assert runtime_spec.hard_deadline_seconds == 50.0
            assert runtime_spec.cadence_seconds == 60
            assert runtime_spec.external_workspace_path == str(
                workspace_spec.workspace
            )

    expected_external_runtimes = {
        runtime_job
        for runtime_job, runtime_spec in RUNTIME_SPECS.items()
        if runtime_spec.external_workspace_path is not None
    }
    assert registered_external_runtimes == expected_external_runtimes


def test_cli_job_argument_uses_jenkins_job_name() -> None:
    parser = workspace_module._parser()
    args = parser.parse_args(
        [
            "--job",
            "polybot-gold",
            "--workspace",
            "/Volumes/t7/jenkins/polybot-gold",
            "--database",
            "/Volumes/t7/jenkins/polybot-gold/golden-plum/data/"
            "plum-shadow-gold-mlb-1m-v1/trades_sim.db",
        ]
    )
    assert args.job == "polybot-gold"

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--job",
                "plum-shadow-gold-mlb-1m-v1",
                "--workspace",
                "/Volumes/t7/jenkins/polybot-gold",
                "--database",
                "/tmp/trades_sim.db",
            ]
        )


@pytest.fixture
def trusted_volume(tmp_path, monkeypatch):
    volume_uuid = "11111111-2222-3333-4444-555555555555"
    mount_root = tmp_path / "Volumes" / "t7"
    mount_root.mkdir(parents=True)
    sentinel = mount_root / ".golden-raspberry-volume"
    sentinel.write_text(
        f"profile={workspace_module.VOLUME_PROFILE}\nvolume_uuid={volume_uuid}\n",
        encoding="utf-8",
    )
    host_pin = tmp_path / "host" / "volume.uuid"
    host_pin.parent.mkdir()
    host_pin.write_text(volume_uuid + "\n", encoding="utf-8")

    specs = {}
    for job, runtime, additional in (
        (
            "polybot-gold",
            "plum-shadow-gold-mlb-1m-v1",
            (
                "plum-shadow-gold-nfl-1m-v1",
                "plum-shadow-gold-nba-1m-v1",
                "plum-shadow-gold-nhl-1m-v1",
            ),
        ),
        ("polybot-silver", "plum-shadow-silver-1m-v1", ()),
    ):
        workspace = mount_root / "jenkins" / job
        (workspace / "golden-plum").mkdir(parents=True)
        specs[job] = workspace_module.WorkspaceSpec(
            job, runtime, workspace, additional
        )

    monkeypatch.setattr(workspace_module, "DEFAULT_MOUNT_ROOT", mount_root)
    monkeypatch.setattr(workspace_module, "DEFAULT_SENTINEL", sentinel)
    monkeypatch.setattr(workspace_module, "DEFAULT_HOST_UUID_PIN", host_pin)
    monkeypatch.setattr(workspace_module, "WORKSPACE_SPECS", specs)
    monkeypatch.setattr(
        workspace_module,
        "_diskutil_info",
        lambda path: {
            "FilesystemType": "apfs",
            "MountPoint": str(path),
            "Internal": False,
            "VolumeUUID": volume_uuid,
        },
    )
    monkeypatch.setattr(
        workspace_module,
        "_device_id",
        lambda path: 1 if Path(path) in {Path("/"), host_pin} else 2,
    )
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(
            total=1_000 * 1024**3,
            used=200 * 1024**3,
            free=800 * 1024**3,
        ),
    )
    return specs


@pytest.mark.parametrize("job", ["polybot-gold", "polybot-silver"])
def test_exact_external_workspace_writes_and_revalidates_marker(
    trusted_volume, job
) -> None:
    spec = trusted_volume[job]

    report = workspace_module.verify_external_workspace(
        job=job,
        workspace=spec.workspace,
        database=spec.database,
        write_marker=True,
    )

    assert report["status"] == "ok"
    assert report["runtime_job"] == spec.runtime_job
    assert report["database"] == str(spec.database)
    marker = spec.workspace / workspace_module.MARKER_NAME
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "job": job,
        "workspace": str(spec.workspace),
    }
    workspace_module.verify_external_workspace(
        job=job,
        workspace=spec.workspace,
        database=spec.database,
        write_marker=False,
    )


def test_missing_workspace_is_not_created(trusted_volume) -> None:
    old = trusted_volume["polybot-gold"]
    absent = old.workspace.parent / "absent-gold"
    spec = workspace_module.WorkspaceSpec(
        old.jenkins_job,
        old.runtime_job,
        absent,
    )
    workspace_module.WORKSPACE_SPECS[old.jenkins_job] = spec

    with pytest.raises(
        workspace_module.WorkspaceVerificationError,
        match="never creates",
    ):
        workspace_module.verify_external_workspace(
            job=old.jenkins_job,
            workspace=absent,
            database=spec.database,
            write_marker=True,
        )

    assert not absent.exists()


def test_symlink_workspace_ancestor_is_rejected(trusted_volume, tmp_path) -> None:
    old = trusted_volume["polybot-silver"]
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = old.workspace.parent / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    workspace = linked_parent / "polybot-silver"
    (real_parent / "polybot-silver" / "golden-plum").mkdir(parents=True)
    spec = workspace_module.WorkspaceSpec(
        old.jenkins_job,
        old.runtime_job,
        workspace,
    )
    workspace_module.WORKSPACE_SPECS[old.jenkins_job] = spec

    with pytest.raises(
        workspace_module.WorkspaceVerificationError,
        match="symlink component",
    ):
        workspace_module.verify_external_workspace(
            job=old.jenkins_job,
            workspace=workspace,
            database=spec.database,
            write_marker=True,
        )


def test_database_must_match_frozen_runtime_path(trusted_volume) -> None:
    spec = trusted_volume["polybot-gold"]
    wrong = spec.workspace / "golden-plum" / "data" / "other" / "trades_sim.db"

    with pytest.raises(
        workspace_module.WorkspaceVerificationError,
        match="database path differs",
    ):
        workspace_module.verify_external_workspace(
            job=spec.jenkins_job,
            workspace=spec.workspace,
            database=wrong,
            write_marker=True,
        )


@pytest.mark.parametrize("family", ["nfl", "nba", "nhl"])
def test_gold_accepts_each_registered_database_path(
    trusted_volume, family
) -> None:
    spec = trusted_volume["polybot-gold"]
    runtime = f"plum-shadow-gold-{family}-1m-v1"
    database = spec.database_for_runtime(runtime)

    report = workspace_module.verify_external_workspace(
        job=spec.jenkins_job,
        workspace=spec.workspace,
        database=database,
        write_marker=True,
    )

    assert report["runtime_job"] == runtime
    assert report["database"] == str(database)


def test_low_free_space_fails_before_marker_write(
    trusted_volume, monkeypatch
) -> None:
    spec = trusted_volume["polybot-gold"]
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(
            total=1_000 * 1024**3,
            used=960 * 1024**3,
            free=40 * 1024**3,
        ),
    )

    with pytest.raises(
        workspace_module.WorkspaceVerificationError,
        match="less than 50 GiB",
    ):
        workspace_module.verify_external_workspace(
            job=spec.jenkins_job,
            workspace=spec.workspace,
            database=spec.database,
            write_marker=True,
        )

    assert not (spec.workspace / workspace_module.MARKER_NAME).exists()


def test_cli_cannot_weaken_frozen_free_space_floor(trusted_volume) -> None:
    spec = trusted_volume["polybot-gold"]
    with pytest.raises(
        workspace_module.WorkspaceVerificationError,
        match="cannot be lower",
    ):
        workspace_module.verify_external_workspace(
            job=spec.jenkins_job,
            workspace=spec.workspace,
            database=spec.database,
            write_marker=True,
            min_free_gib=1,
        )
