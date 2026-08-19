#!/usr/bin/env python3
"""Evaluate the preregistered late-sports-underdog candidate on Pomegranate shards.

This is an offline, read-only analysis.  It never calls an API, opens a wallet,
or writes to the collector databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SPORTS_SLUG = "sports"
WINDOW_HOURS = 6.0
MIN_OBSERVATION_GAP_MINUTES = 5.0
MAX_OBSERVATION_GAP_MINUTES = 30.0
MAX_SPREAD = 0.03
PRIMARY_BAND = (0.10, 0.20)
CONTROL_BAND = (0.20, 0.30)
PRICE_SUM_BOUNDS = (0.98, 1.02)
SPORTS_TAKER_FEE_RATE = 0.03


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def has_sports_tag(raw: str) -> bool:
    try:
        tags = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    return any(
        isinstance(tag, dict) and str(tag.get("slug", "")).lower() == SPORTS_SLUG
        for tag in tags
    )


def parse_pair(raw: str) -> tuple[float, float] | None:
    try:
        values = json.loads(raw)
        if len(values) != 2:
            return None
        pair = (float(values[0]), float(values[1]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not all(0.0 <= value <= 1.0 for value in pair):
        return None
    if not PRICE_SUM_BOUNDS[0] <= sum(pair) <= PRICE_SUM_BOUNDS[1]:
        return None
    return pair


@dataclass(frozen=True)
class Observation:
    condition_id: str
    event_id: str
    question: str
    received_at: datetime
    end_at: datetime
    prices: tuple[float, float]
    tokens: tuple[str, str]
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    run_id: str
    cycle_number: int

    @property
    def remaining_hours(self) -> float:
        return (self.end_at - self.received_at).total_seconds() / 3600.0


@dataclass(frozen=True)
class Signal:
    arm: str
    condition_id: str
    event_id: str
    question: str
    signal_at: str
    end_at: str
    underdog_index: int
    underdog_token: str
    proxy_ask: float
    gamma_spread: float
    run_id: str
    cycle_number: int
    exact_clob_ask: float | None


@dataclass(frozen=True)
class Label:
    observed_at: datetime
    winner_index: int


def database_date(path: Path) -> str:
    name = path.stem
    marker = "trades_sim_"
    if not name.startswith(marker) or len(name) != len(marker) + 8:
        raise ValueError(f"not a dated Pomegranate shard: {path}")
    raw = name[len(marker) :]
    return datetime.strptime(raw, "%Y%m%d").date().isoformat()


def validate_database(path: Path, *, run_quick_check: bool = True) -> None:
    expected_day = database_date(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if run_quick_check:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise RuntimeError(f"SQLite quick_check failed: {path}: {quick_check}")
        row = connection.execute(
            "SELECT contract_name, database_utc_date FROM collection_contracts"
        ).fetchone()
        if row != ("research-full-v1", expected_day):
            raise RuntimeError(f"collection contract mismatch: {path}: {row}")
    finally:
        connection.close()


def iter_observations(path: Path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT m.condition_id, m.event_id, m.question, m.page_received_at,
                   m.end_date, m.outcome_prices_json, m.best_bid, m.best_ask,
                   m.spread, m.tags_json, m.run_id, m.cycle_number,
                   MAX(CASE WHEN o.outcome_index = 0 THEN o.token_id END) AS token_0,
                   MAX(CASE WHEN o.outcome_index = 1 THEN o.token_id END) AS token_1,
                   COUNT(o.outcome_observation_id) AS outcome_count
            FROM market_observations AS m
            JOIN outcome_observations AS o
              ON o.observation_id = m.observation_id
            WHERE m.condition_id IS NOT NULL
              AND m.event_id IS NOT NULL
              AND m.end_date IS NOT NULL
              AND m.active = 1
              AND m.closed = 0
              AND m.enable_order_book = 1
              AND m.accepting_orders = 1
              AND m.tags_json LIKE '%"slug":"sports"%'
            GROUP BY m.observation_id
            HAVING outcome_count = 2 AND token_0 IS NOT NULL AND token_1 IS NOT NULL
            ORDER BY m.page_received_at, m.condition_id
            """
        )
        for row in rows:
            if not has_sports_tag(row["tags_json"]):
                continue
            pair = parse_pair(row["outcome_prices_json"])
            if pair is None:
                continue
            try:
                received_at = parse_time(row["page_received_at"])
                end_at = parse_time(row["end_date"])
            except (TypeError, ValueError):
                continue
            yield Observation(
                condition_id=str(row["condition_id"]),
                event_id=str(row["event_id"]),
                question=str(row["question"] or ""),
                received_at=received_at,
                end_at=end_at,
                prices=pair,
                tokens=(str(row["token_0"]), str(row["token_1"])),
                best_bid=float(row["best_bid"]) if row["best_bid"] is not None else None,
                best_ask=float(row["best_ask"]) if row["best_ask"] is not None else None,
                spread=float(row["spread"]) if row["spread"] is not None else None,
                run_id=str(row["run_id"]),
                cycle_number=int(row["cycle_number"]),
            )
    finally:
        connection.close()


