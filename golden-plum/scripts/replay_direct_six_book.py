#!/usr/bin/env python3
"""Replay Golden Plum on direct full-game snapshot evidence.

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
from typing import Mapping, Sequence

from polybot.config import SPORT_PARAMETER_PROFILES


NOTIONAL_USDC = 5.0
CAPACITY_NOTIONALS_USDC = (5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0)
FEE_BPS_SCENARIOS = (0.0, 25.0, 50.0, 100.0)
ENTRY_OVERSHOOT = 0.03
MIN_SOURCE_MINUTE = 0.0
MAX_SOURCE_MINUTE: float | None = None
FORCE_EXIT_MINUTE: float | None = None
MAX_ENTRY_SPREAD = 0.05
MIN_LEADER_MARGIN = 0.005
TREND_OBSERVATIONS = 3
TREND_MIN_MOVE = 0.02
TREND_MAX_PULLBACK = 0.01
TREND_MAX_GAP_SECONDS = 90.0
EXPECTED_SOCCER_SIX = frozenset(
    (result, side)
    for result in ("HOME", "DRAW", "AWAY")
    for side in ("YES", "NO")
)
EXPECTED_DIRECT_TWO = frozenset(
    {("HOME", "DIRECT"), ("AWAY", "DIRECT")}
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
    source_minute: float | None
    observed_at: datetime
    probability: float
    midpoint: float
    spread: float
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    sport_family: str = ""
    sport_profile_version: str = ""
    protocol_sha256: str = ""
    classifier_version: str = ""
    config_hash: str = ""
    event_cycle_id: str = ""
    event_set_complete: bool = True
    execution_capacity_json: str | None = None


@dataclass(frozen=True)
class ReplayTrade:
    event_id: str
    result_kind: str
    outcome_side: str
    entry_price: float
    entry_source_minute: float | None
    exit_price: float | None
    exit_source_minute: float | None
    exit_reason: str
    pnl_usdc: float | None
    token_id: str = ""
    condition_id: str = ""
    notional_usdc: float = NOTIONAL_USDC
    entry_filled_notional_usdc: float = NOTIONAL_USDC
    entry_residual_usdc: float = 0.0
    entry_shares: float = 0.0
    entry_full_fill: bool = True
    exit_filled_shares: float = 0.0
    exit_residual_shares: float = 0.0
    gross_proceeds_usdc: float = 0.0
    terminal_payout: float | None = None
    fee_bps: float = 0.0
    fee_usdc: float = 0.0
    right_censored: bool = False


@dataclass(frozen=True)
class DepthWalk:
    requested: float
    filled: float
    residual: float
    shares: float
    vwap: float | None
    proceeds: float

    @property
    def full_fill(self) -> bool:
        return self.residual <= 1e-9


@dataclass(frozen=True)
class CohortIdentity:
    config_hash: str
    job_name: str
    mode: str
    sport_family: str
    sport_profile_version: str
    protocol_sha256: str
    classifier_version: str
    league_mapping_sha256: str
    strategy_source_digest: str
    book_shape: str
    scaling_notionals_usdc: tuple[float, ...]


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
    walk = walk_buy_partial(asks, notional)
    if not walk.full_fill or walk.vwap is None or walk.shares <= 0:
        return None
    return walk.vwap, walk.shares


def walk_buy_partial(
    asks: Sequence[tuple[float, float]],
    notional: float,
) -> DepthWalk:
    if not math.isfinite(notional) or notional <= 0:
        raise ValueError("buy notional must be finite and positive")
    remaining = notional
    shares = 0.0
    filled = 0.0
    for price, size in asks:
        consumed = min(remaining, price * size)
        shares += consumed / price
        filled += consumed
        remaining -= consumed
        if remaining <= 1e-9:
            break
    return DepthWalk(
        requested=notional,
        filled=filled,
        residual=max(0.0, remaining),
        shares=shares,
        vwap=(filled / shares if shares > 0 else None),
        proceeds=0.0,
    )


def walk_sell(
    bids: Sequence[tuple[float, float]],
    shares: float,
) -> float | None:
    walk = walk_sell_partial(bids, shares)
    if not walk.full_fill or walk.vwap is None:
        return None
    return walk.vwap


def walk_sell_partial(
    bids: Sequence[tuple[float, float]],
    shares: float,
) -> DepthWalk:
    if not math.isfinite(shares) or shares <= 0:
        raise ValueError("sell shares must be finite and positive")
    remaining = shares
    proceeds = 0.0
    filled = 0.0
    for price, size in bids:
        consumed = min(remaining, size)
        proceeds += consumed * price
        filled += consumed
        remaining -= consumed
        if remaining <= 1e-9:
            break
    return DepthWalk(
        requested=shares,
        filled=filled,
        residual=max(0.0, remaining),
        shares=filled,
        vwap=(proceeds / filled if filled > 0 else None),
        proceeds=proceeds,
    )


def trend_confirmed(
    history: Sequence[Snapshot],
    *,
    threshold: float,
    current_snapshot_id: int,
    source_clock_required: bool = True,
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
    source_minutes = [item.source_minute for item in history]
    if source_clock_required:
        if any(value is None for value in source_minutes):
            return False
    elif any(value is None for value in source_minutes) and not all(
        value is None for value in source_minutes
    ):
        return False
    if all(value is not None for value in source_minutes) and any(
        float(source_minutes[index]) + 1e-9
        < float(source_minutes[index - 1])
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


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _require_replay_schema(connection: sqlite3.Connection) -> None:
    required = {
        "market_snapshots": {
            "run_id",
            "config_hash",
            "sport_family",
            "sport_profile_version",
            "protocol_sha256",
            "classifier_version",
            "league_mapping_sha256",
            "strategy_source_digest",
            "book_shape",
            "event_cycle_id",
            "event_set_complete",
            "execution_capacity_json",
        },
        "run_audits": {"run_id", "status", "config_hash", "job_name", "mode"},
        "strategy_configs": {"config_hash", "config_json"},
        "event_cycle_evidence": {
            "event_cycle_id",
            "run_id",
            "config_hash",
            "complete",
            "reason",
        },
        "market_sweeps": {
            "run_id",
            "config_hash",
            "sport_family",
            "sport_profile_version",
            "protocol_sha256",
            "classifier_version",
            "league_mapping_sha256",
            "strategy_source_digest",
            "book_shape",
        },
        "market_catalog": {
            "condition_id",
            "config_hash",
            "sport_family",
            "sport_profile_version",
            "protocol_sha256",
            "classifier_version",
            "league_mapping_sha256",
            "strategy_source_digest",
            "book_shape",
        },
        "tracked_resolution_observations": {
            "condition_id",
            "config_hash",
            "sport_family",
            "sport_profile_version",
            "protocol_sha256",
            "payouts_json",
            "evidence_sha256",
        },
    }
    for table, columns in required.items():
        actual = _table_columns(connection, table)
        if not actual:
            raise ValueError(f"replay evidence table is missing: {table}")
        missing = columns - actual
        if missing:
            raise ValueError(
                f"replay evidence schema is incomplete for {table}: {sorted(missing)}"
            )


def _cohort_identity(
    connection: sqlite3.Connection,
    *,
    caller_sport_family: str,
) -> CohortIdentity:
    _require_replay_schema(connection)
    total = int(
        connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    )
    if total == 0:
        raise ValueError("replay database has no market snapshots")
    invalid_runs = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM market_snapshots AS snapshot
              LEFT JOIN run_audits AS run ON run.run_id = snapshot.run_id
             WHERE run.run_id IS NULL OR run.status != 'SUCCESS'
            """
        ).fetchone()[0]
    )
    if invalid_runs:
        raise ValueError(
            "replay refuses snapshots outside successful run audits: "
            f"rows={invalid_runs}"
        )
    rows = connection.execute(
        """
        SELECT DISTINCT run.config_hash,run.job_name,run.mode,config.config_json
          FROM market_snapshots AS snapshot
          JOIN run_audits AS run ON run.run_id = snapshot.run_id
          JOIN strategy_configs AS config ON config.config_hash = run.config_hash
         WHERE run.status = 'SUCCESS'
        """
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            "replay requires one config_hash × mode × job_name cohort; "
            f"found={len(rows)}"
        )
    config_hash, job_name, mode, raw_config = rows[0]
    try:
        payload = json.loads(str(raw_config))
        trading = payload["trading"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("resolved strategy config is invalid") from error
    if not isinstance(trading, Mapping):
        raise ValueError("resolved trading config must be an object")

    def required_text(field: str) -> str:
        value = str(trading.get(field) or "").strip()
        if not value:
            raise ValueError(f"resolved trading config lacks {field}")
        return value

    family = required_text("sport_family").lower()
    if family != caller_sport_family:
        raise ValueError(
            f"caller sport family {caller_sport_family} does not match DB {family}"
        )
    profile_version = required_text("sport_profile_version")
    protocol_sha256 = required_text("preregistration_sha256")
    classifier_version = required_text("classifier_version")
    league_mapping_sha256 = required_text("league_mapping_sha256")
    strategy_source_digest = required_text("strategy_source_digest")
    book_shape = required_text("book_shape")
    profile = SPORT_PARAMETER_PROFILES[family]
    if profile.profile_version != profile_version or profile.book_shape != book_shape:
        raise ValueError("DB sport profile does not match the executable profile registry")
    raw_notionals = trading.get("scaling_notionals_usdc") or []
    try:
        notionals = tuple(float(value) for value in raw_notionals)
    except (TypeError, ValueError) as error:
        raise ValueError("resolved scaling notionals are invalid") from error
    if any(not math.isfinite(value) or value <= 0 for value in notionals):
        raise ValueError("resolved scaling notionals must be finite and positive")
    if mode == "sim" and notionals != CAPACITY_NOTIONALS_USDC:
        raise ValueError(
            "simulation replay requires the frozen 5/10/25/50/100/250/500 ladder"
        )
    if mode != "sim" and notionals:
        raise ValueError("live replay cohort unexpectedly contains a scaling ladder")

    expected_values = {
        "config_hash": str(config_hash),
        "sport_family": family,
        "sport_profile_version": profile_version,
        "protocol_sha256": protocol_sha256,
        "classifier_version": classifier_version,
        "league_mapping_sha256": league_mapping_sha256,
        "strategy_source_digest": strategy_source_digest,
        "book_shape": book_shape,
    }
    for table in ("market_snapshots", "market_sweeps", "event_cycle_evidence"):
        for column, expected in expected_values.items():
            values = {
                str(row[0] or "")
                for row in connection.execute(
                    f"SELECT DISTINCT {column} FROM {table}"
                )
            }
            if values != {expected}:
                raise ValueError(
                    f"{table} {column} is missing or mixed: {sorted(values)}"
                )
    catalog_mismatches = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM (SELECT DISTINCT condition_id FROM market_snapshots) AS used
              LEFT JOIN market_catalog AS catalog
                ON catalog.condition_id = used.condition_id
             WHERE catalog.condition_id IS NULL
                OR catalog.config_hash != ?
                OR catalog.sport_family != ?
                OR catalog.sport_profile_version != ?
                OR catalog.protocol_sha256 != ?
                OR catalog.classifier_version != ?
                OR catalog.league_mapping_sha256 != ?
                OR catalog.strategy_source_digest != ?
                OR catalog.book_shape != ?
            """,
            (
                str(config_hash),
                family,
                profile_version,
                protocol_sha256,
                classifier_version,
                league_mapping_sha256,
                strategy_source_digest,
                book_shape,
            ),
        ).fetchone()[0]
    )
    if catalog_mismatches:
        raise ValueError(
            "snapshot/catalog provenance mismatch: "
            f"conditions={catalog_mismatches}"
        )
    sweep_gaps = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM (SELECT DISTINCT run_id FROM market_snapshots) AS snapshot_run
              LEFT JOIN market_sweeps AS sweep ON sweep.run_id = snapshot_run.run_id
             WHERE sweep.run_id IS NULL
            """
        ).fetchone()[0]
    )
    if sweep_gaps:
        raise ValueError(f"successful snapshot run lacks a market sweep: {sweep_gaps}")
    broken_event_links = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM market_snapshots AS snapshot
              LEFT JOIN event_cycle_evidence AS event_cycle
                ON event_cycle.event_cycle_id = snapshot.event_cycle_id
             WHERE event_cycle.event_cycle_id IS NULL
                OR event_cycle.run_id != snapshot.run_id
                OR event_cycle.config_hash != snapshot.config_hash
                OR event_cycle.complete != snapshot.event_set_complete
                OR event_cycle.sport_family != snapshot.sport_family
                OR event_cycle.sport_profile_version != snapshot.sport_profile_version
                OR event_cycle.protocol_sha256 != snapshot.protocol_sha256
                OR event_cycle.classifier_version != snapshot.classifier_version
            """
        ).fetchone()[0]
    )
    if broken_event_links:
        raise ValueError(
            "snapshot/event-cycle provenance mismatch: "
            f"rows={broken_event_links}"
        )
    return CohortIdentity(
        config_hash=str(config_hash),
        job_name=str(job_name),
        mode=str(mode),
        sport_family=family,
        sport_profile_version=profile_version,
        protocol_sha256=protocol_sha256,
        classifier_version=classifier_version,
        league_mapping_sha256=league_mapping_sha256,
        strategy_source_digest=strategy_source_digest,
        book_shape=book_shape,
        scaling_notionals_usdc=notionals,
    )


def _validate_capacity_json(
    raw: object,
    *,
    token_id: str,
    expected_notionals: tuple[float, ...],
    bids: Sequence[tuple[float, float]],
    asks: Sequence[tuple[float, float]],
) -> None:
    if not expected_notionals:
        return
    try:
        payload = json.loads(str(raw or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("scaling snapshot lacks valid capacity JSON") from error
    if not isinstance(payload, Mapping) or str(payload.get("token_id")) != token_id:
        raise ValueError("capacity JSON token identity mismatch")
    rows = payload.get("notionals")
    if not isinstance(rows, list):
        raise ValueError("capacity JSON notionals must be a list")
    try:
        actual = tuple(float(item["notional_usdc"]) for item in rows)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("capacity JSON notional identity is invalid") from error
    if actual != expected_notionals:
        raise ValueError(
            f"capacity JSON ladder mismatch: expected={expected_notionals} actual={actual}"
        )
    if any(
        not isinstance(item.get("buy_full_fill"), bool)
        or not isinstance(item.get("sell_full_fill"), bool)
        for item in rows
    ):
        raise ValueError("capacity JSON fill flags are invalid")
    for item, notional in zip(rows, expected_notionals):
        buy = walk_buy_partial(asks, notional)
        if bool(item["buy_full_fill"]) != buy.full_fill:
            raise ValueError("capacity JSON BUY fill flag disagrees with full book")
        if buy.full_fill:
            if not math.isclose(
                float(item.get("buy_vwap")),
                float(buy.vwap),
                rel_tol=0,
                abs_tol=1e-9,
            ):
                raise ValueError("capacity JSON BUY VWAP disagrees with full book")
            sell = walk_sell_partial(bids, buy.shares)
            if bool(item["sell_full_fill"]) != sell.full_fill:
                raise ValueError("capacity JSON SELL fill flag disagrees with full book")


def load_snapshots(
    connection: sqlite3.Connection,
    *,
    sport_family: str = "soccer",
    cohort: CohortIdentity | None = None,
) -> list[Snapshot]:
    profile = SPORT_PARAMETER_PROFILES[sport_family]
    expected_identities = (
        EXPECTED_SOCCER_SIX if sport_family == "soccer" else EXPECTED_DIRECT_TWO
    )
    rows = connection.execute(
        """
        SELECT snapshot.id,snapshot.event_id,snapshot.condition_id,
               snapshot.token_id,snapshot.run_id,snapshot.result_kind,
               snapshot.outcome_side,snapshot.source_elapsed_minutes,
               snapshot.timestamp,snapshot.probability,snapshot.midpoint,
               snapshot.spread,snapshot.book_json,snapshot.sport_family,
               snapshot.sport_profile_version,snapshot.protocol_sha256,
               snapshot.classifier_version,snapshot.config_hash,
               snapshot.event_cycle_id,snapshot.event_set_complete,
               snapshot.execution_capacity_json
          FROM market_snapshots AS snapshot
          JOIN run_audits AS run ON run.run_id = snapshot.run_id
          JOIN event_cycle_evidence AS event_cycle
            ON event_cycle.event_cycle_id = snapshot.event_cycle_id
         WHERE snapshot.event_id IS NOT NULL
           AND snapshot.token_id IS NOT NULL
           AND run.status = 'SUCCESS'
           AND event_cycle.complete = 1
           AND snapshot.event_set_complete = 1
           AND snapshot.midpoint IS NOT NULL
           AND snapshot.spread IS NOT NULL
           AND snapshot.book_json IS NOT NULL
         ORDER BY snapshot.timestamp,snapshot.id
        """
    ).fetchall()
    snapshots: list[Snapshot] = []
    for row in rows:
        bids = _levels(row[12], "bids")
        asks = _levels(row[12], "asks")
        buy = walk_buy(asks)
        if not bids or buy is None:
            raise ValueError(
                f"complete event snapshot has unusable exact-$5 book: id={row[0]}"
            )
        try:
            snapshot = Snapshot(
                snapshot_id=int(row[0]),
                event_id=str(row[1]),
                condition_id=str(row[2]),
                token_id=str(row[3]),
                run_id=str(row[4]),
                result_kind=str(row[5]).upper(),
                outcome_side=str(row[6]).upper(),
                source_minute=(
                    None if row[7] is None else float(row[7])
                ),
                observed_at=_timestamp(row[8]),
                probability=float(buy[0]),
                midpoint=float(row[10]),
                spread=float(row[11]),
                bids=bids,
                asks=asks,
                sport_family=str(row[13] or ""),
                sport_profile_version=str(row[14] or ""),
                protocol_sha256=str(row[15] or ""),
                classifier_version=str(row[16] or ""),
                config_hash=str(row[17] or ""),
                event_cycle_id=str(row[18] or ""),
                event_set_complete=bool(row[19]),
                execution_capacity_json=(
                    None if row[20] is None else str(row[20])
                ),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid complete snapshot row id={row[0]}") from error
        if (snapshot.result_kind, snapshot.outcome_side) not in expected_identities:
            raise ValueError("complete snapshot has an out-of-profile direct identity")
        if snapshot.source_minute is not None and (
            not math.isfinite(snapshot.source_minute) or snapshot.source_minute < 0
        ):
            raise ValueError("complete snapshot source clock is invalid")
        if profile.source_clock_required and snapshot.source_minute is None:
            raise ValueError("complete soccer snapshot lacks source clock")
        if not 0 < snapshot.probability < 1:
            raise ValueError("complete snapshot exact-$5 price is outside (0,1)")
        if not math.isclose(snapshot.probability, buy[0], abs_tol=1e-9, rel_tol=0):
            raise ValueError("stored exact-$5 price does not match full book evidence")
        if cohort is not None:
            expected_snapshot_identity = (
                cohort.sport_family,
                cohort.sport_profile_version,
                cohort.protocol_sha256,
                cohort.classifier_version,
                cohort.config_hash,
            )
            actual_snapshot_identity = (
                snapshot.sport_family,
                snapshot.sport_profile_version,
                snapshot.protocol_sha256,
                snapshot.classifier_version,
                snapshot.config_hash,
            )
            if actual_snapshot_identity != expected_snapshot_identity:
                raise ValueError("snapshot cohort identity mismatch")
            _validate_capacity_json(
                snapshot.execution_capacity_json,
                token_id=snapshot.token_id,
                expected_notionals=cohort.scaling_notionals_usdc,
                bids=snapshot.bids,
                asks=snapshot.asks,
            )
        snapshots.append(snapshot)
    return snapshots


def load_terminal_payouts(
    connection: sqlite3.Connection,
    *,
    cohort: CohortIdentity,
) -> dict[str, float]:
    invalid = int(
        connection.execute(
            """
            SELECT COUNT(*)
              FROM tracked_resolution_observations AS resolution
              LEFT JOIN run_audits AS run ON run.run_id = resolution.run_id
             WHERE resolution.config_hash = ?
               AND (run.run_id IS NULL OR run.status != 'SUCCESS')
            """,
            (cohort.config_hash,),
        ).fetchone()[0]
    )
    if invalid:
        raise ValueError(
            "terminal evidence exists outside successful run audits: "
            f"rows={invalid}"
        )
    rows = connection.execute(
        """
        SELECT resolution.condition_id,resolution.sport_family,
               resolution.sport_profile_version,resolution.protocol_sha256,
               resolution.payouts_json,resolution.evidence_sha256
          FROM tracked_resolution_observations AS resolution
          JOIN run_audits AS run ON run.run_id = resolution.run_id
         WHERE resolution.config_hash = ? AND run.status = 'SUCCESS'
         ORDER BY resolution.observed_at,resolution.resolution_id
        """,
        (cohort.config_hash,),
    ).fetchall()
    payouts: dict[str, float] = {}
    condition_payloads: dict[str, dict[str, float]] = {}
    for condition_id, family, profile, protocol, raw, evidence_hash in rows:
        if (
            str(family) != cohort.sport_family
            or str(profile) != cohort.sport_profile_version
            or str(protocol) != cohort.protocol_sha256
            or len(str(evidence_hash or "")) != 64
        ):
            raise ValueError("terminal resolution cohort identity mismatch")
        try:
            decoded = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("terminal payout JSON is invalid") from error
        if not isinstance(decoded, Mapping) or len(decoded) != 2:
            raise ValueError("terminal payout JSON must contain two tokens")
        normalized: dict[str, float] = {}
        for token_id, raw_payout in decoded.items():
            payout = float(raw_payout)
            if payout not in (0.0, 1.0) or not str(token_id):
                raise ValueError("terminal payout is not an exact token-aligned 0/1")
            normalized[str(token_id)] = payout
        if sorted(normalized.values()) != [0.0, 1.0]:
            raise ValueError("terminal payout is not unique one-hot")
        previous = condition_payloads.get(str(condition_id))
        if previous is not None and previous != normalized:
            raise ValueError("conflicting terminal evidence for one condition")
        condition_payloads[str(condition_id)] = normalized
        for token_id, payout in normalized.items():
            prior = payouts.get(token_id)
            if prior is not None and prior != payout:
                raise ValueError("conflicting terminal payout for one token")
            payouts[token_id] = payout
    return payouts


def replay_cell(
    snapshots: Sequence[Snapshot],
    *,
    sport_family: str = "soccer",
    entry_threshold: float,
    target_price: float,
    stop_delta: float,
    observations: int = TREND_OBSERVATIONS,
    min_move: float = TREND_MIN_MOVE,
    min_source_minute: float = MIN_SOURCE_MINUTE,
    max_source_minute: float | None = MAX_SOURCE_MINUTE,
    force_exit_minute: float | None = FORCE_EXIT_MINUTE,
    terminal_payouts: Mapping[str, float] | None = None,
    notional_usdc: float = NOTIONAL_USDC,
    fee_bps: float = 0.0,
) -> list[ReplayTrade]:
    if not math.isfinite(notional_usdc) or notional_usdc <= 0:
        raise ValueError("replay notional must be finite and positive")
    if not math.isfinite(fee_bps) or fee_bps < 0:
        raise ValueError("fee sensitivity must be finite and non-negative")
    profile = SPORT_PARAMETER_PROFILES[sport_family]
    expected_identities = (
        EXPECTED_SOCCER_SIX if sport_family == "soccer" else EXPECTED_DIRECT_TWO
    )
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
        entry_walk: DepthWalk | None = None
        for group in run_groups:
            for snapshot in group:
                history[snapshot.token_id].append(snapshot)
                history[snapshot.token_id] = history[snapshot.token_id][-observations:]
            identities = {(item.result_kind, item.outcome_side) for item in group}
            if (
                identities != expected_identities
                or len({item.token_id for item in group})
                != profile.expected_token_count
            ):
                continue
            source_minutes = {item.source_minute for item in group}
            if len(source_minutes) != 1:
                continue
            source_minute = next(iter(source_minutes))
            if (
                profile.source_clock_required
                and source_minute is None
            ):
                continue
            if (
                source_minute is not None
                and source_minute < min_source_minute - 1e-9
            ):
                continue
            if (
                source_minute is not None
                and max_source_minute is not None
                and source_minute > max_source_minute + 1e-9
            ):
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
                source_clock_required=profile.source_clock_required,
                observations=observations,
                min_move=min_move,
            ):
                continue
            buy = walk_buy_partial(candidate.asks, notional_usdc)
            if buy.filled <= 1e-9 or buy.vwap is None:
                trades.append(
                    ReplayTrade(
                        event_id=event_id,
                        result_kind=candidate.result_kind,
                        outcome_side=candidate.outcome_side,
                        entry_price=candidate.probability,
                        entry_source_minute=candidate.source_minute,
                        exit_price=None,
                        exit_source_minute=None,
                        exit_reason="entry_capacity_shortfall",
                        pnl_usdc=0.0,
                        token_id=candidate.token_id,
                        condition_id=candidate.condition_id,
                        notional_usdc=notional_usdc,
                        entry_filled_notional_usdc=0.0,
                        entry_residual_usdc=notional_usdc,
                        entry_shares=0.0,
                        entry_full_fill=False,
                        fee_bps=fee_bps,
                    )
                )
                entry = None
                entry_walk = None
                break
            if buy.vwap >= target_price - 1e-9:
                continue
            entry = candidate
            entry_walk = buy
            break
        if entry is None or entry_walk is None:
            continue

        entry_fee = entry_walk.filled * fee_bps / 10_000.0
        stop_price = float(entry_walk.vwap) - stop_delta
        terminal_payout = (
            None
            if terminal_payouts is None
            else terminal_payouts.get(entry.token_id)
        )
        recorded = False
        for current in by_event_token[(event_id, entry.token_id)]:
            if current.observed_at <= entry.observed_at:
                continue
            best_bid = current.bids[0][0] if current.bids else None
            reason = None
            if best_bid is not None and best_bid + 1e-9 >= target_price:
                reason = "take_profit"
            elif best_bid is not None and best_bid <= stop_price + 1e-9:
                reason = "stop"
            elif (
                force_exit_minute is not None
                and current.source_minute is not None
                and current.source_minute + 1e-9 >= force_exit_minute
            ):
                reason = "time_exit"
            if reason is None:
                continue
            sell = walk_sell_partial(current.bids, entry_walk.shares)
            proceeds = sell.proceeds
            residual_shares = sell.residual
            final_reason = reason
            censored = False
            if residual_shares > 1e-9:
                if terminal_payout is None:
                    final_reason = f"{reason}_partial_right_censored"
                    censored = True
                else:
                    proceeds += residual_shares * terminal_payout
                    final_reason = f"{reason}_partial_then_terminal"
            exit_fee = sell.proceeds * fee_bps / 10_000.0
            fee = entry_fee + exit_fee
            pnl = (
                None
                if censored
                else proceeds - entry_walk.filled - fee
            )
            trades.append(
                ReplayTrade(
                    event_id=event_id,
                    result_kind=entry.result_kind,
                    outcome_side=entry.outcome_side,
                    entry_price=float(entry_walk.vwap),
                    entry_source_minute=entry.source_minute,
                    exit_price=sell.vwap,
                    exit_source_minute=current.source_minute,
                    exit_reason=final_reason,
                    pnl_usdc=pnl,
                    token_id=entry.token_id,
                    condition_id=entry.condition_id,
                    notional_usdc=notional_usdc,
                    entry_filled_notional_usdc=entry_walk.filled,
                    entry_residual_usdc=entry_walk.residual,
                    entry_shares=entry_walk.shares,
                    entry_full_fill=entry_walk.full_fill,
                    exit_filled_shares=sell.filled,
                    exit_residual_shares=residual_shares,
                    gross_proceeds_usdc=proceeds,
                    terminal_payout=(
                        terminal_payout if residual_shares > 1e-9 else None
                    ),
                    fee_bps=fee_bps,
                    fee_usdc=fee,
                    right_censored=censored,
                )
            )
            recorded = True
            break
        if recorded:
            continue
        if terminal_payout is not None:
            proceeds = entry_walk.shares * terminal_payout
            trades.append(
                ReplayTrade(
                    event_id=event_id,
                    result_kind=entry.result_kind,
                    outcome_side=entry.outcome_side,
                    entry_price=float(entry_walk.vwap),
                    entry_source_minute=entry.source_minute,
                    exit_price=terminal_payout,
                    exit_source_minute=None,
                    exit_reason="terminal_resolution",
                    pnl_usdc=proceeds - entry_walk.filled - entry_fee,
                    token_id=entry.token_id,
                    condition_id=entry.condition_id,
                    notional_usdc=notional_usdc,
                    entry_filled_notional_usdc=entry_walk.filled,
                    entry_residual_usdc=entry_walk.residual,
                    entry_shares=entry_walk.shares,
                    entry_full_fill=entry_walk.full_fill,
                    exit_filled_shares=0.0,
                    exit_residual_shares=0.0,
                    gross_proceeds_usdc=proceeds,
                    terminal_payout=terminal_payout,
                    fee_bps=fee_bps,
                    fee_usdc=entry_fee,
                    right_censored=False,
                )
            )
        else:
            trades.append(
                ReplayTrade(
                    event_id=event_id,
                    result_kind=entry.result_kind,
                    outcome_side=entry.outcome_side,
                    entry_price=float(entry_walk.vwap),
                    entry_source_minute=entry.source_minute,
                    exit_price=None,
                    exit_source_minute=None,
                    exit_reason="right_censored",
                    pnl_usdc=None,
                    token_id=entry.token_id,
                    condition_id=entry.condition_id,
                    notional_usdc=notional_usdc,
                    entry_filled_notional_usdc=entry_walk.filled,
                    entry_residual_usdc=entry_walk.residual,
                    entry_shares=entry_walk.shares,
                    entry_full_fill=entry_walk.full_fill,
                    exit_filled_shares=0.0,
                    exit_residual_shares=entry_walk.shares,
                    gross_proceeds_usdc=0.0,
                    terminal_payout=None,
                    fee_bps=fee_bps,
                    fee_usdc=entry_fee,
                    right_censored=True,
                )
            )
    return trades


def summarize_trades(trades: Sequence[ReplayTrade]) -> dict[str, object]:
    known = [item for item in trades if item.pnl_usdc is not None]
    pnl = sum(float(item.pnl_usdc) for item in known)
    reasons = sorted({item.exit_reason for item in trades})
    return {
        "signals": len(trades),
        "entry_full_fill": sum(item.entry_full_fill for item in trades),
        "entry_partial_fill": sum(
            item.entry_filled_notional_usdc > 0 and not item.entry_full_fill
            for item in trades
        ),
        "entry_zero_fill": sum(
            item.entry_filled_notional_usdc <= 1e-9 for item in trades
        ),
        "entry_residual_usdc": sum(item.entry_residual_usdc for item in trades),
        "exit_partial_fill": sum(
            item.exit_filled_shares > 0 and item.exit_residual_shares > 1e-9
            for item in trades
        ),
        "exit_residual_shares": sum(item.exit_residual_shares for item in trades),
        "terminal_resolution": sum(
            "terminal" in item.exit_reason for item in trades
        ),
        "right_censored": sum(item.right_censored for item in trades),
        "known_pnl_count": len(known),
        "positive_known_pnl": sum(float(item.pnl_usdc) > 0 for item in known),
        "pnl_usdc": pnl,
        "mean_known_pnl_usdc": pnl / len(known) if known else None,
        "fee_usdc": sum(item.fee_usdc for item in trades),
        "exit_reasons": {
            reason: sum(item.exit_reason == reason for item in trades)
            for reason in reasons
        },
    }


def database_report(
    path: Path,
    *,
    sport_family: str = "soccer",
    legacy_midgame_v1: bool = False,
) -> dict[str, object]:
    if legacy_midgame_v1 and sport_family != "soccer":
        raise ValueError("legacy midgame v1 replay is soccer-only")
    profile = SPORT_PARAMETER_PROFILES[sport_family]
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise ValueError(f"SQLite quick_check failed: {quick_check}")
        cohort = _cohort_identity(
            connection,
            caller_sport_family=sport_family,
        )
        snapshots = load_snapshots(
            connection,
            sport_family=sport_family,
            cohort=cohort,
        )
        terminal_payouts = load_terminal_payouts(connection, cohort=cohort)
        cutoff = connection.execute(
            "SELECT MAX(timestamp) FROM market_snapshots"
        ).fetchone()[0]
        snapshot_total = int(
            connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        )
        event_health_rows = connection.execute(
            """
            SELECT complete,reason,COUNT(*)
              FROM event_cycle_evidence
             WHERE config_hash = ?
             GROUP BY complete,reason
             ORDER BY complete DESC,reason
            """,
            (cohort.config_hash,),
        ).fetchall()
    finally:
        connection.close()
    min_source_minute = 5.0 if legacy_midgame_v1 else MIN_SOURCE_MINUTE
    max_source_minute = 75.0 if legacy_midgame_v1 else MAX_SOURCE_MINUTE
    force_exit_minute = 80.0 if legacy_midgame_v1 else FORCE_EXIT_MINUTE
    grid = []
    for entry in profile.analysis_entry_thresholds:
        for target in profile.analysis_target_prices:
            if target <= entry + ENTRY_OVERSHOOT:
                continue
            for stop in profile.analysis_stop_deltas:
                for observations in profile.analysis_trend_observations:
                    for min_move in profile.analysis_min_cumulative_moves:
                        trades = replay_cell(
                            snapshots,
                            sport_family=sport_family,
                            entry_threshold=entry,
                            target_price=target,
                            stop_delta=stop,
                            observations=observations,
                            min_move=min_move,
                            min_source_minute=min_source_minute,
                            max_source_minute=max_source_minute,
                            force_exit_minute=force_exit_minute,
                            terminal_payouts=terminal_payouts,
                        )
                        summary = summarize_trades(trades)
                        grid.append(
                            {
                                "entry_threshold": entry,
                                "target_price": target,
                                "stop_delta": stop,
                                "trend_observations": observations,
                                "trend_min_cumulative_move": min_move,
                                **summary,
                            }
                        )
    primary = {}
    for target in (0.90, 0.95):
        trades = replay_cell(
            snapshots,
            sport_family=sport_family,
            entry_threshold=0.75,
            target_price=target,
            stop_delta=0.15,
            min_source_minute=min_source_minute,
            max_source_minute=max_source_minute,
            force_exit_minute=force_exit_minute,
            terminal_payouts=terminal_payouts,
        )
        primary[f"0.75_to_{target:.2f}_stop_0.15"] = {
            "trades": [asdict(item) for item in trades],
            "summary": summarize_trades(trades),
        }
    notionals = cohort.scaling_notionals_usdc or (NOTIONAL_USDC,)
    scaling = []
    for target in profile.analysis_target_prices:
        if target <= 0.75 + ENTRY_OVERSHOOT:
            continue
        for notional in notionals:
            for fee_bps in FEE_BPS_SCENARIOS:
                trades = replay_cell(
                    snapshots,
                    sport_family=sport_family,
                    entry_threshold=0.75,
                    target_price=target,
                    stop_delta=0.15,
                    min_source_minute=min_source_minute,
                    max_source_minute=max_source_minute,
                    force_exit_minute=force_exit_minute,
                    terminal_payouts=terminal_payouts,
                    notional_usdc=notional,
                    fee_bps=fee_bps,
                )
                scaling.append(
                    {
                        "entry_threshold": 0.75,
                        "target_price": target,
                        "stop_delta": 0.15,
                        "notional_usdc": notional,
                        "fee_bps": fee_bps,
                        **summarize_trades(trades),
                    }
                )
    event_health = {
        "complete": sum(
            int(count) for complete, _reason, count in event_health_rows if complete
        ),
        "incomplete": sum(
            int(count) for complete, _reason, count in event_health_rows if not complete
        ),
        "reasons": {
            str(reason): int(count)
            for complete, reason, count in event_health_rows
            if not complete
        },
    }
    return {
        "database": str(path.resolve()),
        "sha256": _sha256(path),
        "quick_check": quick_check,
        "source_cutoff": cutoff,
        "snapshot_rows_total": snapshot_total,
        "snapshot_rows_replay_eligible": len(snapshots),
        "events": len({item.event_id for item in snapshots}),
        "sport_family": sport_family,
        "sport_profile_version": profile.profile_version,
        "book_shape": profile.book_shape,
        "contract_profile": (
            "legacy_midgame_v1"
            if legacy_midgame_v1
            else "full_game_multisport_collection_v3"
        ),
        "source_minute_window": [min_source_minute, max_source_minute],
        "force_exit_minute": force_exit_minute,
        "cohort": asdict(cohort),
        "event_cycle_health": event_health,
        "terminal_token_payouts": len(terminal_payouts),
        "capacity_notionals_usdc": list(notionals),
        "fee_bps_scenarios": list(FEE_BPS_SCENARIOS),
        "evidence_semantics": (
            "displayed full-depth counterfactual, not actual fills; stop and target "
            "trigger on best bid while proceeds use full-depth bid VWAP; partial "
            "fills/residuals, terminal payout, fee sensitivity, and right-censoring "
            "are reported explicitly"
        ),
        "primary": primary,
        "scaling": scaling,
        "grid": grid,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--sport-family",
        choices=tuple(SPORT_PARAMETER_PROFILES),
        default="soccer",
    )
    parser.add_argument(
        "--legacy-midgame-v1",
        action="store_true",
        help="Reproduce the preserved 5-75 minute / minute-80 exit v1 contract",
    )
    args = parser.parse_args()
    payload = {
        "reports": [
            database_report(
                path,
                sport_family=args.sport_family,
                legacy_midgame_v1=args.legacy_midgame_v1,
            )
            for path in args.db
        ]
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
