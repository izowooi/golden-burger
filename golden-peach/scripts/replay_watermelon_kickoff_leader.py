#!/usr/bin/env python3
"""Replay Golden Peach on immutable Golden Watermelon SQLite evidence.

The legacy Watermelon collector persisted only the direct YES book for each
HOME/DRAW/AWAY proposition.  This exploratory replay therefore constructs a
synthetic NO book from the opposite YES side.  It labels that limitation in
every result and must never be interpreted as actual fill evidence.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Sequence


SOCCER_LEAGUES = frozenset({"epl", "bun", "fl1", "lal", "mls", "sea", "ucl", "uel"})
RESULT_KINDS = frozenset({"HOME", "DRAW", "AWAY"})
TAKE_PROFITS = (0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
STOP_LOSSES = (0.05, 0.07, 0.10, 0.15, 0.20)
ENTRY_MIN = 0.60
ENTRY_MAX = 0.94
MAX_SOURCE_MINUTE = 10.0
MIN_LEADER_MARGIN = 0.005
MAX_SPREAD = 0.05
LATE_EXIT_MINUTE = 80.0
LATE_PROFIT_FRACTION = 0.50
NOTIONAL_USDC = 5.0


@dataclass(frozen=True)
class Book:
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def midpoint(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


@dataclass(frozen=True)
class Observation:
    run_id: str
    event_id: str
    event_title: str
    league_code: str
    condition_id: str
    result_kind: str
    yes_token_id: str
    observed_at: str
    source_minute: float
    yes_book: Book


@dataclass(frozen=True)
class Entry:
    database: str
    event_id: str
    event_title: str
    league_code: str
    condition_id: str
    result_kind: str
    outcome_side: str
    token_id: str
    observed_at: str
    source_minute: float
    entry_vwap: float
    shares: float
    leader_midpoint: float
    leader_margin: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_source_minute(normalized: dict) -> float | None:
    clock = normalized.get("sports_clock")
    if not isinstance(clock, dict):
        return None
    period = str(clock.get("period") or "").strip().casefold()
    raw = clock.get("websocket_elapsed_raw")
    if raw in (None, ""):
        raw = clock.get("elapsed_raw")
    if raw in (None, ""):
        raw = clock.get("gamma_elapsed_raw")
    if period in {"ht", "half time", "halftime"}:
        return 45.0
    if period in {"ft", "full time", "fulltime"}:
        return 90.0
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().casefold().rstrip("'")
    if "+" in text:
        parts = text.split("+", 1)
        try:
            minute = float(parts[0]) + float(parts[1])
        except ValueError:
            return None
    elif ":" in text:
        parts = text.split(":")
        try:
            values = [float(value) for value in parts]
        except ValueError:
            return None
        if len(values) == 2:
            minute = values[0] + values[1] / 60.0
        elif len(values) == 3:
            minute = values[0] * 60.0 + values[1] + values[2] / 60.0
        else:
            return None
    else:
        try:
            minute = float(text)
        except ValueError:
            return None
    if not math.isfinite(minute) or minute < 0:
        return None
    if period in {"2h", "second half", "second_half", "2", "second"} and minute < 45:
        return 45.0 + minute
    if period not in {
        "1h", "first half", "first_half", "1", "first",
        "2h", "second half", "second_half", "2", "second",
    }:
        return None
    return minute


def _walk_buy(levels: Sequence[tuple[float, float]], notional: float) -> tuple[float, float] | None:
    remaining = notional
    shares = 0.0
    for price, size in levels:
        if not (0 < price < 1 and size > 0):
            continue
        level_cost = price * size
        consumed_cost = min(remaining, level_cost)
        shares += consumed_cost / price
        remaining -= consumed_cost
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or shares <= 0:
        return None
    return notional / shares, shares


def _walk_sell(levels: Sequence[tuple[float, float]], shares: float) -> float | None:
    remaining = shares
    proceeds = 0.0
    for price, size in levels:
        if not (0 < price < 1 and size > 0):
            continue
        consumed = min(remaining, size)
        proceeds += consumed * price
        remaining -= consumed
        if remaining <= 1e-9:
            break
    if remaining > 1e-9 or shares <= 0:
        return None
    return proceeds / shares


def _synthetic_no_book(yes_book: Book) -> Book:
    return Book(
        bids=tuple((1.0 - price, size) for price, size in yes_book.asks),
        asks=tuple((1.0 - price, size) for price, size in yes_book.bids),
    )


def _read_observations(connection: sqlite3.Connection) -> list[Observation]:
    rows = connection.execute(
        """
        SELECT eo.event_id, COALESCE(eo.event_title, ''), eo.league_code,
               mo.condition_id, mo.normalized_json, oo.run_id, oo.token_id,
               oo.observed_at, ob.snapshot_id
          FROM outcome_observations oo
          JOIN market_observations mo
            ON mo.observation_id = oo.market_observation_id
          JOIN event_observations eo
            ON eo.event_observation_id = mo.event_observation_id
          JOIN orderbook_snapshots ob
            ON ob.run_id = oo.run_id AND ob.token_id = oo.token_id
         WHERE eo.classification_status = 'ACCEPTED'
           AND mo.eligible = 1
           AND oo.outcome_index = 0
         ORDER BY oo.observed_at, eo.event_id, mo.condition_id
        """
    ).fetchall()
    level_rows = connection.execute(
        """
        SELECT snapshot_id, side, level_index, price, size
          FROM orderbook_levels
         ORDER BY snapshot_id, side, level_index
        """
    ).fetchall()
    levels: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: {"BID": [], "ASK": []}
    )
    for snapshot_id, side, _index, price, size in level_rows:
        if side in {"BID", "ASK"}:
            levels[str(snapshot_id)][str(side)].append((float(price), float(size)))

    observations: list[Observation] = []
    for (
        event_id, event_title, league_code, condition_id, normalized_json,
        run_id, token_id, observed_at, snapshot_id,
    ) in rows:
        if str(league_code) not in SOCCER_LEAGUES:
            continue
        try:
            normalized = json.loads(normalized_json)
        except (TypeError, json.JSONDecodeError):
            continue
        result_kind = str(normalized.get("result_kind") or "").upper()
        source_minute = _parse_source_minute(normalized)
        snapshot_levels = levels.get(str(snapshot_id))
        if result_kind not in RESULT_KINDS or source_minute is None or not snapshot_levels:
            continue
        bids = tuple(sorted(snapshot_levels["BID"], key=lambda item: -item[0]))
        asks = tuple(sorted(snapshot_levels["ASK"], key=lambda item: item[0]))
        observations.append(
            Observation(
                run_id=str(run_id),
                event_id=str(event_id),
                event_title=str(event_title),
                league_code=str(league_code),
                condition_id=str(condition_id),
                result_kind=result_kind,
                yes_token_id=str(token_id),
                observed_at=str(observed_at),
                source_minute=source_minute,
                yes_book=Book(bids=bids, asks=asks),
            )
        )
    return observations


def _select_entries(database: str, observations: Sequence[Observation]) -> list[Entry]:
    grouped: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.run_id, observation.event_id)].append(observation)
    selected_events: set[str] = set()
    entries: list[Entry] = []
    for (_run_id, event_id), group in sorted(
        grouped.items(), key=lambda item: min(row.observed_at for row in item[1])
    ):
        if event_id in selected_events:
            continue
        kinds = {row.result_kind for row in group}
        if kinds != RESULT_KINDS or len(group) != 3:
            continue
        if any(not 0 <= row.source_minute <= MAX_SOURCE_MINUTE for row in group):
            continue
        candidates: list[tuple[float, Observation, str, str, Book, float, float]] = []
        invalid = False
        for row in group:
            for side, token_id, book in (
                ("YES", row.yes_token_id, row.yes_book),
                ("NO", f"synthetic-no:{row.yes_token_id}", _synthetic_no_book(row.yes_book)),
            ):
                midpoint = book.midpoint
                spread = book.spread
                buy = _walk_buy(book.asks, NOTIONAL_USDC)
                if midpoint is None or spread is None or spread > MAX_SPREAD + 1e-9 or buy is None:
                    invalid = True
                    break
                candidates.append((midpoint, row, side, token_id, book, buy[0], buy[1]))
            if invalid:
                break
        if invalid or len(candidates) != 6:
            continue
        candidates.sort(key=lambda item: (-item[0], item[1].result_kind, item[2]))
        leader, runner_up = candidates[0], candidates[1]
        margin = leader[0] - runner_up[0]
        if margin + 1e-9 < MIN_LEADER_MARGIN:
            continue
        entry_vwap, shares = leader[5], leader[6]
        if not ENTRY_MIN - 1e-9 <= entry_vwap <= ENTRY_MAX + 1e-9:
            continue
        row = leader[1]
        entries.append(
            Entry(
                database=database,
                event_id=event_id,
                event_title=row.event_title,
                league_code=row.league_code,
                condition_id=row.condition_id,
                result_kind=row.result_kind,
                outcome_side=leader[2],
                token_id=leader[3],
                observed_at=row.observed_at,
                source_minute=row.source_minute,
                entry_vwap=entry_vwap,
                shares=shares,
                leader_midpoint=leader[0],
                leader_margin=margin,
            )
        )
        selected_events.add(event_id)
    return entries


def _paths(observations: Sequence[Observation]) -> dict[tuple[str, str], list[tuple[str, float, Book]]]:
    paths: dict[tuple[str, str], list[tuple[str, float, Book]]] = defaultdict(list)
    for row in observations:
        paths[(row.event_id, f"YES:{row.result_kind}")].append(
            (row.observed_at, row.source_minute, row.yes_book)
        )
        paths[(row.event_id, f"NO:{row.result_kind}")].append(
            (row.observed_at, row.source_minute, _synthetic_no_book(row.yes_book))
        )
    for values in paths.values():
        values.sort(key=lambda item: item[0])
    return paths


def _resolutions(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(condition_id): int(winner_index)
        for condition_id, winner_index in connection.execute(
            "SELECT condition_id, winner_index FROM resolution_observations"
        )
    }


def _evaluate(
    entry: Entry,
    path: Sequence[tuple[str, float, Book]],
    winner_index: int | None,
    take_profit: float,
    stop_loss: float,
) -> tuple[float, str] | None:
    normal_target = min(0.999, entry.entry_vwap + take_profit)
    late_target = min(0.999, entry.entry_vwap + take_profit * LATE_PROFIT_FRACTION)
    stop_trigger = max(0.01, entry.entry_vwap - stop_loss)
    for observed_at, source_minute, book in path:
        if observed_at < entry.observed_at:
            continue
        exit_vwap = _walk_sell(book.bids, entry.shares)
        if exit_vwap is None:
            continue
        if exit_vwap + 1e-9 >= normal_target:
            return (exit_vwap - entry.entry_vwap) / entry.entry_vwap, "take_profit"
        if source_minute + 1e-9 >= LATE_EXIT_MINUTE and exit_vwap + 1e-9 >= late_target:
            return (exit_vwap - entry.entry_vwap) / entry.entry_vwap, "late_half_target"
        if source_minute < LATE_EXIT_MINUTE - 1e-9 and book.best_bid is not None:
            if book.best_bid <= stop_trigger + 1e-9:
                return (exit_vwap - entry.entry_vwap) / entry.entry_vwap, "stop"
    if winner_index in (0, 1):
        payout = float(winner_index == (0 if entry.outcome_side == "YES" else 1))
        return (payout - entry.entry_vwap) / entry.entry_vwap, "resolution"
    return None


def _database_report(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        source_cutoff = connection.execute(
            "SELECT MAX(observed_at) FROM event_observations"
        ).fetchone()[0]
        observations = _read_observations(connection)
        entries = _select_entries(path.name, observations)
        paths = _paths(observations)
        resolutions = _resolutions(connection)
        grid = []
        for take_profit in TAKE_PROFITS:
            for stop_loss in STOP_LOSSES:
                returns: list[float] = []
                reasons: dict[str, int] = defaultdict(int)
                for entry in entries:
                    outcome = _evaluate(
                        entry,
                        paths[(entry.event_id, f"{entry.outcome_side}:{entry.result_kind}")],
                        resolutions.get(entry.condition_id),
                        take_profit,
                        stop_loss,
                    )
                    if outcome is None:
                        continue
                    value, reason = outcome
                    returns.append(value)
                    reasons[reason] += 1
                grid.append(
                    {
                        "take_profit_delta": take_profit,
                        "stop_loss_delta": stop_loss,
                        "evaluated_events": len(returns),
                        "mean_return": statistics.fmean(returns) if returns else None,
                        "median_return": statistics.median(returns) if returns else None,
                        "positive": sum(value > 0 for value in returns),
                        "non_positive": sum(value <= 0 for value in returns),
                        "exit_reasons": dict(sorted(reasons.items())),
                    }
                )
        grid.sort(
            key=lambda item: (
                -(item["mean_return"] if item["mean_return"] is not None else -math.inf),
                item["take_profit_delta"],
                item["stop_loss_delta"],
            )
        )
        return {
            "path": path.as_posix(),
            "sha256": _sha256(path),
            "quick_check": quick_check,
            "source_cutoff": source_cutoff,
            "observation_count": len(observations),
            "eligible_entry_count": len(entries),
            "entry_side_counts": {
                side: sum(entry.outcome_side == side for entry in entries)
                for side in ("YES", "NO")
            },
            "entries": [entry.__dict__ for entry in entries],
            "grid": grid,
        }
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = [path for path in args.db if not path.is_file()]
    if missing:
        raise SystemExit("missing database(s): " + ", ".join(map(str, missing)))
    report = {
        "schema": "golden-peach-historical-replay-v1",
        "evidence_limitations": [
            "legacy Watermelon persisted direct YES books only",
            "NO books are synthetic complements and are not actual direct CLOB evidence",
            "displayed-book replay excludes actual fills and fee",
            "parameter grid is exploratory and not out-of-sample profit evidence",
        ],
        "contract": {
            "soccer_leagues": sorted(SOCCER_LEAGUES),
            "entry_vwap": [ENTRY_MIN, ENTRY_MAX],
            "max_source_minute": MAX_SOURCE_MINUTE,
            "min_leader_margin": MIN_LEADER_MARGIN,
            "max_spread": MAX_SPREAD,
            "notional_usdc": NOTIONAL_USDC,
            "late_exit_minute": LATE_EXIT_MINUTE,
            "late_profit_fraction": LATE_PROFIT_FRACTION,
        },
        "databases": [_database_report(path) for path in args.db],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
