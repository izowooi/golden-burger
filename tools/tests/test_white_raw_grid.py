from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conservative_sports_grid as grid
import watermelon_raw_sidecar as reader
import white_raw_grid as paired
from test_watermelon_raw_sidecar import RawSidecarReaderTests, moment


class PairedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RawSidecarReaderTests()
        self.fixture.setUp()
        f = self.fixture
        f.parent.execute("ALTER TABLE research_config_versions ADD COLUMN config_json TEXT")
        f.parent.execute("UPDATE research_config_versions SET config_json=?", (json.dumps({"trading": {"strategy_source_digest": "source"}}),))
        f.event.update(active=True, closed=False)
        f.event["markets"][0].update(enableOrderBook=True, feesEnabled=False, liquidityNum=10000, volumeNum=10000)
        self.source = {"id": "polybot-white:fixture", "parent_source_key": "parent", "sidecar_source_key": "sidecar"}

    def tearDown(self):
        self.fixture.tearDown()

    def publish(self):
        f = self.fixture
        f.parent.execute("DELETE FROM raw_payloads WHERE request_id='gamma'")
        f.parent.execute("DELETE FROM api_requests WHERE request_id='gamma'")
        digest = f.payload("gamma", "GAMMA_EVENT_PAGE", [f.event], 1, 2)
        ref = json.loads(f.event_row["source_ref_json"])
        ref["sha256"] = digest
        f.event_row.update(event_json=reader.canonical(f.event), source_ref_json=reader.canonical(ref))
        f.rows()

    def decode(self, end="2026-09-08T00:00:00+00:00"):
        return paired.decode_connections(self.source, self.fixture.parent, self.fixture.raw,
                                          "2026-09-07T00:00:00+00:00", end)

    def test_parent_book_is_consumed_once_and_fee_is_observed(self):
        self.publish()
        events, audit = self.decode()
        self.assertEqual(len(events), 1)
        self.assertEqual(audit["stats"]["raw_rows"], 2)
        self.assertEqual(len(events[0].groups[0].snaps), 2)
        self.assertTrue(events[0].groups[0].complete)
        self.assertTrue(all(s.open_observed and s.fee_rate == 0 for s in events[0].groups[0].snaps))
        self.assertEqual(events[0].config["paired_source_keys"], ["parent", "sidecar", paired.ORIGIN])

    def test_failed_raw_cycle_never_falls_back_to_parent_book(self):
        self.fixture.cycle["status"] = "FAILED"
        self.publish()
        events, audit = self.decode()
        self.assertEqual(audit["stats"]["raw_rows"], 2)
        self.assertFalse(events[0].groups[0].complete)
        self.assertTrue(events[0].failures)
        self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))

    def test_missing_raw_cycle_is_not_a_parent_projection(self):
        events, audit = self.decode()
        self.assertEqual(events, [])
        self.assertEqual(audit["cycles"], 0)

    def test_terminal_annotation_alone_has_no_value(self):
        self.fixture.event_row.update(terminal_json='{"payout":1}', lifecycle_state="RESOLVED")
        self.publish()
        events, _ = self.decode()
        self.assertFalse(events[0].terminals)

    def test_terminal_metadata_independent_of_missing_book(self):
        f = self.fixture
        f.event["markets"][0].update(closed=True, outcomePrices='[1,0]')
        for b in f.book_rows:
            b.update(status="NOT_ATTEMPTED", source_kind="NONE", source_ref_json="{}", request_id=None,
                     received_at=None, requested_at=None, point_in_time_identity_valid=0, book_sha256=None)
        self.publish()
        events, audit = self.decode()
        self.assertEqual(audit["stats"]["verified_terminal_token_facts"], 2)
        self.assertEqual(events[0].terminals[("condition", "a")][0]["payout"], 1)
        self.assertEqual(events[0].terminals[("condition", "a")][0]["observed_at"], moment(8))
        self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))

    def test_entry_to_verified_terminal_completes_without_a_final_book(self):
        f = self.fixture
        f.event["markets"][0].update(closed=True, outcomePrices='[1,0]')
        for b in f.book_rows:
            b.update(status="NOT_ATTEMPTED", source_kind="NONE", source_ref_json="{}", request_id=None,
                     received_at=None, requested_at=None, point_in_time_identity_valid=0, book_sha256=None)
        self.publish()
        events, _ = self.decode()
        e = events[0]
        entry_time = reader.stamp(moment(0)) - 60
        e.groups.insert(0, synthetic_group(entry_time))
        candidate, _ = grid.candidates(e, "plum_price_rank", 1, .70)
        path = grid.make_path(e, "plum_price_rank", candidate)
        result = grid.replay_path(path, "absolute", .90, "zero")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["reason"], "RESOLUTION")
        # An earlier independent unknown cycle must still censor this payoff.
        e.groups.insert(1, synthetic_group(entry_time + 30, empty=True, valid=False))
        candidate, _ = grid.candidates(e, "plum_price_rank", 1, .70)
        result = grid.replay_path(grid.make_path(e, "plum_price_rank", candidate), "absolute", .90, "zero")
        self.assertEqual(result["status"], "CENSORED")

    def test_failed_parent_cannot_publish_raw_terminal(self):
        f = self.fixture
        f.event["markets"][0].update(closed=True, outcomePrices='[1,0]')
        f.parent.execute("UPDATE research_run_events SET event_type='FAILED' WHERE event_id='end'")
        self.publish()
        events, _ = self.decode()
        self.assertFalse(events[0].terminals)

    def test_cutoff_before_parent_success_blocks_everything(self):
        self.fixture.event["markets"][0].update(closed=True, outcomePrices='[1,0]')
        self.publish()
        events, _ = self.decode(end=moment(9))
        self.assertFalse(events[0].terminals)
        self.assertFalse(any(s.valid for s in events[0].groups[0].snaps))

    def test_later_metadata_does_not_repair_earlier_parent_request(self):
        self.fixture.book_rows[0]["requested_at"] = moment(1)
        self.publish()
        events, _ = self.decode()
        self.assertFalse(events[0].groups[0].complete)
        self.assertFalse(next(s for s in events[0].groups[0].snaps if s.token == "a").valid)

    def test_invalid_future_receipt_keeps_unknown_barrier_at_cycle_time(self):
        f = self.fixture
        f.book_rows[0]["received_at"] = "2026-09-08T12:00:00Z"
        self.publish()
        events, _ = self.decode()
        bad = next(s for s in events[0].groups[0].snaps if s.token == "a")
        self.assertFalse(bad.valid)
        self.assertEqual(bad.time, reader.stamp(moment(0)))
        self.assertLess(events[0].groups[0].time, reader.stamp(moment(8)))

    def test_invalid_past_receipt_cannot_move_before_its_actual_run(self):
        self.fixture.book_rows[0]["received_at"] = "2026-09-06T12:00:00Z"
        self.publish()
        events, _ = self.decode()
        bad = next(s for s in events[0].groups[0].snaps if s.token == "a")
        self.assertEqual(bad.time, reader.stamp(moment(0)))

    def test_terminal_bool_duplicate_token_and_unresolved_void_rejected(self):
        event = deepcopy(self.fixture.event)
        event["markets"][0].update(closed=True, outcomePrices=[True, False])
        self.assertIsNone(paired.terminal_payouts(event, self.fixture.slots, "mlb"))
        event["markets"][0]["outcomePrices"] = [.5, .5]
        self.assertIsNone(paired.terminal_payouts(event, self.fixture.slots, "mlb"))
        event["markets"][0]["umaResolutionStatus"] = "resolved"
        self.assertEqual(set(paired.terminal_payouts(event, self.fixture.slots, "mlb").values()), {.5})
        slots = deepcopy(self.fixture.slots)
        slots[1]["token_id"] = "a"
        self.assertIsNone(paired.terminal_payouts(event, slots, "mlb"))


