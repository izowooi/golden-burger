"""SQLite helpers shared by every component."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from polylab.db.schema import BOOKS_SCHEMA, CORE_SCHEMA, STRATEGY_SCHEMA


def connect(path: Path, schema: tuple[str, ...] | None = None, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    if schema and not readonly:
        for stmt in schema:
            conn.execute(stmt)
        conn.commit()
    return conn


def core(paths, readonly: bool = False) -> sqlite3.Connection:
    return connect(paths.core_db, None if readonly else CORE_SCHEMA, readonly)


def books(paths, year_month: str, readonly: bool = False) -> sqlite3.Connection:
    return connect(paths.books_db(year_month), None if readonly else BOOKS_SCHEMA, readonly)


def strategy(paths, strategy_id: str, readonly: bool = False) -> sqlite3.Connection:
    return connect(paths.strategy_db(strategy_id), None if readonly else STRATEGY_SCHEMA, readonly)


def now() -> int:
    return int(time.time())


def year_month(ts: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def set_checkpoint(conn: sqlite3.Connection, name: str, value: str) -> None:
    conn.execute(
        "INSERT INTO checkpoints(name, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (name, value, now()),
    )


def get_checkpoint(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT value FROM checkpoints WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def quality(conn: sqlite3.Connection, kind: str, ref: str | None, detail: str | None = None) -> None:
    conn.execute("INSERT INTO quality_events(ts, kind, ref, detail) VALUES(?,?,?,?)", (now(), kind, ref, detail))
