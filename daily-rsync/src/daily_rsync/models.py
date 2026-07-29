from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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

    @property
    def source_key(self) -> str:
        value = f"{self.jenkins_job}\0{self.kind}\0{self.remote_path}"
        return hashlib.sha256(value.encode()).hexdigest()

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
    artifacts: list[RemoteArtifact] = field(default_factory=list)
    skipped_unchanged: int = 0
    estimated_bytes: int = 0
    include_safety_databases: bool = False

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
    ) -> SyncPlan:
        created_at = datetime.now(UTC).isoformat()
        seed = json.dumps(
            {
                "source": source,
                "job": jenkins_job,
                "strategy": strategy,
                "created_at": created_at,
                "paths": [item.remote_path for item in artifacts],
            },
            sort_keys=True,
        )
        return cls(
            plan_id=hashlib.sha256(seed.encode()).hexdigest()[:16],
            created_at=created_at,
            source=source,
            jenkins_job=jenkins_job,
            strategy=strategy,
            artifacts=artifacts,
            skipped_unchanged=skipped_unchanged,
            estimated_bytes=sum(item.size_bytes for item in artifacts),
            include_safety_databases=include_safety_databases,
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
        payload["artifacts"] = [RemoteArtifact.from_dict(item) for item in payload["artifacts"]]
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
