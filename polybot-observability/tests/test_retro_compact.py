from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polybot_observability import RunAudit
from polybot_observability.retro_audit import audit_database


@dataclass
class Config:
    db_path: Path
    job_name: str = "compact-retro-test"
    simulation_mode: bool = False
    trading: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.trading is None:
            self.trading = {"buy_amount": 5.0}


def _membership_digest() -> str:
    payload = [
        {
            "condition_id": "condition-1",
            "raw_seen_count": 1,
            "qualified": True,
            "qualification_reason": "qualified",
        }
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _compact_report(
    *,
    strategy_name: str = "golden-honeydew",
    hot_hours: float = 24.0,
    rollup_hours: float = 4.0,
    snapshot_anchor: datetime | None = None,
) -> str:
    normalized_strategy = strategy_name.strip().lower()
    selector = (
        "minimum"
        if normalized_strategy == "golden-nectarine"
        else (
            "extrema"
            if normalized_strategy
            in {"golden-elderberry", "golden-papaya", "golden-queen"}
            else "latest"
        )
    )
    return json.dumps(
        {
            "policy": {
                "strategy_name": strategy_name,
                "hot_hours": hot_hours,
                "rollup_hours": rollup_hours,
                "retention_days": 60.0,
                "selector": selector,
                "run_interval_hours": 1.0,
                "membership_detail_hours": 24.0,
            },
            "requirements": {
                "full_cadence_hours": (
                    0.5
                    if normalized_strategy == "golden-papaya"
                    else (
                        0.25 if normalized_strategy == "golden-queen" else 24.0
                    )
                ),
                "retention_days": (
                    60.0
                    if normalized_strategy in {"golden-papaya", "golden-queen"}
                    else 0.0
                ),
                "boundary_interval_hours": None,
                "max_rollup_hours": None,
                "minimum_latest_points": 0,
            },
            "snapshot_anchor": (
                snapshot_anchor.isoformat() if snapshot_anchor is not None else None
            ),
            "before": {},
            "after": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _create_compact_archive(
    path: Path,
    *,
    as_of: datetime,
    strategy_name: str = "golden-honeydew",
) -> str:
    path.parent.mkdir(parents=True)
    period_start = as_of - timedelta(days=1)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT);
            INSERT INTO trades(status) VALUES ('HOLDING');
            CREATE TABLE market_snapshots (
                id INTEGER PRIMARY KEY,
                condition_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                probability REAL,
                liquidity REAL,
                volume_24h REAL,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                run_id TEXT
            );
            """
        )
    run = RunAudit.start(Config(path), strategy_name=strategy_name)
    run.succeed()

    snapshot_rows = []
    sweep_rows = []
    for index in range(288):
        observed_at = period_start + timedelta(minutes=index * 5)
        completed_at = observed_at + timedelta(seconds=1)
        snapshot_rows.append(
            (
                "condition-1",
                observed_at.isoformat(),
                0.5,
                10_000.0,
                20_000.0,
                0.49,
                0.51,
                0.02,
                run.run_id,
            )
        )
        sweep_rows.append(
            (
                f"sweep-{index:03d}",
                run.run_id,
                observed_at.isoformat(),
                completed_at.isoformat(),
                "0" * 64,
            )
        )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE run_audits SET started_at = ?, finished_at = ? WHERE run_id = ?",
            (
                period_start.isoformat(),
                (period_start + timedelta(seconds=1)).isoformat(),
                run.run_id,
            ),
        )
        connection.executemany(
            """
            INSERT INTO market_snapshots(
                condition_id, timestamp, probability, liquidity, volume_24h,
                best_bid, best_ask, spread, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            snapshot_rows,
        )
        connection.executescript(
            """
            CREATE TABLE market_sweeps (
                sweep_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                run_id TEXT,
                started_at TEXT,
                completed_at TEXT,
                cursor_complete INTEGER,
                pages INTEGER,
                raw_market_count INTEGER,
                unique_condition_count INTEGER,
                qualified_market_count INTEGER,
                missing_condition_id_count INTEGER,
                duplicate_raw_count INTEGER,
                excluded_condition_count INTEGER,
                exclusion_counts_json TEXT,
                min_liquidity REAL,
                min_volume REAL,
                membership_digest_sha256 TEXT,
                snapshotted_market_count INTEGER,
                membership_detail_stored INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE market_sweep_memberships (
                sweep_id TEXT,
                condition_id TEXT,
                raw_seen_count INTEGER,
                qualified INTEGER,
                qualification_reason TEXT,
                snapshot_eligible INTEGER,
                snapshotted INTEGER,
                snapshot_reason TEXT,
                PRIMARY KEY (sweep_id, condition_id)
            );
            CREATE TABLE market_catalog (
                condition_id TEXT PRIMARY KEY,
                event_id TEXT,
                event_slug TEXT,
                end_date TEXT,
                outcomes_json TEXT,
                token_ids_json TEXT,
                tags_json TEXT,
                fees_enabled INTEGER,
                fee_rate REAL
            );
            CREATE TABLE polybot_db_maintenance (
                profile TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                active INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                last_maintained_at TEXT,
                last_report_json TEXT NOT NULL
            );
            INSERT INTO market_catalog VALUES (
                'condition-1', 'event-1', 'event', '2026-12-31T00:00:00Z',
                '["Yes", "No"]', '["yes-token", "no-token"]',
                '[{"slug": "sports"}]', 0, NULL
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO market_sweeps VALUES (
                ?, 1, ?, ?, ?, 1, 1, 1, 1, 1, 0, 0, 0, '{}',
                0, 0, ?, 1, 0
            )
            """,
            sweep_rows,
        )
        connection.execute(
            """
            INSERT INTO polybot_db_maintenance VALUES (
                'compact-v1', 2, ?, 1, ?, ?, ?
            )
            """,
            (
                strategy_name,
                period_start.isoformat(),
                as_of.isoformat(),
                _compact_report(
                    strategy_name=strategy_name,
                    snapshot_anchor=as_of - timedelta(minutes=5),
                ),
            ),
        )
    return run.run_id


def _create_rolled_compact_archive(path: Path, *, as_of: datetime) -> None:
    run_id = _create_compact_archive(path, as_of=as_of)
    cold_start = as_of - timedelta(days=2)
    cold_snapshot_rows = [
        (
            "condition-1",
            (cold_start + timedelta(hours=index * 4)).isoformat(),
            0.5,
            10_000.0,
            20_000.0,
            0.49,
            0.51,
            0.02,
            run_id,
        )
        for index in range(6)
    ]
    # Maintenance uses MAX(timestamp) - hot_hours as the exact boundary. The
    # boundary observation remains raw and therefore starts the 5-minute zone.
    boundary = as_of - timedelta(days=1, minutes=5)
    cold_snapshot_rows.append(
        (
            "condition-1",
            boundary.isoformat(),
            0.5,
            10_000.0,
            20_000.0,
            0.49,
            0.51,
            0.02,
            run_id,
        )
    )
    sweep_rows = []
    for index in range(288):
        started_at = cold_start + timedelta(minutes=index * 5)
        sweep_rows.append(
            (
                f"cold-sweep-{index:03d}",
                run_id,
                started_at.isoformat(),
                (started_at + timedelta(seconds=1)).isoformat(),
                "0" * 64,
            )
        )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO market_snapshots(
                condition_id, timestamp, probability, liquidity, volume_24h,
                best_bid, best_ask, spread, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cold_snapshot_rows,
        )
        connection.executemany(
            """
            INSERT INTO market_sweeps VALUES (
                ?, 1, ?, ?, ?, 1, 1, 1, 1, 1, 0, 0, 0, '{}',
                0, 0, ?, 1, 0
            )
            """,
            sweep_rows,
        )
        digest = _membership_digest()
        for sweep_id in ("cold-sweep-000", "sweep-000"):
            connection.execute(
                "UPDATE market_sweeps SET membership_detail_stored = 1, "
                "membership_digest_sha256 = ? WHERE sweep_id = ?",
                (digest, sweep_id),
            )
            connection.execute(
                "INSERT INTO market_sweep_memberships VALUES "
                "(?, 'condition-1', 1, 1, 'qualified', 1, 1, 'snapshotted')",
                (sweep_id,),
            )


def _store_detail_checkpoint(connection: sqlite3.Connection, sweep_id: str) -> None:
    connection.execute(
        "UPDATE market_sweeps SET membership_detail_stored = 1, "
        "membership_digest_sha256 = ? WHERE sweep_id = ?",
        (_membership_digest(), sweep_id),
    )
    connection.execute(
        "INSERT INTO market_sweep_memberships VALUES "
        "(?, 'condition-1', 1, 1, 'qualified', 1, 1, 'snapshotted')",
        (sweep_id,),
    )


def _extend_first_crossing_archive_to_sixty_days(
    path: Path, *, as_of: datetime
) -> None:
    archive_start = as_of - timedelta(days=60)
    with sqlite3.connect(path) as connection:
        run_id = connection.execute("SELECT run_id FROM run_audits LIMIT 1").fetchone()[
            0
        ]
        rows = [
            (
                "condition-1",
                (archive_start + timedelta(hours=index * 4)).isoformat(),
                0.5,
                10_000.0,
                20_000.0,
                0.49,
                0.51,
                0.02,
                run_id,
            )
            for index in range(59 * 6)
        ]
        connection.executemany(
            """
            INSERT INTO market_snapshots(
                condition_id, timestamp, probability, liquidity, volume_24h,
                best_bid, best_ask, spread, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        cold_sweeps = []
        detailed_sweep_ids: list[str] = []
        for index in range(59 * 288):
            started_at = archive_start + timedelta(minutes=index * 5)
            sweep_id = f"archive-sweep-{index:05d}"
            detail_stored = int(index % 288 == 0)
            digest = _membership_digest() if detail_stored else "0" * 64
            cold_sweeps.append(
                (
                    sweep_id,
                    run_id,
                    started_at.isoformat(),
                    (started_at + timedelta(seconds=1)).isoformat(),
                    digest,
                    detail_stored,
                )
            )
            if detail_stored:
                detailed_sweep_ids.append(sweep_id)
        connection.executemany(
            """
            INSERT INTO market_sweeps VALUES (
                ?, 1, ?, ?, ?, 1, 1, 1, 1, 1, 0, 0, 0, '{}',
                0, 0, ?, 1, ?
            )
            """,
            cold_sweeps,
        )
        connection.executemany(
            "INSERT INTO market_sweep_memberships VALUES "
            "(?, 'condition-1', 1, 1, 'qualified', 1, 1, 'snapshotted')",
            [(sweep_id,) for sweep_id in detailed_sweep_ids],
        )
        _store_detail_checkpoint(connection, "sweep-000")


def test_compact_summary_sweeps_fail_when_detail_checkpoint_is_missing(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_compact_archive(db_path, as_of=as_of)

    result = audit_database(db_path, days=1, as_of=as_of)
    sweeps = result["market_sweeps"]
    issue_codes = {issue["code"] for issue in result["issues"]}

    assert sweeps["compact_profile_active"] is True
    assert sweeps["valid_complete_sweeps"] == 288
    assert sweeps["valid_summary_only_sweeps"] == 288
    assert sweeps["valid_detailed_sweeps"] == 0
    assert sweeps["invariant_failures"] == 0
    assert sweeps["detailed_membership_metrics_available"] is False
    assert sweeps["detail_checkpoint_bucket_coverage_ratio"] == 0.0
    assert sweeps["contract_complete"] is False
    assert sweeps["snapshot_eligible_coverage_ratio"] is None
    assert sweeps["qualified_snapshot_eligibility_ratio"] is None
    assert sweeps["per_market_attested_snapshot_p10"] is None
    assert "archive_window_short" in issue_codes
    assert "market_sweep_attestation_missing" in issue_codes
    assert "market_sweep_attestation_invalid" not in issue_codes


def test_compact_detail_metrics_use_only_attested_detail_samples(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_compact_archive(db_path, as_of=as_of)
    digest = _membership_digest()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE market_sweeps SET membership_detail_stored = 1, "
            "membership_digest_sha256 = ? WHERE sweep_id = 'sweep-000'",
            (digest,),
        )
        connection.execute(
            """
            INSERT INTO market_sweep_memberships VALUES (
                'sweep-000', 'condition-1', 1, 1, 'qualified',
                1, 1, 'snapshotted'
            )
            """
        )

    result = audit_database(db_path, days=1, as_of=as_of)
    sweeps = result["market_sweeps"]
    issue_codes = {issue["code"] for issue in result["issues"]}

    assert sweeps["valid_complete_sweeps"] == 288
    assert sweeps["valid_summary_only_sweeps"] == 287
    assert sweeps["valid_detailed_sweeps"] == 1
    assert sweeps["detailed_membership_metrics_available"] is True
    assert sweeps["detail_checkpoint_bucket_coverage_ratio"] == 1.0
    assert sweeps["snapshot_eligible_coverage_ratio"] == 1.0
    assert sweeps["qualified_snapshot_eligibility_ratio"] == 1.0
    assert sweeps["per_market_attested_snapshot_p10"] == 1.0
    assert "archive_window_short" not in issue_codes


def test_unaligned_audit_window_accepts_one_daily_detail_checkpoint(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, 12, 30, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_compact_archive(db_path, as_of=as_of)
    with sqlite3.connect(db_path) as connection:
        _store_detail_checkpoint(connection, "sweep-000")

    result = audit_database(db_path, days=1, as_of=as_of)
    sweeps = result["market_sweeps"]

    assert sweeps["expected_detail_checkpoint_buckets"] == 1
    assert sweeps["detail_checkpoint_buckets"] == 1
    assert sweeps["detail_checkpoint_count_coverage_ratio"] == 1.0
    assert sweeps["detail_checkpoint_gap_coverage_ratio"] == 1.0
    assert sweeps["detail_checkpoint_bucket_coverage_ratio"] == 1.0


def test_detail_checkpoint_coverage_detects_a_long_tail_gap(tmp_path: Path) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_rolled_compact_archive(db_path, as_of=as_of)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM market_sweep_memberships WHERE sweep_id = 'sweep-000'"
        )
        connection.execute(
            "UPDATE market_sweeps SET membership_detail_stored = 0, "
            "membership_digest_sha256 = ? WHERE sweep_id = 'sweep-000'",
            ("0" * 64,),
        )

    result = audit_database(db_path, days=2, as_of=as_of)
    sweeps = result["market_sweeps"]

    assert sweeps["expected_detail_checkpoint_buckets"] == 2
    assert sweeps["detail_checkpoint_buckets"] == 1
    assert sweeps["detail_checkpoint_count_coverage_ratio"] == 0.5
    assert 0.5 <= sweeps["detail_checkpoint_gap_coverage_ratio"] < 0.51
    assert sweeps["detail_checkpoint_bucket_coverage_ratio"] == 0.5
    assert 47.9 < sweeps["detail_checkpoint_max_gap_hours"] < 48.0
    assert sweeps["contract_complete"] is False


def test_invalid_detail_digest_remains_critical_when_checkpoint_is_missing(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_compact_archive(db_path, as_of=as_of)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE market_sweeps SET membership_detail_stored = 1, "
            "membership_digest_sha256 = ? WHERE sweep_id = 'sweep-000'",
            ("f" * 64,),
        )
        connection.execute(
            "INSERT INTO market_sweep_memberships VALUES "
            "('sweep-000', 'condition-1', 1, 1, 'qualified', "
            "1, 1, 'snapshotted')"
        )

    result = audit_database(db_path, days=1, as_of=as_of)
    sweeps = result["market_sweeps"]
    issues = {issue["code"]: issue["severity"] for issue in result["issues"]}

    assert sweeps["contract_complete"] is False
    assert sweeps["invariant_failures"] == 1
    assert (
        "membership_digest_sha256" in sweeps["invariant_failure_details"][0]["failed"]
    )
    assert issues["market_sweep_attestation_invalid"] == "CRITICAL"


def test_summary_only_flag_cannot_bypass_legacy_attestation(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_compact_archive(db_path, as_of=as_of)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE polybot_db_maintenance SET active = 0 WHERE profile = 'compact-v1'"
        )

    result = audit_database(db_path, days=1, as_of=as_of)
    sweeps = result["market_sweeps"]

    assert sweeps["compact_profile_active"] is False
    assert sweeps["valid_complete_sweeps"] == 0
    assert sweeps["invariant_failures"] == 288
    assert {"summary_only_requires_compact_profile"} == set(
        sweeps["invariant_failure_details"][0]["failed"]
    )
    assert any(
        issue["code"] == "market_sweep_attestation_invalid"
        for issue in result["issues"]
    )


def test_compact_snapshot_cadence_accepts_cold_rollup_and_strict_hot_window(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_rolled_compact_archive(db_path, as_of=as_of)

    result = audit_database(db_path, days=2, as_of=as_of)
    snapshots = result["market_snapshots"]
    issue_codes = {issue["code"] for issue in result["issues"]}

    assert result["compact_snapshot_policy"]["profile"] == "compact-v1"
    assert snapshots["cadence_mode"] == "compact-v1"
    assert snapshots["compact_hot_hours"] == 24.0
    assert snapshots["compact_rollup_hours"] == 4.0
    assert snapshots["five_minute_bucket_coverage_ratio"] < 0.6
    assert snapshots["cadence_bucket_coverage_ratio"] == 1.0
    assert snapshots["per_market_cadence_p10"] == 1.0
    assert "archive_window_short" not in issue_codes


def test_compact_snapshot_cadence_detects_a_missing_cold_rollup_bucket(
    tmp_path: Path,
) -> None:
    final_as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_rolled_compact_archive(db_path, as_of=final_as_of)
    cold_start = final_as_of - timedelta(days=2)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM market_snapshots WHERE timestamp = ?",
            ((cold_start + timedelta(hours=4)).isoformat(),),
        )

    historical_as_of = cold_start + timedelta(hours=12)
    result = audit_database(db_path, days=0.5, as_of=historical_as_of)
    snapshots = result["market_snapshots"]

    assert snapshots["cadence_mode"] == "compact-v1"
    assert snapshots["expected_cadence_buckets"] == 3
    assert snapshots["cadence_buckets"] == 2
    assert snapshots["cadence_bucket_coverage_ratio"] == 0.666667
    assert snapshots["per_market_cadence_p10"] == 0.666667
    assert any(issue["code"] == "archive_window_short" for issue in result["issues"])


def test_compact_audit_uses_persisted_anchor_not_future_snapshot(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    _create_rolled_compact_archive(db_path, as_of=as_of)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO market_snapshots("
            "condition_id, timestamp, probability, liquidity, volume_24h, "
            "best_bid, best_ask, spread, run_id) "
            "VALUES ('future', '2099-01-01T00:00:00+00:00', 0.5, 10000, "
            "20000, 0.49, 0.51, 0.02, NULL)"
        )

    result = audit_database(db_path, days=2, as_of=as_of)

    assert (
        result["market_snapshots"]["compact_hot_boundary"]
        == (as_of - timedelta(days=1, minutes=5)).isoformat()
    )


def test_unknown_profile_or_broken_policy_cannot_relax_snapshot_cadence(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    mutations = (
        "UPDATE polybot_db_maintenance SET profile = 'compact-v2'",
        "UPDATE polybot_db_maintenance SET last_report_json = "
        '\'{"policy":{"hot_hours":-1}}\'',
    )
    for index, mutation in enumerate(mutations):
        db_path = tmp_path / f"case-{index}" / "golden-honeydew" / "trades.db"
        _create_rolled_compact_archive(db_path, as_of=as_of)
        with sqlite3.connect(db_path) as connection:
            connection.execute(mutation)

        result = audit_database(db_path, days=2, as_of=as_of)
        snapshots = result["market_snapshots"]

        assert result["compact_snapshot_policy"] is None
        assert snapshots["cadence_mode"] == "five-minute"
        assert snapshots["cadence_bucket_coverage_ratio"] < 0.6
        assert any(
            issue["code"] == "archive_window_short" for issue in result["issues"]
        )
        assert any(
            issue["code"] == "market_sweep_attestation_invalid"
            for issue in result["issues"]
        )


def test_contradictory_compact_metadata_cannot_relax_evidence_contract(
    tmp_path: Path,
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    mutations = {
        "anchor_after_maintenance": lambda report: report.update(
            {"snapshot_anchor": (as_of + timedelta(minutes=5)).isoformat()}
        ),
        "anchor_without_snapshot_evidence": lambda report: report.update(
            {"snapshot_anchor": (as_of - timedelta(minutes=7)).isoformat()}
        ),
        "hot_window_too_short": lambda report: report["policy"].update(
            {"hot_hours": 12.0}
        ),
        "retention_does_not_cover_hot": lambda report: report["policy"].update(
            {"retention_days": 0.5}
        ),
        "wrong_strategy_selector": lambda report: report["policy"].update(
            {"selector": "extrema"}
        ),
        "rollup_exceeds_strategy_maximum": lambda report: (
            report["requirements"].update({"max_rollup_hours": 2.0})
        ),
        "rollup_exceeds_boundary_interval": lambda report: (
            report["requirements"].update({"boundary_interval_hours": 2.0})
        ),
        "negative_latest_point_requirement": lambda report: (
            report["requirements"].update({"minimum_latest_points": -1})
        ),
    }
    for case_name, mutate in mutations.items():
        db_path = tmp_path / case_name / "golden-honeydew" / "trades.db"
        _create_rolled_compact_archive(db_path, as_of=as_of)
        with sqlite3.connect(db_path) as connection:
            raw_report = connection.execute(
                "SELECT last_report_json FROM polybot_db_maintenance "
                "WHERE profile = 'compact-v1'"
            ).fetchone()[0]
            report = json.loads(raw_report)
            mutate(report)
            connection.execute(
                "UPDATE polybot_db_maintenance SET last_report_json = ? "
                "WHERE profile = 'compact-v1'",
                (json.dumps(report),),
            )

        result = audit_database(db_path, days=2, as_of=as_of)

        assert result["compact_snapshot_policy"] is None, case_name
        assert result["market_snapshots"]["cadence_mode"] == "five-minute", case_name
        assert any(
            issue["code"] == "archive_window_short" for issue in result["issues"]
        ), case_name


@pytest.mark.parametrize("strategy_name", ("golden-papaya", "golden-queen"))
def test_first_crossing_strategy_requires_its_own_sixty_day_archive(
    tmp_path: Path, strategy_name: str
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / strategy_name / "trades.db"
    _create_compact_archive(
        db_path,
        as_of=as_of,
        strategy_name=strategy_name,
    )
    with sqlite3.connect(db_path) as connection:
        _store_detail_checkpoint(connection, "sweep-000")

    result = audit_database(db_path, days=1, as_of=as_of)
    snapshots = result["market_snapshots"]

    assert result["compact_snapshot_policy"]["strategy_name"] == strategy_name
    assert snapshots["minimum_archive_history_hours"] == 60 * 24
    assert snapshots["archive_history_window_coverage_ratio"] < 0.02
    assert snapshots["archive_history_cadence_coverage_ratio"] < 0.5
    assert any(issue["code"] == "archive_window_short" for issue in result["issues"])


@pytest.mark.parametrize("strategy_name", ("golden-papaya", "golden-queen"))
def test_first_crossing_sixty_day_compact_archive_is_ready(
    tmp_path: Path, strategy_name: str
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / strategy_name / "trades.db"
    _create_compact_archive(
        db_path,
        as_of=as_of,
        strategy_name=strategy_name,
    )
    _extend_first_crossing_archive_to_sixty_days(db_path, as_of=as_of)

    result = audit_database(db_path, days=1, as_of=as_of)
    snapshots = result["market_snapshots"]
    issue_codes = {issue["code"] for issue in result["issues"]}

    assert snapshots["archive_history_window_coverage_ratio"] == 1.0
    assert snapshots["archive_history_cadence_coverage_ratio"] > 0.99
    assert "archive_window_short" not in issue_codes
    assert "market_sweep_attestation_missing" not in issue_codes
    assert "market_sweep_attestation_invalid" not in issue_codes


@pytest.mark.parametrize("strategy_name", ("golden-papaya", "golden-queen"))
def test_first_crossing_validates_snapshot_domains_across_sixty_day_archive(
    tmp_path: Path, strategy_name: str
) -> None:
    as_of = datetime(2026, 7, 31, tzinfo=timezone.utc)
    db_path = tmp_path / strategy_name / "trades.db"
    _create_compact_archive(
        db_path,
        as_of=as_of,
        strategy_name=strategy_name,
    )
    _extend_first_crossing_archive_to_sixty_days(db_path, as_of=as_of)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE market_snapshots SET probability = 2.0 "
            "WHERE timestamp = (SELECT MIN(timestamp) FROM market_snapshots)"
        )

    result = audit_database(db_path, days=1, as_of=as_of)
    issues = {issue["code"]: issue["severity"] for issue in result["issues"]}

    assert result["market_snapshots"]["invalid_value_rows"] == 1
    assert issues["archive_snapshot_domain_invalid"] == "CRITICAL"
