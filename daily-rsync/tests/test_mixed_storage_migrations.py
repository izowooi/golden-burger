import base64
import hashlib
import json
import sqlite3
import struct
import zlib
from pathlib import Path

import pytest
from polybot_observability import market_data_mixed as mixed
from polybot_observability.market_data_migrate import migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from test_public_payloads import LocalRemote

from daily_rsync import remote_agent
from daily_rsync.models import RemoteArtifact
from daily_rsync.public_payloads import (
    synchronize_database_closure,
    verify_storage_migration,
    write_closure_descriptor,
)
from daily_rsync.sync import sha256


@pytest.mark.parametrize("existing_mixed", [False, True])
@pytest.mark.parametrize("tamper", [
    None, "generic_marker", "profile", "private_span", "unchanged_private_span",
])
def test_daily_migration_accepts_only_verified_mixed_ownership(
    app_config, tmp_path, existing_mixed, tamper,
):
    remote = LocalRemote(tmp_path / "remote-mixed", [])
    original = app_config.data_root / "original.db"
    target = app_config.incoming_root / "target.db"
    source = '{ "market_id":"public-market", "decision":"PRIVATE_DECISION" }'
    with sqlite3.connect(original) as connection:
        connection.executescript("""
            CREATE TABLE market_metadata_versions(id INTEGER PRIMARY KEY, metadata_json TEXT);
            CREATE TABLE private_account(id INTEGER PRIMARY KEY, document TEXT);
            INSERT INTO private_account VALUES (1,'PRIVATE_ACCOUNT');
        """)
        connection.execute("INSERT INTO market_metadata_versions VALUES (7,?)", (source,))
    with PayloadStore(remote.public_db) as store:
        refs = PayloadReferences(reader=store, writer=store)
        if existing_mixed:
            row = {"metadata_json": source}
            encoded = mixed.externalize_mixed_row(
                "golden-pomegranate", "market_metadata_versions", row, row, refs,
            )["metadata_json"]
            with sqlite3.connect(original) as connection:
                connection.execute(
                    "UPDATE market_metadata_versions SET metadata_json=?", (encoded,),
                )
        manifest = migrate_public_bodies(
            original, target, strategy="golden-pomegranate", source_sha256=sha256(original),
            references=refs,
        )
        with sqlite3.connect(target) as connection:
            encoded = connection.execute(
                "SELECT metadata_json FROM market_metadata_versions"
            ).fetchone()[0]
            if tamper == "generic_marker":
                encoded = refs.encode_many([source])[0]
            elif tamper in {"profile", "private_span", "unchanged_private_span"}:
                packet = base64.b64decode(encoded[len(mixed.PREFIX):])
                fields = list(mixed.HEADER.unpack_from(packet))
                private = packet[mixed.HEADER.size:-32]
                if tamper == "profile":
                    fields[0] = 6
                else:
                    # This forged packet restores exactly the original wrapper,
                    # but its single shared fragment contains private content.
                    public = mixed._parts([source.encode()])
                    fields[2:4] = [0, 1]
                    template = (
                        mixed._chunk(b"") + mixed._chunk(b"") + struct.pack("!I", 1)
                        + mixed._chunk(b"") + struct.pack("!I", 0) + mixed._chunk(b"")
                    )
                    fields[6:9] = [len(template), len(public), 1]
                    fields[10] = hashlib.sha256(public).digest()
                    refs.encode_many([public])
                    private = zlib.compress(template, 9)
                body = mixed.HEADER.pack(*fields) + private
                body += hashlib.sha256(mixed.PREFIX.encode() + body).digest()
                encoded = mixed.PREFIX + base64.b64encode(body).decode()
                assert mixed.resolve_mixed_payload(encoded, refs) == source
            if tamper:
                connection.execute(
                    "UPDATE market_metadata_versions SET metadata_json=?", (encoded,),
                )
        if tamper == "unchanged_private_span":
            with sqlite3.connect(original) as connection:
                connection.execute(
                    "UPDATE market_metadata_versions SET metadata_json=?", (encoded,),
                )
            manifest["source_sha256"] = sha256(original)
    manifest["destination_sha256"] = sha256(target)
    sidecar = Path(str(target) + ".storage-migration.json")
    sidecar.write_text(json.dumps(manifest))
    proposal = remote_agent.storage_migration_record(target)
    artifact = RemoteArtifact(
        kind="database_research_archive", remote_path=str(target),
        size_bytes=target.stat().st_size, mtime_ns=target.stat().st_mtime_ns,
        source=app_config.ssh_host, jenkins_job="polybot-fixture", strategy="golden-pomegranate",
        runtime_job="pomegranate-fixture", data_contract="research-full-v1",
        database_utc_date="2026-08-05", archive_date="2026-08-05", mode="sim",
        storage_migration=proposal,
    )
    for database in (original, target):
        if database == target and tamper in {"generic_marker", "profile"}:
            requests_before = len(remote.requests)
            with pytest.raises(ValueError, match="private cell|ownership profile"):
                synchronize_database_closure(
                    app_config, remote, database, strategy=artifact.strategy,
                    source_key=artifact.source_key, database_sha256=sha256(database),
                    ensure_capacity=lambda *_: None,
                )
            assert len(remote.requests) == requests_before
            return  # The policy proof now rejects this source before public transfer.
        closure = synchronize_database_closure(
            app_config, remote, database, strategy=artifact.strategy,
            source_key=artifact.source_key, database_sha256=sha256(database),
            ensure_capacity=lambda *_: None,
        )
        write_closure_descriptor(app_config, database, closure)
    existing = {"local_path": str(original), "local_sha256": sha256(original),
                "metadata_json": '{"completed_at":"2026-08-06T00:00:00Z"}'}
    snapshot = {"storage_migration": proposal, "sha256": sha256(target)}
    before = original.read_bytes()
    if tamper:
        with pytest.raises((RuntimeError, mixed.MixedPayloadError), match="private|ownership"):
            verify_storage_migration(
                app_config, original, target, artifact=artifact, existing=existing,
                snapshot=snapshot, closure=closure,
            )
    else:
        result = verify_storage_migration(
            app_config, original, target, artifact=artifact, existing=existing,
            snapshot=snapshot, closure=closure,
        )
        assert result["status"] == "VERIFIED"
        assert result["tables"]["market_metadata_versions"]["rows"] == 1
    assert original.read_bytes() == before
