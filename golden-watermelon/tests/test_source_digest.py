from __future__ import annotations

from pathlib import Path

from polybot.source_digest import (
    ACTIVE_MANIFEST,
    ACTIVE_PREREGISTRATION,
    PROJECT_ROOT,
    SOURCE_PATHS,
    compute_strategy_source_digest,
    verify_frozen_manifest,
)


def test_source_digest_includes_create_only_migration_and_frozen_manifest() -> None:
    assert "src/polybot/db/migrations/0001_soccer_major_league_v3a.sql" in SOURCE_PATHS
    assert ACTIVE_PREREGISTRATION in SOURCE_PATHS
    assert ACTIVE_MANIFEST in SOURCE_PATHS
    assert len(compute_strategy_source_digest()) == 64


def test_frozen_manifest_verifies_every_declared_file() -> None:
    verify_frozen_manifest()
    manifest = PROJECT_ROOT / ACTIVE_MANIFEST
    targets = {
        (manifest.parent / line.split("  ", 1)[1]).resolve()
        for line in manifest.read_text(encoding="utf-8").splitlines()
    }
    expected = {
        (PROJECT_ROOT / relative).resolve()
        for relative in SOURCE_PATHS
        if relative != ACTIVE_MANIFEST
    }
    assert targets == expected
    assert PROJECT_ROOT / "config.yaml" in targets
    assert PROJECT_ROOT / "STRATEGY.md" in targets
    assert PROJECT_ROOT / "src/polybot/league_classifier.py" in targets
    assert (
        PROJECT_ROOT
        / "src/polybot/db/migrations/0001_soccer_major_league_v3a.sql"
    ) in targets
    assert all(path.is_file() for path in targets)
