#!/usr/bin/env python3
"""Evaluate late sports favorite entry/exit grids on Pomegranate shards.

This module is deliberately read-only.  It uses point-in-time Gamma quotes as
an execution proxy and reports the much smaller same-cycle exact CLOB subset
separately.  It never calls a network API or opens a wallet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SPORTS_SLUG = "sports"
WINDOW_HOURS = 6.0
MIN_GAP_MINUTES = 5.0
MAX_GAP_MINUTES = 30.0
MAX_SPREAD = 0.03
PRICE_SUM_BOUNDS = (0.98, 1.02)
ENTRY_CENTS = tuple(range(75, 98))
MAX_ENTRY_OVERSHOOT = 0.01
SPORTS_TAKER_FEE_RATE = 0.03
VIRTUAL_NOTIONAL = 5.0
EPSILON = 1e-9


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


def sports_market_type(raw: str) -> str:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "unknown"
    if not isinstance(value, dict):
        return "unknown"
    result = str(value.get("sportsMarketType") or "unknown").strip().lower()
    return result or "unknown"


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


def executable_quotes(
    best_bid: float | None, best_ask: float | None
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if best_bid is None or best_ask is None:
        return None
    bids = (best_bid, 1.0 - best_ask)
    asks = (best_ask, 1.0 - best_bid)
    for bid, ask in zip(bids, asks, strict=True):
        if not 0.0 < bid <= ask < 1.0:
            return None
    return bids, asks


def database_date(path: Path) -> str:
    marker = "trades_sim_"
    name = path.stem
    if not name.startswith(marker) or len(name) != len(marker) + 8:
        raise ValueError(f"not a dated Pomegranate shard: {path}")
    return datetime.strptime(name[len(marker) :], "%Y%m%d").date().isoformat()


def validate_database(path: Path, *, run_quick_check: bool = True) -> None:
    expected_day = database_date(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if run_quick_check:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise RuntimeError(f"SQLite quick_check failed: {path}: {quick_check}")
        contract = connection.execute(
            "SELECT contract_name, database_utc_date FROM collection_contracts"
        ).fetchone()
        if contract != ("research-full-v1", expected_day):
            raise RuntimeError(f"collection contract mismatch: {path}: {contract}")
    finally:
        connection.close()


@dataclass(frozen=True)
class ExactBook:
    bid: float | None
    ask: float | None


@dataclass(frozen=True)
class Observation:
    condition_id: str
    event_id: str
    question: str
    received_at: datetime
    end_at: datetime
    game_start_at: datetime | None
    outcome_prices: tuple[float, float]
    tokens: tuple[str, str]
    bids: tuple[float, float]
    asks: tuple[float, float]
    spread: float
    fees_enabled: bool | None
    market_type: str
    run_id: str
    cycle_number: int

    @property
    def remaining_hours(self) -> float:
        return (self.end_at - self.received_at).total_seconds() / 3600.0


@dataclass(frozen=True)
class ExitHit:
    observed_at: datetime
    proxy_bid: float
    exact_bid: float | None


@dataclass
class Signal:
    condition_id: str
    event_id: str
    question: str
    outcome_index: int
    token_id: str
    entry_cents: int
    signal_at: datetime
    end_at: datetime
    game_start_at: datetime | None
    proxy_ask: float
    exact_ask: float | None
    spread: float
    fees_enabled: bool | None
    market_type: str
    run_id: str
    cycle_number: int
    target_hits: dict[int, ExitHit] = field(default_factory=dict)


@dataclass(frozen=True)
class Label:
    observed_at: datetime
    winner_index: int


@dataclass(frozen=True)
class TradeResult:
    event_id: str
    condition_id: str
    market_type: str
    signal_at: datetime
    exit_at: datetime
    exit_kind: str
    entry_ask: float
    exit_value: float
    fee_rate: float
    roi: float
    stress_1c_roi: float
    stress_2c_roi: float
    pnl_usdc: float
    stress_1c_pnl_usdc: float
    exact_roi: float | None
    resolution_win: int | None

    @property
    def holding_hours(self) -> float:
        return (self.exit_at - self.signal_at).total_seconds() / 3600.0


def iter_observations(path: Path) -> Iterable[Observation]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT m.condition_id, m.event_id, m.question, m.page_received_at,
                   m.end_date, m.game_start_time, m.outcome_prices_json,
                   m.best_bid, m.best_ask, m.spread, m.fees_enabled,
                   m.tags_json, m.sports_json, m.run_id, m.cycle_number,
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
            prices = parse_pair(row["outcome_prices_json"])
            quotes = executable_quotes(row["best_bid"], row["best_ask"])
            if prices is None or quotes is None or row["spread"] is None:
                continue
            spread = float(row["spread"])
            if spread < 0.0 or spread > MAX_SPREAD + EPSILON:
                continue
            try:
                received_at = parse_time(str(row["page_received_at"]))
                end_at = parse_time(str(row["end_date"]))
                game_start_at = (
                    parse_time(str(row["game_start_time"]))
                    if row["game_start_time"] is not None
                    else None
                )
            except (TypeError, ValueError):
                continue
            fee_raw = row["fees_enabled"]
            fees_enabled = bool(fee_raw) if fee_raw is not None else None
            yield Observation(
                condition_id=str(row["condition_id"]),
                event_id=str(row["event_id"]),
                question=str(row["question"] or ""),
                received_at=received_at,
                end_at=end_at,
                game_start_at=game_start_at,
                outcome_prices=prices,
                tokens=(str(row["token_0"]), str(row["token_1"])),
                bids=quotes[0],
                asks=quotes[1],
                spread=spread,
                fees_enabled=fees_enabled,
                market_type=sports_market_type(row["sports_json"]),
                run_id=str(row["run_id"]),
                cycle_number=int(row["cycle_number"]),
            )
    finally:
        connection.close()


def load_exact_books(paths: list[Path]) -> dict[tuple[str, int, str], ExactBook]:
    books: dict[tuple[str, int, str], tuple[datetime, ExactBook]] = {}
    for path in paths:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                SELECT run_id, cycle_number, token_id, received_at,
                       best_bid, best_ask
                FROM orderbook_snapshots
                WHERE best_bid IS NOT NULL OR best_ask IS NOT NULL
                """
            )
            for run_id, cycle, token, received_at, best_bid, best_ask in rows:
                key = (str(run_id), int(cycle), str(token))
                candidate = (
                    parse_time(str(received_at)),
                    ExactBook(
                        bid=float(best_bid) if best_bid is not None else None,
                        ask=float(best_ask) if best_ask is not None else None,
                    ),
                )
                if key not in books or candidate[0] > books[key][0]:
                    books[key] = candidate
        finally:
            connection.close()
    return {key: value[1] for key, value in books.items()}


