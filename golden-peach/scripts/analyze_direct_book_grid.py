#!/usr/bin/env python3
"""Replay one explicit Golden Peach cohort's direct CLOB books, read-only.

This is a read-only, displayed-book counterfactual.  It uses the first durable
entry episode per event, exact-$5 entry VWAP, subsequent full bid depth, and the
catalogued sports taker-fee formula.  It is not actual fill or realized P&L.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import statistics
from typing import Sequence


TAKE_PROFIT_DELTAS = (0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
STOP_LOSS_DELTAS = (0.05, 0.07, 0.10, 0.15, 0.20)
NOTIONAL_USDC = 5.0
BOOTSTRAP_SAMPLES = 10_000
EPSILON = 1e-9
ELIGIBLE_EPISODE_STATES = (
    "TRADE_CREATED",
    "BLOCKED_GUARD",
)
SIMULATION_GUARD_BUG_REASON = "open_buy_fill_or_fee_evidence_gap"


class EvidenceContractError(ValueError):
    """The requested population cannot be reproduced from this database."""


@dataclass(frozen=True)
class Cohort:
    config_hash: str
    source_digest: str
    job_name: str
    mode: str
    sport_family: str


@dataclass(frozen=True)
class ExitPolicy:
    late_exit_minute: float
    late_profit_fraction: float
    stop_cutoff_minute: float
    max_exit_spread: float
    max_snapshot_gap_minutes: float


def _utc(value: str, *, require_timezone: bool = False) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceContractError("invalid UTC timestamp") from error
    if parsed.tzinfo is None:
        if require_timezone:
            raise EvidenceContractError("review timestamps must include a timezone")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: object, name: str, *, minimum: float = 0) -> float:
    if value is None or isinstance(value, bool):
        raise EvidenceContractError(f"missing/invalid resolved {name}")
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise EvidenceContractError(f"invalid resolved {name}") from error
    if not math.isfinite(number) or number < minimum:
        raise EvidenceContractError(f"invalid resolved {name}")
    return number


def _load_contract(connection: sqlite3.Connection, cohort: Cohort) -> dict:
    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in (cohort.config_hash, cohort.source_digest)
    ):
        raise EvidenceContractError(
            "exact 64-character config/source SHA-256 selectors are required"
        )
    row = connection.execute(
        "SELECT strategy_name,mode,config_json FROM strategy_configs WHERE config_hash=?",
        (cohort.config_hash,),
    ).fetchone()
    if row is None:
        raise EvidenceContractError(
            "requested config_hash not found; use --list-cohorts"
        )
    try:
        config = json.loads(row["config_json"])
        trading = config["trading"]
        entry = trading["entry"]
        profile = trading["sport_profile_version"]
    except (TypeError, KeyError, json.JSONDecodeError) as error:
        raise EvidenceContractError("resolved config contract is incomplete") from error
    if (
        row["strategy_name"] != "golden-peach"
        or config.get("strategy_name") != "golden-peach"
        or row["mode"] != cohort.mode
        or config.get("mode") != cohort.mode
        or cohort.mode not in {"sim", "live"}
        or trading.get("strategy_source_digest") != cohort.source_digest
        or trading.get("sport_family") != cohort.sport_family
        or not isinstance(profile, str)
        or not profile
    ):
        raise EvidenceContractError("config/source/mode/sport identity mismatch")
    family = cohort.sport_family
    if family not in {"soccer", "mlb", "nba", "nfl", "nhl"}:
        raise EvidenceContractError("unsupported sport profile")
    shape = (
        "direct-six-result-books" if family == "soccer" else "direct-two-team-moneyline"
    )
    if trading.get("book_shape") != shape:
        raise EvidenceContractError("sport and direct-book shape disagree")
    if _number(trading.get("buy_amount_usdc"), "buy_amount_usdc") != NOTIONAL_USDC:
        raise EvidenceContractError("this replay supports the baseline $5 cohort only")
    policy = ExitPolicy(
        late_exit_minute=_number(entry.get("late_exit_minute"), "late_exit_minute"),
        late_profit_fraction=_number(
            entry.get("late_profit_fraction"), "late_profit_fraction"
        ),
        stop_cutoff_minute=_number(
            entry.get("stop_cutoff_minute"), "stop_cutoff_minute"
        ),
        max_exit_spread=_number(entry.get("max_stop_spread"), "max_stop_spread"),
        max_snapshot_gap_minutes=_number(
            trading.get("max_snapshot_gap_minutes"),
            "max_snapshot_gap_minutes",
            minimum=1e-9,
        ),
    )
    if policy.late_profit_fraction > 1 or policy.max_exit_spread > 1:
        raise EvidenceContractError("invalid fractional exit policy")
    for field in (
        "take_profit_delta",
        "stop_loss_delta",
        "prob_min",
        "prob_max",
        "max_source_minute",
    ):
        _number(entry.get(field), field)
    if not connection.execute(
        "SELECT 1 FROM run_audits WHERE config_hash=? AND job_name=? AND mode=? AND strategy_name='golden-peach' LIMIT 1",
        (cohort.config_hash, cohort.job_name, cohort.mode),
    ).fetchone():
        raise EvidenceContractError(
            "requested runtime job/mode has no runs for this config"
        )
    return {
        "trading": trading,
        "entry": entry,
        "profile": profile,
        "shape": shape,
        "tokens": 6 if family == "soccer" else 2,
        "policy": policy,
    }


def _same_run(row: sqlite3.Row, cohort: Cohort) -> bool:
    return (
        row["run_config_hash"] == cohort.config_hash
        and row["run_job_name"] == cohort.job_name
        and row["run_mode"] == cohort.mode
        and row["run_status"] == "SUCCESS"
        and row["run_strategy"] == "golden-peach"
    )


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
    evidence_gap: str | None = None


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


def _execution_fee(*, shares: float, price: float, fee_rate: float) -> float:
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
    asks = _levels(payload.get("asks"), reverse=False)
    if not asks:
        return None
    best_ask = asks[0][0]
    recorded_ask = _finite_price(row["best_ask"])
    if recorded_ask is None or not math.isclose(recorded_ask, best_ask, abs_tol=1e-6):
        return None
    spread = None if best_ask is None else best_ask - best_bid
    if spread is not None and (spread < -EPSILON or not math.isfinite(spread)):
        return None
    source_minute = row["source_elapsed_minutes"]
    try:
        source_minute = float(source_minute) if source_minute is not None else None
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


def _walk_buy(raw_book: str) -> tuple[float, float] | None:
    try:
        book = json.loads(raw_book)
    except (TypeError, json.JSONDecodeError):
        return None
    asks = _levels(book.get("asks"), reverse=False) if isinstance(book, dict) else ()
    remaining, shares = NOTIONAL_USDC, 0.0
    for price, size in asks:
        spent = min(remaining, price * size)
        shares += spent / price
        remaining -= spent
        if remaining <= EPSILON:
            return NOTIONAL_USDC / shares, shares
    return None


def _profile_matches(row, cohort: Cohort, contract: dict) -> bool:
    return (
        row["sport_family"] == cohort.sport_family
        and row["sport_profile_version"] == contract["profile"]
        and row["book_shape"] == contract["shape"]
    )


def _read_entries(
    connection: sqlite3.Connection,
    *,
    cohort: Cohort,
    contract: dict,
    review_start: datetime,
    review_end: datetime,
) -> tuple[list[Entry], dict]:
    # Rank across the whole event history first. Selecting a later cohort must
    # not convert an event's second recorded attempt into a new first entry.
    rows = connection.execute(
        """
        WITH eligible AS (
            SELECT episode.*, ROW_NUMBER() OVER (
                PARTITION BY event_id ORDER BY observed_at, id
            ) AS event_rank
            FROM entry_episodes AS episode
            WHERE execution_state='TRADE_CREATED' OR
                  (execution_state='BLOCKED_GUARD' AND execution_reason=:guard_reason)
        )
        SELECT e.*, s.outcome_side, s.result_kind, s.probability, s.book_json,
               s.sport_family, s.sport_profile_version, s.book_shape,
               s.source_clock_reason, s.run_id AS snapshot_run_id,
               s.event_id AS snapshot_event_id, s.condition_id AS snapshot_condition_id,
               s.token_id AS snapshot_token_id, s.outcome AS snapshot_outcome,
               s.timestamp AS snapshot_timestamp,
               c.fees_enabled, c.fee_rate, c.fee_exponent, c.fee_taker_only,
               r.config_hash AS run_config_hash, r.job_name AS run_job_name,
               r.mode AS run_mode, r.status AS run_status,
               r.strategy_name AS run_strategy
        FROM eligible e
        LEFT JOIN market_snapshots s ON s.id=e.entry_snapshot_id
        LEFT JOIN market_catalog c ON c.condition_id=e.condition_id
        LEFT JOIN run_audits r ON r.run_id=s.run_id
        WHERE e.event_rank=1
        ORDER BY e.observed_at,e.event_id
        """,
        {"guard_reason": SIMULATION_GUARD_BUG_REASON},
    ).fetchall()
    entries: list[Entry] = []
    exclusions: Counter[str] = Counter()
    for row in rows:
        if not review_start <= _utc(row["observed_at"]) < review_end:
            continue
        if not _same_run(row, cohort):
            exclusions["OTHER_OR_UNPROVEN_ENTRY_COHORT"] += 1
            continue
        if row["execution_state"] == "BLOCKED_GUARD" and cohort.mode != "sim":
            exclusions["LIVE_GUARD_BLOCK_IS_NOT_SIMULATION_ENTRY"] += 1
            continue
        if not _profile_matches(row, cohort, contract):
            raise EvidenceContractError(
                "entry sport/profile/shape differs from selected cohort"
            )
        if (
            not row["event_id"]
            or row["event_id"] != row["snapshot_event_id"]
            or row["condition_id"] != row["snapshot_condition_id"]
            or row["token_id"] != row["snapshot_token_id"]
            or row["outcome"] != row["snapshot_outcome"]
            or not 0
            <= (
                _utc(row["observed_at"]) - _utc(row["snapshot_timestamp"])
            ).total_seconds()
            <= contract["policy"].max_snapshot_gap_minutes * 60
        ):
            exclusions["ENTRY_SNAPSHOT_IDENTITY_OR_TIME_GAP"] += 1
            continue
        event_books = connection.execute(
            "SELECT * FROM market_snapshots WHERE run_id=? AND event_id=?",
            (row["snapshot_run_id"], row["event_id"]),
        ).fetchall()
        expected_kinds = (
            {"HOME", "DRAW", "AWAY"}
            if cohort.sport_family == "soccer"
            else {"HOME", "AWAY"}
        )
        if (
            len(event_books) != contract["tokens"]
            or len({s["token_id"] for s in event_books}) != contract["tokens"]
            or len({s["condition_id"] for s in event_books}) != contract["tokens"] // 2
            or len({(s["result_kind"], s["outcome_side"]) for s in event_books})
            != contract["tokens"]
            or {s["result_kind"] for s in event_books} != expected_kinds
            or any(
                not _profile_matches(s, cohort, contract)
                or not s["book_json"]
                or _utc(s["timestamp"]) != _utc(row["snapshot_timestamp"])
                for s in event_books
            )
        ):
            exclusions["INCOMPLETE_DIRECT_EVENT_BOOK_SET"] += 1
            continue
        source_minute = _number(row["source_elapsed_minutes"], "source_elapsed_minutes")
        if source_minute > contract["entry"]["max_source_minute"] + EPSILON:
            exclusions["OUTSIDE_RECORDED_ENTRY_CLOCK_WINDOW"] += 1
            continue
        reason = str(row["source_clock_reason"] or "")
        if (cohort.sport_family == "soccer" and not reason.startswith("SOURCE_")) or (
            cohort.sport_family != "soccer"
            and reason != "SCHEDULED_START_AGE_SHADOW_ONLY"
        ):
            exclusions["CLOCK_SEMANTICS_UNPROVEN"] += 1
            continue
        walked = _walk_buy(row["book_json"])
        if walked is None:
            exclusions["BASELINE_5_ASK_DEPTH_MISSING"] += 1
            continue
        entry_vwap, shares = walked
        if (
            not math.isclose(entry_vwap, row["probability"], abs_tol=1e-6)
            or not math.isclose(entry_vwap, row["exact_vwap"], abs_tol=1e-6)
            or not contract["entry"]["prob_min"] - EPSILON
            <= entry_vwap
            <= contract["entry"]["prob_max"] + EPSILON
        ):
            exclusions["BASELINE_ENTRY_VWAP_MISMATCH"] += 1
            continue
        if row["fees_enabled"] not in (0, 1):
            exclusions["FEE_ENABLEMENT_UNPROVEN"] += 1
            continue
        fee_rate = 0.0
        if row["fees_enabled"]:
            try:
                fee_rate = _number(row["fee_rate"], "fee_rate")
            except EvidenceContractError:
                exclusions["FEE_RATE_MISSING_OR_INVALID"] += 1
                continue
            if row["fee_exponent"] != 1 or row["fee_taker_only"] != 1:
                exclusions["FEE_SCHEDULE_OUTSIDE_SUPPORTED_CONTRACT"] += 1
                continue
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
                execution_reason=row["execution_reason"],
                trade_id=row["trade_id"],
            )
        )
    return entries, {"count": sum(exclusions.values()), "reasons": dict(exclusions)}


def _read_paths(
    connection: sqlite3.Connection,
    entries: Sequence[Entry],
    *,
    cohort: Cohort,
    contract: dict,
    as_of: datetime,
) -> dict[str, list[BookObservation]]:
    if not entries:
        return {}
    expected = {e.token_id: e for e in entries}
    placeholders = ",".join("?" for _ in expected)
    rows = connection.execute(
        f"""
        SELECT s.*, r.config_hash AS run_config_hash,r.job_name AS run_job_name,
               r.mode AS run_mode,r.status AS run_status,r.strategy_name AS run_strategy
        FROM market_snapshots s LEFT JOIN run_audits r ON r.run_id=s.run_id
        WHERE s.token_id IN ({placeholders}) AND julianday(s.timestamp)<julianday(?)
        ORDER BY s.token_id,s.timestamp,s.id
        """,
        [*expected, as_of.isoformat()],
    ).fetchall()
    paths: dict[str, list[BookObservation]] = defaultdict(list)
    for row in rows:
        entry = expected[row["token_id"]]
        if _utc(row["timestamp"]) <= _utc(entry.observed_at):
            continue
        gap = None
        if not _same_run(row, cohort):
            gap = "COHORT_OR_SUCCESS_BOUNDARY"
        elif not _profile_matches(row, cohort, contract):
            gap = "SPORT_PROFILE_BOUNDARY"
        elif (
            row["event_id"] != entry.event_id
            or row["condition_id"] != entry.condition_id
            or row["outcome"] != entry.outcome
        ):
            gap = "TOKEN_EVENT_IDENTITY_GAP"
        book = _decode_book(row) if gap is None else None
        if book is None:
            # Keep the gap as an explicit censoring boundary: dropping it would
            # allow a later profitable quote to hide an earlier unknown exit.
            book = BookObservation(
                str(row["timestamp"]),
                None,
                0.0,
                None,
                None,
                (),
                evidence_gap=gap or "RAW_BOOK_EVIDENCE_GAP",
            )
        paths[entry.token_id].append(book)
    # Failed cycles often have no snapshot at all. Filtering the snapshot
    # table alone could bridge that failure with a profitable later quote.
    boundaries = connection.execute(
        "SELECT started_at,status,config_hash FROM run_audits "
        "WHERE job_name=? AND mode=? AND strategy_name='golden-peach' "
        "AND julianday(started_at)<julianday(?) "
        "AND (status!='SUCCESS' OR config_hash!=?) ORDER BY started_at",
        (cohort.job_name, cohort.mode, as_of.isoformat(), cohort.config_hash),
    ).fetchall()
    for entry in entries:
        for row in boundaries:
            if _utc(row["started_at"]) > _utc(entry.observed_at):
                paths[entry.token_id].append(
                    BookObservation(
                        row["started_at"],
                        None,
                        0.0,
                        None,
                        None,
                        (),
                        evidence_gap="RUN_WITHOUT_SUCCESS_OR_SAME_COHORT",
                    )
                )
        paths[entry.token_id].sort(key=lambda book: _utc(book.observed_at))
    return dict(paths)


def _evaluate(
    entry: Entry,
    path: Sequence[BookObservation],
    *,
    take_profit_delta: float,
    stop_loss_delta: float,
    policy: ExitPolicy,
) -> ExitResult | None:
    normal_target = min(0.999, entry.entry_vwap + take_profit_delta)
    late_target = min(
        0.999,
        entry.entry_vwap + take_profit_delta * policy.late_profit_fraction,
    )
    stop_trigger = max(0.01, entry.entry_vwap - stop_loss_delta)
    buy_fee = _execution_fee(
        shares=entry.shares,
        price=entry.entry_vwap,
        fee_rate=entry.fee_rate,
    )
    previous_at = _utc(entry.observed_at)
    for observation in path:
        observed_at = _utc(observation.observed_at)
        if observed_at <= _utc(entry.observed_at):
            continue
        if (
            observation.evidence_gap
            or (observed_at - previous_at).total_seconds()
            > policy.max_snapshot_gap_minutes * 60 + EPSILON
        ):
            return None
        previous_at = observed_at
        if (
            observation.spread is None
            or observation.spread < -EPSILON
            or observation.spread > policy.max_exit_spread + EPSILON
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
            and observation.source_minute + EPSILON >= policy.late_exit_minute
            and sell_vwap + EPSILON >= late_target
        ):
            reason = "LATE_HALF_TARGET"
        elif (
            observation.source_minute is not None
            and observation.source_minute < policy.stop_cutoff_minute - EPSILON
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
    policy: ExitPolicy,
) -> dict:
    results: list[ExitResult] = []
    censored_events: list[str] = []
    for entry in entries:
        result = _evaluate(
            entry,
            paths.get(entry.token_id, ()),
            take_profit_delta=take_profit_delta,
            stop_loss_delta=stop_loss_delta,
            policy=policy,
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


def list_cohorts(path: Path) -> list[dict]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                """
            SELECT r.config_hash,
                   json_extract(c.config_json,'$.trading.strategy_source_digest') source_digest,
                   r.job_name,r.mode,
                   json_extract(c.config_json,'$.trading.sport_family') sport_family,
                   json_extract(c.config_json,'$.trading.sport_profile_version') sport_profile_version,
                   min(r.started_at) first_run,max(r.started_at) last_run,count(*) runs
            FROM run_audits r JOIN strategy_configs c USING(config_hash)
            WHERE r.strategy_name='golden-peach'
            GROUP BY r.config_hash,r.job_name,r.mode ORDER BY first_run
            """
            )
        ]


def analyze(
    path: Path,
    *,
    cohort: Cohort,
    review_start: str,
    review_end_exclusive: str,
    as_of: str | None = None,
) -> dict:
    start = _utc(review_start, require_timezone=True)
    end = _utc(review_end_exclusive, require_timezone=True)
    followup = _utc(as_of or review_end_exclusive, require_timezone=True)
    if not start < end <= followup:
        raise EvidenceContractError(
            "require review_start < review_end_exclusive <= as_of"
        )
    path = path.resolve()
    source_sha = _sha256(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        checks = [row[0] for row in connection.execute("PRAGMA quick_check")]
        if checks != ["ok"]:
            raise EvidenceContractError("database quick_check failed")
        contract = _load_contract(connection, cohort)
        entries, exclusions = _read_entries(
            connection,
            cohort=cohort,
            contract=contract,
            review_start=start,
            review_end=end,
        )
        paths = _read_paths(
            connection, entries, cohort=cohort, contract=contract, as_of=followup
        )
        grid = [
            _grid_row(
                entries,
                paths,
                take_profit_delta=tp,
                stop_loss_delta=sl,
                policy=contract["policy"],
            )
            for tp in TAKE_PROFIT_DELTAS
            for sl in STOP_LOSS_DELTAS
        ]
        complete_grid = sorted(
            (row for row in grid if row["coverage_pct"] == 100.0),
            key=lambda row: (
                -row["fee_net_mean_return_pct"],
                row["take_profit_delta"],
                row["stop_loss_delta"],
            ),
        )
        configured_policy = _grid_row(
            entries,
            paths,
            take_profit_delta=contract["entry"]["take_profit_delta"],
            stop_loss_delta=contract["entry"]["stop_loss_delta"],
            policy=contract["policy"],
        )
        selected_runs = connection.execute(
            "SELECT started_at,status FROM run_audits WHERE config_hash=? AND job_name=? AND mode=? "
            "AND julianday(started_at)>=julianday(?) AND julianday(started_at)<julianday(?) ORDER BY started_at",
            (
                cohort.config_hash,
                cohort.job_name,
                cohort.mode,
                start.isoformat(),
                followup.isoformat(),
            ),
        ).fetchall()
        source_cutoff = connection.execute(
            "SELECT max(started_at) FROM run_audits WHERE config_hash=? AND job_name=? AND mode=?",
            (cohort.config_hash, cohort.job_name, cohort.mode),
        ).fetchone()[0]
    finally:
        connection.close()
    if source_sha != _sha256(path):
        raise EvidenceContractError(
            "source changed during analysis; take a new verified snapshot"
        )
    sides = dict(sorted(Counter(e.outcome_side for e in entries).items()))
    limitations = [
        "표시 호가 재생이며 실제 체결·실현 손익·체결 보장이 아니다.",
        "기존 durable episode로 선택된 event만 재생한다. 놓친 경기와 진입 기준 변경 효과를 복원하지 않는다.",
        "원본 $5 ask를 재생하며 별도로 갱신한 실제/가상 주문 가격과 수량을 대신 쓰지 않는다.",
        "설정·소스·종목 경계, 실패 run, 원본 book 결손 또는 설정된 최대 간격을 넘은 경로는 이후 가격으로 메우지 않고 중단한다.",
        "가격이 목표에 닿지 않거나 증거가 끊긴 경기는 미완결이다. resolution payout을 추정하지 않는다.",
        "수수료는 저장된 catalog의 알려진 sports taker 공식 가정이다. 당시 체결 수수료나 시점별 수수료 변경 증거가 아니다.",
        "매개변수별 결과와 신뢰구간은 여러 값을 시험한 탐색값이다. 독립 표본 검증이나 최적값 선언이 아니다.",
    ]
    if cohort.sport_family != "soccer":
        limitations.append(
            "이 종목의 시계는 예정 시작 후 경과시간이다. 실제 이닝/쿼터/피리어드 시계가 아니다."
        )
    if sides and len(sides) == 1:
        limitations.append(
            f"선택된 표본의 outcome_side는 {next(iter(sides))}만 있다. 다른 쪽의 성과를 일반화하지 않는다."
        )
    return {
        "schema": "golden-peach-direct-book-grid-v2",
        "analysis_status": "EXPLORATORY_ONLY" if entries else "NO_ELIGIBLE_EVIDENCE",
        "interpretation": "DISPLAYED_BOOK_COUNTERFACTUAL_NOT_ACTUAL_FILL_OR_REALIZED_PNL",
        "database": {
            "path": str(path),
            "sha256": source_sha,
            "quick_check": "ok",
            "source_cutoff": source_cutoff,
        },
        "cohort": asdict(cohort),
        "range": {
            "entry_start_inclusive": start.isoformat(),
            "entry_end_exclusive": end.isoformat(),
            "path_end_exclusive": followup.isoformat(),
        },
        "contract": {
            "notional_usdc": NOTIONAL_USDC,
            "sport_profile_version": contract["profile"],
            "book_shape": contract["shape"],
            "expected_token_count": contract["tokens"],
            "exit_policy": asdict(contract["policy"]),
            "take_profit_deltas": list(TAKE_PROFIT_DELTAS),
            "stop_loss_deltas": list(STOP_LOSS_DELTAS),
            "fee_formula": "shares * fee_rate * price * (1-price)",
            "entry_price_basis": "original entry snapshot full-$5 ask, not trade fill",
        },
        "collection": {
            "selected_run_statuses": dict(Counter(r["status"] for r in selected_runs)),
            "maximum_selected_run_gap_seconds": max(
                (
                    (_utc(b["started_at"]) - _utc(a["started_at"])).total_seconds()
                    for a, b in zip(selected_runs, selected_runs[1:])
                ),
                default=None,
            ),
            "path_boundaries": dict(
                Counter(
                    b.evidence_gap
                    for seq in paths.values()
                    for b in seq
                    if b.evidence_gap
                )
            ),
        },
        "limitations": limitations,
        "episodes": {
            "selected_unique_events": len(entries),
            "selected_execution_states": dict(
                sorted(Counter(e.execution_state for e in entries).items())
            ),
            "selected_sides": sides,
            "selected_result_kinds": dict(
                sorted(Counter(e.result_kind for e in entries).items())
            ),
            "exclusions": exclusions,
            "entries": [asdict(e) for e in entries],
        },
        "configured_policy": configured_policy,
        "complete_grid_ranked": complete_grid,
        "grid": grid,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--list-cohorts",
        action="store_true",
        help="list exact persisted selectors without replay",
    )
    parser.add_argument("--config-hash")
    parser.add_argument("--source-digest")
    parser.add_argument("--job")
    parser.add_argument("--mode", choices=("live", "sim"))
    parser.add_argument(
        "--sport-family", choices=("soccer", "mlb", "nba", "nfl", "nhl")
    )
    parser.add_argument("--review-start")
    parser.add_argument("--review-end-exclusive")
    parser.add_argument(
        "--as-of", help="exclusive path follow-up cutoff; defaults to entry end"
    )
    args = parser.parse_args()
    if not args.list_cohorts:
        required = (
            "config_hash",
            "source_digest",
            "job",
            "mode",
            "sport_family",
            "review_start",
            "review_end_exclusive",
        )
        missing = [name for name in required if not getattr(args, name)]
        if missing:
            parser.error(
                "explicit cohort and UTC range required: "
                + ", ".join("--" + v.replace("_", "-") for v in missing)
            )
    return args


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"missing database: {args.db}")
    if args.output is not None and (
        args.output.resolve() == args.db.resolve()
        or (args.output.exists() and args.output.samefile(args.db))
    ):
        raise SystemExit("output must not overwrite the source database")
    try:
        report = (
            {"cohorts": list_cohorts(args.db)}
            if args.list_cohorts
            else analyze(
                args.db,
                cohort=Cohort(
                    args.config_hash,
                    args.source_digest,
                    args.job,
                    args.mode,
                    args.sport_family,
                ),
                review_start=args.review_start,
                review_end_exclusive=args.review_end_exclusive,
                as_of=args.as_of,
            )
        )
    except (EvidenceContractError, sqlite3.Error) as error:
        raise SystemExit(f"evidence contract rejected: {error}") from error
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
