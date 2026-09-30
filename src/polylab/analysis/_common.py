"""Shared read helpers for analysis: canonical prices, game clock, phase buckets.

Everything here is read-only against core.db / books shards and returns pandas frames.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from polylab.collector.common import MAJOR_SOCCER_LEAGUES
from polylab.db import connect

SPORTS = ("soccer", "mlb", "nba", "nfl", "nhl")
PHASES = ("pre", "early", "mid", "late", "final")

# Canonical price policy (ARCHITECTURE §5): poll_mid > ws_last > history for the same minute.
SOURCE_RANK = {"poll_mid": 0, "ws_last": 1, "history": 2}
STATE_SOURCE_RANK = {"ws_sports": 0, "gamma": 1, "backfill": 2}

# Regulation length in the unit of games.game_states.game_minute (MLB: innings).
REGULATION = {"soccer": 90.0, "nba": 48.0, "nfl": 60.0, "nhl": 60.0, "mlb": 9.0}
# Typical wall-clock minutes from scheduled start to regulation end, used only when the
# feed gives no game clock. Includes breaks (soccer half-time, NFL/NBA stoppages).
WALL_MINUTES = {"soccer": 112.0, "nba": 140.0, "nfl": 195.0, "nhl": 155.0, "mlb": 180.0}
# Phase cutoffs on the normalised fraction of regulation played.
PHASE_CUTS = ((0.30, "early"), (0.60, "mid"), (0.85, "late"))
STALE_CLOCK_MIN = {"soccer": 20.0, "other": 30.0}
# Event-study minute bucket width per sport, in game_minute units.
MINUTE_BUCKET = {"soccer": 15.0, "nba": 12.0, "nfl": 15.0, "nhl": 20.0, "mlb": 3.0}

PHASE_NOTE = (
    "phase = normalised fraction f of regulation (game_minute / soccer 90, NBA 48, NFL 60, NHL 60, MLB 9 innings; "
    "fallback wall-clock elapsed since start_time / soccer 112, NBA 140, NFL 195, NHL 155, MLB 180 min): "
    "pre = before start, early f<0.30, mid <0.60, late <0.85, final >=0.85 until game end"
)

KST = dt.timezone(dt.timedelta(hours=9))

LEAGUE_NOTE = ("soccer = major competitions only (" + ", ".join(sorted(MAJOR_SOCCER_LEAGUES))
               + "); pass all_leagues / --all-leagues to include every stored league")


def league_filter_sql(alias: str = "g", all_leagues: bool = False) -> tuple[str, list[str]]:
    """(' AND <clause>', params) keeping non-soccer rows and soccer rows of MAJOR_SOCCER_LEAGUES — the same set
    the collector uses. Minor-league rows collected earlier stay in core.db and come back with all_leagues.
    A NULL league is unknown, not minor, and is kept."""
    if all_leagues:
        return "", []
    leagues = sorted(MAJOR_SOCCER_LEAGUES)
    return (f" AND ({alias}.sport != 'soccer' OR {alias}.league IS NULL OR LOWER({alias}.league) IN "
            f"({','.join('?' * len(leagues))}))", leagues)


def league_mask(df: pd.DataFrame, all_leagues: bool = False) -> pd.Series:
    """Row mask for frames with `sport` and `league` columns (same rule as league_filter_sql)."""
    if all_leagues or df.empty:
        return pd.Series(True, index=df.index)
    league = df["league"]
    return (df["sport"] != "soccer") | league.isna() | league.astype(str).str.lower().isin(MAJOR_SOCCER_LEAGUES)


def open_ro(path: Path) -> sqlite3.Connection | None:
    """Read-only connection or None when the DB does not exist yet."""
    if not Path(path).exists():
        return None
    return connect(Path(path), readonly=True)


def iso(ts: float | int | None) -> str | None:
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_since(value: str | None) -> int | None:
    """`YYYY-MM-DD` (UTC midnight), `Nd` (N days ago) or unix seconds."""
    if not value:
        return None
    value = value.strip()
    if value.endswith("d") and value[:-1].isdigit():
        return int(dt.datetime.now(dt.timezone.utc).timestamp()) - int(value[:-1]) * 86400
    if value.isdigit():
        return int(value)
    return int(dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())


def _chunks(items: list, size: int = 500) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def read_sql(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> pd.DataFrame:
    cur = conn.execute(sql, tuple(params))
    cols = [c[0] for c in cur.description]
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def canonical_prices(conn: sqlite3.Connection, token_ids: list[str] | None = None,
                     since: int | None = None, until: int | None = None) -> pd.DataFrame:
    """One price per (token_id, ts minute) following the source priority policy."""
    where, params = [], []
    if since is not None:
        where.append("ts >= ?")
        params.append(since)
    if until is not None:
        where.append("ts < ?")
        params.append(until)
    frames = []
    groups = list(_chunks(sorted(set(token_ids)))) if token_ids is not None else [None]
    for group in groups:
        w = list(where)
        p = list(params)
        if group is not None:
            if not group:
                continue
            w.append(f"token_id IN ({','.join('?' * len(group))})")
            p.extend(group)
        sql = "SELECT token_id, ts, source, price FROM price_bars"
        if w:
            sql += " WHERE " + " AND ".join(w)
        frames.append(read_sql(conn, sql, p))
    cols = ["token_id", "ts", "price", "source"]
    if not frames:
        return pd.DataFrame(columns=cols)
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["_rank"] = df["source"].map(SOURCE_RANK).fillna(9)
    df = df.sort_values(["token_id", "ts", "_rank"]).drop_duplicates(["token_id", "ts"], keep="first")
    return df[cols].reset_index(drop=True)


def game_states(conn: sqlite3.Connection, game_keys: list[str] | None = None) -> pd.DataFrame:
    """Game state change log, one row per (game_key, ts) preferring the WS sports feed."""
    cols = "game_key, ts, source, status, live, ended, period, game_minute, home_score, away_score"
    frames = []
    groups = list(_chunks(sorted(set(game_keys)))) if game_keys is not None else [None]
    for group in groups:
        if group is None:
            frames.append(read_sql(conn, f"SELECT {cols} FROM game_states"))
        elif group:
            frames.append(read_sql(conn, f"SELECT {cols} FROM game_states WHERE game_key IN "
                                         f"({','.join('?' * len(group))})", group))
    if not frames or all(f.empty for f in frames):
        return pd.DataFrame(columns=[c.strip() for c in cols.split(",")])
    df = pd.concat(frames, ignore_index=True)
    df["_rank"] = df["source"].map(STATE_SOURCE_RANK).fillna(9)
    df = df.sort_values(["game_key", "ts", "_rank"]).drop_duplicates(["game_key", "ts"], keep="first")
    return df.drop(columns="_rank").reset_index(drop=True)


def attach_state(obs: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    """As-of (backward) join of the latest game state onto each (game_key, ts) observation.

    game_states only stores changes, so the state valid at ts is the last one at or before ts.
    """
    extra = ["game_minute", "period", "home_score", "away_score", "state_ts"]
    if obs.empty:
        return obs.assign(**{c: pd.Series(dtype=float) for c in extra})
    if states.empty:
        out = obs.copy()
        for c in extra:
            out[c] = np.nan
        return out
    left = obs.copy()
    left["_order"] = np.arange(len(left))
    left["ts"] = left["ts"].astype("int64")
    right = states[["game_key", "ts", "game_minute", "period", "home_score", "away_score"]].copy()
    right["ts"] = right["ts"].astype("int64")
    right["state_ts"] = right["ts"]
    merged = pd.merge_asof(left.sort_values("ts"), right.sort_values("ts"), on="ts",
                           by="game_key", direction="backward")
    merged = merged.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    # States are stored on change only. A soccer clock keeps running between updates, so it is
    # extrapolated; any clock older than STALE_CLOCK_MIN is dropped and the wall-clock fallback applies.
    gm = pd.to_numeric(merged["game_minute"], errors="coerce")
    age_min = (merged["ts"] - merged["state_ts"]) / 60.0
    sport = merged.get("sport")
    if sport is not None:
        soccer = (sport == "soccer").to_numpy()
        gm = pd.Series(np.where(soccer & gm.notna(), gm + age_min.clip(lower=0), gm), index=merged.index)
        limit = np.where(soccer, STALE_CLOCK_MIN["soccer"], STALE_CLOCK_MIN["other"])
        gm = gm.where(~(age_min > limit))
    merged["game_minute"] = gm
    return merged


def fraction_played(sport: pd.Series, ts: pd.Series, start_time: pd.Series,
                    game_minute: pd.Series) -> pd.Series:
    reg = sport.map(REGULATION)
    wall = sport.map(WALL_MINUTES)
    gm = pd.to_numeric(game_minute, errors="coerce")
    by_clock = gm / reg
    elapsed = (pd.to_numeric(ts) - pd.to_numeric(start_time, errors="coerce")) / 60.0
    by_wall = elapsed / wall
    return by_clock.where(gm.notna(), by_wall)


def phase_series(sport: pd.Series, ts: pd.Series, start_time: pd.Series, game_minute: pd.Series,
                 ended_at: pd.Series | None = None) -> pd.Series:
    """pre/early/mid/late/final, or None for rows after the game ended / unknown timing."""
    frac = fraction_played(sport, ts, start_time, game_minute)
    start = pd.to_numeric(start_time, errors="coerce")
    tsn = pd.to_numeric(ts)
    gm = pd.to_numeric(game_minute, errors="coerce")
    out = pd.Series([None] * len(ts), index=ts.index, dtype=object)
    pre = (tsn < start) & gm.isna()
    pre = pre | (gm.notna() & (gm <= 0))
    out[pre] = "pre"
    lo = 0.0
    live = ~pre & frac.notna() & (frac >= 0)
    for cut, name in PHASE_CUTS:
        out[live & (frac >= lo) & (frac < cut)] = name
        lo = cut
    out[live & (frac >= lo)] = "final"
    if ended_at is not None:
        end = pd.to_numeric(ended_at, errors="coerce")
        out[end.notna() & (tsn >= end)] = None
    return out


def minute_bucket(sport: str, minute: float | None) -> str | None:
    if minute is None or (isinstance(minute, float) and np.isnan(minute)):
        return None
    width = MINUTE_BUCKET.get(sport, 15.0)
    reg = REGULATION.get(sport, 90.0)
    if minute >= reg:
        return f"{int(reg)}+"
    lo = int(max(minute, 0) // width * width)
    hi = int(min(lo + width, reg))
    return f"{lo}-{hi}"


def minute_from_fraction(sport: str, frac: float | None) -> float | None:
    if frac is None or np.isnan(frac):
        return None
    return float(frac) * REGULATION.get(sport, 90.0)


def kst_day_start(now: int) -> int:
    d = dt.datetime.fromtimestamp(now, KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())
