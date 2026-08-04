#!/usr/bin/env python3
"""Read-only A/B post-mortem for Golden Blueberry databases.

The analyzer never contacts Polymarket and never mutates a bot database. It
keeps confirmed CLOB round trips separate from resolution-based settlement
assumptions and refuses to pool multiple runtime-source cohorts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import fmean
from typing import Any, Sequence


EXPECTED_SURGE = {"A": 0.02, "B": 0.05}
MIN_CONFIRMED_CLOSED_PER_ARM = 20


def _window(start: str, end: str) -> tuple[str, str]:
    """Return SQLite-compatible UTC-naive bounds for an inclusive date range.

    SQLAlchemy stores ``DateTime`` values in SQLite as ``YYYY-MM-DD HH:MM:SS``.
    ISO strings containing ``T`` and ``+00:00`` do not sort in the same lexical
    domain, so the analyzer deliberately binds the same representation used by
    the database.
    """
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as error:
        raise ValueError("review dates must be YYYY-MM-DD") from error
    if end_date < start_date:
        raise ValueError("review end must not precede start")
    begin = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    finish = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    return (
        begin.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        finish.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _display_utc(value: str) -> str:
    """Render a SQLite UTC-naive timestamp as an explicit UTC instant."""
    return f"{value.replace(' ', 'T')}Z"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"database not found: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        connection.close()
        raise ValueError(f"database integrity check failed: {path}: {integrity}")
    required = {"trades", "entry_signal_decisions", "run_audits", "strategy_configs"}
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required - tables)
    if missing:
        connection.close()
        raise ValueError(f"database missing evidence tables: {missing}")
    return connection


def _cohorts(
    connection: sqlite3.Connection, start: str, end: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ra.config_hash, ra.job_name, sc.config_json,
               COUNT(*) AS successful_runs
        FROM run_audits ra
        JOIN strategy_configs sc ON sc.config_hash = ra.config_hash
        WHERE ra.strategy_name = 'golden-blueberry'
          AND ra.mode = 'live'
          AND ra.status = 'SUCCESS'
          AND ra.started_at >= ? AND ra.started_at < ?
        GROUP BY ra.config_hash, ra.job_name, sc.config_json
        ORDER BY ra.config_hash, ra.job_name
        """,
        (start, end),
    ).fetchall()
    cohorts: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["config_json"])
        trading = payload.get("trading") or {}
        entry = trading.get("entry") or {}
        # The preregistered treatment is exactly one field.  Comparing only
        # source digests would still allow accidental arm differences in risk,
        # execution, timing, or archive settings.  Hash the complete resolved
        # trading contract after removing the one permitted treatment field.
        common_trading = json.loads(json.dumps(trading))
        common_entry = common_trading.get("entry") or {}
        common_entry.pop("min_surge", None)
        common_trading["entry"] = common_entry
        common_contract_sha256 = hashlib.sha256(
            json.dumps(
                common_trading,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cohorts.append(
            {
                "config_hash": row["config_hash"],
                "successful_runs": int(row["successful_runs"]),
                "min_surge": float(entry["min_surge"]),
                "common_contract_sha256": common_contract_sha256,
                "strategy_source_digest": str(
                    trading.get("strategy_source_digest") or ""
                ),
                "job_name": str(row["job_name"]),
                "mode": str(payload.get("mode") or ""),
            }
        )
    return cohorts


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _arm_metrics(
    path: Path, label: str, start: str, end: str
) -> dict[str, Any]:
    connection = _connect(path)
    try:
        cohorts = _cohorts(connection, start, end)
        signals = connection.execute(
            """
            SELECT decision, reason, surge
            FROM entry_signal_decisions
            WHERE observed_at >= ? AND observed_at < ?
            """,
            (start, end),
        ).fetchall()
        trades = connection.execute(
            """
            SELECT status, buy_confirmed_size, buy_confirmed_vwap,
                   buy_confirmed_fee_usdc, sell_confirmed_size,
                   sell_confirmed_vwap, sell_confirmed_fee_usdc,
                   settlement_pnl_assumption
            FROM trades
            WHERE buy_timestamp >= ? AND buy_timestamp < ?
              AND mode = 'live'
            """,
            (start, end),
        ).fetchall()

        gross_round_trips: list[float] = []
        net_round_trips: list[float] = []
        buy_confirmed = 0
        closed_confirmed = 0
        for row in trades:
            buy_size = _safe_float(row["buy_confirmed_size"])
            buy_vwap = _safe_float(row["buy_confirmed_vwap"])
            if buy_size is not None and buy_size > 0 and buy_vwap is not None:
                buy_confirmed += 1
            sell_size = _safe_float(row["sell_confirmed_size"])
            sell_vwap = _safe_float(row["sell_confirmed_vwap"])
            if not all(
                value is not None
                for value in (buy_size, buy_vwap, sell_size, sell_vwap)
            ):
                continue
            size = min(float(buy_size), float(sell_size))
            if size <= 0:
                continue
            closed_confirmed += 1
            gross = (float(sell_vwap) - float(buy_vwap)) * size
            gross_round_trips.append(gross)
            buy_fee = _safe_float(row["buy_confirmed_fee_usdc"])
            sell_fee = _safe_float(row["sell_confirmed_fee_usdc"])
            if buy_fee is not None and sell_fee is not None:
                net_round_trips.append(gross - buy_fee - sell_fee)

        settlement_values = [
            value
            for row in trades
            if (value := _safe_float(row["settlement_pnl_assumption"])) is not None
        ]
        reasons = Counter(str(row["reason"]) for row in signals)
        surges = [float(row["surge"]) for row in signals]
        return {
            "label": label,
            "database": str(path.resolve()),
            "database_sha256": _sha256(path),
            "cohorts": cohorts,
            "signals": {
                "total_first_crossings": len(signals),
                "candidates": sum(row["decision"] == "candidate" for row in signals),
                "rejected": sum(row["decision"] == "rejected" for row in signals),
                "reasons": dict(sorted(reasons.items())),
                "mean_surge": fmean(surges) if surges else None,
            },
            "trades": {
                "submitted": len(trades),
                "confirmed_buy": buy_confirmed,
                "confirmed_closed": closed_confirmed,
                "fee_complete_closed": len(net_round_trips),
                "confirmed_gross_pnl_usdc": sum(gross_round_trips),
                "confirmed_net_pnl_usdc": (
                    sum(net_round_trips) if net_round_trips else None
                ),
                "confirmed_net_mean_usdc": (
                    fmean(net_round_trips) if net_round_trips else None
                ),
                "confirmed_net_win_rate": (
                    sum(value > 0 for value in net_round_trips)
                    / len(net_round_trips)
                    if net_round_trips
                    else None
                ),
                "resolution_settlement_assumption_pnl_usdc": sum(settlement_values),
                "status_counts": dict(
                    sorted(Counter(str(row["status"]) for row in trades).items())
                ),
            },
        }
    finally:
        connection.close()


def analyze(
    arm_a: Path,
    arm_b: Path,
    output_dir: Path,
    review_start: str,
    review_end: str,
) -> Path:
    if arm_a.resolve() == arm_b.resolve():
        raise ValueError("A/B arms must use different database files")
    start, end = _window(review_start, review_end)
    arms = {
        "A": _arm_metrics(arm_a, "A", start, end),
        "B": _arm_metrics(arm_b, "B", start, end),
    }
    issues: list[str] = []
    source_digests: set[str] = set()
    common_contracts: set[str] = set()
    job_names: set[str] = set()
    for label, arm in arms.items():
        cohorts = arm["cohorts"]
        if len(cohorts) != 1:
            issues.append(f"arm_{label}_cohort_count_{len(cohorts)}")
            continue
        cohort = cohorts[0]
        if not math.isclose(cohort["min_surge"], EXPECTED_SURGE[label]):
            issues.append(f"arm_{label}_unexpected_min_surge")
        digest = cohort["strategy_source_digest"]
        if len(digest) != 64:
            issues.append(f"arm_{label}_source_digest_invalid")
        else:
            source_digests.add(digest)
        common_contracts.add(cohort["common_contract_sha256"])
        job_names.add(cohort["job_name"])
    if len(source_digests) > 1:
        issues.append("arms_have_different_strategy_source_digest")
    if len(common_contracts) > 1:
        issues.append("arms_have_different_common_contract")
    if len(job_names) == 1 and all(arm["cohorts"] for arm in arms.values()):
        issues.append("arms_share_job_name")

    sample_ready = all(
        arm["trades"]["confirmed_closed"] >= MIN_CONFIRMED_CLOSED_PER_ARM
        for arm in arms.values()
    )
    fee_ready = all(
        arm["trades"]["fee_complete_closed"]
        == arm["trades"]["confirmed_closed"]
        for arm in arms.values()
    )
    status = (
        "NOT_EVALUABLE_EVIDENCE_CONTRACT"
        if issues
        else "INSUFFICIENT_CONFIRMED_SAMPLE"
        if not sample_ready
        else "NOT_EVALUABLE_FEE_GAP"
        if not fee_ready
        else "EVALUABLE_NO_AUTOMATIC_WINNER"
    )
    display_start = _display_utc(start)
    display_end = _display_utc(end)
    result = {
        "schema_version": 1,
        "strategy": "golden-blueberry",
        "review_window": {
            "start_utc": display_start,
            "end_exclusive_utc": display_end,
        },
        "status": status,
        "issues": issues,
        "promotion_contract": {
            "minimum_confirmed_closed_per_arm": MIN_CONFIRMED_CLOSED_PER_ARM,
            "sample_ready": sample_ready,
            "fee_coverage_complete": fee_ready,
            "automatic_winner_selection": False,
        },
        "arms": arms,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "blueberry-ab-report.json"
    json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Golden Blueberry A/B report",
        "",
        f"- Status: `{status}`",
        f"- Window: `{display_start}` to `{display_end}` (exclusive)",
        f"- Issues: `{issues or 'none'}`",
        "",
        "| Arm | First crossings | Candidates | Submitted | Confirmed closed | Fee-complete | Net P&L |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, arm in arms.items():
        signals = arm["signals"]
        trades = arm["trades"]
        net = trades["confirmed_net_pnl_usdc"]
        lines.append(
            f"| {label} | {signals['total_first_crossings']} | "
            f"{signals['candidates']} | {trades['submitted']} | "
            f"{trades['confirmed_closed']} | {trades['fee_complete_closed']} | "
            f"{('-' if net is None else f'${net:.4f}')} |"
        )
    lines.extend(
        [
            "",
            "Resolution settlement assumptions are reported separately and are not mixed into confirmed CLOB round-trip P&L.",
        ]
    )
    (output_dir / "blueberry-ab-report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return json_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-a", type=Path, required=True)
    parser.add_argument("--arm-b", type=Path, required=True)
    parser.add_argument("--review-start", required=True)
    parser.add_argument("--review-end", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(
        analyze(
            args.arm_a,
            args.arm_b,
            args.output_dir,
            args.review_start,
            args.review_end,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
