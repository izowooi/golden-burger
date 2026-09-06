"""Offline fixtures only. Never open a synced or production database."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "sports_payoff_gap_replay.py"
spec = importlib.util.spec_from_file_location("sports_payoff_gap_replay", SCRIPT)
replay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = replay
spec.loader.exec_module(replay)
T0 = replay.utc("2026-09-01T00:00:00Z")
END = T0 + timedelta(hours=1)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_db(tmp_path, name="pin.db", strategy="golden-plum", runtime=None, source="a" * 64):
    path = tmp_path / name
    runtime = runtime or ("plum-shadow-silver-1m-v1" if strategy == "golden-plum" else "peach-shadow-1m-v1")
    config = {"schema_version": 1, "strategy_name": strategy, "mode": "sim",
              "trading": {"strategy_source_digest": source, "sport_family": "soccer",
                          "sport_profile_version": "fixture-soccer-v1", "book_shape": "direct-six-result-books"}}
    config_text = canonical(config)
    config_hash = hashlib.sha256(config_text.encode()).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE strategy_configs(config_hash TEXT PRIMARY KEY,strategy_name TEXT,mode TEXT,config_json TEXT);
            CREATE TABLE run_audits(run_id TEXT PRIMARY KEY,strategy_name TEXT,job_name TEXT,mode TEXT,config_hash TEXT,
                                   started_at TEXT,finished_at TEXT,status TEXT);
            CREATE TABLE market_catalog(condition_id TEXT PRIMARY KEY,event_id TEXT,outcomes_json TEXT,token_ids_json TEXT,
                                        outcome_prices_json TEXT,fee_rate REAL);
            CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,condition_id TEXT,event_id TEXT,token_id TEXT,
                outcome TEXT,outcome_side TEXT,result_kind TEXT,timestamp TEXT,book_json TEXT,run_id TEXT,
                sport_family TEXT,sport_profile_version TEXT,book_shape TEXT);
        """)
        connection.execute("INSERT INTO strategy_configs VALUES(?,?,?,?)", (config_hash, strategy, "sim", config_text))
    return {"path": path, "config_hash": config_hash, "strategy": strategy, "runtime": runtime}


