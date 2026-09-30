"""Offline native v4 fixtures prove source, private cells, rowids and replay."""
import sqlite3

import pytest

from polybot_observability.market_data_levels import iter_level_logical_rows
from polybot_observability.market_data_migrate import file_sha256, migrate_public_bodies
from polybot_observability.market_data_raw_links import (
    initialize_raw_links, insert_raw_rows, iter_raw_logical_rows,
    validate_raw_source_schema,
)
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import POMEGRANATE_PROFILE_ID
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadStore
from test_shared_raw_source import bind, producer


STRATEGY='golden-pomegranate'
NAMESPACE=scalar_namespace('fixture-source','pomegranate-parent',STRATEGY,'integration')


def offline(path):
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
    db.close()


def test_native_v4_migration_exact_rows_levels_schema_private_and_repeat(tmp_path, monkeypatch):
    bind(monkeypatch,None,raw=False)
    repository,_,_=producer(tmp_path)
    source=repository.db_path
    with sqlite3.connect(source) as db:
        snapshot=db.execute('SELECT snapshot_id FROM orderbook_snapshots LIMIT 1').fetchone()[0]
        for rowid in (-7,0,99):
            db.execute('INSERT INTO orderbook_levels(rowid,level_id,snapshot_id,side,level_index,price_raw,price,size_raw,size) VALUES(?,?,?,?,?,?,?,?,?)',
                (rowid,None if rowid<=0 else 'sparse-'+str(rowid),snapshot,'ASK',rowid+100,'0.5100',.51,'3e1',30.))
    db.close()
    offline(source)
    source_sha=file_sha256(source)
    with PayloadStore(tmp_path/'shared.db') as store:
        refs=PayloadReferences(store,store)
        target=tmp_path/'converted.db'
        manifest=migrate_raw_database(source,target,source_sha256=source_sha,namespace=NAMESPACE,
            references=refs,profile_id=POMEGRANATE_PROFILE_ID,batch_rows=2)
        assert manifest['status']=='VERIFIED'
        assert manifest['tables']['orderbook_levels']['implicit_rowid_preserved']
        assert manifest['tables']['orderbook_levels']['externalized_level_rows']==11
        assert file_sha256(source)==source_sha
        assert manifest['private_storage']['orderbook_levels']['rows']==11
        assert manifest['private_storage']['orderbook_levels']['source_sha256']==manifest['private_storage']['orderbook_levels']['target_sha256']
        with connect(source,references=refs) as before, connect(target,references=refs) as after:
            old=list(iter_level_logical_rows(before,STRATEGY,'orderbook_levels',references=refs,require_rowid=True))
            new=list(iter_level_logical_rows(after,STRATEGY,'orderbook_levels',references=refs,require_rowid=True))
            assert old==new
            assert [row['__rowid__'] for row in new][:2]==[-7,0]
            assert after.execute('SELECT COUNT(*) FROM main.orderbook_levels').fetchone()[0]==0
            assert before.execute('SELECT m.question,o.price_raw FROM market_observations m JOIN outcome_observations o USING(observation_id) ORDER BY m.question,o.outcome_index').fetchall()==after.execute('SELECT m.question,o.price_raw FROM market_observations m JOIN outcome_observations o USING(observation_id) ORDER BY m.question,o.outcome_index').fetchall()
        # Existing exact-rowid ladder bindings and body/mixed links also migrate.
        repeat=migrate_raw_database(target,tmp_path/'repeated.db',source_sha256=file_sha256(target),
            namespace=NAMESPACE,references=refs,profile_id=POMEGRANATE_PROFILE_ID,batch_rows=3)
        assert repeat['status']=='VERIFIED'
        assert repeat['tables']==manifest['tables']


@pytest.mark.parametrize('version',[1,2,3,5])
def test_contract_row_authority_rejects_unreviewed_version(tmp_path,monkeypatch,version):
    bind(monkeypatch,None,raw=False)
    repository,_,_=producer(tmp_path)
    with sqlite3.connect(repository.db_path) as db:
        db.execute('DROP TRIGGER collection_contracts_append_only_update')
        db.execute('UPDATE collection_contracts SET schema_version=?',(version,))
        with pytest.raises(ValueError,match='table-row authority'):
            validate_raw_source_schema(db,profile_id=POMEGRANATE_PROFILE_ID)


