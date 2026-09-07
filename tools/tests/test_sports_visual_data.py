"""Safety checks for the offline sports visualization adapter."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

SPEC = importlib.util.spec_from_file_location(
    "sports_visual_data", Path(__file__).resolve().parents[1] / "sports_visual_data.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DepthTests(unittest.TestCase):
    def test_full_depth_and_same_share_liquidation(self):
        book = {"token_id": "a", "asks": [{"price": .5, "size": 5}, {"price": .625, "size": 4}], "bids": [{"price": .49, "size": 5}, {"price": .4, "size": 4}]}
        ask, bid = MODULE.depth_prices(book, "a")
        self.assertAlmostEqual(ask, 5 / 9)
        self.assertAlmostEqual(bid, 4.05 / 9)

    def test_partial_bid_is_missing_and_token_mismatch_rejected(self):
        book = {"token_id": "a", "asks": [{"price": .5, "size": 10}], "bids": [{"price": .49, "size": 9}]}
        self.assertEqual(MODULE.depth_prices(book, "a"), (.5, None))
        self.assertEqual(MODULE.depth_prices(book, "b"), (None, None))

    def test_nonfinite_and_empty_depth_never_create_prices(self):
        self.assertEqual(MODULE.depth_prices({"asks": [{"price": float("nan"), "size": 5}]}, "a"), (None, None))
        self.assertEqual(MODULE.depth_prices({}, "a"), (None, None))


class TerminalTests(unittest.TestCase):
    def connection(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE resolution_observations(run_id TEXT,condition_id TEXT,observed_at TEXT,evidence_json TEXT)")
        return c

    def test_only_successful_exact_closed_onehot_evidence(self):
        c = self.connection()
        payload = {"closed": True, "tokens": [{"token_id": "a", "price": 1}, {"token_id": "b", "price": 0}]}
        c.execute("INSERT INTO resolution_observations VALUES(?,?,?,?)", ("success", "c", "2026-09-07T00:00:00Z", json.dumps(payload)))
        c.execute("INSERT INTO resolution_observations VALUES(?,?,?,?)", ("failed", "d", "2026-09-07T00:00:00Z", json.dumps(payload)))
        payload["closed"] = False
        c.execute("INSERT INTO resolution_observations VALUES(?,?,?,?)", ("success", "e", "2026-09-07T00:00:00Z", json.dumps(payload)))
        runs = {"success": {"status": "SUCCESS", "config_hash": "h"}, "failed": {"status": "FAILED", "config_hash": "h"}}
        result = MODULE.terminal_records(c, True, runs)
        self.assertEqual(set(result), {("c", "a"), ("c", "b")})
        self.assertEqual(result[("c", "a")][0]["payout"], 1)

    def test_duplicate_token_and_mid_prices_rejected(self):
        c = self.connection()
        for i, tokens in enumerate([
            [{"token_id": "a", "price": 1}, {"token_id": "a", "price": 0}],
            [{"token_id": "a", "price": .99}, {"token_id": "b", "price": .01}],
        ]):
            c.execute("INSERT INTO resolution_observations VALUES(?,?,?,?)", ("success", str(i), "2026-09-07T00:00:00Z", json.dumps({"closed": True, "tokens": tokens})))
        self.assertFalse(MODULE.terminal_records(c, True, {"success": {"status": "SUCCESS", "config_hash": "h"}}))

    def test_terminal_cutoff_is_half_open_without_future_result_leakage(self):
        c = self.connection()
        payload = json.dumps({"closed": True, "tokens": [{"token_id": "a", "price": 1}, {"token_id": "b", "price": 0}]})
        for condition, observed in [("before", "2026-09-07T10:05:48Z"), ("boundary", "2026-09-07T10:05:49Z"), ("after", "2026-09-07T10:06:00Z")]:
            c.execute("INSERT INTO resolution_observations VALUES(?,?,?,?)", ("success", condition, observed, payload))
        result = MODULE.terminal_records(c, True, {"success": {"status": "SUCCESS", "config_hash": "h"}}, end="2026-09-07T10:05:49Z")
        self.assertEqual(set(result), {("before", "a"), ("before", "b")})


if __name__ == "__main__":
    unittest.main()
