"""Static guardrails for the preregistered two-job Jenkins experiment."""

from pathlib import Path


JENKINSFILE = Path(__file__).resolve().parents[1] / "Jenkinsfile"


def test_jenkins_arms_share_wall_clock_but_not_runtime_job():
    source = JENKINSFILE.read_text(encoding="utf-8")

    assert "cron('*/5 * * * *')" in source
    assert "cron('H/5 * * * *')" not in source
    assert "disableConcurrentBuilds()" in source
    assert "POLYBOT_MIN_SURGE=0.02" in source
    assert "POLYBOT_MIN_SURGE=0.05" in source
    assert "RUNTIME_JOB=blueberry-live-a-2pp" in source
    assert "RUNTIME_JOB=blueberry-live-b-5pp" in source


def test_jenkins_requires_explicit_live_and_bound_credentials():
    source = JENKINSFILE.read_text(encoding="utf-8")

    assert "BLUEBERRY_LIVE" in source
    assert "POLYMARKET_PRIVATE_KEY:?Bind" in source
    assert "POLYMARKET_FUNDER_ADDRESS:?Bind" in source
    assert "set +x" in source
