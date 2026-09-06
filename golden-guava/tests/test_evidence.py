"""Independent stdlib tests: python -B golden-guava/tests/test_evidence.py.

Load only our owned module, never a sibling project's ``polybot`` package or the
concurrently authored Guava main/config/public clients.
"""

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "src/polybot/evidence.py"
SPEC = importlib.util.spec_from_file_location("guava_evidence_under_test", MODULE_PATH)
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)
Repository = evidence.Repository

T0 = "2026-09-06T01:00:00Z"
T1 = "2026-09-06T01:00:01Z"
T2 = "2026-09-06T01:00:02Z"
CONTRACT = {
    "strategy_name": "golden-guava", "job_name": "guava-fixture-shard-0",
    "mode": "sim", "data_contract": "guava-research-v1",
    "config_hash": "fixture-config-a", "strategy_source_digest": "fixture-source-a",
}
CONFIG = {"cadence_seconds": 60, "shard_index": 0, "shard_count": 4}


class FixtureBudgetExceeded(RuntimeError):
    pass


class FixtureBudget:
    """Structural require_commit protocol; no dependency on the changing main."""

    def __init__(self):
        self.expired = False
        self.calls = 0

    def require_commit(self):
        self.calls += 1
        if self.expired:
            raise FixtureBudgetExceeded("fixture publication budget exhausted")
        return 10.0


LONG_SQL = """WITH RECURSIVE numbers(n) AS (
    VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n<10000
) SELECT sum(n) FROM numbers"""


def event(event_id="event-1", tokens=None):
    return {
        "event_id": event_id, "sport_family": "soccer", "league_code": "EPL",
        "observed_at": T1, "raw": {"id": event_id, "score": "1-0",
            "markets": [{"conditionId": "condition-1", "rules": "90 minutes",
                         "clobTokenIds": ["public-yes", "public-no"]}]},
        "clock": {"period": "2H", "score": {"home": 1, "away": 0},
                  "source_timestamp": T0, "received_at": T1, "minute": 76,
                  "raw": {"elapsed": "76:13", "stoppage": None}},
        "eligible": True, "exclusion_reason": None,
        "expected_token_ids": ["public-yes", "public-no"] if tokens is None else tokens,
        "cohort_key": "runtime-supplied-label",
    }


def receipt(request_id="req-1"):
    return {"request_id": request_id, "source": "clob", "method": "GET",
            "path": "/book", "params": {"token_id": "public-yes"},
            "started_at": T0, "received_at": T1, "status": 200,
            "error_type": None, "source_timestamp": T0, "latency_ms": 75}


def book(token_id="public-yes", request_id="req-1"):
    return {
        "event_id": "event-1", "token_id": token_id, "condition_id": "condition-1",
        "result_kind": "HOME", "outcome_side": "YES", "observed_at": T1,
        "request_id": request_id, "status": "OK",
        "raw": {"asset_id": token_id, "timestamp": "1788656400000",
                "bids": [{"price": "0.58", "size": "120.500"}],
                "asks": [{"price": "0.60", "size": "8.00"},
                         {"price": "0.61", "size": "20.40"}],
                "min_order_size": "5", "tick_size": "0.01", "neg_risk": True},
        "fee_evidence": {"rate_bps": None, "raw": {"feeSchedule": {"rate": "0.02",
                         "exponent": 2}}, "received_at": T1, "source": "gamma"},
        "depth_metrics": [{"notional": 5, "complete": True, "vwap": "0.601"},
                          {"notional": 100, "complete": False, "vwap": None}],
        "source_timestamp": T0,
    }