def test_ignore_preserves_sqlite_check_notnull_unique_and_fk_semantics(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'shared.db') as store:
        refs=bind(monkeypatch,store,store)
        repository,_,_=producer(tmp_path)
        with repository._connect() as db:
            original=next(iter_raw_logical_rows(db,'trade_observations',references=refs))
            original.pop('__rowid__')
            changed={**original,'price_raw':'0.410000','first_received_at':'later'}
            invalid={**original,'trade_id':'invalid-not-null','economic_row_hash':None}
            fresh={**original,'trade_id':'new-first','economic_row_hash':'other','first_received_at':'first'}
            db.execute('BEGIN IMMEDIATE')
            insert_raw_rows(db,STRATEGY,'trade_observations',[changed,invalid,fresh,{**fresh,'first_received_at':'second'}],
                references=refs,namespace=NAMESPACE,or_ignore=True)
            db.commit()
            rows=list(iter_raw_logical_rows(db,'trade_observations',references=refs))
            assert len(rows)==2
            assert rows[-1]['first_received_at']=='first'
            assert rows[0]['price_raw']==original['price_raw']
            outcome=next(iter_raw_logical_rows(db,'outcome_observations',references=refs))
            outcome.pop('__rowid__');outcome.update(outcome_observation_id='bad-fk',observation_id='absent')
            db.execute('BEGIN IMMEDIATE')
            with pytest.raises(sqlite3.IntegrityError,match='FOREIGN KEY'):
                insert_raw_rows(db,STRATEGY,'outcome_observations',[outcome],references=refs,namespace=NAMESPACE,or_ignore=True)
            db.rollback()


def test_legacy_level_bindings_without_original_rowids_cannot_claim_exact_migration(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'shared.db') as store:
        refs=bind(monkeypatch,store,store,raw=False)
        repository,_,_=producer(tmp_path)
        offline(repository.db_path)
        target=tmp_path/'rejected.db'
        before=store.projection_stats()
        with pytest.raises(ValueError,match='original level rowid is unknown'):
            migrate_raw_database(repository.db_path,target,source_sha256=file_sha256(repository.db_path),
                namespace=NAMESPACE,references=refs,profile_id=POMEGRANATE_PROFILE_ID)
        assert not target.exists()
        assert store.projection_stats()==before


