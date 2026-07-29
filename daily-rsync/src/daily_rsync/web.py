from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
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


class TaskStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")

    def create(self, function: Any) -> str:
        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = {"status": "QUEUED", "events": []}

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
                function(callback)
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


def create_app(config: AppConfig) -> FastAPI:
    application = FastAPI(title="daily-rsync", docs_url="/api/docs")
    service = SyncService(config)
    tasks = TaskStore()
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))

    @application.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "ssh_host": config.ssh_host,
                "data_root": str(config.data_root),
                "initial_days": config.initial_log_days,
            },
        )

    @application.get("/api/doctor")
    def doctor() -> dict[str, object]:
        return service.doctor()

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
        task_id = tasks.create(lambda callback: service.execute(plan, progress=callback))
        return {"task_id": task_id}

    @application.get("/api/tasks/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return task

    @application.get("/api/catalog")
    def catalog(job: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
        return [
            {key: row[key] for key in row.keys()}
            for row in service.catalog.list_artifacts(job=job, strategy=strategy)
        ]

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

    @application.post("/api/bundles")
    def bundle(payload: BundleRequest) -> dict[str, str]:
        path = create_bundle(
            config,
            job=payload.job,
            strategy=payload.strategy,
            from_date=payload.from_date,
            to_date=payload.to_date,
        )
        return {"path": str(path)}

    return application
