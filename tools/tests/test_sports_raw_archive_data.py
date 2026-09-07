"""Raw/full-book evidence must survive filters without fabricated quotes."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "sports_visual_data", Path(__file__).resolve().parents[1] / "sports_visual_data.py"
)
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
START, END = "2026-09-07T00:00:00Z", "2026-09-08T00:00:00Z"
DIGEST = "d" * 64


class RawFixture:
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.executescript("""
        CREATE TABLE strategy_configs(config_hash TEXT, mode TEXT, config_json TEXT);
        CREATE TABLE run_audits(run_id TEXT,job_name TEXT,mode TEXT,config_hash TEXT,status TEXT,started_at TEXT,finished_at TEXT);
        CREATE TABLE raw_book_cycles(run_id TEXT,config_hash TEXT,strategy_source_digest TEXT,job_name TEXT,sport_family TEXT,contract TEXT,observed_at TEXT,published_at TEXT,status TEXT,expected_events INTEGER,expected_tokens INTEGER,observed_tokens INTEGER,evidence_json TEXT);
        CREATE TABLE raw_event_observations(observation_id TEXT,run_id TEXT,event_id TEXT,status TEXT,reason TEXT,expected_tokens INTEGER,identified_tokens INTEGER,observed_at TEXT,evidence_json TEXT);
        CREATE TABLE raw_book_observations(observation_id TEXT,run_id TEXT,event_id TEXT,slot TEXT,condition_id TEXT,token_id TEXT,status TEXT,reason TEXT,requested_at TEXT,received_at TEXT,book_json TEXT,book_sha256 TEXT);
        """)
        cfg = {
            "trading": {
                "sport_family": "soccer",
                "strategy_source_digest": DIGEST,
                "sport_profile_version": "profile",
                "preregistration_sha256": "protocol",
                "classifier_version": "classifier",
                "league_mapping_sha256": "mapping",
            }
        }
        self.c.execute(
            "INSERT INTO strategy_configs VALUES('cfg','sim',?)", (json.dumps(cfg),)
        )
        self.slots = {
            f"{kind}:{side}": {
                "condition_id": kind,
                "token_id": f"{kind}-{side}",
                "outcome": side.title(),
            }
            for kind in ("HOME", "DRAW", "AWAY")
            for side in ("YES", "NO")
        }

    def cycle(
        self,
        run="raw",
        *,
        status="SUCCESS",
        missing=(),
        bid_only=(),
        unknown=(),
        resolved=False,
    ):
        observed = "2026-09-07 01:00:00" if not resolved else "2026-09-07 03:00:00"
        receipt = "2026-09-07 01:00:01" if not resolved else "2026-09-07 03:00:01"
        self.c.execute(
            "INSERT INTO run_audits VALUES(?,'shadow','sim','cfg',?,?,?)",
            (run, status, observed, receipt),
        )
        slots = {k: v for k, v in self.slots.items() if k not in unknown}
        payload = {
            "contract": "full-sports-raw-v1",
            "event": {
                "id": "event",
                "title": "Home vs Away",
                "period": "1H",
                "elapsed": "5",
                "active": True,
                "closed": resolved,
                "live": not resolved,
                "ended": resolved,
            },
            "slots": slots,
            "market_context": [
                {
                    "conditionId": kind,
                    "closed": resolved,
                    "active": True,
                    "enableOrderBook": True,
                    "acceptingOrders": not resolved,
                    "volumeNum": 10000,
                    "feesEnabled": True,
                    "feeSchedule": {"rate": 0.05, "exponent": 1},
                }
                for kind in ("HOME", "DRAW", "AWAY")
            ],
            "terminal_proofs": [],
        }
        if resolved:
            for kind in ("HOME", "DRAW", "AWAY"):
                tokens = [
                    {
                        "token_id": f"{kind}-{side}",
                        "outcome": side.title(),
                        "probability": int((kind == "HOME") == (side == "YES")),
                        "token_index": i,
                    }
                    for i, side in enumerate(("YES", "NO"))
                ]
                payload["terminal_proofs"].append(
                    {
                        "condition_id": kind,
                        "tokens": tokens,
                        "proof": {"yes_payout": tokens[0]["probability"]},
                    }
                )
        self.c.execute(
            "INSERT INTO raw_event_observations VALUES(?,?,'event',?,?,?, ?,?,?)",
            (
                run + "-event",
                run,
                "RESOLVED" if resolved else "LIVE",
                "exact_all_condition_terminal_proofs"
                if resolved
                else "current_discovery",
                0 if resolved else 6,
                len(slots),
                observed,
                json.dumps(payload),
            ),
        )
        self.c.execute(
            "INSERT INTO raw_book_cycles VALUES(?,'cfg',?,'shadow','soccer','full-sports-raw-v1',?,?,'COMPLETE',1,?,?, '{}')",
            (
                run,
                DIGEST,
                observed,
                receipt,
                0 if resolved else 6,
                0 if resolved else 6 - len(missing) - len(unknown),
            ),
        )
        if not resolved:
            for slot, anchor in self.slots.items():
                state = (
                    "IDENTITY_MISSING"
                    if slot in unknown
                    else "MISSING"
                    if slot in missing
                    else "EMPTY_ASKS"
                    if slot in bid_only
                    else "FULL"
                )
                book = (
                    None
                    if slot in missing or slot in unknown
                    else json.dumps(
                        {
                            "token_id": anchor["token_id"],
                            "asks": []
                            if slot in bid_only
                            else [{"price": 0.6, "size": 20}],
                            "bids": [
                                {
                                    "price": 0.99 if slot in bid_only else 0.59,
                                    "size": 20,
                                }
                            ],
                        }
                    )
                )
                self.c.execute(
                    "INSERT INTO raw_book_observations VALUES(?,?,'event',?,?,?,?,?,?,?,?,?)",
                    (
                        run + ":" + slot,
                        run,
                        slot,
                        None if slot in unknown else anchor["condition_id"],
                        None if slot in unknown else anchor["token_id"],
                        state,
                        "fixture",
                        observed,
                        receipt if book else None,
                        book,
                        hashlib.sha256(book.encode()).hexdigest() if book else None,
                    ),
                )
        self.c.commit()

    def legacy(self, run="old", at="2026-09-07 00:30:00"):
        self.c.executescript("""CREATE TABLE IF NOT EXISTS market_snapshots(id INTEGER,condition_id TEXT,event_id TEXT,token_id TEXT,outcome TEXT,timestamp TEXT,book_json TEXT,run_id TEXT);
        CREATE TABLE IF NOT EXISTS market_catalog(condition_id TEXT,event_title TEXT,event_slug TEXT,question TEXT,league_code TEXT);
        """)
        if run == "old":
            self.c.execute(
                "INSERT INTO run_audits VALUES(?,'shadow','sim','cfg','SUCCESS',?,?)",
                (run, at, at),
            )
        self.c.execute(
            "INSERT INTO market_snapshots VALUES(1,'HOME','event','HOME-YES','Yes',?,?,?)",
            (
                at,
                json.dumps(
                    {
                        "token_id": "HOME-YES",
                        "asks": [{"price": 0.4, "size": 20}],
                        "bids": [{"price": 0.39, "size": 20}],
                    }
                ),
                run,
            ),
        )
        self.c.commit()


class RawReaderTests(unittest.TestCase):
    def test_raw_only_without_mutable_catalog_keeps_six_direct_books(self):
        f = RawFixture()
        f.cycle()
        rows = list(v.trading_rows(f.c, START, END))
        self.assertEqual(len(rows), 6)
        self.assertTrue(
            all(
                r["raw_point_in_time_identity_proven"] and r["raw_entry_set_complete"]
                for r in rows
            )
        )
        self.assertEqual({r["outcome_side"] for r in rows}, {"YES", "NO"})
        self.assertTrue(all(r["title"] == "Home vs Away" for r in rows))
        self.assertEqual(
            rows[0]["point_in_time_market_fields"]["feeSchedule"]["rate"], 0.05
        )

    def test_raw_supersedes_same_run_legacy_without_filling_missing_quote(self):
        f = RawFixture()
        f.cycle(missing=("HOME:YES",))
        f.legacy()
        f.legacy("raw", "2026-09-07 01:00:00")
        rows = list(v.trading_rows(f.c, START, END))
        self.assertEqual(len(rows), 7)
        missing = next(r for r in rows if r.get("raw_observation_status") == "MISSING")
        self.assertEqual(missing["book"], {})
        self.assertIsNone(missing["best_ask"])
        self.assertEqual(sum(r["run_id"] == "old" for r in rows), 1)

    def test_bid_only_is_real_quote_and_missing_is_not_empty_liquidity(self):
        f = RawFixture()
        f.cycle(bid_only=("HOME:YES",), missing=("DRAW:YES",))
        rows = list(v.trading_rows(f.c, START, END))
        bid = next(r for r in rows if r["token_id"] == "HOME-YES")
        missing = next(r for r in rows if r["token_id"] == "DRAW-YES")
        self.assertEqual(bid["book"]["asks"], [])
        self.assertEqual(bid["best_bid"], 0.99)
        self.assertTrue(bid["raw_book_valid"])
        self.assertNotIn("asks", missing["book"])
        self.assertFalse(missing["raw_book_valid"])
        self.assertFalse(missing["raw_book_integrity_error"])

    def test_failed_run_and_unknown_expected_slots_are_preserved(self):
        f = RawFixture()
        f.cycle(status="FAILED", unknown=("AWAY:YES", "AWAY:NO"))
        rows = list(v.trading_rows(f.c, START, END))
        self.assertEqual(len(rows), 6)
        missing = [r for r in rows if r["token_id"] is None]
        self.assertEqual(len(missing), 2)
        self.assertEqual({r["outcome_side"] for r in missing}, {"YES", "NO"})
        self.assertTrue(
            all(not r["raw_point_in_time_identity_proven"] for r in missing)
        )
        self.assertEqual(v.read_runs(f.c, False)["raw"]["status"], "FAILED")

    def test_hash_corruption_does_not_create_trusted_prices(self):
        f = RawFixture()
        f.cycle()
        f.c.execute(
            "UPDATE raw_book_observations SET book_sha256='bad' WHERE token_id='HOME-YES'"
        )
        row = next(
            r for r in v.trading_rows(f.c, START, END) if r["token_id"] == "HOME-YES"
        )
        self.assertFalse(row["raw_book_valid"])
        self.assertTrue(row["raw_book_integrity_error"])
        self.assertEqual(row["book"], {})
        self.assertIsNotNone(row["raw_book_json"])

    def test_atomic_count_and_token_mapping_tamper_are_invalid(self):
        f = RawFixture()
        f.cycle()
        f.c.execute("UPDATE raw_book_cycles SET expected_tokens=7")
        self.assertTrue(
            all(
                not r["raw_point_in_time_identity_proven"]
                for r in v.trading_rows(f.c, START, END)
            )
        )
        f.c.execute("UPDATE raw_book_cycles SET expected_tokens=6")
        f.c.execute(
            "UPDATE raw_book_observations SET token_id='other'WHERE token_id='HOME-YES'"
        )
        row = next(
            r for r in v.trading_rows(f.c, START, END) if r["token_id"] == "other"
        )
        self.assertFalse(row["raw_point_in_time_identity_proven"])

    def test_native_clock_is_kept_verbatim_not_invented_minutes(self):
        f = RawFixture()
        f.cycle()
        row = next(v.trading_rows(f.c, START, END))
        self.assertIsNone(row["source_elapsed_minutes"])
        self.assertEqual(row["clock"]["source_sport_context"]["fields"]["elapsed"], "5")
        self.assertEqual(row["clock"]["raw_archive"]["timestamp_basis"], "BOOK_RECEIPT")

    def test_raw_empty_cycle_does_not_invent_event_or_book_rows(self):
        f = RawFixture()
        f.c.execute(
            "INSERT INTO run_audits VALUES('empty','shadow','sim','cfg','SUCCESS','2026-09-07 01:00:00','2026-09-07 01:00:01')"
        )
        f.c.execute(
            "INSERT INTO raw_book_cycles VALUES('empty','cfg',?,'shadow','soccer','full-sports-raw-v1','2026-09-07 01:00:00','2026-09-07 01:00:01','EMPTY',0,0,0,'{}')",
            (DIGEST,),
        )
        self.assertEqual(list(v.trading_rows(f.c, START, END)), [])

    def test_half_open_receipt_range_and_expected_missing_timestamp(self):
        f = RawFixture()
        f.cycle(missing=("HOME:YES",))
        rows = list(v.trading_rows(f.c, "2026-09-07T01:00:00Z", "2026-09-07T01:00:01Z"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["raw_observation_status"], "MISSING")
        self.assertEqual(
            rows[0]["clock"]["raw_archive"]["timestamp_basis"],
            "EXPECTED_SLOT_CYCLE_REFERENCE_NOT_QUOTE",
        )


class RawTerminalTests(unittest.TestCase):
    def test_independent_terminal_proof_needs_no_trade_or_catalog(self):
        f = RawFixture()
        f.cycle(resolved=True)
        result = v.terminal_records(f.c, False, v.read_runs(f.c, False), end=END)
        self.assertEqual(len(result), 6)
        self.assertEqual(result[("HOME", "HOME-YES")][0]["payout"], 1)
        self.assertEqual(result[("AWAY", "AWAY-YES")][0]["payout"], 0)

    def test_failed_run_or_publication_after_cutoff_cannot_supply_terminal(self):
        f = RawFixture()
        f.cycle(resolved=True, status="FAILED")
        self.assertFalse(
            v.terminal_records(f.c, False, v.read_runs(f.c, False), end=END)
        )
        f.c.execute("UPDATE run_audits SET status='SUCCESS'")
        self.assertFalse(
            v.terminal_records(
                f.c, False, v.read_runs(f.c, False), end="2026-09-07T03:00:01Z"
            )
        )

    def test_terminal_token_label_swap_and_non_onehot_are_rejected(self):
        for swap in (True, False):
            f = RawFixture()
            f.cycle(resolved=True)
            payload = json.loads(
                f.c.execute(
                    "SELECT evidence_json FROM raw_event_observations"
                ).fetchone()[0]
            )
            tokens = payload["terminal_proofs"][0]["tokens"]
            if swap:
                tokens[0]["token_id"], tokens[1]["token_id"] = (
                    tokens[1]["token_id"],
                    tokens[0]["token_id"],
                )
            else:
                tokens[0]["probability"] = 0.99
                tokens[1]["probability"] = 0.01
            f.c.execute(
                "UPDATE raw_event_observations SET evidence_json=?",
                (json.dumps(payload),),
            )
            self.assertFalse(
                v.terminal_records(f.c, False, v.read_runs(f.c, False), end=END)
            )

    def test_each_binary_onehot_cannot_hide_impossible_soccer_triad(self):
        f = RawFixture()
        f.cycle(resolved=True)
        payload = json.loads(
            f.c.execute("SELECT evidence_json FROM raw_event_observations").fetchone()[
                0
            ]
        )
        payload["terminal_proofs"][1]["tokens"][0]["probability"] = 1
        payload["terminal_proofs"][1]["tokens"][1]["probability"] = 0
        f.c.execute(
            "UPDATE raw_event_observations SET evidence_json=?", (json.dumps(payload),)
        )
        self.assertFalse(
            v.terminal_records(f.c, False, v.read_runs(f.c, False), end=END)
        )


if __name__ == "__main__":
    unittest.main()


class RawExportTests(unittest.TestCase):
    def test_export_retains_failed_and_unidentified_expected_slot_rows(self):
        f = RawFixture()
        f.cycle(status="FAILED", unknown=("AWAY:YES", "AWAY:NO"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "raw.db"
            target = sqlite3.connect(path)
            f.c.backup(target)
            target.close()
            output = directory / "export"
            (output / "events").mkdir(parents=True)
            source = {
                "id": "grey:raw",
                "strategy": "golden-peach",
                "runtime_job": "shadow",
                "local_path": str(path),
                "local_sha256": v.sha256(path),
            }
            index = {"cohorts": {}, "matches": [], "sources": []}
            v.export_source(source, output, START, END, index)
            fragment = json.loads(
                (output / index["matches"][0]["fragment"]).read_text()
            )
            self.assertEqual(len(fragment["points"]), 6)
            self.assertTrue(
                all(point[8] & v.FLAGS["failed_run"] for point in fragment["points"])
            )
            self.assertEqual(
                sum(token["token_id"] is None for token in fragment["tokens"]), 2
            )

    def test_terminal_time_is_publication_bound_not_early_cycle_reference(self):
        f = RawFixture()
        f.cycle(resolved=True)
        f.c.execute("UPDATE raw_book_cycles SET published_at='2026-09-07 03:00:25'")
        f.c.execute("UPDATE run_audits SET finished_at='2026-09-07 03:00:26'")
        result = v.terminal_records(f.c, False, v.read_runs(f.c, False), end=END)
        proof = result[("HOME", "HOME-YES")][0]
        self.assertEqual(proof["observed_at"], "2026-09-07 03:00:25")
        self.assertEqual(proof["event_cycle_reference_at"], "2026-09-07 03:00:00")
        self.assertIn("PUBLICATION", proof["observed_at_basis"])


class RawReviewRegressionTests(unittest.TestCase):
    def test_reversed_request_receipt_and_after_publication_are_not_trusted(self):
        for changes in (
            "requested_at='2026-09-07 01:00:10'",
            "received_at='2026-09-07 01:00:30'",
            "received_at=NULL",
            "requested_at='2026-09-07 00:59:59'",
            "received_at='not-a-timestamp'",
        ):
            f = RawFixture()
            f.cycle()
            f.c.execute(
                "UPDATE raw_book_observations SET "
                + changes
                + " WHERE token_id='HOME-YES'"
            )
            rows = list(v.trading_rows(f.c, START, END))
            self.assertEqual(len(rows), 6)
            row = next(r for r in rows if r["token_id"] == "HOME-YES")
            self.assertFalse(row["raw_point_in_time_identity_proven"])
            self.assertFalse(row["raw_book_valid"])
            self.assertFalse(row["raw_quote_time_valid"])
            self.assertIsNone(row["best_bid"])
            self.assertIsNotNone(row["raw_book_json"])

    def test_condition_topology_cannot_mix_home_no_with_away_condition(self):
        f = RawFixture()
        f.cycle()
        payload = json.loads(
            f.c.execute("SELECT evidence_json FROM raw_event_observations").fetchone()[
                0
            ]
        )
        payload["slots"]["HOME:NO"]["condition_id"] = "AWAY"
        f.c.execute(
            "UPDATE raw_event_observations SET evidence_json=?", (json.dumps(payload),)
        )
        f.c.execute(
            "UPDATE raw_book_observations SET condition_id='AWAY' WHERE token_id='HOME-NO'"
        )
        self.assertTrue(
            all(
                not row["raw_entry_set_complete"]
                for row in v.trading_rows(f.c, START, END)
            )
        )

    def test_explicit_orderbook_disabled_or_event_closed_prevents_tradable_flag(self):
        for event_update, market_update in [
            ({"closed": True}, {}),
            ({"active": False}, {}),
            ({}, {"enableOrderBook": False}),
            ({}, {"enableOrderBook": None}),
        ]:
            f = RawFixture()
            f.cycle()
            payload = json.loads(
                f.c.execute(
                    "SELECT evidence_json FROM raw_event_observations"
                ).fetchone()[0]
            )
            payload["event"].update(event_update)
            for market in payload["market_context"]:
                market.update(market_update)
            f.c.execute(
                "UPDATE raw_event_observations SET evidence_json=?",
                (json.dumps(payload),),
            )
            rows = list(v.trading_rows(f.c, START, END))
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(row["raw_book_valid"] for row in rows))
            self.assertTrue(all(not row["raw_tradable_observed"] for row in rows))

    def test_good_raw_book_does_not_masquerade_as_legacy_event_cycle(self):
        f = RawFixture()
        f.cycle()
        row = next(v.trading_rows(f.c, START, END))
        self.assertTrue(row["raw_tradable_observed"])
        self.assertIsNone(row["event_cycle_id"])
        self.assertEqual(row["raw_evidence_origin"], "full-sports-raw-v1")
        self.assertEqual(row["raw_event_observation_id"], "raw-event")


class RawTimeRangeTests(unittest.TestCase):
    def test_invalid_future_receipt_survives_as_expected_slot_in_cycle_range(self):
        f = RawFixture()
        f.cycle()
        f.c.execute(
            "UPDATE raw_book_observations SET received_at='2026-09-07 01:30:00' WHERE token_id='HOME-YES'"
        )
        rows = list(v.trading_rows(f.c, "2026-09-07T01:00:00Z", "2026-09-07T01:00:02Z"))
        self.assertEqual(len(rows), 6)
        bad = next(r for r in rows if r["token_id"] == "HOME-YES")
        self.assertIsNone(bad["best_ask"])
        self.assertEqual(bad["timestamp"], "2026-09-07 01:00:00")
        self.assertEqual(
            bad["clock"]["raw_archive"]["received_at"], "2026-09-07 01:30:00"
        )
        self.assertEqual(
            bad["clock"]["raw_archive"]["timestamp_basis"],
            "EXPECTED_SLOT_CYCLE_REFERENCE_NOT_QUOTE",
        )


class RawTerminalTimeRegressionTests(unittest.TestCase):
    def test_terminal_publication_must_follow_reference_and_precede_run_finish(self):
        for table, change in (
            ("raw_book_cycles", "published_at='2026-09-07 02:59:59'"),
            ("run_audits", "finished_at='2026-09-07 02:59:59'"),
            ("run_audits", "started_at='2026-09-07 03:00:05'"),
            ("raw_event_observations", "observed_at='2026-09-07 02:59:59'"),
            ("raw_book_cycles", "published_at='invalid-time'"),
        ):
            f = RawFixture()
            f.cycle(resolved=True)
            f.c.execute("UPDATE " + table + " SET " + change)
            self.assertFalse(
                v.terminal_records(f.c, False, v.read_runs(f.c, False), end=END)
            )