def synthetic_group(t, *, bid=.69, empty=False, valid=True):
    snaps = []
    for token, slot, p in (("a", "HOME", .70), ("b", "AWAY", .30)):
        asks = ((p, 100.),)
        bids = () if empty and token == "a" else ((bid if token == "a" else .29, 100.),)
        snaps.append(grid.Snap(token, "condition", (slot, "DIRECT"), t, str(t), None, p-.005,
            asks, bids, valid, valid, valid, 0., p-bids[0][0] if bids else None, "PRESENT_VALID", "RAW",
            grid.walk(asks, 5, True), evidence_origin=paired.ORIGIN,
            observation_status="EMPTY_BIDS" if not bids else "FULL"))
    return grid.normalize_group(snaps, "mlb", True)


class EmptyAndGapPathTests(unittest.TestCase):
    def test_known_empty_bid_can_continue_to_later_tp(self):
        groups = [synthetic_group(100), synthetic_group(160, empty=True), synthetic_group(220, bid=.75)]
        # Keep the final full book uncrossed.
        a = next(s for s in groups[-1].snaps if s.token == "a")
        a.asks, a.spread = ((.76, 100.),), .01
        event = grid.Event("polybot-white:fixture", "c", "mlb", "e", "test", {}, groups, [], {})
        candidate, _ = grid.candidates(event, "plum_price_rank", 1, .70)
        path = grid.make_path(event, "plum_price_rank", candidate)
        result = grid.replay_path(path, "absolute", .72, "zero")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["reason"], "TP")
        self.assertEqual(len(path["observations"]), 2)

    def test_unknown_missing_bid_is_still_censored(self):
        groups = [synthetic_group(100), synthetic_group(160, empty=True, valid=False), synthetic_group(220)]
        event = grid.Event("polybot-white:fixture", "c", "mlb", "e", "test", {}, groups, [], {})
        candidate, _ = grid.candidates(event, "plum_price_rank", 1, .70)
        result = grid.replay_path(grid.make_path(event, "plum_price_rank", candidate), "absolute", .72, "zero")
        self.assertEqual(result["status"], "CENSORED")


if __name__ == "__main__":
    unittest.main()
