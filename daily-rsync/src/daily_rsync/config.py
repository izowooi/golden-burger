from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    data_root: Path
    ssh_host: str = "macmini-m5"
    expected_ip: str = "192.168.50.23"
    remote_jenkins_home: str = "/Users/jongwoopark/.jenkins"
    remote_staging_root: str = "/Users/jongwoopark/.cache/daily-rsync"
    initial_log_days: int = 60
    log_retention_days: int = 365
    minimum_free_bytes: int = 50 * GIB
    batch_file_limit: int = 1000
    batch_byte_limit: int = 2 * GIB
    default_job_pattern: str = "polybot-*"

    @property
    def catalog_path(self) -> Path:
        return self.data_root / "catalog.sqlite3"

    @property
    def plans_root(self) -> Path:
        return self.data_root / "plans"

    @property
    def incoming_root(self) -> Path:
        return self.data_root / "incoming"

    @property
    def bundles_root(self) -> Path:
        return self.data_root / "bundles"


def _positive_int(payload: dict[str, object], key: str, default: int) -> int:
    value = int(payload.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def load_config(path: Path | None = None) -> AppConfig:
    project_root = Path(__file__).resolve().parents[2]
    config_path = path or project_root / "config.local.toml"
    payload: dict[str, object] = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)

    data_value = os.getenv("DAILY_RSYNC_DATA_ROOT") or str(payload.get("data_root", "data"))
    data_root = Path(data_value).expanduser()
    if not data_root.is_absolute():
        data_root = project_root / data_root
    data_root = data_root.resolve()

    config = AppConfig(
        project_root=project_root,
        data_root=data_root,
        ssh_host=os.getenv("DAILY_RSYNC_SSH_HOST") or str(payload.get("ssh_host", "macmini-m5")),
        expected_ip=str(payload.get("expected_ip", "192.168.50.23")),
        remote_jenkins_home=str(payload.get("remote_jenkins_home", "/Users/jongwoopark/.jenkins")),
        remote_staging_root=str(
            payload.get("remote_staging_root", "/Users/jongwoopark/.cache/daily-rsync")
        ),
        initial_log_days=_positive_int(payload, "initial_log_days", 60),
        log_retention_days=_positive_int(payload, "log_retention_days", 365),
        minimum_free_bytes=_positive_int(payload, "minimum_free_gb", 50) * GIB,
        batch_file_limit=_positive_int(payload, "batch_file_limit", 1000),
        batch_byte_limit=_positive_int(payload, "batch_byte_limit_gb", 2) * GIB,
        default_job_pattern=str(payload.get("default_job_pattern", "polybot-*")),
    )
    ensure_runtime_directories(config)
    return config


def ensure_runtime_directories(config: AppConfig) -> None:
    for path in (
        config.data_root,
        config.plans_root,
        config.incoming_root,
        config.bundles_root,
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
