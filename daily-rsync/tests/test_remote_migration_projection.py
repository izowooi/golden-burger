from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from daily_rsync.remote_agent import sqlite_source_state, storage_migration_record


def migration_manifest() -> dict:
    return {
        "contract": "shared-public-bodies-migration-v1",
        "status": "VERIFIED",
        "strategy": "golden-coconut",
        "source_sha256": "a" * 64,
        "destination_sha256": "b" * 64,
        "source_bytes": 100,
        "destination_bytes": 50,
        "tables": {
            "requests": {
                "rows": 2,
                "logical_sha256": "c" * 64,
                "externalized_cells": 1,
                "inline_original_bytes": 20,
                "reference_bytes": 10,
            },
            "book_levels": {
                "rows": 3,
                "logical_sha256": "d" * 64,
                "externalized_level_rows": 3,
                "key_basis": "declared primary key",
                "implicit_rowid_preserved": False,
            },
        },
    }


def write_manifest(database: Path, value: object) -> bytes:
    raw = json.dumps(value).encode()
    Path(str(database) + ".storage-migration.json").write_bytes(raw)
    return raw


def test_storage_migration_projects_only_reviewed_bounded_evidence(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    value = migration_manifest()
    expected = migration_manifest()
    value["private_config"] = "PRIVATE_SENTINEL"
    value["tables"]["requests"]["raw_row"] = "PRIVATE_SENTINEL"
    raw = write_manifest(database, value)
    result = storage_migration_record(database)
    assert result["manifest"] == expected
    assert result["sidecar_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "PRIVATE_SENTINEL" not in json.dumps(result)


def projection_manifest(strategy="golden-blueberry"):
    from daily_rsync.remote_agent import PROJECTION_KINDS

    value = migration_manifest()
    namespace = json.dumps({'source': 'macmini-m5', 'jenkins_job': 'job', 'strategy': strategy,
                            'runtime': 'default'}, sort_keys=True, separators=(',', ':'))
    authority = 'f112edce-971d-47c5-a157-64c71586e8c4'
    value['strategy'] = strategy
    value['projection_namespace'] = namespace
    value['projection_authority_uuid'] = authority
    value['tables']['market_snapshots'] = {
        'rows': 2, 'logical_sha256': 'e' * 64, 'externalized_projection_rows': 2,
        'key_basis': 'declared INTEGER primary key', 'implicit_rowid_preserved': False,
    }
    value['public_projection_records'] = {
        'contract': 'public-projection-closure-v1', 'record_count': 1, 'receipt_count': 2,
        'raw_bytes': 10, 'namespace': namespace, 'authority_uuid': authority,
        'closure_sha256': 'f' * 64, 'kinds': list(PROJECTION_KINDS[strategy]),
    }
    return value


def test_remote_projection_profile_mirror_matches_actual_registry():
    from daily_rsync.remote_agent import PROJECTION_KINDS
    from polybot_observability.market_data_projection_profiles import SNAPSHOT_PROFILES

    assert PROJECTION_KINDS == {strategy: tuple(sorted(profile.groups)) for strategy, profile in SNAPSHOT_PROFILES.items()}


@pytest.mark.parametrize('strategy', ['golden-blueberry', 'golden-kiwi', 'golden-plum', 'golden-watermelon-live'])
def test_projection_manifest_preserves_exact_kind_set_and_excludes_private_fields(tmp_path, strategy):
    database = tmp_path / 'source.db'
    value = projection_manifest(strategy)
    expected = projection_manifest(strategy)
    value['private_config'] = 'PRIVATE_SENTINEL'
    value['public_projection_records']['private_run_id'] = 'PRIVATE_SENTINEL'
    write_manifest(database, value)
    result = storage_migration_record(database)
    assert result['manifest'] == expected
    assert 'PRIVATE_SENTINEL' not in json.dumps(result)


@pytest.mark.parametrize('damage', ['unknown_kind', 'other_source', 'authority', 'row_count', 'unsupported_strategy'])
def test_projection_manifest_rejects_forged_ownership_or_kind_contract(tmp_path, damage):
    database = tmp_path / 'source.db'
    value = projection_manifest()
    if damage == 'unknown_kind':
        value['public_projection_records']['kinds'] = ['private-order-v1']
    elif damage == 'other_source':
        value['public_projection_records']['namespace'] = '{}'
    elif damage == 'authority':
        value['projection_authority_uuid'] = 'not-a-uuid'
    elif damage == 'row_count':
        value['tables']['market_snapshots']['externalized_projection_rows'] = True
    else:
        value['strategy'] = 'golden-date'
    write_manifest(database, value)
    with pytest.raises(RuntimeError):
        storage_migration_record(database)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rows", -1),
        ("rows", True),
        ("rows", "1"),
        ("externalized_cells", 1.5),
        ("externalized_level_rows", -1),
        ("logical_sha256", "C" * 64),
        ("logical_sha256", "a" * 63),
        ("implicit_rowid_preserved", 0),
        ("key_basis", "PRIVATE_SENTINEL"),
    ],
)
def test_storage_migration_rejects_forged_table_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    database = tmp_path / "source.db"
    manifest = migration_manifest()
    manifest["tables"]["requests"][field] = value
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="storage migration") as failure:
        storage_migration_record(database)
    assert "PRIVATE_SENTINEL" not in str(failure.value)


