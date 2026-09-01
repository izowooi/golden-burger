#!/usr/bin/env python3
"""Replay Golden Peach TP/SL pairs on direct six-token CLOB evidence.

This is a read-only, displayed-book counterfactual.  It uses the first durable
entry episode per event, exact-$5 entry VWAP, subsequent full bid depth, and the
catalogued sports taker-fee formula.  It is not actual fill or realized P&L.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Iterable, Sequence


TAKE_PROFIT_DELTAS = (0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
STOP_LOSS_DELTAS = (0.05, 0.07, 0.10, 0.15, 0.20)
NOTIONAL_USDC = 5.0
MAX_EXIT_SPREAD = 0.10
LATE_EXIT_MINUTE = 80.0
LATE_PROFIT_FRACTION = 0.50
STOP_CUTOFF_MINUTE = 80.0
BOOTSTRAP_SAMPLES = 10_000
EPSILON = 1e-9
ELIGIBLE_EPISODE_STATES = (
    "TRADE_CREATED",
    "BLOCKED_GUARD",
)
SIMULATION_GUARD_BUG_REASON = "open_buy_fill_or_fee_evidence_gap"


@dataclass(frozen=True)
class Entry:
    event_id: str
    condition_id: str
    token_id: str
    outcome: str
    outcome_side: str
    result_kind: str
    observed_at: str
    source_minute: float | None
    entry_vwap: float
    shares: float
    fee_rate: float
    execution_state: str
    execution_reason: str | None
    trade_id: int | None


@dataclass(frozen=True)
class BookObservation:
    observed_at: str
    source_minute: float | None
    best_bid: float
    best_ask: float | None
    spread: float | None
    bids: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ExitResult:
    reason: str
    observed_at: str
    source_minute: float | None
    sell_vwap: float
    gross_pnl_usdc: float
    fee_net_pnl_usdc: float
    fee_net_return_pct: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_price(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 < number < 1:
        return None
    return number


def _levels(raw: object, *, reverse: bool) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, list):
        return ()
    levels: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        price = _finite_price(item.get("price"))
        try:
            size = float(item.get("size"))
        except (TypeError, ValueError):
            continue
        if price is None or not math.isfinite(size) or size <= 0:
            continue
        levels.append((price, size))
    return tuple(sorted(levels, key=lambda item: item[0], reverse=reverse))


def _walk_sell(
    bids: Sequence[tuple[float, float]], shares: float
) -> tuple[float, float] | None:
    if not math.isfinite(shares) or shares <= 0:
        return None
    remaining = shares
    proceeds = 0.0
    for price, size in bids:
        consumed = min(remaining, size)
        proceeds += consumed * price
        remaining -= consumed
        if remaining <= EPSILON:
            break
    if remaining > 1e-7:
        return None
    return proceeds / shares, proceeds


def _execution_fee(
    *, shares: float, price: float, fee_rate: float
) -> float:
    """Match the frozen sports taker-fee exponent=1 contract."""
    if shares <= 0 or price <= 0 or fee_rate <= 0:
        return 0.0
    return shares * fee_rate * price * (1.0 - price)


def _decode_book(row: sqlite3.Row) -> BookObservation | None:
    try:
        payload = json.loads(row["book_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    bids = _levels(payload.get("bids"), reverse=True)
    if not bids:
        return None
    best_bid = bids[0][0]
    best_ask = _finite_price(row["best_ask"])
    spread = None if best_ask is None else best_ask - best_bid
    if spread is not None and (spread < -EPSILON or not math.isfinite(spread)):
        return None
    source_minute = row["source_elapsed_minutes"]
    try:
        source_minute = (
            float(source_minute) if source_minute is not None else None
        )
    except (TypeError, ValueError):
        source_minute = None
    if source_minute is not None and (
        not math.isfinite(source_minute) or source_minute < 0
    ):
        source_minute = None
    return BookObservation(
        observed_at=str(row["timestamp"]),
        source_minute=source_minute,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        bids=bids,
    )


def _read_entries(connection: sqlite3.Connection) -> tuple[list[Entry], dict]:
    rows = connection.execute(
        """
        WITH eligible AS (
            SELECT episode.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY episode.event_id
                       ORDER BY episode.observed_at, episode.id
                   ) AS event_rank
              FROM entry_episodes AS episode
             WHERE episode.execution_state IN ('TRADE_CREATED', 'BLOCKED_GUARD')
               AND (
                    episode.execution_state = 'TRADE_CREATED'
                    OR episode.execution_reason = :guard_reason
               )
        )
        SELECT episode.event_id, episode.condition_id, episode.token_id,
               episode.outcome, snapshot.outcome_side, snapshot.result_kind,
               episode.observed_at, episode.source_elapsed_minutes,
               COALESCE(trade.buy_confirmed_vwap, trade.buy_price,
                        episode.exact_vwap) AS entry_vwap,
               COALESCE(trade.buy_confirmed_size, trade.buy_shares,
                        :notional / episode.exact_vwap) AS shares,
               catalog.fees_enabled, catalog.fee_rate,
               catalog.fee_exponent, catalog.fee_taker_only,
               episode.execution_state, episode.execution_reason,
               episode.trade_id
          FROM eligible AS episode
          JOIN market_snapshots AS snapshot
            ON snapshot.id = episode.entry_snapshot_id
           AND snapshot.token_id = episode.token_id
          JOIN market_catalog AS catalog
            ON catalog.condition_id = episode.condition_id
          LEFT JOIN trades AS trade ON trade.id = episode.trade_id
         WHERE episode.event_rank = 1
         ORDER BY episode.observed_at, episode.event_id
        """,
        {"guard_reason": SIMULATION_GUARD_BUG_REASON, "notional": NOTIONAL_USDC},
    ).fetchall()
    entries: list[Entry] = []
    exclusions: Counter[str] = Counter()
    for row in rows:
        entry_vwap = _finite_price(row["entry_vwap"])
        try:
            shares = float(row["shares"])
        except (TypeError, ValueError):
            shares = math.nan
        if entry_vwap is None or not math.isfinite(shares) or shares <= 0:
            exclusions["INVALID_ENTRY_EXECUTION"] += 1
            continue
        fees_enabled = int(row["fees_enabled"] or 0)
        if fees_enabled:
            if row["fee_rate"] is None:
                exclusions["FEE_RATE_MISSING"] += 1
                continue
            try:
                fee_rate = float(row["fee_rate"])
                fee_exponent = int(row["fee_exponent"])
                taker_only = int(row["fee_taker_only"])
            except (TypeError, ValueError):
                exclusions["FEE_SCHEDULE_INVALID"] += 1
                continue
            if (
                not math.isfinite(fee_rate)
                or fee_rate < 0
                or fee_exponent != 1
                or taker_only != 1
            ):
                exclusions["FEE_SCHEDULE_OUTSIDE_FROZEN_CONTRACT"] += 1
                continue
        else:
            fee_rate = 0.0
        source_minute = row["source_elapsed_minutes"]
        try:
            source_minute = (
                float(source_minute) if source_minute is not None else None
            )
        except (TypeError, ValueError):
            source_minute = None
        entries.append(
            Entry(
                event_id=str(row["event_id"]),
                condition_id=str(row["condition_id"]),
                token_id=str(row["token_id"]),
                outcome=str(row["outcome"]),
                outcome_side=str(row["outcome_side"] or "").upper(),
                result_kind=str(row["result_kind"] or "").upper(),
                observed_at=str(row["observed_at"]),
                source_minute=source_minute,
                entry_vwap=entry_vwap,
                shares=shares,
                fee_rate=fee_rate,
                execution_state=str(row["execution_state"]),
                execution_reason=(
                    str(row["execution_reason"])
                    if row["execution_reason"] is not None
                    else None
                ),
                trade_id=(int(row["trade_id"]) if row["trade_id"] is not None else None),
            )
        )
    return entries, {"count": sum(exclusions.values()), "reasons": dict(exclusions)}


def _read_paths(
    connection: sqlite3.Connection, token_ids: Iterable[str]
) -> dict[str, list[BookObservation]]:
    normalized = sorted(set(token_ids))
    if not normalized:
        return {}
    placeholders = ",".join("?" for _ in normalized)
    rows = connection.execute(
        f"""
        SELECT token_id, timestamp, source_elapsed_minutes, best_ask, book_json
          FROM market_snapshots
         WHERE token_id IN ({placeholders})
           AND book_json IS NOT NULL
         ORDER BY token_id, timestamp, id
        """,
        normalized,
    ).fetchall()
    paths: dict[str, list[BookObservation]] = defaultdict(list)
    for row in rows:
        decoded = _decode_book(row)
        if decoded is not None:
            paths[str(row["token_id"])].append(decoded)
    return dict(paths)


def _evaluate(
    entry: Entry,
    path: Sequence[BookObservation],
    *,
    take_profit_delta: float,
    stop_loss_delta: float,
) -> ExitResult | None:
    normal_target = min(0.999, entry.entry_vwap + take_profit_delta)
    late_target = min(
        0.999,
        entry.entry_vwap + take_profit_delta * LATE_PROFIT_FRACTION,
    )
    stop_trigger = max(0.01, entry.entry_vwap - stop_loss_delta)
    buy_fee = _execution_fee(
        shares=entry.shares,
        price=entry.entry_vwap,
        fee_rate=entry.fee_rate,
    )
    for observation in path:
        if observation.observed_at <= entry.observed_at:
            continue
        if (
            observation.spread is None
            or observation.spread < -EPSILON
            or observation.spread > MAX_EXIT_SPREAD + EPSILON
        ):
            continue
        walked = _walk_sell(observation.bids, entry.shares)
        if walked is None:
            continue
        sell_vwap, proceeds = walked
        reason: str | None = None
        if sell_vwap + EPSILON >= normal_target:
            reason = "TAKE_PROFIT"
        elif (
            observation.source_minute is not None
            and observation.source_minute + EPSILON >= LATE_EXIT_MINUTE
            and sell_vwap + EPSILON >= late_target
        ):
            reason = "LATE_HALF_TARGET"
        elif (
            observation.source_minute is not None
            and observation.source_minute < STOP_CUTOFF_MINUTE - EPSILON
            and observation.best_bid <= stop_trigger + EPSILON
        ):
            reason = "STOP"
        if reason is None:
            continue
        sell_fee = _execution_fee(
            shares=entry.shares,
            price=sell_vwap,
            fee_rate=entry.fee_rate,
        )
        gross_pnl = proceeds - NOTIONAL_USDC
        fee_net_pnl = gross_pnl - buy_fee - sell_fee
        return ExitResult(
            reason=reason,
            observed_at=observation.observed_at,
            source_minute=observation.source_minute,
            sell_vwap=sell_vwap,
            gross_pnl_usdc=gross_pnl,
            fee_net_pnl_usdc=fee_net_pnl,
            fee_net_return_pct=fee_net_pnl / NOTIONAL_USDC * 100.0,
        )
    return None


def _bootstrap_mean_ci(values: Sequence[float]) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        return [values[0], values[0]]
    rng = random.Random(20260902)
    count = len(values)
    means = sorted(
        statistics.fmean(values[rng.randrange(count)] for _ in range(count))
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    return [
        means[int(0.025 * (BOOTSTRAP_SAMPLES - 1))],
        means[int(0.975 * (BOOTSTRAP_SAMPLES - 1))],
    ]


def _grid_row(
    entries: Sequence[Entry],
    paths: dict[str, list[BookObservation]],
    *,
    take_profit_delta: float,
    stop_loss_delta: float,
) -> dict:
    results: list[ExitResult] = []
    censored_events: list[str] = []
    for entry in entries:
        result = _evaluate(
            entry,
            paths.get(entry.token_id, ()),
            take_profit_delta=take_profit_delta,
            stop_loss_delta=stop_loss_delta,
        )
        if result is None:
            censored_events.append(entry.event_id)
        else:
            results.append(result)
    pnl = [result.fee_net_pnl_usdc for result in results]
    returns = [result.fee_net_return_pct for result in results]
    reasons = Counter(result.reason for result in results)
    evaluated = len(results)
    return {
        "take_profit_delta": take_profit_delta,
        "stop_loss_delta": stop_loss_delta,
        "events": len(entries),
        "evaluated_events": evaluated,
        "coverage_pct": evaluated / len(entries) * 100.0 if entries else None,
        "censored_event_ids": censored_events,
        "fee_net_total_pnl_usdc": sum(pnl),
        "fee_net_mean_pnl_usdc": statistics.fmean(pnl) if pnl else None,
        "fee_net_mean_return_pct": statistics.fmean(returns) if returns else None,
        "fee_net_mean_return_bootstrap_95ci_pct": _bootstrap_mean_ci(returns),
        "fee_net_median_return_pct": statistics.median(returns) if returns else None,
        "minimum_fee_net_return_pct": min(returns) if returns else None,
        "maximum_fee_net_return_pct": max(returns) if returns else None,
        "positive_events": sum(value > 0 for value in pnl),
        "non_positive_events": sum(value <= 0 for value in pnl),
        "exit_reasons": dict(sorted(reasons.items())),
    }


def analyze(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        entries, exclusions = _read_entries(connection)
        paths = _read_paths(connection, (entry.token_id for entry in entries))
        grid = [
            _grid_row(
                entries,
                paths,
                take_profit_delta=take_profit,
                stop_loss_delta=stop_loss,
            )
            for take_profit in TAKE_PROFIT_DELTAS
            for stop_loss in STOP_LOSS_DELTAS
        ]
        complete_grid = [row for row in grid if row["coverage_pct"] == 100.0]
        complete_grid.sort(
            key=lambda row: (
                -float(row["fee_net_mean_return_pct"]),
                row["take_profit_delta"],
                row["stop_loss_delta"],
            )
        )
        source_cutoff = connection.execute(
            "SELECT MAX(timestamp) FROM market_snapshots"
        ).fetchone()[0]
        raw_episode_rows = connection.execute(
            "SELECT execution_state, execution_reason, COUNT(*) AS count "
            "FROM entry_episodes GROUP BY execution_state, execution_reason"
        ).fetchall()
        raw_episode_counts = [dict(row) for row in raw_episode_rows]
    finally:
        connection.close()
    current_arms = [
        row
        for row in grid
        if row["stop_loss_delta"] == 0.10
        and row["take_profit_delta"] in (0.03, 0.05)
    ]
    return {
        "schema": "golden-peach-direct-book-grid-v1",
        "interpretation": "DISPLAYED_BOOK_COUNTERFACTUAL_NOT_ACTUAL_FILL_OR_REALIZED_PNL",
        "database": {
            "path": path.resolve().as_posix(),
            "sha256": _sha256(path),
            "quick_check": quick_check,
            "source_cutoff": source_cutoff,
        },
        "contract": {
            "notional_usdc": NOTIONAL_USDC,
            "take_profit_deltas": list(TAKE_PROFIT_DELTAS),
            "stop_loss_deltas": list(STOP_LOSS_DELTAS),
            "max_exit_spread": MAX_EXIT_SPREAD,
            "late_exit_minute": LATE_EXIT_MINUTE,
            "late_profit_fraction": LATE_PROFIT_FRACTION,
            "stop_cutoff_minute": STOP_CUTOFF_MINUTE,
            "fee_formula": "shares * fee_rate * price * (1-price)",
            "event_selection": (
                "first durable TRADE_CREATED or simulation-only "
                "open_buy_fill_or_fee_evidence_gap episode per event"
            ),
        },
        "limitations": [
            "displayed full-depth books do not guarantee actual FOK fills",
            "the separately refreshed execution book is not archived, so replay timestamps and VWAP can differ from the simulation trade row",
            "the parameter grid is exploratory and multiple-tested",
            "paths without a displayed TP/SL exit are censored; no payout is guessed",
            "all selected entries in this cohort are direct NO, so YES is untested",
            "source collection predates the simulation-only evidence-gap fix",
        ],
        "episodes": {
            "raw_counts": raw_episode_counts,
            "selected_unique_events": len(entries),
            "selected_execution_states": dict(
                sorted(Counter(entry.execution_state for entry in entries).items())
            ),
            "selected_sides": dict(
                sorted(Counter(entry.outcome_side for entry in entries).items())
            ),
            "selected_result_kinds": dict(
                sorted(Counter(entry.result_kind for entry in entries).items())
            ),
            "exclusions": exclusions,
            "entries": [asdict(entry) for entry in entries],
        },
        "current_arms": current_arms,
        "complete_grid_ranked": complete_grid,
        "grid": grid,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"missing database: {args.db}")
    report = analyze(args.db)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