def add_run(db, run="r0", event="event1", seconds=0, *, missing=(), overrides=None,
            status="SUCCESS", config_hash=None, skew=None):
    overrides = overrides or {}
    observed = T0 + timedelta(seconds=seconds)
    with sqlite3.connect(db["path"]) as connection:
        connection.execute("INSERT INTO run_audits VALUES(?,?,?,?,?,?,?,?)", (
            run, db["strategy"], db["runtime"], "sim", config_hash or db["config_hash"],
            replay.iso(observed-timedelta(seconds=2)), replay.iso(observed+timedelta(seconds=3)), status,
        ))
        for kind in replay.KINDS:
            condition = f"{event}-{kind}"
            tokens = [f"{condition}-YES", f"{condition}-NO"]
            connection.execute("INSERT OR IGNORE INTO market_catalog VALUES(?,?,?,?,?,?)", (
                condition, event, canonical(["Yes", "No"]), canonical(tokens), canonical([".5", ".5"]), .05,
            ))
            for side in replay.SIDES:
                if (kind, side) in missing:
                    continue
                token = f"{condition}-{side}"
                # HOME NO ask .4 buys q12.5 at $5; other YES bids .25+.20=.45.
                bid, ask = {("HOME", "NO"): (".39", ".4"), ("DRAW", "YES"): (".25", ".26"),
                            ("AWAY", "YES"): (".20", ".21")}.get((kind, side), (".30", ".31"))
                book = {"token_id": token, "bids": [{"price": bid, "size": "1000"}],
                        "asks": [{"price": ask, "size": "1000"}]}
                book.update(overrides.get((kind, side), {}))
                timestamp = observed + timedelta(seconds=(skew or {}).get((kind, side), 0))
                connection.execute("""INSERT INTO market_snapshots(condition_id,event_id,token_id,outcome,
                    outcome_side,result_kind,timestamp,book_json,run_id,sport_family,sport_profile_version,book_shape)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    condition, event, token, side.title(), side, kind, replay.iso(timestamp), canonical(book), run,
                    "soccer", "fixture-soccer-v1", "direct-six-result-books",
                ))


def analyze(db, end=END):
    return replay.analyze_sources([db["path"]], T0, end)


def case(rows, kind="HOME", rung=5, run="r0"):
    return next(r for r in rows if r["run_id"] == run and r["result_kind"] == kind and r["notional_usdc"] == rung)


def test_equal_share_gap_all_rungs_and_explicit_fee_stress(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    before = replay.digest_file(db["path"])
    report, rows = analyze(db)
    assert len(rows) == 15
    for rung in replay.RUNGS:
        row = case(rows, rung=rung)
        assert row["q_shares"] == pytest.approx(rung/.4)
        assert row["no_ask_cost"] == rung
        assert row["basket_bid_proceeds"] == pytest.approx((rung/.4)*.45)
        assert row["gross_gap_per_share"] == pytest.approx(.05)
        assert row["fee_stress"]["0.05"]["gap_per_share_after_stress"] == pytest.approx(
            .05-.05*(.4*.6+.25*.75+.20*.80)
        )
    cohort = report["cohorts"][0]
    assert cohort["complete_six_rate"] == 1
    assert cohort["rungs"]["5"]["threshold_events_strictly_greater"]["0.03"] == 1
    assert replay.digest_file(db["path"]) == before == report["sources"][0]["sha256_after"]
    assert not Path(str(db["path"])+"-wal").exists()


@pytest.mark.parametrize("strategy", ["golden-peach", "golden-plum"])
def test_both_realistic_snapshot_schemas_use_run_cohort_not_filename(tmp_path, strategy):
    db = make_db(tmp_path, strategy=strategy)
    add_run(db)
    if strategy == "golden-plum":
        with sqlite3.connect(db["path"]) as connection:
            connection.executescript("ALTER TABLE market_snapshots ADD COLUMN config_hash TEXT;"
                                     "ALTER TABLE market_snapshots ADD COLUMN strategy_source_digest TEXT;")
            connection.execute("UPDATE market_snapshots SET config_hash=?,strategy_source_digest=?", (db["config_hash"], "a"*64))
    report, rows = analyze(db)
    assert case(rows)["status"] == "observational_gross_only"
    assert report["cohorts"][0]["cohort"]["strategy"] == strategy


@pytest.mark.parametrize("bad_price", ["0", "1", "-0.1", "NaN", "Infinity", True])
def test_zero_terminal_invalid_prices_never_become_executable(tmp_path, bad_price):
    db = make_db(tmp_path)
    add_run(db, overrides={("HOME", "NO"): {"asks": [{"price": bad_price, "size": "100"}]}})
    report, rows = analyze(db)
    assert case(rows)["gross_gap_per_share"] is None
    assert report["cohorts"][0]["complete_six_rate"] == 0


def test_uses_actual_no_not_synthetic_complement(tmp_path):
    db = make_db(tmp_path)
    add_run(db, overrides={("HOME", "YES"): {"asks": [{"price": ".95", "size": "1000"}]}})
    _, rows = analyze(db)
    assert case(rows)["no_ask_vwap"] == .4  # Not 1-.95.


@pytest.mark.parametrize("kind,side,depth,status", [
    ("HOME", "NO", "1", "insufficient_no_ask_depth"),
    ("DRAW", "YES", "10", "insufficient_other_yes_bid_depth:DRAW"),
])
def test_insufficient_depth_is_not_filled_or_zero_priced(tmp_path, kind, side, depth, status):
    db = make_db(tmp_path)
    leg = "asks" if side == "NO" else "bids"
    add_run(db, overrides={(kind, side): {leg: [{"price": ".4" if side == "NO" else ".25", "size": depth}]}})
    _, rows = analyze(db)
    assert case(rows)["status"] == status
    assert case(rows)["gross_gap_per_share"] is None


def test_every_other_yes_must_supply_q_not_half_q(tmp_path):
    db = make_db(tmp_path)
    # q=12.5: 7 shares in each YES is not enough despite combined 14 > 12.5.
    add_run(db, overrides={(k, "YES"): {"bids": [{"price": ".2", "size": "7"}]}
                          for k in ("DRAW", "AWAY")})
    _, rows = analyze(db)
    assert case(rows)["status"].startswith("insufficient_other_yes_bid_depth")


def test_missing_no_keeps_denominator_and_all_case_rows(tmp_path):
    db = make_db(tmp_path)
    add_run(db, missing={("HOME", "NO")})
    report, rows = analyze(db)
    assert len(rows) == 15
    assert case(rows)["no_token_id"] is None
    assert report["cohorts"][0]["event_run_count"] == 1
    assert report["cohorts"][0]["complete_six_rate"] == 0


@pytest.mark.parametrize("tamper", ["book", "catalog", "outcome", "condition", "duplicate"])
def test_independent_token_outcome_and_triad_checks(tmp_path, tamper):
    db = make_db(tmp_path)
    add_run(db)
    with sqlite3.connect(db["path"]) as connection:
        if tamper == "book":
            connection.execute("UPDATE market_snapshots SET book_json=? WHERE outcome_side='NO'", (canonical({"token_id": "wrong", "asks": [], "bids": []}),))
        elif tamper == "catalog":
            connection.execute("UPDATE market_catalog SET token_ids_json=? WHERE condition_id='event1-HOME'", (canonical(["wrong", "event1-HOME-NO"]),))
        elif tamper == "outcome":
            connection.execute("UPDATE market_snapshots SET outcome='Yes' WHERE result_kind='HOME' AND outcome_side='NO'")
        elif tamper == "condition":
            connection.execute("UPDATE market_snapshots SET condition_id='event1-DRAW' WHERE result_kind='HOME' AND outcome_side='NO'")
        else:
            connection.execute("UPDATE market_snapshots SET result_kind='DRAW' WHERE result_kind='HOME'")
    _, rows = analyze(db)
    assert all(row["gross_gap_per_share"] is None for row in rows)


def test_unsigned_fee_and_time_metadata_never_claims_promotion(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    report, rows = analyze(db)
    row = case(rows)
    assert row["gross_gap_per_share"] is not None
    assert row["fee_evidence"] == "point_in_time_raw_fee_receipts_missing"
    assert "unverified" in row["simultaneity_evidence"]
    assert not row["promotion_ready"] and not report["promotion_ready"]
    assert report["payoff_reference_type"] == "conditional_payoff_reference"


def test_void_is_not_removed_after_observing_result(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    # Catalog already contains .5/.5 in all markets: no post-hoc exclusion.
    report, rows = analyze(db)
    assert report["cohorts"][0]["event_count"] == 1
    assert case(rows)["gross_gap_per_share"] is not None
    assert any("void" in text and "NegRisk" in text for text in report["limitations"])


def test_multiple_paths_same_bytes_deduplicated_distinct_sources_separate(tmp_path):
    first = make_db(tmp_path)
    add_run(first)
    copy = tmp_path / "copy.db"
    shutil.copyfile(first["path"], copy)
    other = make_db(tmp_path, "other.db", strategy="golden-peach", source="b"*64)
    add_run(other)
    report, rows = replay.analyze_sources([first["path"], first["path"], copy, other["path"]], T0, END)
    assert len(rows) == 30 and len(report["cohorts"]) == 2
    assert sum(s["duplicate_of"] is not None for s in report["sources"]) == 2
    assert report["unique_events_across_sources"] == 1


def test_threshold_denominator_is_event_not_repeated_minutes_or_no_rungs(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "r1", seconds=60)
    report, _ = analyze(db)
    rung = report["cohorts"][0]["rungs"]["5"]
    assert rung["event_denominator_all_observed"] == 1
    assert rung["case_count"] == 6
    assert rung["threshold_events_strictly_greater"]["0"] == 1


def test_future_no_progress_is_separate_from_rich_basket_decline(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "r1", seconds=61, overrides={
        ("HOME", "NO"): {"bids": [{"price": ".42", "size": "1000"}], "asks": [{"price": ".43", "size": "1000"}]},
        ("DRAW", "YES"): {"bids": [{"price": ".20", "size": "1000"}]},
    })
    _, rows = analyze(db)
    future = case(rows)["forward"]["1"]
    assert future["run_id"] == "r1" and future["delay_seconds"] == 1
    assert future["no_bid_change_per_share"] == pytest.approx(.03)
    assert future["no_bid_minus_entry_ask"] == pytest.approx(.02)
    assert future["no_markout_gross_usdc"] == pytest.approx(.25)
    assert future["basket_decline_per_share"] == pytest.approx(.05)


@pytest.mark.parametrize("seconds,found", [(59, False), (60, True), (210, True), (211, False)])
def test_future_first_at_or_after_target_with_150s_bound(tmp_path, seconds, found):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "future", seconds=seconds)
    _, rows = analyze(db)
    assert (case(rows)["forward"]["1"]["run_id"] == "future") is found


def test_future_half_open_end_and_no_interpolation(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "at-end", seconds=60)
    _, rows = analyze(db, T0+timedelta(seconds=60))
    assert case(rows)["forward"]["1"]["no_status"] == "outside_review_range"
    assert len(rows) == 15


def test_first_incomplete_future_is_not_skipped_for_later_good_quote(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "missing", seconds=60, missing={("HOME", "NO")})
    add_run(db, "later", seconds=70)
    _, rows = analyze(db)
    future = case(rows)["forward"]["1"]
    assert future["run_id"] == "missing"
    assert future["no_bid_vwap"] is None
    assert future["basket_bid_per_share"] is not None


def test_other_cohort_future_does_not_fill_gap(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    with sqlite3.connect(db["path"]) as connection:
        raw = connection.execute("SELECT config_json FROM strategy_configs").fetchone()[0]
        config = json.loads(raw)
        config["trading"]["strategy_source_digest"] = "b"*64
        raw = canonical(config)
        config_hash = hashlib.sha256(raw.encode()).hexdigest()
        connection.execute("INSERT INTO strategy_configs VALUES(?,?,?,?)", (config_hash, "golden-plum", "sim", raw))
    add_run(db, "other", seconds=60, config_hash=config_hash)
    report, rows = analyze(db)
    assert len(report["cohorts"]) == 2
    assert case(rows)["forward"]["1"]["run_id"] is None


def test_missing_or_unsigned_source_config_metadata_is_not_inferred(tmp_path):
    db = make_db(tmp_path, source="unknown")
    add_run(db)
    report, rows = analyze(db)
    assert case(rows)["gross_gap_per_share"] is None
    assert report["cohorts"][0]["complete_six_rate"] == 0


def test_failed_run_and_post_end_completion_are_not_success_evidence(tmp_path):
    db = make_db(tmp_path)
    add_run(db, status="FAILED")
    add_run(db, "late-complete", seconds=59)
    _, rows = analyze(db, T0+timedelta(seconds=60))
    assert all(row["gross_gap_per_share"] is None for row in rows)


def test_standalone_wal_requirement_and_source_mutation_detection(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    add_run(db)
    wal = Path(str(db["path"])+"-wal")
    wal.write_bytes(b"not-a-standalone-pin")
    with pytest.raises(replay.EvidenceError, match="WAL"):
        analyze(db)
    wal.unlink()
    original = replay.load_groups
    def mutate_fixture(*args):
        result = original(*args)
        with sqlite3.connect(db["path"]) as connection:
            connection.execute("UPDATE market_catalog SET fee_rate=.03")
        return result
    monkeypatch.setattr(replay, "load_groups", mutate_fixture)
    with pytest.raises(replay.EvidenceError, match="source changed"):
        analyze(db)


@pytest.mark.parametrize("value", ["2026-09-01", "2026-09-01T00:00:00", "2026-09-01T09:00:00+09:00"])
def test_cli_requires_absolute_utc(value):
    with pytest.raises(replay.EvidenceError):
        replay.utc(value)


def test_cli_new_json_and_complete_csv_no_overwrite(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    output = tmp_path / "output.json"
    args = ["--db", str(db["path"]), "--start", replay.iso(T0), "--end", replay.iso(END), "--output", str(output)]
    assert replay.main(args) == 0
    assert json.loads(output.read_text())["row_count"] == 15
    assert len(output.with_suffix(".rows.csv").read_text().splitlines()) == 16
    with pytest.raises(SystemExit) as error:
        replay.main(args)
    assert error.value.code == 2


def test_multilevel_walk_and_fee_stress_use_each_consumed_level(tmp_path):
    db = make_db(tmp_path)
    add_run(db, overrides={("HOME", "NO"): {"asks": [
        {"price": ".4", "size": "5"}, {"price": ".5", "size": "1000"},
    ]}})
    _, rows = analyze(db)
    row = case(rows)
    assert row["q_shares"] == 11
    assert row["basket_bid_proceeds"] == pytest.approx(11*.45)
    assert row["gross_gap_per_share"] == pytest.approx((11*.45-5)/11, abs=1e-12)
    assert row["fee_stress"]["0.05"]["assumed_three_leg_fees_usdc"] == pytest.approx(
        .05*(5*.4*.6 + 6*.5*.5 + 11*.25*.75 + 11*.2*.8)
    )


def test_zero_gap_and_threshold_boundary_do_not_gain_arithmetic_edge(tmp_path):
    db = make_db(tmp_path)
    add_run(db, overrides={
        ("HOME", "NO"): {"bids": [{"price": ".29", "size": "1000"}], "asks": [{"price": ".3", "size": "1000"}]},
        ("DRAW", "YES"): {"bids": [{"price": ".1", "size": "1000"}]},
    })
    _, rows = analyze(db)
    assert case(rows)["gross_gap_per_share"] == 0


def test_duplicate_json_key_is_not_last_value_wins():
    with pytest.raises(replay.EvidenceError):
        replay.decoded('{"token_id":"wrong","token_id":"right"}')


def test_future_must_stay_in_same_event_and_source(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "unrelated", event="event2", seconds=60)
    other = make_db(tmp_path, "other.db", source="b"*64)
    add_run(other, "other-source-future", seconds=60)
    _, rows = replay.analyze_sources([db["path"], other["path"]], T0, END)
    assert case(rows)["forward"]["1"]["run_id"] is None


def test_duplicate_same_event_time_is_not_arbitrarily_selected(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    add_run(db, "same-time", seconds=0)
    _, rows = analyze(db)
    assert all(r["gross_gap_per_share"] is None for r in rows)


def test_source_hash_and_snapshot_cohort_disagreement_rejected(tmp_path):
    db = make_db(tmp_path)
    add_run(db)
    with sqlite3.connect(db["path"]) as connection:
        connection.execute("UPDATE strategy_configs SET config_json=replace(config_json,'fixture-soccer-v1','tampered')")
    _, rows = analyze(db)
    assert case(rows)["gross_gap_per_share"] is None


def test_shallow_initial_basket_does_not_erase_independent_no_path(tmp_path):
    db = make_db(tmp_path)
    add_run(db, overrides={("DRAW", "YES"): {"bids": [{"price": ".2", "size": "1"}]}})
    add_run(db, "future", seconds=60)
    _, rows = analyze(db)
    row = case(rows)
    assert row["status"] == "insufficient_other_yes_bid_depth:DRAW"
    assert row["gross_gap_per_share"] is None
    future = row["forward"]["1"]
    assert future["no_bid_vwap"] == .39
    assert future["basket_bid_per_share"] == .45
    assert future["basket_bid_change_per_share"] is None
