"""Research feature layer: one row per token-minute, exported to parquet.

Columns follow docs/thesis/sport-prediction-improvement-plan.md §16 where the data exists.
Not derivable from 1-minute storage and exported as null: probability_change_10s/30s,
holder_concentration. Output: <research_dir>/token_minutes/date=YYYY-MM-DD.parquet (UTC day).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from polylab.analysis import _common as C

FEATURES = ["sport", "league", "event_id", "game_id", "market_id", "condition_id", "market_type", "outcome",
            "token_id", "timestamp", "ts", "game_minute", "period", "phase", "home_score", "away_score",
            "score_diff", "current_probability", "price_source", "probability_change_10s",
            "probability_change_30s", "probability_change_1m", "probability_change_5m",
            "probability_change_10m", "volume_1m", "trade_count_1m", "buy_volume", "sell_volume",
            "net_order_flow", "bid", "ask", "spread", "midpoint", "orderbook_imbalance", "bid_depth_usd",
            "ask_depth_usd", "liquidity", "open_interest", "holder_concentration", "final_result"]


def _token_meta(conn: sqlite3.Connection, since: int, until: int, all_leagues: bool = False) -> pd.DataFrame:
    lg_sql, lg_params = C.league_filter_sql("g", all_leagues)
    return C.read_sql(conn, """
        SELECT t.token_id, t.outcome_index, t.outcome_label AS outcome, t.side,
               m.condition_id, m.market_id, m.event_id, m.market_type, m.resolved_outcome_index,
               g.game_key AS game_id, g.sport, g.league, g.start_time, g.ended_at
        FROM tokens t JOIN markets m ON m.condition_id = t.condition_id
        JOIN games g ON g.game_key = m.game_key
        WHERE g.start_time < ? AND (g.ended_at IS NULL OR g.ended_at >= ?)
    """ + lg_sql, (until, since - 86400, *lg_params))


def _trades_per_minute(conn: sqlite3.Connection, tokens: list[str], since: int, until: int) -> pd.DataFrame:
    frames = []
    for group in C._chunks(tokens):
        frames.append(C.read_sql(conn, f"""
            SELECT token_id, (ts / 60) * 60 AS ts, COUNT(*) AS trade_count_1m,
                   SUM(COALESCE(usd, price * size)) AS volume_1m,
                   SUM(CASE WHEN UPPER(side) = 'BUY' THEN COALESCE(usd, price * size) ELSE 0 END) AS buy_volume,
                   SUM(CASE WHEN UPPER(side) = 'SELL' THEN COALESCE(usd, price * size) ELSE 0 END) AS sell_volume
            FROM public_trades WHERE ts >= ? AND ts < ? AND token_id IN ({','.join('?' * len(group))})
            GROUP BY token_id, (ts / 60) * 60
        """, [since, until] + group))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(columns=["token_id", "ts", "trade_count_1m", "volume_1m", "buy_volume", "sell_volume"])
    return df


def _books_per_minute(paths, tokens: list[str], since: int, until: int) -> pd.DataFrame:
    cols = ["token_id", "ts", "bid", "ask", "spread", "midpoint", "orderbook_imbalance",
            "bid_depth_usd", "ask_depth_usd"]
    frames = []
    months = sorted({dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m")
                     for t in range(since, until + 1, 86400)} |
                    {dt.datetime.fromtimestamp(until - 1, dt.timezone.utc).strftime("%Y-%m")})
    for ym in months:
        conn = C.open_ro(paths.books_db(ym))
        if conn is None:
            continue
        try:
            for group in C._chunks(tokens):
                frames.append(C.read_sql(conn, f"""
                    SELECT token_id, ts, best_bid AS bid, best_ask AS ask, spread, mid AS midpoint,
                           imb_l5 AS orderbook_imbalance, bid_depth_usd, ask_depth_usd
                    FROM book_snapshots WHERE ts >= ? AND ts < ? AND token_id IN ({','.join('?' * len(group))})
                """, [since, until] + group))
        finally:
            conn.close()
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=cols)
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = (df["ts"].astype(int) // 60) * 60
    return df.sort_values(["token_id", "ts"]).drop_duplicates(["token_id", "ts"], keep="last")[cols]


def _metrics(conn: sqlite3.Connection, conditions: list[str], until: int) -> pd.DataFrame:
    frames = []
    for group in C._chunks(conditions):
        frames.append(C.read_sql(conn, f"""
            SELECT condition_id, ts, liquidity, open_interest FROM market_metrics
            WHERE ts < ? AND condition_id IN ({','.join('?' * len(group))})
        """, [until] + group))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["condition_id", "ts", "liquidity", "open_interest"])


def build(conn: sqlite3.Connection, paths, since: int, until: int, all_leagues: bool = False) -> pd.DataFrame:
    meta = _token_meta(conn, since, until, all_leagues)
    if meta.empty:
        return pd.DataFrame(columns=FEATURES)
    tokens = meta["token_id"].tolist()
    prices = C.canonical_prices(conn, tokens, since - 600, until)
    if prices.empty:
        return pd.DataFrame(columns=FEATURES)
    prices = prices.rename(columns={"price": "current_probability", "source": "price_source"})
    for k in (1, 5, 10):
        lag = prices[["token_id", "ts", "current_probability"]].copy()
        lag["ts"] = lag["ts"] + k * 60
        prices = prices.merge(lag.rename(columns={"current_probability": f"_lag{k}"}),
                              on=["token_id", "ts"], how="left")
        prices[f"probability_change_{k}m"] = prices["current_probability"] - prices[f"_lag{k}"]
    df = prices[prices["ts"] >= since].merge(meta, on="token_id")
    df = C.attach_state(df.rename(columns={"game_id": "game_key"}),
                        C.game_states(conn, df["game_id"].unique().tolist())).rename(columns={"game_key": "game_id"})
    df["phase"] = C.phase_series(df["sport"], df["ts"], df["start_time"], df["game_minute"], df["ended_at"])
    diff = df["home_score"] - df["away_score"]
    df["score_diff"] = np.where(df["side"] == "away", -diff, diff)
    df = df.merge(_trades_per_minute(conn, tokens, since, until), on=["token_id", "ts"], how="left")
    df["net_order_flow"] = df["buy_volume"] - df["sell_volume"]
    df = df.merge(_books_per_minute(paths, tokens, since, until), on=["token_id", "ts"], how="left")
    metrics = _metrics(conn, meta["condition_id"].unique().tolist(), until)
    if not metrics.empty:
        df["_o"] = np.arange(len(df))
        df["ts"] = df["ts"].astype("int64")
        df = pd.merge_asof(df.sort_values("ts"), metrics.sort_values("ts").astype({"ts": "int64"}),
                           on="ts", by="condition_id", direction="backward").sort_values("_o").drop(columns="_o")
    else:
        df["liquidity"] = np.nan
        df["open_interest"] = np.nan
    df["holder_concentration"] = np.nan
    df["probability_change_10s"] = np.nan
    df["probability_change_30s"] = np.nan
    df["final_result"] = np.where(df["resolved_outcome_index"].isna(), np.nan,
                                  (df["outcome_index"] == df["resolved_outcome_index"]).astype(float))
    df["timestamp"] = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
    for c in FEATURES:
        if c not in df.columns:
            df[c] = np.nan
    return df[FEATURES].reset_index(drop=True)


def export(conn: sqlite3.Connection, paths, since: int, until: int, all_leagues: bool = False) -> list[Path]:
    """Write one parquet per UTC day in [since, until). Returns written files."""
    out_dir = Path(paths.research_dir) / "token_minutes"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    day = since - since % 86400
    while day < until:
        lo, hi = max(day, since), min(day + 86400, until)
        df = build(conn, paths, lo, hi, all_leagues)
        if not df.empty:
            name = dt.datetime.fromtimestamp(day, dt.timezone.utc).strftime("%Y-%m-%d")
            path = out_dir / f"date={name}.parquet"
            df["period"] = df["period"].astype("string")
            df.to_parquet(path, index=False)
            written.append(path)
        day += 86400
    return written
