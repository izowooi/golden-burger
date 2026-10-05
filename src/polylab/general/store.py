"""Storage for near-resolution general markets: `data/general/registry.db` + monthly `data/general/YYYY-MM.db`.

Schema changes are additive (append statements, never edit shipped ones), like db/schema.py.
All timestamps are unix seconds UTC; `gen_quotes.ts` is the minute bucket (multiple of 60).

Single definitions shared by discover, poll, backfill, MarketView (cherry live/paper) and the cherry backtest:
- `end_ref(game_start, end_date)`: the market's reference end. Gamma `endDate` of game markets is often kickoff+7d
  (probe 2026-10-05: sports endDate − gameStartTime median 0 h, p75 164 h), so a market with `gameStartTime` ends at
  kickoff + NOMINAL_GAME_S; every other market ends at `endDate`.
- `category(tags, fee_type)`: versioned tag-slug classifier (CATEGORY_VERSION). Raw tags/feeType are stored so a new
  classifier never needs a refetch.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from polylab import db as dbmod

POLL_HEARTBEAT_S = 600       # an unchanged polled quote is re-stored at least every 10 minutes
HIST_HEARTBEAT_S = 3600      # history rows: change-only, re-stored at least hourly (gaps > 1 h are missing data)
GAP_S = 180
NOMINAL_GAME_S = 3 * 3600
WINDOW_BEFORE_END_S = 4 * 86400          # collector horizon: end_ref within the next 4 days
HIST_BEFORE_END_S = int(4.5 * 86400)     # history window starts 4.5 days before end_ref
HIST_AFTER_END_S = 4 * 86400             # ... and runs to min(close, end_ref + 4 days) + 1 h

CATEGORY_VERSION = 1
CATEGORIES = ("sports", "esports", "crypto", "weather", "economy", "politics", "world", "culture", "tech", "other")
_CAT_TAGS: tuple[tuple[str, frozenset[str]], ...] = (
    ("esports", frozenset({"esports", "counter-strike-2", "league-of-legends", "dota-2", "valorant", "cs2"})),
    ("sports", frozenset({"sports", "soccer", "baseball", "basketball", "nba", "nfl", "nhl", "mlb", "tennis",
                          "football", "hockey", "golf", "ufc", "mma", "boxing", "cricket", "f1", "formula-1",
                          "ncaa", "cfb", "wnba", "epl", "games"})),
    ("crypto", frozenset({"crypto", "bitcoin", "ethereum", "solana", "xrp", "crypto-prices", "up-or-down", "dogecoin",
                          "hit-price", "multi-strikes", "kraken", "stablecoins", "airdrops"})),
    ("weather", frozenset({"weather", "climate", "temperature", "hurricanes"})),
    ("economy", frozenset({"economy", "economics", "finance", "fed", "fed-rates", "interest-rates", "inflation", "cpi",
                           "jobs", "earnings", "equities", "stocks", "spx", "oil", "commodities", "gdp",
                           "recession", "tariffs", "ipos", "finance-prices", "big-tech"})),
    ("politics", frozenset({"elections", "global-elections", "world-elections", "us-presidential-election",
                            "primaries", "main-election", "macro-election-2", "midterms", "us-elections"})),
    ("world", frozenset({"geopolitics", "world", "iran", "israel", "ukraine", "russia", "middle-east", "gaza", "china",
                         "war", "foreign-policy"})),
    ("politics", frozenset({"politics", "trump", "us-politics", "congress", "supreme-court", "courts"})),
    ("culture", frozenset({"pop-culture", "culture", "movies", "music", "song", "tweets-markets", "mentions",
                           "celebrities", "awards", "netflix", "rotten-tomatoes", "mrbeast", "views", "tv", "youtube",
                           "box-office", "spotify"})),
    ("tech", frozenset({"tech", "ai", "science", "openai", "spacex", "space", "apple", "tesla", "google"})),
)
_FEE_CAT = {"politics": "politics", "crypto": "crypto", "sports": "sports", "weather": "weather",
            "finance": "economy", "economics": "economy", "culture": "culture", "mentions": "culture", "tech": "tech"}


def category(tags: list[str] | None, fee_type: str | None = None) -> str:
    """First matching rule in _CAT_TAGS order (sports beats everything: game markets carry many tags)."""
    slugs = {str(t).lower() for t in tags or []}
    for name, keys in _CAT_TAGS:
        if slugs & keys:
            return name
    ft = (fee_type or "").lower()
    for prefix, name in _FEE_CAT.items():
        if ft.startswith(prefix):
            return name
    return "other"


def end_ref(game_start: int | None, end_date: int | None) -> int | None:
    if game_start:
        return int(game_start) + NOMINAL_GAME_S
    return int(end_date) if end_date else None


REGISTRY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS gen_markets (
        id INTEGER PRIMARY KEY,                 -- compact key used by the quote shards
        condition_id TEXT NOT NULL UNIQUE,
        market_id TEXT,
        event_id TEXT,
        event_slug TEXT,
        question TEXT,
        slug TEXT,
        category TEXT,                          -- store.category(tags, fee_type) at CATEGORY_VERSION
        tags TEXT,                              -- JSON list of Gamma tag slugs (raw input of the classifier)
        outcomes TEXT,                          -- JSON list of outcome labels (index order)
        yes_token TEXT NOT NULL,                -- outcome index 0
        no_token TEXT NOT NULL,                 -- outcome index 1
        neg_risk INTEGER,
        sports_market_type TEXT,
        created_at INTEGER,                     -- Gamma createdAt
        start_date INTEGER,                     -- Gamma startDate (listing)
        end_date INTEGER,                       -- Gamma endDate (latest seen)
        game_start INTEGER,                     -- Gamma gameStartTime (game markets)
        end_ref INTEGER,                        -- store.end_ref(game_start, end_date)
        closed INTEGER NOT NULL DEFAULT 0,
        closed_at INTEGER,                      -- Gamma closedTime
        resolved_index INTEGER,                 -- winning outcome index (exact), NULL unknown/void
        resolution_source TEXT,
        fee_type TEXT,
        fee_json TEXT,                          -- {"feesEnabled":..,"feeSchedule":{rate,exponent,takerOnly}} as seen
        volume REAL,                            -- latest Gamma volumeNum (final for closed markets: look-ahead)
        liquidity REAL,                         -- latest Gamma liquidityNum (0 after close)
        in_core INTEGER NOT NULL DEFAULT 0,     -- condition also tracked by core.db (polled there, not here)
        first_seen INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        source TEXT NOT NULL,                   -- discover | history_enum
        hist_status TEXT,                       -- NULL todo | done | empty | failed | skip
        hist_rows INTEGER,
        hist_from INTEGER,
        hist_until INTEGER,
        hist_updated_at INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS gen_markets_open ON gen_markets(closed, end_ref)",
    "CREATE INDEX IF NOT EXISTS gen_markets_hist ON gen_markets(hist_status, end_ref)",
    "CREATE INDEX IF NOT EXISTS gen_markets_yes ON gen_markets(yes_token)",
    "CREATE INDEX IF NOT EXISTS gen_markets_no ON gen_markets(no_token)",
    """
    CREATE TABLE IF NOT EXISTS gen_metrics (
        id INTEGER NOT NULL,                    -- gen_markets.id
        ts INTEGER NOT NULL,                    -- discover run minute
        volume REAL,
        liquidity REAL,
        PRIMARY KEY (id, ts)
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS gen_last (
        id INTEGER PRIMARY KEY,                 -- last stored poll row (dedupe across process runs)
        ts INTEGER NOT NULL,
        sig TEXT NOT NULL
    )
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
        kind TEXT NOT NULL,
        ref TEXT,
        detail TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS gen_quality_ts ON quality_events(ts)",
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
    "CREATE INDEX IF NOT EXISTS gen_job_runs ON job_runs(job, started_at)",
)

