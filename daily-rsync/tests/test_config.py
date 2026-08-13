from __future__ import annotations

import os
from pathlib import Path

import pytest

from daily_rsync.config import load_config


def test_workspace_roots_default_to_jenkins_home_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DAILY_RSYNC_REMOTE_WORKSPACE_ROOTS", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'remote_jenkins_home = "{tmp_path}/remote/.jenkins"\ndata_root = "{tmp_path}/data"\n',
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.effective_remote_workspace_roots == (f"{tmp_path}/remote/.jenkins/workspace",)


def test_workspace_root_environment_override_uses_platform_path_separator(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first" / "workspace"
    second = tmp_path / "second" / "workspace"
    monkeypatch.setenv(
        "DAILY_RSYNC_REMOTE_WORKSPACE_ROOTS",
        os.pathsep.join((str(first), str(second))),
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'data_root = "{tmp_path}/data"\n', encoding="utf-8")

    config = load_config(config_path)

    assert config.effective_remote_workspace_roots == (str(first), str(second))


def test_workspace_epochs_are_explicit_exact_workspace_mappings(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''data_root = "{tmp_path}/data"

[workspace_epochs]
"/Volumes/t7/jenkins/polybot-do" = "external-v2"
''',
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.workspace_epoch_for("/Volumes/t7/jenkins/polybot-do") == "external-v2"
    assert config.workspace_epoch_for("/Volumes/t7/jenkins/polybot-re") is None


@pytest.mark.parametrize(
    ("workspace", "epoch"),
    [
        ("Volumes/t7/jenkins/polybot-do", "external-v2"),
        ("/Volumes/t7/jenkins/polybot-do/", "external-v2"),
        ("/Volumes/t7/jenkins/polybot-do", "../unsafe"),
    ],
)
def test_workspace_epochs_reject_ambiguous_or_unsafe_values(
    tmp_path: Path,
    workspace: str,
    epoch: str,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''data_root = "{tmp_path}/data"

[workspace_epochs]
"{workspace}" = "{epoch}"
''',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(config_path)
