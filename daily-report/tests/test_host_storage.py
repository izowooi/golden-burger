"""Tests for mounted filesystem collection and atomic Supabase persistence."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from polybot_reporter.storage.host_storage import (
    DiskSnapshot,
    DiskUsage,
    HostStorageError,
    HostStorageWriteError,
    MountSpec,
    SupabaseHostStorageWriter,
    collect_disk_snapshots,
    filesystem_mount_root,
    parse_mount_specs,
)
from polybot_reporter.storage.supabase_writer import SupabaseConfigurationError


class FakeRpc:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.calls.append((self.name, self.params))
        if self.name == SupabaseHostStorageWriter.PREFLIGHT_RPC:
            return SimpleNamespace(data={"contract_version": "pb-storage/v1"})
        if self.name == SupabaseHostStorageWriter.SNAPSHOT_RPC:
            return SimpleNamespace(
                data={
                    "contract_version": "pb-storage/v1",
                    "report_date": self.params["p_report_date"],
                    "host_id": self.params["p_host_id"],
                    "mount_count": len(self.params["p_mounts"]),
                    "reported_at": self.params["p_reported_at"],
                }
            )
        raise RuntimeError("unknown RPC")


class FakeClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        return FakeRpc(self, name, params)


def snapshot(mount_id="internal"):
    return DiskSnapshot(
        mount_id=mount_id,
        mount_label=mount_id,
        mount_path="/System/Volumes/Data",
        total_bytes=256_000_000_000,
        used_bytes=200_000_000_000,
        available_bytes=56_000_000_000,
    )


def test_parse_mount_specs_supports_labels_and_rejects_relative_paths():
    specs = parse_mount_specs(
        ["internal=/System/Volumes/Data", "shadow-backup=/Volumes/Polybot"],
        ["internal=Mac mini internal"],
    )

    assert specs == [
        MountSpec("internal", Path("/System/Volumes/Data"), "Mac mini internal"),
        MountSpec("shadow-backup", Path("/Volumes/Polybot"), "shadow-backup"),
    ]

    with pytest.raises(HostStorageError, match="절대경로"):
        parse_mount_specs(["internal=relative/path"])


def test_parse_mount_specs_rejects_duplicate_and_unknown_label():
    with pytest.raises(HostStorageError, match="중복"):
        parse_mount_specs(["internal=/", "internal=/System/Volumes/Data"])
    with pytest.raises(HostStorageError, match="mount가 없는"):
        parse_mount_specs(["internal=/"], ["external=External"])


def test_collect_disk_snapshots_requires_a_real_mount(tmp_path):
    spec = MountSpec("internal", tmp_path, "Internal")

    with pytest.raises(HostStorageError, match="mount point"):
        collect_disk_snapshots([spec], mount_root=lambda _path: "/")


def test_collect_disk_snapshots_records_integer_capacity(tmp_path):
    spec = MountSpec("internal", tmp_path, "Internal")

    rows = collect_disk_snapshots(
        [spec],
        mount_root=lambda path: path,
        disk_usage=lambda _path: DiskUsage(1_000, 600, 400),
    )

    assert rows == [
        DiskSnapshot(
            mount_id="internal",
            mount_label="Internal",
            mount_path=str(tmp_path.resolve()),
            total_bytes=1_000,
            used_bytes=600,
            available_bytes=400,
        )
    ]


def test_filesystem_mount_root_parses_a_mount_path_with_spaces(monkeypatch):
    monkeypatch.setattr(
        "polybot_reporter.storage.host_storage.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=(
                "Filesystem 512-blocks Used Available Capacity Mounted on\n"
                "/dev/disk9s1 1000 400 600 40% /Volumes/Shadow Backup\n"
            )
        ),
    )

    assert filesystem_mount_root("/Volumes/Shadow Backup") == "/Volumes/Shadow Backup"


def test_writer_rejects_publishable_key_before_client_creation():
    with pytest.raises(SupabaseConfigurationError, match="sb_publishable"):
        SupabaseHostStorageWriter(
            url="https://example.supabase.co",
            secret_key="sb_publishable_example_key",
        )


def test_writer_preflights_and_reconciles_atomic_snapshot():
    client = FakeClient()
    writer = SupabaseHostStorageWriter(client=client)
    reported_at = datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    result = writer.write_snapshot(
        report_date="2026-08-12",
        reported_at=reported_at,
        host_id="macmini-m5",
        snapshots=[snapshot(), snapshot("shadow-backup")],
    )

    assert result.report_date == "2026-08-12"
    assert result.host_id == "macmini-m5"
    assert result.mount_count == 2
    assert [name for name, _params in client.calls] == [
        SupabaseHostStorageWriter.PREFLIGHT_RPC,
        SupabaseHostStorageWriter.SNAPSHOT_RPC,
    ]
    payload = client.calls[-1][1]
    assert payload["p_reported_at"] == "2026-08-11T23:00:00+00:00"
    assert payload["p_mounts"][0]["total_bytes"] == 256_000_000_000


def test_writer_rejects_incomplete_rpc_response():
    class IncompleteRpc(FakeRpc):
        def execute(self):
            if self.name == SupabaseHostStorageWriter.PREFLIGHT_RPC:
                return super().execute()
            return SimpleNamespace(data={"contract_version": "pb-storage/v1"})

    class IncompleteClient(FakeClient):
        def rpc(self, name, params):
            return IncompleteRpc(self, name, params)

    with pytest.raises(HostStorageWriteError, match="불완전"):
        SupabaseHostStorageWriter(client=IncompleteClient()).write_snapshot(
            report_date="2026-08-12",
            reported_at=datetime.now(timezone.utc),
            host_id="macmini-m5",
            snapshots=[snapshot()],
        )


def test_storage_migration_is_atomic_and_server_only():
    repository = Path(__file__).resolve().parents[2]
    sql = (repository / "slack-data-collector/sql/pb_host_storage_v1.sql").read_text()
    normalized = " ".join(sql.lower().split())

    assert "alter table public.pb_host_storage_daily enable row level security" in normalized
    assert "security invoker" in normalized
    assert "pb_write_host_storage_snapshot_v1" in normalized
    assert "from public, anon, authenticated" in normalized
    assert "to service_role" in normalized
    assert "grant select on table public.pb_host_storage_daily to anon" not in normalized
