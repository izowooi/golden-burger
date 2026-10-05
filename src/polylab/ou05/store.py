"""Storage for the O/U 0.5 study: `data/ou05/registry.db` + monthly `data/ou05/YYYY-MM.db` shards.

Schema changes are additive (append statements, never edit shipped ones), like db/schema.py.
All timestamps are unix seconds UTC; `ou05_quotes.ts` is the minute bucket (multiple of 60).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from polylab import db as dbmod

HEARTBEAT_S = 600        # an unchanged quote is re-stored at least every 10 minutes
GAP_S = 180              # poll runs further apart than this are a gap (quality event)

REGISTRY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS ou05_markets (
        condition_id TEXT PRIMARY KEY,
        market_id TEXT,
        game_key TEXT,                          -- Gamma main event id (child event's parentEventId) = core games.game_key
        event_id TEXT,                          -- the "<game> - More Markets" child event holding the line
        league TEXT,                            -- event slug prefix (teams[].league code), all soccer leagues
        home TEXT,
        away TEXT,
        question TEXT,
        slug TEXT,
        over_token TEXT NOT NULL,
        under_token TEXT NOT NULL,
        created_at INTEGER,                     -- Gamma createdAt (market listed)
        game_start INTEGER,                     -- gameStartTime, refreshed every discover (postponements)
        end_date INTEGER,
        closed INTEGER NOT NULL DEFAULT 0,
        closed_at INTEGER,                      -- Gamma closedTime
        resolved_over INTEGER,                  -- 1 = Over won, 0 = Under won, NULL = unresolved/unknown
        resolution_source TEXT,                 -- gamma_outcome_prices | data_api_v2
        final_score TEXT,                       -- "home-away" from the main event once ended
        home_score INTEGER,
        away_score INTEGER,
        volume REAL,                            -- latest Gamma volumeNum
        liquidity REAL,                         -- latest Gamma liquidityNum
        first_seen INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        source TEXT NOT NULL,                   -- discover | history_enum
        hist_status TEXT,                       -- NULL todo | done | empty | failed
        hist_rows INTEGER,
        hist_until INTEGER,                     -- history fetched up to this ts
        hist_updated_at INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS ou05_markets_open ON ou05_markets(closed, game_start)",
    "CREATE INDEX IF NOT EXISTS ou05_markets_hist ON ou05_markets(hist_status, game_start)",
    """
    CREATE TABLE IF NOT EXISTS ou05_metrics (
        condition_id TEXT NOT NULL,
        ts INTEGER NOT NULL,                    -- discover run minute
        volume REAL,
        liquidity REAL,
        PRIMARY KEY (condition_id, ts)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS ou05_last (
        condition_id TEXT PRIMARY KEY,          -- last stored poll row (dedupe across process runs)
        ts INTEGER NOT NULL,
        sig TEXT NOT NULL
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        name TEXT PRIMARY KEY,
        value TEXT,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL,                     -- ou05_poll_gap | ou05_mirror_break | ou05_stale_open | ...
        ref TEXT,
        detail TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ou05_quality_ts ON quality_events(ts)",
    """
    CREATE TABLE IF NOT EXISTS job_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job TEXT NOT NULL,
        started_at INTEGER NOT NULL,
        finished_at INTEGER,
        ok INTEGER,
        summary TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ou05_job_runs ON job_runs(job, started_at)",
)

QUOTES_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS ou05_quotes (
        condition_id TEXT NOT NULL,
        ts INTEGER NOT NULL,                    -- minute bucket (UTC)
        over_bid REAL, over_ask REAL, under_bid REAL, under_ask REAL,
        over_bid_sz REAL, over_ask_sz REAL, under_bid_sz REAL, under_ask_sz REAL,   -- level-1 sizes (shares)
        over_mid REAL, under_mid REAL,          -- poll: (bid+ask)/2 when two-sided; history: prices-history p
        sum_ask REAL, sum_bid REAL,             -- over_ask+under_ask / over_bid+under_bid (poll only)
        over_last REAL, under_last REAL,        -- book last_trade_price (poll only)
        minutes_to_kickoff REAL,                -- (game_start - ts)/60; negative = after kickoff
        game_status TEXT,                       -- core games.status when known, else pre | started (clock)
        source TEXT NOT NULL,                   -- poll | history
        PRIMARY KEY (condition_id, ts)
    ) WITHOUT ROWID
    """,
)

QUOTE_COLS = ("condition_id", "ts", "over_bid", "over_ask", "under_bid", "under_ask", "over_bid_sz", "over_ask_sz",
              "under_bid_sz", "under_ask_sz", "over_mid", "under_mid", "sum_ask", "sum_bid", "over_last", "under_last",
              "minutes_to_kickoff", "game_status", "source")
INSERT_QUOTES = (f"INSERT OR IGNORE INTO ou05_quotes({', '.join(QUOTE_COLS)}) "
                 f"VALUES({', '.join('?' * len(QUOTE_COLS))})")

SHARD_RE = re.compile(r"^\d{4}-\d{2}\.db$")


def ou05_dir(paths) -> Path:
    return Path(paths.data) / "ou05"


def registry(paths, readonly: bool = False) -> sqlite3.Connection:
    return dbmod.connect(ou05_dir(paths) / "registry.db", None if readonly else REGISTRY_SCHEMA, readonly)


def shard(paths, year_month: str, readonly: bool = False) -> sqlite3.Connection:
    return dbmod.connect(ou05_dir(paths) / f"{year_month}.db", None if readonly else QUOTES_SCHEMA, readonly)


def shard_paths(paths) -> list[Path]:
    d = ou05_dir(paths)
    return sorted(p for p in d.glob("*.db") if SHARD_RE.match(p.name)) if d.exists() else []


def write_quotes(paths, rows: list[tuple]) -> int:
    """Insert quote rows (QUOTE_COLS order) into their month shards. Existing (condition_id, ts) rows win
    (INSERT OR IGNORE), so a history row never overwrites a poll row of the same minute. Returns rows inserted."""
    by_month: dict[str, list[tuple]] = {}
    for r in rows:
        by_month.setdefault(dbmod.year_month(r[1]), []).append(r)
    n = 0
    for ym, rs in sorted(by_month.items()):
        conn = shard(paths, ym)
        try:
            with dbmod.tx(conn):
                before = conn.total_changes
                conn.executemany(INSERT_QUOTES, rs)
                n += conn.total_changes - before
        finally:
            conn.close()
    return n


def quality(conn: sqlite3.Connection, kind: str, ref: str | None, **detail) -> None:
    conn.execute("INSERT INTO quality_events(ts, kind, ref, detail) VALUES(?,?,?,?)",
                 (int(time.time()), kind, ref, json.dumps(detail, separators=(",", ":"), default=str)))


def job_start(conn: sqlite3.Connection, job: str) -> int:
    cur = conn.execute("INSERT INTO job_runs(job, started_at) VALUES(?,?)", (job, int(time.time())))
    conn.commit()
    return int(cur.lastrowid)


def job_finish(conn: sqlite3.Connection, run_id: int, ok: bool, summary: dict) -> None:
    conn.execute("UPDATE job_runs SET finished_at=?, ok=?, summary=? WHERE id=?",
                 (int(time.time()), 1 if ok else 0, json.dumps(summary, separators=(",", ":"), default=str), run_id))
    conn.commit()


def clock_status(ts: int, game_start: int | None) -> str | None:
    if game_start is None:
        return None
    return "pre" if ts < game_start else "started"


def minutes_to_kickoff(ts: int, game_start: int | None) -> float | None:
    return None if game_start is None else round((game_start - ts) / 60.0, 1)
