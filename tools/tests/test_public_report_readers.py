"""Public-body migration parity on synthetic pins; no real data or network."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import test_guava_collection_health as guava_fixture
import test_sports_payoff_gap_replay as replay_fixture
import test_sports_trade_report as trade_fixture
from public_market_reader import public_references
from polybot_observability.market_data_refs import MissingMarketDataConfiguration, PayloadReferences
from polybot_observability.market_data_store import MissingPayloadError, PayloadStore


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def externalize(path, public_store, columns):
    with PayloadStore(public_store) as store, sqlite3.connect(path) as connection:
        references = PayloadReferences(reader=store, writer=store)
        for table, column in columns:
            rows = connection.execute(f'SELECT rowid, "{column}" FROM "{table}"').fetchall()
            for rowid, original in rows:
                marker = references.encode_many([original])[0]
                connection.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (marker, rowid))


def ledger_rows(path):
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in ("trades", "order_submissions", "order_fills")
        }


def trade_pin(root):
    path = root / "pinned" / "snapshot" / "trades.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE run_audits(run_id TEXT PRIMARY KEY,started_at,finished_at,status,config_hash,job_name,strategy_name,mode,cycle_stats_json);
            CREATE TABLE strategy_configs(config_hash,config_json);
            CREATE TABLE trades(id,condition_id,event_id,event_slug,question,outcome,token_id,buy_order_id,sell_order_id,buy_timestamp,status,sport_family,realized_pnl);
            CREATE TABLE market_catalog(condition_id,event_id,event_title,sport_family,league_code,token_ids_json);
            CREATE TABLE order_submissions(submission_id,order_id,token_id,side,simulation,latest_order_status,latest_size_matched,needs_reconciliation,reconciliation_error,run_id,submitted_at);
            CREATE TABLE order_fills(submission_id,order_id,trade_id,bucket_index,status,side,size,price,fee_amount_usdc,fee_rate_bps,liquidity_role,matched_at,domain_error);
            CREATE TABLE resolution_observations(condition_id,selected_token_id,selected_outcome,selected_payout,winner_index,winner_token_id,winner_outcome,observed_at,evidence_json,evidence_sha256);
        """)
        for side, hour, size, price, fee in [("BUY", 14, 10, .9, .1), ("SELL", 15, 4, .95, .02)]:
            at = f"2026-09-07T{hour}:00:00Z"
            connection.execute("INSERT INTO run_audits VALUES(?,?,?,?,?,?,?,?,?)", (
                "run-" + side, at, at, "SUCCESS", "cfg", "runtime-mlb", "golden-peach", "live", "{}",
            ))
            submission = trade_fixture.submission(side, size, run_id="run-" + side, submitted_at=at)
            fill = trade_fixture.fill(side, size, price, fee, at)
            for table, record in [("order_submissions", submission), ("order_fills", fill)]:
                columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
                connection.execute(
                    f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})", [record.get(key) for key in columns],
                )
        connection.execute("INSERT INTO strategy_configs VALUES(?,?)", (
            "cfg", json.dumps({"trading": {"sport_family": "mlb", "strategy_source_digest": "fixture-code"}}),
        ))
        connection.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            1, "c", "e", "a-b", "Will A win?", "Yes", "token-yes", "order-BUY", "order-SELL",
            "2026-09-07T14:00:00Z", "COMPLETED", "mlb", 99999,
        ))
        connection.execute("INSERT INTO market_catalog VALUES(?,?,?,?,?,?)", (
            "c", "e", "A vs B", "mlb", "mlb", '["token-yes","token-no"]',
        ))
        evidence = json.dumps({"closed": True, "tokens": [
            {"token_id": "token-yes", "outcome": "Yes", "price": 1, "winner": True},
            {"token_id": "token-no", "outcome": "No", "price": 0, "winner": False},
        ]}, separators=(",", ":"), sort_keys=True)
        connection.execute("INSERT INTO resolution_observations VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "c", "token-yes", "Yes", 1, 0, "token-yes", "Yes", "2026-09-07T16:00:00Z",
            evidence, hashlib.sha256(evidence.encode()).hexdigest(),
        ))
    source = {
        "db_path": str(path), "mode": "live", "pinned": True, "source_key": "fixture-source",
        "strategy": "golden-peach", "jenkins_job": "fixture-job", "runtime_job": "runtime-mlb",
        "timestamp_contract": "repository_sqlite_utc",
    }

    def seal():
        source["sha256"] = file_sha(path)
        path.with_name("manifest.json").write_text(json.dumps({
            "pinned_path": str(path), "source_key": source["source_key"],
            "sha256": source["sha256"], "quick_check": ["ok"],
        }))

    seal()
    inputs = {"schema": "sports-trade-report-inputs-v1", "sources": [source]}
    return path, inputs, seal


