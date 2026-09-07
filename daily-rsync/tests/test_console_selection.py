import json
from dataclasses import replace
from types import SimpleNamespace

from typer.testing import CliRunner

from daily_rsync.cli import app
from daily_rsync.models import JobInventory, RemoteArtifact, SyncPlan
from daily_rsync.sync import SyncService


def test_console_opt_out_preserves_database_and_bot_log_evidence(app_config, monkeypatch):
    service = SyncService(app_config)
    base = RemoteArtifact(
        kind="database_sim",
        remote_path="/jenkins/workspace/job/golden-plum/data/run/trades_sim.db",
        size_bytes=100,
        mtime_ns=1,
        jenkins_job="job",
        strategy="golden-plum",
        runtime_job="run",
    )
    inventory = JobInventory(
        name="job",
        workspace="/jenkins/workspace/job",
        build_count=1,
        min_build=1,
        max_build=1,
        current_strategy="golden-plum",
        strategies=("golden-plum",),
        remote_free_bytes=10**9,
        workspace_identity={"root_st_dev": 42, "workspace_st_dev": 42},
        artifacts=(
            base,
            replace(
                base, kind="bot_log", remote_path="/jenkins/workspace/job/golden-plum/logs/bot.log"
            ),
            replace(
                base,
                kind="jenkins_console",
                remote_path="/jenkins/jobs/job/builds/1/log",
                build_number=1,
            ),
            replace(
                base,
                kind="database_safety",
                remote_path="/jenkins/workspace/job/golden-plum/data/run/trades_old.db",
            ),
        ),
    )
    monkeypatch.setattr(service, "scan", lambda **kwargs: [inventory])
    full = service.create_plan(job="job")
    selected = service.create_plan(job="job", include_console_logs=False)
    assert {a.kind for a in full.artifacts} == {"database_sim", "bot_log", "jenkins_console"}
    assert {a.kind for a in selected.artifacts} == {"database_sim", "bot_log"}
    assert selected.estimated_bytes == 200
    assert selected.skipped_unchanged == 0
    restored = SyncPlan.read(app_config.plans_root / f"{selected.plan_id}.json")
    assert restored.include_console_logs is False
    assert restored.workspace_identity == inventory.workspace_identity
    # Older persisted plans retain their previous full-console meaning.
    payload = full.to_dict()
    payload.pop("include_console_logs")
    legacy_path = app_config.plans_root / "legacy.json"
    legacy_path.write_text(json.dumps(payload))
    assert SyncPlan.read(legacy_path).include_console_logs is True


def test_cli_exposes_explicit_console_opt_out(monkeypatch):
    seen = {}

    def create_plan(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            plan_id="test",
            jenkins_job="job",
            strategy="plum",
            artifacts=[],
            skipped_unchanged=0,
            estimated_bytes=0,
        )

    monkeypatch.setattr(
        "daily_rsync.cli._service", lambda _: SimpleNamespace(create_plan=create_plan)
    )
    result = CliRunner().invoke(app, ["plan", "--job", "job", "--no-include-console-logs"])
    assert result.exit_code == 0
    assert seen["include_console_logs"] is False
