from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .bundle import create_bundle
from .config import AppConfig
from .sync import SyncService


class PlanRequest(BaseModel):
    job: str = Field(min_length=1)
    strategy: str | None = None
    days: int = Field(default=60, ge=1, le=365)
    include_safety_databases: bool = False


class BundleRequest(BaseModel):
    job: str
    strategy: str
    from_date: date
    to_date: date


class AccountEpochRequest(BaseModel):
    job: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    account_alias: str = Field(min_length=1)
    first_build: int = Field(ge=1)


class PinRequest(BaseModel):
    source_key: str = Field(min_length=1)


class OpenPathRequest(BaseModel):
    path: str = Field(min_length=1)


class TaskStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")

    def create(self, function: Any, *, label: str) -> str:
        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "label": label,
                "status": "QUEUED",
                "events": [],
            }

        def callback(payload: dict[str, object]) -> None:
            with self._lock:
                task = self._tasks[task_id]
                task["status"] = (
                    "RUNNING" if payload.get("phase") != "finished" else payload["status"]
                )
                task["latest"] = payload
                task["events"].append(payload)
                task["events"] = task["events"][-200:]

        def runner() -> None:
            try:
                with self._lock:
                    self._tasks[task_id]["status"] = "RUNNING"
                result = function(callback)
                with self._lock:
                    task = self._tasks[task_id]
                    task["result"] = result
                    if task["status"] == "RUNNING":
                        result_status = result.get("status") if isinstance(result, dict) else None
                        task["status"] = (
                            result_status
                            if result_status in {"SUCCESS", "PARTIAL", "FAILED"}
                            else "SUCCESS"
                        )
            except Exception as error:
                with self._lock:
                    self._tasks[task_id].update(
                        {
                            "status": "FAILED",
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )

        self._executor.submit(runner)
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(task) for task in reversed(self._tasks.values())]


def create_app(config: AppConfig) -> FastAPI:
    application = FastAPI(title="daily-rsync", docs_url="/api/docs")
    service = SyncService(config)
    tasks = TaskStore()
    application.state.service = service
    application.state.tasks = tasks
    package_root = Path(__file__).parent
    templates = Jinja2Templates(directory=str(package_root / "templates"))
    application.mount("/static", StaticFiles(directory=str(package_root / "static")), name="static")

    @application.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "ssh_host": config.ssh_host,
                "data_root": str(config.data_root),
                "initial_days": config.initial_log_days,
                "minimum_free_gb": config.minimum_free_bytes // (1024**3),
            },
        )

    @application.get("/api/doctor")
    def doctor() -> dict[str, object]:
        return service.doctor()

    @application.get("/api/status")
    def status() -> dict[str, object]:
        disk = shutil.disk_usage(config.data_root)
        return {
            **service.catalog.dashboard_summary(source=config.ssh_host),
            "data_root": str(config.data_root),
            "free_bytes": disk.free,
            "total_bytes": disk.total,
            "minimum_free_bytes": config.minimum_free_bytes,
            "retention_days": config.log_retention_days,
            "ssh_host": config.ssh_host,
        }

    @application.get("/api/jobs")
    def jobs() -> list[dict[str, object]]:
        return [asdict(item) for item in service.scan()]

    @application.get("/api/jobs/{job}")
    def job_inventory(job: str, days: int = 60) -> dict[str, object]:
        values = service.scan(job=job, days=days)
        if not values:
            raise HTTPException(404, "job not found")
        return asdict(values[0])

    @application.post("/api/plans")
    def create_plan(payload: PlanRequest) -> dict[str, object]:
        return service.create_plan(
            job=payload.job,
            strategy=payload.strategy,
            days=payload.days,
            include_safety_databases=payload.include_safety_databases,
        ).to_dict()

    @application.post("/api/plans/{plan_id}/sync")
    def start_sync(plan_id: str) -> dict[str, str]:
        plan = service.load_plan(plan_id)
        task_id = tasks.create(
            lambda callback: service.execute(plan, progress=callback).__dict__,
            label=f"{plan.jenkins_job} 동기화",
        )
        return {"task_id": task_id}

    @application.post("/api/verify")
    def start_verify(job: str | None = None, strategy: str | None = None) -> dict[str, str]:
        task_id = tasks.create(
            lambda _callback: service.verify(job=job, strategy=strategy),
            label=f"{job or '전체'} 무결성 검사",
        )
        return {"task_id": task_id}

    @application.get("/api/tasks/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return task

    @application.get("/api/tasks")
    def task_list() -> list[dict[str, Any]]:
        return tasks.list()

    @application.get("/api/catalog")
    def catalog(job: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
        return [
            {key: row[key] for key in row.keys()}
            for row in service.catalog.list_artifacts(
                source=config.ssh_host,
                job=job,
                strategy=strategy,
            )
        ]

    @application.get("/api/runs")
    def runs(limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise HTTPException(422, "limit must be between 1 and 100")
        return [
            {key: row[key] for key in row.keys()}
            for row in service.catalog.list_sync_runs(source=config.ssh_host, limit=limit)
        ]

    @application.get("/api/pins")
    def pins(job: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
        return [
            {key: row[key] for key in row.keys()}
            for row in service.catalog.list_pins(
                source=config.ssh_host,
                job=job,
                strategy=strategy,
            )
        ]

    @application.post("/api/pins")
    def pin(payload: PinRequest) -> dict[str, str]:
        path = service.pin_database(payload.source_key)
        return {"path": str(path)}

    @application.get("/api/account-epochs")
    def account_epochs(job: str | None = None) -> list[dict[str, Any]]:
        return [
            {key: row[key] for key in row.keys()}
            for row in service.catalog.list_account_epochs(job=job)
        ]

    @application.post("/api/account-epochs")
    def save_account_epoch(payload: AccountEpochRequest) -> dict[str, object]:
        service.catalog.add_account_epoch(
            source=config.ssh_host,
            job=payload.job,
            strategy=payload.strategy,
            account_alias=payload.account_alias,
            first_build=payload.first_build,
        )
        return payload.model_dump()

    @application.get("/api/retention")
    def retention() -> dict[str, object]:
        return service.prune_retention(apply=False)

    @application.post("/api/retention")
    def apply_retention() -> dict[str, object]:
        return service.prune_retention(apply=True)

    @application.post("/api/bundles")
    def bundle(payload: BundleRequest) -> dict[str, str]:
        task_id = tasks.create(
            lambda _callback: {
                "path": str(
                    create_bundle(
                        config,
                        job=payload.job,
                        strategy=payload.strategy,
                        from_date=payload.from_date,
                        to_date=payload.to_date,
                    )
                )
            },
            label=f"{payload.job} AI bundle",
        )
        return {"task_id": task_id}

    @application.post("/api/open")
    def open_path(payload: OpenPathRequest) -> dict[str, str]:
        requested = Path(payload.path).expanduser().resolve()
        root = config.data_root.resolve()
        if requested != root and not requested.is_relative_to(root):
            raise HTTPException(403, "data root 밖의 경로는 열 수 없습니다")
        if not requested.exists():
            raise HTTPException(404, "경로가 존재하지 않습니다")
        if os.uname().sysname != "Darwin":
            raise HTTPException(501, "Finder 열기는 macOS에서만 지원합니다")
        command = (
            ["open", "-R", str(requested)] if requested.is_file() else ["open", str(requested)]
        )
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"path": str(requested)}

    return application
