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