def load_exact_books(paths: list[Path]) -> dict[tuple[str, int, str], float]:
    books: dict[tuple[str, int, str], tuple[datetime, float]] = {}
    for path in paths:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for run_id, cycle_number, token_id, received_at, best_ask in connection.execute(
                """
                SELECT run_id, cycle_number, token_id, received_at, best_ask
                FROM orderbook_snapshots
                WHERE best_ask IS NOT NULL
                """
            ):
                key = (str(run_id), int(cycle_number), str(token_id))
                candidate = (parse_time(str(received_at)), float(best_ask))
                if key not in books or candidate[0] > books[key][0]:
                    books[key] = candidate
        finally:
            connection.close()
    return {key: value[1] for key, value in books.items()}


def load_labels(paths: list[Path], cutoff: datetime) -> tuple[dict[str, Label], int]:
    labels: dict[str, Label] = {}
    conflicts = 0
    for path in paths:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for condition_id, observed_at, winner_index in connection.execute(
                """
                SELECT condition_id, observed_at, one_hot_outcome_index
                FROM resolution_observations
                WHERE lookup_status = 'OBSERVED'
                  AND closed = 1
                  AND one_hot = 1
                  AND one_hot_outcome_index IN (0, 1)
                """
            ):
                observed = parse_time(str(observed_at))
                if observed >= cutoff:
                    continue
                key = str(condition_id)
                candidate = Label(observed_at=observed, winner_index=int(winner_index))
                previous = labels.get(key)
                if previous and previous.winner_index != candidate.winner_index:
                    conflicts += 1
                if previous is None or candidate.observed_at > previous.observed_at:
                    labels[key] = candidate
        finally:
            connection.close()
    return labels, conflicts


def arm_for_price(price: float) -> str | None:
    if PRIMARY_BAND[0] <= price < PRIMARY_BAND[1]:
        return "primary_10_20"
    if CONTROL_BAND[0] <= price < CONTROL_BAND[1]:
        return "control_20_30"
    return None


def discover_signals(
    paths: list[Path], exact_books: dict[tuple[str, int, str], float]
) -> tuple[list[Signal], dict[str, int]]:
    previous: dict[str, Observation] = {}
    crossed: set[str] = set()
    signals: list[Signal] = []
    counters: defaultdict[str, int] = defaultdict(int)

    for path in paths:
        for current in iter_observations(path):
            counters["eligible_observations"] += 1
            prior = previous.get(current.condition_id)
            previous[current.condition_id] = current
            if current.condition_id in crossed or prior is None:
                continue
            if prior.end_at != current.end_at:
                counters["end_date_changed"] += 1
                continue
            gap_minutes = (current.received_at - prior.received_at).total_seconds() / 60.0
            if not MIN_OBSERVATION_GAP_MINUTES <= gap_minutes <= MAX_OBSERVATION_GAP_MINUTES:
                counters["crossing_gap_rejected"] += 1
                continue
            if not (prior.remaining_hours > WINDOW_HOURS >= current.remaining_hours > 0.0):
                continue
            crossed.add(current.condition_id)
            counters["six_hour_crossings"] += 1

            if current.spread is None or current.spread > MAX_SPREAD:
                counters["spread_rejected"] += 1
                continue
            if current.prices[0] == current.prices[1]:
                counters["price_tie_rejected"] += 1
                continue
            underdog_index = 0 if current.prices[0] < current.prices[1] else 1
            if underdog_index == 0:
                proxy_ask = current.best_ask
            else:
                proxy_ask = 1.0 - current.best_bid if current.best_bid is not None else None
            if proxy_ask is None or not 0.0 < proxy_ask < 1.0:
                counters["missing_proxy_ask"] += 1
                continue
            arm = arm_for_price(proxy_ask)
            if arm is None:
                counters["outside_price_arms"] += 1
                continue
            token = current.tokens[underdog_index]
            signals.append(
                Signal(
                    arm=arm,
                    condition_id=current.condition_id,
                    event_id=current.event_id,
                    question=current.question,
                    signal_at=current.received_at.isoformat(),
                    end_at=current.end_at.isoformat(),
                    underdog_index=underdog_index,
                    underdog_token=token,
                    proxy_ask=proxy_ask,
                    gamma_spread=current.spread,
                    run_id=current.run_id,
                    cycle_number=current.cycle_number,
                    exact_clob_ask=exact_books.get(
                        (current.run_id, current.cycle_number, token)
                    ),
                )
            )
            counters[f"signals_{arm}"] += 1
    return signals, dict(sorted(counters.items()))


