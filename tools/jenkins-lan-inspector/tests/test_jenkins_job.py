from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "inspect-jenkins-job"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from jenkins_job import (  # noqa: E402
    JenkinsInputError,
    build_selector_path,
    job_path,
    parse_config,
    parse_workspace_entries,
    sanitize_text,
    sanitize_url,
)


FREESTYLE_CONFIG = b"""<?xml version='1.1' encoding='UTF-8'?>
<project>
  <description>Resolution Momentum</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <scm class="hudson.plugins.git.GitSCM" plugin="git@5.7.0">
    <userRemoteConfigs>
      <hudson.plugins.git.UserRemoteConfig>
        <url>https://user:password@example.test/team/repo.git?token=abc&amp;view=1</url>
        <credentialsId>private-credential-id</credentialsId>
      </hudson.plugins.git.UserRemoteConfig>
    </userRemoteConfigs>
    <branches>
      <hudson.plugins.git.BranchSpec><name>*/main</name></hudson.plugins.git.BranchSpec>
    </branches>
  </scm>
  <canRoam>true</canRoam>
  <disabled>false</disabled>
  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>
  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>
  <triggers>
    <hudson.triggers.TimerTrigger><spec>H/5 * * * *</spec></hudson.triggers.TimerTrigger>
  </triggers>
  <concurrentBuild>false</concurrentBuild>
  <builders>
    <hudson.tasks.Shell>
      <command>#!/bin/bash
export LOG_LEVEL=INFO
export POLYMARKET_PRIVATE_KEY=deadbeef-secret
export POLYMARKET_FUNDER_ADDRESS=0xabc123
cd ./golden-cherry
uv run python ./main.py run --yes-only</command>
    </hudson.tasks.Shell>
  </builders>
  <publishers/>
  <buildWrappers/>
</project>
"""


class PathTests(unittest.TestCase):
    def test_job_path_supports_jenkins_folders(self) -> None:
        self.assertEqual(job_path("team/polybot yellow"), "/job/team/job/polybot%20yellow")

    def test_job_path_rejects_traversal(self) -> None:
        for value in ("", ".", "..", "team/../job", "/job"):
            with self.subTest(value=value), self.assertRaises(JenkinsInputError):
                job_path(value)

    def test_build_selector_is_allowlisted(self) -> None:
        self.assertEqual(build_selector_path("47971"), "47971")
        self.assertEqual(build_selector_path("lastSuccessfulBuild"), "lastSuccessfulBuild")
        with self.assertRaises(JenkinsInputError):
            build_selector_path("lastBuild/../config.xml")


class RedactionTests(unittest.TestCase):
    def test_shell_and_console_assignments_are_redacted(self) -> None:
        source = (
            "+ export POLYMARKET_PRIVATE_KEY=super-secret\n"
            "echo super-secret\n"
            "API_TOKEN='another secret'\n"
            "export LOG_LEVEL=INFO\n"
            "curl -H 'Authorization: Bearer token-value' https://example.test\n"
        )
        sanitized, names = sanitize_text(source)
        self.assertNotIn("super-secret", sanitized)
        self.assertNotIn("another secret", sanitized)
        self.assertNotIn("token-value", sanitized)
        self.assertIn("echo [REDACTED]", sanitized)
        self.assertIn("POLYMARKET_PRIVATE_KEY=[REDACTED]", sanitized)
        self.assertIn("API_TOKEN=[REDACTED]", sanitized)
        self.assertIn("LOG_LEVEL=INFO", sanitized)
        self.assertEqual(names, ["API_TOKEN", "POLYMARKET_PRIVATE_KEY"])

    def test_url_credentials_and_sensitive_query_are_redacted(self) -> None:
        value = "https://user:password@example.test/repo?token=abc&view=1"
        sanitized = sanitize_url(value)
        self.assertEqual(
            sanitized,
            "https://[REDACTED]@example.test/repo?token=%5BREDACTED%5D&view=1",
        )


class ConfigParsingTests(unittest.TestCase):
    def test_freestyle_config_is_summarized_without_secrets(self) -> None:
        result = parse_config(FREESTYLE_CONFIG, anonymous_read=True, base_scheme="http")

        self.assertEqual(result["type"], "project")
        self.assertEqual(result["description"], "Resolution Momentum")
        self.assertEqual(result["scm"]["branches"], ["*/main"])
        self.assertTrue(result["scm"]["credentials_configured"])
        self.assertNotIn("private-credential-id", str(result))
        self.assertEqual(result["triggers"][0]["spec"], "H/5 * * * *")

        script = result["builders"][0]["script"]
        self.assertIn("cd ./golden-cherry", script)
        self.assertNotIn("deadbeef-secret", script)
        self.assertNotIn("0xabc123", script)
        self.assertEqual(
            result["inline_sensitive_variables"],
            ["POLYMARKET_FUNDER_ADDRESS", "POLYMARKET_PRIVATE_KEY"],
        )

        finding_codes = {finding["code"] for finding in result["security_findings"]}
        self.assertIn("INLINE_SECRET_IN_JOB_CONFIG", finding_codes)
        self.assertIn("ANONYMOUS_CONFIG_READ", finding_codes)
        self.assertIn("PLAINTEXT_HTTP", finding_codes)


class WorkspaceParsingTests(unittest.TestCase):
    def test_workspace_parser_keeps_relative_entries_only(self) -> None:
        page = b"""
        <html><body>
          <a href="#skip">skip</a>
          <a href="/job/demo/">job</a>
          <a href="?token=secret">query</a>
          <a href="golden-cherry/">golden-cherry/</a>
          <a href="README.md/*view*/">README.md</a>
          <a href="https://example.test/">external</a>
        </body></html>
        """
        self.assertEqual(
            parse_workspace_entries(page),
            [
                {"name": "golden-cherry", "kind": "directory"},
                {"name": "README.md", "kind": "file"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
