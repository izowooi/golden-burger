from __future__ import annotations

import json
import shutil
import webbrowser
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from .bundle import create_bundle
from .config import GIB, load_config
from .sync import SyncService

app = typer.Typer(
    name="daily-rsync",
    help="Jenkins Mac mini SQLite/log pull synchronizer",
    no_args_is_help=True,
)


def _service(config: Path | None) -> SyncService:
    return SyncService(load_config(config))


def _bytes(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.2f} GiB"
    if value >= 1024**2:
        return f"{value / 1024**2:.2f} MiB"
    return f"{value / 1024:.2f} KiB"


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Verify SSH, Jenkins paths and disk capacity."""
    payload = _service(config).doctor()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def scan(
    job: Annotated[str | None, typer.Option(help="Jenkins job name")] = None,
    days: Annotated[int | None, typer.Option(min=1)] = None,
    from_date: Annotated[str | None, typer.Option(help="Research archive start YYYY-MM-DD")] = None,
    to_date: Annotated[str | None, typer.Option(help="Research archive end YYYY-MM-DD")] = None,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Discover jobs or inspect one job in detail."""
    if (from_date is None) != (to_date is None):
        raise typer.BadParameter("pass --from-date and --to-date together")
    inventories = _service(config).scan(
        job=job,
        days=days,
        from_date=date.fromisoformat(from_date) if from_date else None,
        to_date=date.fromisoformat(to_date) if to_date else None,
    )
    for inventory in inventories:
        total = sum(item.size_bytes for item in inventory.artifacts)
        typer.echo(
            f"{inventory.name}: current={inventory.current_strategy or 'unknown'} "
            f"builds={inventory.build_count} artifacts={len(inventory.artifacts)} "
            f"bytes={_bytes(total)} strategies={','.join(inventory.strategies)}"
        )


