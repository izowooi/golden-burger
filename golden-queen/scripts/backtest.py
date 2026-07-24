#!/usr/bin/env python3
"""Offline, replayable research backtest for Golden Queen.

The input is an immutable CSV export.  This module deliberately imports no bot
database or API client: it cannot discover, open, or mutate a live trading DB.

Required CSV columns
--------------------
``event_id,condition_id,timestamp,hours_left,yes_price,best_ask,best_bid,liquidity,volume_24h,resolution_payout,outcomes,token_ids,yes_token_id,neg_risk``

``timestamp`` must be ISO-8601.  ``resolution_payout`` is the authoritative YES
payout (0, 0.5, or 1) and must be consistent for every row of a condition.  Entries
use the first *crossing* of the configured threshold, pay the observed ask,
and absolute exits receive the observed bid. These are explicitly hypothetical
research fills: they make no claim that a live GTC order was accepted or that an exact
``order_fills.status=CONFIRMED`` row exists.  Positions that do not stop are
valued only at the resolution payout; no trailing or time exit exists. The CSV
does not contain full depth levels or sports ``gameStartTime`` evidence, so this
tool cannot prove the production depth or sports-clock gates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence


ENGINE_VERSION = "queen-offline-v5"
ENTRY_GRID = (0.90,)
ENTRY_UPPER_GRID = (0.94,)
STOP_GRID = (0.85,)
TAKE_PROFIT_GRID = (0.98,)
ORDER_NOTIONAL_USDC = 100.0
MIN_LIQUIDITY_FLOOR = 10_000.0
MAX_ORDER_LIQUIDITY_RATIO = 0.001
MIN_VOLUME_24H_FLOOR = 2_000.0
MAX_ORDER_VOLUME_RATIO = 0.02
LIQUIDITY_GRID = (
    max(
        MIN_LIQUIDITY_FLOOR,
        ORDER_NOTIONAL_USDC / MAX_ORDER_LIQUIDITY_RATIO,
    ),
)
VOLUME_GRID = (
    max(
        MIN_VOLUME_24H_FLOOR,
        ORDER_NOTIONAL_USDC / MAX_ORDER_VOLUME_RATIO,
    ),
)
HOURS_MAX_GRID = (12.0, 24.0)
ENTRY_HOURS_MIN = 0.0
MAX_SNAPSHOT_GAP_MINUTES = 15.0
MAX_SPREAD = 0.02
MAX_OPEN_POSITIONS = 20

REQUIRED_COLUMNS = frozenset(
    {
        "event_id",
        "condition_id",
        "timestamp",
        "hours_left",
        "yes_price",
        "best_ask",
        "best_bid",
        "liquidity",
        "volume_24h",
        "resolution_payout",
        "outcomes",
        "token_ids",
        "yes_token_id",
        "neg_risk",
    }
)


@dataclass(frozen=True)
class Observation:
    event_id: str
    condition_id: str
    timestamp: datetime
    hours_left: float
    yes_price: float
    best_ask: float
    best_bid: float
    liquidity: float
    volume_24h: float
    resolution_payout: float
    outcomes: tuple[str, str]
    token_ids: tuple[str, str]
    yes_token_id: str
    neg_risk: bool


@dataclass(frozen=True)
class Parameters:
    entry_probability: float
    entry_price_max: float
    stop_probability: float
    take_profit_probability: float
    min_liquidity: float
    min_volume_24h: float
    entry_hours_max: float


@dataclass(frozen=True)
class TradeResult:
    event_id: str
    condition_id: str
    split: str
    entry_timestamp: str
    exit_timestamp: str
    entry_signal_price: float
    entry_ask: float
    exit_signal_price: float
    exit_value: float
    exit_reason: str
    resolution_payout: float
    liquidity_at_entry: float
    volume_24h_at_entry: float
    pnl_per_share: float
    midpoint_pnl_per_share: float
    observed_book_pnl_per_share: float
    confirmed_fill_pnl_per_share: float | None
    execution_evidence: str
    return_on_cost: float
    pnl_after_fee_10bps: float
    pnl_after_fee_50bps: float
    pnl_after_slippage_10bps: float
    entry_probability: float
    entry_price_max: float
    stop_probability: float
    take_profit_probability: float
    min_liquidity: float
    min_volume_24h: float
    entry_hours_max: float


@dataclass(frozen=True)
class ReviewWindow:
    start: datetime
    end_exclusive: datetime

    def contains(self, timestamp: datetime) -> bool:
        return self.start <= timestamp < self.end_exclusive

    @property
    def end_inclusive_date(self) -> str:
        return (self.end_exclusive.date() - timedelta(days=1)).isoformat()


def _finite_float(value: str, *, field: str, line: int) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"line {line}: {field} must be numeric") from error
    if not math.isfinite(number):
        raise ValueError(f"line {line}: {field} must be finite")
    return number


def _parse_timestamp(value: str, *, line: int) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError(f"line {line}: timestamp must not be blank")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"line {line}: timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_json_string_pair(value: str, *, field: str, line: int) -> tuple[str, str]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"line {line}: {field} must be a JSON string array") from error
    if (
        not isinstance(parsed, list)
        or len(parsed) != 2
        or any(not isinstance(item, str) or not item.strip() for item in parsed)
    ):
        raise ValueError(f"line {line}: {field} must contain exactly two strings")
    return parsed[0].strip(), parsed[1].strip()


def _parse_neg_risk(value: str, *, line: int) -> bool:
    text = str(value).strip().lower()
    if text not in {"false", "0"}:
        raise ValueError(f"line {line}: neg_risk must explicitly be false")
    return False


def build_review_window(review_start: str, review_end: str) -> ReviewWindow:
    """Build an inclusive UTC-date review window with a half-open end."""
    try:
        start_date = date.fromisoformat(review_start)
        end_date = date.fromisoformat(review_end)
    except ValueError as error:
        raise ValueError("review_start/review_end must be YYYY-MM-DD") from error
    if end_date < start_date:
        raise ValueError("review_end must be on or after review_start")
    return ReviewWindow(
        start=datetime.combine(start_date, time.min, tzinfo=timezone.utc),
        end_exclusive=datetime.combine(
            end_date + timedelta(days=1), time.min, tzinfo=timezone.utc
        ),
    )


def _validate_observation(observation: Observation, *, line: int) -> None:
    if not observation.event_id:
        raise ValueError(f"line {line}: event_id must not be blank")
    if not observation.condition_id:
        raise ValueError(f"line {line}: condition_id must not be blank")
    if observation.hours_left < 0:
        raise ValueError(f"line {line}: hours_left must be >= 0")
    for field in ("yes_price", "best_ask", "best_bid"):
        value = getattr(observation, field)
        if not 0 <= value <= 1:
            raise ValueError(f"line {line}: {field} must be between 0 and 1")
    if observation.best_bid > observation.best_ask:
        raise ValueError(f"line {line}: best_bid must be <= best_ask")
    if observation.liquidity < 0 or observation.volume_24h < 0:
        raise ValueError(f"line {line}: liquidity and volume_24h must be >= 0")
    if observation.resolution_payout not in {0.0, 0.5, 1.0}:
        raise ValueError(f"line {line}: resolution_payout must be 0, 0.5, or 1")
    if observation.outcomes != ("Yes", "No"):
        raise ValueError(f"line {line}: outcomes must be exactly [\"Yes\", \"No\"]")
    if len(set(observation.token_ids)) != 2:
        raise ValueError(f"line {line}: token_ids must be two distinct values")
    if observation.yes_token_id != observation.token_ids[0]:
        raise ValueError(f"line {line}: yes_token_id must match the Yes outcome token")
    if observation.neg_risk:
        raise ValueError(f"line {line}: neg_risk markets are forbidden")


def read_research_csv(path: Path) -> dict[str, list[Observation]]:
    """Read and strictly validate a research export, grouped by condition."""
    if path.suffix.lower() != ".csv":
        raise ValueError("research input must be a CSV file")
    grouped: dict[str, list[Observation]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"research CSV missing columns: {missing}")
        for line, row in enumerate(reader, start=2):
            observation = Observation(
                event_id=str(row["event_id"]).strip(),
                condition_id=str(row["condition_id"]).strip(),
                timestamp=_parse_timestamp(row["timestamp"], line=line),
                hours_left=_finite_float(row["hours_left"], field="hours_left", line=line),
                yes_price=_finite_float(row["yes_price"], field="yes_price", line=line),
                best_ask=_finite_float(row["best_ask"], field="best_ask", line=line),
                best_bid=_finite_float(row["best_bid"], field="best_bid", line=line),
                liquidity=_finite_float(row["liquidity"], field="liquidity", line=line),
                volume_24h=_finite_float(
                    row["volume_24h"], field="volume_24h", line=line
                ),
                resolution_payout=_finite_float(
                    row["resolution_payout"], field="resolution_payout", line=line
                ),
                outcomes=_parse_json_string_pair(
                    row["outcomes"], field="outcomes", line=line
                ),
                token_ids=_parse_json_string_pair(
                    row["token_ids"], field="token_ids", line=line
                ),
                yes_token_id=str(row["yes_token_id"]).strip(),
                neg_risk=_parse_neg_risk(row["neg_risk"], line=line),
            )
            _validate_observation(observation, line=line)
            grouped.setdefault(observation.condition_id, []).append(observation)

    if not grouped:
        raise ValueError("research CSV contains no observations")
    for condition_id, observations in grouped.items():
        observations.sort(key=lambda item: item.timestamp)
        timestamps = [item.timestamp for item in observations]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError(f"{condition_id}: duplicate timestamps are not allowed")
        payouts = {item.resolution_payout for item in observations}
        if len(payouts) != 1:
            raise ValueError(f"{condition_id}: resolution_payout must be consistent")
        event_ids = {item.event_id for item in observations}
        if len(event_ids) != 1:
            raise ValueError(f"{condition_id}: event_id must be consistent")
        universe_contracts = {
            (item.outcomes, item.token_ids, item.yes_token_id, item.neg_risk)
            for item in observations
        }
        if len(universe_contracts) != 1:
            raise ValueError(f"{condition_id}: strict binary universe fields must be consistent")
    return grouped


@dataclass(frozen=True)
class TimeSplit:
    cutoff: datetime
    event_assignments: dict[str, str]


def build_time_split(
    grouped: dict[str, list[Observation]], train_fraction: float
) -> TimeSplit:
    """Return a deterministic chronological event-level split cutoff.

    Events are ordered by their last observation (the closest available proxy
    for resolution time).  Every condition in one event therefore remains on
    the same side, preventing related-market leakage between train and test.
    """
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    event_endpoints: dict[str, datetime] = {}
    for observations in grouped.values():
        event_id = observations[0].event_id
        endpoint = observations[-1].timestamp
        event_endpoints[event_id] = max(event_endpoints.get(event_id, endpoint), endpoint)
    endpoints = sorted(event_endpoints.values())
    train_count = max(1, min(len(endpoints) - 1, int(len(endpoints) * train_fraction)))
    if len(endpoints) < 2:
        raise ValueError("time split requires at least two events")
    cutoff = endpoints[train_count - 1]
    assignments = {
        event_id: "train" if endpoint <= cutoff else "test"
        for event_id, endpoint in event_endpoints.items()
    }
    return TimeSplit(cutoff=cutoff, event_assignments=assignments)


def split_cutoff(
    grouped: dict[str, list[Observation]], train_fraction: float
) -> datetime:
    """Backward-compatible convenience accessor for the chronological cutoff."""
    return build_time_split(grouped, train_fraction).cutoff


def replay_market(
    observations: Sequence[Observation],
    params: Parameters,
    time_split: TimeSplit,
    review_window: ReviewWindow | None = None,
) -> TradeResult | None:
    """Replay one market and return its first-crossing trade, if any."""
    if not observations:
        return None
    previous = observations[0]
    entry_index: int | None = None
    for index, current in enumerate(observations[1:], start=1):
        crossed = (
            previous.yes_price < params.entry_probability
            and current.yes_price >= params.entry_probability
        )
        if crossed:
            gap_minutes = (current.timestamp - previous.timestamp).total_seconds() / 60
            current_in_review = review_window is None or review_window.contains(
                current.timestamp
            )
            executable = (
                params.entry_probability <= current.yes_price <= params.entry_price_max
                and params.entry_probability
                <= current.best_ask
                <= params.entry_price_max
                and current.best_ask - current.best_bid <= MAX_SPREAD
            )
            in_window = (
                ENTRY_HOURS_MIN < current.hours_left <= params.entry_hours_max
            )
            liquid = (
                current.liquidity >= params.min_liquidity
                and current.volume_24h >= params.min_volume_24h
            )
            lineage_fresh = 0 < gap_minutes <= MAX_SNAPSHOT_GAP_MINUTES
            # Consume the first observed crossing even if a later gate fails.
            # A later re-crossing must never be relabelled as the first one.
            if current_in_review and executable and in_window and liquid and lineage_fresh:
                entry_index = index
                break
            return None
        previous = current
    if entry_index is None:
        return None

    entry = observations[entry_index]
    exit_observation = observations[-1]
    exit_value = entry.resolution_payout
    exit_signal_price = entry.resolution_payout
    exit_reason = "resolution"
    for current in observations[entry_index + 1 :]:
        # Signal is the YES reference price. The bid is execution only.
        if (
            current.yes_price >= params.take_profit_probability
            and current.best_bid >= params.take_profit_probability
        ):
            exit_observation = current
            exit_value = current.best_bid
            exit_signal_price = current.yes_price
            exit_reason = "take_profit"
            break
        if current.yes_price <= params.stop_probability:
            exit_observation = current
            exit_value = current.best_bid
            exit_signal_price = current.yes_price
            exit_reason = "stop_loss"
            break

    pnl = exit_value - entry.best_ask
    midpoint_pnl = exit_signal_price - entry.yes_price
    fee_notional = entry.best_ask + exit_value
    fee_10bps = fee_notional * 0.001
    fee_50bps = fee_notional * 0.005
    slippage_10bps = (entry.best_ask + exit_value) * 0.001
    return TradeResult(
        event_id=entry.event_id,
        condition_id=entry.condition_id,
        split=time_split.event_assignments[entry.event_id],
        entry_timestamp=entry.timestamp.isoformat().replace("+00:00", "Z"),
        exit_timestamp=exit_observation.timestamp.isoformat().replace("+00:00", "Z"),
        entry_signal_price=entry.yes_price,
        entry_ask=entry.best_ask,
        exit_signal_price=exit_signal_price,
        exit_value=exit_value,
        exit_reason=exit_reason,
        resolution_payout=entry.resolution_payout,
        liquidity_at_entry=entry.liquidity,
        volume_24h_at_entry=entry.volume_24h,
        pnl_per_share=pnl,
        midpoint_pnl_per_share=midpoint_pnl,
        observed_book_pnl_per_share=pnl,
        confirmed_fill_pnl_per_share=None,
        execution_evidence="hypothetical_observed_book",
        return_on_cost=pnl / entry.best_ask,
        pnl_after_fee_10bps=pnl - fee_10bps,
        pnl_after_fee_50bps=pnl - fee_50bps,
        pnl_after_slippage_10bps=pnl - slippage_10bps,
        entry_probability=params.entry_probability,
        entry_price_max=params.entry_price_max,
        stop_probability=params.stop_probability,
        take_profit_probability=params.take_profit_probability,
        min_liquidity=params.min_liquidity,
        min_volume_24h=params.min_volume_24h,
        entry_hours_max=params.entry_hours_max,
    )


def parameter_grid() -> Iterable[Parameters]:
    for entry in ENTRY_GRID:
        for entry_upper in ENTRY_UPPER_GRID:
            if entry_upper < entry:
                continue
            for stop in STOP_GRID:
                if stop >= entry:
                    continue
                for take_profit in TAKE_PROFIT_GRID:
                    if take_profit <= entry_upper:
                        continue
                    for liquidity in LIQUIDITY_GRID:
                        for volume in VOLUME_GRID:
                            for hours_max in HOURS_MAX_GRID:
                                yield Parameters(
                                    entry,
                                    entry_upper,
                                    stop,
                                    take_profit,
                                    liquidity,
                                    volume,
                                    hours_max,
                                )


def replay_parameter(
    grouped: dict[str, list[Observation]],
    params: Parameters,
    time_split: TimeSplit,
    review_window: ReviewWindow | None = None,
) -> list[TradeResult]:
    """Replay one parameter set with simultaneous event/global position caps."""
    candidates = [
        result
        for observations in grouped.values()
        if (
            result := replay_market(
                observations, params, time_split, review_window=review_window
            )
        ) is not None
    ]
    candidates.sort(
        key=lambda trade: (trade.entry_timestamp, trade.event_id, trade.condition_id)
    )
    selected: list[TradeResult] = []
    active: list[TradeResult] = []
    for candidate in candidates:
        entry_at = _parse_timestamp(candidate.entry_timestamp, line=0)
        active = [
            trade
            for trade in active
            if _parse_timestamp(trade.exit_timestamp, line=0) > entry_at
        ]
        if any(trade.event_id == candidate.event_id for trade in active):
            continue
        if len(active) >= MAX_OPEN_POSITIONS:
            continue
        selected.append(candidate)
        active.append(candidate)
    return selected


def _summarize(
    params: Parameters,
    trades: Sequence[TradeResult],
    review_window: ReviewWindow | None = None,
) -> dict:
    row = asdict(params)
    row["review_start"] = review_window.start.date().isoformat() if review_window else None
    row["review_end"] = review_window.end_inclusive_date if review_window else None
    for split in ("train", "test", "all"):
        selected = list(trades) if split == "all" else [t for t in trades if t.split == split]
        count = len(selected)
        total_pnl = sum(t.pnl_per_share for t in selected)
        row[f"{split}_trades"] = count
        row[f"{split}_pnl_per_share"] = total_pnl
        row[f"{split}_observed_book_pnl_per_share"] = sum(
            t.observed_book_pnl_per_share for t in selected
        )
        row[f"{split}_midpoint_pnl_per_share"] = sum(
            t.midpoint_pnl_per_share for t in selected
        )
        confirmed = [
            t.confirmed_fill_pnl_per_share
            for t in selected
            if t.confirmed_fill_pnl_per_share is not None
        ]
        row[f"{split}_confirmed_fill_trades"] = len(confirmed)
        row[f"{split}_confirmed_fill_pnl_per_share"] = (
            sum(confirmed) if confirmed else None
        )
        row[f"{split}_mean_return"] = (
            sum(t.return_on_cost for t in selected) / count if count else None
        )
        row[f"{split}_win_rate"] = (
            sum(t.pnl_per_share > 0 for t in selected) / count if count else None
        )
        for sensitivity in (
            "pnl_after_fee_10bps",
            "pnl_after_fee_50bps",
            "pnl_after_slippage_10bps",
        ):
            row[f"{split}_{sensitivity}"] = sum(
                getattr(trade, sensitivity) for trade in selected
            )
    return row


def run_grid(
    grouped: dict[str, list[Observation]],
    time_split: TimeSplit,
    review_window: ReviewWindow | None = None,
) -> tuple[list[TradeResult], list[dict], list[dict]]:
    """Run the declared grid and return detailed trades plus summary rows."""
    all_trades: list[TradeResult] = []
    summaries: list[dict] = []
    monthly_summaries: list[dict] = []
    for params in parameter_grid():
        trades = replay_parameter(
            grouped, params, time_split, review_window=review_window
        )
        all_trades.extend(trades)
        summaries.append(_summarize(params, trades, review_window))
        by_month: dict[str, list[TradeResult]] = {}
        for trade in trades:
            by_month.setdefault(trade.entry_timestamp[:7], []).append(trade)
        for month, month_trades in sorted(by_month.items()):
            row = _summarize(params, month_trades, review_window)
            row["entry_month_utc"] = month
            monthly_summaries.append(row)
    return all_trades, summaries, monthly_summaries


def _json_ready(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite output is forbidden")
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _write_json(path: Path, rows) -> None:
    path.write_text(
        json.dumps(_json_ready(rows), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(
    path: Path, rows: Sequence[dict], *, fieldnames: Sequence[str] | None = None
) -> None:
    resolved_fields = list(fieldnames or (list(rows[0]) if rows else ()))
    if not resolved_fields:
        raise ValueError(f"CSV schema is required: {path.name}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=resolved_fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifacts(
    *,
    input_path: Path,
    output_dir: Path,
    grouped: dict[str, list[Observation]],
    time_split: TimeSplit,
    trades: Sequence[TradeResult],
    summaries: Sequence[dict],
    monthly_summaries: Sequence[dict],
    train_fraction: float,
    review_window: ReviewWindow | None,
) -> Path:
    """Write research-only artifacts and a SHA-256 manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    trade_rows = [asdict(trade) for trade in trades]
    artifacts = {
        "trades.csv": output_dir / "trades.csv",
        "trades.json": output_dir / "trades.json",
        "grid.csv": output_dir / "grid.csv",
        "grid.json": output_dir / "grid.json",
        "monthly_grid.csv": output_dir / "monthly_grid.csv",
        "monthly_grid.json": output_dir / "monthly_grid.json",
    }
    _write_csv(
        artifacts["trades.csv"],
        trade_rows,
        fieldnames=list(TradeResult.__dataclass_fields__),
    )
    _write_json(artifacts["trades.json"], [asdict(trade) for trade in trades])
    _write_csv(artifacts["grid.csv"], list(summaries))
    _write_json(artifacts["grid.json"], list(summaries))
    monthly_fields = list(summaries[0]) + ["entry_month_utc"]
    _write_csv(
        artifacts["monthly_grid.csv"],
        list(monthly_summaries),
        fieldnames=monthly_fields,
    )
    _write_json(artifacts["monthly_grid.json"], list(monthly_summaries))

    manifest = {
        "schema_version": 2,
        "engine_version": ENGINE_VERSION,
        "input": {
            "path": str(input_path.resolve()),
            "sha256": sha256_file(input_path),
            "markets": len(grouped),
            "observations": sum(len(rows) for rows in grouped.values()),
            "strict_universe_columns": [
                "outcomes",
                "token_ids",
                "yes_token_id",
                "neg_risk",
            ],
        },
        "review_window": (
            {
                "start_utc": review_window.start.isoformat().replace("+00:00", "Z"),
                "end_exclusive_utc": review_window.end_exclusive.isoformat().replace(
                    "+00:00", "Z"
                ),
                "entry_cohort": "first_observed_crossing_timestamp",
            }
            if review_window
            else None
        ),
        "time_split": {
            "train_fraction": train_fraction,
            "cutoff": time_split.cutoff.isoformat().replace("+00:00", "Z"),
            "assignment": "event_last_observation",
            "event_assignments": dict(sorted(time_split.event_assignments.items())),
        },
        "assumptions": {
            "entry": "first yes_price crossing; hypothetical fill at observed best_ask",
            "exit": "absolute stop/target; hypothetical fill at observed best_bid",
            "terminal": "authoritative resolution_payout; no trailing/time exit",
            "execution_views": {
                "midpoint": "signal-price counterfactual",
                "observed_book": "hypothetical ask/bid execution",
                "confirmed_fill": "null here; requires exact ledger join",
            },
            "sensitivity": "10/50 bps fee and 10 bps additional book slippage",
            "entry_hours_min": ENTRY_HOURS_MIN,
            "max_snapshot_gap_minutes": MAX_SNAPSHOT_GAP_MINUTES,
            "max_spread": MAX_SPREAD,
            "event_cap": "one simultaneously open condition per event",
            "global_position_cap": MAX_OPEN_POSITIONS,
        },
        "evidence_scope": {
            "research_only": True,
            "hypothetical_fills": True,
            "live_order_fill_claims": False,
            "live_fill_requirement": "exact order_fills.status=CONFIRMED",
            "strict_standard_binary_required": True,
        },
        "limitations": {
            "confirmed_fill_view": "not inferred; join the immutable output to exact order/fill evidence",
            "capital_sizing": "per-share only; dollar cash availability is not simulated",
            "book_depth": "full CLOB depth levels are absent, so production depth gate is not replayed",
            "sports_clock": "gameStartTime and in-play phase are absent; hours_left must already use the intended clock",
        },
        "grid": {
            "order_notional_usdc": ORDER_NOTIONAL_USDC,
            "entry": list(ENTRY_GRID),
            "entry_upper": list(ENTRY_UPPER_GRID),
            "stop": list(STOP_GRID),
            "take_profit": list(TAKE_PROFIT_GRID),
            "liquidity": list(LIQUIDITY_GRID),
            "volume_24h": list(VOLUME_GRID),
            "hours_max": list(HOURS_MAX_GRID),
        },
        "rows": {
            "trades": len(trades),
            "grid": len(summaries),
            "monthly_grid": len(monthly_summaries),
        },
        "artifacts": {
            name: {"sha256": sha256_file(path)} for name, path in artifacts.items()
        },
        "safety": {
            "database_writes": False,
            "network_calls": False,
            "live_order_submission": False,
        },
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def run(
    input_path: Path,
    output_dir: Path,
    train_fraction: float = 0.7,
    review_start: str | None = None,
    review_end: str | None = None,
) -> Path:
    if (review_start is None) != (review_end is None):
        raise ValueError("review_start and review_end must be supplied together")
    review_window = (
        build_review_window(review_start, review_end)
        if review_start is not None and review_end is not None
        else None
    )
    grouped = read_research_csv(input_path)
    time_split = build_time_split(grouped, train_fraction)
    trades, summaries, monthly_summaries = run_grid(
        grouped, time_split, review_window=review_window
    )
    return write_artifacts(
        input_path=input_path,
        output_dir=output_dir,
        grouped=grouped,
        time_split=time_split,
        trades=trades,
        summaries=summaries,
        monthly_summaries=monthly_summaries,
        train_fraction=train_fraction,
        review_window=review_window,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    parser.add_argument("--review-start", help="inclusive UTC date (YYYY-MM-DD)")
    parser.add_argument("--review-end", help="inclusive UTC date (YYYY-MM-DD)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = run(
        args.input_csv,
        args.output_dir,
        args.train_fraction,
        args.review_start,
        args.review_end,
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
