from __future__ import annotations

import pytest

from polybot.db.repository import ResearchRepository
from polybot.run_audit import ResearchRunAudit


def test_run_audit_has_one_terminal_event(config):
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)
    audit = ResearchRunAudit.start(config, repository=repository)
    audit.succeed({"cycle_number": 1})
    with pytest.raises(RuntimeError, match="terminal"):
        audit.succeed({})
    with repository._read_connect() as connection:
        rows = connection.execute(
            "SELECT event_type FROM research_run_events WHERE run_id=? ORDER BY event_at,event_id",
            (audit.run_id,),
        ).fetchall()
    assert {row[0] for row in rows} == {"STARTED", "SUCCEEDED"}
    assert len(rows) == 2


def test_run_audit_records_failure_without_success(config):
    repository = ResearchRepository(config.db_path)
    repository.initialize(config)
    audit = ResearchRunAudit.start(config, repository=repository)
    audit.fail(RuntimeError("broken census"))
    audit.fail(RuntimeError("ignored duplicate"))
    with repository._read_connect() as connection:
        terminal = connection.execute(
            "SELECT event_type,error_type,error_message FROM research_run_events "
            "WHERE run_id=? AND event_type!='STARTED'",
            (audit.run_id,),
        ).fetchone()
    assert terminal["event_type"] == "FAILED"
    assert terminal["error_type"] == "RuntimeError"
    assert terminal["error_message"] == "broken census"