@app.command("plan")
def plan_command(
    job: Annotated[str, typer.Option(help="Jenkins job name")],
    strategy: Annotated[str | None, typer.Option(help="Strategy folder/name")] = None,
    include_safety_databases: Annotated[bool, typer.Option()] = False,
    days: Annotated[int | None, typer.Option(min=1)] = None,
    from_date: Annotated[str | None, typer.Option(help="Research archive start YYYY-MM-DD")] = None,
    to_date: Annotated[str | None, typer.Option(help="Research archive end YYYY-MM-DD")] = None,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Create a persisted, reviewable synchronization plan."""
    if (from_date is None) != (to_date is None):
        raise typer.BadParameter("pass --from-date and --to-date together")
    plan = _service(config).create_plan(
        job=job,
        strategy=strategy,
        include_safety_databases=include_safety_databases,
        days=days,
        from_date=date.fromisoformat(from_date) if from_date else None,
        to_date=date.fromisoformat(to_date) if to_date else None,
    )
    typer.echo(
        f"plan_id={plan.plan_id} job={plan.jenkins_job} strategy={plan.strategy} "
        f"transfer={len(plan.artifacts)} unchanged={plan.skipped_unchanged} "
        f"upper_bound={_bytes(plan.estimated_bytes)}"
    )
    for artifact in plan.artifacts:
        typer.echo(f"  {artifact.kind:18} {_bytes(artifact.size_bytes):>10} {artifact.remote_path}")


@app.command("sync")
def sync_command(
    plan: Annotated[str, typer.Option(help="Persisted plan ID")],
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Execute a persisted plan."""
    service = _service(config)
    sync_plan = service.load_plan(plan)

    def progress(payload: dict[str, object]) -> None:
        typer.echo(json.dumps(payload, ensure_ascii=False))

    result = service.execute(sync_plan, progress=progress)
    typer.echo(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if result.status != "SUCCESS":
        raise typer.Exit(1)


@app.command("sync-job")
def sync_job(
    job: Annotated[str, typer.Option(help="Jenkins job name")],
    strategy: Annotated[str | None, typer.Option()] = None,
    include_safety_databases: Annotated[bool, typer.Option()] = False,
    days: Annotated[int | None, typer.Option(min=1)] = None,
    from_date: Annotated[str | None, typer.Option(help="Research archive start YYYY-MM-DD")] = None,
    to_date: Annotated[str | None, typer.Option(help="Research archive end YYYY-MM-DD")] = None,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Create and execute a plan for one selected job."""
    if (from_date is None) != (to_date is None):
        raise typer.BadParameter("pass --from-date and --to-date together")
    service = _service(config)
    parsed_from = date.fromisoformat(from_date) if from_date else None
    parsed_to = date.fromisoformat(to_date) if to_date else None
    typer.echo(f"Executing sync-job: job={job} strategy={strategy or 'auto'}")
    result = service.sync_job(
        job=job,
        strategy=strategy,
        include_safety_databases=include_safety_databases,
        days=days,
        from_date=parsed_from,
        to_date=parsed_to,
        progress=lambda payload: typer.echo(json.dumps(payload, ensure_ascii=False)),
    )
    typer.echo(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if result.status != "SUCCESS":
        raise typer.Exit(1)


@app.command()
def verify(
    job: Annotated[str | None, typer.Option()] = None,
    strategy: Annotated[str | None, typer.Option()] = None,
    from_date: Annotated[str | None, typer.Option(help="Research archive start YYYY-MM-DD")] = None,
    to_date: Annotated[str | None, typer.Option(help="Research archive end YYYY-MM-DD")] = None,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Recheck synchronized files and SQLite integrity."""
    if (from_date is None) != (to_date is None):
        raise typer.BadParameter("pass --from-date and --to-date together")
    result = _service(config).verify(
        job=job,
        strategy=strategy,
        from_date=date.fromisoformat(from_date) if from_date else None,
        to_date=date.fromisoformat(to_date) if to_date else None,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "SUCCESS":
        raise typer.Exit(1)


@app.command()
def locate(
    job: Annotated[str | None, typer.Option(help="Jenkins job name")] = None,
    strategy: Annotated[str | None, typer.Option(help="Strategy folder/name")] = None,
    from_date: Annotated[str | None, typer.Option(help="Research archive start YYYY-MM-DD")] = None,
    to_date: Annotated[str | None, typer.Option(help="Research archive end YYYY-MM-DD")] = None,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Locate synchronized DB/log evidence without scanning the remote host."""
    if not job and not strategy:
        raise typer.BadParameter("pass --job or --strategy")
    if (from_date is None) != (to_date is None):
        raise typer.BadParameter("pass --from-date and --to-date together")
    result = _service(config).locate_evidence(
        job=job,
        strategy=strategy,
        from_date=date.fromisoformat(from_date) if from_date else None,
        to_date=date.fromisoformat(to_date) if to_date else None,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "FOUND":
        raise typer.Exit(1)


@app.command()
def pin(
    artifact: Annotated[str, typer.Option(help="Catalog source_key")],
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Pin the current local database snapshot."""
    destination = _service(config).pin_database(artifact)
    typer.echo(str(destination))


@app.command("account-epoch")
def account_epoch(
    job: Annotated[str, typer.Option()],
    strategy: Annotated[str, typer.Option()],
    account_alias: Annotated[str, typer.Option()],
    first_build: Annotated[int, typer.Option(min=1)],
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Record the account alias that applies from a Jenkins build onward."""
    service = _service(config)
    service.catalog.add_account_epoch(
        source=service.config.ssh_host,
        job=job,
        strategy=strategy,
        account_alias=account_alias,
        first_build=first_build,
    )
    typer.echo(
        f"saved job={job} strategy={strategy} account={account_alias} first_build={first_build}"
    )


@app.command("account-epochs")
def account_epochs(
    job: Annotated[str | None, typer.Option()] = None,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """List account deployment epochs."""
    rows = _service(config).catalog.list_account_epochs(job=job)
    typer.echo(
        json.dumps(
            [{key: row[key] for key in row.keys()} for row in rows],
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def prune(
    apply: Annotated[
        bool, typer.Option("--apply/--dry-run", help="Delete only with --apply")
    ] = False,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Preview or apply log retention."""
    result = _service(config).prune_retention(apply=apply)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def bundle(
    job: Annotated[str, typer.Option()],
    strategy: Annotated[str, typer.Option()],
    from_date: Annotated[str, typer.Option(help="YYYY-MM-DD")],
    to_date: Annotated[str, typer.Option(help="YYYY-MM-DD")],
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Create a self-contained database and log folder for AI analysis."""
    app_config = load_config(config)
    destination = create_bundle(
        app_config,
        job=job,
        strategy=strategy,
        from_date=date.fromisoformat(from_date),
        to_date=date.fromisoformat(to_date),
    )
    typer.echo(str(destination))


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
    config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Run the local web UI."""
    if host not in {"127.0.0.1", "localhost"}:
        raise typer.BadParameter("daily-rsync UI may only bind to localhost")
    import uvicorn

    from .web import create_app

    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run(create_app(load_config(config)), host="127.0.0.1", port=port)


@app.command("install-app")
def install_app(
    destination: Annotated[
        Path | None, typer.Option(help="macOS app installation directory")
    ] = None,
) -> None:
    """Install a Finder-launchable Daily Rsync.app into ~/Applications."""
    config = load_config()
    source = config.project_root / "macos" / "Daily Rsync.app"
    if not source.is_dir():
        raise typer.BadParameter(f"app bundle not found: {source}")
    install_root = (destination or Path.home() / "Applications").expanduser()
    install_root.mkdir(parents=True, exist_ok=True)
    target = install_root / source.name
    shutil.copytree(source, target, dirs_exist_ok=True)
    executable = target / "Contents" / "MacOS" / "daily-rsync"
    executable.chmod(0o755)
    project_marker = target / "Contents" / "Resources" / "project-root.txt"
    project_marker.parent.mkdir(parents=True, exist_ok=True)
    project_marker.write_text(f"{config.project_root}\n", encoding="utf-8")
    typer.echo(str(target))


if __name__ == "__main__":
    app()
