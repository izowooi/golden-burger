from __future__ import annotations

import pytest

from polybot.main import main


def test_live_cli_rejected_before_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--live", "--job", "watermelon-white-1m"]) == 2
    assert not list(tmp_path.rglob("*.db"))


def test_credential_rejected_before_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "")
    with pytest.raises(ValueError, match="credential-bearing"):
        main(["run", "--simulate", "--job", "watermelon-white-1m"])
    assert not list(tmp_path.rglob("*.db"))
