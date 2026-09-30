"""Realised strategy performance from the per-variant ledgers.

Only fully settled positions count: status closed/resolved with a realized_pnl, settled by a
confirmed sell or a confirmed resolution, and (live) backed by at least one CONFIRMED BUY fill.
Paper results are aggregated separately and never mixed with live money.
Quarantined / pending / unsettled positions are excluded, not zeroed.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from polylab.analysis import _common as C

LIVE_SETTLEMENTS = ("confirmed_sell", "resolution")
OPEN_STATUSES = ("pending", "open", "closing")
ENTRY_MINUTE_WIDTH = 15

POSITION_COLS = ["position_id", "mode", "param_version", "stake_usdc", "sport", "league", "game_key",
                 "condition_id", "token_id", "outcome_label", "opened_at", "game_minute_at_entry",
                 "entry_price", "shares", "cost_usdc", "status", "closed_at", "exit_reason", "exit_price",
                 "proceeds_usdc", "realized_pnl", "settlement"]


def load_positions(conn: sqlite3.Connection) -> pd.DataFrame:
    df = C.read_sql(conn, f"""
        SELECT {', '.join('p.' + c for c in POSITION_COLS)},
               EXISTS(SELECT 1 FROM fills f JOIN orders o ON o.intent_id = f.intent_id
                      WHERE o.position_id = p.position_id AND f.side = 'BUY'
                        AND f.status = 'CONFIRMED') AS confirmed_buy
        FROM positions p
    """)
    return df


def load_variant_positions(db_path: Path) -> pd.DataFrame:
    conn = C.open_ro(db_path)
    if conn is None:
        return pd.DataFrame(columns=POSITION_COLS + ["confirmed_buy"])
    try:
        return load_positions(conn)
    except sqlite3.OperationalError:
        return pd.DataFrame(columns=POSITION_COLS + ["confirmed_buy"])
    finally:
        conn.close()


def settled(df: pd.DataFrame, mode: str = "live") -> pd.DataFrame:
    if df.empty:
        return df
    base = df["status"].isin(["closed", "resolved"]) & df["realized_pnl"].notna() & df["closed_at"].notna()
    if mode == "live":
        mask = base & (df["mode"] == "live") & df["settlement"].isin(LIVE_SETTLEMENTS) \
            & (df["confirmed_buy"].astype(int) == 1)
    else:
        mask = base & (df["mode"] == "paper")
    return df[mask].sort_values("closed_at").reset_index(drop=True)


def open_positions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df["status"].isin(OPEN_STATUSES)].sort_values("opened_at").reset_index(drop=True)


def max_drawdown(pnls: pd.Series | list[float]) -> float:
    cum = np.cumsum(np.asarray(list(pnls), dtype=float))
    if cum.size == 0:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float(np.max(peak - cum))


def _r(x: float | None, nd: int = 4) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


def summary(s: pd.DataFrame, now: int) -> dict:
    """PnL windows and win/loss counts over settled positions (today = KST calendar day)."""
    if s.empty:
        return {"pnl": {"today": 0.0, "d7": 0.0, "d30": 0.0, "all": 0.0},
                "trades": {"all": 0, "wins": 0, "losses": 0}, "win_rate": None, "roi": None,
                "cost": 0.0, "last_trade_at": None}
    pnl = s["realized_pnl"].astype(float)
    closed = s["closed_at"].astype(int)
    day0 = C.kst_day_start(now)
    cost = float(s["cost_usdc"].astype(float).sum())
    wins = int((pnl > 0).sum())
    return {"pnl": {"today": _r(pnl[closed >= day0].sum()), "d7": _r(pnl[closed >= now - 7 * 86400].sum()),
                    "d30": _r(pnl[closed >= now - 30 * 86400].sum()), "all": _r(pnl.sum())},
            "trades": {"all": int(len(s)), "wins": wins, "losses": int((pnl <= 0).sum())},
            "win_rate": _r(wins / len(s)), "roi": _r(pnl.sum() / cost) if cost > 0 else None,
            "cost": _r(cost, 2), "last_trade_at": C.iso(int(closed.max()))}


def entry_minute_bucket(minute: float | None) -> str | None:
    if minute is None or pd.isna(minute):
        return None
    lo = int(max(minute, 0) // ENTRY_MINUTE_WIDTH * ENTRY_MINUTE_WIDTH)
    return f"{lo}-{lo + ENTRY_MINUTE_WIDTH}"


def breakdown(s: pd.DataFrame) -> dict:
    out = {"by_sport": [], "by_entry_minute": [], "by_stake": [], "by_day": []}
    if s.empty:
        return out
    s = s.copy()
    s["entry_bucket"] = s["game_minute_at_entry"].map(entry_minute_bucket)
    s["stake_key"] = s["stake_usdc"].map(lambda v: f"{float(v):g}")
    s["day"] = pd.to_datetime(s["closed_at"].astype(int), unit="s", utc=True).dt.tz_convert(C.KST) \
        .dt.strftime("%Y-%m-%d")
    for field, col in (("by_sport", "sport"), ("by_entry_minute", "entry_bucket"),
                       ("by_stake", "stake_key"), ("by_day", "day")):
        for key, g in s.groupby(col, dropna=True):
            pnl = g["realized_pnl"].astype(float)
            cost = float(g["cost_usdc"].astype(float).sum())
            out[field].append({"key": key, "n": int(len(g)), "pnl": _r(pnl.sum()),
                               "win_rate": _r(float((pnl > 0).mean())),
                               "roi": _r(pnl.sum() / cost) if cost > 0 else None})
    out["by_entry_minute"].sort(key=lambda r: int(r["key"].split("-")[0]))
    out["by_stake"].sort(key=lambda r: float(r["key"]))
    return out


def equity_curve(s: pd.DataFrame) -> list[dict]:
    if s.empty:
        return []
    cum = s["realized_pnl"].astype(float).cumsum()
    return [{"at": C.iso(int(t)), "cum_pnl": round(float(c), 4)} for t, c in zip(s["closed_at"], cum)]


def tier_stats(rows: pd.DataFrame) -> list[dict]:
    """Stake-tier stability: per tier across variants. rows need variant_id, stake_usdc, realized_pnl, cost_usdc.

    sharpe_like = mean(per-trade ROI) / std(per-trade ROI), unannualised; max_drawdown on the
    tier's cumulative realised PnL ordered by close time.
    """
    out = []
    if rows.empty:
        return out
    rows = rows.sort_values("closed_at")
    for tier, g in rows.groupby(rows["stake_usdc"].astype(float)):
        pnl = g["realized_pnl"].astype(float)
        cost = g["cost_usdc"].astype(float)
        per_roi = (pnl / cost.where(cost > 0)).dropna()
        std_roi = float(per_roi.std(ddof=1)) if len(per_roi) > 1 else None
        out.append({"tier_usdc": float(tier), "variants": int(g["variant_id"].nunique()), "trades": int(len(g)),
                    "pnl": _r(pnl.sum()), "roi": _r(pnl.sum() / cost.sum()) if cost.sum() > 0 else None,
                    "pnl_std": _r(float(pnl.std(ddof=1))) if len(g) > 1 else None,
                    "max_drawdown": _r(max_drawdown(pnl)),
                    "sharpe_like": _r(float(per_roi.mean()) / std_roi) if std_roi else None})
    return out


def roi_ci_lo(s: pd.DataFrame, level: float = 0.80, n_boot: int = 2000, seed: int = 7) -> float | None:
    """Bootstrap lower bound of mean per-trade ROI (deterministic seed)."""
    if s.empty or len(s) < 2:
        return None
    roi = (s["realized_pnl"].astype(float) / s["cost_usdc"].astype(float)).replace([np.inf, -np.inf], np.nan)
    roi = roi.dropna().to_numpy()
    if roi.size < 2:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(roi, size=(n_boot, roi.size), replace=True).mean(axis=1)
    return _r(float(np.quantile(means, 1 - level)))


def collect(paths, variant_ids: list[str]) -> dict[str, pd.DataFrame]:
    return {vid: load_variant_positions(paths.strategy_db(vid)) for vid in variant_ids}


def all_settled(positions: dict[str, pd.DataFrame], mode: str = "live") -> pd.DataFrame:
    frames = [settled(df, mode).assign(variant_id=vid) for vid, df in positions.items() if not df.empty]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=POSITION_COLS + ["confirmed_buy", "variant_id"])
    return pd.concat(frames, ignore_index=True)


def excluded_counts(df: pd.DataFrame) -> dict:
    """Why positions were left out of realised PnL (transparency for reports)."""
    if df.empty:
        return {}
    closed = df["status"].isin(["closed", "resolved"])
    live = df["mode"] == "live"
    return {"quarantined": int((df["status"] == "quarantined").sum()),
            "closed_without_pnl": int((closed & df["realized_pnl"].isna()).sum()),
            "live_without_confirmed_buy": int((closed & live & (df["confirmed_buy"].astype(int) == 0)).sum())}


def run(paths, variant_ids: list[str], now: int) -> dict:
    positions = collect(paths, variant_ids)
    out = {"variants": {}, "stake_tiers": tier_stats(all_settled(positions, "live")),
           "stake_tiers_paper": tier_stats(all_settled(positions, "paper"))}
    for vid, df in positions.items():
        live, paper = settled(df, "live"), settled(df, "paper")
        out["variants"][vid] = {"live": summary(live, now), "paper": summary(paper, now),
                                "breakdown": breakdown(live if not live.empty else paper),
                                "open_positions": int(len(open_positions(df))),
                                "excluded": excluded_counts(df)}
    return out


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
