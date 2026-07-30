"""CLI research-only contracts fail before bot/network construction."""

import sys

import pytest

from polybot import main as main_module


def test_polybot_run_live_fails_before_bot_construction(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module,
        "PolymarketBot",
        lambda *_args, **_kwargs: pytest.fail("bot must not be constructed"),
    )
    monkeypatch.setattr(sys, "argv", ["polybot", "run", "--live"])
    with pytest.raises(SystemExit) as error:
        main_module.main()
    assert error.value.code == 1
    assert "research/simulation-only" in capsys.readouterr().out


def test_config_command_works_without_wallet_credentials(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["polybot", "config"])
    main_module.main()
    output = capsys.readouterr().out
    assert "Golden Kiwi / Micro-Cascade" in output
    assert "Simulation only: True" in output
    assert "Frozen arm: B" in output
