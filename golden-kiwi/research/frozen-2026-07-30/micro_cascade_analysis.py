#!/usr/bin/env python3
"""Reproduce the frozen Golden Kiwi / Micro-Cascade research protocol.

The repository is read-only. Outputs are written beside this script.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
PREREG = HERE / "PREREGISTRATION.md"
DB = Path(
    "/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/"
    "polybot-bear/strategies/golden-honeydew/runtime/default/"
    "databases/latest/trades.db"
)

EXPECTED_DB_SHA256 = (
    "f0ae41a1a8b88d94e0d20c307d07f3d8fa02f77022c6d8a0804bd2b00d3486df"
)
EXPECTED_PREREG_SHA256 = (
    "0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006"
)

HISTORY_START = datetime(2026, 7, 27, 15, 20, tzinfo=timezone.utc)
SIGNAL_START = datetime(2026, 7, 27, 15, 45, tzinfo=timezone.utc)
OOS_START = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
OOS_SPLIT = datetime(2026, 7, 28, 7, 0, tzinfo=timezone.utc)
OOS_SIGNAL_END = datetime(2026, 7, 28, 14, 15, tzinfo=timezone.utc)
DATA_END = datetime(2026, 7, 28, 15, 30, tzinfo=timezone.utc)

TARGET_MINUTES = 60
EXIT_TOLERANCE_MINUTES = 15
EVENT_COOLDOWN_HOURS = 6
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20_260_730
EXTRA_COST_RETURN = 0.00104  # 10.4 bps

EXCLUDED_TAGS = {
    "sports",
    "games",
    "esports",
    "crypto-prices",
    "up-or-down",
    "multi-strikes",
    "5m",
    "15m",
    "1h",
}

ARMS = {
    "A": {"steps": 3, "min_move": 0.01},
    "B": {"steps": 3, "min_move": 0.02},
    "C": {"steps": 5, "min_move": 0.01},
    "D": {"steps": 5, "min_move": 0.02},
}


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        if len(text) == 10:
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sqlite_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(" ")


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Catalog:
    condition_id: str
    event_id: str
    first_seen_at: datetime
    end_date: datetime
    tag_slugs: frozenset[str]
    binary_yes_no: bool


@dataclass(frozen=True)
class Point:
    condition_id: str
    timestamp: datetime
    probability: float
    liquidity: float | None
    volume_24h: float | None
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    run_id: str
    config_hash: str
    git_commit: str
    mode: str
    job_name: str

    @property
    def cohort(self) -> str:
        return "|".join(
            [self.config_hash, self.git_commit, self.mode, self.job_name]
        )


@dataclass
class Signal:
    arm: str
    condition_id: str
    event_id: str
    timestamp: datetime
    run_id: str
    cohort: str
    steps: int
    cumulative_move: float
    entry_probability: float
    entry_bid: float
    entry_ask: float
    entry_spread: float
    liquidity: float
    volume_24h: float
    period: str
    oos_half: str | None
    purged_oos: bool
    exit_timestamp: datetime | None = None
    exit_probability: float | None = None
    exit_bid: float | None = None
    target_delay_minutes: float | None = None
    midpoint_point_change: float | None = None
    midpoint_return: float | None = None
    executable_return: float | None = None


def open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def load_catalog(connection: sqlite3.Connection) -> dict[str, Catalog]:
    catalog: dict[str, Catalog] = {}
    query = """
        SELECT condition_id, event_id, first_seen_at, end_date,
               outcomes_json, tags_json
        FROM market_catalog
    """
    for row in connection.execute(query):
        first_seen = parse_ts(row["first_seen_at"])
        end_date = parse_ts(row["end_date"])
        if first_seen is None or end_date is None:
            continue
        try:
            outcomes = json.loads(row["outcomes_json"])
            tags = json.loads(row["tags_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        normalized_outcomes = {
            str(value).strip().lower() for value in outcomes
        }
        tag_slugs = frozenset(
            str(item.get("slug") or "").strip().lower()
            for item in tags
            if isinstance(item, dict)
        )
        condition_id = str(row["condition_id"])
        event_id = str(row["event_id"] or f"condition:{condition_id}")
        catalog[condition_id] = Catalog(
            condition_id=condition_id,
            event_id=event_id,
            first_seen_at=first_seen,
            end_date=end_date,
            tag_slugs=tag_slugs,
            binary_yes_no=normalized_outcomes == {"yes", "no"},
        )
    return catalog


def load_points(
    connection: sqlite3.Connection,
) -> tuple[dict[str, list[Point]], dict[str, int]]:
    series: dict[str, list[Point]] = defaultdict(list)
    diagnostics: dict[str, int] = defaultdict(int)
    query = """
        SELECT s.condition_id, s.timestamp, s.probability, s.liquidity,
               s.volume_24h, s.best_bid, s.best_ask, s.spread, s.run_id,
               r.config_hash, r.git_commit, r.mode, r.job_name
        FROM market_snapshots AS s
        JOIN run_audits AS r ON r.run_id = s.run_id
        WHERE s.timestamp >= ?
          AND s.timestamp < ?
          AND r.status = 'SUCCESS'
          AND r.mode = 'live'
          AND EXISTS (
              SELECT 1 FROM market_sweeps AS w
              WHERE w.run_id = s.run_id AND w.cursor_complete = 1
          )
        ORDER BY s.condition_id, s.timestamp, s.id
    """
    for row in connection.execute(
        query, (sqlite_time(HISTORY_START), sqlite_time(DATA_END))
    ):
        diagnostics["sql_rows"] += 1
        timestamp = parse_ts(row["timestamp"])
        probability = finite_number(row["probability"])
        if timestamp is None or probability is None or not 0 < probability < 1:
            diagnostics["invalid_core"] += 1
            continue
        point = Point(
            condition_id=str(row["condition_id"]),
            timestamp=timestamp,
            probability=probability,
            liquidity=finite_number(row["liquidity"]),
            volume_24h=finite_number(row["volume_24h"]),
            best_bid=finite_number(row["best_bid"]),
            best_ask=finite_number(row["best_ask"]),
            spread=finite_number(row["spread"]),
            run_id=str(row["run_id"]),
            config_hash=str(row["config_hash"]),
            git_commit=str(row["git_commit"]),
            mode=str(row["mode"]),
            job_name=str(row["job_name"]),
        )
        series[point.condition_id].append(point)
    diagnostics["conditions"] = len(series)
    return dict(series), dict(diagnostics)


def entry_eligible(point: Point, meta: Catalog | None) -> bool:
    if meta is None or not meta.binary_yes_no:
        return False
    if meta.first_seen_at > point.timestamp:
        return False
    if meta.tag_slugs & EXCLUDED_TAGS:
        return False
    if meta.end_date - point.timestamp < timedelta(hours=6):
        return False
    if not 0.20 <= point.probability <= 0.80:
        return False
    if point.liquidity is None or point.liquidity < 20_000:
        return False
    if point.volume_24h is None or point.volume_24h < 10_000:
        return False
    bid, ask = point.best_bid, point.best_ask
    if bid is None or ask is None:
        return False
    if not (0 < bid <= ask < 1):
        return False
    if ask - bid > 0.020000001:
        return False
    return True


def arm_qualifies(points: list[Point], index: int, arm: str) -> float | None:
    settings = ARMS[arm]
    steps = int(settings["steps"])
    if index < steps:
        return None
    window = points[index - steps : index + 1]
    deltas: list[float] = []
    for previous, current in zip(window, window[1:]):
        gap_minutes = (current.timestamp - previous.timestamp).total_seconds() / 60
        if not 3 <= gap_minutes <= 10:
            return None
        delta = current.probability - previous.probability
        if not 0 < delta <= 0.020000001:
            return None
        deltas.append(delta)
    cumulative = sum(deltas)
    if cumulative + 1e-12 < float(settings["min_move"]):
        return None
    if cumulative > 0.040000001:
        return None
    return cumulative


def raw_candidates(
    series: dict[str, list[Point]], catalog: dict[str, Catalog]
) -> dict[str, list[tuple[Point, Catalog, float]]]:
    candidates: dict[str, list[tuple[Point, Catalog, float]]] = {
        arm: [] for arm in ARMS
    }
    for condition_id, points in series.items():
        meta = catalog.get(condition_id)
        if meta is None:
            continue
        for index, point in enumerate(points):
            if not SIGNAL_START <= point.timestamp < OOS_SIGNAL_END:
                continue
            if not entry_eligible(point, meta):
                continue
            for arm in ARMS:
                cumulative = arm_qualifies(points, index, arm)
                if cumulative is not None:
                    candidates[arm].append((point, meta, cumulative))
    return candidates


def select_signals(
    candidates: dict[str, list[tuple[Point, Catalog, float]]]
) -> list[Signal]:
    selected: list[Signal] = []
    for arm, rows in candidates.items():
        # Sibling markets qualifying in one collection run are one event choice.
        per_run_event: dict[tuple[str, str], tuple[Point, Catalog, float]] = {}
        for point, meta, cumulative in rows:
            key = (point.run_id, meta.event_id)
            current = per_run_event.get(key)
            if current is None:
                per_run_event[key] = (point, meta, cumulative)
                continue
            current_point = current[0]
            rank = (-(point.liquidity or 0.0), point.condition_id)
            current_rank = (
                -(current_point.liquidity or 0.0),
                current_point.condition_id,
            )
            if rank < current_rank:
                per_run_event[key] = (point, meta, cumulative)

        ordered = sorted(
            per_run_event.values(),
            key=lambda item: (
                item[0].timestamp,
                -(item[0].liquidity or 0.0),
                item[0].condition_id,
            ),
        )
        last_event_signal: dict[str, datetime] = {}
        mechanics_events: set[str] = set()
        staged: list[Signal] = []
        for point, meta, cumulative in ordered:
            previous = last_event_signal.get(meta.event_id)
            if (
                previous is not None
                and point.timestamp - previous
                < timedelta(hours=EVENT_COOLDOWN_HOURS)
            ):
                continue
            last_event_signal[meta.event_id] = point.timestamp
            if point.timestamp < OOS_START:
                period = "mechanics"
                oos_half = None
                mechanics_events.add(meta.event_id)
            else:
                period = "oos"
                oos_half = (
                    "oos_early" if point.timestamp < OOS_SPLIT else "oos_late"
                )
            staged.append(
                Signal(
                    arm=arm,
                    condition_id=point.condition_id,
                    event_id=meta.event_id,
                    timestamp=point.timestamp,
                    run_id=point.run_id,
                    cohort=point.cohort,
                    steps=int(ARMS[arm]["steps"]),
                    cumulative_move=cumulative,
                    entry_probability=point.probability,
                    entry_bid=float(point.best_bid),
                    entry_ask=float(point.best_ask),
                    entry_spread=float(point.best_ask - point.best_bid),
                    liquidity=float(point.liquidity),
                    volume_24h=float(point.volume_24h),
                    period=period,
                    oos_half=oos_half,
                    purged_oos=period == "oos" and meta.event_id not in mechanics_events,
                )
            )
        # mechanics_events grows during chronological processing; recompute the
        # final strict purge label so every OOS row sees the complete mechanics set.
        mechanics_events = {
            signal.event_id for signal in staged if signal.period == "mechanics"
        }
        for signal in staged:
            if signal.period == "oos":
                signal.purged_oos = signal.event_id not in mechanics_events
        selected.extend(staged)
    return sorted(
        selected, key=lambda signal: (signal.arm, signal.timestamp, signal.event_id)
    )


def attach_exits(
    signals: list[Signal], series: dict[str, list[Point]]
) -> None:
    for signal in signals:
        target = signal.timestamp + timedelta(minutes=TARGET_MINUTES)
        latest = target + timedelta(minutes=EXIT_TOLERANCE_MINUTES)
        exit_point: Point | None = None
        for point in series.get(signal.condition_id, []):
            if point.timestamp < target:
                continue
            if point.timestamp > latest:
                break
            exit_point = point
            break
        if exit_point is None:
            continue
        signal.exit_timestamp = exit_point.timestamp
        signal.exit_probability = exit_point.probability
        signal.target_delay_minutes = (
            exit_point.timestamp - target
        ).total_seconds() / 60
        signal.midpoint_point_change = (
            exit_point.probability - signal.entry_probability
        )
        signal.midpoint_return = (
            exit_point.probability / signal.entry_probability - 1
        )
        exit_bid = exit_point.best_bid
        if (
            exit_bid is not None
            and math.isfinite(exit_bid)
            and 0 < exit_bid < 1
        ):
            signal.exit_bid = exit_bid
            signal.executable_return = exit_bid / signal.entry_ask - 1


def percentile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = quantile * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return (
        sorted_values[low] * (1.0 - weight)
        + sorted_values[high] * weight
    )


def event_means(
    signals: Iterable[Signal], field: str
) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for signal in signals:
        value = getattr(signal, field)
        if value is not None and math.isfinite(value):
            grouped[signal.event_id].append(float(value))
    return {
        event_id: statistics.fmean(values)
        for event_id, values in grouped.items()
    }


def bootstrap_intervals(
    values: list[float], seed: int
) -> dict[str, Any]:
    if not values:
        return {
            "mean": None,
            "ci95": [None, None],
            "ci98_75": [None, None],
            "bootstrap_p_le_zero": None,
            "ci_estimable": False,
        }
    mean = statistics.fmean(values)
    if len(values) == 1:
        return {
            "mean": mean,
            "ci95": [None, None],
            "ci98_75": [None, None],
            "bootstrap_p_le_zero": None,
            "ci_estimable": False,
        }
    generator = random.Random(seed)
    count = len(values)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        draws.append(
            statistics.fmean(generator.choices(values, k=count))
        )
    draws.sort()
    return {
        "mean": mean,
        "ci95": [
            percentile(draws, 0.025),
            percentile(draws, 0.975),
        ],
        "ci98_75": [
            percentile(draws, 0.00625),
            percentile(draws, 0.99375),
        ],
        "bootstrap_p_le_zero": sum(value <= 0 for value in draws)
        / len(draws),
        "ci_estimable": True,
    }


def summarize(
    signals: list[Signal],
    *,
    arm: str,
    period: str,
    strict_purge: bool = False,
    oos_half: str | None = None,
    cohort: str | None = None,
) -> dict[str, Any]:
    chosen = [
        signal
        for signal in signals
        if signal.arm == arm
        and signal.period == period
        and (not strict_purge or signal.purged_oos)
        and (oos_half is None or signal.oos_half == oos_half)
        and (cohort is None or signal.cohort == cohort)
    ]
    midpoint_complete = [
        signal for signal in chosen if signal.midpoint_return is not None
    ]
    quote_complete = [
        signal for signal in chosen if signal.executable_return is not None
    ]
    event_exec = event_means(quote_complete, "executable_return")
    event_mid = event_means(quote_complete, "midpoint_return")
    event_point = event_means(quote_complete, "midpoint_point_change")
    exec_values = [signal.executable_return for signal in quote_complete]
    point_values = [
        signal.midpoint_point_change for signal in quote_complete
    ]
    mid_values = [signal.midpoint_return for signal in quote_complete]
    seed = BOOTSTRAP_SEED + ord(arm) + len(period) + int(strict_purge)
    exec_bootstrap = bootstrap_intervals(list(event_exec.values()), seed)
    cost_bootstrap: dict[str, Any] = {}
    for key, value in exec_bootstrap.items():
        if isinstance(value, list):
            cost_bootstrap[key] = [
                item - EXTRA_COST_RETURN if item is not None else None
                for item in value
            ]
        elif key == "mean" and isinstance(value, (int, float)):
            cost_bootstrap[key] = value - EXTRA_COST_RETURN
        else:
            cost_bootstrap[key] = value
    return {
        "arm": arm,
        "period": period,
        "strict_event_purge": strict_purge,
        "oos_half": oos_half,
        "cohort": cohort,
        "signals": len(chosen),
        "signal_events": len({signal.event_id for signal in chosen}),
        "midpoint_complete": len(midpoint_complete),
        "quote_complete": len(quote_complete),
        "quote_coverage": len(quote_complete) / len(chosen) if chosen else None,
        "event_clusters_quote_complete": len(event_exec),
        "event_equal_executable": exec_bootstrap,
        "event_equal_executable_minus_10_4bps": cost_bootstrap,
        "event_equal_midpoint_return": bootstrap_intervals(
            list(event_mid.values()), seed + 100
        ),
        "event_equal_midpoint_point_change": bootstrap_intervals(
            list(event_point.values()), seed + 200
        ),
        "trade_weighted_executable_mean": (
            statistics.fmean(exec_values) if exec_values else None
        ),
        "trade_weighted_executable_median": (
            statistics.median(exec_values) if exec_values else None
        ),
        "trade_weighted_executable_win_rate": (
            sum(value > 0 for value in exec_values) / len(exec_values)
            if exec_values
            else None
        ),
        "trade_weighted_midpoint_return_mean": (
            statistics.fmean(mid_values) if mid_values else None
        ),
        "trade_weighted_point_change_mean": (
            statistics.fmean(point_values) if point_values else None
        ),
        "mean_entry_spread": (
            statistics.fmean(signal.entry_spread for signal in chosen)
            if chosen
            else None
        ),
        "mean_target_delay_minutes": (
            statistics.fmean(
                signal.target_delay_minutes
                for signal in midpoint_complete
                if signal.target_delay_minutes is not None
            )
            if midpoint_complete
            else None
        ),
    }


def cohort_rows(
    signals: list[Signal], arm: str, strict_purge: bool
) -> list[dict[str, Any]]:
    cohorts = sorted(
        {
            signal.cohort
            for signal in signals
            if signal.arm == arm
            and signal.period == "oos"
            and (not strict_purge or signal.purged_oos)
        }
    )
    return [
        summarize(
            signals,
            arm=arm,
            period="oos",
            strict_purge=strict_purge,
            cohort=cohort,
        )
        for cohort in cohorts
    ]


def serializable_signal(signal: Signal) -> dict[str, Any]:
    result = asdict(signal)
    for key in ("timestamp", "exit_timestamp"):
        value = result[key]
        result[key] = value.isoformat() if value is not None else None
    return result


def write_signal_csv(signals: list[Signal]) -> None:
    rows = [serializable_signal(signal) for signal in signals]
    if not rows:
        return
    with (HERE / "signals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def format_pct(value: float | None) -> str:
    return "NA" if value is None else f"{100 * value:.3f}%"


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Golden Kiwi / Micro-Cascade — frozen-result report",
        "",
        f"- Preregistration SHA-256: `{result['manifest']['prereg_sha256']}`",
        f"- DB SHA-256: `{result['manifest']['db_sha256']}`",
        f"- SQLite quick_check: `{result['manifest']['quick_check']}`",
        f"- Rows loaded: {result['load_diagnostics']['sql_rows']:,}",
        f"- Conditions loaded: {result['load_diagnostics']['conditions']:,}",
        "",
        "## Temporal OOS (cooldown carried across the split)",
        "",
        "| arm | signals | events | quote n | coverage | exec event mean | 95% CI | 98.75% CI | exec -10.4bps |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["summaries"]["oos"]:
        boot = row["event_equal_executable"]
        cost = row["event_equal_executable_minus_10_4bps"]
        lines.append(
            "| {arm} | {signals} | {events} | {quote} | {coverage} | "
            "{mean} | [{lo95}, {hi95}] | [{lo99}, {hi99}] | {cost_mean} |".format(
                arm=row["arm"],
                signals=row["signals"],
                events=row["signal_events"],
                quote=row["quote_complete"],
                coverage=format_pct(row["quote_coverage"]),
                mean=format_pct(boot["mean"]),
                lo95=format_pct(boot["ci95"][0]),
                hi95=format_pct(boot["ci95"][1]),
                lo99=format_pct(boot["ci98_75"][0]),
                hi99=format_pct(boot["ci98_75"][1]),
                cost_mean=format_pct(cost["mean"]),
            )
        )
    lines.extend(
        [
            "",
            "## Strict event-purged temporal OOS",
            "",
            "| arm | signals | events | quote n | coverage | exec event mean | 95% CI | 98.75% CI | exec -10.4bps |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in result["summaries"]["oos_event_purged"]:
        boot = row["event_equal_executable"]
        cost = row["event_equal_executable_minus_10_4bps"]
        lines.append(
            "| {arm} | {signals} | {events} | {quote} | {coverage} | "
            "{mean} | [{lo95}, {hi95}] | [{lo99}, {hi99}] | {cost_mean} |".format(
                arm=row["arm"],
                signals=row["signals"],
                events=row["signal_events"],
                quote=row["quote_complete"],
                coverage=format_pct(row["quote_coverage"]),
                mean=format_pct(boot["mean"]),
                lo95=format_pct(boot["ci95"][0]),
                hi95=format_pct(boot["ci95"][1]),
                lo99=format_pct(boot["ci98_75"][0]),
                hi99=format_pct(boot["ci98_75"][1]),
                cost_mean=format_pct(cost["mean"]),
            )
        )
    gate = result["primary_gate"]
    lines.extend(
        [
            "",
            "## Frozen gates",
            "",
            "| arm | decision | failed reasons |",
            "|---|---|---|",
        ]
    )
    for arm in ARMS:
        arm_gate = result["arm_gates"][arm]
        lines.append(
            f"| {arm} | {arm_gate['decision']} | "
            f"{'; '.join(arm_gate['failed_reasons']) or 'all passed'} |"
        )
    lines.extend(
        [
            "",
            f"Frozen primary Arm B result: **{gate['decision']}**.",
            "",
            "This is a top-of-book counterfactual, not confirmed execution P&L.",
            "No depth, queue, latency, actual fill, partial fill, or fee evidence is present.",
            "",
        ]
    )
    return "\n".join(lines)


def evaluate_gate(
    arm: str,
    primary: dict[str, Any],
    halves: list[dict[str, Any]],
    cohort_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    failed: list[str] = []
    if primary["quote_complete"] < 50:
        failed.append("quote-complete signals < 50")
    if primary["event_clusters_quote_complete"] < 30:
        failed.append("event clusters < 30")
    primary_ci = primary["event_equal_executable"]["ci98_75"]
    if primary_ci[0] is None:
        failed.append("98.75% executable CI not estimable")
    elif primary_ci[0] <= 0:
        failed.append("98.75% executable lower bound <= 0")
    cost_ci = primary["event_equal_executable_minus_10_4bps"]["ci98_75"]
    if cost_ci[0] is None:
        failed.append("98.75% executable -10.4bps CI not estimable")
    elif cost_ci[0] <= 0:
        failed.append("98.75% executable -10.4bps lower bound <= 0")
    if primary["quote_coverage"] is None or primary["quote_coverage"] < 0.90:
        failed.append("quote coverage < 90%")
    for half in halves:
        mean = half["event_equal_executable"]["mean"]
        if mean is None or mean <= 0:
            failed.append(f"{half['oos_half']} executable mean <= 0 or absent")
    adequately_sampled = [
        row for row in cohort_summaries if row["quote_complete"] >= 10
    ]
    if any(
        row["event_equal_executable"]["mean"] is None
        or row["event_equal_executable"]["mean"] <= 0
        for row in adequately_sampled
    ):
        failed.append("adequately sampled collection cohort sign reversal")
    return {
        "arm": arm,
        "decision": (
            "FAIL_NO_LIVE_RECOMMENDATION" if failed else "PASS_SHADOW_ONLY"
        ),
        "failed_reasons": failed,
    }


def main() -> None:
    prereg_hash = file_sha256(PREREG)
    if prereg_hash != EXPECTED_PREREG_SHA256:
        raise RuntimeError(
            f"preregistration changed: {prereg_hash} != {EXPECTED_PREREG_SHA256}"
        )
    db_hash = file_sha256(DB)
    if db_hash != EXPECTED_DB_SHA256:
        raise RuntimeError(
            f"database changed: {db_hash} != {EXPECTED_DB_SHA256}"
        )
    connection = open_readonly(DB)
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
    catalog = load_catalog(connection)
    series, load_diagnostics = load_points(connection)
    candidates = raw_candidates(series, catalog)
    signals = select_signals(candidates)
    attach_exits(signals, series)

    summaries = {
        "mechanics": [
            summarize(signals, arm=arm, period="mechanics") for arm in ARMS
        ],
        "oos": [
            summarize(signals, arm=arm, period="oos") for arm in ARMS
        ],
        "oos_event_purged": [
            summarize(
                signals,
                arm=arm,
                period="oos",
                strict_purge=True,
            )
            for arm in ARMS
        ],
        "oos_halves_event_purged": {
            arm: [
                summarize(
                    signals,
                    arm=arm,
                    period="oos",
                    strict_purge=True,
                    oos_half=half,
                )
                for half in ("oos_early", "oos_late")
            ]
            for arm in ARMS
        },
        "oos_cohorts_event_purged": {
            arm: cohort_rows(signals, arm, strict_purge=True)
            for arm in ARMS
        },
    }
    strict_by_arm = {
        row["arm"]: row for row in summaries["oos_event_purged"]
    }
    arm_gates = {
        arm: evaluate_gate(
            arm,
            strict_by_arm[arm],
            summaries["oos_halves_event_purged"][arm],
            summaries["oos_cohorts_event_purged"][arm],
        )
        for arm in ARMS
    }

    result = {
        "manifest": {
            "preregistration": str(PREREG),
            "prereg_sha256": prereg_hash,
            "database": str(DB),
            "db_sha256": db_hash,
            "quick_check": quick_check,
            "source_cutoff": "2026-07-28T15:42:05.414525Z",
            "synced_at": "2026-07-30T12:13:51.689096Z",
            "latest_successful_sync_finished_at": (
                "2026-07-30T12:14:27.341243Z"
            ),
            "analysis_generated_at": datetime.now(timezone.utc).isoformat(),
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_draws": BOOTSTRAP_DRAWS,
        },
        "load_diagnostics": load_diagnostics,
        "catalog_conditions": len(catalog),
        "raw_candidate_counts": {
            arm: len(rows) for arm, rows in candidates.items()
        },
        "selected_signal_counts": {
            arm: sum(signal.arm == arm for signal in signals)
            for arm in ARMS
        },
        "summaries": summaries,
        "primary_gate": arm_gates["B"],
        "arm_gates": arm_gates,
        "limitations": [
            "One sub-day full-cadence regime; compact-v1 makes older rows 12-hour rollups.",
            "Snapshot probability and quotes are observational; no order was submitted.",
            "Top-of-book proxy lacks depth, queue, latency, partial fills, and actual fee.",
            "Catalog tags/end dates are stable-identity joins from the synchronized snapshot, not point-in-time catalog versions.",
            "Nested arms are dependent and are not alternative winners if primary Arm B fails.",
        ],
    }
    connection.close()
    (HERE / "results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    write_signal_csv(signals)
    (HERE / "RESULTS.md").write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps(result["primary_gate"], indent=2))
    for row in result["summaries"]["oos_event_purged"]:
        print(
            row["arm"],
            "signals=", row["signals"],
            "events=", row["event_clusters_quote_complete"],
            "exec=", row["event_equal_executable"],
        )


if __name__ == "__main__":
    main()
