from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .config import AppConfig
from .models import read_research_database_contract, research_archive_date


def _clone_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    process = subprocess.run(
        ["cp", "-c", str(source), str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        shutil.copy2(source, destination)
    destination.chmod(0o600)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _gzip_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quick_check(path: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        return [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    finally:
        connection.close()


def _row_date(row: Any) -> date | None:
    try:
        metadata = json.loads(row["metadata_json"])
        value = metadata.get("completed_at")
        return datetime.fromisoformat(value).date() if value else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _row_metadata(row: Any) -> dict[str, Any]:
    try:
        return json.loads(row["metadata_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _research_row_date(row: Any) -> date | None:
    archive_day = research_archive_date(str(row["remote_path"]))
    if archive_day is not None:
        return archive_day
    metadata = _row_metadata(row)
    if row["kind"] != "database_sim" or metadata.get("data_contract") != "research-full-v1":
        return None
    path = Path(row["local_path"] or "")
    if path.is_file():
        contract = read_research_database_contract(path)
        if contract is not None:
            return contract.database_utc_date
    try:
        return date.fromisoformat(str(metadata.get("archive_date")))
    except (TypeError, ValueError):
        return None


def _research_row_available(row: Any, archive_day: date) -> bool:
    path = Path(row["local_path"] or "")
    if not path.is_file():
        return False
    metadata = _row_metadata(row)
    if metadata.get("data_contract") != "research-full-v1":
        return False
    try:
        contract = read_research_database_contract(path)
    except (OSError, RuntimeError, sqlite3.Error):
        return False
    if contract is None or contract.database_utc_date != archive_day:
        return False
    if row["kind"] == "database_research_archive":
        if research_archive_date(str(row["remote_path"])) != archive_day:
            return False
    elif row["kind"] == "database_sim":
        # The active shard is useful for current-day inspection but is
        # mutable and never substitutes for a completed dated archive.
        return False
    if row["status"] == "SYNCED":
        return True
    if row["kind"] != "database_research_archive" or row["status"] != "SOURCE_MISSING":
        return False
    value = metadata.get("source_completed_at") or metadata.get("completed_at")
    try:
        cutoff = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        cutoff = cutoff.astimezone(UTC)
    except (TypeError, ValueError):
        try:
            cutoff = datetime.fromtimestamp(int(row["remote_mtime_ns"]) / 1_000_000_000, UTC)
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            return False
    required = datetime.combine(archive_day + timedelta(days=1), datetime.min.time(), UTC)
    return cutoff >= required


def _validate_local_artifact(row: Any) -> str:
    """Validate the exact catalog evidence before it can enter a bundle."""

    path = Path(row["local_path"] or "")
    if not path.is_file():
        raise RuntimeError(f"bundle artifact is missing locally: {path}")
    if row["status"] in {
        "RETENTION_DELETED",
        "IMMUTABLE_CONFLICT",
        "PROVENANCE_CONFLICT",
    }:
        raise RuntimeError(
            f"bundle artifact has unusable catalog status {row['status']}: {row['remote_path']}"
        )
    if str(row["kind"]).startswith("database"):
        integrity = _quick_check(path)
        if integrity != ["ok"]:
            raise RuntimeError(f"bundle database quick_check failed for {path}: {integrity}")
        digest = _sha256(path)
    elif path.suffix == ".gz":
        digest = _gzip_content_sha256(path)
    else:
        digest = _sha256(path)
    expected = str(row["local_sha256"] or "")
    if not expected or digest != expected:
        raise RuntimeError(f"bundle artifact checksum mismatch: {path}")
    return digest


def create_bundle(
    config: AppConfig,
    *,
    job: str,
    strategy: str,
    from_date: date,
    to_date: date,
) -> Path:
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    catalog = Catalog(config.catalog_path)
    rows = catalog.list_artifacts(source=config.ssh_host, job=job, strategy=strategy)
    open_conflicts = catalog.list_open_conflicts(
        source=config.ssh_host,
        job=job,
        strategy=strategy,
    )
    if open_conflicts:
        raise RuntimeError(
            "bundle refuses unresolved artifact conflict(s): "
            + ", ".join(f"#{row['id']}:{row['conflict_type']}" for row in open_conflicts)
        )
    selected: list[Any] = []
    research_rows = [
        row
        for row in rows
        if row["kind"] == "database_research_archive"
        or (
            row["kind"] == "database_sim"
            and _row_metadata(row).get("data_contract") == "research-full-v1"
        )
    ]
    research_source_keys = {str(row["source_key"]) for row in research_rows}
    for row in rows:
        if str(row["source_key"]) in research_source_keys:
            continue
        if row["kind"].startswith("database"):
            selected.append(row)
            continue
        artifact_date = _row_date(row)
        if artifact_date and from_date <= artifact_date <= to_date:
            selected.append(row)
    if research_rows:
        requested = {
            from_date + timedelta(days=offset) for offset in range((to_date - from_date).days + 1)
        }
        rows_by_runtime: dict[str, list[Any]] = {}
        for row in research_rows:
            if row["kind"] != "database_research_archive":
                continue
            archive_day = _research_row_date(row)
            if archive_day is None or not from_date <= archive_day <= to_date:
                continue
            rows_by_runtime.setdefault(str(row["runtime_job"] or "default"), []).append(row)

        covered_by_runtime: dict[str, set[date]] = {}
        eligible_by_runtime: dict[str, list[Any]] = {}
        for runtime_job, runtime_rows in rows_by_runtime.items():
            covered: set[date] = set()
            eligible: list[Any] = []
            for row in runtime_rows:
                archive_day = _research_row_date(row)
                if archive_day is None:
                    continue
                _validate_local_artifact(row)
                if _research_row_available(row, archive_day):
                    covered.add(archive_day)
                    eligible.append(row)
            covered_by_runtime[runtime_job] = covered
            eligible_by_runtime[runtime_job] = eligible

        complete_runtime_jobs = sorted(
            runtime_job
            for runtime_job, covered in covered_by_runtime.items()
            if requested <= covered
        )
        if not complete_runtime_jobs:
            union_covered = (
                set().union(*covered_by_runtime.values()) if covered_by_runtime else set()
            )
            missing = sorted(requested - union_covered)
            if not missing and union_covered:
                raise RuntimeError(
                    "bundle research dates are split across runtime jobs; "
                    "no single runtime has complete UTC-day coverage"
                )
            raise RuntimeError(
                "bundle research coverage is incomplete; missing UTC date(s): "
                + ", ".join(day.isoformat() for day in missing)
            )
        for runtime_job in complete_runtime_jobs:
            selected.extend(eligible_by_runtime[runtime_job])

    validated_digests: dict[str, str] = {}
    for row in selected:
        validated_digests[str(row["source_key"])] = _validate_local_artifact(row)
    if not any(
        row["kind"].startswith("database")
        and row["local_path"]
        and Path(row["local_path"]).is_file()
        for row in selected
    ):
        raise RuntimeError("bundle requires at least one synchronized database")

    logical_bytes = sum(
        Path(row["local_path"]).stat().st_size
        for row in selected
        if row["local_path"] and Path(row["local_path"]).is_file()
    )
    free = shutil.disk_usage(config.bundles_root).free
    if free - logical_bytes < config.minimum_free_bytes:
        raise RuntimeError("bundle would violate the 50 GiB free-space floor")

    bundle_id = (
        f"{job}-{strategy}-{from_date.isoformat()}-{to_date.isoformat()}-{uuid.uuid4().hex[:8]}"
    )
    root = config.bundles_root / bundle_id
    root.mkdir(parents=True, mode=0o700)
    records: list[dict[str, object]] = []
    for row in selected:
        source = Path(row["local_path"])
        if not source.is_file():
            continue
        if row["kind"].startswith("database"):
            destination = root / "databases" / source.name
            if destination.exists():
                destination = (
                    root / "databases" / f"{row['runtime_job'] or 'default'}-{source.name}"
                )
            _clone_or_copy(source, destination)
        else:
            build = row["build_number"]
            name = f"jenkins-{build}.log" if build is not None else source.name
            if name.endswith(".gz"):
                name = name[:-3]
            destination = root / "logs" / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if source.suffix == ".gz":
                with gzip.open(source, "rb") as input_file, destination.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=8 * 1024 * 1024)
            else:
                _clone_or_copy(source, destination)
            destination.chmod(0o600)
        metadata = _row_metadata(row)
        archive_date = research_archive_date(str(row["remote_path"]))
        records.append(
            {
                "kind": row["kind"],
                "canonical": bool(
                    metadata.get(
                        "canonical",
                        row["kind"] in {"database_live", "database_sim"},
                    )
                ),
                "mode": metadata.get(
                    "mode",
                    (
                        "sim"
                        if row["kind"] in {"database_sim", "database_research_archive"}
                        else "live"
                        if row["kind"] == "database_live"
                        else None
                    ),
                ),
                "archive_date": (
                    archive_date.isoformat() if archive_date else metadata.get("archive_date")
                ),
                "data_contract": metadata.get("data_contract"),
                "source_key": row["source_key"],
                "source": str(source),
                "bundle_path": str(destination.relative_to(root)),
                "sha256": validated_digests[str(row["source_key"])],
                "remote_path": row["remote_path"],
            }
        )

    manifest = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "created_at": datetime.now(UTC).isoformat(),
        "job": job,
        "strategy": strategy,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "artifacts": records,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"# AI evidence bundle\n\n"
        f"- Jenkins job: `{job}`\n"
        f"- Strategy: `{strategy}`\n"
        f"- Range: `{from_date}` ~ `{to_date}`\n"
        f"- Artifacts: `{len(records)}`\n\n"
        "SQLite와 로그는 이 폴더 안에서 독립적으로 읽을 수 있습니다. "
        "manifest의 hash와 source provenance를 보존하세요.\n",
        encoding="utf-8",
    )
    for path in (root / "manifest.json", root / "README.md"):
        path.chmod(0o600)
    return root