@pytest.mark.parametrize("layout", ["shared_bodies", "raw_derivative", "native_raw"])
def test_both_native_analyzers_replay_source_json_group_token_books_and_label_cutoff(tmp_path,monkeypatch,layout):
    from datetime import datetime,timezone
    from uuid import UUID
    from test_late_underdog_analysis import analysis as underdog
    from test_sports_favorite_grid import analysis as favorite
    import test_shared_raw_source as native
    original_market=native._market
    def sports_market(number):
        return {**original_market(number),'eventId':f'event-{number}',
            'active':True,'closed':False,'enableOrderBook':True,'acceptingOrders':True,
            'tags':[{'slug':'sports'}],'endDate':'2026-08-07T00:00:00Z',
            'bestBid':.40,'bestAsk':.42,'spread':.02,'feesEnabled':True}
    monkeypatch.setattr(native,'_market',sports_market)
    monkeypatch.setattr('polybot.utils.retry.utc_now',lambda:native.NOW.isoformat())
    monkeypatch.setattr('polybot.run_audit.uuid4',iter(UUID(int=i) for i in range(1,10)).__next__)
    bind(monkeypatch,None,raw=False)
    repository,_,_=producer(tmp_path)
    with sqlite3.connect(repository.db_path) as db:
        columns=db.execute('PRAGMA table_info(resolution_observations)').fetchall()
        row={name:None if not required else 0 if affinity in ('INTEGER','REAL') else 'fixture'
             for _,name,affinity,required,*_ in columns}
        row.update(resolution_observation_id='label-before-cutoff',run_id=str(UUID(int=1)),cycle_number=1,
            requested_at='2026-08-06T01:00:00Z',observed_at='2026-08-06T01:00:00Z',
            condition_id='condition-1',lookup_status='OBSERVED',closed=1,one_hot=1,one_hot_outcome_index=1,
            outcome_prices_json='["0","1"]',raw_market_json='{"closed":true}')
        names=tuple(row)
        db.execute('INSERT INTO resolution_observations('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row.values()))
        label_rows=[dict(row)]
        row.update(resolution_observation_id='label-after-cutoff',observed_at='2026-08-08T00:00:00Z',one_hot_outcome_index=0)
        label_rows.append(dict(row))
        db.execute('INSERT INTO resolution_observations('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row.values()))
    db.close();offline(repository.db_path)
    with PayloadStore(tmp_path/'shared.db') as store:
        refs=PayloadReferences(store,store)
        target=tmp_path/'analyzer.db'
        if layout == 'shared_bodies':
            migrate_public_bodies(repository.db_path,target,strategy=STRATEGY,
                source_sha256=file_sha256(repository.db_path),references=refs,include_levels=True)
        elif layout == 'raw_derivative':
            migrate_raw_database(repository.db_path,target,source_sha256=file_sha256(repository.db_path),
                namespace=NAMESPACE,references=refs,profile_id=POMEGRANATE_PROFILE_ID)
        else:
            bind(monkeypatch,store,store)
            monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','native-fixture-source')
            monkeypatch.setattr('polybot.run_audit.uuid4',iter(UUID(int=i) for i in range(1,10)).__next__)
            (tmp_path/'native').mkdir()
            native_repository,_,_=producer(tmp_path/'native')
            with native_repository._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                native_repository._insert_many(db,'resolution_observations',names,label_rows)
                db.commit()
            target=native_repository.db_path
        bind(monkeypatch,store,raw=False)
        for analysis in (underdog,favorite):
            before=list(analysis.iter_observations(repository.db_path))
            assert len(before)==2
            assert before==list(analysis.iter_observations(target))
            books=analysis.load_exact_books([repository.db_path])
            assert len(books)==4
            assert books==analysis.load_exact_books([target])
            cutoff=datetime(2026,8,7,tzinfo=timezone.utc)
            labels=analysis.load_labels([repository.db_path],cutoff)
            assert labels[0]
            assert labels==analysis.load_labels([target],cutoff)


@pytest.mark.parametrize("analyzer", ["underdog", "favorite"])
def test_missing_shared_tag_payload_fails_closed_in_sql_filter(tmp_path,monkeypatch,analyzer):
    from test_late_underdog_analysis import analysis as underdog
    from test_sports_favorite_grid import analysis as favorite
    from polybot_observability.market_data_refs import parse_reference
    from polybot_observability.market_data_store import PayloadReader
    import test_shared_raw_source as native
    original_market=native._market
    monkeypatch.setattr(native,'_market',lambda number:{**original_market(number),
        'eventId':f'event-{number}','active':True,'closed':False,
        'enableOrderBook':True,'acceptingOrders':True,'tags':[{'slug':'sports'}],
        'endDate':'2026-08-07T00:00:00Z','bestBid':.40,'bestAsk':.42,'spread':.02})
    with PayloadStore(tmp_path/'shared.db') as store:
        bind(monkeypatch,store,store)
        repository,_,_=producer(tmp_path)
        with sqlite3.connect(repository.db_path) as db:
            reference=db.execute('SELECT tags_json FROM market_observations LIMIT 1').fetchone()[0]
        db.close()
        _,tag_hash=parse_reference(reference)
        with PayloadReader(tmp_path/'shared.db') as reader:
            get_many=reader.get_many
            def missing_tag(hashes):
                if tag_hash in hashes:
                    raise KeyError('fixture missing public tag payload')
                return get_many(hashes)
            monkeypatch.setattr(reader,'get_many',missing_tag)
            bind(monkeypatch,reader,raw=False)
            with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
                list((underdog if analyzer=='underdog' else favorite).iter_observations(repository.db_path))
