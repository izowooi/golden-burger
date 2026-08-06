from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import JobInventory


class RemoteCommandError(RuntimeError):
    pass


class RemoteClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._helper_source = (
            Path(__file__).with_name("remote_agent.py").read_text(encoding="utf-8")
        )

    @property
    def ssh_base(self) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            self.config.ssh_host,
        ]

    @property
    def workspace_arguments(self) -> list[str]:
        return [
            value
            for root in self.config.effective_remote_workspace_roots
            for value in ("--workspace-root", root)
        ]

    def _helper(self, command: str, arguments: list[str], *, timeout: int = 120) -> dict[str, Any]:
        remote_command = " ".join(
            shlex.quote(value) for value in ["python3", "-", command, *arguments]
        )
        process = subprocess.run(
            [*self.ssh_base, remote_command],
            input=self._helper_source,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.strip().splitlines()
            message = detail[-1] if detail else f"exit={process.returncode}"
            raise RemoteCommandError(f"remote {command} failed: {message}")
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        if not lines:
            raise RemoteCommandError(f"remote {command} returned no JSON")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as error:
            raise RemoteCommandError(
                f"remote {command} returned invalid JSON: {lines[-1][:200]}"
            ) from error

    def doctor(self) -> dict[str, Any]:
        return self._helper(
            "doctor",
            [
                "--jenkins-home",
                self.config.remote_jenkins_home,
                *self.workspace_arguments,
            ],
            timeout=30,
        )

    def scan(
        self,
        *,
        job: str | None,
        cutoff_epoch: float,
        archive_from_date: date | None = None,
        archive_to_date: date | None = None,
    ) -> list[JobInventory]:
        arguments = [
            "--jenkins-home",
            self.config.remote_jenkins_home,
            *self.workspace_arguments,
            "--cutoff-epoch",
            str(cutoff_epoch),
        ]
        if archive_from_date:
            arguments.extend(["--archive-from-date", archive_from_date.isoformat()])
        if archive_to_date:
            arguments.extend(["--archive-to-date", archive_to_date.isoformat()])
        if job:
            arguments.extend(["--job", job])
        payload = self._helper("scan", arguments, timeout=300)
        for item in payload.get("jobs", []):
            for artifact in item.get("artifacts", []):
                artifact["source"] = self.config.ssh_host
        return [JobInventory.from_dict(item) for item in payload.get("jobs", [])]

    def snapshot_database(
        self,
        remote_path: str,
        *,
        job: str,
        expected_workspace: str,
        expected_identity: dict[str, Any],
        expected_data_contract: str | None = None,
        expected_database_utc_date: str | None = None,
    ) -> dict[str, Any]:
        arguments = [
            "--jenkins-home",
            self.config.remote_jenkins_home,
            *self.workspace_arguments,
            "--source",
            remote_path,
            "--staging-root",
            self.config.remote_staging_root,
            "--job",
            job,
            "--expected-workspace",
            expected_workspace,
            "--expected-identity",
            json.dumps(expected_identity, sort_keys=True),
        ]
        if expected_data_contract:
            arguments.extend(["--expected-data-contract", expected_data_contract])
        if expected_database_utc_date:
            arguments.extend(["--expected-database-utc-date", expected_database_utc_date])
        return self._helper(
            "snapshot",
            arguments,
            timeout=7200,
        )

    def validate_workspace(
        self,
        *,
        job: str,
        expected_workspace: str,
        expected_identity: dict[str, Any],
    ) -> dict[str, Any]:
        return self._helper(
            "validate-workspace",
            [
                "--jenkins-home",
                self.config.remote_jenkins_home,
                *self.workspace_arguments,
                "--job",
                job,
                "--expected-workspace",
                expected_workspace,
                "--expected-identity",
                json.dumps(expected_identity, sort_keys=True),
            ],
            timeout=60,
        )

    def cleanup_snapshot(self, snapshot_path: str) -> None:
        run_directory = str(Path(snapshot_path).parent)
        self._helper(
            "cleanup",
            [
                "--staging-root",
                self.config.remote_staging_root,
                "--path",
                run_directory,
            ],
            timeout=60,
        )

    def rsync(
        self,
        *,
        remote_path: str,
        local_path: Path,
        compress: bool,
        timeout: int = 7200,
    ) -> subprocess.CompletedProcess[str]:
        local_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = ["rsync", "-a", "--partial", "--itemize-changes"]
        if compress:
            command.append("-z")
        command.extend([f"{self.config.ssh_host}:{shlex.quote(remote_path)}", str(local_path)])
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if process.returncode != 0:
            message = process.stderr.strip().splitlines()
            raise RemoteCommandError(
                f"rsync failed: {message[-1] if message else process.returncode}"
            )
        return process

    def rsync_files(
        self,
        *,
        remote_paths: list[str],
        local_root: Path,
        compress: bool = True,
        timeout: int = 7200,
    ) -> None:
        remote_root = Path(self.config.remote_jenkins_home)
        relatives: list[str] = []
        for value in remote_paths:
            path = Path(value)
            try:
                relative = path.relative_to(remote_root)
            except ValueError:
                raise ValueError(f"batch path is outside Jenkins home: {value}") from None
            if path.is_absolute() and ".." not in relative.parts:
                relatives.append(relative.as_posix())
            else:
                raise ValueError(f"unsafe batch path: {value}")
        local_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="daily-rsync-files-",
            delete=False,
        ) as handle:
            handle.write("\n".join(relatives) + "\n")
            list_path = Path(handle.name)
        try:
            command = [
                "rsync",
                "-a",
                "--partial",
                "--itemize-changes",
                f"--files-from={list_path}",
            ]
            if compress:
                command.append("-z")
            command.extend(
                [
                    f"{self.config.ssh_host}:{self.config.remote_jenkins_home}/",
                    f"{local_root}/",
                ]
            )
            process = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if process.returncode != 0:
                lines = process.stderr.strip().splitlines()
                raise RemoteCommandError(
                    f"batch rsync failed: {lines[-1] if lines else process.returncode}"
                )
        finally:
            list_path.unlink(missing_ok=True)
