import unittest
from urllib.parse import parse_qs, urlsplit

from scripts.sync_jenkins_status import (
    epoch_to_iso,
    job_api_url,
    job_update_payload,
    normalize_base_url,
    positive_integer,
    safe_message,
)


class SyncJenkinsStatusTests(unittest.TestCase):
    def test_normalize_base_url_removes_query_fragment_and_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_base_url("https://jenkins.example/base/?ignored=yes#fragment"),
            "https://jenkins.example/base",
        )

    def test_job_api_url_encodes_nested_job_names_and_tree(self) -> None:
        parsed = urlsplit(job_api_url("http://jenkins:8080", "folder/job name"))
        self.assertEqual(parsed.path, "/job/folder/job/job%20name/api/json")
        self.assertEqual(parse_qs(parsed.query)["tree"][0].split(",")[0], "name")

    def test_job_update_payload_matches_javascript_collector_contract(self) -> None:
        result = job_update_payload(
            {
                "color": "blue",
                "buildable": True,
                "inQueue": False,
                "lastBuild": {
                    "number": 42,
                    "building": False,
                    "result": "SUCCESS",
                    "timestamp": 1_000,
                    "duration": 250,
                },
            },
            "2026-08-18T12:00:00.000Z",
        )
        self.assertEqual(result["last_build_number"], 42)
        self.assertEqual(result["last_build_status"], "SUCCESS")
        self.assertEqual(result["last_build_started_at"], "1970-01-01T00:00:01.000Z")
        self.assertTrue(result["enabled"])

    def test_disabled_color_disables_job_when_buildable_is_unknown(self) -> None:
        result = job_update_payload(
            {"color": "disabled", "buildable": None, "lastBuild": None},
            "2026-08-18T12:00:00.000Z",
        )
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["last_build_number"])

    def test_validation_and_secret_redaction(self) -> None:
        self.assertEqual(positive_integer(None, 10_000), 10_000)
        with self.assertRaises(ValueError):
            positive_integer("0", 10_000)
        self.assertEqual(epoch_to_iso(False), None)
        self.assertEqual(safe_message("bad sb_secret_example token"), "bad [REDACTED] token")


if __name__ == "__main__":
    unittest.main()
