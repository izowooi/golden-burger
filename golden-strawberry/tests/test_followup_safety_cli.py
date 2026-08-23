from __future__ import annotations

import polybot.followup_main as followup_cli
import polybot.main as v1_cli
from polybot.followup_config import FOLLOWUP_CANONICAL_JOB


def test_followup_live_rejected_before_config_db_log_or_network(monkeypatch):
    touched = []

    def forbidden(*args, **kwargs):
        touched.append((args, kwargs))
        raise AssertionError("side effect occurred before live rejection")

    monkeypatch.setattr(followup_cli, "load_followup_config", forbidden)
    monkeypatch.setattr(followup_cli, "setup_logging", forbidden)
    monkeypatch.setattr(followup_cli, "FollowupBot", forbidden)
    assert (
        followup_cli.main(
            ["run", "--live", "--job", FOLLOWUP_CANONICAL_JOB]
        )
        == 2
    )
    assert touched == []


def test_followup_credential_fails_before_config_and_log(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "test-only-sentinel")
    touched = []

    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError("side effect occurred before credential rejection")

    monkeypatch.setattr(followup_cli, "load_followup_config", forbidden)
    monkeypatch.setattr(followup_cli, "setup_logging", forbidden)
    assert (
        followup_cli.main(
            ["run", "--simulate", "--job", FOLLOWUP_CANONICAL_JOB]
        )
        == 2
    )
    assert touched == []


def test_v1_run_is_retired_while_read_only_commands_remain(project_root):
    assert (
        v1_cli.main(
            [
                "run",
                "--simulate",
                "--job",
                "strawberry-shadow-one",
                "--config",
                str(project_root / "config.yaml"),
            ]
        )
        == 2
    )


def test_followup_console_script_and_runtime_are_separate(project_root):
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    config = (project_root / "config.followup-v2.yaml").read_text(encoding="utf-8")
    assert 'polybot-followup = "polybot.followup_main:main"' in pyproject
    assert "strawberry-shadow-one-followup-v2" not in config  # CLI pins it.
    assert "last-mile-clob-followup-v2" in config
