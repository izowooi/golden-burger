import hashlib
import sqlite3

import pytest

from polybot_observability import market_data_projection_bundle as bundle
from polybot_observability.market_data_bundle import export_bundle, import_bundle
from polybot_observability.market_data_scalar_bundle import export_scalar_bundle, import_scalar_bundle
from polybot_observability.market_data_projections import (
    MissingProjectionRecordError, PublicProjection, ProjectionReceipt,
)
from polybot_observability.market_data_store import PayloadStore


def quote(price):
    return PublicProjection('gamma-quote-v1', ('condition-a', price, None, 100.0, 0.7, 0.9, 0.2, None))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def seed(store):
    refs = store.put_public_projections([quote(0.81), quote(0.82)])
    authority = store.scalar_authority_identity()
    rows = [ProjectionReceipt('job/runtime', 'market_snapshots', i, refs[i % 2].kind, refs[i % 2].record_id)
            for i in range(5)]
    store.append_projection_receipts(rows, authority_uuid=authority)
    return authority, [ref.record_id for ref in refs], rows


def test_paged_projection_transfer_preserves_cells_ids_receipts_and_omits_existing_bodies(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, 'PAGE_SIZE', 2)
    with PayloadStore(tmp_path / 'origin.db') as source, PayloadStore(tmp_path / 'replica.db') as target:
        authority, ids, receipts = seed(source)
        cursor = None
        manifests = []
        while True:
            path = tmp_path / f'page-{len(manifests)}.db'
            manifest = bundle.export_projection_bundle(source, ids, path, authority_uuid=authority,
                                                        transferred_ids=ids if not manifests else [], receipt_after=cursor)
            result = bundle.import_projection_bundle(path, manifest, target)
            assert result['imported_record_count'] == (2 if not manifests else 0)
            manifests.append(manifest)
            cursor = manifest['next_receipt_after']
            if cursor is None:
                break
        assert len(manifests) == 3
        assert target.get_projection_records(ids, authority) == source.get_projection_records(ids, authority)
        assert target.get_projection_receipts([row.row() for row in receipts], authority_uuid=authority) == receipts
        assert target.scalar_authority_role() == 'REPLICA'
        # Replaying a page is idempotent, including the last receipt-only page.
        before = target.projection_stats()
        bundle.import_projection_bundle(tmp_path / 'page-2.db', manifests[-1], target)
        assert target.projection_stats() == before


def test_receipt_only_bundle_never_invents_missing_receiver_values(tmp_path):
    with PayloadStore(tmp_path / 'origin.db') as source, PayloadStore(tmp_path / 'replica.db') as target:
        authority, ids, _ = seed(source)
        target.import_projection_records(authority, [])
        path = tmp_path / 'omitted.db'
        manifest = bundle.export_projection_bundle(source, ids, path, authority_uuid=authority, transferred_ids=[])
        before = target.projection_stats()
        with pytest.raises(MissingProjectionRecordError):
            bundle.import_projection_bundle(path, manifest, target)
        assert target.projection_stats() == before


def test_empty_projection_handshake_binds_authority_without_fake_records(tmp_path):
    with PayloadStore(tmp_path / 'origin.db') as source, PayloadStore(tmp_path / 'replica.db') as target:
        authority = source.scalar_authority_identity()
        path = tmp_path / 'empty.db'
        manifest = bundle.export_projection_bundle(source, [], path, authority_uuid=authority)
        bundle.import_projection_bundle(path, manifest, target)
        assert target.scalar_authority_identity() == authority
        assert target.scalar_authority_role() == 'REPLICA'
        assert not any(target.projection_stats().values())


@pytest.mark.parametrize('damage', ['token', 'body', 'hash', 'count', 'receipt'])
def test_tampered_projection_bundle_is_rejected_before_any_target_write(tmp_path, damage):
    with PayloadStore(tmp_path / 'origin.db') as source, PayloadStore(tmp_path / 'replica.db') as target:
        authority, ids, _ = seed(source)
        path = tmp_path / 'transfer.db'
        manifest = bundle.export_projection_bundle(source, ids, path, authority_uuid=authority)
        if damage == 'hash':
            manifest['record_hashes'][0][2] = '0' * 64
        elif damage == 'count':
            manifest['receipt_count'] = True
        else:
            with sqlite3.connect(path) as connection:
                if damage == 'token':
                    connection.execute("INSERT INTO projection_tokens(token_id) VALUES('UNREQUESTED_SENTINEL')")
                elif damage == 'body':
                    connection.execute("INSERT INTO scalar_sources(name) VALUES('UNREQUESTED_SENTINEL')")
                else:
                    from polybot_observability.market_data_projections import PROJECTION_TRIGGER_SQL
                    connection.execute('DROP TRIGGER projection_receipts_no_update')
                    connection.execute('UPDATE projection_receipts SET original_id=original_id+100')
                    connection.execute(PROJECTION_TRIGGER_SQL['projection_receipts_no_update'])
            manifest['file_sha256'] = sha(path)
        before = target.projection_stats()
        with pytest.raises(ValueError):
            bundle.import_projection_bundle(path, manifest, target)
        assert target.projection_stats() == before
        assert target.scalar_authority_role() == 'UNCLAIMED'


@pytest.mark.parametrize('kind', ['body', 'scalar'])
def test_other_bundle_contracts_reject_undeclared_projection_dictionary(tmp_path, kind):
    with PayloadStore(tmp_path / 'origin.db') as source, PayloadStore(tmp_path / 'replica.db') as target:
        path = tmp_path / 'transfer.db'
        if kind == 'body':
            digest = source.put_many([b'public quote'])[0]
            manifest = export_bundle(source, [digest], path)
            load = import_bundle
        else:
            manifest = export_scalar_bundle(source, [], path, authority_uuid=source.scalar_authority_identity())
            load = import_scalar_bundle
        with sqlite3.connect(path) as connection:
            connection.execute("INSERT INTO projection_tokens(token_id) VALUES('UNDECLARED_SENTINEL')")
        manifest['file_sha256'] = sha(path)
        with pytest.raises(ValueError, match='undeclared projection data'):
            load(path, manifest, target)
        assert not any(target.projection_stats().values())


def test_oversize_id_selection_is_rejected_without_bundle_creation(tmp_path):
    from polybot_observability.market_data_store import StoreLimitError
    with PayloadStore(tmp_path / 'origin.db') as source:
        path = tmp_path / 'not-created.db'
        with pytest.raises(StoreLimitError):
            bundle.export_projection_bundle(source, list(range(1, 1026)), path,
                                            authority_uuid=source.scalar_authority_identity())
        assert not path.exists()
