from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from polybot_observability import ExecutionLedger, RunAudit, current_run_id
from polybot_observability.run_audit import _safe_error_message
from polybot_observability.retro_audit import (
    audit_database,
    audit_many,
    backup_databases,
    discover_databases,
    parse_as_of,
    render_markdown,
)
from datetime import datetime, timedelta, timezone


@dataclass
class Trading:
    buy_amount: float = 5.0
    nested: tuple[str, ...] = ("one", "two")
    api_token: str = "nested-secret"


@dataclass
class Api:
    private_key: str = "must-not-be-persisted"
    funder_address: str = "0xsecret"


@dataclass
class Config:
    db_path: Path
    job_name: str = "test-job"
    simulation_mode: bool = True
    trading: Trading = field(default_factory=Trading)
    api: Api = field(default_factory=Api)


def test_date_as_of_is_next_midnight_exclusive() -> None:
    assert parse_as_of("2026-07-11") == datetime(
        2026, 7, 12, tzinfo=timezone.utc
    )


def test_database_discovery_excludes_simulation_by_default(tmp_path: Path) -> None:
    live = tmp_path / "golden-test" / "trades.db"
    simulation = tmp_path / "golden-test" / "trades_sim.db"
    live.parent.mkdir(parents=True)
    live.touch()
    simulation.touch()

    assert discover_databases(tmp_path) == [live.resolve()]
    assert discover_databases(tmp_path, include_sim=True) == [
        live.resolve(),
        simulation.resolve(),
    ]


