"""Deployment files must preserve the current account/evidence contract."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_jenkins_and_env_example_supply_all_eleven_accounts_and_archive_evidence():
    jenkinsfile = (PROJECT_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    for slot in range(1, 12):
        assert f"ACCOUNT_{slot}_NAME" in jenkinsfile
        assert f"ACCOUNT_{slot}_ADDRESS" in jenkinsfile
        assert f"ACCOUNT_{slot}_NAME" in env_example
        assert f"ACCOUNT_{slot}_ADDRESS" in env_example
    assert "polymarket-golden-eco-address" in jenkinsfile
    assert "polymarket-golden-fox-address" in jenkinsfile
    assert "polymarket-golden-lion-address" in jenkinsfile
    assert "polymarket-golden-tiger-address" in jenkinsfile
    assert "polymarket-golden-wolf-address" in jenkinsfile
    assert "polymarket-golden-eagle-address" in jenkinsfile
    assert "polymarket-golden-bear-address" in jenkinsfile
    assert "post {" in jenkinsfile
    assert "always {" in jenkinsfile
    assert "daily_evidence.sqlite3" in jenkinsfile
    assert "fingerprint: true" in jenkinsfile
    assert "DAILY_REPORT_LOG_FILE" in jenkinsfile
    assert "daily_report_*.log" not in jenkinsfile
    assert '${env.DAILY_REPORT_LOG_FILE}' in jenkinsfile
    assert 'rm -f -- "daily-report/$DAILY_REPORT_LOG_FILE"' in jenkinsfile
    assert "sh '''" in jenkinsfile
    assert '"$SLACK_WEBHOOK_URL"' in jenkinsfile
    assert "${slackWebhook}" not in jenkinsfile
    assert "TZ=Asia/Seoul\\n0 9 * * *" in jenkinsfile


def test_broken_console_entrypoints_are_not_published():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "polybot_reporter.cli:main" not in pyproject
    assert "polybot_reporter.test_cli:main" not in pyproject


def test_deployment_docs_require_atomic_migration_and_restricted_evidence_backup():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    jenkins_setup = (PROJECT_ROOT / "JENKINS_SETUP.md").read_text(encoding="utf-8")

    assert "pb_portfolio_history_v3.sql" in readme
    assert "unsafe" in readme
    assert "암호화된" in readme
    assert "권한을 제한" in readme
    assert "pb_portfolio_history_v3.sql" in jenkins_setup
    assert "암호화된" in jenkins_setup
    assert "raw wallet address" in jenkins_setup


def test_daily_pipeline_preflights_but_never_installs_database_migrations():
    jenkinsfile = (PROJECT_ROOT / "Jenkinsfile").read_text(encoding="utf-8")

    assert "check-supabase" in jenkinsfile
    assert "Generate Report" in jenkinsfile
    assert jenkinsfile.index("check-supabase") < jenkinsfile.index("Generate Report")
    assert "apply_supabase_migrations" not in jenkinsfile
    assert "PGPASSWORD" not in jenkinsfile
    assert "psql" not in jenkinsfile