def feature():
    return {"event_id": "event-1", "hypothesis_id": "H1", "observed_at": T1,
            "applicable": False, "reason": "incomplete_direct_books",
            "metrics": {"raw_spread": None, "maker_fill_observed": False}}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="guava-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "trades_sim.db"
        self.repo = Repository(self.path, CONTRACT)
        self.addCleanup(self.repo.close)

    def sql(self, query, args=()):
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, args)]

    def start(self, run_id="run-1"):
        self.repo.start_run(run_id, T0, CONFIG)

    def publish(self, run_id="run-1", events=None, books=None, features=None):
        self.repo.publish_cycle(run_id, T2, [event()] if events is None else events,
                                [] if books is None else books,
                                [feature()] if features is None else features,
                                {"cursor_complete": True, "pages": 1})

    def test_full_lifecycle_and_lossless_evidence(self):
        self.start()
        self.assertEqual(self.repo.status()["latest_run"]["status"], "STARTED")
        original_book = book()
        self.repo.record_request("run-1", receipt(), original_book["raw"])
        self.publish(books=[original_book])
        self.assertEqual(self.repo.status()["latest_run"]["status"], "SUCCEEDED")
        self.assertEqual(self.repo.previous_events(), {"event-1": event()})
        stored = self.sql("SELECT * FROM book_attempts WHERE token_id='public-yes'")[0]
        raw = gzip.decompress(stored["raw_gzip"])
        self.assertEqual(json.loads(raw), original_book["raw"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), stored["raw_sha256"])
        self.assertEqual(json.loads(stored["fee_evidence_json"]), original_book["fee_evidence"])
        self.assertEqual(json.loads(stored["depth_metrics_json"]), original_book["depth_metrics"])
        self.assertEqual(json.loads(stored["book_json"]), original_book)
        stored_event = self.sql("SELECT * FROM events")[0]
        self.assertEqual(json.loads(stored_event["clock_json"]), event()["clock"])
        self.assertEqual(json.loads(gzip.decompress(stored_event["raw_gzip"])), event()["raw"])
        requests = self.sql("SELECT * FROM source_requests")
        self.assertEqual(json.loads(requests[0]["receipt_json"]), receipt())
        self.assertEqual(requests[0]["payload_sha256"], stored["raw_sha256"])
        self.assertEqual(requests[0]["payload_gzip"], stored["raw_gzip"])
        self.assertEqual(json.loads(self.sql("SELECT feature_json FROM features")[0]["feature_json"]), feature())
        self.assertEqual([r["status"] for r in self.sql("SELECT status FROM run_events ORDER BY rowid")],
                         ["STARTED", "SUCCEEDED"])
        self.assertIs(evidence.SQLiteRepository, Repository)

    def test_expected_missing_book_has_explicit_attempt_not_zero_price(self):
        self.start()
        self.publish()
        attempts = self.sql("SELECT * FROM book_attempts ORDER BY token_id")
        self.assertEqual(len(attempts), 2)
        for row in attempts:
            self.assertEqual(row["status"], "NOT_ATTEMPTED")
            self.assertIsNone(row["raw_gzip"])
            self.assertIsNone(row["raw_sha256"])
            self.assertIsNone(row["request_id"])
            self.assertEqual(json.loads(row["depth_metrics_json"]), [])
            self.assertEqual(json.loads(row["book_json"])["reason"], "missing_book_attempt")

    def test_failed_http_receipt_and_book_preserve_missingness(self):
        self.start()
        failed_receipt = receipt()
        failed_receipt.update(status=None, error_type="TimeoutError")
        self.repo.record_request("run-1", failed_receipt, None)
        failed_book = book()
        failed_book.update(raw=None, status="TIMEOUT", depth_metrics=[], error_type="TimeoutError")
        self.publish(books=[failed_book])
        row = self.sql("SELECT * FROM source_requests")[0]
        self.assertIsNone(row["payload_gzip"])
        self.assertIsNone(row["payload_sha256"])
        self.assertIsNone(row["status"])
        self.assertEqual(json.loads(row["receipt_json"]), failed_receipt)
        self.assertEqual(json.loads(self.sql("SELECT book_json FROM book_attempts WHERE token_id='public-yes'")[0]["book_json"]), failed_book)

    def test_failed_run_is_durable_and_cannot_publish(self):
        self.start()
        self.repo.record_request("run-1", receipt(), [{"token_id": "public-yes"}])
        self.repo.fail_run("run-1", T2, "TimeoutError", "discovery")
        self.repo.close()
        reopened = Repository(self.path, CONTRACT)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.status()["latest_run"]["status"], "FAILED")
        self.assertEqual(self.sql("SELECT error_type FROM run_events WHERE status='FAILED'")[0]["error_type"], "TimeoutError")
        self.assertEqual(len(self.sql("SELECT * FROM source_requests")), 1)
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            reopened.publish_cycle("run-1", T2, [], [], [], {})
        self.assertEqual(reopened.previous_events(), {})

    def test_duplicate_run_and_terminal_state_are_not_overwritten(self):
        self.start()
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.start()
        self.publish()
        self.repo.fail_run("run-1", T2, "UnexpectedError", "cleanup")
        self.assertEqual(self.repo.status()["latest_run"]["status"], "SUCCEEDED")
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.publish()
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.repo.record_request("run-1", receipt(), None)
        self.assertEqual(len(self.sql("SELECT * FROM run_events")), 2)

    def test_atomic_failure_after_inserts_rolls_back_cache_and_success(self):
        self.start("old-run")
        self.publish("old-run")
        self.start()
        self.repo.record_request("run-1", receipt(), book()["raw"])
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER fixture_abort_success BEFORE INSERT ON run_events
                WHEN NEW.status='SUCCEEDED' AND NEW.run_id='run-1'
                BEGIN SELECT RAISE(ABORT, 'fixture after evidence/cache inserts'); END""")
        changed = event()
        changed["clock"]["minute"] = 80
        with self.assertRaises(sqlite3.IntegrityError):
            self.publish(events=[changed], books=[book()])
        for table in ("cycles", "events", "book_attempts", "features"):
            self.assertEqual(self.sql(f"SELECT * FROM {table} WHERE run_id='run-1'"), [])
        self.assertEqual(self.repo.previous_events()["event-1"]["clock"]["minute"], 76)
        self.assertEqual(self.sql("SELECT run_id FROM latest_event_state")[0]["run_id"], "old-run")
        self.assertEqual(len(self.sql("SELECT * FROM source_requests")), 1)
        self.repo.fail_run("run-1", T2, "IntegrityError", "publication")
        self.assertEqual(self.repo.status()["latest_run"]["status"], "FAILED")

    def test_fixed_identity_and_unknown_database_rejected_without_mutation(self):
        before = self.path.read_bytes()
        for field, value in (("strategy_name", "golden-plum"), ("job_name", "other-job"),
                             ("mode", "live"), ("data_contract", "research-full-v1"),
                             ("schema_profile", "research-full-v1"), ("config_hash", "")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    Repository(self.path, {**CONTRACT, field: value})
        self.assertEqual(before, self.path.read_bytes())
        legacy = self.path.parent / "unrelated.db"
        with sqlite3.connect(legacy) as connection:
            connection.execute("CREATE TABLE legacy (value TEXT)")
        old = legacy.read_bytes()
        with self.assertRaises(ValueError):
            Repository(legacy, CONTRACT)
        self.assertEqual(old, legacy.read_bytes())

    def test_config_and_source_cache_isolation_even_same_caller_cohort_label(self):
        self.start()
        self.publish()
        for field in ("config_hash", "strategy_source_digest"):
            other = Repository(self.path, {**CONTRACT, field: "fixture-new"})
            self.addCleanup(other.close)
            self.assertEqual(other.previous_events(), {})
            other.start_run(field, T1, CONFIG)
            changed = event()
            changed["clock"]["minute"] = 83
            other.publish_cycle(field, T2, [changed], [], [], {})
            self.assertEqual(other.previous_events()["event-1"], changed)
            self.assertEqual(self.repo.previous_events()["event-1"], event())
            with self.assertRaises(ValueError):
                self.repo.fail_run(field, T2, "Error", "cross-cohort")
        self.assertEqual(len(self.sql("SELECT * FROM latest_event_state")), 3)

    def test_same_hash_cannot_describe_different_config(self):
        self.start()
        with self.assertRaises(ValueError):
            self.repo.start_run("different-config", T1, {**CONFIG, "shard_index": 2})
        self.assertEqual(self.sql("SELECT * FROM run_audits WHERE run_id='different-config'"), [])

    def test_secret_rejected_every_write_boundary_without_echo(self):
        self.start()
        for key in ("secretkey", "clientauth", "private_key", "apiKey", "Authorization",
                    "client-secret", "access_token", "password", "POLYMARKET_PRIVATE_KEY"):
            secret = {"nested": [{key: "fixture-not-a-real-credential"}]}
            calls = [
                lambda: Repository(self.path.parent / "never-created.db", {**CONTRACT, **secret}),
                lambda: self.repo.start_run("secret-run", T1, {**CONFIG, **secret}),
                lambda: self.repo.record_request("run-1", receipt(), secret),
                lambda: self.repo.record_request("run-1", {**receipt(), **secret}, None),
                lambda: self.publish(events=[{**event(), "raw": secret}]),
                lambda: self.publish(events=[{**event(), "clock": secret}]),
                lambda: self.publish(books=[{**book(), "fee_evidence": secret}]),
                lambda: self.publish(features=[{**feature(), "metrics": secret}]),
                lambda: self.repo.publish_cycle("run-1", T2, [], [], [], secret),
            ]
            for call in calls:
                with self.subTest(key=key, call=calls.index(call)):
                    with self.assertRaises(ValueError) as caught:
                        call()
                    self.assertNotIn("fixture-not-a-real-credential", str(caught.exception))
        self.assertFalse((self.path.parent / "never-created.db").exists())
        self.assertEqual(self.sql("SELECT * FROM source_requests"), [])
        self.assertEqual(self.sql("SELECT * FROM cycles"), [])
        with self.assertRaises(ValueError):
            self.repo.fail_run("run-1", T2, "Bearer fixture-auth-value", "request")

    def test_public_identifiers_are_not_credentials(self):
        self.start()
        public = {"token_id": "12345678901234567890", "token_ids": ["1", "2"],
                  "clobTokenIds": ["3"], "asset_id": "4", "condition_id": "0x123",
                  "tokens": [{"token_id": "5", "outcome": "YES"}]}
        self.repo.record_request("run-1", receipt(), public)
        stored = self.sql("SELECT payload_gzip FROM source_requests")[0]
        self.assertEqual(json.loads(gzip.decompress(stored["payload_gzip"])), public)

    def test_empty_expected_tokens_fail_closed_but_exclusions_are_preserved(self):
        self.start()
        for tokens in ([], [""], ["public-yes", "public-yes"], [None]):
            with self.subTest(tokens=tokens):
                with self.assertRaises(ValueError):
                    self.publish(events=[event(tokens=tokens)])
        excluded = event(tokens=[])
        excluded.update(eligible=False, exclusion_reason="missing_direct_tokens")
        self.publish(events=[excluded], features=[])
        self.assertEqual(self.repo.previous_events(), {"event-1": excluded})
        self.assertEqual(self.sql("SELECT * FROM book_attempts"), [])

    def test_foreign_keys_are_real_and_enforced_on_writer(self):
        self.start()
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.publish(books=[book(request_id="unknown-request")])
        self.assertEqual(self.sql("SELECT * FROM cycles"), [])
        self.assertEqual(self.repo.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        for table in ("run_audits", "run_events", "source_requests", "cycles", "events",
                      "book_attempts", "features", "latest_event_state"):
            self.assertTrue(self.sql(f"PRAGMA foreign_key_list({table})"), table)
        self.assertEqual(self.sql("PRAGMA foreign_key_check"), [])

    def test_cross_run_request_and_unexpected_token_rejected(self):
        self.start("other-run")
        self.repo.record_request("other-run", receipt(), book()["raw"])
        self.start()
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.publish(books=[book()])
        with self.assertRaises(ValueError):
            self.publish(books=[book(token_id="not-expected")])
        self.assertEqual(self.sql("SELECT * FROM cycles"), [])

    def test_append_only_update_delete_and_replace_rejected(self):
        self.start()
        self.repo.record_request("run-1", receipt(), book()["raw"])
        self.publish(books=[book()])
        tables = ("collection_contracts", "strategy_configs", "run_audits", "run_events",
                  "source_requests", "cycles", "events", "book_attempts", "features")
        with sqlite3.connect(self.path) as connection:
            for table in tables:
                column = connection.execute(f"PRAGMA table_info({table})").fetchone()[1]
                for query in (f"UPDATE {table} SET {column}={column}", f"DELETE FROM {table}",
                              f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1"):
                    with self.subTest(query=query):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(query)
            connection.execute("DELETE FROM latest_event_state")
        self.assertEqual(self.repo.previous_events(), {})
        self.assertEqual(len(self.sql("SELECT * FROM events")), 1)

    def test_status_is_small_read_only_and_daily_rsync_canonical_metadata(self):
        self.start()
        self.publish()
        self.repo.close()
        readonly = Repository(self.path, CONTRACT, read_only=True)
        self.addCleanup(readonly.close)
        traced = []
        readonly.connection.set_trace_callback(traced.append)
        before = readonly.connection.total_changes
        status = readonly.status()
        self.assertEqual(before, readonly.connection.total_changes)
        self.assertEqual(status["latest_run"]["status"], "SUCCEEDED")
        self.assertEqual(status["metadata"]["contract_name"], "guava-research-v1")
        self.assertIsNone(status["metadata"]["database_utc_date"])
        self.assertGreater(status["file_sizes"]["database_bytes"], 0)
        for statement in traced:
            self.assertNotIn("COUNT(", statement.upper())
            self.assertNotIn("QUICK_CHECK", statement.upper())
            self.assertTrue(statement.lstrip().upper().startswith("SELECT"), statement)
        self.assertNotIn("config_snapshot", status["latest_run"])
        self.assertEqual(self.sql("SELECT strategy_name,job_name,mode FROM run_audits ORDER BY started_at DESC LIMIT 1")[0],
                         {k: CONTRACT[k] for k in ("strategy_name", "job_name", "mode")})
        self.assertEqual(len(self.sql("SELECT contract_name,database_utc_date FROM collection_contracts")), 1)
        with self.assertRaises((ValueError, sqlite3.OperationalError)):
            readonly.start_run("readonly-no-write", T2, CONFIG)
        missing = self.path.parent / "missing.db"
        with self.assertRaises((ValueError, sqlite3.OperationalError)):
            Repository(missing, CONTRACT, read_only=True)
        self.assertFalse(missing.exists())

    def test_empty_cycle_is_explicit_not_fabricated_market_data(self):
        self.start()
        self.publish(events=[], books=[], features=[])
        self.assertEqual(self.repo.previous_events(), {})
        self.assertEqual(self.sql("SELECT * FROM events"), [])
        self.assertEqual(len(self.sql("SELECT * FROM cycles")), 1)
        self.assertEqual(self.repo.status()["latest_run"]["status"], "SUCCEEDED")

    def test_status_before_first_run_and_config_snapshot_digest(self):
        self.assertIsNone(self.repo.status()["latest_run"])
        self.assertEqual(self.repo.previous_events(), {})
        self.start()
        snapshot = self.sql("SELECT * FROM strategy_configs")[0]
        self.assertEqual(json.loads(snapshot["config_json"]), CONFIG)
        self.assertEqual(snapshot["snapshot_sha256"],
                         hashlib.sha256(snapshot["config_json"].encode("utf-8")).hexdigest())

    def test_newer_cache_does_not_regress_and_returned_data_is_detached(self):
        self.start()
        self.publish()
        changed = self.repo.previous_events()
        changed["event-1"]["clock"]["minute"] = -1
        self.assertEqual(self.repo.previous_events()["event-1"], event())
        self.repo.start_run("older-observation", T1, CONFIG)
        older = event()
        older.update(observed_at=T0)
        older["clock"]["minute"] = 70
        self.repo.publish_cycle("older-observation", T2, [older], [], [], {})
        self.assertEqual(self.repo.previous_events()["event-1"], event())
        self.assertEqual(len(self.sql("SELECT * FROM events")), 2)

    def test_unclassified_exclusion_preserves_null_labels(self):
        self.start()
        excluded = event(tokens=[])
        excluded.update(eligible=False, exclusion_reason="unknown_league",
                        sport_family=None, league_code=None)
        self.publish(events=[excluded], features=[])
        self.assertEqual(self.repo.previous_events()["event-1"], excluded)
        self.assertIsNone(self.sql("SELECT league_code FROM events")[0]["league_code"])

    def test_caller_inputs_not_mutated_and_failed_late_feature_rolls_back(self):
        self.start()
        events, books, features = [event()], [], [feature(), feature()]
        before = copy.deepcopy((events, books, features))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.publish_cycle("run-1", T2, events, books, features, {})
        self.assertEqual((events, books, features), before)
        for table in ("cycles", "events", "book_attempts", "features", "latest_event_state"):
            self.assertEqual(self.sql(f"SELECT * FROM {table}"), [])
        self.assertEqual(self.repo.status()["latest_run"]["status"], "STARTED")

    def test_orphan_insert_is_rejected_by_sqlite_not_only_python_validation(self):
        self.start()
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            self.repo.connection.execute("""INSERT INTO features
                (run_id,event_id,hypothesis_id,observed_at,applicable,reason,metrics_json,feature_json)
                VALUES('run-1','absent-event','H1',?,0,'missing','{}','{}')""", (T1,))
        self.assertIn("FOREIGN KEY", str(caught.exception))
        self.assertEqual(self.sql("SELECT * FROM features"), [])

    def test_success_children_cannot_be_appended_through_direct_sql(self):
        self.start()
        self.publish()
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.connection.execute("""INSERT INTO features
                (run_id,event_id,hypothesis_id,observed_at,applicable,reason,metrics_json,feature_json)
                VALUES('run-1','event-1','H2',?,0,'missing','{}','{}')""", (T1,))

    def test_atomic_publication_is_invisible_to_independent_reader_until_commit(self):
        self.start()
        original_insert = self.repo._insert
        checked = []

        def inspect_during_insert(table, values):
            original_insert(table, values)
            if table == "features":
                for hidden in ("cycles", "events", "book_attempts", "features"):
                    self.assertEqual(self.sql(f"SELECT * FROM {hidden}"), [])
                self.assertEqual(self.sql("SELECT status FROM run_events"), [{"status": "STARTED"}])
                checked.append(True)

        self.repo._insert = inspect_during_insert
        self.publish()
        self.assertEqual(checked, [True])
        self.assertEqual(len(self.sql("SELECT * FROM features")), 1)

    def test_interruption_even_after_terminal_insert_rolls_back(self):
        self.start()
        original_insert = self.repo._insert

        def interrupt_after_success(table, values):
            original_insert(table, values)
            if table == "run_events" and values["status"] == "SUCCEEDED":
                raise KeyboardInterrupt("fixture interruption")

        self.repo._insert = interrupt_after_success
        with self.assertRaises(KeyboardInterrupt):
            self.publish()
        for table in ("cycles", "events", "book_attempts", "features", "latest_event_state"):
            self.assertEqual(self.sql(f"SELECT * FROM {table}"), [])
        self.assertEqual(self.repo.status()["latest_run"]["status"], "STARTED")

    def test_nonfinite_and_embedded_secrets_are_not_persisted(self):
        self.start()
        for payload in ({"raw": float("nan")}, {"raw": float("inf")},
                        {"wrapper": '{"clientAuth":"fixture-not-a-credential"}'},
                        {"url": "/book?secretkey=fixture-not-a-credential"}):
            with self.assertRaises(ValueError):
                self.repo.record_request("run-1", receipt(), payload)
        self.assertEqual(self.sql("SELECT * FROM source_requests"), [])

    def test_absent_run_and_duplicate_request_do_not_make_evidence(self):
        with self.assertRaises(ValueError):
            self.repo.record_request("absent", receipt(), {})
        with self.assertRaises(ValueError):
            self.repo.fail_run("absent", T2, "Error", "phase")
        self.start()
        self.repo.record_request("run-1", receipt(), [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.record_request("run-1", receipt(), None)
        row = self.sql("SELECT * FROM source_requests")[0]
        self.assertEqual(json.loads(gzip.decompress(row["payload_gzip"])), [])
        self.assertEqual(len(self.sql("SELECT * FROM source_requests")), 1)

    def test_status_and_cache_use_indexes_not_history_sort_scans(self):
        self.start()
        self.publish()
        plan = self.sql("EXPLAIN QUERY PLAN SELECT run_id FROM run_audits "
                        "ORDER BY started_at DESC,run_id DESC LIMIT 1")
        self.assertTrue(any("run_audits_latest" in row["detail"] for row in plan))
        self.assertFalse(any("TEMP B-TREE" in row["detail"] for row in plan))
        plan = self.sql("EXPLAIN QUERY PLAN SELECT event_id FROM latest_event_state "
                        "WHERE cohort_key=? ORDER BY event_id", (self.repo.cohort_key,))
        self.assertTrue(any("SEARCH" in row["detail"] and "INDEX" in row["detail"] for row in plan))

    def test_runtime_snapshot_with_source_provenance_is_preserved_per_source(self):
        first_snapshot = {**CONTRACT, "simulation_mode": True, "trading": CONFIG}
        self.repo.start_run("first-source", T0, first_snapshot)
        self.publish("first-source")
        next_contract = {**CONTRACT, "strategy_source_digest": "fixture-source-b"}
        next_snapshot = {**next_contract, "simulation_mode": True, "trading": CONFIG}
        other = Repository(self.path, next_contract)
        self.addCleanup(other.close)
        other.start_run("next-source", T1, next_snapshot)
        self.assertEqual(other.previous_events(), {})
        other.publish_cycle("next-source", T2, [event()], [], [], {})
        snapshots = self.sql("SELECT config_json FROM strategy_configs ORDER BY strategy_source_digest")
        self.assertEqual([json.loads(row["config_json"]) for row in snapshots],
                         [first_snapshot, next_snapshot])
        self.assertEqual(self.repo.previous_events()["event-1"], event())

    def test_conflicting_snapshot_identity_is_rejected_before_start(self):
        for key, value in (("job_name", "other-job"), ("mode", "live"),
                           ("strategy_source_digest", "other-source"),
                           ("data_contract", "research-full-v1"), ("simulation_mode", False)):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.repo.start_run("bad-snapshot", T0, {**CONTRACT, key: value})
        self.assertEqual(self.sql("SELECT * FROM strategy_configs"), [])
        self.assertEqual(self.sql("SELECT * FROM run_audits"), [])

    def budget_repository(self, budget):
        self.repo.close()
        self.repo = Repository(self.path, CONTRACT, budget=budget)
        self.addCleanup(self.repo.close)

    def assert_unpublished(self, run_id="run-1"):
        for table in ("cycles", "events", "book_attempts", "features", "latest_event_state"):
            self.assertEqual(self.sql(f"SELECT * FROM {table} WHERE run_id=?", (run_id,)), [])
        self.assertEqual(self.sql("SELECT status FROM run_events WHERE run_id=?", (run_id,)),
                         [{"status": "STARTED"}])

    def test_budget_expired_before_publication_preserves_durable_failure_path(self):
        budget = FixtureBudget()
        self.budget_repository(budget)
        self.start()
        self.repo.record_request("run-1", receipt(), book()["raw"])
        budget.expired = True
        with self.assertRaises(FixtureBudgetExceeded):
            self.publish(books=[book()])
        self.assert_unpublished()
        self.assertEqual(len(self.sql("SELECT * FROM source_requests")), 1)
        self.repo.fail_run("run-1", T2, "BudgetExceeded", "publication")
        self.assertEqual(self.repo.status()["latest_run"]["status"], "FAILED")
        self.assertFalse(self.repo.connection.in_transaction)

    def test_budget_checked_again_immediately_before_commit(self):
        budget = FixtureBudget()
        self.budget_repository(budget)
        self.start()
        original_insert = self.repo._insert

        def expire_after_terminal_insert(table, values):
            original_insert(table, values)
            if table == "run_events" and values["status"] == "SUCCEEDED":
                budget.expired = True

        self.repo._insert = expire_after_terminal_insert
        with self.assertRaises(FixtureBudgetExceeded):
            self.publish()
        self.assert_unpublished()
        self.assertFalse(self.repo.connection.in_transaction)
        self.assertEqual(self.repo.connection.execute(LONG_SQL).fetchone()[0], 50005000)

    def test_sql_progress_budget_timeout_clears_handler_before_rollback(self):
        budget = FixtureBudget()
        self.budget_repository(budget)
        self.start("old-run")
        self.publish("old-run")
        self.start()
        self.repo.record_request("run-1", receipt(), book()["raw"])
        original_insert = self.repo._insert
        rollback_calls = []

        def expire_inside_sql(table, values):
            original_insert(table, values)
            if table == "features":
                budget.expired = True
                self.repo.connection.execute(LONG_SQL).fetchone()

        def inspect_rollback(statement):
            if statement.upper() == "ROLLBACK":
                rollback_calls.append(budget.calls)

        self.repo._insert = expire_inside_sql
        self.repo.connection.set_trace_callback(inspect_rollback)
        with self.assertRaises(FixtureBudgetExceeded) as caught:
            self.publish(books=[book()])
        self.assertIsInstance(caught.exception.__cause__, sqlite3.OperationalError)
        self.assert_unpublished()
        self.assertEqual(self.repo.previous_events()["event-1"], event())
        self.assertEqual(len(self.sql("SELECT * FROM source_requests")), 1)
        self.assertFalse(self.repo.connection.in_transaction)
        calls_after_timeout = budget.calls
        self.assertEqual(self.repo.connection.execute(LONG_SQL).fetchone()[0], 50005000)
        self.assertEqual(budget.calls, calls_after_timeout)
        if rollback_calls:  # Some SQLite DML interruptions already roll back.
            self.assertEqual(rollback_calls, [calls_after_timeout])
        self.repo.fail_run("run-1", T2, "BudgetExceeded", "publication")

    def test_successful_budgeted_sql_and_cleanup_leave_no_handler(self):
        budget = FixtureBudget()
        self.budget_repository(budget)
        self.start()
        self.assertEqual(budget.calls, 0)
        original_insert = self.repo._insert

        def substantial_sql(table, values):
            original_insert(table, values)
            if table == "features":
                self.assertEqual(self.repo.connection.execute(LONG_SQL).fetchone()[0], 50005000)

        self.repo._insert = substantial_sql
        self.publish()
        self.assertGreater(budget.calls, 3)
        self.assertEqual(self.repo.status()["latest_run"]["status"], "SUCCEEDED")
        budget.expired = True
        calls = budget.calls
        self.assertEqual(self.repo.connection.execute(LONG_SQL).fetchone()[0], 50005000)
        self.assertEqual(calls, budget.calls)

    def test_non_budget_failure_keeps_original_exception_and_clears_progress(self):
        budget = FixtureBudget()
        self.budget_repository(budget)
        self.start()
        with self.assertRaises(sqlite3.IntegrityError):
            self.publish(features=[feature(), feature()])
        self.assert_unpublished()
        budget.expired = True
        calls = budget.calls
        self.assertEqual(self.repo.connection.execute(LONG_SQL).fetchone()[0], 50005000)
        self.assertEqual(calls, budget.calls)

    def test_previous_events_empty_ids_does_not_query_database(self):
        self.start()
        self.publish()
        statements = []
        self.repo.connection.set_trace_callback(statements.append)
        self.assertEqual(self.repo.previous_events([]), {})
        self.assertEqual(statements, [])
        self.assertEqual(self.repo.previous_events(None), {"event-1": event()})

    def test_previous_events_selected_ids_and_unknown_ids(self):
        self.start()
        events = [event("event-1"), event("event-2"), event("event-3")]
        self.publish(events=events, features=[])
        self.assertEqual(self.repo.previous_events(["event-3", "missing", "event-3"]),
                         {"event-3": events[2]})
        self.assertEqual(self.repo.previous_events(["missing"]), {})
        self.assertEqual(self.repo.previous_events(), {row["event_id"]: row for row in events})

    def test_previous_events_filtered_ids_remain_source_isolated(self):
        self.start()
        self.publish()
        other = Repository(self.path, {**CONTRACT, "strategy_source_digest": "another-source"})
        self.addCleanup(other.close)
        self.assertEqual(other.previous_events(["event-1"]), {})

    def test_previous_events_batches_sql_parameters_and_uses_index(self):
        self.start()
        events = [event(f"event-{i}") for i in range(31)]
        self.publish(events=events, features=[])
        original_limit = self.repo.connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 12)
        self.addCleanup(self.repo.connection.setlimit, sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, original_limit)
        statements = []
        self.repo.connection.set_trace_callback(statements.append)
        selected = [row["event_id"] for row in events[::2]]
        self.assertEqual(set(self.repo.previous_events(selected)), set(selected))
        self.assertGreater(len(statements), 1)
        for query in statements:
            self.assertIn("c.event_id IN (", query)
            plan = self.sql("EXPLAIN QUERY PLAN " + query)
            self.assertTrue(any("SEARCH c USING INDEX" in row["detail"] for row in plan))

    def test_previous_events_rejects_bare_string_and_invalid_ids(self):
        for event_ids in ("event-1", [None], [""], [1]):
            with self.subTest(event_ids=event_ids):
                with self.assertRaises(ValueError):
                    self.repo.previous_events(event_ids)

    def test_shared_metadata_and_run_publish_actual_contract_field_names(self):
        self.start()
        for table in ("collection_contracts", "run_audits"):
            row = self.sql(f"SELECT mode,data_contract FROM {table}")[0]
            self.assertEqual(row, {"mode": "sim", "data_contract": "guava-research-v1"})
        status = self.repo.status()
        for key in ("metadata", "latest_run"):
            self.assertEqual(status[key]["mode"], "sim")
            self.assertEqual(status[key]["data_contract"], "guava-research-v1")
        self.assertEqual(status["metadata"]["contract_name"], "guava-research-v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
