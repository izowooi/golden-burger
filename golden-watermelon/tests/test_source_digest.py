from __future__ import annotations

from pathlib import Path
import hashlib

from polybot.source_digest import (
    ACTIVE_MANIFEST,
    ACTIVE_PREREGISTRATION,
    ACTIVE_STORAGE_CONTRACT,
    PREVIOUS_MANIFEST,
    PREVIOUS_PREREGISTRATION,
    PROJECT_ROOT,
    SOURCE_PATHS,
    STORAGE_SOURCE_EPOCH,
    compute_strategy_source_digest,
    verify_frozen_manifest,
)


def test_source_digest_includes_create_only_migration_and_frozen_manifest() -> None:
    assert "src/polybot/db/migrations/0002_watermelon_major_sports_v4a.sql" in SOURCE_PATHS
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
        / "src/polybot/db/migrations/0002_watermelon_major_sports_v4a.sql"
    ) in targets
    assert all(path.is_file() for path in targets)


def test_shared_storage_epoch_preserves_predecessor_and_research_parameters() -> None:
    assert STORAGE_SOURCE_EPOCH == "watermelon-shared-data-v7"
    assert ACTIVE_STORAGE_CONTRACT in SOURCE_PATHS
    expected = {
        PREVIOUS_PREREGISTRATION: "3cd975ea20c04ae1293c9229ec29ecc458711d3200cedcc76a8a4a60fac3ee9f",
        PREVIOUS_MANIFEST: "a24707b70bff5b006b3e2b17836fd791c77eef740749dd70261ef175fdd69f51",
        "config.yaml": "8e4a46474758a6a130ed70e4990f801d34571d15d4dbd68add8d8c16353ec5bb",
        "STRATEGY.md": "2da7cd64660b5ed44cc915b56354a7a6264db5b25235b93947efddeef1efb690",
        "src/polybot/league_classifier.py": "6dbee50ed4d27cc024905619424b9d876a6eaaeb6e496f385511197516c87c1c",
    }
    for relative, expected_sha in expected.items():
        assert hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest() == expected_sha
