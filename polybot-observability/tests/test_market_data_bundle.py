import sqlite3
import io
import json
from types import SimpleNamespace

import pytest

from polybot_observability.market_data_bundle import (
    export_bundle, import_bundle, missing_payloads, reference_closure, verify_closure,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import MissingPayloadError, PayloadStore


def test_incremental_bundle_is_shared_across_private_databases(tmp_path):
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        hashes = remote.put_many([b'common book', b'second book'])
        local.put_many([b'common book'])
        missing = missing_payloads(local, hashes)
        assert missing == [hashes[1]]
        manifest = export_bundle(remote, missing, tmp_path/'bundle.db')
        result = import_bundle(tmp_path/'bundle.db', manifest, local)
        assert result['imported_payload_count'] == 1
        assert local.stats()['payload_count'] == 2
        import_bundle(tmp_path/'bundle.db', manifest, local)
        assert local.stats()['payload_count'] == 2
        assert verify_closure(local, hashes)['payload_count'] == 2


def test_missing_payload_blocks_pin_and_bad_bundle_blocks_import(tmp_path):
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        hashes = remote.put_many([b'book'])
        with pytest.raises(MissingPayloadError):
            verify_closure(local, hashes)
        manifest = export_bundle(remote, hashes, tmp_path/'bundle.db')
        manifest['file_sha256'] = '0'*64
        with pytest.raises(ValueError, match='checksum'):
            import_bundle(tmp_path/'bundle.db', manifest, local)
        assert local.stats()['payload_count'] == 0


def test_trace_only_explicit_public_columns_not_private_ledger(tmp_path):
    with PayloadStore(tmp_path/'store.db') as store:
        codec = PayloadReferences(store, store)
        public, private = codec.encode_many(['public body', 'private text'])
        path = tmp_path/'strategy.db'
        c = sqlite3.connect(path)
        c.execute('CREATE TABLE event_observations(event_json TEXT,config_json TEXT)')
        c.execute('INSERT INTO event_observations VALUES(?,?)',(public,private))
        c.commit();c.close()
        closure = reference_closure(path,'golden-coconut')
        assert len(closure) == 1
        assert codec.decode_many([public]) == ['public body']
        assert store.get_many(closure) == [b'public body']


def test_cli_export_and_checksum_bound_cleanup(tmp_path, monkeypatch, capsys):
    from polybot_observability.market_data_bundle import main
    staging=tmp_path/'staging';staging.mkdir(mode=0o700)
    path=tmp_path/'store.db'
    with PayloadStore(path) as store:
        hashes=store.put_many([b'public book'])
    monkeypatch.setattr('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps({'hashes':hashes}).encode())))
    assert main(['export','--db',str(path),'--storage-root',str(tmp_path),'--staging-root',str(staging)])==0
    result=json.loads(capsys.readouterr().out)
    bundle=result['bundle_path']
    assert main(['cleanup','--bundle',bundle,'--staging-root',str(staging),'--expected-sha','0'*64])==1
    assert 'checksum' in capsys.readouterr().err
    assert main(['cleanup','--bundle',bundle,'--staging-root',str(staging),'--expected-sha',result['manifest']['file_sha256']])==0
    capsys.readouterr()
    assert list(staging.iterdir())==[]
    assert path.exists()


def test_cli_missing_volume_and_symlink_cannot_create_fallback(tmp_path, monkeypatch, capsys):
    from polybot_observability.market_data_bundle import main
    staging=tmp_path/'staging';staging.mkdir(mode=0o700)
    missing=tmp_path/'absent'
    monkeypatch.setattr('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'{"hashes":[]}')))
    assert main(['export','--db',str(missing/'public.db'),'--storage-root',str(missing),'--staging-root',str(staging)])==1
    assert not missing.exists()
    capsys.readouterr()
    linked=tmp_path/'link';linked.symlink_to(staging,target_is_directory=True)
    assert main(['export','--db',str(missing/'public.db'),'--storage-root',str(missing),'--staging-root',str(linked)])==1
    assert 'symlink' in capsys.readouterr().err


def test_bundle_cannot_smuggle_private_tables(tmp_path):
    from polybot_observability.market_data_migrate import file_sha256
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        hashes=remote.put_many([b'public'])
        path=tmp_path/'bundle.db'
        manifest=export_bundle(remote,hashes,path)
        c=sqlite3.connect(path);c.execute('CREATE TABLE private_ledger(value TEXT)');c.commit();c.close()
        manifest['file_sha256']=file_sha256(path)
        with pytest.raises(ValueError,match='unexpected tables'):
            import_bundle(path,manifest,local)
        assert local.stats()['payload_count']==0


def test_unknown_strategy_cannot_misreport_shared_database_as_inline(tmp_path):
    with PayloadStore(tmp_path/'public.db') as store:
        reference=PayloadReferences(store,store).encode_many(['public'])[0]
        database=tmp_path/'unknown.db'
        c=sqlite3.connect(database)
        c.execute('CREATE TABLE event_observations(event_json TEXT)')
        c.execute('INSERT INTO event_observations VALUES(?)',(reference,))
        c.commit();c.close()
        with pytest.raises(ValueError,match='classified strategy'):
            reference_closure(database,'')


def _receipt(sha, identity='batch-1', *, tokens=('token-a', 'token-b')):
    from polybot_observability.market_data_store import Observation
    return Observation('public-recorder', identity, '2026-09-29T12:00:00Z', 'books', sha,
                       metadata_json='{"source":"public-http"}', token_ids=tokens,
                       event_ids=('event-a',))


def test_bundle_roundtrip_preserves_only_reachable_receipts_and_batch_subjects(tmp_path):
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        wanted, unrelated = remote.put_many([b'public batch books', b'unrelated public body'])
        receipt = _receipt(wanted)
        remote.append_observations([receipt, _receipt(unrelated, 'unrelated', tokens=('other',))])
        path = tmp_path/'bundle.db'
        manifest = export_bundle(remote, [wanted], path)
        assert manifest['observation_count'] == 1
        result = import_bundle(path, manifest, local)
        assert result['imported_observation_count'] == 1
        assert list(local.iter_observations(token_id='token-b')) == [receipt]
        assert list(local.iter_observations(event_id='event-a')) == [receipt]
        assert list(local.iter_observations(token_id='other')) == []
        assert local.stats()['payload_count'] == 1
        import_bundle(path, manifest, local)
        assert local.stats()['observation_count'] == 1
        with sqlite3.connect(path) as connection:
            assert connection.execute('SELECT COUNT(*) FROM public_subject_sets').fetchone() == (1,)
            assert connection.execute('SELECT COUNT(*) FROM public_subject_set_members').fetchone() == (3,)


def test_metadata_only_bundle_refreshes_observations_for_already_local_body(tmp_path):
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        sha = remote.put_many([b'already present'])[0]
        local.put_many([b'already present'])
        first = _receipt(sha)
        remote.append_observations([first])
        path = tmp_path/'index-one.db'
        manifest = export_bundle(remote, [sha], path, payload_hashes=[])
        assert manifest['payload_count'] == manifest['raw_bytes'] == 0
        assert manifest['reference_hashes'] == [sha]
        import_bundle(path, manifest, local)
        second = _receipt(sha, 'batch-2')
        remote.append_observations([second])
        path = tmp_path/'index-two.db'
        manifest = export_bundle(remote, [sha], path, payload_hashes=[])
        import_bundle(path, manifest, local)
        assert list(local.iter_observations(token_id='token-b')) == [first, second]
        assert local.stats()['payload_count'] == 1
        with PayloadStore(tmp_path/'empty.db') as empty:
            with pytest.raises(MissingPayloadError):
                import_bundle(path, manifest, empty)
            assert empty.stats()['observation_count'] == 0


@pytest.mark.parametrize('tamper', ['subject', 'index', 'view', 'column', 'orphan', 'outside'])
def test_bundle_rejects_changed_index_or_unknown_layout_before_import(tmp_path, tamper):
    from polybot_observability.market_data_migrate import file_sha256
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        sha = remote.put_many([b'public body'])[0]
        remote.append_observations([_receipt(sha)])
        path = tmp_path/'bundle.db'
        manifest = export_bundle(remote, [sha], path)
        with sqlite3.connect(path) as connection:
            if tamper == 'subject':
                connection.execute("UPDATE public_subject_set_members SET subject_id='changed' WHERE subject_kind='event'")
            elif tamper == 'index':
                connection.execute('DROP INDEX public_subject_lookup')
                connection.execute('CREATE INDEX public_subject_lookup ON public_subject_set_members(set_sha)')
            elif tamper == 'view':
                connection.execute('CREATE VIEW private_view AS SELECT body FROM payloads')
            elif tamper == 'column':
                connection.execute('ALTER TABLE observations ADD COLUMN private_order TEXT')
            elif tamper == 'orphan':
                connection.execute('INSERT INTO public_subject_sets VALUES(?)', ('0'*64,))
            else:
                connection.execute('DROP TRIGGER observations_no_update')
                connection.execute('UPDATE observations SET payload_sha=?', ('0'*64,))
        manifest['file_sha256'] = file_sha256(path)
        with pytest.raises(ValueError):
            import_bundle(path, manifest, local)
        assert local.stats()['payload_count'] == 0
        assert local.stats()['observation_count'] == 0


def test_bundle_rejects_observation_manifest_tamper_even_with_valid_file_hash(tmp_path):
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        sha = remote.put_many([b'public body'])[0]
        remote.append_observations([_receipt(sha)])
        path = tmp_path/'bundle.db'
        manifest = export_bundle(remote, [sha], path)
        manifest['observation_sha256'] = '0'*64
        with pytest.raises(ValueError, match='index checksum'):
            import_bundle(path, manifest, local)
        assert local.stats()['payload_count'] == 0


def test_legacy_body_only_bundle_and_reader_without_subject_tables_remain_supported(tmp_path):
    from polybot_observability.market_data_migrate import file_sha256
    from polybot_observability.market_data_store import PayloadReader
    path = tmp_path/'legacy.db'
    with PayloadStore(path) as store:
        hashes = store.put_many([b'legacy public body'])
        stats = store.stats()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE observation_subject_sets')
        connection.execute('DROP TABLE public_subject_set_members')
        connection.execute('DROP TABLE public_subject_sets')
        connection.execute('DROP INDEX observations_owner_kind_time')
        connection.execute('DROP INDEX observations_payload_sha')
    from polybot_observability.market_data_bundle import closure_digest
    manifest = {'contract': 'public-payload-bundle-v1', 'status': 'VERIFIED',
                'file_sha256': file_sha256(path), 'payload_hashes': hashes,
                'closure_sha256': closure_digest(hashes), **stats}
    with PayloadStore(tmp_path/'local.db') as local:
        import_bundle(path, manifest, local)
        assert local.get_many(hashes) == [b'legacy public body']
    with PayloadReader(path) as reader:
        current = export_bundle(reader, hashes, tmp_path/'upgraded-bundle.db')
    assert current['contract'] == 'public-payload-bundle-v2'
    assert current['observation_count'] == 0


def test_bundle_observation_transfer_is_bounded(tmp_path, monkeypatch):
    from polybot_observability.market_data_bundle import BundleLimitError
    with PayloadStore(tmp_path/'remote.db') as remote:
        sha = remote.put_many([b'public body'])[0]
        remote.append_observations([_receipt(sha)])
        monkeypatch.setattr('polybot_observability.market_data_bundle.MAX_INDEX_BYTES', 1)
        with pytest.raises(BundleLimitError):
            export_bundle(remote, [sha], tmp_path/'oversized.db')


def test_apple_legacy_pool_closure_requires_exclusively_public_roles(tmp_path):
    import hashlib
    with PayloadStore(tmp_path/'store.db') as store:
        reference = PayloadReferences(store, store).encode_many([b'public packed bytes'])[0]
        sha = hashlib.sha256(b'original unpacked bytes').hexdigest()
        path = tmp_path/'apple.db'
        with sqlite3.connect(path) as connection:
            connection.executescript('''
                CREATE TABLE payloads(hash TEXT PRIMARY KEY,encoding TEXT,bytes INTEGER,compressed BLOB);
                CREATE TABLE events(payload_hash TEXT REFERENCES payloads(hash));
                CREATE TABLE book_observations(metrics_hash TEXT REFERENCES payloads(hash));
            ''')
            connection.execute('INSERT INTO payloads VALUES(?,?,?,?)', (sha, 'zlib', 20, reference))
            connection.execute('INSERT INTO events VALUES(?)', (sha,))
        assert reference_closure(path, 'golden-apple') == [hashlib.sha256(b'public packed bytes').hexdigest()]
        with sqlite3.connect(path) as connection:
            connection.execute('INSERT INTO book_observations VALUES(?)', (sha,))
        with pytest.raises(ValueError, match='exclusively public'):
            reference_closure(path, 'golden-apple')


def test_one_payload_many_observations_pages_without_repeating_bodies(tmp_path, monkeypatch):
    monkeypatch.setattr('polybot_observability.market_data_bundle.MAX_BUNDLE_OBSERVATIONS', 1)
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        sha = remote.put_many([b'common empty book'])[0]
        receipts = [_receipt(sha, 'receipt-' + str(i)) for i in range(3)]
        remote.append_observations(receipts)
        after = None
        manifests = []
        for page in range(3):
            path = tmp_path/f'page-{page}.db'
            manifest = export_bundle(remote, [sha], path, payload_hashes=[sha] if page == 0 else [],
                                     observation_after=after)
            assert manifest['observation_after'] == after
            assert manifest['observation_count'] == 1
            import_bundle(path, manifest, local)
            after = manifest['next_observation_after']
            manifests.append(manifest)
        assert after is None
        assert [m['payload_count'] for m in manifests] == [1, 0, 0]
        assert list(local.iter_observations(token_id='token-b')) == receipts


def test_bundle_continuation_cannot_skip_or_repeat_its_actual_page(tmp_path, monkeypatch):
    monkeypatch.setattr('polybot_observability.market_data_bundle.MAX_BUNDLE_OBSERVATIONS', 1)
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        sha = remote.put_many([b'public'])[0]
        remote.append_observations([_receipt(sha, 'one'), _receipt(sha, 'two')])
        path = tmp_path/'page.db'
        manifest = export_bundle(remote, [sha], path)
        manifest['next_observation_after'][-1] = 'unseen-key'
        with pytest.raises(ValueError, match='continuation'):
            import_bundle(path, manifest, local)
        assert local.stats()['payload_count'] == 0


def test_live_page_backfill_is_included_on_next_metadata_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr('polybot_observability.market_data_bundle.MAX_BUNDLE_OBSERVATIONS', 1)
    with PayloadStore(tmp_path/'remote.db') as remote, PayloadStore(tmp_path/'local.db') as local:
        sha = remote.put_many([b'common book'])[0]
        remote.append_observations([_receipt(sha, 'b'), _receipt(sha, 'c')])
        first = export_bundle(remote, [sha], tmp_path/'first.db')
        import_bundle(tmp_path/'first.db', first, local)
        remote.append_observations([_receipt(sha, 'a')])
        last = export_bundle(remote, [sha], tmp_path/'last.db', payload_hashes=[],
                             observation_after=first['next_observation_after'])
        import_bundle(tmp_path/'last.db', last, local)
        assert last['next_observation_after'] is None
        assert [row.observation_id for row in local.iter_observations()] == ['b', 'c']
        after = None
        for page in range(3):
            path = tmp_path/f'refresh-{page}.db'
            current = export_bundle(remote, [sha], path, payload_hashes=[], observation_after=after)
            import_bundle(path, current, local)
            after = current['next_observation_after']
        assert after is None
        assert [row.observation_id for row in local.iter_observations()] == ['a', 'b', 'c']
