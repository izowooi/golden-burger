"""The presentation packaging must not interpolate, resample, or mix sources."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "visual_pack", Path(__file__).resolve().parents[1] / "build_sports_path_visual.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VisualPackTests(unittest.TestCase):
    def fixture(self, root):
        matches = []
        for name in ("grey", "white"):
            meta = dict(id=name, event_id="same", title="Example", slug="match",
                        sport="soccer", source_id=f"polybot-{name}:runtime",
                        cohort_id=name, start=1, end=180, point_count=2,
                        invalid_count=1, gap_count=1, fragment=name+".json")
            point = [1.123, 0, .954321, .932145, .94, .93, .95, 8.5, 1, 0]
            value = {"tokens": [{"label": "HOME YES", "token_id": "unused",
                                "result_kind": "HOME", "outcome_side": "YES",
                                "payout": None}],
                     "points": [point, [180.789, *point[1:]]],
                     "gaps": [{"start": 1.123, "end": 180.789, "token": 0}],
                     "clocks": [{"source_sport_context": {"fields": {
                         "period": "Q1", "elapsed": "12:01", "score": "0-0"}}}]}
            (root / meta["fragment"]).write_text(json.dumps(value))
            matches.append(meta)
        return {"matches": matches, "sports": []}

    def test_all_points_and_quality_fields_survive_without_source_stitching(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            index = self.fixture(root)
            data = MODULE.display_payload(index, root, white=False)
            self.assertEqual(list(data["fragments"]), ["grey"])
            f = data["fragments"]["grey"]
            self.assertEqual(len(f["points"]), 2)
            self.assertEqual(f["points"][0][:4], [1.123, 0, .954321, .932145])
            self.assertEqual(f["points"][0][7:], [8.5, 1, 0])
            self.assertEqual(f["clocks"][0]["period"], "Q1")
            self.assertEqual(f["gaps"][0]["end"], 180.789)
            self.assertIsNone(f["tokens"][0]["payout"])
            self.assertEqual(list(MODULE.display_payload(index, root, white=True)["fragments"]), ["white"])

    def test_fragment_path_cannot_escape_export_directory(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            index = self.fixture(root)
            index["matches"][0]["fragment"] = "../outside.json"
            with self.assertRaisesRegex(ValueError, "outside"):
                MODULE.display_payload(index, root, white=False)


if __name__ == "__main__":
    unittest.main()
