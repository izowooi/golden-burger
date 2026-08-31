#!/usr/bin/env python3
"""Replay Golden Plum on direct six-book snapshot evidence.

The output is exploratory displayed-book evidence, not actual fill or realized
P&L.  Every grid cell reuses the same event as a paired unit.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable, Sequence


DEFAULT_ENTRIES = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
DEFAULT_TARGETS = (0.85, 0.90, 0.95)
DEFAULT_STOPS = (0.05, 0.10, 0.15, 0.20)
DEFAULT_OBSERVATIONS = (2, 3, 5)
DEFAULT_MIN_MOVES = (0.01, 0.02, 0.03, 0.05)
NOTIONAL_USDC = 5.0
ENTRY_OVERSHOOT = 0.03
MIN_SOURCE_MINUTE = 5.0
MAX_SOURCE_MINUTE = 75.0
FORCE_EXIT_MINUTE = 80.0
MAX_ENTRY_SPREAD = 0.05
MIN_LEADER_MARGIN = 0.005
TREND_OBSERVATIONS = 3
TREND_MIN_MOVE = 0.02
TREND_MAX_PULLBACK = 0.01
TREND_MAX_GAP_SECONDS = 90.0
EXPECTED_SIX = frozenset(
    (result, side)
    for result in ("HOME", "DRAW", "AWAY")
    for side in ("YES", "NO")
)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    event_id: str
    condition_id: str
    token_id: str
    run_id: str
    result_kind: str
    outcome_side: str
    source_minute: float
    observed_at: datetime
    probability: float
    midpoint: float
    spread: float
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ReplayTrade:
    event_id: str
    result_kind: str
    outcome_side: str
    entry_price: float
    entry_source_minute: float
    exit_price: float
    exit_source_minute: float
    exit_reason: str
    pnl_usdc: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: object) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def _levels(raw: object, side: str) -> tuple[tuple[float, float], ...]:
    try:
        decoded = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    values: list[tuple[float, float]] = []
    for item in decoded.get(side, []):
        if not isinstance(item, dict):
            continue
        try:
            price = float(item["price"])
            size = float(item["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(price) and math.isfinite(size) and 0 < price < 1 and size > 0:
            values.append((price, size))
    values.sort(key=lambda item: -item[0] if side == "bids" else item[0])
    return tuple(values)


def walk_buy(
    asks: Sequence[tuple[float, float]],
    notional: float = NOTIONAL_USDC,
) -> tuple[float, float] | None:
    remaining = notional
    shares = 0.0
    for price, size in asks:
        consumed = min(remaining, price * size)
        shares += consumed / price
        remaining -= consumed
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or shares <= 0:
        return None
    return notional / shares, shares


def walk_sell(
    bids: Sequence[tuple[float, float]],
    shares: float,
) -> float | None:
    remaining = shares
    proceeds = 0.0
    for price, size in bids:
        consumed = min(remaining, size)
        proceeds += consumed * price
        remaining -= consumed
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or shares <= 0:
        return None
    return proceeds / shares


def trend_confirmed(
    history: Sequence[Snapshot],
    *,
    threshold: float,
    current_snapshot_id: int,
    observations: int = TREND_OBSERVATIONS,
    min_move: float = TREND_MIN_MOVE,
) -> bool:
    if len(history) != observations or history[-1].snapshot_id != current_snapshot_id:
        return False
    gaps = [
        (history[index].observed_at - history[index - 1].observed_at).total_seconds()
        for index in range(1, observations)
    ]
    if any(gap <= 0 or gap > TREND_MAX_GAP_SECONDS + 1e-9 for gap in gaps):
        return False
    if any(
        history[index].source_minute + 1e-9
        < history[index - 1].source_minute
        for index in range(1, observations)
    ):
        return False
    prices = [snapshot.probability for snapshot in history]
    deltas = [prices[index] - prices[index - 1] for index in range(1, observations)]
    if any(delta < -TREND_MAX_PULLBACK - 1e-9 for delta in deltas):
        return False
    if prices[-1] - prices[0] + 1e-9 < min_move:
        return False
    return bool(
        prices[-2] < threshold - 1e-9
        and threshold - 1e-9 <= prices[-1] <= threshold + ENTRY_OVERSHOOT + 1e-9
    )


def load_snapshots(connection: sqlite3.Connection) -> list[Snapshot]:
    rows = connection.execute(
        """
        SELECT id,event_id,condition_id,token_id,run_id,result_kind,outcome_side,
               source_elapsed_minutes,timestamp,probability,midpoint,spread,book_json
          FROM market_snapshots
         WHERE event_id IS NOT NULL
           AND token_id IS NOT NULL
           AND run_id IS NOT NULL
           AND source_elapsed_minutes IS NOT NULL
           AND midpoint IS NOT NULL
           AND spread IS NOT NULL
           AND book_json IS NOT NULL
         ORDER BY timestamp,id
        """
    ).fetchall()
    snapshots: list[Snapshot] = []
    for row in rows:
        bids = _levels(row[12], "bids")
        asks = _levels(row[12], "asks")
        buy = walk_buy(asks)
        if not bids or buy is None:
            continue
        try:
            snapshot = Snapshot(
                snapshot_id=int(row[0]),
                event_id=str(row[1]),
                condition_id=str(row[2]),
                token_id=str(row[3]),
                run_id=str(row[4]),
                result_kind=str(row[5]).upper(),
                outcome_side=str(row[6]).upper(),
                source_minute=float(row[7]),
                observed_at=_timestamp(row[8]),
                probability=float(buy[0]),
                midpoint=float(row[10]),
                spread=float(row[11]),
                bids=bids,
                asks=asks,
            )
        except (TypeError, ValueError):
            continue
        if (
            (snapshot.result_kind, snapshot.outcome_side) in EXPECTED_SIX
            and math.isfinite(snapshot.source_minute)
            and 0 <= snapshot.source_minute <= 120
            and 0 < snapshot.probability < 1
        ):
            snapshots.append(snapshot)
    return snapshots


def replay_cell(
    snapshots: Sequence[Snapshot],
    *,
    entry_threshold: float,
    target_price: float,
    stop_delta: float,
    observations: int = TREND_OBSERVATIONS,
    min_move: float = TREND_MIN_MOVE,
) -> list[ReplayTrade]:
    by_event_run: dict[tuple[str, str], list[Snapshot]] = defaultdict(list)
    by_event_token: dict[tuple[str, str], list[Snapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_event_run[(snapshot.event_id, snapshot.run_id)].append(snapshot)
        by_event_token[(snapshot.event_id, snapshot.token_id)].append(snapshot)
    for values in by_event_token.values():
        values.sort(key=lambda item: (item.observed_at, item.snapshot_id))

    runs_by_event: dict[str, list[list[Snapshot]]] = defaultdict(list)
    for (event_id, _run_id), values in by_event_run.items():
        values.sort(key=lambda item: item.snapshot_id)
        runs_by_event[event_id].append(values)
    for groups in runs_by_event.values():
        groups.sort(key=lambda group: min(item.observed_at for item in group))

    trades: list[ReplayTrade] = []
    for event_id, run_groups in sorted(runs_by_event.items()):
        history: dict[str, list[Snapshot]] = defaultdict(list)
        entry: Snapshot | None = None
        shares: float | None = None
        for group in run_groups:
            for snapshot in group:
                history[snapshot.token_id].append(snapshot)
                history[snapshot.token_id] = history[snapshot.token_id][-observations:]
            identities = {(item.result_kind, item.outcome_side) for item in group}
            if identities != EXPECTED_SIX or len({item.token_id for item in group}) != 6:
                continue
            source_minutes = {item.source_minute for item in group}
            if len(source_minutes) != 1:
                continue
            source_minute = next(iter(source_minutes))
            if not MIN_SOURCE_MINUTE <= source_minute <= MAX_SOURCE_MINUTE:
                continue
            ranked = sorted(group, key=lambda item: (-item.midpoint, item.token_id))
            if ranked[0].midpoint - ranked[1].midpoint + 1e-9 < MIN_LEADER_MARGIN:
                continue
            candidate = ranked[0]
            if candidate.spread > MAX_ENTRY_SPREAD + 1e-9:
                continue
            if not trend_confirmed(
                history[candidate.token_id],
                threshold=entry_threshold,
                current_snapshot_id=candidate.snapshot_id,
                observations=observations,
                min_move=min_move,
            ):
                continue
            buy = walk_buy(candidate.asks)
            if buy is None or buy[0] >= target_price - 1e-9:
                continue
            entry = candidate
            shares = buy[1]
            break
        if entry is None or shares is None:
            continue

        stop_price = entry.probability - stop_delta
        for current in by_event_token[(event_id, entry.token_id)]:
            if current.observed_at <= entry.observed_at:
                continue
            exit_vwap = walk_sell(current.bids, shares)
            if exit_vwap is None:
                continue
            reason = None
            if exit_vwap + 1e-9 >= target_price:
                reason = "take_profit"
            elif exit_vwap <= stop_price + 1e-9:
                reason = "stop"
            elif current.source_minute + 1e-9 >= FORCE_EXIT_MINUTE:
                reason = "minute_80_exit"
            if reason is None:
                continue
            trades.append(
                ReplayTrade(
                    event_id=event_id,
                    result_kind=entry.result_kind,
                    outcome_side=entry.outcome_side,
                    entry_price=entry.probability,
                    entry_source_minute=entry.source_minute,
                    exit_price=exit_vwap,
                    exit_source_minute=current.source_minute,
                    exit_reason=reason,
                    pnl_usdc=shares * exit_vwap - NOTIONAL_USDC,
                )
            )
            break
    return trades


def database_report(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        snapshots = load_snapshots(connection)
        cutoff = connection.execute(
            "SELECT MAX(timestamp) FROM market_snapshots"
        ).fetchone()[0]
    finally:
        connection.close()
    grid = []
    for entry in DEFAULT_ENTRIES:
        for target in DEFAULT_TARGETS:
            if target <= entry + ENTRY_OVERSHOOT:
                continue
            for stop in DEFAULT_STOPS:
                for observations in DEFAULT_OBSERVATIONS:
                    for min_move in DEFAULT_MIN_MOVES:
                        trades = replay_cell(
                            snapshots,
                            entry_threshold=entry,
                            target_price=target,
                            stop_delta=stop,
                            observations=observations,
                            min_move=min_move,
                        )
                        pnl = sum(item.pnl_usdc for item in trades)
                        grid.append(
                            {
                                "entry_threshold": entry,
                                "target_price": target,
                                "stop_delta": stop,
                                "trend_observations": observations,
                                "trend_min_cumulative_move": min_move,
                                "trades": len(trades),
                                "positive": sum(
                                    item.pnl_usdc > 0 for item in trades
                                ),
                                "pnl_usdc": pnl,
                                "mean_pnl_usdc": (
                                    pnl / len(trades) if trades else None
                                ),
                                "exit_reasons": {
                                    reason: sum(
                                        item.exit_reason == reason
                                        for item in trades
                                    )
                                    for reason in (
                                        "take_profit",
                                        "stop",
                                        "minute_80_exit",
                                    )
                                },
                            }
                        )
    primary = {}
    for target in (0.90, 0.95):
        trades = replay_cell(
            snapshots,
            entry_threshold=0.75,
            target_price=target,
            stop_delta=0.15,
        )
        primary[f"0.75_to_{target:.2f}_stop_0.15"] = {
            "trades": [asdict(item) for item in trades],
            "pnl_usdc": sum(item.pnl_usdc for item in trades),
        }
    return {
        "database": str(path.resolve()),
        "sha256": _sha256(path),
        "quick_check": quick_check,
        "source_cutoff": cutoff,
        "snapshot_rows": len(snapshots),
        "events": len({item.event_id for item in snapshots}),
        "evidence_semantics": "displayed full-depth counterfactual; fees excluded; not actual fills",
        "primary": primary,
        "grid": grid,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = {"reports": [database_report(path) for path in args.db]}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
