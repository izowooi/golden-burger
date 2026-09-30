"""Guava's reader revision cannot waive frozen provenance or code hashes."""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import conservative_sports_prospective as replay


class GuavaRevisionTests(unittest.TestCase):
    def setUp(self):
        self.record_path = ROOT / replay.GUAVA_REVISION_RECORD
        self.record = json.loads(self.record_path.read_text())
        self.path = ROOT / replay.GUAVA_PUBLIC_ADAPTER
        self.frozen = self.record["frozen_sha256"]
        self.review = {
            "frozen_sha256": self.frozen,
            "approved_sha256": replay.grid.visual.sha256(self.path),
            "path": str(self.path),
            "review_evidence": "reviewed public reader revision with exact Event parity",
            "revision_sha256": replay.grid.visual.sha256(self.record_path),
        }

    def test_original_frozen_adapter_needs_no_supplement(self):
        original = replay.load_guava_adapter(ROOT / replay.GUAVA_FROZEN_FIXTURE, self.frozen)
        self.assertTrue(callable(original.read_source))
        self.assertFalse(hasattr(original, "__guava_revision__"))

    def test_approved_revision_keeps_frozen_and_actual_dependency_provenance(self):
        module = replay.load_guava_adapter(self.path, self.frozen, self.review)
        self.assertEqual(module.__guava_revision__["frozen_sha256"], self.frozen)
        self.assertEqual(module.__guava_revision__["dependency_sha256"], self.record["dependency_sha256"])
        self.assertEqual(module.__guava_revision__["revision_sha256"], self.review["revision_sha256"])

    def test_evidence_string_or_unbound_revision_cannot_waive_hash(self):
        for review in (None, {k: v for k, v in self.review.items() if k != "revision_sha256"},
                       {**self.review, "revision_sha256": "0" * 64}):
            with self.subTest(review=review), self.assertRaisesRegex(ValueError, "candidate freeze"):
                replay.load_guava_adapter(self.path, self.frozen, review)

    def test_arbitrary_adapter_rejected_even_with_valid_revision_record_hash(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "unreviewed.py"
            path.write_text("raise AssertionError('unreviewed adapter must not execute')\n")
            review = {**self.review, "path": str(path), "approved_sha256": replay.grid.visual.sha256(path),
                      "review_evidence": "x"}
            with self.assertRaisesRegex(ValueError, "adapter or frozen baseline"):
                replay.load_guava_adapter(path, self.frozen, review)

    def test_original_baseline_and_each_dependency_must_match_actual_files(self):
        hash_file = replay.grid.visual.sha256
        for relative in (replay.GUAVA_FROZEN_FIXTURE, "tools/public_market_reader.py",
                         replay.GUAVA_PACKAGE_ROOT + "/market_data_raw_guava_reader.py"):
            def changed_hash(path):
                return "0" * 64 if Path(path) == ROOT / relative else hash_file(path)
            with self.subTest(relative=relative), patch.object(replay.grid.visual, "sha256", changed_hash):
                with self.assertRaisesRegex(ValueError, "baseline differs|dependency hash differs"):
                    replay.load_guava_adapter(self.path, self.frozen, self.review)

    def test_dependency_inventory_cannot_drop_a_file(self):
        with patch.object(replay, "_guava_dependency_paths", return_value=set(self.record["dependency_sha256"]) | {"new.py"}):
            with self.assertRaisesRegex(ValueError, "dependency inventory differs"):
                replay.load_guava_adapter(self.path, self.frozen, self.review)

    def test_alternate_installed_module_is_rejected(self):
        import public_market_reader
        with patch.object(public_market_reader, "__file__", "/tmp/unreviewed/public_market_reader.py"):
            with self.assertRaisesRegex(ValueError, "module path differs"):
                replay.load_guava_adapter(self.path, self.frozen, self.review)

    def test_run_copies_the_verified_revision_into_output(self):
        module = replay.load_guava_adapter(self.path, self.frozen, self.review)
        manifest = {"expected_source_epochs": [], "focus_specifications": [], "prior_named_controls": [],
                    "evaluation_window": {"start_inclusive": "2026-09-01T00:00:00Z", "end_exclusive": "2026-09-02T00:00:00Z"},
                    "population": {"previous_analysis_event_ids": []}, "stop_sensitivity": {"values": []},
                    "statistics": {"family_trials_before_execution": {}}}
        with TemporaryDirectory() as td:
            output = Path(td) / "replay"
            replay.run(manifest, [], output, manifest_sha256="original-frozen-manifest", guava_adapter=module)
            self.assertEqual(json.loads((output / "GUAVA_ADAPTER_REVISION.json").read_text()), module.__guava_revision__)
            self.assertIn("GUAVA_ADAPTER_REVISION.json", json.loads((output / "OUTPUT_SHA256.json").read_text()))


if __name__ == "__main__":
    unittest.main()
