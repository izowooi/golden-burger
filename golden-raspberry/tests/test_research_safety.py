from __future__ import annotations

from pathlib import Path

from polybot.config import PROJECT_ROOT
from polybot.main import main


def test_source_has_no_order_submission_path():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "src" / "polybot").rglob("*.py")
    )
    for forbidden in (
        "ExecutionLedger",
        "submit_and_record",
        "place_limit_order",
        "create_order(",
        "post_order(",
    ):
        assert forbidden not in combined


def test_live_cli_fails_without_creating_data(monkeypatch, tmp_path):
    monkeypatch.chdir(PROJECT_ROOT)
    before = set((PROJECT_ROOT / "data").glob("raspberry-do-v3-shard-0/*")) if (PROJECT_ROOT / "data").exists() else set()
    assert main(["config", "--live", "--job", "raspberry-do-v3-shard-0"]) == 2
    after = set((PROJECT_ROOT / "data").glob("raspberry-do-v3-shard-0/*")) if (PROJECT_ROOT / "data").exists() else set()
    assert after == before