def percentile_interval(values: list[float]) -> list[float | None]:
    if not values:
        return [None, None]
    ordered = sorted(values)

    def pick(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return [pick(0.025), pick(0.975)]


def event_bootstrap(
    rows: list[tuple[Signal, int]], *, seed: str, iterations: int = 4000
) -> dict[str, list[float | None]]:
    grouped: defaultdict[str, list[tuple[Signal, int]]] = defaultdict(list)
    for row in rows:
        grouped[row[0].event_id].append(row)
    event_rows = list(grouped.values())
    if len(event_rows) < 2:
        return {"edge_pp_95ci": [None, None], "gross_roi_pct_95ci": [None, None]}
    rng_seed = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)
    rng = random.Random(rng_seed)
    edges: list[float] = []
    rois: list[float] = []
    for _ in range(iterations):
        sampled_events = [rng.choice(event_rows) for _ in event_rows]
        event_edges = []
        event_rois = []
        for event in sampled_events:
            event_edges.append(
                statistics.fmean(winner - signal.proxy_ask for signal, winner in event)
            )
            event_rois.append(
                statistics.fmean(winner / signal.proxy_ask - 1.0 for signal, winner in event)
            )
        edges.append(statistics.fmean(event_edges) * 100.0)
        rois.append(statistics.fmean(event_rois) * 100.0)
    return {
        "edge_pp_95ci": percentile_interval(edges),
        "gross_roi_pct_95ci": percentile_interval(rois),
    }


def summarize(
    signals: list[Signal], labels: dict[str, Label], *, seed: str
) -> dict[str, Any]:
    labeled = [(signal, labels[signal.condition_id].winner_index) for signal in signals if signal.condition_id in labels]
    result: dict[str, Any] = {
        "signals": len(signals),
        "events": len({signal.event_id for signal in signals}),
        "labeled": len(labeled),
        "labeled_events": len({signal.event_id for signal, _ in labeled}),
        "label_coverage": len(labeled) / len(signals) if signals else None,
        "wins": sum(winner == signal.underdog_index for signal, winner in labeled),
        "exact_clob_quotes": sum(signal.exact_clob_ask is not None for signal in signals),
    }
    if not labeled:
        return result
    normalized = [(signal, int(winner == signal.underdog_index)) for signal, winner in labeled]
    asks = [signal.proxy_ask for signal, _ in normalized]
    edges = [winner - signal.proxy_ask for signal, winner in normalized]
    returns = [winner / signal.proxy_ask - 1.0 for signal, winner in normalized]
    by_event: defaultdict[str, list[tuple[Signal, int]]] = defaultdict(list)
    for row in normalized:
        by_event[row[0].event_id].append(row)
    event_rois = [
        statistics.fmean(winner / signal.proxy_ask - 1.0 for signal, winner in rows)
        for rows in by_event.values()
    ]

    def taker_cost(price: float, *, adverse: float = 0.0) -> float:
        stressed = min(price + adverse, 0.999)
        return stressed + SPORTS_TAKER_FEE_RATE * stressed * (1.0 - stressed)

    result.update(
        {
            "win_rate": statistics.fmean(winner for _, winner in normalized),
            "mean_proxy_ask": statistics.fmean(asks),
            "edge_pp": statistics.fmean(edges) * 100.0,
            "gross_roi_pct": statistics.fmean(returns) * 100.0,
            "event_equal_gross_roi_pct": statistics.fmean(event_rois) * 100.0,
            "adverse_1c_event_equal_roi_pct": statistics.fmean(
                statistics.fmean(
                    winner / min(signal.proxy_ask + 0.01, 0.999) - 1.0
                    for signal, winner in rows
                )
                for rows in by_event.values()
            )
            * 100.0,
            "adverse_2c_event_equal_roi_pct": statistics.fmean(
                statistics.fmean(
                    winner / min(signal.proxy_ask + 0.02, 0.999) - 1.0
                    for signal, winner in rows
                )
                for rows in by_event.values()
            )
            * 100.0,
            "sports_taker_fee_event_equal_roi_pct": statistics.fmean(
                statistics.fmean(
                    winner / taker_cost(signal.proxy_ask) - 1.0
                    for signal, winner in rows
                )
                for rows in by_event.values()
            )
            * 100.0,
            "sports_taker_fee_plus_1c_event_equal_roi_pct": statistics.fmean(
                statistics.fmean(
                    winner / taker_cost(signal.proxy_ask, adverse=0.01) - 1.0
                    for signal, winner in rows
                )
                for rows in by_event.values()
            )
            * 100.0,
        }
    )
    result.update(event_bootstrap(normalized, seed=seed))

    exact = [
        (signal, winner)
        for signal, winner in normalized
        if signal.exact_clob_ask is not None and 0.0 < float(signal.exact_clob_ask) < 1.0
    ]
    if exact:
        result["exact_clob_subset"] = {
            "labeled": len(exact),
            "mean_ask": statistics.fmean(float(signal.exact_clob_ask) for signal, _ in exact),
            "edge_pp": statistics.fmean(
                winner - float(signal.exact_clob_ask) for signal, winner in exact
            )
            * 100.0,
            "gross_roi_pct": statistics.fmean(
                winner / float(signal.exact_clob_ask) - 1.0 for signal, winner in exact
            )
            * 100.0,
        }
    return result