@pytest.fixture(params=["trade", "replay", "guava"])
def public_case(request, tmp_path, monkeypatch):
    if request.param == "trade":
        path, inputs, seal = trade_pin(tmp_path)
        module = trade_fixture.report
        class ReportClock(datetime):
            current = datetime.fromisoformat("2026-09-29T12:00:00+00:00")

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz)

        monkeypatch.setattr(module, "datetime", ReportClock)
        start, end = trade_fixture.START.isoformat(), trade_fixture.END.isoformat()
        analyze = lambda references: module.build_report(inputs, start=start, end=end, references=references)
        columns = [("resolution_observations", "evidence_json")]

        def cli(output):
            input_path = tmp_path / "inputs.json"
            input_path.write_text(json.dumps(inputs))
            return ["--inputs", str(input_path), "--start", start, "--end", end, "--output", str(output)]

        output = tmp_path / "trade-output"
        result_path = output / "report.json"
    elif request.param == "replay":
        db = replay_fixture.make_db(tmp_path)
        replay_fixture.add_run(db)
        replay_fixture.add_run(db, run="r1", seconds=60)
        path, module = db["path"], replay_fixture.replay
        start, end = replay_fixture.T0.isoformat(), replay_fixture.END.isoformat()
        analyze = lambda references: module.analyze_sources(
            [path], replay_fixture.T0, replay_fixture.END, references=references,
        )
        seal = lambda: None
        columns = [("market_snapshots", "book_json")]
        cli = lambda output: ["--db", str(path), "--start", start, "--end", end, "--output", str(output)]
        output = result_path = tmp_path / "replay.json"
    else:
        path = guava_fixture.fixture(tmp_path)
        module = guava_fixture.health
        start, end = guava_fixture.START, guava_fixture.END
        analyze = lambda references: module.analyze([path], start, end, references=references)
        seal = lambda: guava_fixture.seal(path)
        columns = [("source_requests", "payload_gzip"), ("events", "raw_gzip"), ("book_attempts", "raw_gzip")]
        cli = lambda output: ["--db", str(path), "--start", start, "--end", end, "--output", str(output)]
        output = result_path = tmp_path / "guava.json"
    return {
        "kind": request.param, "path": path, "analyze": analyze, "seal": seal, "columns": columns,
        "module": module, "cli": cli, "output": output, "result_path": result_path,
    }


def test_public_report_results_match_inline_bodies(public_case, tmp_path):
    case = public_case
    path = case["path"]
    baseline = case["analyze"](PayloadReferences())
    before_sha, before_bytes = file_sha(path), path.stat().st_size
    ledger_before = ledger_rows(path) if case["kind"] == "trade" else None
    public_store = tmp_path / "public.sqlite"
    externalize(path, public_store, case["columns"])
    case["seal"]()
    after_sha = file_sha(path)
    assert before_sha != after_sha
    assert path.stat().st_size == before_bytes
    with public_references(public_store) as references:
        actual = case["analyze"](references)
    assert file_sha(path) == after_sha
    # The same pin was converted in place; only its physical-byte SHA changes.
    normalized = json.loads(json.dumps(actual).replace(after_sha, before_sha))
    assert normalized == json.loads(json.dumps(baseline))
    if case["kind"] == "trade":
        assert ledger_rows(path) == ledger_before
        economics = actual["sources"][0]["positions"][0]["economics"]
        assert case["module"].number(economics["realized_sold_net_in_window"]) == case["module"].number(".14")
        assert case["module"].number(economics["settlement_value_net_in_window"]) == case["module"].number(".54")
        assert actual["sources"][0]["positions"][0]["trade_pnl_column_used"] is False
    elif case["kind"] == "replay":
        assert actual[0]["actual_fill_or_realized_pnl"] is False
        assert replay_fixture.case(actual[1])["gross_gap_per_share"] == pytest.approx(.05)
    else:
        assert actual["integrity_pass"] is True
        assert actual["totals"]["observed_books"] == 2


def test_externalized_reports_fail_closed_without_payload_dependency(public_case, tmp_path):
    case = public_case
    externalize(case["path"], tmp_path / "public.sqlite", case["columns"])
    case["seal"]()
    with pytest.raises(MissingMarketDataConfiguration):
        case["analyze"](PayloadReferences())
    missing_store = tmp_path / "empty-public.sqlite"
    with PayloadStore(missing_store):
        pass
    with public_references(missing_store) as references:
        with pytest.raises(MissingPayloadError):
            case["analyze"](references)


def test_report_cli_resolves_explicit_public_store(public_case, tmp_path, monkeypatch):
    case = public_case
    monkeypatch.delenv("PUBLIC_MARKET_DATA_DB", raising=False)
    monkeypatch.delenv("PUBLIC_MARKET_DATA_SOCKET", raising=False)
    monkeypatch.delenv("PUBLIC_MARKET_DATA_REQUIRED", raising=False)
    public_store = tmp_path / "public.sqlite"
    externalize(case["path"], public_store, case["columns"])
    case["seal"]()
    command = [sys.executable, str(Path(case["module"].__file__).resolve()), *case["cli"](case["output"])]
    failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert failed.returncode != 0
    assert "market-data reader" in failed.stderr
    assert not case["result_path"].exists()
    success = subprocess.run(command + ["--public-store", str(public_store)], capture_output=True, text=True, timeout=15)
    assert success.returncode == (1 if case["kind"] == "guava" else 0), success.stderr
    actual = json.loads(case["result_path"].read_text())
    if case["kind"] == "trade":
        case["module"].datetime.current = datetime.fromisoformat(actual["created_at"].replace("Z", "+00:00"))
    with public_references(public_store) as references:
        expected = case["analyze"](references)
    if case["kind"] == "replay":
        expected = expected[0]
        csv_path = case["output"].with_suffix(".rows.csv")
        assert csv_path.exists()
        expected["rows_csv"] = str(csv_path)
    assert actual == json.loads(json.dumps(expected))
