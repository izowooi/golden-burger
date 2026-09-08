"""Fixed-policy later-window tests; no production prices or network access."""
from copy import deepcopy
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conservative_sports_grid as grid
import conservative_sports_prospective as replay


def group(t, ask=.60, bid=None, fee=.05):
    snaps = []
    for token, slot, p, b in (("home", ("HOME", "DIRECT"), ask, bid if bid is not None else ask-.01),
                               ("away", ("AWAY", "DIRECT"), .40, .39)):
        asks, bids = ((p, 100.),), ((b, 100.),)
        snaps.append(grid.Snap(token, "condition", slot, t, str(t), None, (p+b)/2,
                               asks, bids, True, True, True, fee, p-b, "PRESENT_VALID", "RAW",
                               grid.walk(asks, 5, True), entry_set_complete=True,
                               evidence_origin="full-sports-raw-v1", observation_status="FULL"))
    return grid.normalize_group(snaps, "mlb", False)


def event(groups, **kwargs):
    return grid.Event("polybot-gold:fixture", "cohort", "mlb", "new-game", "Fixture",
                      {"config_hash": "cfg", "strategy_source_digest": "source", "mode": "sim",
                       "raw_point_in_time_archive": True, "evidence_origins": ["full-sports-raw-v1"]},
                      groups, kwargs.get("failures", []), kwargs.get("terminals", {}))


def spec(entry=.60, target=.05, mode="relative", tp="positive_full"):
    return {"spec_id": "fixed", "case_id": "fixed", "sport": "mlb", "policy": "plum_price_rank", "rank": 1,
            "entry": entry, "entry_width": .03, "target_mode": mode, "target": target, "tp_policy": tp,
            "selection_mode": "rank_midpoint", "require_clear_rank": False,
            "baseline_stop": grid.policy_knobs("plum_price_rank", "mlb"), "tier": "FROZEN_FOCUS_PRIMARY_BASELINE_STOP"}


def epoch():
    return {"source_id": "polybot-gold:fixture", "strategy": "golden-plum", "config_hash": "cfg", "source_digest": "source",
            "admitted_financial_evaluation": True, "compatible_policy_axes": {"mlb": ["plum_price_rank"]},
            "focus_spec_ids": ["fixed"], "control_spec_ids": []}


