"""Closure budgets propagate distinctly from missing/corrupt public evidence."""
import sqlite3

import pytest

from polybot_observability.market_data_bundle import reference_closure, verify_closure
from polybot_observability import market_data_projection_closure as projection


class BudgetExpired(RuntimeError):
    pass


def test_reference_discovery_sqlite_budget_preserves_guard_exception(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.executemany("INSERT INTO evidence VALUES(?)", [("ordinary private text",)] * 10000)
    calls = 0
    def guard():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise BudgetExpired("body discovery budget")
    with pytest.raises(BudgetExpired, match="body discovery budget"):
        reference_closure(path, "unclassified", immutable=True, guard=guard)
    assert reference_closure(path, "unclassified", immutable=True) == []


def test_projection_sqlite_guard_is_not_reported_as_corrupt_evidence(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    sqlite3.connect(path).close()
    def owners(connection, strategy):
        connection.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT SUM(x) FROM n").fetchone()
        return {}
    monkeypatch.setattr(projection, "_owners", owners)
    calls = 0
    def guard():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise BudgetExpired("projection query budget")
    with pytest.raises(BudgetExpired, match="projection query budget"):
        projection.verify_projection_closure(None, path, "golden-strawberry", immutable=True, guard=guard)


def test_empty_body_closure_still_checks_budget_before_success():
    def guard():
        raise BudgetExpired("expired before empty closure")
    with pytest.raises(BudgetExpired, match="empty closure"):
        verify_closure(None, [], guard=guard)


def test_projection_batch_checks_budget_after_readback():
    class Reader:
        def get_projection_records(self, ids, authority):
            return []
    calls = 0
    def guard():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise BudgetExpired("projection readback budget")
    with pytest.raises(BudgetExpired, match="readback budget"):
        list(projection.read_projection_records(Reader(), [1], "authority", guard=guard))
