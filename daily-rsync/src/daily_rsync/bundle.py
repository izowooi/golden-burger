from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .config import AppConfig


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


def _row_date(row: Any) -> date | None:
    try:
        metadata = json.loads(row["metadata_json"])
        value = metadata.get("completed_at")
        return datetime.fromisoformat(value).date() if value else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


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
    rows = catalog.list_artifacts(job=job, strategy=strategy)
    selected = []
    for row in rows:
        if row["kind"].startswith("database"):
            selected.append(row)
            continue
        artifact_date = _row_date(row)
        if artifact_date and from_date <= artifact_date <= to_date:
            selected.append(row)
    if not any(row["kind"].startswith("database") for row in selected):
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
        records.append(
            {
                "kind": row["kind"],
                "source_key": row["source_key"],
                "source": str(source),
                "bundle_path": str(destination.relative_to(root)),
                "sha256": row["local_sha256"],
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
