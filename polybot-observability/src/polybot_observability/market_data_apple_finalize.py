"""Finalize an offline Apple collection-v2 stage; never replace its source.

The intermediate migration remains separate evidence. Only computed monthly
budget metadata is added, then original-clock observations are ACKed and read
back. Verification compares private physical cells as well as decoded frames.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from itertools import zip_longest
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import zlib

from . import market_data_apple as apple
from .market_data_migrate import _cell_bytes, _select, _update_digest, file_sha256
from .market_data_refs import PayloadReferences
from .market_data_store import Observation, validate_hashes

CONTRACT = "apple-monthly-finalization-v1"
MIGRATION_CONTRACT = "shared-public-bodies-migration-v1"
MAX_MANIFEST_BYTES = 8 << 20
_CONTRACT_KEY = "shared_frame_budget_contract"
_RESERVED_KEY = "shared_frame_reserved_bytes"
_RESERVATION_PREFIX = "shared_frame_reservation:"
_INTERMEDIATE_KEYS = frozenset({"contract", "status", "strategy", "source_sha256",
    "destination_sha256", "tables", "source_bytes", "destination_bytes",
    "payload_hashes", "shared_payload_count"})
_SCHEMA_SQL = ("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
               "AND name NOT LIKE 'sqlite_%' ORDER BY type,name")


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bounded_read(path: Path, limit: int = MAX_MANIFEST_BYTES) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Apple manifest exceeds its bounded size")
    return raw


def _offline(path: Path) -> Path:
    path = Path(path).expanduser().absolute()
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("Apple finalization requires direct existing file paths")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal", "-shm")):
        raise ValueError("Apple finalization requires offline files without WAL/journal/SHM")
    return path


def _read_connection(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    return db


def _identity(db: sqlite3.Connection) -> dict:
    if (db.execute("PRAGMA application_id").fetchone()[0] != apple.APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != 1
            or db.execute("PRAGMA page_size").fetchone()[0] != 4096
            or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
        raise ValueError("Apple monthly SQLite identity/page/journal mismatch")
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Apple monthly integrity check failed")
    meta = dict(db.execute("SELECT key,value FROM meta"))
    if (meta.get("format") != apple.ORIGINAL_FORMAT
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", meta.get("job", ""))
            or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", meta.get("month", ""))):
        raise ValueError("Apple monthly metadata identity mismatch")
    return meta


def _configured_limit(db, effective_config_hash: str, maximum_bytes: int) -> None:
    validate_hashes([effective_config_hash])
    row = db.execute("SELECT document FROM configs WHERE hash=?", (effective_config_hash,)).fetchone()
    if row is None or not isinstance(row[0], str):
        raise ValueError("effective config is absent from original monthly provenance")
    config = json.loads(row[0])
    if _canonical(config).decode() != row[0] or hashlib.sha256(row[0].encode()).hexdigest() != effective_config_hash:
        raise ValueError("original effective config hash/canonical document mismatch")
    configured = config.get("storage", {}).get("max_month_db_bytes_per_job")
    if type(maximum_bytes) is not int or type(configured) is not int or maximum_bytes != configured or maximum_bytes <= 0:
        raise ValueError("monthly limit must equal the supplied original effective config")


def _observation(blob, *, slot, run_key, finished, month, job, observer):
    header = apple.frame_provenance(blob)
    if not header["public_count"]:
        return None
    if (type(slot) is not int or slot < 0 or not isinstance(run_key, str)
            or not re.fullmatch(r"(?:jenkins|manual):[0-9]+", run_key)
            or type(finished) not in (float, int) or not math.isfinite(finished)):
        raise ValueError("Apple frame requires its original canonical slot/run key/finished clock")
    metadata = {"job": job, **{key: header[key] for key in
                ("public_codec", "public_raw_bytes", "public_count")}}
    observation = Observation(observer, f"{month}/{slot}/{run_key}",
        datetime.fromtimestamp(finished, timezone.utc).isoformat(), apple.PUBLIC_FRAME_KIND,
        header["public_payload_sha256"], metadata_json=_canonical(metadata).decode())
    observation.row()
    return observation


def _verify_observations(reader, expected: list[Observation]) -> str:
    digest = hashlib.sha256()
    for observation in expected:
        _update_digest(digest, observation.row())
    if not expected:
        return digest.hexdigest()
    lookup = getattr(reader, "get_observations", None)
    if not callable(lookup):
        raise ValueError("Apple finalization requires independent observation readback")
    for offset in range(0, len(expected), 1024):
        batch = expected[offset:offset + 1024]
        page = lookup([(item.observer, item.observation_id) for item in batch])
        if len(page) != len(batch):
            raise ValueError("Apple historical observation readback is incomplete")
        for wanted, found in zip(batch, page, strict=True):
            if found is None:
                raise ValueError("Apple historical observation readback is incomplete")
            if found.row() != wanted.row() or found.subjects():
                raise ValueError("Apple historical observation differs from original frame")
    return digest.hexdigest()


def _inspect(original: Path, target: Path, references: PayloadReferences, *, observer: str,
             effective_config_hash: str, maximum_bytes: int, require_seed: bool,
             require_observations: bool) -> dict:
    if not isinstance(observer, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", observer):
        raise ValueError("Apple finalization requires an explicit bounded observer identity")
    # Uncached reads prevent the publisher from proving closure using its own cache.
    refs = PayloadReferences(reader=references.reader, cache_bytes=0)
    with closing(_read_connection(original)) as old, closing(_read_connection(target)) as new:
        old_meta, new_meta = _identity(old), _identity(new)
        if any(key in (_CONTRACT_KEY, _RESERVED_KEY) or key.startswith(_RESERVATION_PREFIX) for key in old_meta):
            raise ValueError("Apple original already contains shared budget metadata")
        _configured_limit(old, effective_config_hash, maximum_bytes)
        for pragma in ("application_id", "user_version", "encoding", "page_size"):
            if old.execute("PRAGMA " + pragma).fetchall() != new.execute("PRAGMA " + pragma).fetchall():
                raise ValueError("Apple monthly SQLite identity changed")
        schema = old.execute(_SCHEMA_SQL).fetchall()
        if schema != new.execute(_SCHEMA_SQL).fetchall() or any("VIRTUAL TABLE" in row[3].upper() for row in schema):
            raise ValueError("Apple monthly schema changed or unsupported virtual table")
        tables = {row[1] for row in schema if row[0] == "table"}
        if not {"meta", "configs", "runs", "watch"} <= tables:
            raise ValueError("Apple native monthly tables missing")
        for internal in ("sqlite_sequence", "sqlite_stat1", "sqlite_stat4"):
            present = [bool(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (internal,)).fetchone())
                       for db in (old, new)]
            if present[0] != present[1] or (present[0] and
                    sorted(old.execute("SELECT * FROM " + internal).fetchall(), key=repr) !=
                    sorted(new.execute("SELECT * FROM " + internal).fetchall(), key=repr)):
                raise ValueError("Apple SQLite internal state changed")
        if sorted(old.execute("PRAGMA foreign_key_check").fetchall(), key=repr) != sorted(
                new.execute("PRAGMA foreign_key_check").fetchall(), key=repr):
            raise ValueError("Apple foreign-key evidence changed")
        source_tables, final_tables, payload_hashes = {}, {}, set()
        reservations, observations, frame_count, reserved = {}, [], 0, 0
        for table in sorted(tables - {"meta"}):
            columns, query = _select(old, table)
            before_digest, after_digest, count = hashlib.sha256(), hashlib.sha256(), 0
            missing = object()
            for left, right in zip_longest(old.execute(query), new.execute(query), fillvalue=missing):
                if left is missing or right is missing:
                    raise ValueError("Apple monthly row count changed: " + table)
                decoded = list(right)
                if table == "runs":
                    source_row, target_row = dict(zip(columns, left)), dict(zip(columns, right))
                    source_frame, target_frame = source_row["frame"], target_row["frame"]
                    if source_frame is not None:
                        if apple.is_shared_frame(source_frame) or not apple.is_shared_frame(target_frame):
                            raise ValueError("Apple monthly requires inline original and fully shared stage")
                        restored = apple.resolve_frame(target_frame, refs)
                        if restored != source_frame:
                            raise ValueError("Apple frame original compressed bytes changed")
                        raw = zlib.decompress(restored)
                        if (type(source_row["frame_raw_bytes"]) is not int
                                or len(raw) != source_row["frame_raw_bytes"]
                                or hashlib.sha256(raw).hexdigest() != source_row["frame_sha256"]):
                            raise ValueError("Apple original frame checksum/size evidence mismatch")
                        decoded[columns.index("frame")] = restored
                        frame_count += 1
                        header = apple.frame_provenance(target_frame)
                        observation = _observation(target_frame, slot=source_row["slot"],
                            run_key=source_row["run_key"], finished=source_row["finished"],
                            month=old_meta["month"], job=old_meta["job"], observer=observer)
                        if header["public_count"]:
                            sha = header["public_payload_sha256"]
                            plan = apple.PreparedFrame(target_frame, refs.reader.get_many([sha])[0])
                            amount = plan.public_budget_bytes
                            reserved += amount
                            reservations[_RESERVATION_PREFIX + str(source_row["slot"])] = _canonical({
                                "reserved_bytes": amount, "public_sha256": sha,
                                "original_sha256": header["original_compressed_sha256"],
                            }).decode()
                            payload_hashes.add(sha)
                            observations.append(observation)
                if any(_cell_bytes(a) != _cell_bytes(b) for a, b in zip(left, decoded, strict=True)):
                    raise ValueError("Apple monthly private row/PK/rowid changed: " + table)
                _update_digest(before_digest, left)
                _update_digest(after_digest, decoded)
                count += 1
            source_tables[table] = {"rows": count, "logical_sha256": before_digest.hexdigest()}
            final_tables[table] = {"rows": count, "logical_sha256": after_digest.hexdigest()}
        additions = {_CONTRACT_KEY: apple.MONTH_BUDGET_CONTRACT, _RESERVED_KEY: str(reserved), **reservations}
        actual_additions = {key: value for key, value in new_meta.items() if key not in old_meta}
        if (any(new_meta.get(key) != value for key, value in old_meta.items())
                or (actual_additions != additions and (require_seed or actual_additions))):
            raise ValueError("Apple monthly metadata differs from computed budget-only additions")
        columns, query = _select(old, "meta")
        key_index = columns.index("key")
        original_meta_rows = {row[key_index]: row for row in old.execute(query)}
        old_digest, final_digest = hashlib.sha256(), hashlib.sha256()
        for row in original_meta_rows.values():
            _update_digest(old_digest, row)
        count = 0
        for row in new.execute(query):
            if row[key_index] in original_meta_rows and any(_cell_bytes(a) != _cell_bytes(b)
                    for a, b in zip(row, original_meta_rows[row[key_index]], strict=True)):
                raise ValueError("Apple original metadata rowid/private storage changed")
            _update_digest(final_digest, row)
            count += 1
        source_tables["meta"] = {"rows": len(original_meta_rows), "logical_sha256": old_digest.hexdigest()}
        final_tables["meta"] = {"rows": count, "logical_sha256": final_digest.hexdigest()}
        additions_digest = hashlib.sha256()
        for row in sorted(additions.items()):
            _update_digest(additions_digest, row)
        page_count = new.execute("PRAGMA page_count").fetchone()[0]
        page_cap = (maximum_bytes - reserved) // 4096
        if reserved >= maximum_bytes or page_cap < page_count:
            raise ValueError("Apple monthly total private/public budget exceeded")
        observations_sha = (_verify_observations(refs.reader, observations) if require_observations
                            else None)
        return {"job": old_meta["job"], "month": old_meta["month"], "tables": final_tables,
                "source_tables": source_tables, "payload_hashes": sorted(payload_hashes),
                "allowed_meta_additions_count": len(additions),
                "allowed_meta_additions_sha256": additions_digest.hexdigest(),
                "reserved_bytes": reserved, "page_size": 4096, "page_count": page_count,
                "page_cap": page_cap, "frame_count": frame_count,
                "observation_count": len(observations), "observations_sha256": observations_sha,
                "seeded": actual_additions == additions}


def _check_tables(actual: dict, claimed: dict) -> None:
    if not isinstance(claimed, dict) or set(actual) != set(claimed):
        raise ValueError("Apple manifest table set mismatch")
    for table, values in actual.items():
        if not isinstance(claimed[table], dict) or any(type(claimed[table].get(key)) is not type(value)
                or claimed[table].get(key) != value for key, value in values.items()):
            raise ValueError("Apple manifest logical table evidence mismatch: " + table)


def _check_intermediate(intermediate: dict, result: dict, source_sha: str) -> None:
    if (set(intermediate) != _INTERMEDIATE_KEYS or intermediate.get("contract") != MIGRATION_CONTRACT
            or intermediate.get("status") != "VERIFIED" or intermediate.get("strategy") != "golden-apple"
            or intermediate.get("source_sha256") != source_sha
            or intermediate.get("payload_hashes") != result["payload_hashes"]
            or intermediate.get("shared_payload_count") != len(result["payload_hashes"])):
        raise ValueError("Apple intermediate migration evidence mismatch")
    validate_hashes([intermediate["source_sha256"], intermediate["destination_sha256"]])
    _check_tables(result["source_tables"], intermediate["tables"])


def verify_monthly_finalization(original: Path, target: Path, references: PayloadReferences,
                                manifest: dict, *, source_snapshot_sha256: str | None = None) -> dict:
    """Read-only proof against the original, including exact observation readback.

    A caller supplying source_snapshot_sha256 MUST independently verify its
    source-file-to-SQLite-backup lineage first. It replaces only the physical
    original-file checksum; all original logical rows remain mandatory.
    """
    original, target = _offline(original), _offline(target)
    if source_snapshot_sha256 is not None:
        validate_hashes([source_snapshot_sha256])
    if original.samefile(target):
        raise ValueError("Apple source and final target must be distinct")
    if len(_canonical(manifest)) > MAX_MANIFEST_BYTES:
        raise ValueError("Apple final manifest exceeds 8 MiB")
    detail = manifest.get("apple_monthly_finalization")
    if (manifest.get("contract") != MIGRATION_CONTRACT or manifest.get("status") != "VERIFIED"
            or manifest.get("strategy") != "golden-apple" or not isinstance(detail, dict)
            or detail.get("contract") != CONTRACT):
        raise ValueError("Apple finalized migration contract missing")
    original_sha, target_sha = file_sha256(original), file_sha256(target)
    expected_original = source_snapshot_sha256 or manifest.get("source_sha256")
    if (original_sha != expected_original or target_sha != manifest.get("destination_sha256")
            or detail.get("source_sha256") != manifest.get("source_sha256")
            or detail.get("final_sha256") != target_sha
            or manifest.get("destination_bytes") != target.stat().st_size):
        raise ValueError("Apple finalization source/final checksum chain mismatch")
    if source_snapshot_sha256 is None and manifest.get("source_bytes") != original.stat().st_size:
        raise ValueError("Apple original source byte count mismatch")
    validate_hashes([detail["source_sha256"], detail["intermediate_sha256"],
                     detail["intermediate_manifest_sha256"], detail["intermediate_manifest_canonical_sha256"],
                     detail["final_sha256"]])
    intermediate = detail.get("intermediate_manifest")
    if (not isinstance(intermediate, dict) or _digest(intermediate) != detail["intermediate_manifest_canonical_sha256"]
            or intermediate.get("destination_sha256") != detail["intermediate_sha256"]
            or intermediate.get("source_bytes") != manifest.get("source_bytes")):
        raise ValueError("Apple intermediate checksum chain mismatch")
    result = _inspect(original, target, references, observer=detail["observer"],
        effective_config_hash=detail["effective_config_hash"], maximum_bytes=detail["maximum_bytes"],
        require_seed=True, require_observations=True)
    _check_intermediate(intermediate, result, manifest["source_sha256"])
    _check_tables(result["tables"], manifest.get("tables"))
    for key in ("job", "month", "allowed_meta_additions_count", "allowed_meta_additions_sha256",
                "reserved_bytes", "page_size", "page_count", "page_cap", "frame_count",
                "observation_count", "observations_sha256"):
        if type(detail.get(key)) is not type(result[key]) or detail.get(key) != result[key]:
            raise ValueError("Apple finalization computed evidence mismatch: " + key)
    if (manifest.get("payload_hashes") != result["payload_hashes"]
            or manifest.get("shared_payload_count") != len(result["payload_hashes"])):
        raise ValueError("Apple finalization payload closure mismatch")
    if file_sha256(original) != original_sha or file_sha256(target) != target_sha:
        raise ValueError("Apple monthly files changed during verification")
    _offline(original)
    _offline(target)
    return {"contract": CONTRACT, "status": "VERIFIED", **result}


def _write_manifest(path: Path, manifest: dict) -> None:
    raw = _canonical(manifest)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Apple final manifest exceeds 8 MiB")
    fd, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def _lock(path: Path):
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Apple lock path must not cross symlinks")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def finalize_monthly_migration(original: Path, stage: Path, *, references: PayloadReferences,
        intermediate_manifest_path: Path, final_manifest_path: Path, collector_lock: Path,
        observer: str, job: str, month: str, maximum_bytes: int, effective_config_hash: str,
        storage_guard=None) -> dict:
    """Seed and index only an explicit verified offline stage, with retry evidence.

    Holds the collector's native exclusive flock and an exclusive stage flock.
    No job management, source overwrite, canonical swap or payload deletion.
    """
    original, stage = _offline(original), _offline(stage)
    if storage_guard is not None:
        storage_guard()
    intermediate_path = _offline(intermediate_manifest_path)
    final_path = Path(final_manifest_path).expanduser().absolute()
    collector_lock = Path(collector_lock).expanduser().absolute()
    stage_lock = Path(str(stage) + ".finalize.lock")
    protected_paths = {original, stage, intermediate_path, collector_lock, stage_lock}
    protected_paths.update(Path(str(path) + suffix) for path in (original, stage)
                           for suffix in ("-wal", "-journal", "-shm"))
    if (original.samefile(stage) or final_path.resolve() != final_path
            or final_path in protected_paths
            or collector_lock != original.parent / ".collector.lock"):
        raise ValueError("Apple finalization requires distinct explicit stage/evidence and native collector lock")
    if references.reader is None or references.writer is None:
        raise ValueError("Apple finalization requires the public reader and durable ACK writer")
    raw_intermediate = _bounded_read(intermediate_path)
    base = json.loads(raw_intermediate)
    if base.get("destination_path") != str(stage) or base.get("source_path") != str(original):
        raise ValueError("Apple intermediate manifest does not bind explicit source/stage paths")
    intermediate = {key: base[key] for key in _INTERMEDIATE_KEYS}
    binding = {"contract": CONTRACT, "source_sha256": base["source_sha256"],
        "intermediate_sha256": base["destination_sha256"],
        "intermediate_manifest_sha256": hashlib.sha256(raw_intermediate).hexdigest(),
        "intermediate_manifest_canonical_sha256": _digest(intermediate),
        "intermediate_manifest": intermediate, "job": job, "month": month, "observer": observer,
        "effective_config_hash": effective_config_hash, "maximum_bytes": maximum_bytes}
    receipt = {**base, "status": "FINALIZING", "apple_monthly_finalization": binding,
               "scope": "Offline Apple monthly finalization; only computed budget metadata added."}
    # Reserve room for final hashes/counters before publishing any observation.
    if len(_canonical(receipt)) + 4096 > MAX_MANIFEST_BYTES:
        raise ValueError("Apple final manifest exceeds 8 MiB")
    with ExitStack() as stack:
        stack.enter_context(_lock(collector_lock))
        stack.enter_context(_lock(stage_lock))
        _offline(original)
        _offline(stage)
        if file_sha256(original) != base["source_sha256"]:
            raise ValueError("Apple original source checksum changed")
        if final_path.exists():
            if any(path.exists() and final_path.samefile(path) for path in protected_paths):
                raise ValueError("Apple final manifest aliases source/stage/intermediate/lock")
            previous_raw = _bounded_read(final_path)
            previous = json.loads(previous_raw)
            prior_binding = previous.get("apple_monthly_finalization", {})
            if any(prior_binding.get(key) != value for key, value in binding.items()):
                raise ValueError("Apple finalization retry identity changed")
            if previous.get("status") == "VERIFIED":
                verify_monthly_finalization(original, stage, references, previous)
                return previous
            if previous.get("status") not in ("FINALIZING", "FAILED"):
                raise ValueError("Apple finalization retry state is unsupported")
        elif file_sha256(stage) != base["destination_sha256"]:
            raise ValueError("Apple intermediate stage checksum mismatch")
        result = _inspect(original, stage, references, observer=observer,
            effective_config_hash=effective_config_hash, maximum_bytes=maximum_bytes,
            require_seed=False, require_observations=False)
        _check_intermediate(intermediate, result, base["source_sha256"])
        if (result["job"], result["month"]) != (job, month):
            raise ValueError("Apple supplied job/month differs from original runtime")
        _write_manifest(final_path, receipt)
        try:
            if storage_guard is not None:
                storage_guard()
            with closing(sqlite3.connect(stage, isolation_level=None, timeout=0)) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA locking_mode=EXCLUSIVE")
                db.execute("BEGIN EXCLUSIVE")
                db.execute("COMMIT")
                apple.seed_month_storage_budget(db, references, maximum_bytes)
                for slot, run_key, finished, blob in db.execute(
                        "SELECT slot,run_key,finished,frame FROM runs WHERE frame IS NOT NULL ORDER BY slot"):
                    observation = _observation(blob, slot=slot, run_key=run_key, finished=finished,
                                               month=month, job=job, observer=observer)
                    if observation is not None:
                        if storage_guard is not None:
                            storage_guard()
                        apple.publish_frame_observation(blob, references, observer=observer,
                            frame_id=observation.observation_id, observed_at=observation.observed_at, job=job)
            if storage_guard is not None:
                storage_guard()
            result = _inspect(original, stage, references, observer=observer,
                effective_config_hash=effective_config_hash, maximum_bytes=maximum_bytes,
                require_seed=True, require_observations=True)
            final_detail = {**binding, "final_sha256": file_sha256(stage), **{
                key: result[key] for key in ("allowed_meta_additions_count", "allowed_meta_additions_sha256",
                    "reserved_bytes", "page_size", "page_count", "page_cap", "frame_count",
                    "observation_count", "observations_sha256")}}
            receipt.update(status="VERIFIED", completed_at=datetime.now(timezone.utc).isoformat(),
                destination_sha256=final_detail["final_sha256"], destination_bytes=stage.stat().st_size,
                tables={name: {**base["tables"][name], **values} for name, values in result["tables"].items()},
                apple_monthly_finalization=final_detail)
            verify_monthly_finalization(original, stage, references, receipt)
            if file_sha256(intermediate_path) != binding["intermediate_manifest_sha256"]:
                raise ValueError("Apple intermediate manifest changed during finalization")
            if storage_guard is not None:
                storage_guard()
            _write_manifest(final_path, receipt)
            return receipt
        except BaseException as error:
            receipt.update(status="FAILED", error=type(error).__name__)
            _write_manifest(final_path, receipt)
            raise


def retire_monthly_sidecar(original: Path, canonical: Path, checkpoint: Path, *,
        sidecar_path: Path, review_path: Path, pinned_db: Path, pin_manifest_path: Path,
        catalog_path: Path, source_key: str, source_identity: dict,
        references: PayloadReferences, collector_lock: Path) -> dict:
    """Move only the fixed sidecar after checking actual same-host pin evidence.

    Every proof file and the public reader must be accessible here. This is not
    an authentication protocol for claims from another host. A two-host cutover
    must independently validate its local pin before its separately guarded
    remote move. The sidecar's actual location is authoritative after a crash.
    """
    from .market_data_bundle import verify_closure

    original, canonical, checkpoint, pinned_db = map(_offline, (original, canonical, checkpoint, pinned_db))
    sidecar_path, pin_manifest_path = map(_offline, (sidecar_path, pin_manifest_path))
    catalog_path = Path(catalog_path).expanduser().absolute()
    review_path = Path(review_path).expanduser().absolute()
    collector_lock = Path(collector_lock).expanduser().absolute()
    if (catalog_path.resolve(strict=True) != catalog_path or not catalog_path.is_file()
            or review_path.resolve() != review_path or not review_path.parent.is_dir()
            or review_path.exists() or review_path.is_symlink()
            or review_path.parent == canonical.parent
            or review_path.parent.stat().st_dev != sidecar_path.stat().st_dev
            or sidecar_path != Path(str(canonical) + ".storage-migration.json")
            or collector_lock != canonical.parent / ".collector.lock"):
        raise ValueError("Apple sidecar retirement requires explicit native paths and a new review location")
    files = (original, canonical, checkpoint, pinned_db, sidecar_path, pin_manifest_path, catalog_path)
    if len({(path.stat().st_dev, path.stat().st_ino) for path in files}) != len(files):
        raise ValueError("Apple retirement source/canonical/checkpoint/pin/proof files must have distinct inodes")
    identity_keys = ("source", "jenkins_job", "strategy", "runtime_job", "remote_path")
    if (set(source_identity) != set(identity_keys) or any(not isinstance(source_identity[key], str)
            or not source_identity[key] for key in identity_keys)):
        raise ValueError("Apple retirement requires the exact expected pin source identity")
    evidence_path = review_path.with_name(review_path.name + ".retirement.json")
    if evidence_path.exists() or evidence_path.is_symlink():
        raise ValueError("Apple sidecar retirement evidence already exists; inspect actual sidecar location")
    with _lock(collector_lock), _lock(Path(str(canonical) + ".finalize.lock")):
        for path in (original, canonical, checkpoint, pinned_db):
            _offline(path)
        raw = _bounded_read(sidecar_path)
        pin_raw = _bounded_read(pin_manifest_path, MAX_MANIFEST_BYTES * 4)
        manifest, pin = json.loads(raw), json.loads(pin_raw)
        result = verify_monthly_finalization(original, canonical, references, manifest)
        digest = manifest["destination_sha256"]
        file_hashes = {path: file_sha256(path) for path in files[:-1]}
        if file_hashes[checkpoint] != digest or file_hashes[pinned_db] != digest:
            raise ValueError("Apple immutable checkpoint/pin SHA differs from finalized canonical")
        if (pin.get("schema_version") != 1 or pin.get("source_key") != source_key
                or pin.get("pinned_path") != str(pinned_db) or pin.get("sha256") != digest
                or pin.get("storage_generation") != digest or pin.get("quick_check") != ["ok"]
                or pin.get("database_month") != result["month"] or pin.get("source_identity") != source_identity
                or source_identity["strategy"] != "golden-apple"
                or source_identity["runtime_job"] != result["job"]
                or source_identity["jenkins_job"] != result["job"]
                or source_identity["remote_path"] != str(canonical)):
            raise ValueError("Apple pin identity/month/final SHA evidence mismatch")
        closure = pin.get("public_payloads")
        verified_closure = verify_closure(references.reader, result["payload_hashes"])
        expected_closure = {"contract": "daily-rsync-public-closure-v1", "status": "VERIFIED",
            "source_key": source_key, "source": source_identity["source"], "strategy": "golden-apple",
            "database_sha256": digest, **verified_closure}
        if not isinstance(closure, dict) or any(closure.get(key) != value for key, value in expected_closure.items()):
            raise ValueError("Apple pinned public closure evidence mismatch")
        lineage = pin.get("storage_lineage")
        expected_lineage = {"contract": "daily-rsync-storage-migration-lineage-v1", "status": "VERIFIED",
            "source_key": source_key, "source": source_identity["source"], "strategy": "golden-apple",
            "remote_path": str(canonical), "source_file_sha256": manifest["source_sha256"],
            "generation_sha256": digest, "closure_sha256": verified_closure["closure_sha256"]}
        if (not isinstance(lineage, dict) or any(lineage.get(key) != value for key, value in expected_lineage.items())
                or lineage.get("remote_sidecar", {}).get("sidecar_path") != str(sidecar_path)
                or lineage.get("remote_sidecar", {}).get("sidecar_sha256") != file_hashes[sidecar_path]):
            raise ValueError("Apple pin storage lineage does not bind the fixed sidecar")
        with closing(sqlite3.connect(catalog_path.as_uri() + "?mode=ro", uri=True)) as catalog:
            catalog.execute("PRAGMA query_only=ON")
            catalog.execute("BEGIN")
            rows = catalog.execute("SELECT manifest_json FROM pins WHERE source_key=? AND pinned_path=?",
                                   (source_key, str(pinned_db))).fetchall()
            generation = catalog.execute("SELECT attestation_json FROM storage_generations "
                "WHERE source_key=? AND generation_sha256=?", (source_key, digest)).fetchone()
            artifact = catalog.execute("SELECT source,jenkins_job,strategy,runtime_job,remote_path,local_sha256,status "
                "FROM artifacts WHERE source_key=?", (source_key,)).fetchone()
            conflicts = catalog.execute("SELECT 1 FROM artifact_conflicts WHERE status='OPEN' "
                "AND (source_key=? OR existing_source_key=?) LIMIT 1", (source_key, source_key)).fetchone()
            if (len(rows) != 1 or json.loads(rows[0][0]) != pin or generation is None
                    or json.loads(generation[0]) != lineage or artifact is None
                    or tuple(artifact[:5]) != tuple(source_identity[key] for key in identity_keys)
                    or artifact[5] != digest or artifact[6] not in ("SYNCED", "SOURCE_MISSING") or conflicts):
                raise ValueError("Apple official catalog pin/generation/identity evidence mismatch")
            receipt = {"contract": "apple-monthly-sidecar-retirement-v1", "status": "PREPARED",
                "source_key": source_key, "source_identity": source_identity, "final_sha256": digest,
                "checkpoint_path": str(checkpoint), "pinned_path": str(pinned_db),
                "pin_manifest_sha256": file_hashes[pin_manifest_path],
                "sidecar_sha256": file_hashes[sidecar_path], "sidecar_path": str(sidecar_path),
                "review_path": str(review_path), "closure_sha256": verified_closure["closure_sha256"],
                "created_at": datetime.now(timezone.utc).isoformat()}
            _write_manifest(evidence_path, receipt)
            for path, expected_sha in file_hashes.items():
                _offline(path)
                if file_sha256(path) != expected_sha:
                    raise ValueError("Apple retirement proof changed before sidecar move")
            if review_path.exists() or review_path.is_symlink():
                raise ValueError("Apple retirement review target appeared concurrently")
            # link(O_EXCL) + unlink avoids replacing a concurrently created review
            # artifact. A crash may leave both names; matching SHA makes recovery explicit.
            os.link(sidecar_path, review_path)
            sidecar_path.unlink()
            for directory in (sidecar_path.parent, review_path.parent):
                fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            receipt.update(status="RETIRED", completed_at=datetime.now(timezone.utc).isoformat())
            _write_manifest(evidence_path, receipt)
            return receipt


def main(argv=None):
    import argparse
    from .market_data_client import StoreClient
    from .market_data_migrate import migration_storage_guard
    from .market_data_store import PayloadReader

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "stage", "intermediate-manifest", "final-manifest", "collector-lock",
                 "storage-root", "socket", "public-db"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("observer", "job", "month", "effective-config-hash"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--maximum-bytes", type=int, required=True)
    parser.add_argument("--min-free-gib", type=float, default=50)
    parser.add_argument("--max-used-ratio", type=float, default=0.90)
    args = parser.parse_args(argv)
    root = args.storage_root.expanduser().absolute()
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("Apple storage root must exist without symlinks")
    if len(root.parts) > 2 and root.parts[1] == "Volumes":
        volume = Path(*root.parts[:3])
        if not volume.is_mount() or root.stat().st_dev != volume.stat().st_dev:
            raise ValueError("Apple external storage volume is absent")
    for path in (args.source, args.stage, args.intermediate_manifest, args.final_manifest,
                 args.collector_lock, args.public_db):
        path = path.expanduser().absolute()
        if path.resolve() != path or not path.is_relative_to(root) or path.parent.stat().st_dev != root.stat().st_dev:
            raise ValueError("Apple cutover artifact escapes the explicit storage root")
    guard = migration_storage_guard(root, minimum_free_bytes=int(args.min_free_gib * (1 << 30)),
                                    maximum_used_ratio=args.max_used_ratio)
    with StoreClient(args.socket) as writer, PayloadReader(args.public_db) as reader:
        result = finalize_monthly_migration(args.source, args.stage,
            references=PayloadReferences(reader=reader, writer=writer, cache_bytes=0),
            intermediate_manifest_path=args.intermediate_manifest, final_manifest_path=args.final_manifest,
            collector_lock=args.collector_lock, observer=args.observer, job=args.job, month=args.month,
            maximum_bytes=args.maximum_bytes, effective_config_hash=args.effective_config_hash,
            storage_guard=guard)
    print(json.dumps({"status": result["status"], "destination_sha256": result["destination_sha256"],
                      "final_manifest": str(args.final_manifest)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