QUOTES_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS gen_quotes (
        id INTEGER NOT NULL,                    -- gen_markets.id
        ts INTEGER NOT NULL,                    -- minute bucket (UTC)
        bid REAL, ask REAL,                     -- YES token level-1 (poll only)
        bid_sz REAL, ask_sz REAL,               -- level-1 sizes in shares (poll only)
        last REAL,                              -- book last_trade_price (poll only)
        p REAL,                                 -- poll: (bid+ask)/2 when two-sided; history: prices-history p
        src INTEGER NOT NULL,                   -- 0 poll | 1 history
        PRIMARY KEY (id, ts)
    ) WITHOUT ROWID
    """,
)
QUOTE_COLS = ("id", "ts", "bid", "ask", "bid_sz", "ask_sz", "last", "p", "src")
INSERT_QUOTES = f"INSERT OR IGNORE INTO gen_quotes({', '.join(QUOTE_COLS)}) VALUES({', '.join('?' * len(QUOTE_COLS))})"
SRC_POLL, SRC_HISTORY = 0, 1

SHARD_RE = re.compile(r"^\d{4}-\d{2}\.db$")


def general_dir(paths) -> Path:
    return Path(paths.data) / "general"


def registry_path(paths) -> Path:
    return general_dir(paths) / "registry.db"


def registry(paths, readonly: bool = False) -> sqlite3.Connection:
    return dbmod.connect(registry_path(paths), None if readonly else REGISTRY_SCHEMA, readonly)


def shard(paths, year_month: str, readonly: bool = False) -> sqlite3.Connection:
    return dbmod.connect(general_dir(paths) / f"{year_month}.db", None if readonly else QUOTES_SCHEMA, readonly)


def shard_paths(paths) -> list[Path]:
    d = general_dir(paths)
    return sorted(p for p in d.glob("*.db") if SHARD_RE.match(p.name)) if d.exists() else []


def write_quotes(paths, rows: list[tuple]) -> int:
    """Insert rows (QUOTE_COLS order) into their month shards; an existing (id, ts) row wins (poll beats history)."""
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


def core_condition_ids(paths, cids: list[str]) -> set[str]:
    """Which of `cids` core.db already tracks (read-only; empty when core.db is unavailable)."""
    if not cids or not Path(paths.core_db).exists():
        return set()
    try:
        conn = dbmod.core(paths, readonly=True)
    except sqlite3.Error:
        return set()
    try:
        out: set[str] = set()
        for i in range(0, len(cids), 500):
            part = cids[i:i + 500]
            out |= {r[0] for r in conn.execute(
                f"SELECT condition_id FROM markets WHERE condition_id IN ({','.join('?' * len(part))})", part)}
        return out
    except sqlite3.Error:
        return set()
    finally:
        conn.close()
