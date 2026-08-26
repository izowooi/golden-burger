from __future__ import annotations

import pytest

import polybot.main as main_module
from polybot.main import main


def test_live_cli_rejected_before_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--live", "--job", "watermelon-white-1m-v3b"]) == 2
    assert not list(tmp_path.rglob("*.db"))


def test_credential_rejected_before_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "")
    with pytest.raises(ValueError, match="credential-bearing"):
        main(["run", "--simulate", "--job", "watermelon-white-1m-v3b"])
    assert not list(tmp_path.rglob("*.db"))


def test_explicit_db_analysis_keeps_immutable_legacy_job_readable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy-v3a.db"
    observed: list[object] = []

    def analyze(path):
        observed.append(path)
        return {"analyzer_contract": "soccer-major-league-analyzer-v3a"}

    monkeypatch.setattr(main_module, "analyze_database", analyze)
    assert main(
        [
            "analyze", "--simulate",
            "--job", "watermelon-white-1m-v3a",
            "--db", str(legacy),
        ]
    ) == 0
    assert observed == [legacy]