@pytest.mark.parametrize("name", ["", "x" * 257, "invalid\nname", "quote'", "../path"])
def test_storage_migration_rejects_unbounded_or_nonidentifier_table_names(
    tmp_path: Path,
    name: str,
) -> None:
    database = tmp_path / "source.db"
    manifest = migration_manifest()
    manifest["tables"][name] = manifest["tables"].pop("requests")
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="table identity"):
        storage_migration_record(database)


@pytest.mark.parametrize("value", [[], {"contract": "other"}])
def test_storage_migration_rejects_nonmanifest_json(tmp_path: Path, value: object) -> None:
    database = tmp_path / "source.db"
    write_manifest(database, value)
    with pytest.raises(RuntimeError, match="not a verified migration"):
        storage_migration_record(database)


LINKAGE_KEYS = (
    "source_snapshot_sha256",
    "source_file_sha256",
    "original_source_path",
    "original_source_fingerprint",
    "original_source_members",
)


def linked_manifest(database: Path) -> dict:
    database.write_bytes(b"old offline source")
    state = sqlite_source_state(database)
    manifest = migration_manifest()
    manifest.update(
        source_snapshot_sha256="e" * 64,
        source_file_sha256=manifest["source_sha256"],
        original_source_path=str(database),
        original_source_fingerprint=state["fingerprint"],
        original_source_members=state["members"],
    )
    return manifest


def test_snapshot_linkage_retains_distinct_snapshot_and_file_hashes(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    original = manifest["original_source_members"][0]
    assert set(original) == {"suffix", "size_bytes", "mtime_ns", "inode"}
    assert original["suffix"] == "main"
    raw = write_manifest(database, manifest)
    # Migration changes the current file's physical metadata. Linkage describes
    # the preserved original, not a second attestation of the current file.
    database.write_bytes(b"new physically distinct derivative")
    result = storage_migration_record(database)
    assert result["manifest"] == manifest
    assert result["sidecar_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["manifest"]["source_snapshot_sha256"] != manifest["source_sha256"]


@pytest.mark.parametrize("missing", LINKAGE_KEYS)
def test_snapshot_linkage_rejects_any_partial_field_set(tmp_path: Path, missing: str) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    del manifest[missing]
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="snapshot linkage is incomplete"):
        storage_migration_record(database)


@pytest.mark.parametrize("field", LINKAGE_KEYS)
def test_snapshot_linkage_rejects_each_field_on_its_own(tmp_path: Path, field: str) -> None:
    database = tmp_path / "source.db"
    linked = linked_manifest(database)
    manifest = migration_manifest()
    manifest[field] = linked[field]
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="snapshot linkage is incomplete"):
        storage_migration_record(database)


@pytest.mark.parametrize(
    "field", ["source_snapshot_sha256", "source_file_sha256", "original_source_fingerprint"]
)
@pytest.mark.parametrize("invalid", [None, "A" * 64, "a" * 63, 123])
def test_snapshot_linkage_requires_lowercase_sha256(
    tmp_path: Path, field: str, invalid: object
) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    manifest[field] = invalid
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="snapshot linkage checksum"):
        storage_migration_record(database)


def test_snapshot_linkage_source_file_hash_must_match_migration_source(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    manifest["source_file_sha256"] = "f" * 64
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="source file checksum mismatch"):
        storage_migration_record(database)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "relative/source.db",
        "/tmp/../source.db",
        "/tmp/./source.db",
        "//tmp/source.db",
        "/tmp//source.db",
        "/tmp/source.db/",
        "/tmp/PRIVATE_SENTINEL\nsource.db",
        "/tmp/PRIVATE_SENTINEL\x7fsource.db",
        "/tmp/PRIVATE_SENTINEL\x85source.db",
        "/tmp\\source.db",
        "/tmp/other.db",
        "/" + "x" * 4096,
    ],
)
def test_snapshot_linkage_rejects_noncanonical_or_mismatched_path(
    tmp_path: Path, invalid: object
) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    manifest["original_source_path"] = invalid
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="original source path") as failure:
        storage_migration_record(database)
    assert "PRIVATE_SENTINEL" not in str(failure.value)


