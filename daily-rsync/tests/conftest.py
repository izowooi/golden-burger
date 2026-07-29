from __future__ import annotations

from pathlib import Path

import pytest

from daily_rsync.config import AppConfig, ensure_runtime_directories


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        ssh_host="test-host",
        expected_ip="127.0.0.1",
        remote_jenkins_home=str(tmp_path / "remote" / ".jenkins"),
        remote_staging_root=str(tmp_path / "remote" / ".cache" / "daily-rsync"),
        minimum_free_bytes=1,
    )
    ensure_runtime_directories(config)
    return config
