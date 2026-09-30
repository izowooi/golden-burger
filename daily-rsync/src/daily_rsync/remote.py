from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from polybot_observability.market_data_bundle import closure_digest
from polybot_observability.market_data_store import validate_hashes

from .config import AppConfig, validate_public_source
from .models import JobInventory


class RemoteCommandError(RuntimeError):
    pass


class PublicPayloadBundleLimitError(RemoteCommandError):
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

    def _public_command(
        self,
        arguments: list[str],
        *,
        hashes=None,
        payload_hashes=None,
        observation_after=None,
        scalar_request=None,
    ) -> dict[str, Any]:
        validate_public_source(self.config, required=True)
        command = shlex.join(
            [
                str(self.config.remote_public_python),
                "-m",
                "polybot_observability.market_data_bundle",
                *arguments,
            ]
        )
        request = scalar_request
        if request is None and hashes is not None:
            request = {"hashes": hashes}
            if payload_hashes is not None:
                request["payload_hashes"] = payload_hashes
            if observation_after is not None:
                request["observation_after"] = observation_after
        process = subprocess.run(
            [*self.ssh_base, command],
            input=json.dumps(request) if request is not None else "",
            text=True,
            capture_output=True,
            timeout=7200,
            check=False,
        )
        if process.returncode:
            try:
                error = json.loads(process.stderr)
            except (ValueError, TypeError):
                error = {}
            if isinstance(error, dict) and error.get("error") == "BUNDLE_LIMIT":
                raise PublicPayloadBundleLimitError("remote public payload batch exceeds limit")
            # The remote process must never echo payload bodies into an error log.
            raise RemoteCommandError("remote public payload command failed")
        if len(process.stdout) > 262_144:
            raise RemoteCommandError("remote public payload response exceeds limit")
        try:
            payload = json.loads(process.stdout)
        except ValueError as error:
            raise RemoteCommandError("remote public payload response is invalid") from error
        if not isinstance(payload, dict):
            raise RemoteCommandError("remote public payload response must be an object")
        return payload

    def _validate_public_bundle_path(self, value: object) -> str:
        root = self.config.remote_staging_root
        if (
            not isinstance(value, str)
            or not value.startswith("/")
            or posixpath.normpath(value) != value
            or "\x00" in value
        ):
            raise RemoteCommandError("unsafe remote public bundle path")
        parent = posixpath.dirname(value)
        if (
            posixpath.dirname(parent) != root
            or not re.fullmatch(r"shared-payload-[0-9a-f]{32}", posixpath.basename(parent))
            or posixpath.basename(value) != "payloads.db"
        ):
            raise RemoteCommandError("remote public bundle is outside configured staging")
        return value

    def export_public_payloads(
        self, hashes: list[str], *, payload_hashes=None, observation_after=None
    ) -> dict[str, Any]:
        validate_hashes(hashes)
        requested = sorted(set(hashes))
        from polybot_observability.market_data_bundle import validate_observation_cursor

        validate_observation_cursor(observation_after, requested)
        if payload_hashes is not None:
            validate_hashes(payload_hashes)
            payload_hashes = sorted(set(payload_hashes))
            if not set(payload_hashes) <= set(requested):
                raise ValueError("export bodies must belong to requested public closure")
        bodies = requested if payload_hashes is None else payload_hashes
        if not requested:
            raise ValueError("public payload export requires hashes")
        payload = self._public_command(
            [
                "export",
                "--db",
                str(self.config.remote_public_db),
                "--storage-root",
                str(self.config.remote_public_storage_root),
                "--staging-root",
                self.config.remote_staging_root,
            ],
            hashes=requested,
            payload_hashes=payload_hashes,
            observation_after=observation_after,
        )
        self._validate_public_bundle_path(payload.get("bundle_path"))
        manifest = payload.get("manifest")
        identity = payload.get("source_identity")
        if not isinstance(manifest, dict) or not isinstance(identity, dict):
            raise RemoteCommandError("public bundle attestation is missing")
        contract = manifest.get("contract")
        if contract == "public-payload-bundle-v2":
            validate_observation_cursor(manifest.get("next_observation_after"), requested)
            if (
                manifest.get("observation_after") != observation_after
                or manifest.get("observation_snapshot") != "per-page-read-snapshot-v1"
                or (
                    manifest.get("next_observation_after") is not None
                    and observation_after is not None
                    and manifest["next_observation_after"] <= observation_after
                )
            ):
                raise RemoteCommandError("public observation continuation is invalid")
            if (
                manifest.get("reference_hashes") != requested
                or type(manifest.get("observation_count")) is not int
                or not 0 <= manifest["observation_count"] <= 100_000
                or type(manifest.get("index_bytes")) is not int
                or not 0 <= manifest["index_bytes"] <= 64 << 20
                or not isinstance(manifest.get("observation_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", manifest["observation_sha256"])
            ):
                raise RemoteCommandError("public observation index attestation is invalid")
        elif contract != "public-payload-bundle-v1" or payload_hashes is not None:
            raise RemoteCommandError("public observation refresh requires bundle v2")
        if (
            manifest.get("status") != "VERIFIED"
            or manifest.get("payload_hashes") != bodies
            or manifest.get("closure_sha256") != closure_digest(requested)
            or manifest.get("payload_count") != len(bodies)
            or not isinstance(manifest.get("file_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["file_sha256"])
        ):
            raise RemoteCommandError("public bundle manifest does not match requested hashes")
        if (
            type(manifest.get("raw_bytes")) is not int
            or not 0 <= manifest["raw_bytes"] <= 256 << 20
        ):
            raise RemoteCommandError("public bundle raw size exceeds limit")
        if (
            set(identity) != {"db_path", "storage_root", "device", "inode"}
            or identity.get("db_path") != self.config.remote_public_db
            or identity.get("storage_root") != self.config.remote_public_storage_root
            or any(
                type(identity.get(key)) is not int or identity[key] < 0
                for key in ("device", "inode")
            )
        ):
            raise RemoteCommandError("public bundle source identity mismatch")
        return payload

    def export_public_scalars(
        self,
        record_ids: list[int],
        *,
        authority_uuid: str,
        transferred_ids=None,
        receipt_after=None,
    ) -> dict[str, Any]:
        return self._export_public_records(record_ids, authority_uuid=authority_uuid,
                                           transferred_ids=transferred_ids, receipt_after=receipt_after,
                                           projection=False)

    def export_public_projections(
        self, record_ids: list[int], *, authority_uuid: str,
        transferred_ids=None, receipt_after=None,
    ) -> dict[str, Any]:
        return self._export_public_records(record_ids, authority_uuid=authority_uuid,
                                           transferred_ids=transferred_ids, receipt_after=receipt_after,
                                           projection=True)

    def _export_public_records(
        self, record_ids: list[int], *, authority_uuid: str,
        transferred_ids, receipt_after, projection: bool,
    ) -> dict[str, Any]:
        label = "projection" if projection else "scalar"
        from polybot_observability.market_data_scalar_bundle import (
            CONTRACT,
            PAGE_SIZE,
            validate_cursor,
            validate_ids,
        )
        if projection:
            from polybot_observability.market_data_projection_bundle import CONTRACT, PAGE_SIZE, validate_cursor
        from polybot_observability.market_data_scalars import validate_authority

        validate_ids(record_ids)
        validate_authority(authority_uuid)
        if transferred_ids is not None:
            validate_ids(transferred_ids)
        requested = sorted(set(record_ids))
        bodies = requested if transferred_ids is None else sorted(set(transferred_ids))
        validate_ids(bodies)
        validate_cursor(receipt_after, requested)
        if (
            not set(bodies) <= set(requested)
            or not isinstance(authority_uuid, str)
            or len(authority_uuid) > 64
        ):
            raise ValueError(f"{label} export requires requested shared records")
        payload = self._public_command(
            [
                "export-projections" if projection else "export-scalars",
                "--db",
                str(self.config.remote_public_db),
                "--storage-root",
                str(self.config.remote_public_storage_root),
                "--staging-root",
                self.config.remote_staging_root,
            ],
            scalar_request={
                "record_ids": requested,
                "authority_uuid": authority_uuid,
                "transferred_ids": bodies,
                "receipt_after": receipt_after,
            },
        )
        self._validate_public_bundle_path(payload.get("bundle_path"))
        manifest, identity = payload.get("manifest"), payload.get("source_identity")
        if not isinstance(manifest, dict) or not isinstance(identity, dict):
            raise RemoteCommandError(f"public {label} attestation is missing")
        validate_cursor(manifest.get("next_receipt_after"), requested)
        if (
            manifest.get("contract") != CONTRACT
            or manifest.get("status") != "VERIFIED"
            or manifest.get("record_ids") != requested
            or manifest.get("transferred_ids") != bodies
            or manifest.get("authority_uuid") != authority_uuid
            or type(manifest.get("record_count")) is not int
            or manifest.get("record_count") != len(bodies)
            or manifest.get("receipt_after") != receipt_after
            or manifest.get("receipt_snapshot") != "per-page-read-snapshot-v1"
            or (
                manifest.get("next_receipt_after") is not None
                and receipt_after is not None
                and manifest["next_receipt_after"] <= receipt_after
            )
        ):
            raise RemoteCommandError(f"public {label} bundle does not match request")
        record_hashes = manifest.get("record_hashes")
        if (
            not isinstance(record_hashes, list)
            or len(record_hashes) != len(requested)
            or any(
                not isinstance(row, list)
                or len(row) != (3 if projection else 2)
                or type(row[0]) is not int
                or row[0] != record_id
                or not isinstance(row[-1], str)
                or not re.fullmatch(r"[0-9a-f]{64}", row[-1])
                for row, record_id in zip(record_hashes, requested, strict=True)
            )
        ):
            raise RemoteCommandError(f"public {label} record hashes are invalid")
        if projection:
            from polybot_observability.market_data_projection_profiles import projection_profile
            for row in record_hashes:
                projection_profile(row[1])
            kinds = {row[0]: row[1] for row in record_hashes}
            for cursor in (receipt_after, manifest.get("next_receipt_after")):
                if cursor is not None and kinds.get(cursor[4]) != cursor[3]:
                    raise RemoteCommandError("public projection cursor kind differs from its record")
        expected_hash = hashlib.sha256(
            b"".join(
                json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in record_hashes
            )
        ).hexdigest()
        if manifest.get("closure_sha256") != expected_hash:
            raise RemoteCommandError(f"public {label} closure checksum is invalid")
        for key in ("file_sha256", "receipt_sha256"):
            if not isinstance(manifest.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{64}", manifest[key]
            ):
                raise RemoteCommandError(f"public {label} checksum is invalid")
        for key, maximum in (
            ("raw_bytes", 256 << 20),
            ("index_bytes", 64 << 20),
            ("receipt_count", PAGE_SIZE),
        ):
            if type(manifest.get(key)) is not int or not 0 <= manifest[key] <= maximum:
                raise RemoteCommandError(f"public {label} size/count is invalid")
        if (
            set(identity) != {"db_path", "storage_root", "device", "inode"}
            or identity.get("db_path") != self.config.remote_public_db
            or identity.get("storage_root") != self.config.remote_public_storage_root
            or any(
                type(identity.get(key)) is not int or identity[key] < 0
                for key in ("device", "inode")
            )
        ):
            raise RemoteCommandError(f"public {label} source identity mismatch")
        return payload

    def cleanup_public_payloads(self, bundle_path: str, expected_sha: str) -> None:
        self._validate_public_bundle_path(bundle_path)
        validate_hashes([expected_sha])
        response = self._public_command(
            [
                "cleanup",
                "--bundle",
                bundle_path,
                "--staging-root",
                self.config.remote_staging_root,
                "--expected-sha",
                expected_sha,
            ]
        )
        if response.get("status") != "REMOVED":
            raise RemoteCommandError("remote public bundle cleanup was not confirmed")

    def rsync(
        self,
        *,
        remote_path: str,
        local_path: Path,
        compress: bool,
        timeout: int = 7200,
    ) -> subprocess.CompletedProcess[str]:
        local_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # ``local_path`` is an untrusted incoming ``*.partial`` staging file,
        # never the canonical latest/pinned artifact. Updating it in place lets
        # rsync reuse unchanged SQLite pages on slower external data volumes;
        # the sync service still requires the planned SHA-256 and quick_check
        # before atomically promoting the staging file.
        command = ["rsync", "-a", "--partial", "--inplace", "--itemize-changes"]
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

    def existing_files(self, *, remote_paths: list[str]) -> set[str]:
        """Return console paths that still exist under Jenkins home.

        Keep each SSH command comfortably below platform command-line limits;
        a normal console batch can contain one thousand paths.
        """

        existing: set[str] = set()
        for offset in range(0, len(remote_paths), 200):
            chunk = remote_paths[offset : offset + 200]
            payload = self._helper(
                "existing-files",
                [
                    "--jenkins-home",
                    self.config.remote_jenkins_home,
                    "--paths-json",
                    json.dumps(chunk, ensure_ascii=False),
                ],
                timeout=60,
            )
            reported = payload.get("existing", [])
            if not isinstance(reported, list) or not all(
                isinstance(value, str) for value in reported
            ):
                raise RemoteCommandError("remote existing-files returned invalid paths")
            unexpected = set(reported) - set(chunk)
            if unexpected:
                raise RemoteCommandError("remote existing-files returned an unrequested path")
            existing.update(reported)
        return existing
