from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time

import pytest

from polybot.db.followup_repository import FollowupRepository, READ_QUERY_INDEXES
from polybot.utils.retry import CooperativeDeadline, CycleDeadlineExceeded


@pytest.fixture
def query_repository(tmp_path):
    path = tmp_path / "queries.db"
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE imported_episodes (
                episode_id TEXT PRIMARY KEY,condition_id TEXT,token_id TEXT,
                entry_threshold REAL,source_last_executable_bid_vwap REAL);
            CREATE TABLE imported_condition_status (
                condition_id TEXT PRIMARY KEY,terminal_at_handoff INTEGER);
            CREATE TABLE episode_path_observations (
                path_observation_id TEXT PRIMARY KEY,episode_id TEXT,observed_at TEXT,
                path_status TEXT,exit_bid_vwap REAL);
            CREATE INDEX followup_path_episode_idx
                ON episode_path_observations(episode_id,observed_at);
            CREATE TABLE resolution_observations (
                condition_id TEXT,observed_at TEXT,resolution_status TEXT);
            CREATE INDEX followup_resolution_condition_idx
                ON resolution_observations(condition_id,observed_at);
        """)
    return FollowupRepository(path)


def add_indexes(repository):
    with sqlite3.connect(repository.db_path) as c:
        for statement in READ_QUERY_INDEXES:
            c.execute(statement)


def legacy_latest(connection, ids):
    result = {}
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        params = ",".join("?" for _ in chunk)
        for key, price in connection.execute(
            f"SELECT episode_id,source_last_executable_bid_vwap FROM imported_episodes WHERE episode_id IN ({params})", chunk
        ):
            if price is not None:
                result[key] = float(price)
        for key, price in connection.execute(f"""
            WITH ranked AS (
                SELECT episode_id,exit_bid_vwap,ROW_NUMBER() OVER (
                    PARTITION BY episode_id ORDER BY observed_at DESC,path_observation_id DESC
                ) AS position
                FROM episode_path_observations WHERE episode_id IN ({params})
                  AND path_status='EXECUTABLE' AND exit_bid_vwap IS NOT NULL
            ) SELECT episode_id,exit_bid_vwap FROM ranked WHERE position=1
        """, chunk):
            result[key] = float(price)
    return result


def test_latest_query_preserves_seed_fallback_ties_null_zero_and_chunks(query_repository):
    repository = query_repository
    ids = [f"episode-{i}" for i in range(410)]
    with sqlite3.connect(repository.db_path) as c:
        c.executemany("INSERT INTO imported_episodes VALUES(?,?,?,?,?)", [
            (key, "c", key, .95, .81) for key in ids
        ] + [("null-seed", "c", "n", .95, None)])
        c.executemany("INSERT INTO episode_path_observations VALUES(?,?,?,?,?)", [
            ("a", ids[0], "2026-08-24T00:00:00Z", "EXECUTABLE", .85),
            ("z", ids[0], "2026-08-24T00:00:00Z", "EXECUTABLE", .86),
            ("null", ids[0], "2026-08-24T00:10:00Z", "EXECUTABLE", None),
            ("missing", ids[0], "2026-08-24T00:20:00Z", "MISSING", .99),
            ("zero", ids[1], "2026-08-24T00:00:00Z", "EXECUTABLE", 0.),
            ("orphan", "orphan", "2026-08-24T00:00:00Z", "EXECUTABLE", .72),
        ])
    add_indexes(repository)
    requested = ids + [ids[0], "null-seed", "missing-episode", "orphan"]
    with repository.read_connect() as c:
        expected = legacy_latest(c, requested)
    before = hashlib.sha256(repository.db_path.read_bytes()).hexdigest()
    actual = repository.latest_path_vwaps(requested)
    assert actual == expected
    assert actual[ids[0]] == .86
    assert actual[ids[1]] == 0.
    assert actual[ids[-1]] == .81
    assert actual["orphan"] == .72
    assert "null-seed" not in actual and "missing-episode" not in actual
    assert repository.latest_path_vwaps([]) == {}
    assert hashlib.sha256(repository.db_path.read_bytes()).hexdigest() == before


def test_resolved_partial_index_preserves_unresolved_rows_and_uses_index(query_repository):
    repository = query_repository
    with sqlite3.connect(repository.db_path) as c:
        c.executemany("INSERT INTO imported_condition_status VALUES(?,?)", [
            ("open", 0), ("resolved", 0), ("handoff-terminal", 1), ("malformed", 0),
        ])
        c.executemany("INSERT INTO imported_episodes VALUES(?,?,?,?,?)", [
            ("open-b", "open", "b", .95, .8), ("open-a", "open", "a", .97, .9),
            ("done", "resolved", "d", .95, .9), ("terminal", "handoff-terminal", "t", .95, .9),
            ("malformed", "malformed", "m", .95, .9),
        ])
        c.executemany("INSERT INTO resolution_observations VALUES(?,?,?)", [
            ("open", str(i), "OPEN") for i in range(1000)
        ] + [("resolved", "1", "OPEN"), ("resolved", "2", "RESOLVED"), ("malformed", "3", "MALFORMED")])
    before = repository.unresolved_episodes()
    add_indexes(repository)
    assert repository.unresolved_episodes() == before
    assert [r["episode_id"] for r in before] == ["malformed", "open-a", "open-b"]
    with repository.read_connect() as c:
        plan = " ".join(str(row[3]) for row in c.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM resolution_observations r WHERE r.condition_id=? AND r.resolution_status='RESOLVED'", ("open",)
        ))
        assert "followup_resolution_resolved_condition_idx" in plan
        assert c.execute("SELECT count(*) FROM resolution_observations").fetchone()[0] == 1003


def test_indexed_latest_query_avoids_full_history_work(query_repository, monkeypatch):
    repository = query_repository
    ids = [f"episode-{i:03}" for i in range(200)]
    with sqlite3.connect(repository.db_path) as c:
        c.executemany("INSERT INTO imported_episodes VALUES(?,?,?,?,?)", [(x,"c",x,.95,.8) for x in ids])
        c.executemany("INSERT INTO episode_path_observations VALUES(?,?,?,?,?)", (
            (f"{key}-{i:04}", key, f"2026-08-24T{i:06}", "EXECUTABLE", .8+i/10000)
            for key in ids for i in range(400)
        ))
    add_indexes(repository)
    original_read = repository.read_connect
    old_steps = [0];new_steps = [0];statements = []
    def count(target):
        def tick():
            target[0] += 100
            return 0
        return tick
    with original_read() as c:
        c.set_progress_handler(count(old_steps), 100)
        started = time.perf_counter();expected = legacy_latest(c, ids)
        old_seconds = time.perf_counter()-started
    @contextmanager
    def measured_read(**kwargs):
        with original_read(**kwargs) as c:
            c.set_progress_handler(count(new_steps), 100)
            c.set_trace_callback(statements.append)
            yield c
    monkeypatch.setattr(repository, "read_connect", measured_read)
    started = time.perf_counter();actual = repository.latest_path_vwaps(ids)
    new_seconds = time.perf_counter()-started
    assert actual == expected
    assert new_steps[0] * 10 < old_steps[0]
    sql = next(s for s in statements if "WITH requested" in s)
    with original_read() as c:
        plan = " ".join(str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN "+sql))
    assert "followup_path_latest_executable_idx" in plan
    assert "USE TEMP B-TREE" not in plan
    print(json.dumps({"history_rows":80000,"requested_episodes":200,
                      "old_vm_steps":old_steps[0],"new_vm_steps":new_steps[0],
                      "old_seconds":old_seconds,"new_seconds":new_seconds}))


def test_sqlite_progress_deadline_interrupts_read_and_preserves_db(query_repository):
    repository = query_repository
    clock = [0.0]
    deadline = CooperativeDeadline(expires_at=1, monotonic=lambda:clock[0])
    before = hashlib.sha256(repository.db_path.read_bytes()).hexdigest()
    calls = [0]
    def advance(value):
        calls[0] += 1;clock[0] += .01
        return value
    with pytest.raises(CycleDeadlineExceeded, match="SQLite read progress"):
        with repository.read_connect(deadline=deadline) as c:
            c.create_function("advance_clock", 1, advance)
            c.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(advance_clock(x)) FROM n").fetchone()
    assert calls[0] < 1000
    assert hashlib.sha256(repository.db_path.read_bytes()).hexdigest() == before
    with repository.read_connect() as c:
        assert c.execute("SELECT 1").fetchone()[0] == 1


def test_expired_deadline_and_lock_timeout_are_bounded(query_repository):
    with pytest.raises(CycleDeadlineExceeded):
        with query_repository.read_connect(deadline=CooperativeDeadline(expires_at=0,monotonic=lambda:0)):
            pytest.fail("expired deadline yielded a connection")
    deadline = CooperativeDeadline(expires_at=.25,monotonic=lambda:0)
    with query_repository.read_connect(deadline=deadline) as c:
        assert 0 <= c.execute("PRAGMA busy_timeout").fetchone()[0] <= 240


def test_unrelated_sql_errors_are_not_relabelled_as_deadlines(query_repository):
    deadline = CooperativeDeadline(expires_at=10,monotonic=lambda:0)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with query_repository.read_connect(deadline=deadline) as c:
            c.execute("SELECT * FROM table_that_does_not_exist").fetchall()