def load_labels(
    paths: list[Path], cutoff: datetime
) -> tuple[dict[str, list[Label]], set[str]]:
    raw: defaultdict[str, list[Label]] = defaultdict(list)
    winners: defaultdict[str, set[int]] = defaultdict(set)
    for path in paths:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                SELECT condition_id, observed_at, one_hot_outcome_index
                FROM resolution_observations
                WHERE lookup_status = 'OBSERVED'
                  AND closed = 1
                  AND one_hot = 1
                  AND one_hot_outcome_index IN (0, 1)
                """
            )
            for condition_id, observed_at, winner_index in rows:
                observed = parse_time(str(observed_at))
                if observed >= cutoff:
                    continue
                key = str(condition_id)
                winner = int(winner_index)
                raw[key].append(Label(observed_at=observed, winner_index=winner))
                winners[key].add(winner)
        finally:
            connection.close()
    conflicts = {condition for condition, values in winners.items() if len(values) > 1}
    return {
        condition: sorted(values, key=lambda value: value.observed_at)
        for condition, values in raw.items()
        if condition not in conflicts
    }, conflicts


def _gap_minutes(previous: Observation, current: Observation) -> float:
    return (current.received_at - previous.received_at).total_seconds() / 60.0


def discover_signals(
    paths: list[Path], exact_books: dict[tuple[str, int, str], ExactBook]
) -> tuple[list[Signal], dict[str, int]]:
    previous: dict[str, Observation] = {}
    armed_end: dict[str, datetime] = {}
    emitted: set[tuple[str, int, int]] = set()
    active: defaultdict[str, list[Signal]] = defaultdict(list)
    signals: list[Signal] = []
    counters: defaultdict[str, int] = defaultdict(int)

    for path in paths:
        for current in iter_observations(path):
            counters["eligible_quote_observations"] += 1

            for signal in active.get(current.condition_id, ()):
                if current.received_at <= signal.signal_at:
                    continue
                bid = current.bids[signal.outcome_index]
                exact = exact_books.get(
                    (current.run_id, current.cycle_number, signal.token_id)
                )
                for target_cents in range(signal.entry_cents + 2, 100):
                    if target_cents in signal.target_hits:
                        continue
                    target = target_cents / 100.0
                    if bid + EPSILON >= target:
                        signal.target_hits[target_cents] = ExitHit(
                            observed_at=current.received_at,
                            proxy_bid=bid,
                            exact_bid=exact.bid if exact is not None else None,
                        )

            prior = previous.get(current.condition_id)
            previous[current.condition_id] = current
            if prior is None:
                continue
            same_end = prior.end_at == current.end_at
            gap = _gap_minutes(prior, current)
            cadence_ok = MIN_GAP_MINUTES <= gap <= MAX_GAP_MINUTES
            if not same_end:
                armed_end.pop(current.condition_id, None)
                counters["end_date_changed"] += 1
                continue
            if (
                cadence_ok
                and prior.remaining_hours > WINDOW_HOURS
                and 0.0 < current.remaining_hours <= WINDOW_HOURS
            ):
                armed_end[current.condition_id] = current.end_at
                counters["six_hour_boundaries"] += 1
            if armed_end.get(current.condition_id) != current.end_at:
                continue
            if not 0.0 < current.remaining_hours <= WINDOW_HOURS:
                continue
            if not cadence_ok:
                counters["entry_gap_rejected"] += 1
                continue

            for outcome_index in (0, 1):
                prior_ask = prior.asks[outcome_index]
                current_ask = current.asks[outcome_index]
                for entry_cents in ENTRY_CENTS:
                    key = (current.condition_id, outcome_index, entry_cents)
                    if key in emitted:
                        continue
                    threshold = entry_cents / 100.0
                    if not (
                        prior_ask < threshold <= current_ask + EPSILON
                        and current_ask <= threshold + MAX_ENTRY_OVERSHOOT + EPSILON
                    ):
                        continue
                    emitted.add(key)
                    token = current.tokens[outcome_index]
                    exact = exact_books.get(
                        (current.run_id, current.cycle_number, token)
                    )
                    signal = Signal(
                        condition_id=current.condition_id,
                        event_id=current.event_id,
                        question=current.question,
                        outcome_index=outcome_index,
                        token_id=token,
                        entry_cents=entry_cents,
                        signal_at=current.received_at,
                        end_at=current.end_at,
                        game_start_at=current.game_start_at,
                        proxy_ask=current_ask,
                        exact_ask=exact.ask if exact is not None else None,
                        spread=current.spread,
                        fees_enabled=current.fees_enabled,
                        market_type=current.market_type,
                        run_id=current.run_id,
                        cycle_number=current.cycle_number,
                    )
                    signals.append(signal)
                    active[current.condition_id].append(signal)
                    counters["signals"] += 1
                    counters[f"signals_entry_{entry_cents}"] += 1
                    if current.game_start_at is None:
                        counters["signals_without_game_start"] += 1
                    else:
                        end_start_delta = abs(
                            (current.end_at - current.game_start_at).total_seconds()
                        )
                        if end_start_delta <= 60.0:
                            counters["signals_end_equals_game_start"] += 1
                        else:
                            counters["signals_end_differs_from_game_start"] += 1

    counters["conditions_with_signals"] = len(
        {signal.condition_id for signal in signals}
    )
    counters["events_with_signals"] = len({signal.event_id for signal in signals})
    return signals, dict(sorted(counters.items()))


def label_before(
    labels: dict[str, list[Label]], condition_id: str, after: datetime, cutoff: datetime
) -> Label | None:
    for label in labels.get(condition_id, ()):
        if after < label.observed_at < cutoff:
            return label
    return None


def fee_rate(signal: Signal) -> float:
    return 0.0 if signal.fees_enabled is False else SPORTS_TAKER_FEE_RATE


def net_entry_cost(price: float, rate: float) -> float:
    return price + rate * price * (1.0 - price)


def net_exit_value(price: float, rate: float) -> float:
    return price - rate * price * (1.0 - price)


def trade_roi(
    *,
    entry_price: float,
    exit_value: float,
    rate: float,
    resolution_exit: bool,
    adverse: float = 0.0,
) -> float:
    stressed_entry = min(entry_price + adverse, 0.999)
    cost = net_entry_cost(stressed_entry, rate)
    if resolution_exit:
        proceeds = exit_value
    else:
        stressed_exit = max(exit_value - adverse, 0.001)
        proceeds = net_exit_value(stressed_exit, rate)
    return proceeds / cost - 1.0


def evaluate_signal(
    signal: Signal,
    *,
    target_cents: int | None,
    cutoff: datetime,
    labels: dict[str, list[Label]],
) -> TradeResult | None:
    target_hit = (
        signal.target_hits.get(target_cents) if target_cents is not None else None
    )
    if target_hit is not None and target_hit.observed_at < cutoff:
        exit_at = target_hit.observed_at
        exit_kind = "target"
        exit_value = target_hit.proxy_bid
        resolution_win = None
        exact_exit_value = target_hit.exact_bid
    else:
        label = label_before(labels, signal.condition_id, signal.signal_at, cutoff)
        if label is None:
            return None
        exit_at = label.observed_at
        exit_kind = "resolution"
        resolution_win = int(label.winner_index == signal.outcome_index)
        exit_value = float(resolution_win)
        exact_exit_value = exit_value

    rate = fee_rate(signal)
    roi = trade_roi(
        entry_price=signal.proxy_ask,
        exit_value=exit_value,
        rate=rate,
        resolution_exit=exit_kind == "resolution",
    )
    stress_1c_roi = trade_roi(
        entry_price=signal.proxy_ask,
        exit_value=exit_value,
        rate=rate,
        resolution_exit=exit_kind == "resolution",
        adverse=0.01,
    )
    stress_2c_roi = trade_roi(
        entry_price=signal.proxy_ask,
        exit_value=exit_value,
        rate=rate,
        resolution_exit=exit_kind == "resolution",
        adverse=0.02,
    )
    exact_roi: float | None = None
    if (
        signal.exact_ask is not None
        and 0.0 < signal.exact_ask < 1.0
        and exact_exit_value is not None
        and 0.0 <= exact_exit_value <= 1.0
    ):
        exact_roi = trade_roi(
            entry_price=signal.exact_ask,
            exit_value=exact_exit_value,
            rate=rate,
            resolution_exit=exit_kind == "resolution",
        )
    return TradeResult(
        event_id=signal.event_id,
        condition_id=signal.condition_id,
        market_type=signal.market_type,
        signal_at=signal.signal_at,
        exit_at=exit_at,
        exit_kind=exit_kind,
        entry_ask=signal.proxy_ask,
        exit_value=exit_value,
        fee_rate=rate,
        roi=roi,
        stress_1c_roi=stress_1c_roi,
        stress_2c_roi=stress_2c_roi,
        pnl_usdc=roi * VIRTUAL_NOTIONAL,
        stress_1c_pnl_usdc=stress_1c_roi * VIRTUAL_NOTIONAL,
        exact_roi=exact_roi,
        resolution_win=resolution_win,
    )


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float | None]:
    if total <= 0:
        return [None, None]
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * (
            proportion * (1.0 - proportion) / total
            + z * z / (4.0 * total * total)
        )
        ** 0.5
        / denominator
    )
    return [max(0.0, center - margin), min(1.0, center + margin)]


def cluster_bootstrap_ci(
    trades: list[TradeResult], *, metric: str, seed: str, iterations: int = 4000
) -> list[float | None]:
    grouped: defaultdict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        grouped[trade.event_id].append(trade)
    event_values = [
        statistics.fmean(float(getattr(trade, metric)) for trade in rows)
        for rows in grouped.values()
    ]
    if len(event_values) < 2:
        return [None, None]
    seed_value = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed_value)
    draws = [
        statistics.fmean(rng.choice(event_values) for _ in event_values) * 100.0
        for _ in range(iterations)
    ]
    return [percentile(draws, 0.025), percentile(draws, 0.975)]


def summarize(
    signals: list[Signal],
    *,
    target_cents: int | None,
    cutoff: datetime,
    labels: dict[str, list[Label]],
    bootstrap_seed: str | None = None,
) -> dict[str, Any]:
    signal_results = [
        (
            signal,
            evaluate_signal(
                signal,
                target_cents=target_cents,
                cutoff=cutoff,
                labels=labels,
            ),
        )
        for signal in signals
    ]
    trades = [result for _, result in signal_results if result is not None]
    censored_signals = [signal for signal, result in signal_results if result is None]
    grouped: defaultdict[str, list[TradeResult]] = defaultdict(list)
    for trade in trades:
        grouped[trade.event_id].append(trade)
    event_roi = [statistics.fmean(row.roi for row in rows) for rows in grouped.values()]
    event_stress_1c = [
        statistics.fmean(row.stress_1c_roi for row in rows)
        for rows in grouped.values()
    ]
    event_stress_2c = [
        statistics.fmean(row.stress_2c_roi for row in rows)
        for rows in grouped.values()
    ]
    resolution_trades = [row for row in trades if row.exit_kind == "resolution"]
    target_trades = [row for row in trades if row.exit_kind == "target"]
    exact_trades = [row for row in trades if row.exact_roi is not None]
    result: dict[str, Any] = {
        "signals": len(signals),
        "signal_events": len({signal.event_id for signal in signals}),
        "evaluable": len(trades),
        "evaluable_events": len(grouped),
        "evaluable_pct": len(trades) / len(signals) * 100.0 if signals else None,
        "censored": len(censored_signals),
        "censored_end_before_cutoff": sum(
            signal.end_at < cutoff for signal in censored_signals
        ),
        "censored_end_at_or_after_cutoff": sum(
            signal.end_at >= cutoff for signal in censored_signals
        ),
        "censored_matured_6h_before_cutoff": sum(
            signal.end_at <= cutoff - timedelta(hours=6)
            for signal in censored_signals
        ),
        "target_exits": len(target_trades),
        "resolution_exits": len(resolution_trades),
        "resolution_wins": sum(row.resolution_win or 0 for row in resolution_trades),
        "exact_entry_quotes": sum(signal.exact_ask is not None for signal in signals),
        "exact_evaluable": len(exact_trades),
        "fee_enabled": sum(signal.fees_enabled is True for signal in signals),
        "fee_disabled": sum(signal.fees_enabled is False for signal in signals),
        "fee_unknown": sum(signal.fees_enabled is None for signal in signals),
    }
    if not trades:
        return result

    censored_loss_rois = [-1.0 for _ in censored_signals]
    censored_win_rois = [
        trade_roi(
            entry_price=signal.proxy_ask,
            exit_value=1.0,
            rate=fee_rate(signal),
            resolution_exit=True,
        )
        for signal in censored_signals
    ]

    result.update(
        {
            "mean_entry_ask": statistics.fmean(row.entry_ask for row in trades),
            "signal_equal_fee_net_roi_pct": statistics.fmean(row.roi for row in trades)
            * 100.0,
            "event_equal_fee_net_roi_pct": statistics.fmean(event_roi) * 100.0,
            "event_equal_fee_plus_1c_roi_pct": statistics.fmean(event_stress_1c)
            * 100.0,
            "event_equal_fee_plus_2c_roi_pct": statistics.fmean(event_stress_2c)
            * 100.0,
            "total_5usdc_pnl": sum(row.pnl_usdc for row in trades),
            "total_5usdc_stress_1c_pnl": sum(
                row.stress_1c_pnl_usdc for row in trades
            ),
            "profitable_trade_pct": sum(row.roi > 0.0 for row in trades)
            / len(trades)
            * 100.0,
            "worst_trade_roi_pct": min(row.roi for row in trades) * 100.0,
            "best_trade_roi_pct": max(row.roi for row in trades) * 100.0,
            "worst_event_fee_net_roi_pct": min(event_roi) * 100.0,
            "all_censored_loss_signal_roi_pct": statistics.fmean(
                [row.roi for row in trades] + censored_loss_rois
            )
            * 100.0,
            "all_censored_win_signal_roi_pct": statistics.fmean(
                [row.roi for row in trades] + censored_win_rois
            )
            * 100.0,
            "holding_hours_p50": percentile(
                [row.holding_hours for row in trades], 0.5
            ),
            "holding_hours_p95": percentile(
                [row.holding_hours for row in trades], 0.95
            ),
            "exact_subset_fee_net_roi_pct": (
                statistics.fmean(float(row.exact_roi) for row in exact_trades) * 100.0
                if exact_trades
                else None
            ),
            "market_type_counts": dict(
                sorted(
                    {
                        market_type: sum(
                            row.market_type == market_type for row in trades
                        )
                        for market_type in {row.market_type for row in trades}
                    }.items()
                )
            ),
        }
    )
    if resolution_trades:
        resolution_wins = sum(row.resolution_win or 0 for row in resolution_trades)
        win_interval = wilson_interval(resolution_wins, len(resolution_trades))
        mean_resolution_cost = statistics.fmean(
            net_entry_cost(row.entry_ask, row.fee_rate) for row in resolution_trades
        )
        result["resolution_outcome_diagnostics"] = {
            "wins": resolution_wins,
            "total": len(resolution_trades),
            "win_rate_pct": resolution_wins / len(resolution_trades) * 100.0,
            "win_rate_wilson_95ci_pct": [
                value * 100.0 if value is not None else None for value in win_interval
            ],
            "mean_fee_inclusive_break_even_pct": mean_resolution_cost * 100.0,
            "wilson_lower_edge_pp": (
                (float(win_interval[0]) - mean_resolution_cost) * 100.0
                if win_interval[0] is not None
                else None
            ),
        }
    if bootstrap_seed is not None:
        result["event_cluster_fee_net_roi_95ci_pct"] = cluster_bootstrap_ci(
            trades,
            metric="roi",
            seed=f"{bootstrap_seed}:fee",
        )
        result["event_cluster_fee_plus_1c_roi_95ci_pct"] = cluster_bootstrap_ci(
            trades,
            metric="stress_1c_roi",
            seed=f"{bootstrap_seed}:stress1",
        )
        market_type_metrics: dict[str, Any] = {}
        for market_type in sorted({trade.market_type for trade in trades}):
            selected_type = [
                trade for trade in trades if trade.market_type == market_type
            ]
            type_events: defaultdict[str, list[TradeResult]] = defaultdict(list)
            for trade in selected_type:
                type_events[trade.event_id].append(trade)
            market_type_metrics[market_type] = {
                "trades": len(selected_type),
                "events": len(type_events),
                "event_equal_fee_net_roi_pct": statistics.fmean(
                    statistics.fmean(trade.roi for trade in rows)
                    for rows in type_events.values()
                )
                * 100.0,
                "event_equal_fee_plus_1c_roi_pct": statistics.fmean(
                    statistics.fmean(trade.stress_1c_roi for trade in rows)
                    for rows in type_events.values()
                )
                * 100.0,
            }
        result["market_type_metrics"] = market_type_metrics
    return result


def in_range(signal: Signal, start: datetime, end: datetime) -> bool:
    return start <= signal.signal_at < end


def grid_rows(
    signals: list[Signal],
    *,
    cutoff: datetime,
    labels: dict[str, list[Label]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_entry: defaultdict[int, list[Signal]] = defaultdict(list)
    for signal in signals:
        by_entry[signal.entry_cents].append(signal)
    for entry_cents in ENTRY_CENTS:
        selected = by_entry[entry_cents]
        exits: list[int | None] = [None]
        exits.extend(range(entry_cents + 2, 100))
        for target_cents in exits:
            summary = summarize(
                selected,
                target_cents=target_cents,
                cutoff=cutoff,
                labels=labels,
            )
            rows.append(
                {
                    "entry": entry_cents / 100.0,
                    "exit": (
                        "resolution" if target_cents is None else target_cents / 100.0
                    ),
                    **summary,
                }
            )
    return rows


def eligible_for_selection(row: dict[str, Any]) -> bool:
    return (
        int(row["evaluable"]) >= 50
        and int(row["evaluable_events"]) >= 30
        and row.get("event_equal_fee_plus_1c_roi_pct") is not None
    )


def eligible_for_exploratory_stability(
    train_row: dict[str, Any], validation_row: dict[str, Any]
) -> bool:
    return (
        int(train_row["evaluable"]) >= 20
        and int(train_row["evaluable_events"]) >= 15
        and int(validation_row["evaluable"]) >= 20
        and int(validation_row["evaluable_events"]) >= 15
        and train_row.get("event_equal_fee_plus_1c_roi_pct") is not None
        and validation_row.get("event_equal_fee_plus_1c_roi_pct") is not None
    )


def row_rank(row: dict[str, Any]) -> tuple[float, int, float, float]:
    exit_value = 1.0 if row["exit"] == "resolution" else float(row["exit"])
    return (
        float(row["event_equal_fee_plus_1c_roi_pct"]),
        int(row["evaluable_events"]),
        -float(row["entry"]),
        -exit_value,
    )


def find_row(
    rows: list[dict[str, Any]], entry: float, exit_value: float | str
) -> dict[str, Any]:
    for row in rows:
        if abs(float(row["entry"]) - entry) > EPSILON:
            continue
        if row["exit"] == exit_value:
            return row
        if isinstance(row["exit"], float) and isinstance(exit_value, float):
            if abs(row["exit"] - exit_value) <= EPSILON:
                return row
    raise KeyError((entry, exit_value))


def with_bootstrap(
    row: dict[str, Any],
    signals: list[Signal],
    *,
    cutoff: datetime,
    labels: dict[str, list[Label]],
    seed: str,
) -> dict[str, Any]:
    entry_cents = round(float(row["entry"]) * 100)
    target_cents = (
        None if row["exit"] == "resolution" else round(float(row["exit"]) * 100)
    )
    selected = [signal for signal in signals if signal.entry_cents == entry_cents]
    return {
        "entry": entry_cents / 100.0,
        "exit": "resolution" if target_cents is None else target_cents / 100.0,
        **summarize(
            selected,
            target_cents=target_cents,
            cutoff=cutoff,
            labels=labels,
            bootstrap_seed=seed,
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", type=Path, required=True)
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-quick-check",
        action="store_true",
        help="Use only after daily-rsync verify has checked every input shard.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = sorted({path.resolve() for path in args.db}, key=database_date)
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"database not found: {path}")
        validate_database(path, run_quick_check=not args.skip_quick_check)

    train_start = parse_time(args.train_start)
    train_end = parse_time(args.train_end)
    validation_start = parse_time(args.validation_start)
    validation_end = parse_time(args.validation_end)
    if not train_start < train_end <= validation_start < validation_end:
        raise SystemExit("periods must be ordered half-open ranges")

    exact_books = load_exact_books(paths)
    labels, label_conflicts = load_labels(paths, validation_end)
    signals, discovery_counters = discover_signals(paths, exact_books)
    train = [signal for signal in signals if in_range(signal, train_start, train_end)]
    validation_raw = [
        signal
        for signal in signals
        if in_range(signal, validation_start, validation_end)
    ]
    train_events = {signal.event_id for signal in train}
    validation = [
        signal for signal in validation_raw if signal.event_id not in train_events
    ]
    overall = train + validation

    train_grid = grid_rows(train, cutoff=train_end, labels=labels)
    validation_grid = grid_rows(validation, cutoff=validation_end, labels=labels)
    train_eligible = [row for row in train_grid if eligible_for_selection(row)]
    selected_train = max(train_eligible, key=row_rank) if train_eligible else None
    selected_validation = (
        find_row(validation_grid, selected_train["entry"], selected_train["exit"])
        if selected_train is not None
        else None
    )
    validation_eligible = [
        row for row in validation_grid if eligible_for_selection(row)
    ]
    exploratory_validation_best = (
        max(validation_eligible, key=row_rank) if validation_eligible else None
    )

    anchor_specs: list[tuple[float, float | str]] = [
        (0.80, 0.90),
        (0.85, 0.95),
        (0.90, "resolution"),
        (0.95, "resolution"),
        (0.80, "resolution"),
        (0.85, "resolution"),
    ]
    anchors: dict[str, Any] = {}
    for entry, exit_value in anchor_specs:
        key = f"{entry:.2f}->{exit_value}"
        train_row = find_row(train_grid, entry, exit_value)
        validation_row = find_row(validation_grid, entry, exit_value)
        anchors[key] = {
            "train": with_bootstrap(
                train_row,
                train,
                cutoff=train_end,
                labels=labels,
                seed=f"anchor:train:{key}",
            ),
            "validation": with_bootstrap(
                validation_row,
                validation,
                cutoff=validation_end,
                labels=labels,
                seed=f"anchor:validation:{key}",
            ),
            "overall_descriptive": with_bootstrap(
                validation_row,
                overall,
                cutoff=validation_end,
                labels=labels,
                seed=f"anchor:overall:{key}",
            ),
        }

    selected_payload = None
    if selected_train is not None and selected_validation is not None:
        selected_payload = {
            "train": with_bootstrap(
                selected_train,
                train,
                cutoff=train_end,
                labels=labels,
                seed="selected:train",
            ),
            "validation": with_bootstrap(
                selected_validation,
                validation,
                cutoff=validation_end,
                labels=labels,
                seed="selected:validation",
            ),
        }

    exploratory_payload = None
    if exploratory_validation_best is not None:
        exploratory_payload = with_bootstrap(
            exploratory_validation_best,
            validation,
            cutoff=validation_end,
            labels=labels,
            seed="exploratory:validation-best",
        )

    stable_pairs = [
        (train_row, validation_row)
        for train_row, validation_row in zip(
            train_grid, validation_grid, strict=True
        )
        if eligible_for_exploratory_stability(train_row, validation_row)
    ]
    exploratory_stable_payload = None
    if stable_pairs:
        stable_train, stable_validation = max(
            stable_pairs,
            key=lambda pair: (
                min(
                    float(pair[0]["event_equal_fee_plus_1c_roi_pct"]),
                    float(pair[1]["event_equal_fee_plus_1c_roi_pct"]),
                ),
                min(int(pair[0]["evaluable_events"]), int(pair[1]["evaluable_events"])),
                -float(pair[0]["entry"]),
            ),
        )
        exploratory_stable_payload = {
            "selection_warning": (
                "post-hoc stability screen; not the preregistered primary selection"
            ),
            "train": with_bootstrap(
                stable_train,
                train,
                cutoff=train_end,
                labels=labels,
                seed="exploratory-stable:train",
            ),
            "validation": with_bootstrap(
                stable_validation,
                validation,
                cutoff=validation_end,
                labels=labels,
                seed="exploratory-stable:validation",
            ),
            "overall_descriptive": with_bootstrap(
                stable_validation,
                overall,
                cutoff=validation_end,
                labels=labels,
                seed="exploratory-stable:overall",
            ),
        }

    payload: dict[str, Any] = {
        "schema": "pomegranate-sports-favorite-grid-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "databases": [str(path) for path in paths],
        "database_dates": [database_date(path) for path in paths],
        "ranges": {
            "train": [train_start.isoformat(), train_end.isoformat()],
            "validation": [
                validation_start.isoformat(),
                validation_end.isoformat(),
            ],
        },
        "rule": {
            "sports_only": True,
            "clock": "Gamma end_date",
            "window_hours": WINDOW_HOURS,
            "entry_cents": list(ENTRY_CENTS),
            "max_entry_overshoot": MAX_ENTRY_OVERSHOOT,
            "target_min_increment_cents": 2,
            "target_max": 0.99,
            "max_spread": MAX_SPREAD,
            "virtual_notional": VIRTUAL_NOTIONAL,
            "sports_taker_fee_rate": SPORTS_TAKER_FEE_RATE,
            "adverse_execution_cents": [1, 2],
        },
        "integrity": {
            "sqlite_quick_check": (
                "delegated_to_daily_rsync_verify"
                if args.skip_quick_check
                else "ok"
            ),
            "collection_contract": "research-full-v1",
            "label_conflict_conditions": sorted(label_conflicts),
        },
        "discovery": {
            **discovery_counters,
            "train_signals": len(train),
            "validation_signals_before_event_purge": len(validation_raw),
            "validation_signals": len(validation),
            "train_events": len(train_events),
            "validation_events": len({signal.event_id for signal in validation}),
            "event_purge_removed_signals": len(validation_raw) - len(validation),
        },
        "selection": selected_payload,
        "exploratory_stable_across_periods": exploratory_stable_payload,
        "exploratory_validation_best": exploratory_payload,
        "anchors": anchors,
        "train_top_20": sorted(
            train_eligible, key=row_rank, reverse=True
        )[:20],
        "validation_top_20_posthoc": sorted(
            validation_eligible, key=row_rank, reverse=True
        )[:20],
        "grid": {
            "train": train_grid,
            "validation": validation_grid,
        },
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