class ProspectiveTests(unittest.TestCase):
    def test_epoch_mismatch_and_training_overlap_are_separate(self):
        e = event([group(100)])
        self.assertIsNone(replay.event_admission(e, epoch(), set()))
        self.assertEqual(replay.event_admission(e, epoch(), {"new-game"}), "PRIOR_TRAINING_GAME_OVERLAP")
        e.config["config_hash"] = "other"
        self.assertEqual(replay.event_admission(e, epoch(), set()), "SOURCE_CONFIG_EPOCH_MISMATCH")

    def test_mixed_raw_legacy_does_not_silently_switch_fee(self):
        e = event([group(100)])
        e.config["evidence_origins"] = ["legacy", "full-sports-raw-v1"]
        self.assertEqual(replay.event_admission(e, epoch(), set()), "MIXED_RAW_LEGACY_ORIGINS_REQUIRE_SEPARATE_VIEW")

    def test_first_candidate_before_window_does_not_reenter_later(self):
        p, reason, _ = replay.candidate_path(event([group(100), group(160, .61)]), spec(), 120, 200)
        self.assertIsNone(p)
        self.assertEqual(reason, "PRE_WINDOW_FIRST_CANDIDATE")

    def test_pre_window_feature_history_can_confirm_later_cross(self):
        s = spec(entry=.61)
        s["policy"] = "peach_scheduled_age_cross_rank"
        e = event([group(100, .60), group(160, .62)])
        for g in e.groups:
            for snap in g.snaps:
                snap.scheduled_age = 2
        p, reason, _ = replay.candidate_path(e, s, 120, 200)
        self.assertIsNone(reason)
        self.assertEqual(p["entry_time"], 160)

    def test_future_quote_and_foreign_terminal_cannot_finish_path(self):
        proofs = {("condition", "home"): [
            {"observed_at": grid.iso(140), "payout": 1, "observer_config": "foreign"},
            {"observed_at": grid.iso(170), "payout": 1, "observer_config": "cfg"}]}
        p, _, _ = replay.candidate_path(event([group(100), group(180, .80, .79)], terminals=proofs), spec(), 100, 160)
        r = grid.replay_path(p, "relative", .05, "zero")
        self.assertEqual(r["status"], "CENSORED")
        self.assertIsNone(r["net"])
        self.assertEqual(p["observations"], [])

    def test_actual_entry_overshoot_rejects_every_stop_without_reentry(self):
        s = spec(entry=.70, target=.72, mode="absolute")
        p, _, _ = replay.candidate_path(event([group(100, .72), group(160, .71), group(220, .99, .98)]), s, 100, 300)
        rows = replay.evaluate_path(p, s, [.01, .12, .30], ["zero", "recorded_schedule"])
        self.assertEqual({x[2]["status"] for x in rows}, {"REJECTED"})
        self.assertEqual({x[2]["reason"] for x in rows}, {"target_not_above_actual_entry"})

    def test_missing_recorded_fee_is_not_replaced_by_assumed_fee(self):
        s = spec()
        p, _, _ = replay.candidate_path(event([group(100, fee=None), group(160, .70, .69)]), s, 100, 200)
        rows = replay.evaluate_path(p, s, [.12], ["recorded_schedule", "sports_005"])
        by_model = {x[0]: x[2] for x in rows}
        self.assertEqual(by_model["recorded_schedule"]["reason"], "entry_fee_evidence_missing")
        self.assertIsNone(by_model["recorded_schedule"]["lower"])
        self.assertEqual(by_model["sports_005"]["status"], "COMPLETE")

    def test_tighter_stop_changes_only_following_exit_and_is_paired(self):
        s = spec()
        p, _, _ = replay.candidate_path(event([group(100), group(160, .56, .55), group(220, .70, .69)]), s, 100, 300)
        rows = replay.evaluate_path(p, s, [.01, .12], ["zero"])
        by_delta = {x[1]: x for x in rows}
        self.assertEqual(by_delta[.01][2]["reason"], "SL")
        self.assertEqual(by_delta[.12][2]["reason"], "TP")
        self.assertTrue(by_delta[.01][4]["paired_complete"])
        self.assertLess(by_delta[.01][4]["paired_net_delta"], 0)

    def test_same_bar_and_failed_run_never_produce_later_profit(self):
        s = spec()
        for e in [event([group(100)]), event([group(100), group(160, .70, .69)], failures=[(120,130)])]:
            p, _, _ = replay.candidate_path(e, s, 100, 200)
            r = replay.evaluate_path(p, s, [.12], ["zero"])[0][2]
            self.assertEqual(r["status"], "CENSORED")
            self.assertIsNone(r["net"])

    def test_named_published_tp_keeps_fee_negative_exit(self):
        e = event([group(100), group(160, .63, .62)])
        a, b = spec(target=.02), spec(target=.02, tp="peach_published")
        p, _, _ = replay.candidate_path(e, a, 100, 200)
        guarded = replay.evaluate_path(p, a, [.12], ["sports_005"])[0][2]
        published = replay.evaluate_path(p, b, [.12], ["sports_005"])[0][2]
        self.assertEqual(guarded["status"], "CENSORED")
        self.assertEqual(published["status"], "COMPLETE")
        self.assertLess(published["net"], 0)

    def test_named_controls_do_not_expand_stop_sensitivity(self):
        s = spec()
        s["tier"] = "PRIOR_NAMED_PARAMETER_PROXY_CONTROL_NOT_LIVE_EQUIVALENCE"
        self.assertEqual(replay.stop_values(s, [.01, .30]), [.12])

    def test_manifest_sha_tampering_rejected_before_data_access(self):
        with TemporaryDirectory() as td:
            p = Path(td)/"manifest.json"
            p.write_text("{}")
            with self.assertRaisesRegex(ValueError, "SHA"):
                replay.read_manifest(p, "0"*64)

    def test_pin_source_key_mismatch_is_rejected(self):
        with TemporaryDirectory() as td:
            p = Path(td)/"trades_sim.db"
            m = Path(td)/"manifest.json"
            m.write_text(json.dumps({"source_key": "other", "pinned_path": str(p), "sha256": "sha", "quick_check": ["ok"]}))
            with self.assertRaisesRegex(ValueError, "identity"):
                replay.normalize_source({"job":"polybot-gold","runtime_job":"fixture","pinned":True,"mode":"sim","source_key":"source", "local_path":str(p),"local_sha256":"sha","manifest":str(m)})

    def test_driver_uses_only_fixed_specs_and_primary_fee_rule(self):
        s, ep = spec(), epoch()
        manifest = {"expected_source_epochs":[ep],"focus_specifications":[s],"prior_named_controls":[],
                    "evaluation_window":{"start_inclusive":grid.iso(100),"end_exclusive":grid.iso(300)},
                    "population":{"previous_analysis_event_ids":[]},"stop_sensitivity":{"values":[.01,.12]},
                    "fees":{"secondary_models":["sports_005","zero","flat_100bps"]},
                    "statistics":{"family_trials_before_execution":{"primary_plus_controls":500,"all_focus_stop_cells_plus_controls":3830}}}
        source = {"id":ep["source_id"],"strategy":"golden-plum"}
        e = event([group(100),group(160,.70,.69)])
        with TemporaryDirectory() as td, patch.object(replay,"normalize_source",side_effect=lambda x:x), patch.object(grid,"read_source",return_value=([e],{})), patch.object(grid,"run_grid",side_effect=AssertionError("full grid forbidden")):
            result = replay.run(manifest,[source],Path(td)/"out",manifest_sha256="fixed")
            self.assertEqual(result["event_outcome_rows"],8)
            self.assertEqual(result["primary_baseline_summary_rows"],1)
            self.assertEqual(result["primary_baseline_summaries"][0]["fee_model"],"recorded_schedule")
            self.assertEqual(result["primary_baseline_summaries"][0]["nominal_family_trials"],500)
            self.assertTrue(result["no_winner_selection_performed"])


if __name__ == "__main__":
    unittest.main()
