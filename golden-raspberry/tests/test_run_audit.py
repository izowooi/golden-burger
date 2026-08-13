from __future__ import annotations

import sqlite3

from polybot.config import PROJECT_ROOT, load_config
from polybot.db.repository import ResearchRepository
from polybot.run_audit import ResearchRunAudit


def test_run_audit_is_started_then_terminal(monkeypatch, tmp_path):
    config = load_config(PROJECT_ROOT / "config.yaml", "raspberry-do-shard-0")
    repository = ResearchRepository(tmp_path / "trades_sim.db")
    repository.initialize(config)
    audit = ResearchRunAudit.start(config, repository=repository)
    audit.succeed({"ok": True})
    with sqlite3.connect(repository.db_path) as connection:
        events = [row[0] for row in connection.execute(
            "SELECT event_type FROM research_run_events WHERE run_id=? ORDER BY event_at, rowid",
            (audit.run_id,),
        )]
    assert events == ["STARTED", "SUCCEEDED"]