def test_snapshot_linkage_rejects_path_through_symlinked_parent(tmp_path: Path) -> None:
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    database = alias / "source.db"
    manifest = linked_manifest(database)
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="original source path"):
        storage_migration_record(database)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [],
        [None],
        [{"suffix": "main"}],
        [{"suffix": "-wal", "size_bytes": 1, "mtime_ns": 2, "inode": 3}],
        [
            {"suffix": "main", "size_bytes": 1, "mtime_ns": 2, "inode": 3},
            {"suffix": "-wal", "size_bytes": 1, "mtime_ns": 2, "inode": 4},
        ],
        [
            {
                "suffix": "main",
                "size_bytes": 1,
                "mtime_ns": 2,
                "inode": 3,
                "private": "PRIVATE_SENTINEL",
            }
        ],
    ],
)
def test_snapshot_linkage_members_are_exactly_one_offline_main(
    tmp_path: Path, invalid: object
) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    manifest["original_source_members"] = invalid
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="original source members") as failure:
        storage_migration_record(database)
    assert "PRIVATE_SENTINEL" not in str(failure.value)


@pytest.mark.parametrize("field", ["size_bytes", "mtime_ns", "inode"])
@pytest.mark.parametrize("invalid", [True, -1, "1", 1.5])
def test_snapshot_linkage_member_stats_require_nonnegative_integer(
    tmp_path: Path, field: str, invalid: object
) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    manifest["original_source_members"][0][field] = invalid
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="original source members"):
        storage_migration_record(database)


def test_snapshot_linkage_fingerprint_binds_the_member_record(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    manifest = linked_manifest(database)
    manifest["original_source_members"][0]["inode"] += 1
    write_manifest(database, manifest)
    with pytest.raises(RuntimeError, match="original source fingerprint mismatch"):
        storage_migration_record(database)


def catalog_manifest(strategy="golden-blueberry", *, combined=True):
    from daily_rsync.remote_agent import CATALOG_KINDS, PROJECTION_KINDS

    value = projection_manifest(strategy)
    if not combined:
        del value["tables"]["market_snapshots"]
    value["tables"]["market_catalog"] = {
        "rows": 2, "logical_sha256": "1" * 64, "externalized_catalog_rows": 2,
        "key_basis": "declared-primary-key+rowid-v1", "implicit_rowid_preserved": True,
    }
    layouts = {"market_catalog": list(CATALOG_KINDS[strategy])}
    if combined:
        layouts["market_snapshots"] = list(PROJECTION_KINDS[strategy])
    counts = {table: 2 * len(kinds) for table, kinds in layouts.items()}
    value["public_projection_records"].update(
        contract="public-projection-closure-v2", layouts=layouts,
        layout_receipt_counts=counts, receipt_count=sum(counts.values()),
        kinds=sorted({kind for kinds in layouts.values() for kind in kinds}),
    )
    return value


def test_remote_catalog_profile_mirror_matches_actual_registry():
    from daily_rsync.remote_agent import CATALOG_KINDS
    from polybot_observability.market_data_catalog_profiles import CATALOG_PROFILES

    assert CATALOG_KINDS == {
        strategy: tuple(sorted(profile.groups)) for strategy, profile in CATALOG_PROFILES.items()
    }


@pytest.mark.parametrize("strategy", ["golden-blueberry", "golden-honeydew", "golden-watermelon-live", "golden-plum"])
@pytest.mark.parametrize("combined", [False, True])
def test_remote_catalog_closure_preserves_layout_counts_and_real_rowid_contract(tmp_path,strategy,combined):
    database = tmp_path / "source.db"
    value = catalog_manifest(strategy, combined=combined)
    expected = catalog_manifest(strategy, combined=combined)
    value["public_projection_records"]["private_account"] = "PRIVATE_SENTINEL"
    write_manifest(database, value)
    result = storage_migration_record(database)
    assert result["manifest"] == expected
    assert "PRIVATE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("damage", ["unknown_layout", "unknown_kind", "count_type", "receipt_total", "catalog_missing", "v1_with_layout", "rowid_basis"])
def test_remote_catalog_rejects_unreviewed_layout_or_count_shape(tmp_path,damage):
    database = tmp_path / "source.db"
    value = catalog_manifest()
    closure = value["public_projection_records"]
    if damage == "unknown_layout":
        closure["layouts"]["private_ledger"] = []
    elif damage == "unknown_kind":
        closure["layouts"]["market_catalog"] = ["private-order-v1"]
    elif damage == "count_type":
        closure["layout_receipt_counts"]["market_catalog"] = True
    elif damage == "receipt_total":
        closure["receipt_count"] += 1
    elif damage == "catalog_missing":
        del closure["layouts"]["market_catalog"]
        del closure["layout_receipt_counts"]["market_catalog"]
    elif damage == "v1_with_layout":
        closure["contract"] = "public-projection-closure-v1"
    else:
        value["tables"]["market_catalog"]["key_basis"] = "implicit view rowid"
    write_manifest(database, value)
    with pytest.raises(RuntimeError):
        storage_migration_record(database)
