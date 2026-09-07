from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import watermelon_raw_sidecar as reader

spec = importlib.util.spec_from_file_location("white_raw_repo_fixture", TOOLS.parent / "golden-watermelon/src/polybot/db/raw_repository.py")
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


def moment(second):
    return f"2026-09-07T12:00:{second:02d}+00:00"


class RawSidecarReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "trades_sim.db"
        self.parent = sqlite3.connect(self.path)
        self.parent.row_factory = sqlite3.Row
        self.parent.executescript("""
        CREATE TABLE research_run_events(event_id TEXT,run_id TEXT,event_type TEXT,observed_at TEXT,config_hash TEXT,strategy_source_digest TEXT);
        CREATE TABLE research_config_versions(config_hash TEXT,strategy_source_digest TEXT,job_name TEXT,mode TEXT);
        CREATE TABLE api_requests(request_id TEXT,run_id TEXT,started_at TEXT,completed_at TEXT,response_sha256 TEXT,status TEXT);
        CREATE TABLE raw_payloads(payload_id TEXT,run_id TEXT,payload_kind TEXT,request_id TEXT,observed_at TEXT,sha256 TEXT,payload_gzip BLOB);
        CREATE TABLE orderbook_snapshots(snapshot_id TEXT,run_id TEXT,token_id TEXT,request_id TEXT,observed_at TEXT,raw_book_sha256 TEXT);
        """)
        self.parent.executemany("INSERT INTO research_run_events VALUES(?,?,?,?,?,?)", [
            ("start", "r", "STARTED", moment(0), "cfg", "source"),
            ("end", "r", "SUCCEEDED", moment(9), "cfg", "source")])
        self.parent.execute("INSERT INTO research_config_versions VALUES('cfg','source','white','sim')")
        market = {"conditionId": "condition", "clobTokenIds": '["a","b"]', "outcomes": '["Team A","Team B"]',
                  "active": True, "closed": False, "acceptingOrders": True, "feeSchedule": {"rate": 0.05}}
        self.event = {"id": "123", "live": True, "ended": False, "markets": [market]}
        self.slots = [{"slot": f"OUTCOME_{i}", "condition_id": "condition", "token_id": token,
                       "outcome": label, "all_tokens": ["a", "b"], "all_outcomes": ["Team A", "Team B"]}
                      for i, (token, label) in enumerate((("a", "Team A"), ("b", "Team B")))]
        self.books = [{"asset_id": token, "market": "condition", "asks": [{"price": ".7", "size": "10"}],
                       "bids": [{"price": ".69", "size": "10"}]} for token in ("a", "b")]
        self.gamma_sha = self.payload("gamma", "GAMMA_EVENT_PAGE", [self.event], 1, 2)
        self.book_sha = self.payload("book", "CLOB_BOOK_BATCH", self.books, 3, 4)
        self.repo = producer.RawRepository(self.path)
        self.raw = self.repo.connection
        self.cycle = {"run_id": "r", "component_run_id": "component", "contract": reader.CONTRACT,
                      "parent_filename": self.path.name, "config_hash": "cfg", "source_digest": "source", "job_name": "white",
                      "reference_at": moment(0), "published_at": moment(8), "status": "PUBLISHED",
                      "expected_events": 1, "expected_tokens": 2, "stats_json": "{}"}
        self.event_row = {"run_id": "r", "event_id": "123", "family": "mlb", "metadata_status": "OBSERVED",
                          "metadata_received_at": moment(2), "metadata_request_id": "gamma", "source_kind": "PARENT_GAMMA_PAGE",
                          "source_ref_json": reader.canonical({"run_id": "r", "request_id": "gamma", "payload_kind": "GAMMA_EVENT_PAGE", "sha256": self.gamma_sha}),
                          "event_json": reader.canonical(self.event), "slots_json": reader.canonical(self.slots),
                          "identity_valid": 1, "lifecycle_state": "TRACKING", "terminal_json": None, "missing_count": 0}
        self.book_rows = []
        for slot, book in zip(self.slots, self.books):
            self.book_rows.append({"run_id": "r", "event_id": "123", **{k: slot[k] for k in ("slot", "condition_id", "token_id", "outcome")},
                "status": "FULL", "request_id": "book", "requested_at": moment(3), "received_at": moment(4),
                "book_sha256": hashlib.sha256(reader.canonical(book).encode()).hexdigest(), "source_kind": "PARENT_BOOK",
                "source_ref_json": reader.canonical({"run_id": "r", "request_id": "book", "payload_kind": "CLOB_BOOK_BATCH", "sha256": self.book_sha}),
                "point_in_time_identity_valid": 1, "error_type": None})

    def payload(self, request, kind, value, start, end):
        data = reader.canonical(value).encode()
        digest = hashlib.sha256(data).hexdigest()
        self.parent.execute("INSERT INTO raw_payloads VALUES(?,?,?,?,?,?,?)", (request, "r", kind, request, moment(end), digest, gzip.compress(data)))
        self.parent.execute("INSERT INTO api_requests VALUES(?,?,?,?,?,?)", (request, "r", moment(start), moment(end), digest, "SUCCESS"))
        return digest

    def rows(self, end="2026-09-08T00:00:00+00:00"):
        self.parent.commit()
        with self.repo.transaction() as c:
            producer.RawRepository.insert(c, "raw_cycles", self.cycle)
            producer.RawRepository.insert(c, "raw_events", self.event_row)
            for row in self.book_rows:
                producer.RawRepository.insert(c, "raw_books", row)
        reader.validate_contract(self.raw, self.path.name)
        return list(reader.iter_rows(self.parent, self.raw, "2026-09-07T00:00:00+00:00", end))

    def tearDown(self):
        self.repo.close()
        self.parent.close()
        self.temp.cleanup()

    def test_exact_parent_payloads_and_dual_publication(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["evidence_valid"] for r in rows), rows)
        self.assertTrue(all(r["market_open_observed"] for r in rows))
        self.assertEqual(rows[0]["market"]["feeSchedule"]["rate"], .05)

    def test_parent_failed_is_not_promoted_by_raw_publication(self):
        self.parent.execute("UPDATE research_run_events SET event_type='FAILED' WHERE event_id='end'")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(r["evidence_valid"] for r in rows))

    def test_later_metadata_cannot_prove_prior_book(self):
        self.book_rows[0]["requested_at"] = moment(1)
        row = self.rows()[0]
        self.assertFalse(row["evidence_valid"])
        self.assertIn("RECEIPT_ORDER_UNPROVEN", row["evidence_errors"])

    def test_cutoff_before_parent_success(self):
        self.assertFalse(any(r["evidence_valid"] for r in self.rows(end=moment(9))))

    def test_book_content_hash_mismatch_preserves_invalid_row(self):
        self.book_rows[0]["book_sha256"] = "0" * 64
        rows = self.rows()
        self.assertIsNone(rows[0]["book"])
        self.assertFalse(rows[0]["evidence_valid"])
        self.assertTrue(rows[1]["evidence_valid"])

    def test_event_copy_cannot_disagree_with_raw_payload(self):
        changed = deepcopy(self.event)
        changed["ended"] = True
        self.event_row["event_json"] = reader.canonical(changed)
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_missing_book_row_is_not_zero_profit(self):
        row = self.book_rows[0]
        row.update(status="NOT_ATTEMPTED", source_kind="NONE", source_ref_json="{}", point_in_time_identity_valid=0,
                   requested_at=None, received_at=None, request_id=None, book_sha256=None)
        result = self.rows()[0]
        self.assertEqual(result["status"], "NOT_ATTEMPTED")
        self.assertIsNone(result["book"])
        self.assertFalse(result["evidence_valid"])
        self.assertNotIn("pnl", result)

    def test_atomic_count_mismatch_fails(self):
        self.cycle["expected_tokens"] = 3
        with self.assertRaisesRegex(ValueError, "expected/observed"):
            self.rows()

    def test_wrong_parent_filename_refused(self):
        with self.assertRaisesRegex(ValueError, "parent"):
            reader.validate_contract(self.raw, "other.db")

    def test_wrong_pin_sha_refused(self):
        self.parent.commit()
        with self.assertRaisesRegex(ValueError, "SHA"):
            reader.open_pin(self.path, "0" * 64)

    def test_claimed_slot_label_must_match_raw_market(self):
        self.slots[0]["outcome"] = "Wrong"
        self.book_rows[0]["outcome"] = "Wrong"
        self.event_row["slots_json"] = reader.canonical(self.slots)
        self.assertFalse(self.rows()[0]["evidence_valid"])

    def test_foreign_parent_config_cannot_join_same_run_id(self):
        self.parent.execute("UPDATE research_config_versions SET mode='live'")
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_additional_raw_book_uses_sidecar_payload_and_parent_http_receipt(self):
        blob = reader.canonical(self.books).encode()
        producer.RawRepository.insert(self.raw, "raw_payloads", {"payload_id": "extra", "run_id": "r", "kind": "CLOB_BOOK_BATCH",
            "request_id": "book", "received_at": moment(4), "sha256": self.book_sha, "payload_gzip": gzip.compress(blob)})
        self.raw.commit()
        for row in self.book_rows:
            ref = json.loads(row["source_ref_json"])
            ref["payload_id"] = "extra"
            row.update(source_kind="RAW_BOOK", source_ref_json=reader.canonical(ref))
        self.assertTrue(all(r["evidence_valid"] for r in self.rows()))

    def test_claimed_full_book_cannot_hide_empty_side(self):
        self.book_rows[0]["status"] = "EMPTY_BOOK"
        self.assertFalse(self.rows()[0]["evidence_valid"])

    def test_http_failure_cannot_supply_successful_book(self):
        self.parent.execute("UPDATE api_requests SET status='FAILED' WHERE request_id='book'")
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_gamma_request_cannot_start_after_its_response(self):
        self.parent.execute("UPDATE api_requests SET started_at=? WHERE request_id='gamma'", (moment(7),))
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_reference_cannot_follow_publication(self):
        self.cycle["reference_at"] = moment(10)
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_cycle_parent_mismatch_fails_evidence(self):
        self.cycle["parent_filename"] = "other.db"
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_terminal_annotation_is_never_exported_as_verified_payout(self):
        self.event_row.update(terminal_json='{"payout":1}', lifecycle_state="RESOLVED")
        row = self.rows()[0]
        self.assertEqual(row["unverified_terminal_annotation_json"], '{"payout":1}')
        self.assertFalse(row["terminal_evidence_verified"])
        self.assertNotIn("terminal_evidence_json", row)

    def test_two_slot_names_cannot_represent_duplicate_token(self):
        self.slots[1].update(token_id="a", outcome="Team A")
        second = deepcopy(self.book_rows[0])
        second["slot"] = "OUTCOME_1"
        self.book_rows[1] = second
        self.event_row["slots_json"] = reader.canonical(self.slots)
        rows = self.rows()
        self.assertFalse(any(r["evidence_valid"] for r in rows))
        self.assertTrue(all("EXPECTED_SET_INVALID" in r["evidence_errors"] for r in rows))

    def test_failed_metadata_does_not_become_observed_via_flags(self):
        self.event_row["metadata_status"] = "ERROR"
        self.assertFalse(any(r["evidence_valid"] for r in self.rows()))

    def test_cli_cannot_overwrite_either_input_database(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "distinct"):
            reader.main(["--parent", str(self.path), "--sidecar", str(self.repo.path),
                         "--parent-sha256", "unused", "--sidecar-sha256", "unused",
                         "--start", moment(0), "--end", moment(30), "--output", str(self.path)])
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
