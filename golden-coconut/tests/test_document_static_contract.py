from __future__ import annotations

import re
from pathlib import Path

from polybot.source_digest import verify_frozen_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_manifest_and_registry_hash_are_valid():
    verify_frozen_manifest()


def test_no_transaction_sdk_or_authenticated_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").casefold()
    assert "py-clob-client" not in pyproject
    assert "web3" not in pyproject
    assert "dotenv" not in pyproject
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src").rglob("*.py")
    ).casefold()
    for forbidden in ("create_order", "post_order", "market_order", "sign_message"):
        assert forbidden not in source
    assert "trust_env = false" in source


def test_sql_has_no_forbidden_transaction_table_names_and_no_alter():
    sql = (
        ROOT / "src/polybot/db/migrations/0003_major_sports_lifecycle_v3.sql"
    ).read_text(encoding="utf-8")
    tables = {
        match.casefold()
        for match in re.findall(r"CREATE TABLE\s+([A-Za-z0-9_]+)", sql, re.IGNORECASE)
    }
    assert tables.isdisjoint({"orders", "fills", "positions", "wallets", "trades", "pnl"})
    assert "ALTER TABLE" not in sql.upper()
    assert "IF NOT EXISTS" not in sql.upper()


def test_v2_migration_is_preserved_beside_create_only_v3():
    v2 = (
        ROOT / "src/polybot/db/migrations/0002_major_sports_lifecycle_v2.sql"
    ).read_text(encoding="utf-8")
    v3 = (
        ROOT / "src/polybot/db/migrations/0003_major_sports_lifecycle_v3.sql"
    ).read_text(encoding="utf-8")
    assert "PRAGMA user_version=2" in v2
    assert "start_date_min TEXT NOT NULL" in v2
    assert "start_date_max TEXT NOT NULL" in v2
    assert "PRAGMA user_version=3" in v3
    assert "start_time_min TEXT NOT NULL" in v3
    assert "start_time_max TEXT NOT NULL" in v3
    assert "ALTER TABLE" not in v3.upper()


def test_docs_and_config_use_daily_rsync_canonical_path_and_preseason_contract():
    paths = [
        ROOT / "README.md",
        ROOT / "OPERATIONS.md",
        ROOT / "AGENTS.md",
        ROOT / "config.yaml",
        ROOT.parent / "docs/retro/golden-coconut.md",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "trades_sim.db" in source
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "PRESEASON" in combined
    assert "polybot-gold" in combined
    assert "/Volumes/t7/jenkins/polybot-gold" in combined