def test_simulation_database_is_separate_non_live_assumption_cohort(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "golden-test" / "trades_sim.db"
    db_path.parent.mkdir(parents=True)
    _create_domain_tables(db_path)

    result = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    codes = {issue["code"] for issue in result["issues"]}
    assert result["cohort"] == "simulation_assumption"
    assert result["trades"]["pnl_quality"] == "SIMULATION_ASSUMPTION"
    assert "execution_ledger_missing" not in codes
    assert "order_ids_missing" not in codes

    bundle = audit_many(
        [db_path], days=30, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    assert bundle["database_cohorts"] == {"simulation_assumption": 1}
    assert bundle["live_issue_counts"] == {}


def test_filename_cohort_cannot_hide_persisted_live_or_simulation_evidence(
    tmp_path: Path,
) -> None:
    sim_path = tmp_path / "golden-test" / "trades_sim.db"
    sim_path.parent.mkdir(parents=True)
    _create_domain_tables(sim_path)
    live_run = RunAudit.start(
        Config(db_path=sim_path, simulation_mode=False), strategy_name="golden-test"
    )
    live_run.succeed()
    disguised_live = audit_database(
        sim_path, days=30, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    assert disguised_live["cohort"] == "live"
    assert any(
        issue["code"] == "cohort_mode_mismatch"
        for issue in disguised_live["issues"]
    )
    bundle = audit_many(
        [sim_path], days=30, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    assert bundle["live_issue_counts"]["CRITICAL"] >= 1

    live_path = tmp_path / "golden-other" / "trades.db"
    live_path.parent.mkdir(parents=True)
    _create_domain_tables(live_path)
    sim_run = RunAudit.start(
        Config(db_path=live_path, simulation_mode=True), strategy_name="golden-other"
    )
    sim_run.succeed()
    disguised_sim = audit_database(
        live_path, days=30, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    assert disguised_sim["cohort"] == "live"
    assert any(
        issue["code"] == "cohort_mode_mismatch"
        for issue in disguised_sim["issues"]
    )


def _create_domain_tables(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE market_snapshots (
                id INTEGER PRIMARY KEY, condition_id TEXT, timestamp TEXT
            );
            CREATE TABLE skipped_markets (id INTEGER PRIMARY KEY);
            INSERT INTO trades(status) VALUES ('HOLDING'), ('COMPLETED');
            INSERT INTO market_snapshots(condition_id, timestamp)
            VALUES ('condition-1', '2026-07-01T00:00:00');
            """
        )


def test_success_records_config_without_api_secrets(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    _create_domain_tables(db_path)
    config = Config(db_path=db_path)

    audit = RunAudit.start(config, strategy_name="golden-test")
    assert current_run_id() == audit.run_id
    audit.succeed({"bought": 1})
    assert current_run_id() is None

    with sqlite3.connect(db_path) as connection:
        config_json = connection.execute(
            "SELECT config_json FROM strategy_configs"
        ).fetchone()[0]
        run = connection.execute(
            "SELECT status, cycle_stats_json, db_summary_json FROM run_audits"
        ).fetchone()

    assert "must-not-be-persisted" not in config_json
    assert "0xsecret" not in config_json
    assert "nested-secret" not in config_json
    assert json.loads(config_json)["trading"]["api_token"] == "<redacted>"
    assert json.loads(config_json)["trading"]["buy_amount"] == 5.0
    assert run[0] == "SUCCESS"
    assert json.loads(run[1]) == {"bought": 1}
    assert json.loads(run[2])["trade_status_counts"] == {
        "COMPLETED": 1,
        "HOLDING": 1,
    }


def test_failure_records_only_error_type_and_message(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    config = Config(db_path=db_path, simulation_mode=False)

    audit = RunAudit.start(config, strategy_name="golden-test")
    audit.fail(ValueError("bad cycle"))

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status, error_type, error_message FROM run_audits"
        ).fetchone()

    assert row == ("FAILED", "ValueError", "bad cycle")


def test_failure_redacts_known_secret_shapes(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    config = Config(db_path=db_path)
    audit = RunAudit.start(config, strategy_name="golden-test")
    github = "gho_" + "H" * 36
    aws_access_id = "ASIA" + "J" * 16
    openai = "sk-" + "k" * 32
    jwt = "eyJ" + "m" * 10 + "." + "n" * 12 + "." + "p" * 12
    dsn = "redis://fake_user:fake_password@cache.invalid:6379/0"
    audit.fail(
        ValueError(
            "api_key=abc123 token: xoxb-super-secret "
            "Authorization: Bearer bearer-secret-value "
            "wallet=0x1111111111111111111111111111111111111111 "
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            f"github={github} aws={aws_access_id} openai={openai} "
            f"jwt={jwt} connection={dsn} "
            "headers={'Authorization': 'Basic quoted-basic-value'}"
        )
    )

    with sqlite3.connect(db_path) as connection:
        message = connection.execute(
            "SELECT error_message FROM run_audits"
        ).fetchone()[0]

    assert "abc123" not in message
    assert "xoxb-super-secret" not in message
    assert "bearer-secret-value" not in message
    assert "0x1111111111111111111111111111111111111111" not in message
    assert "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in message
    assert github not in message
    assert aws_access_id not in message
    assert openai not in message
    assert jwt not in message
    assert dsn not in message
    assert "quoted-basic-value" not in message


def test_error_sanitizer_redacts_adversarial_credential_shapes() -> None:
    synthetic = {
        "github": "ghp_" + "A" * 36,
        "github_fine": "github_pat_" + "B" * 40,
        "aws": "AKIA" + "C" * 16,
        "openai": "sk-proj-" + "d" * 32,
        "jwt": "eyJ" + "e" * 12 + "." + "f" * 16 + "." + "g" * 20,
        "dsn": "postgresql://audit_user:not-a-real-password@db.invalid:5432/fake",
        "quoted": "synthetic-quoted-credential",
        "authorization": "synthetic-basic-credential",
        "escaped": "synthetic-escaped-credential",
    }
    payload = (
        f"github={synthetic['github']} fine={synthetic['github_fine']} "
        f"aws={synthetic['aws']} openai={synthetic['openai']} jwt={synthetic['jwt']} "
        f"connection={synthetic['dsn']} "
        f"headers={{'Authorization': 'Basic {synthetic['authorization']}'}} "
        f"config={{\"client_secret\": \"{synthetic['quoted']}\"}} "
        f"escaped={{\\\"refresh_token\\\":\\\"{synthetic['escaped']}\\\"}} "
        "Authorization=Bearer another-synthetic-credential "
        "-----BEGIN PRIVATE KEY----- synthetic-private-material "
        "-----END PRIVATE KEY----- "
        + "bounded " * 1_000
    )

    sanitized = _safe_error_message(RuntimeError(payload))

    assert len(sanitized) <= 2_000
    assert all(value not in sanitized for value in synthetic.values())
    assert "synthetic-private-material" not in sanitized
    assert "another-synthetic-credential" not in sanitized
    assert "<redacted-dsn>" in sanitized
    assert "<redacted-jwt>" in sanitized
    assert "<redacted-private-key>" in sanitized


def test_identical_configs_are_deduplicated(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    config = Config(db_path=db_path)

    first = RunAudit.start(config, strategy_name="golden-test")
    first.succeed()
    second = RunAudit.start(config, strategy_name="golden-test")
    second.succeed()

    with sqlite3.connect(db_path) as connection:
        configs = connection.execute("SELECT COUNT(*) FROM strategy_configs").fetchone()[0]
        runs = connection.execute("SELECT COUNT(*) FROM run_audits").fetchone()[0]

    assert configs == 1
    assert runs == 2


def test_nested_run_context_restores_outer_run(tmp_path: Path) -> None:
    outer = RunAudit.start(
        Config(db_path=tmp_path / "outer.db"), strategy_name="golden-outer"
    )
    inner = RunAudit.start(
        Config(db_path=tmp_path / "inner.db"), strategy_name="golden-inner"
    )
    assert current_run_id() == inner.run_id

    inner.succeed()
    assert current_run_id() == outer.run_id
    outer.succeed()
    assert current_run_id() is None


def test_invalid_git_commit_environment_is_not_persisted(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "trades.db"
    monkeypatch.setenv("GIT_COMMIT", "not-a-commit-or-a-secret")

    audit = RunAudit.start(Config(db_path=db_path), strategy_name="golden-test")
    audit.succeed()

    with sqlite3.connect(db_path) as connection:
        git_commit = connection.execute(
            "SELECT git_commit FROM run_audits"
        ).fetchone()[0]
    assert git_commit == "unknown"


def test_retro_audit_reports_provenance_and_execution_gap(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-test" / "data" / "trades.db"
    db_path.parent.mkdir(parents=True)
    _create_domain_tables(db_path)
    config = Config(db_path=db_path)
    audit = RunAudit.start(config, strategy_name="golden-test")
    audit.succeed({"bought": 1})

    result = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )

    assert result["strategy_name"] == "golden-test"
    assert result["runs"]["config_versions"] == 1
    assert any(issue["code"] == "execution_ledger_missing" for issue in result["issues"])
    assert result["runs"]["max_success_gap_hours"] is not None
    assert any(issue["code"] == "run_schedule_gap" for issue in result["issues"])
    markdown = render_markdown(
        {
            "generated_at": "now",
            "period": {"start": "a", "end": "b"},
            "database_count": 1,
            "issue_counts": {"CRITICAL": 1},
            "databases": [result],
        }
    )
    assert "golden-test" in markdown


def test_sqlite_backup_is_consistent_and_checksummed(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-test" / "trades.db"
    db_path.parent.mkdir(parents=True)
    _create_domain_tables(db_path)

    destination = backup_databases([db_path], tmp_path / "backups")
    manifest = json.loads((destination / "manifest.json").read_text())
    backup_path = destination / manifest["databases"][0]["backup"]
    assert manifest["databases"][0]["quick_check"] == ["ok"]

    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 2


def test_audit_does_not_accept_empty_fill_table_or_two_point_archive(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-nectarine" / "trades.db"
    db_path.parent.mkdir(parents=True)
    _create_domain_tables(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fills (id INTEGER PRIMARY KEY)")
        connection.execute("DELETE FROM market_snapshots")
        connection.executemany(
            "INSERT INTO market_snapshots(condition_id, timestamp) VALUES (?, ?)",
            [
                ("one", "2026-07-01T00:00:00"),
                ("one", "2026-07-31T00:00:00"),
            ],
        )
    config = Config(db_path=db_path)
    run = RunAudit.start(config, strategy_name="golden-nectarine")
    run.succeed()

    result = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    codes = {issue["code"] for issue in result["issues"]}

    assert result["trades"]["pnl_quality"] == "ORDER_ASSUMPTION"
    assert "execution_ledger_incomplete" in codes
    assert "archive_window_short" in codes
    assert "market_catalog_missing" in codes


def test_audit_requires_full_fill_size_and_reports_confirmed_pnl(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-test" / "trades.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                status TEXT,
                buy_order_id TEXT,
                sell_order_id TEXT,
                buy_shares REAL,
                sell_shares REAL,
                buy_timestamp TEXT,
                sell_timestamp TEXT
            );
            INSERT INTO trades VALUES (
                1, 'COMPLETED', 'buy-1', 'sell-1', 10, 10,
                '2026-07-10T00:00:00+00:00',
                '2026-07-11T00:00:00+00:00'
            );
            """
        )
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submissions = {}
    for side, order_id, price in (("BUY", "buy-1", 0.4), ("SELL", "sell-1", 0.6)):
        submission_id = ledger.record_submission(
            token_id="token",
            side=side,
            requested_price=price,
            requested_size=10,
            result={"success": True, "orderID": order_id},
            simulation=False,
        )
        submissions[side] = submission_id
        ledger.record_fill(
            submission_id,
            order_id,
            {
                "id": f"fill-{side.lower()}",
                "status": "CONFIRMED",
                "size": 10_000_000 if side == "BUY" else 5_000_000,
                "price": price,
                "taker_order_id": order_id,
                "fee_rate_bps": 0,
                "fee_amount_usdc": 0,
            },
        )
        trade_ids = (
            ["fill-buy"] if side == "BUY" else ["fill-sell", "fill-sell-two"]
        )
        ledger.record_order_status(
            submission_id,
            {
                "status": "MATCHED",
                "original_size": "10000000",
                "size_matched": "10000000",
                "associate_trades": trade_ids,
            },
        )
        assert ledger.finish_reconciliation(submission_id) is (side == "BUY")
    run = RunAudit.start(Config(db_path=db_path), strategy_name="golden-test")
    run.succeed()

    partial = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert partial["fill_ledger"]["completed_trade_fill_coverage"] == 0.0
    assert any(
        issue["code"] == "completed_trade_fill_gap" for issue in partial["issues"]
    )

    sell_submission = submissions["SELL"]
    ledger.record_fill(
        sell_submission,
        "sell-1",
        {
            "id": "fill-sell-two",
            "status": "CONFIRMED",
            "size": 5_000_000,
            "price": 0.6,
            "taker_order_id": "sell-1",
            "fee_rate_bps": 0,
            "fee_amount_usdc": 0,
        },
    )
    assert ledger.finish_reconciliation(sell_submission) is True
    complete = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )

    assert complete["fill_ledger"]["completed_trade_fill_coverage"] == 1.0
    assert complete["fill_ledger"]["liquidity_role_known_ratio"] == 1.0
    assert complete["fill_ledger"]["confirmed_fill_gross_pnl_usdc"] == 2.0
    assert complete["fill_ledger"]["confirmed_fill_net_pnl_usdc"] == 2.0

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_fills SET fee_rate_bps = 30, fee_amount_usdc = NULL "
            "WHERE order_id = 'sell-1'"
        )
    gross_only = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert gross_only["trades"]["pnl_quality"] == "FILL_LEDGER_GROSS_ONLY"
    assert gross_only["fill_ledger"]["confirmed_fill_net_pnl_usdc"] is None
    assert any(issue["code"] == "fill_fee_missing" for issue in gross_only["issues"])


def test_reconciled_partial_round_trip_uses_actual_fills_not_legacy_shares(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "golden-test" / "trades.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, status TEXT,
                buy_order_id TEXT, sell_order_id TEXT,
                buy_shares REAL, sell_shares REAL,
                buy_timestamp TEXT, sell_timestamp TEXT
            );
            INSERT INTO trades VALUES (
                1, 'COMPLETED', 'buy-partial', 'sell-partial', 10, 10,
                '2026-07-10T00:00:00+00:00',
                '2026-07-11T00:00:00+00:00'
            );
            """
        )
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    for side, order_id, price in (
        ("BUY", "buy-partial", 0.4),
        ("SELL", "sell-partial", 0.6),
    ):
        submission_id = ledger.record_submission(
            token_id="token",
            side=side,
            requested_price=price,
            requested_size=10,
            result={"success": True, "orderID": order_id},
            simulation=False,
        )
        ledger.record_fill(
            submission_id,
            order_id,
            {
                "id": f"fill-{side.lower()}",
                "status": "CONFIRMED",
                "size": "5000000",
                "price": price,
                "taker_order_id": order_id,
                "fee_rate_bps": 0,
                "fee_amount_usdc": 0,
            },
        )
        ledger.record_order_status(
            submission_id,
            {
                "status": "MATCHED",
                "size_matched": "5000000",
                "associate_trades": [f"fill-{side.lower()}"],
            },
        )
        assert ledger.finish_reconciliation(submission_id) is True
    run = RunAudit.start(Config(db_path=db_path), strategy_name="golden-test")
    run.succeed()

    result = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert result["fill_ledger"]["completed_trade_fill_coverage"] == 1.0
    assert result["fill_ledger"]["confirmed_fill_net_pnl_usdc"] == 1.0
    assert result["fill_ledger"]["legacy_share_mismatches"] == 1
    assert result["trades"]["pnl_quality"] == "FILL_LEDGER_NET"


def test_overfilled_order_is_critical_and_excluded_from_pnl(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-test" / "trades.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, status TEXT,
                buy_order_id TEXT, sell_order_id TEXT,
                buy_shares REAL, sell_shares REAL,
                buy_timestamp TEXT, sell_timestamp TEXT
            );
            INSERT INTO trades VALUES (
                1, 'COMPLETED', 'buy-over', 'sell-exact', 10, 10,
                '2026-07-10T00:00:00+00:00',
                '2026-07-11T00:00:00+00:00'
            );
            """
        )
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    for side, order_id, raw_size in (
        ("BUY", "buy-over", "10000002"),
        ("SELL", "sell-exact", "10000000"),
    ):
        submission_id = ledger.record_submission(
            token_id="token",
            side=side,
            requested_price=0.4 if side == "BUY" else 0.6,
            requested_size=10,
            result={"success": True, "orderID": order_id},
            simulation=False,
        )
        fill_id = f"fill-{side.lower()}"
        ledger.record_fill(
            submission_id,
            order_id,
            {
                "id": fill_id,
                "status": "CONFIRMED",
                "size": raw_size,
                "price": 0.4 if side == "BUY" else 0.6,
                "taker_order_id": order_id,
                "fee_rate_bps": 0,
                "fee_amount_usdc": 0,
            },
        )
        ledger.record_order_status(
            submission_id,
            {
                "status": "MATCHED",
                "size_matched": "10000000",
                "associate_trades": [fill_id],
            },
        )
        assert ledger.finish_reconciliation(submission_id) is (side == "SELL")
    run = RunAudit.start(Config(db_path=db_path), strategy_name="golden-test")
    run.succeed()

    result = audit_database(
        db_path,
        days=30,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    codes = {issue["code"] for issue in result["issues"]}
    assert result["fill_ledger"]["overfilled_orders"] == 1
    assert result["fill_ledger"]["completed_trade_fill_coverage"] == 0.0
    assert result["fill_ledger"]["confirmed_fill_gross_pnl_usdc"] is None
    assert result["trades"]["pnl_quality"] == "ORDER_ASSUMPTION"
    assert "fill_quantity_overflow" in codes


def test_invalid_execution_domains_are_critical_in_retro_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-test" / "trades.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)")
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token", side="BUY", requested_price=0.4,
        requested_size=10, result={"success": True, "orderID": "bad-domain"},
        simulation=False,
    )
    ledger.record_order_status(
        submission_id,
        {
            "status": "MATCHED", "original_size": "5000000",
            "size_matched": "10000000", "associate_trades": ["bad-fill"],
        },
    )
    ledger.record_fill(
        submission_id,
        "bad-domain",
        {
            "id": "bad-fill", "status": "CONFIRMED", "size": "-5000000",
            "price": 0.4, "taker_order_id": "bad-domain",
            "fee_rate_bps": -1, "fee_amount_usdc": "-100",
        },
    )
    assert ledger.finish_reconciliation(submission_id) is False
    run = RunAudit.start(
        Config(db_path=db_path, simulation_mode=False), strategy_name="golden-test"
    )
    run.succeed()

    result = audit_database(
        db_path, days=30, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    codes = {issue["code"] for issue in result["issues"]}
    assert result["fill_ledger"]["invalid_confirmed_fill_domains"] == 1
    assert result["fill_ledger"]["invalid_order_status_domains"] == 1
    assert "confirmed_fill_domain_invalid" in codes
    assert "order_execution_domain_invalid" in codes


def test_legacy_unavailable_order_remains_high_evidence_gap_and_out_of_pnl(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "golden-test" / "trades.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT)")
    ledger = ExecutionLedger(db_path, strategy_name="golden-test")
    submission_id = ledger.record_submission(
        token_id="token",
        side="BUY",
        requested_price=0.4,
        requested_size=1,
        result={"success": True, "orderID": "legacy-missing"},
        simulation=False,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_submissions SET response_status = 'LEGACY_ASSUMED', "
            "submitted_at = '2026-07-10T00:00:00+00:00' "
            "WHERE submission_id = ?",
            (submission_id,),
        )
    ledger.mark_legacy_unavailable(submission_id)

    result = audit_database(
        db_path, days=30, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    codes = {issue["code"] for issue in result["issues"]}

    assert result["fill_ledger"]["legacy_unavailable_evidence_gaps"] == 1
    assert result["fill_ledger"]["confirmed_fill_gross_pnl_usdc"] is None
    assert result["fill_ledger"]["confirmed_fill_net_pnl_usdc"] is None
    assert "legacy_order_evidence_gap" in codes
    assert next(
        issue for issue in result["issues"]
        if issue["code"] == "legacy_order_evidence_gap"
    )["severity"] == "HIGH"


def test_staggered_two_row_markets_fail_history_depth_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    db_path.parent.mkdir(parents=True)
    _create_domain_tables(db_path)
    period_start = datetime(2026, 7, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(144):
        first = period_start + timedelta(minutes=index * 10)
        rows.extend(
            [
                (f"condition-{index}", first.isoformat()),
                (f"condition-{index}", (first + timedelta(minutes=5)).isoformat()),
            ]
        )
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM market_snapshots")
        connection.executemany(
            "INSERT INTO market_snapshots(condition_id, timestamp) VALUES (?, ?)",
            rows,
        )
    run = RunAudit.start(Config(db_path=db_path), strategy_name="golden-honeydew")
    run.succeed()

    result = audit_database(
        db_path,
        days=1,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    snapshots = result["market_snapshots"]
    assert snapshots["five_minute_bucket_coverage_ratio"] == 1.0
    assert snapshots["per_market_cadence_p10"] == 1.0
    assert snapshots["per_market_history_depth_p10"] < 0.01
    assert any(issue["code"] == "archive_window_short" for issue in result["issues"])


def test_corrupt_snapshot_values_are_critical_even_when_rows_cover_buckets(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE market_snapshots (
                id INTEGER PRIMARY KEY, condition_id TEXT, timestamp TEXT,
                probability REAL, liquidity REAL, volume_24h REAL,
                best_bid REAL, best_ask REAL, spread REAL
            );
            INSERT INTO trades(status) VALUES ('HOLDING');
            INSERT INTO market_snapshots VALUES
                (1, 'bad-probability', '2026-07-30T00:00:00', 1.2, 10, 10, 0.4, 0.5, 0.1),
                (2, 'bad-liquidity', '2026-07-30T00:05:00', 0.5, -1, -2, 0.4, 0.5, 0.1),
                (3, 'bad-book', '2026-07-30T00:10:00', 0.5, 10, 10, 0.8, 0.7, 0.5),
                (4, 'non-finite', '2026-07-30T00:15:00', 'Infinity', 10, 10, 0.4, 0.5, 0.1);
            """
        )
    run = RunAudit.start(
        Config(db_path=db_path, simulation_mode=False),
        strategy_name="golden-honeydew",
    )
    run.succeed()

    result = audit_database(
        db_path, days=1, as_of=datetime(2026, 7, 31, tzinfo=timezone.utc)
    )
    snapshots = result["market_snapshots"]
    assert snapshots["invalid_value_rows"] == 4
    assert snapshots["invalid_value_reasons"]["probability"] == 2
    assert snapshots["invalid_value_reasons"]["bid_ask_order"] == 1
    assert snapshots["invalid_value_reasons"]["spread_consistency"] == 1
    assert any(
        issue["code"] == "archive_snapshot_domain_invalid"
        for issue in result["issues"]
    )


def test_sweep_digest_counts_and_catalog_metadata_are_validated(tmp_path: Path) -> None:
    db_path = tmp_path / "golden-honeydew" / "trades.db"
    db_path.parent.mkdir(parents=True)
    _create_domain_tables(db_path)
    run = RunAudit.start(Config(db_path=db_path), strategy_name="golden-honeydew")
    run.succeed()
    membership_payload = [
        {
            "condition_id": "condition-1",
            "raw_seen_count": 1,
            "qualified": True,
            "qualification_reason": "qualified",
        }
    ]
    digest = hashlib.sha256(
        json.dumps(
            membership_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE market_snapshots ADD COLUMN run_id TEXT")
        connection.execute(
            "UPDATE market_snapshots SET run_id = ?, timestamp = ?",
            (run.run_id, "2026-07-30T00:00:01"),
        )
        connection.executescript(
            """
            CREATE TABLE market_sweeps (
                sweep_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
                run_id TEXT, started_at TEXT, completed_at TEXT,
                cursor_complete INTEGER, pages INTEGER, raw_market_count INTEGER,
                unique_condition_count INTEGER, qualified_market_count INTEGER,
                missing_condition_id_count INTEGER, duplicate_raw_count INTEGER,
                excluded_condition_count INTEGER, exclusion_counts_json TEXT,
                min_liquidity REAL, min_volume REAL,
                membership_digest_sha256 TEXT, snapshotted_market_count INTEGER
            );
            CREATE TABLE market_sweep_memberships (
                sweep_id TEXT, condition_id TEXT, raw_seen_count INTEGER,
                qualified INTEGER, qualification_reason TEXT,
                snapshot_eligible INTEGER, snapshotted INTEGER,
                snapshot_reason TEXT,
                PRIMARY KEY (sweep_id, condition_id)
            );
            CREATE TABLE market_catalog (
                condition_id TEXT PRIMARY KEY, event_id TEXT, event_slug TEXT,
                end_date TEXT, outcomes_json TEXT, token_ids_json TEXT,
                tags_json TEXT, fees_enabled INTEGER, fee_rate REAL
            );
            """
        )
        connection.execute(
            "INSERT INTO market_sweeps VALUES "
            "(?, 1, ?, ?, ?, 1, 1, 1, 1, 1, 0, 0, 0, '{}', 0, 0, ?, 1)",
            (
                "sweep-1",
                run.run_id,
                "2026-07-30T00:00:00",
                "2026-07-30T00:00:01",
                digest,
            ),
        )
        connection.execute(
            "INSERT INTO market_sweep_memberships VALUES "
            "('sweep-1', 'condition-1', 1, 1, 'qualified', 1, 1, 'snapshotted')"
        )
        connection.execute(
            "INSERT INTO market_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "condition-1",
                "event-1",
                "event",
                "2026-08-01T00:00:00Z",
                '["Yes", "No"]',
                '["token-yes", "token-no"]',
                '[{"slug": "politics"}]',
                0,
                None,
            ),
        )

    valid = audit_database(
        db_path,
        days=1,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert valid["market_sweeps"]["valid_complete_sweeps"] == 1
    assert valid["market_sweeps"]["invariant_failures"] == 0
    assert valid["market_catalog"]["qualified_condition_coverage_ratio"] == 1.0
    assert valid["market_catalog"]["metadata_completeness_ratio"] == 1.0

    degraded_payload = [
        *membership_payload,
        {
            "condition_id": "condition-2",
            "raw_seen_count": 1,
            "qualified": True,
            "qualification_reason": "qualified",
        },
    ]
    degraded_digest = hashlib.sha256(
        json.dumps(
            degraded_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO market_sweep_memberships VALUES "
            "('sweep-1', 'condition-2', 1, 1, 'qualified', 0, 0, "
            "'missing_outcome_prices')"
        )
        connection.execute(
            "UPDATE market_sweeps SET raw_market_count = 2, "
            "unique_condition_count = 2, qualified_market_count = 2, "
            "membership_digest_sha256 = ?",
            (degraded_digest,),
        )
        connection.execute(
            "INSERT INTO market_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "condition-2", "event-2", "event-2",
                "2026-08-01T00:00:00Z", '["Yes", "No"]',
                '["token-2-yes", "token-2-no"]',
                '[{"slug": "politics"}]', 0, None,
            ),
        )
    degraded = audit_database(
        db_path,
        days=1,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert degraded["market_sweeps"]["invariant_failures"] == 0
    assert degraded["market_sweeps"]["qualified_snapshot_eligibility_ratio"] == 0.5
    assert any(issue["code"] == "archive_window_short" for issue in degraded["issues"])

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE market_sweeps SET started_at = '2026-07-30T00:00:02', "
            "completed_at = '2026-07-30T00:00:01', "
            "min_liquidity = 'NaN', min_volume = -1"
        )
    invalid = audit_database(
        db_path,
        days=1,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert invalid["market_sweeps"]["invariant_failures"] == 1
    failed = set(
        invalid["market_sweeps"]["invariant_failure_details"][0]["failed"]
    )
    assert "timestamp_order" in failed
    assert "finite_nonnegative_filters" in failed
    assert any(
        issue["code"] == "market_sweep_attestation_invalid"
        for issue in invalid["issues"]
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE market_sweeps SET started_at = '2026-07-30T00:00:00', "
            "completed_at = '2026-07-30T00:00:01', "
            "min_liquidity = 0, min_volume = 0, "
            "membership_digest_sha256 = 'tampered'"
        )
    tampered = audit_database(
        db_path,
        days=1,
        as_of=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert tampered["market_sweeps"]["invariant_failures"] == 1
    assert "membership_digest_sha256" in tampered["market_sweeps"][
        "invariant_failure_details"
    ][0]["failed"]
