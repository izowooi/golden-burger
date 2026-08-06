from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

RESEARCH_ARCHIVE_PATTERN = re.compile(r"^trades_sim_(\d{8})\.db$")
SOURCE_KEY_VERSION = 2


def research_archive_date(path: str | Path) -> date | None:
    match = RESEARCH_ARCHIVE_PATTERN.fullmatch(Path(path).name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def artifact_source_key(*, source: str, jenkins_job: str, kind: str, remote_path: str) -> str:
    """Return the host-scoped artifact identity used by catalog schema v4.

    A remote path is only meaningful within one configured SSH source.  Omitting
    ``source`` allowed two Jenkins hosts with identical workspace layouts to
    overwrite one catalog row, so source-key v2 makes that boundary explicit.
    """

    if not source:
        raise ValueError("artifact source identity is required")
    value = f"v{SOURCE_KEY_VERSION}\0{source}\0{jenkins_job}\0{kind}\0{remote_path}"
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class ResearchDatabaseContract:
    contract_name: str
    database_utc_date: date


def read_research_database_contract(path: Path) -> ResearchDatabaseContract | None:
    """Read the single research collection contract from a SQLite snapshot.

    ``None`` means this is an ordinary strategy database.  Once the table is
    present, malformed/multiple rows are evidence corruption and fail closed.
    """

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "collection_contracts" not in tables:
            return None
        rows = list(
            connection.execute(
                "SELECT contract_name, database_utc_date FROM collection_contracts"
            )
        )
    finally:
        connection.close()
    if len(rows) != 1:
        raise RuntimeError(
            "research database must contain exactly one collection_contracts row"
        )
    contract_name, raw_date = rows[0]
    if not isinstance(contract_name, str) or not contract_name:
        raise RuntimeError("research database contract_name is missing")
    try:
        database_day = date.fromisoformat(str(raw_date))
    except (TypeError, ValueError):
        raise RuntimeError("research database database_utc_date is invalid") from None
    if str(raw_date) != database_day.isoformat():
        raise RuntimeError("research database database_utc_date is not canonical YYYY-MM-DD")
    return ResearchDatabaseContract(contract_name, database_day)


@dataclass(frozen=True)
class RemoteArtifact:
    kind: str
    remote_path: str
    size_bytes: int
    mtime_ns: int
    jenkins_job: str
    strategy: str | None = None
    runtime_job: str | None = None
    build_number: int | None = None
    completed_at: str | None = None
    status: str | None = None
    canonical: bool = True
    archive_date: str | None = None
    mode: str | None = None
    data_contract: str | None = None
    database_utc_date: str | None = None
    source: str | None = None
    # For SQLite this covers the main database and durable ``-wal`` sidecar.
    # Volatile ``-shm`` coordination metadata is deliberately excluded. A
    # WAL-only write still invalidates an old plan when main stat is unchanged.
    fingerprint: str | None = None

    @property
    def source_key(self) -> str:
        return artifact_source_key(
            source=self.source or "",
            jenkins_job=self.jenkins_job,
            kind=self.kind,
            remote_path=self.remote_path,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RemoteArtifact:
        return cls(**payload)


@dataclass(frozen=True)
class JobInventory:
    name: str
    workspace: str
    build_count: int
    min_build: int | None
    max_build: int | None
    current_strategy: str | None
    strategies: tuple[str, ...]
    artifacts: tuple[RemoteArtifact, ...]
    remote_free_bytes: int
    strategy_evidence: dict[str, Any] = field(default_factory=dict)
    workspace_identity: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> JobInventory:
        data = dict(payload)
        data["strategies"] = tuple(data.get("strategies", []))
        data["artifacts"] = tuple(
            RemoteArtifact.from_dict(item) for item in data.get("artifacts", [])
        )
        return cls(**data)


@dataclass
class SyncPlan:
    plan_id: str
    created_at: str
    source: str
    jenkins_job: str
    strategy: str
    workspace: str | None = None
    workspace_identity: dict[str, Any] | None = None
    artifacts: list[RemoteArtifact] = field(default_factory=list)
    skipped_unchanged: int = 0
    estimated_bytes: int = 0
    include_safety_databases: bool = False
    from_date: str | None = None
    to_date: str | None = None

    @classmethod
    def create(
        cls,
        *,
        source: str,
        jenkins_job: str,
        strategy: str,
        artifacts: list[RemoteArtifact],
        skipped_unchanged: int,
        include_safety_databases: bool,
        workspace: str | None = None,
        workspace_identity: dict[str, Any] | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> SyncPlan:
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date and to_date must be passed together")
        bound_artifacts = [
            RemoteArtifact(**{**asdict(item), "source": item.source or source})
            for item in artifacts
        ]
        created_at = datetime.now(UTC).isoformat()
        seed = json.dumps(
            {
                "source": source,
                "job": jenkins_job,
                "strategy": strategy,
                "created_at": created_at,
                "workspace": workspace,
                "workspace_identity": workspace_identity,
                "from_date": from_date.isoformat() if from_date else None,
                "to_date": to_date.isoformat() if to_date else None,
                "artifacts": [
                    (
                        item.source_key,
                        item.fingerprint,
                        item.size_bytes,
                        item.mtime_ns,
                        item.database_utc_date,
                    )
                    for item in bound_artifacts
                ],
            },
            sort_keys=True,
        )
        return cls(
            plan_id=hashlib.sha256(seed.encode()).hexdigest()[:16],
            created_at=created_at,
            source=source,
            jenkins_job=jenkins_job,
            strategy=strategy,
            workspace=workspace,
            workspace_identity=workspace_identity,
            artifacts=bound_artifacts,
            skipped_unchanged=skipped_unchanged,
            estimated_bytes=sum(item.size_bytes for item in artifacts),
            include_safety_databases=include_safety_databases,
            from_date=from_date.isoformat() if from_date else None,
            to_date=to_date.isoformat() if to_date else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "artifacts": [asdict(item) for item in self.artifacts],
        }

    def write(self, root: Path) -> Path:
        path = root / f"{self.plan_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        return path

    @classmethod
    def read(cls, path: Path) -> SyncPlan:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("from_date", None)
        payload.setdefault("to_date", None)
        source = str(payload["source"])
        payload["artifacts"] = [
            RemoteArtifact(
                **{
                    **item,
                    "source": item.get("source") or source,
                }
            )
            for item in payload["artifacts"]
        ]
        return cls(**payload)


@dataclass
class SyncResult:
    run_id: str
    status: str
    transferred: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_written: int = 0
    errors: list[str] = field(default_factory=list)
