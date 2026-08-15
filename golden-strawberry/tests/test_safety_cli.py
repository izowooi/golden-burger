from __future__ import annotations

import pytest

import polybot.main as cli
from polybot.config import CANONICAL_JOB, _CREDENTIAL_ENV_KEYS


def test_simulate_flag_is_required():
    with pytest.raises(SystemExit):
        cli.main(["config", "--job", CANONICAL_JOB])


def test_live_rejected_before_config_db_log_or_network(monkeypatch, tmp_path):
    touched = []

    def forbidden(*args, **kwargs):
        touched.append((args, kwargs))
        raise AssertionError("side effect occurred before live rejection")

    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "setup_logging", forbidden)
    monkeypatch.setattr(cli, "PolymarketResearchBot", forbidden)
    assert cli.main(["run", "--live", "--job", CANONICAL_JOB]) == 2
    assert touched == []
    assert list(tmp_path.iterdir()) == []


def test_credential_fails_before_config_and_log(monkeypatch):
    key = sorted(_CREDENTIAL_ENV_KEYS)[0]
    monkeypatch.setenv(key, "")
    touched = []

    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError("side effect occurred before credential rejection")

    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(cli, "setup_logging", forbidden)
    assert cli.main(["run", "--simulate", "--job", CANONICAL_JOB]) == 2
    assert touched == []


def test_source_has_no_account_or_order_capability(project_root):
    forbidden = (
        "py_clob_client",
        "executionledger",
        "place_order",
        "post_order",
        "realized_pnl",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (project_root / "src").rglob("*.py")
    )
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8").lower()
    for value in forbidden:
        assert value not in source
        assert value not in pyproject


def test_env_example_has_no_credential_keys(project_root):
    text = (project_root / ".env.example").read_text(encoding="utf-8")
    assert all(key not in text for key in _CREDENTIAL_ENV_KEYS)