def in_range(signal: Signal, start: datetime, end: datetime) -> bool:
    value = parse_time(signal.signal_at)
    return start <= value < end


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", type=Path, required=True)
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--holdout-start", required=True)
    parser.add_argument("--holdout-end", required=True)
    parser.add_argument("--include-signals", action="store_true")
    parser.add_argument(
        "--skip-quick-check",
        action="store_true",
        help="Use only after daily-rsync verify has checked every input shard.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = sorted({path.resolve() for path in args.db}, key=database_date)
    if not paths:
        raise SystemExit("at least one database is required")
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"database not found: {path}")
        validate_database(path, run_quick_check=not args.skip_quick_check)

    train_start = parse_time(args.train_start)
    train_end = parse_time(args.train_end)
    holdout_start = parse_time(args.holdout_start)
    holdout_end = parse_time(args.holdout_end)
    if not train_start < train_end <= holdout_start < holdout_end:
        raise SystemExit("periods must be ordered half-open ranges")

    books = load_exact_books(paths)
    labels, label_conflicts = load_labels(paths, holdout_end)
    signals, counters = discover_signals(paths, books)
    train = [signal for signal in signals if in_range(signal, train_start, train_end)]
    holdout_raw = [signal for signal in signals if in_range(signal, holdout_start, holdout_end)]
    train_events = {signal.event_id for signal in train}
    holdout = [signal for signal in holdout_raw if signal.event_id not in train_events]

    def arms(rows: list[Signal], prefix: str) -> dict[str, Any]:
        return {
            arm: summarize(
                [row for row in rows if row.arm == arm],
                labels,
                seed=f"{prefix}:{arm}",
            )
            for arm in ("primary_10_20", "control_20_30")
        }

    payload: dict[str, Any] = {
        "schema": "pomegranate-late-underdog-evaluation-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "databases": [str(path) for path in paths],
        "database_dates": [database_date(path) for path in paths],
        "ranges": {
            "train": [train_start.isoformat(), train_end.isoformat()],
            "holdout": [holdout_start.isoformat(), holdout_end.isoformat()],
        },
        "rule": {
            "sports_slug": SPORTS_SLUG,
            "window_hours": WINDOW_HOURS,
            "observation_gap_minutes": [
                MIN_OBSERVATION_GAP_MINUTES,
                MAX_OBSERVATION_GAP_MINUTES,
            ],
            "max_spread": MAX_SPREAD,
            "primary_band": list(PRIMARY_BAND),
            "control_band": list(CONTROL_BAND),
            "price_sum_bounds": list(PRICE_SUM_BOUNDS),
            "conservative_sports_taker_fee_rate": SPORTS_TAKER_FEE_RATE,
            "exit": "closed one-hot resolution",
        },
        "integrity": {
            "sqlite_quick_check": (
                "delegated_to_daily_rsync_verify" if args.skip_quick_check else "ok"
            ),
            "collection_contract": "research-full-v1",
            "label_conflicts": label_conflicts,
        },
        "discovery_counters": counters,
        "train": arms(train, "train"),
        "holdout_before_event_purge": arms(holdout_raw, "holdout-raw"),
        "event_purge": {
            "train_events": len(train_events),
            "removed_signals": len(holdout_raw) - len(holdout),
            "removed_events": len(
                {signal.event_id for signal in holdout_raw if signal.event_id in train_events}
            ),
        },
        "holdout_event_purged": arms(holdout, "holdout-purged"),
    }
    if args.include_signals:
        payload["signals"] = {
            "train": [asdict(signal) for signal in train],
            "holdout_event_purged": [asdict(signal) for signal in holdout],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
