"""`polylab forecast <daily|run|resolve|eval>` — AI cross-check: will this soccer match end 0-0?

Design and pre-registered metrics: docs/research/llm-forecast-study.md. Once a day (Jenkins
`polylab-llm-forecast`, 10:00 KST):

1. resolve: fill outcomes for past forecasts from core.db (Total 0.5 resolution first, else the final score).
2. run (one batch): major-league soccer games kicking off in the next ~30h → one context (games, Polymarket prices,
   league base rates) copied into a separate dir per engine → BOTH engines independently, in parallel, same prompt:
   Claude (`claude -p`, web search/fetch) and ChatGPT (`codex exec`, web search off unless
   POLYLAB_FORECAST_CODEX_WEB=1) → each engine's validated rows appended as its own run (`<batch>-claude`,
   `<batch>-codex`) strictly before kickoff. No fallback: a failed engine is a failed run.
3. consensus (pre-registered, `compute_consensus`): only when both engines succeeded. Per game both forecast:
   P(0-0) = mean of the two engines' P(0-0); qualifies only if both engines ranked it in their own top-5; consensus
   rank = qualified games by that mean, lowest first. One engine failed → the batch's consensus is recorded `empty`.
4. O/U 0.5 picks (2026-10-10 owner, `compute_ou05_picks`, frozen in `ou05_picks` by the same run): inside the
   Europe top-5 + MLS pool, games both engines rank among their own lowest P(0-0) -> Over side, highest -> Under
   side. ai-ou05-red rests fee-free maker bids on exactly these rows (docs/research/ai-ou05-study.md).
5. Slack: consensus top-3 with both AI probabilities, market implied P(0-0) = 1 - Over 0.5 ask, the O/U 0.5
   picks, and the variants' paper/live status.

The research DB is append-only (SQLite triggers abort UPDATE/DELETE). core.db is only ever opened read-only.
Which forecast counts for a game (eval and llm_nil share these rules): per engine, the forecast from the latest
successful run of that engine created before kickoff (`canonical_forecasts(engine=...)`); for the consensus, the row
from the latest `ok` consensus batch that contains the game, created before kickoff (`canonical_consensus`). An
`empty` batch does not revoke an earlier batch's row. Same-KST-day reruns are refused unless --force (logged).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from polylab import settings
from polylab.autopilot.runner import ClaudeEngine, CodexEngine, Engine, RunResult
from polylab.collector.common import MAJOR_SOCCER_LEAGUES
from polylab.marketview import MarketView, make_book

DB_NAME = "llm_forecasts.db"
SCHEMA_ID = "polylab.llm_forecast/v1"
PROMPT_FILE = settings.REPO_ROOT / "prompts" / "llm_forecast.md"
HORIZON_H = 30.0
MIN_LEAD_MIN = 20              # games kicking off sooner than this are left out (AI run takes minutes)
MAX_GAMES = 40                 # "all candidate games"; both engines run in parallel inside the Jenkins budget
ENGINE_TIMEOUT_S = 3000        # 2026-10-10: 11 games took ~9 min; a 40-game league weekend needs headroom
MAX_SOURCES = 12
MAX_FACTORS = 12
RESEARCH_KEYS = ("form", "strength_gap", "managers", "h2h", "absences", "context")   # prompt v2 (2026-10-10)
TOP_N = 5                      # each engine's own ranked list (consensus qualification uses top-5)
ENGINES = ("claude", "codex")  # codex = ChatGPT (codex CLI)
ENGINE_LABEL = {"claude": "Claude", "codex": "ChatGPT"}
CONSENSUS_RULE = {"aggregate": "mean_p00", "order": "lowest_p00_first", "qualify": "both_engines_top5",
                  "qualify_top": TOP_N, "picks": 3, "engines": list(ENGINES), "on_engine_failure": "empty"}
CONSENSUS_VARIANT = "llm-nil-consensus"
# 2026-10-10 owner decisions `ai-ou05:red-live` + `ai-ou05:leagues`: the O/U 0.5 study pool is Europe's top-5 leagues,
# MLS, the Champions League, the Europa League and the Nations League. These games are offered to the AIs first
# (select_games) so the game cap never trims them, and picks rank only among them.
OU05_LEAGUES = ("epl", "lal", "bun", "sea", "fl1", "mls", "ucl", "uel", "unl")
OU05_VARIANT = "ai-ou05-red"
OU05_RULE = {"pool": "OU05_LEAGUES games with a Total 0.5 market (both tokens) at forecast time, forecast by both "
                     "engines, kickoff after the picks were made",
             "leagues": list(OU05_LEAGUES), "qualify_top": 8, "aggregate": "mean_p00",
             "engine_rank": "each engine's own P(0-0) inside the pool, ties by game_key",
             "over": "both engines rank the game within their qualify_top LOWEST P(0-0) -> buy Over 0.5",
             "under": "both engines rank the game within their qualify_top HIGHEST P(0-0) -> buy Under 0.5",
             "overlap": "a game qualifying for both sides is dropped (small slates)",
             "order": "over: mean P(0-0) asc; under: mean P(0-0) desc", "on_engine_failure": "empty",
             "variant": OU05_VARIANT}
CONFIDENCE = ("low", "medium", "high")
KST = dt.timezone(dt.timedelta(hours=9))
DISCLAIMER = "연구 기록이며 베팅 권유가 아님."

# claude: Read/Write/Edit + web lookup only. Must NOT reuse runner.DENY_TOOLS (it denies WebSearch/WebFetch and a
# deny beats an allow). No Bash/Task, no reads or writes under $HOME, writes only under the cwd. Grep/Glob are left
# out entirely: a canary on the Mac mini (claude 2.1.138, 2026-10-01) showed Grep on an absolute $HOME path ignores
# the Read(~/**) and Grep(~/**) denies, and with WebFetch/WebSearch that would be an exfiltration channel.
FORECAST_TOOLS = "Read,Write,Edit,WebSearch,WebFetch"
FORECAST_ALLOW = "Read,Write(./**),Edit(./**),WebSearch,WebFetch"
FORECAST_DENY = "Bash,Grep,Glob,NotebookEdit,Task,Read(~/**),Edit(~/**),Write(~/**)"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        ts INTEGER NOT NULL,                    -- forecasts written at (UTC)
        kst_date TEXT NOT NULL,
        engine TEXT,                            -- claude | codex | NULL (failed)
        model TEXT,
        prompt_sha TEXT NOT NULL,
        context_sha TEXT,
        games INTEGER NOT NULL,                 -- games offered to the AI
        n_forecasts INTEGER NOT NULL,
        status TEXT NOT NULL,                   -- ok | failed
        forced INTEGER NOT NULL DEFAULT 0,      -- same-day rerun with --force
        detail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forecasts (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        game_key TEXT NOT NULL,
        league TEXT,
        home_team TEXT,
        away_team TEXT,
        kickoff INTEGER NOT NULL,
        condition_id TEXT,                      -- Total 0.5 market (NULL = none in core.db at forecast time)
        token_id TEXT,                          -- its Over token
        market_price_at_forecast REAL,          -- Over 0.5 mid (NULL = no two-sided Over book)
        market_ask_at_forecast REAL,
        market_bid_at_forecast REAL,
        market_source TEXT,                     -- clob_live | stored_book | price_bar
        draw_price REAL,                        -- context only, never used as "market price"
        home_price REAL,
        away_price REAL,
        ai_prob REAL NOT NULL,                  -- P(not 0-0)
        ai_p_draw REAL,
        confidence TEXT,
        rank INTEGER,                           -- 1..3 in the AI top-3, NULL otherwise
        factors_json TEXT,
        sources_json TEXT,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (run_id, game_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS forecasts_game ON forecasts(game_key, created_at)",
    """
    CREATE TABLE IF NOT EXISTS consensus_batches (
        batch_id TEXT PRIMARY KEY,              -- <stamp>-daily|forced; engine runs are <batch_id>-<engine>
        ts INTEGER NOT NULL,
        kst_date TEXT NOT NULL,
        status TEXT NOT NULL,                   -- ok | empty (an engine failed / no games)
        rule_json TEXT NOT NULL,                -- CONSENSUS_RULE at the time (pre-registered)
        claude_run_id TEXT,
        codex_run_id TEXT,
        n_games INTEGER NOT NULL,               -- games both engines forecast before kickoff
        n_qualified INTEGER NOT NULL,
        forced INTEGER NOT NULL DEFAULT 0,
        detail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consensus (
        batch_id TEXT NOT NULL REFERENCES consensus_batches(batch_id),
        game_key TEXT NOT NULL,
        league TEXT,
        home_team TEXT,
        away_team TEXT,
        kickoff INTEGER NOT NULL,
        condition_id TEXT,
        token_id TEXT,
        p00_claude REAL NOT NULL,               -- P(0-0) = 1 - p_not_0_0
        p00_codex REAL NOT NULL,
        p00_consensus REAL NOT NULL,            -- mean of the two
        rank_claude INTEGER,                    -- the engine's own top-5 rank (NULL = outside)
        rank_codex INTEGER,
        qualifies INTEGER NOT NULL,             -- both ranks within top-5
        consensus_rank INTEGER,                 -- 1.. among qualified games (NULL = not qualified)
        market_price_at_forecast REAL,          -- Over 0.5 mid
        market_ask_at_forecast REAL,
        market_implied_p00 REAL,                -- 1 - Over 0.5 ask
        created_at INTEGER NOT NULL,
        PRIMARY KEY (batch_id, game_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS consensus_game ON consensus(game_key, created_at)",
    """
    CREATE TABLE IF NOT EXISTS ou05_pick_batches (
        batch_id TEXT PRIMARY KEY,              -- same id as consensus_batches
        ts INTEGER NOT NULL,
        kst_date TEXT NOT NULL,
        status TEXT NOT NULL,                   -- ok | empty (an engine failed / no pool)
        rule_json TEXT NOT NULL,                -- OU05_RULE at the time (pre-registered)
        n_pool INTEGER NOT NULL,
        n_over INTEGER NOT NULL,
        n_under INTEGER NOT NULL,
        detail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ou05_picks (
        batch_id TEXT NOT NULL REFERENCES ou05_pick_batches(batch_id),
        game_key TEXT NOT NULL,                 -- every pool game (side NULL = not picked: the ITT denominator)
        league TEXT,
        home_team TEXT,
        away_team TEXT,
        kickoff INTEGER NOT NULL,
        condition_id TEXT NOT NULL,             -- Total 0.5 market
        over_token TEXT NOT NULL,
        under_token TEXT NOT NULL,
        p00_claude REAL NOT NULL,
        p00_codex REAL NOT NULL,
        p00_mean REAL NOT NULL,
        low_rank_claude INTEGER NOT NULL,       -- 1 = the engine's lowest P(0-0) in the pool
        low_rank_codex INTEGER NOT NULL,
        high_rank_claude INTEGER NOT NULL,      -- 1 = the engine's highest P(0-0) in the pool
        high_rank_codex INTEGER NOT NULL,
        side TEXT,                              -- over | under | NULL
        pick_rank INTEGER,                      -- 1.. within the side
        over_bid REAL,
        over_ask REAL,
        under_bid REAL,
        under_ask REAL,
        overround REAL,                         -- over_ask + under_ask - 1 at pick time (NULL = a side unquoted)
        created_at INTEGER NOT NULL,
        PRIMARY KEY (batch_id, game_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ou05_picks_game ON ou05_picks(game_key, created_at)",
    """
    CREATE TABLE IF NOT EXISTS outcomes (
        game_key TEXT PRIMARY KEY,
        not_0_0 INTEGER NOT NULL,               -- 1 = at least one goal
        home_score INTEGER,
        away_score INTEGER,
        source TEXT NOT NULL,                   -- total_0_5_resolution | final_score
        resolved_at INTEGER,
        recorded_at INTEGER NOT NULL,
        detail TEXT
    )
    """,
    *[f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
      f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END"
      for t in ("runs", "forecasts", "outcomes", "consensus_batches", "consensus", "ou05_pick_batches", "ou05_picks")
      for op in ("UPDATE", "DELETE")],
)


# ---------------------------------------------------------------- research DB

def db_path(paths) -> Path:
    return Path(paths.research_dir) / DB_NAME


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    for stmt in SCHEMA:
        conn.execute(stmt)
    conn.commit()
    return conn


def connect_ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def kst_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")


def kst(ts: int | None, fmt: str = "%m-%d %H:%M") -> str:
    return dt.datetime.fromtimestamp(ts, KST).strftime(fmt) if ts else "–"


def canonical_forecasts(conn: sqlite3.Connection | None, now: int | None = None,
                        engine: str | None = None) -> dict[str, dict]:
    """game_key -> the forecast that counts: latest successful run (of `engine`, or any engine when None) created
    before kickoff (and at or before now). Shared by llm_eval and llm_nil so both use the same pre-registered rule.
    """
    if conn is None:
        return {}
    now = int(now if now is not None else time.time())
    rows = conn.execute(
        "SELECT f.*, r.engine, r.model, r.ts AS run_ts FROM forecasts f JOIN runs r USING(run_id) "
        "WHERE r.status='ok' AND f.created_at <= ? AND f.created_at < f.kickoff AND (? IS NULL OR r.engine = ?) "
        "ORDER BY f.game_key, f.created_at, f.run_id", (now, engine, engine)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["game_key"]] = dict(r)       # ordered by created_at: the last one wins
    return out


def load_canonical(path: Path, now: int | None = None, engine: str | None = None) -> dict[str, dict]:
    conn = connect_ro(path)
    try:
        return canonical_forecasts(conn, now, engine)
    finally:
        if conn is not None:
            conn.close()


def canonical_consensus(conn: sqlite3.Connection | None, now: int | None = None) -> dict[str, dict]:
    """game_key -> consensus row from the latest `ok` batch containing the game, created before kickoff and at or
    before now. `empty` batches have no rows, so they never revoke an earlier batch's row."""
    if conn is None:
        return {}
    now = int(now if now is not None else time.time())
    try:
        rows = conn.execute(
            "SELECT c.* FROM consensus c JOIN consensus_batches b USING(batch_id) WHERE b.status='ok' "
            "AND c.created_at <= ? AND c.created_at < c.kickoff ORDER BY c.game_key, c.created_at, c.batch_id",
            (now,)).fetchall()
    except sqlite3.OperationalError:       # research DB from before the consensus tables (read-only open)
        return {}
    return {r["game_key"]: dict(r) for r in rows}


def load_consensus_forecasts(path: Path, now: int | None = None) -> dict[str, dict]:
    """Consensus in the llm_nil forecast shape: rank = consensus rank, ai_prob = 1 - consensus P(0-0)."""
    conn = connect_ro(path)
    try:
        cons = canonical_consensus(conn, now)
    finally:
        if conn is not None:
            conn.close()
    return {gk: {**c, "rank": c["consensus_rank"], "ai_prob": round(1 - c["p00_consensus"], 6),
                 "run_id": c["batch_id"]} for gk, c in cons.items()}


def canonical_ou05_picks(conn: sqlite3.Connection | None, now: int | None = None) -> dict[str, dict]:
    """game_key -> its row in the latest `ok` O/U 0.5 pick batch containing it, created before kickoff and at or
    before now. A later batch that still has the game in its pool but no longer picks it (side NULL) wins, so a
    resting entry for it is withdrawn; `empty` batches have no rows and revoke nothing."""
    if conn is None:
        return {}
    now = int(now if now is not None else time.time())
    try:
        rows = conn.execute(
            "SELECT p.* FROM ou05_picks p JOIN ou05_pick_batches b USING(batch_id) WHERE b.status='ok' "
            "AND p.created_at <= ? AND p.created_at < p.kickoff ORDER BY p.game_key, p.created_at, p.batch_id",
            (now,)).fetchall()
    except sqlite3.OperationalError:       # research DB from before the pick tables (read-only open)
        return {}
    return {r["game_key"]: dict(r) for r in rows}


def load_ou05_picks(path: Path, now: int | None = None) -> dict[str, dict]:
    conn = connect_ro(path)
    try:
        return canonical_ou05_picks(conn, now)
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------- game selection & prices

@dataclass
class GameCtx:
    game_key: str
    league: str | None
    home_team: str | None
    away_team: str | None
    kickoff: int
    over_condition: str | None = None
    over_token: str | None = None
    under_token: str | None = None
    tokens: dict[str, str] | None = None      # role (over|under|draw|home|away) -> token_id
    volume: float = 0.0


def over_token(view: MarketView, game_key: str) -> tuple[str, str] | None:
    """(condition_id, Over token) of the game's Total 0.5 goals market, if collected."""
    ou = ou_tokens(view, game_key)
    return (ou[0], ou[1]) if ou else None


def ou_tokens(view: MarketView, game_key: str) -> tuple[str, str, str | None] | None:
    """(condition_id, Over token, Under token or None) of the game's Total 0.5 goals market, if collected."""
    for m in view.markets(game_key, types=("total",)):
        if m.line is None or abs(m.line - 0.5) > 1e-9:
            continue
        sides = {(t.side or (t.outcome_label or "").strip().lower()): t.token_id for t in m.tokens}
        if sides.get("over"):
            return m.condition_id, sides["over"], sides.get("under")
    return None


def select_games(view: MarketView, now: int, horizon_h: float = HORIZON_H, max_games: int = MAX_GAMES,
                 min_lead_min: int = MIN_LEAD_MIN) -> list[GameCtx]:
    """Major-league soccer games kicking off in (now+lead, now+horizon]. The O/U 0.5 study leagues come first, then
    games with an Over 0.5 token, then by result-market volume; the rest still get forecast with draw/moneyline
    prices as context."""
    leagues = {x.lower() for x in MAJOR_SOCCER_LEAGUES}
    out = []
    for g in view.upcoming_games(now, horizon_h, ["soccer"]):
        if (g.league or "").lower() not in leagues or g.start_time is None:
            continue
        if g.start_time - now < min_lead_min * 60:
            continue
        ctx = GameCtx(g.game_key, g.league, g.home_team, g.away_team, int(g.start_time), tokens={})
        ou = ou_tokens(view, g.game_key)
        if ou:
            ctx.over_condition, ctx.over_token, ctx.under_token = ou
            ctx.tokens["over"] = ou[1]
            if ou[2]:
                ctx.tokens["under"] = ou[2]
        for m in view.markets(g.game_key, types=("moneyline", "draw")):
            ctx.volume += m.volume or 0.0
            for t in m.tokens:
                if t.side in ("home", "away", "draw"):
                    ctx.tokens.setdefault(t.side, t.token_id)
        if not ctx.tokens:
            continue
        out.append(ctx)
    out.sort(key=lambda c: ((c.league or "").lower() not in OU05_LEAGUES, c.over_token is None, -c.volume, c.kickoff,
                            c.game_key))
    return out[:max_games]


def fetch_live_books(token_ids: list[str]) -> dict[str, dict]:
    """CLOB public POST /books (read-only, no auth). Best effort: {} on any failure."""
    if not token_ids:
        return {}
    try:
        from polylab.api import clob_public  # noqa: PLC0415
        return clob_public.books(token_ids)
    except Exception as exc:  # network/VPN down -> stored snapshots
        print(f"forecast: live books unavailable ({type(exc).__name__}); using stored snapshots", file=sys.stderr)
        return {}


def quote(token_id: str, live: dict[str, dict], view: MarketView, now: int) -> dict:
    """bid/ask/mid of a token: live CLOB book > stored book (<=1h) > canonical price bar (<=3h)."""
    raw = live.get(token_id)
    book, source = None, None
    if raw:
        book, source = make_book(token_id, now, raw.get("bids") or [], raw.get("asks") or []), "clob_live"
    if book is None or (book.best_bid is None and book.best_ask is None):
        book, source = view.book(token_id, now, max_age_s=3600), "stored_book"
    if book is not None and book.best_bid is not None and book.best_ask is not None and not book.crossed:
        return {"bid": book.best_bid, "ask": book.best_ask, "mid": round(book.mid, 4), "source": source}
    p = view.price(token_id, now, max_age_s=3 * 3600)
    ask = book.best_ask if book is not None else None
    if p:
        return {"bid": None, "ask": ask, "mid": round(p[1], 4), "source": "price_bar"}
    return {"bid": None, "ask": ask, "mid": None, "source": source if ask is not None else None}


def league_base_rates(core: sqlite3.Connection, now: int) -> dict[str, dict]:
    """Per-league finished-game goal stats known before `now` (context for the AI and the Poisson baseline)."""
    rows = core.execute(
        "SELECT LOWER(league) AS league, COUNT(*) AS n, AVG(home_score + away_score) AS mean_goals, "
        "AVG(CASE WHEN home_score + away_score = 0 THEN 1.0 ELSE 0.0 END) AS rate_0_0 FROM games "
        "WHERE sport='soccer' AND home_score IS NOT NULL AND away_score IS NOT NULL AND ended_at IS NOT NULL "
        "AND start_time < ? GROUP BY LOWER(league)", (now,)).fetchall()
    return {r["league"]: {"n": r["n"], "mean_goals": round(r["mean_goals"], 3), "rate_0_0": round(r["rate_0_0"], 4)}
            for r in rows if r["league"] in MAJOR_SOCCER_LEAGUES}


# ---------------------------------------------------------------- context & AI

def build_context(cwd: Path, games: list[GameCtx], quotes: dict[str, dict], base_rates: dict, now: int) -> str:
    cwd.mkdir(parents=True, exist_ok=True)
    rows = []
    for g in games:
        # 2026-10-10 protocol v2: the AI never sees Polymarket prices. The study asks whether a frontier model's own
        # research beats the market, so an anchor on the market price would only measure the market twice.
        # Prices are still recorded per forecast row (from `quotes`) for scoring.
        rows.append({
            "game_key": g.game_key, "league": g.league, "home_team": g.home_team, "away_team": g.away_team,
            "kickoff_utc": dt.datetime.fromtimestamp(g.kickoff, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "kickoff_kst": kst(g.kickoff, "%Y-%m-%d %H:%M"),
        })
    games_json = json.dumps({"generated_at_utc": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                             "games": rows}, ensure_ascii=False, indent=1)
    (cwd / "games.json").write_text(games_json)
    (cwd / "base_rates.json").write_text(json.dumps({
        "note": "finished games in the polylab DB since 2026-02 (regulation + any extra time as reported by Gamma); "
                "rate_0_0 = share of 0-0 finals, mean_goals = mean total goals",
        "leagues": base_rates}, indent=1))
    (cwd / "MANIFEST.md").write_text(
        "# LLM 0-0 forecast context\n\n- games.json: games to forecast (teams, league, kickoff; no market prices)\n"
        "- base_rates.json: league 0-0 base rates from our own finished games\n\n"
        f"Write forecasts.json here (schema {SCHEMA_ID}, see the prompt). Nothing else is required.\n")
    return hashlib.sha256(games_json.encode()).hexdigest()[:16]


# 2026-10-10 owner `ai-ou05:frontier-models`: both engines are pinned to the current frontier models the owner chose
# (Claude Opus 5.5, ChatGPT GPT-6.1-Sol) with high reasoning effort. Unpinned, the forecasts had silently run on the
# CLI defaults (claude: the account default Sonnet 4.6; codex with --ignore-user-config: gpt-6.1-sol at effort none).
FORECAST_CLAUDE_MODEL = "claude-opus-5-5"     # needs Claude Code >= 2.1.280
FORECAST_CLAUDE_EFFORT = "high"
FORECAST_CODEX_MODEL = "gpt-6.1-sol"
FORECAST_CODEX_EFFORT = "high"


class ForecastClaudeEngine(ClaudeEngine):
    def __init__(self, secrets_dir: Path | None = None, model: str | None = None, effort: str | None = None):
        super().__init__(secrets_dir, model or os.environ.get("POLYLAB_FORECAST_CLAUDE_MODEL") or FORECAST_CLAUDE_MODEL)
        self.effort = effort or os.environ.get("POLYLAB_FORECAST_CLAUDE_EFFORT") or FORECAST_CLAUDE_EFFORT

    def command(self) -> list[str]:
        cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", "dontAsk",
               "--tools", FORECAST_TOOLS, "--allowedTools", FORECAST_ALLOW, "--disallowedTools", FORECAST_DENY]
        cmd += ["--model", self.model] if self.model else []
        return cmd + (["--effort", self.effort] if self.effort else [])


CODEX_WEB_ENV = "POLYLAB_FORECAST_CODEX_WEB"
# Read-protected paths under $HOME while codex has a live web search (2026-10-10, owner asked for ChatGPT web search):
# ~/.polylab holds the 16 account keys, the Claude token and Slack/Supabase secrets. codex's own seatbelt cannot be
# nested, so codex runs with -s danger-full-access INSIDE this outer sandbox-exec profile, which denies reading these
# paths and any write outside the context dir / codex state / temp. Verified on the Mac mini (macOS 26, codex-cli
# 0.159.2): `head ~/.polylab/accounts.env` -> Operation not permitted, `touch ~/x` -> Operation not permitted.
CODEX_SECRET_DIRS = (".polylab", ".ssh", ".aws", ".gnupg", ".jenkins", ".config", ".docker", ".kube",
                     "Library/Keychains", ".claude", ".claude.json", ".netrc", ".git-credentials", ".npmrc", ".pypirc")


def codex_sandbox_profile(cwd: Path, home: Path | None = None) -> str:
    home = Path(home or Path.home())
    reads = " ".join(f'(subpath "{home / d}")' for d in CODEX_SECRET_DIRS)
    writable = [Path(cwd).resolve(), home / ".codex", Path("/private/tmp"), Path("/private/var/folders"), Path("/dev")]
    writes = " ".join(f'(subpath "{w}")' for w in writable)
    return (f"(version 1)(allow default)(deny file-read* {reads})"
            f"(deny file-write* (require-not (require-any {writes})))")


class ForecastCodexEngine(CodexEngine):
    """ChatGPT engine (runs alongside Claude, not as a fallback), GPT-6.1-Sol at high effort with live web search.
    Web search runs only inside the outer sandbox-exec profile (codex_sandbox_profile); where sandbox-exec is missing
    the engine refuses to run with web search rather than run unprotected. POLYLAB_FORECAST_CODEX_WEB=0 turns web
    search off (then codex's own workspace-write sandbox applies, as before 2026-10-10)."""

    def __init__(self, *a, web: bool | None = None, effort: str | None = None, sandbox: str | None = None, **kw):
        kw.setdefault("model", os.environ.get("POLYLAB_FORECAST_CODEX_MODEL") or FORECAST_CODEX_MODEL)
        super().__init__(*a, **kw)
        self.web = os.environ.get(CODEX_WEB_ENV, "1") != "0" if web is None else web
        self.effort = effort or os.environ.get("POLYLAB_FORECAST_CODEX_EFFORT") or FORECAST_CODEX_EFFORT
        self.sandbox = sandbox or "/usr/bin/sandbox-exec"

    def available(self) -> tuple[bool, str]:
        ok, why = super().available()
        if ok and self.web and not Path(self.sandbox).exists():
            return False, "sandbox-exec 없음: 비밀 보호 없이 웹 검색을 켜지 않음"
        return ok, why

    def command(self, cwd: Path) -> list[str]:
        cmd = super().command(cwd) + []
        cmd[-1:-1] = ["-c", f'model_reasoning_effort="{self.effort}"'] if self.effort else []
        if not self.web:
            return cmd
        out = []
        for c in cmd:
            if c == 'web_search="disabled"':
                c = 'web_search="live"'
            elif c == "workspace-write":
                c = "danger-full-access"          # confinement comes from the outer profile below
            elif c == "sandbox_workspace_write.network_access=false":
                c = "sandbox_workspace_write.network_access=true"
            out.append(c)
        return [self.sandbox, "-p", codex_sandbox_profile(cwd)] + out


def default_engines() -> list[Engine]:
    return [ForecastClaudeEngine(), ForecastCodexEngine()]


_URL = re.compile(r"^https?://[^\s]{3,500}$")


def _prob(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and 0.0 <= x <= 1.0 else None


def _text(v: Any, limit: int = 300) -> str:
    from polylab.reports.slack import scrub  # noqa: PLC0415
    return scrub(" ".join(str(v).split()))[:limit]


def parse_forecasts(data: Any, allowed: set[str]) -> tuple[list[dict], list[str], list[str]]:
    """Validate forecasts.json → (per-game rows, ranked top-3 game_keys, problems). Unknown keys are dropped."""
    problems: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("games"), list):
        return [], [], ["forecasts.json must be an object with a games list"]
    rows, seen = [], set()
    for g in data["games"]:
        if not isinstance(g, dict):
            continue
        key = str(g.get("game_key", ""))
        if key not in allowed or key in seen:
            problems.append(f"unknown or duplicate game_key {key[:40]!r}")
            continue
        p = _prob(g.get("p_not_0_0"))
        if p is None:
            problems.append(f"{key}: p_not_0_0 missing or outside [0,1]")
            continue
        seen.add(key)
        sources = [s.strip() for s in (g.get("sources") or []) if isinstance(s, str) and _URL.match(s.strip())]
        factors = [_text(f) for f in (g.get("factors") or g.get("key_factors") or []) if str(f).strip()]
        research = g.get("research") if isinstance(g.get("research"), dict) else {}
        found = [f"{k}: {_text(research[k])}" for k in RESEARCH_KEYS if str(research.get(k) or "").strip()]
        if len(found) < len(RESEARCH_KEYS):
            problems.append(f"{key}: research {len(found)}/{len(RESEARCH_KEYS)} items")
        factors = found + factors
        conf = str(g.get("confidence", "")).lower()
        rows.append({"game_key": key, "ai_prob": round(p, 4), "ai_p_draw": _prob(g.get("p_draw")),
                     "confidence": conf if conf in CONFIDENCE else None,
                     "factors": factors[:MAX_FACTORS], "sources": list(dict.fromkeys(sources))[:MAX_SOURCES]})
    top = []
    for k in data.get("top5") or data.get("top3") or data.get("ranked_top3") or []:
        k = str(k.get("game_key") if isinstance(k, dict) else k)
        if k in seen and k not in top:
            top.append(k)
    return rows, top[:TOP_N], problems


def _model_from_log(cwd: Path, engine: Engine) -> str | None:
    """The model that actually answered (claude: modelUsage of the run), else the pinned model; `@effort` appended."""
    effort = getattr(engine, "effort", None)
    tag = f"@{effort}" if effort else ""
    if engine.name != "claude":
        return f"{engine.model}{tag}" if getattr(engine, "model", None) else None
    try:
        out = (cwd / "claude.log").read_text().split("\n--- stderr ---")[0].strip().splitlines()
        usage = json.loads(out[-1]).get("modelUsage") or {}
        return (",".join(sorted(usage)) + tag) if usage else (f"{engine.model}{tag}" if engine.model else None)
    except (OSError, IndexError, json.JSONDecodeError, AttributeError):
        return f"{engine.model}{tag}" if getattr(engine, "model", None) else None


def run_engine(engine: Engine, prompt: str, cwd: Path, allowed: set[str], timeout: int,
               clock: Callable[[], float] = time.time) -> dict:
    """One engine, one context dir; no fallback. `finished_at` is when its forecasts became final."""
    out = {"engine": engine.name, "model": None, "rows": [], "top": [], "ok": False, "problems": [],
           "top_derived": False}
    ok, why = engine.available()
    if not ok:
        return {**out, "reason": why, "finished_at": int(clock())}
    (cwd / "forecasts.json").unlink(missing_ok=True)
    print(f"forecast: engine {engine.name} running", flush=True)
    res: RunResult = engine.run(prompt, cwd, timeout)
    finished = int(clock())
    try:
        data = json.loads((cwd / "forecasts.json").read_text())
    except (OSError, json.JSONDecodeError):
        data = None
    rows, top, problems = parse_forecasts(data, allowed) if data is not None else ([], [], ["forecasts.json missing"])
    if not (res.ok and rows):
        return {**out, "finished_at": finished, "problems": problems[:10],
                "reason": (res.reason or "; ".join(problems) or "no valid forecasts")[:300]}
    derived = not top
    if derived:      # no usable ranked list: rank by the engine's own probabilities (documented fallback)
        top = [r["game_key"] for r in sorted(rows, key=lambda r: (-r["ai_prob"], r["game_key"]))][:TOP_N]
    return {**out, "ok": True, "model": _model_from_log(cwd, engine), "rows": rows, "top": top,
            "problems": problems[:10], "top_derived": derived, "finished_at": finished}


# ---------------------------------------------------------------- run

def prompt_text() -> str:
    return PROMPT_FILE.read_text()


def already_ran_today(conn: sqlite3.Connection, now: int) -> bool:
    return conn.execute("SELECT 1 FROM runs WHERE status='ok' AND kst_date=?", (kst_date(now),)).fetchone() is not None


def run_forecast(paths, *, now: int | None = None, force: bool = False, engines: list[Engine] | None = None,
                 live_books: Callable[[list[str]], dict] = fetch_live_books, timeout: int = ENGINE_TIMEOUT_S,
                 max_games: int = MAX_GAMES, clock: Callable[[], float] = time.time) -> dict:
    now = int(now if now is not None else clock())
    engines = list(engines if engines is not None else default_engines())
    names = [e.name for e in engines]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate engine names {names}")
    rdb = connect(db_path(paths))
    try:
        if already_ran_today(rdb, now) and not force:
            return {"ok": True, "skipped": f"already forecast for KST {kst_date(now)} (use --force to rerun; logged)"}
        view = MarketView.open(paths, now)          # read-only core.db / books
        try:
            games = select_games(view, now, max_games=max_games)
            tokens = sorted({t for g in games for t in (g.tokens or {}).values()})
            live = live_books(tokens)
            quotes = {t: quote(t, live, view, now) for t in tokens}
            base_rates = league_base_rates(view.core, now)
        finally:
            view.close()
        prompt = prompt_text()
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
        batch_id = f"{stamp}-{'forced' if force else 'daily'}"
        if not games:
            for n in names:
                _insert_run(rdb, f"{batch_id}-{n}", now, n, None, prompt_sha, None, 0, [], "failed", force,
                            {"reason": "no games"})
            _insert_consensus(rdb, batch_id, now, "empty", {}, [], force, {"reason": "no games"})
            _insert_ou05(rdb, batch_id, now, "empty", [], {"reason": "no games"})
            return {"ok": True, "batch_id": batch_id, "games": 0, "forecasts": 0, "skipped": "no eligible games",
                    "consensus": {"status": "empty", "reason": "no games", "picks": [],
                                  "ou05": {"status": "empty", "reason": "no games", "over": [], "under": []}}}
        base = Path(paths.state) / "forecast" / stamp
        shas = {n: build_context(base / n, games, quotes, base_rates, now) for n in names}
        context_sha = shas[names[0]]
        allowed = {g.game_key for g in games}
        stored: dict[str, dict] = {}
        try:
            with ThreadPoolExecutor(max_workers=len(engines)) as ex:      # independent: own cwd, same prompt/context
                futs = {e.name: ex.submit(run_engine, e, prompt, base / e.name, allowed, timeout, clock)
                        for e in engines}
                results = {n: f.result() for n, f in futs.items()}
            for n in names:                                                # all DB writes on this thread
                stored[n] = _store_engine(rdb, results[n], games, quotes, f"{batch_id}-{n}", prompt_sha,
                                          context_sha, force, base / n)
            cons = _store_consensus(rdb, batch_id, stored, games, force, clock)
            cons["ou05"] = _store_ou05(rdb, batch_id, stored, games, quotes, clock)
        except Exception as exc:     # an AI run must never vanish silently: log failed runs, then raise
            err = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "context_dir": str(base)}
            for n in names:
                if n not in stored:
                    _insert_run(rdb, f"{batch_id}-{n}", int(clock()), n, None, prompt_sha, context_sha, len(games),
                                [], "failed", force, err)
            if not rdb.execute("SELECT 1 FROM consensus_batches WHERE batch_id=?", (batch_id,)).fetchone():
                _insert_consensus(rdb, batch_id, int(clock()), "empty", {}, [], force, err)
            if not rdb.execute("SELECT 1 FROM ou05_pick_batches WHERE batch_id=?", (batch_id,)).fetchone():
                _insert_ou05(rdb, batch_id, int(clock()), "empty", [], err)
            raise
        ok_engines = [n for n in names if stored[n]["ok"]]
        return {"ok": bool(ok_engines), "batch_id": batch_id, "games": len(games),
                "over_markets": sum(1 for g in games if g.over_token), "context_dir": str(base),
                "context_sha": context_sha, "context_shas_equal": len(set(shas.values())) == 1,
                "forecasts": sum(s["n"] for s in stored.values()), "engines": stored, "consensus": cons}
    finally:
        rdb.close()


def _store_engine(rdb, ai: dict, games: list[GameCtx], quotes: dict, run_id: str, prompt_sha: str, context_sha: str,
                  force: bool, cwd: Path) -> dict:
    created = int(ai["finished_at"])
    by_key = {g.game_key: g for g in games}
    rank = {k: i + 1 for i, k in enumerate(ai["top"])}
    rows, late = [], []
    for r in ai["rows"]:
        g = by_key[r["game_key"]]
        if g.kickoff <= created:
            late.append(g.game_key)         # never store a forecast made at/after kickoff
            continue
        q = quotes.get(g.over_token) if g.over_token else None
        rows.append({
            "run_id": run_id, "game_key": g.game_key, "league": g.league, "home_team": g.home_team,
            "away_team": g.away_team, "kickoff": g.kickoff, "condition_id": g.over_condition,
            "token_id": g.over_token,
            "market_price_at_forecast": q["mid"] if q and q.get("bid") is not None else None,
            "market_ask_at_forecast": q.get("ask") if q else None,
            "market_bid_at_forecast": q.get("bid") if q else None,
            "market_source": q.get("source") if q else None,
            "draw_price": (quotes.get((g.tokens or {}).get("draw")) or {}).get("mid"),
            "home_price": (quotes.get((g.tokens or {}).get("home")) or {}).get("mid"),
            "away_price": (quotes.get((g.tokens or {}).get("away")) or {}).get("mid"),
            "ai_prob": r["ai_prob"], "ai_p_draw": r["ai_p_draw"], "confidence": r["confidence"],
            "rank": rank.get(g.game_key), "factors_json": json.dumps(r["factors"], ensure_ascii=False),
            "sources_json": json.dumps(r["sources"]), "created_at": created})
    status = "ok" if rows else "failed"
    detail = {"problems": ai.get("problems"), "reason": ai.get("reason"), "top_derived": ai.get("top_derived"),
              "dropped_after_kickoff": late, "context_dir": str(cwd)}
    _insert_run(rdb, run_id, created, ai["engine"], ai["model"], prompt_sha, context_sha, len(games), rows,
                status, force, detail)
    return {"ok": status == "ok", "run_id": run_id, "engine": ai["engine"], "model": ai["model"], "n": len(rows),
            "created_at": created, "reason": ai.get("reason") if status != "ok" else None,
            "dropped_after_kickoff": late, "rows": rows,
            "top": [dict(r) for r in sorted((r for r in rows if r["rank"]), key=lambda r: r["rank"])]}


def compute_consensus(claude_rows: list[dict], codex_rows: list[dict], created: int,
                      rule: dict = CONSENSUS_RULE) -> list[dict]:
    """Pre-registered consensus (docs/research/llm-forecast-study.md §3). Inputs are the stored forecast rows of each
    engine (ai_prob = P(not 0-0), rank = the engine's own top-5 rank). Only games both engines forecast and that
    kick off after `created`. P(0-0) = mean of the two; qualifies iff both ranks <= qualify_top; consensus rank =
    qualified games ordered by (P(0-0) asc, worse of the two ranks, kickoff, game_key)."""
    top = int(rule["qualify_top"])
    other = {r["game_key"]: r for r in codex_rows}
    out = []
    for a in claude_rows:
        b = other.get(a["game_key"])
        if b is None or a["kickoff"] <= created:
            continue
        pa, pb = round(1 - a["ai_prob"], 6), round(1 - b["ai_prob"], 6)
        ra, rb = a.get("rank"), b.get("rank")
        ask = a.get("market_ask_at_forecast")
        out.append({"game_key": a["game_key"], "league": a["league"], "home_team": a["home_team"],
                    "away_team": a["away_team"], "kickoff": a["kickoff"], "condition_id": a["condition_id"],
                    "token_id": a["token_id"], "p00_claude": pa, "p00_codex": pb,
                    "p00_consensus": round((pa + pb) / 2, 6), "rank_claude": ra, "rank_codex": rb,
                    "qualifies": int(bool(ra and rb and ra <= top and rb <= top)), "consensus_rank": None,
                    "market_price_at_forecast": a.get("market_price_at_forecast"),
                    "market_ask_at_forecast": ask,
                    "market_implied_p00": round(1 - ask, 6) if ask is not None else None, "created_at": created})
    q = sorted((r for r in out if r["qualifies"]),
               key=lambda r: (r["p00_consensus"], max(r["rank_claude"], r["rank_codex"]), r["kickoff"], r["game_key"]))
    for i, r in enumerate(q):
        r["consensus_rank"] = i + 1
    return sorted(out, key=lambda r: (r["consensus_rank"] is None, r["consensus_rank"] or 0, r["p00_consensus"],
                                      r["game_key"]))


def _store_consensus(rdb, batch_id: str, stored: dict[str, dict], games: list[GameCtx], force: bool,
                     clock: Callable[[], float]) -> dict:
    created = int(clock())
    failed = [n for n in ENGINES if not (stored.get(n) or {}).get("ok")]
    if failed:
        reason = "; ".join(f"{ENGINE_LABEL.get(n, n)} 실패: {((stored.get(n) or {}).get('reason') or 'not run')[:120]}"
                           for n in failed)
        _insert_consensus(rdb, batch_id, created, "empty", stored, [], force, {"reason": reason})
        return {"status": "empty", "reason": reason, "picks": [], "rows": []}
    rows = compute_consensus(stored["claude"]["rows"], stored["codex"]["rows"], created)
    _insert_consensus(rdb, batch_id, created, "ok", stored, rows, force, {})
    picks = [r for r in rows if r["consensus_rank"] and r["consensus_rank"] <= int(CONSENSUS_RULE["picks"])]
    return {"status": "ok", "n_games": len(rows), "n_qualified": sum(r["qualifies"] for r in rows),
            "picks": picks, "rows": rows}


def compute_ou05_picks(claude_rows: list[dict], codex_rows: list[dict], games: dict[str, GameCtx], quotes: dict,
                      created: int, rule: dict = OU05_RULE) -> list[dict]:
    """Pre-registered O/U 0.5 picks (OU05_RULE, docs/research/ai-ou05-study.md). One row per pool game; side is
    over / under / None. Engine ranks come from each engine's own P(0-0) within the pool."""
    leagues = set(rule["leagues"])
    top = int(rule["qualify_top"])
    other = {r["game_key"]: r for r in codex_rows}
    pool = []
    for a in claude_rows:
        b, g = other.get(a["game_key"]), games.get(a["game_key"])
        if b is None or g is None or a["kickoff"] <= created or (g.league or "").lower() not in leagues:
            continue
        if not (g.over_condition and g.over_token and g.under_token):
            continue
        pool.append({"game_key": g.game_key, "pa": round(1 - a["ai_prob"], 6), "pb": round(1 - b["ai_prob"], 6),
                     "g": g})

    def ranks(key: str, high: bool) -> dict[str, int]:
        order = sorted(pool, key=lambda r: ((-r[key] if high else r[key]), r["game_key"]))
        return {r["game_key"]: i + 1 for i, r in enumerate(order)}
    lo_a, lo_b, hi_a, hi_b = ranks("pa", False), ranks("pb", False), ranks("pa", True), ranks("pb", True)
    out = []
    for r in pool:
        gk, g = r["game_key"], r["g"]
        over = lo_a[gk] <= top and lo_b[gk] <= top
        under = hi_a[gk] <= top and hi_b[gk] <= top
        oq, uq = quotes.get(g.over_token) or {}, quotes.get(g.under_token) or {}
        o_ask, u_ask = oq.get("ask"), uq.get("ask")
        out.append({"game_key": gk, "league": g.league, "home_team": g.home_team, "away_team": g.away_team,
                    "kickoff": g.kickoff, "condition_id": g.over_condition, "over_token": g.over_token,
                    "under_token": g.under_token, "p00_claude": r["pa"], "p00_codex": r["pb"],
                    "p00_mean": round((r["pa"] + r["pb"]) / 2, 6), "low_rank_claude": lo_a[gk],
                    "low_rank_codex": lo_b[gk], "high_rank_claude": hi_a[gk], "high_rank_codex": hi_b[gk],
                    "side": None if over == under else ("over" if over else "under"), "pick_rank": None,
                    "over_bid": oq.get("bid"), "over_ask": o_ask, "under_bid": uq.get("bid"), "under_ask": u_ask,
                    "overround": round(o_ask + u_ask - 1, 6) if o_ask is not None and u_ask is not None else None,
                    "created_at": created})
    for side, sign in (("over", 1), ("under", -1)):
        rank_key = "low_rank" if side == "over" else "high_rank"
        q = sorted((r for r in out if r["side"] == side),
                   key=lambda r: (sign * r["p00_mean"], max(r[f"{rank_key}_claude"], r[f"{rank_key}_codex"]),
                                  r["kickoff"], r["game_key"]))
        for i, r in enumerate(q):
            r["pick_rank"] = i + 1
    return sorted(out, key=lambda r: (r["side"] is None, r["side"] or "", r["pick_rank"] or 0, r["game_key"]))


def _store_ou05(rdb, batch_id: str, stored: dict[str, dict], games: list[GameCtx], quotes: dict,
                clock: Callable[[], float]) -> dict:
    created = int(clock())
    failed = [n for n in ENGINES if not (stored.get(n) or {}).get("ok")]
    if failed:
        reason = "; ".join(f"{ENGINE_LABEL.get(n, n)} 실패" for n in failed)
        _insert_ou05(rdb, batch_id, created, "empty", [], {"reason": reason})
        return {"status": "empty", "reason": reason, "over": [], "under": []}
    rows = compute_ou05_picks(stored["claude"]["rows"], stored["codex"]["rows"], {g.game_key: g for g in games},
                              quotes, created)
    _insert_ou05(rdb, batch_id, created, "ok", rows, {})
    return {"status": "ok", "n_pool": len(rows), "over": [r for r in rows if r["side"] == "over"],
            "under": [r for r in rows if r["side"] == "under"]}


def _insert_ou05(conn, batch_id, ts, status, rows, detail):
    with conn:
        conn.execute("INSERT INTO ou05_pick_batches(batch_id, ts, kst_date, status, rule_json, n_pool, n_over, "
                     "n_under, detail) VALUES(?,?,?,?,?,?,?,?,?)",
                     (batch_id, ts, kst_date(ts), status, json.dumps(OU05_RULE, sort_keys=True, ensure_ascii=False),
                      len(rows), sum(r["side"] == "over" for r in rows), sum(r["side"] == "under" for r in rows),
                      json.dumps(detail, ensure_ascii=False, default=str)))
        for r in rows:
            cols = ["batch_id", *r]
            conn.execute(f"INSERT INTO ou05_picks({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                         [batch_id, *r.values()])


def _insert_consensus(conn, batch_id, ts, status, stored, rows, forced, detail):
    with conn:
        conn.execute("INSERT INTO consensus_batches(batch_id, ts, kst_date, status, rule_json, claude_run_id, "
                     "codex_run_id, n_games, n_qualified, forced, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (batch_id, ts, kst_date(ts), status, json.dumps(CONSENSUS_RULE, sort_keys=True),
                      (stored.get("claude") or {}).get("run_id"), (stored.get("codex") or {}).get("run_id"),
                      len(rows), sum(r["qualifies"] for r in rows), int(bool(forced)),
                      json.dumps(detail, ensure_ascii=False, default=str)))
        for r in rows:
            cols = ["batch_id", *r]
            conn.execute(f"INSERT INTO consensus({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                         [batch_id, *r.values()])


def _insert_run(conn, run_id, ts, engine, model, prompt_sha, context_sha, games, rows, status, forced, detail):
    with conn:
        conn.execute("INSERT INTO runs(run_id, ts, kst_date, engine, model, prompt_sha, context_sha, games, "
                     "n_forecasts, status, forced, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (run_id, ts, kst_date(ts), engine, model, prompt_sha, context_sha, games, len(rows), status,
                      int(bool(forced)), json.dumps(detail, ensure_ascii=False, default=str)))
        for r in rows:
            cols = list(r)
            conn.execute(f"INSERT INTO forecasts({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                         [r[c] for c in cols])


# ---------------------------------------------------------------- outcomes

FINAL_SCORE_SETTLE_S = 3 * 3600      # a Gamma final score must be this old before it is trusted


def resolve_outcomes(paths, now: int | None = None) -> dict:
    """Append outcomes for forecast games that finished. Total 0.5 resolution first (regulation, as Polymarket
    settles), else the final score of an ended game. Never overwrites (append-only)."""
    now = int(now if now is not None else time.time())
    rdb = connect(db_path(paths))
    core = connect_ro(paths.core_db)
    added = {"total_0_5_resolution": 0, "final_score": 0}
    try:
        if core is None:
            return {"ok": False, "error": "core.db missing"}
        pending = [r[0] for r in rdb.execute(
            "SELECT DISTINCT game_key FROM forecasts WHERE game_key NOT IN (SELECT game_key FROM outcomes)")]
        view = MarketView(core)
        for gk in pending:
            g = core.execute("SELECT * FROM games WHERE game_key=?", (gk,)).fetchone()
            if g is None:
                continue
            row = None
            ov = over_token(view, gk)
            if ov:
                m = core.execute("SELECT resolved_outcome_index, resolved_at FROM markets WHERE condition_id=?",
                                 (ov[0],)).fetchone()
                if m and m["resolved_outcome_index"] is not None:
                    win = core.execute("SELECT token_id FROM tokens WHERE condition_id=? AND outcome_index=?",
                                       (ov[0], m["resolved_outcome_index"])).fetchone()
                    if win is not None:
                        row = (int(win["token_id"] == ov[1]), "total_0_5_resolution", m["resolved_at"])
            if row is None and g["ended_at"] and g["ended_at"] <= now - FINAL_SCORE_SETTLE_S \
                    and g["home_score"] is not None and g["away_score"] is not None and g["status"] != "cancelled":
                row = (int(g["home_score"] + g["away_score"] > 0), "final_score", g["ended_at"])
            if row is None:
                continue
            detail = {}
            if g["home_score"] is not None and g["away_score"] is not None:
                detail["score_consistent"] = int(g["home_score"] + g["away_score"] > 0) == row[0]
            with rdb:
                rdb.execute("INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?,?,?,?,?)",
                            (gk, row[0], g["home_score"], g["away_score"], row[1], row[2], now, json.dumps(detail)))
            added[row[1]] += 1
        return {"ok": True, "pending": len(pending), "added": added}
    finally:
        rdb.close()
        if core is not None:
            core.close()


# ---------------------------------------------------------------- slack

def consensus_variant_status(registry_dir: Path | None = None) -> str:
    """'live' | 'paper' | 'off' | 'absent' for llm-nil-consensus (read from strategies/*.yaml)."""
    try:
        from polylab.registry import REGISTRY_DIR, load_variant  # noqa: PLC0415
        return load_variant((registry_dir or REGISTRY_DIR) / f"{CONSENSUS_VARIANT}.yaml").mode
    except Exception:
        return "absent"


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{v:.1%}"


def slack_text(result: dict, status: str | None = None, now: int | None = None) -> str:
    date = kst_date(int(now if now is not None else time.time()))
    head = f"[연구] AI 교차검증 0:0 예측 {date}"
    if not result.get("ok") and not result.get("engines"):
        return f"{head}: 예측 실패 — {str(result.get('error') or result.get('skipped') or '')[:200]}"
    eng = result.get("engines") or {}
    parts = []
    for n in ENGINES:
        e = eng.get(n) or {}
        parts.append(f"{ENGINE_LABEL[n]}({e.get('model') or '?'}) {e['n']}경기" if e.get("ok")
                     else f"{ENGINE_LABEL[n]} 실패")
    lines = [f"{head}: {' · '.join(parts)} · Over 0.5 시장 {result.get('over_markets', 0)}경기"]
    cons = result.get("consensus") or {}
    if cons.get("status") != "ok":
        lines.append(f"두 AI 합의 없음(규칙상 비움): {str(cons.get('reason') or '')[:200]}")
    lines += ou05_slack_lines(cons.get("ou05") or {})
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def ou05_slack_lines(ou: dict, status: str | None = None) -> list[str]:
    """O/U 0.5 picks section: what ai-ou05-red will rest fee-free bids on (overround gate applies at order time)."""
    if not ou:
        return []
    mode = {"live": "실거래(live)", "paper": "paper(가상)", "off": "꺼짐"}.get(
        status or variant_mode(OU05_VARIANT), "미등록")
    head = (f"O/U 0.5 픽 (5대 리그·MLS·UCL·UEL·네이션스리그, 두 AI 모두 각자 상위 {OU05_RULE['qualify_top']}위) "
            f"→ {OU05_VARIANT} {mode}")
    if ou.get("status") != "ok":
        return [f"{head}: 없음 — {str(ou.get('reason') or '')[:150]}"]
    lines = [f"{head}: 대상 {ou.get('n_pool', 0)}경기 · Over {len(ou.get('over') or [])} · Under {len(ou.get('under') or [])}"]
    for side, label in (("over", "Over(0:0 아님)"), ("under", "Under(0:0)")):
        for r in ou.get(side) or []:
            ovr = r.get("overround")
            lines.append(f"  {label} {r['pick_rank']}. {r['home_team']} vs {r['away_team']} "
                         f"({(r['league'] or '').upper()}, {kst(r['kickoff'])} KST) P(0:0) Claude {_pct(r['p00_claude'])}"
                         f" · ChatGPT {_pct(r['p00_codex'])} · 호가 합 "
                         f"{'–' if ovr is None else f'{1 + ovr:.3f}'}")
    return lines


def variant_mode(variant_id: str, registry_dir: Path | None = None) -> str:
    try:
        from polylab.registry import REGISTRY_DIR, load_variant  # noqa: PLC0415
        return load_variant((registry_dir or REGISTRY_DIR) / f"{variant_id}.yaml").mode
    except Exception:
        return "absent"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab forecast")
    ap.add_argument("action", choices=("daily", "run", "resolve", "eval"),
                    help="daily = resolve + run + Slack (Jenkins polylab-llm-forecast)")
    ap.add_argument("--force", action="store_true", help="rerun even if today's forecast exists (logged in runs)")
    ap.add_argument("--no-slack", action="store_true")
    ap.add_argument("--max-games", type=int, default=MAX_GAMES)
    ap.add_argument("--timeout", type=int, default=ENGINE_TIMEOUT_S, help="per engine, seconds")
    ap.add_argument("--since", help="eval: kickoff since (YYYY-MM-DD)")
    args = ap.parse_args(argv)
    paths = settings.paths()
    if args.action == "eval":
        from polylab.research import llm_eval  # noqa: PLC0415
        since = int(dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc).timestamp()) \
            if args.since else None
        print(json.dumps(llm_eval.evaluate(paths, since=since), ensure_ascii=False, indent=1, default=str))
        return 0
    out: dict[str, Any] = {}
    if args.action in ("daily", "resolve"):
        out["resolve"] = resolve_outcomes(paths)
    if args.action in ("daily", "run"):
        res = run_forecast(paths, force=args.force, timeout=args.timeout, max_games=args.max_games)
        out["run"] = res
        for e in (res.get("engines") or {}).values():
            e.pop("rows", None)                     # full rows live in the research DB; keep the log readable
        text = slack_text(res) if not res.get("skipped") else None
        if text:
            out["slack_text"] = text
            if args.action == "daily" and not args.no_slack:
                from polylab.reports import slack  # noqa: PLC0415
                out["slack_posted"] = slack.post(text)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    run = out.get("run") or {}
    return 0 if (out.get("resolve") or {}).get("ok", True) and run.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
