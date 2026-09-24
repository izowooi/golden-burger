from __future__ import annotations

import subprocess
from pathlib import Path

from daily_rsync.remote import RemoteClient


def test_database_rsync_updates_only_incoming_partial_in_place(
    app_config, monkeypatch, tmp_path: Path
) -> None:
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    destination = tmp_path / "incoming" / "snapshot.db.partial"

    RemoteClient(app_config).rsync(
        remote_path="/remote/staging/snapshot.db",
        local_path=destination,
        compress=False,
    )

    assert captured[:5] == [
        "rsync",
        "-a",
        "--partial",
        "--inplace",
        "--itemize-changes",
    ]
    assert captured[-1] == str(destination)
