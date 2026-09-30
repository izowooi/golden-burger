"""Golden Black RAW parent skeletons keep child FKs and source query results."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from polybot.collector import Collector
from polybot.config import load_config
from polybot.db.repository import ResearchRepository
from polybot_observability import market_data_refs, market_data_sqlite, market_data_index, market_data_levels
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadStore
from polybot_observability.market_data_raw_profiles import BLACK_PROFILE_ID
from polybot_observability.market_data_raw_links import (
    RAW_TABLES, raw_layout_metadata, validate_raw_layout, initialize_raw_links,
    iter_raw_logical_rows, insert_raw_rows, verify_raw_dependencies,
)
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_scalar_links import scalar_namespace
from test_collector import ROOT, FakeGamma, FakeClob

NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def codec(monkeypatch, store):
    refs = PayloadReferences(store, store)
    from polybot_observability import market_data_raw_links
    from polybot.db import repository as repository_module
    for module in (market_data_refs, market_data_sqlite, market_data_index, market_data_levels, market_data_raw_links, repository_module):
        monkeypatch.setattr(module, 'configured_references', lambda: refs)
    return refs


def repository(tmp_path, monkeypatch, *, shared=None, raw=False):
    config = load_config(ROOT/'config.yaml')
    config = replace(config, db_path=tmp_path/'data'/config.job_name/'trades_sim.db')
    monkeypatch.setenv('PUBLIC_MARKET_DATA_SOURCE','fixture-source')
    monkeypatch.setenv('JOB_NAME','fixture-job')
    monkeypatch.setenv('PUBLIC_MARKET_DATA_RAW', '1' if raw else '0')
    if shared is not None: codec(monkeypatch, shared)
    repo = ResearchRepository(config.db_path, busy_timeout_ms=1000, data_contract=config.trading.data_contract,
                              raw_profile_id=BLACK_PROFILE_ID if raw else None)
    with repo.connect() as c:
        c.execute('INSERT INTO research_config_versions VALUES(?,?,?,?,?,?,?)',
                  ('config','source','prereg',config.job_name,'sim','{}','2026-08-21T00:00:00Z'))
        c.execute('INSERT INTO research_run_events VALUES(?,?,?,?,?,?,?)',
                  ('start','run-1','STARTED','2026-08-21T00:00:00Z','config','source','{}'))
    return config, repo


def collected(config, repo):
    Collector(config, repo, FakeGamma(), FakeClob()).collect('run-1', now=NOW)


def source_rows(repo):
    with repo.connect() as c:
        return {table:list(iter_raw_logical_rows(c, table)) for table in RAW_TABLES}


def test_fresh_raw_runtime_keeps_real_parents_and_source_columns_public(tmp_path, monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config, repo = repository(tmp_path, monkeypatch, shared=store, raw=True)
        collected(config, repo)
        with repo.connect() as c:
            validate_raw_layout(c); verify_raw_dependencies(c)
            assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
            assert c.execute('SELECT count(*) FROM outcome_observations o JOIN market_observations m ON m.observation_id=o.market_observation_id').fetchone()[0] == 2
            assert c.execute('SELECT SUM(l.price*l.size) FROM orderbook_levels l JOIN orderbook_snapshots s USING(snapshot_id)').fetchone()[0] > 0
            assert tuple(c.execute('SELECT question,gamma_probability FROM market_observations JOIN outcome_observations USING(condition_id) ORDER BY outcome_index').fetchone()) == ('Will A win?', .94)
            assert json.loads(c.execute('SELECT normalized_json FROM market_observations').fetchone()[0])['fee_rate'] == .05
            for table in RAW_TABLES:
                with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                    c.execute('DELETE FROM '+table)
                with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                    c.execute('DELETE FROM main.'+table)
        with sqlite3.connect(repo.path) as physical:
            assert physical.execute('SELECT count(*) FROM market_observations').fetchone()[0] == 1
            names = {r[1] for r in physical.execute('PRAGMA table_info(market_observations)')}
            assert {'question','event_title','liquidity','fee_schedule_json','outcome_prices_json'}.isdisjoint(names)
            assert {'observation_id','sweep_id','condition_id','eligible','fee_rate','_public_record_id'} <= names
            names = {r[1] for r in physical.execute('PRAGMA table_info(orderbook_snapshots)')}
            assert {'best_bid','best_ask','source_timestamp','tick_size'}.isdisjoint(names)


def test_populated_source_requires_explicit_offline_migration(tmp_path, monkeypatch):
    config, repo = repository(tmp_path, monkeypatch)
    collected(config, repo)
    with PayloadStore(tmp_path/'public.db') as store:
        refs = codec(monkeypatch, store)
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            with pytest.raises(ValueError, match='offline derivative'):
                initialize_raw_links(c, scalar_namespace('fixture-source','fixture-job','golden-black',config.job_name), store.scalar_authority_identity(), references=refs)
            assert raw_layout_metadata(c) is None


def test_derivative_migration_preserves_all_rows_fk_and_file_measurement(tmp_path, monkeypatch):
    config, repo = repository(tmp_path, monkeypatch)
    collected(config, repo)
    before = source_rows(repo)
    source_sha = file_sha256(repo.path)
    target = tmp_path/'derivative.db'
    with PayloadStore(tmp_path/'public.db') as store:
        refs = codec(monkeypatch, store)
        result = migrate_raw_database(repo.path,target, source_sha256=source_sha,
            namespace=scalar_namespace('fixture-source','fixture-job','golden-black',config.job_name),references=refs)
        assert result['status']=='VERIFIED' and file_sha256(repo.path)==source_sha
        with market_data_sqlite.connect(target,references=refs) as c:
            assert {t:list(iter_raw_logical_rows(c,t,references=refs)) for t in RAW_TABLES}==before
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
            verify_raw_dependencies(c,references=refs)
        store._connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        measurement={'inline_bytes':repo.path.stat().st_size,'private_bytes':target.stat().st_size,'public_bytes':(tmp_path/'public.db').stat().st_size}
        (tmp_path/'measured-raw-pilot.json').write_text(json.dumps(measurement))
        print('RAW_PILOT_BYTES', json.dumps(measurement,sort_keys=True))


@pytest.mark.parametrize('failure', ['unique','primary_key','not_null','check','foreign_key'])
def test_source_constraints_reject_whole_batch_before_public_ack(tmp_path,monkeypatch,failure):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True)
        collected(config,repo)
        rows=source_rows(repo)
        table='outcome_observations' if failure=='foreign_key' else 'market_observations' if failure in ('unique','check') else 'orderbook_snapshots'
        row=dict(rows[table][0]); row.pop('__rowid__')
        pk=RAW_TABLES[table].primary_key
        if failure!='primary_key': row[pk]='new-key'
        if failure=='not_null': row['request_id']=None;row['run_id']='new-run'
        if failure=='check': row['eligible']=2;row['condition_id']='new-condition'
        if failure=='foreign_key': row['market_observation_id']='absent-parent';row['token_id']='new-token'
        calls=[]
        monkeypatch.setattr(store,'put_public_projections',lambda groups: calls.append(groups))
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            before=c.execute('SELECT count(*) FROM main.'+table).fetchone()[0]
            with pytest.raises(sqlite3.IntegrityError): insert_raw_rows(c,'golden-black',table,[row])
            assert c.execute('SELECT count(*) FROM main.'+table).fetchone()[0]==before
            assert c.in_transaction and calls==[]


@pytest.mark.parametrize('phase',['body','receipt'])
def test_failed_public_ack_rolls_back_local_row_and_keeps_caller_transaction(tmp_path,monkeypatch,phase):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True)
        collected(config,repo)
        row=dict(source_rows(repo)['orderbook_snapshots'][0]);row.pop('__rowid__')
        row.update(snapshot_id='new-snapshot',run_id='new-run',best_bid=.11)
        def fail(*args,**kwargs): raise OSError('injected '+phase+' ACK failure')
        monkeypatch.setattr(store,'put_public_projections' if phase=='body' else 'append_projection_receipts',fail)
        with repo.connect() as c:
            c.execute('INSERT INTO data_quality_issues VALUES(?,?,?,?,?,?)',('prior','run-1','time','LOW','test','{}'))
            with pytest.raises(OSError,match='ACK failure'): insert_raw_rows(c,'golden-black','orderbook_snapshots',[row])
            assert c.execute("SELECT count(*) FROM main.orderbook_snapshots WHERE snapshot_id='new-snapshot'").fetchone()[0]==0
            assert c.execute("SELECT count(*) FROM data_quality_issues WHERE issue_id='prior'").fetchone()[0]==1
            assert c.in_transaction


def test_nullable_market_subject_and_token_only_book_are_preserved(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True)
        collected(config,repo)
        row=dict(source_rows(repo)['market_observations'][0]);row.pop('__rowid__')
        row.update(observation_id='missing-condition',condition_id=None,eligible=0,exclusion_reason='MISSING_ID')
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            insert_raw_rows(c,'golden-black','market_observations',[row])
            assert c.execute("SELECT condition_id,question FROM market_observations WHERE observation_id='missing-condition'").fetchone()[0] is None
            record_id=c.execute("SELECT _public_record_id FROM main.market_observations WHERE observation_id='missing-condition'").fetchone()[0]
            record,=store.get_projection_records([record_id],store.scalar_authority_identity())
            assert record.projection.values[1] is None
        books=list(store.query_projection_records(authority_uuid=store.scalar_authority_identity(),kind='black-book-source-v1',token_id='yes'))
        assert len(books)==1 and 'condition_id' not in RAW_TABLES['orderbook_snapshots'].public_columns


def test_reopen_and_second_generation_preserve_logical_rows_and_receipts(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True)
        collected(config,repo)
        before=source_rows(repo)
        reopened=ResearchRepository(repo.path,busy_timeout_ms=1000,data_contract=config.trading.data_contract,raw_profile_id=BLACK_PROFILE_ID)
        assert source_rows(reopened)==before
        refs=codec(monkeypatch,store)
        namespace=scalar_namespace('fixture-source','fixture-job','golden-black',config.job_name)
        target=tmp_path/'second.db'
        result=migrate_raw_database(repo.path,target,source_sha256=file_sha256(repo.path),namespace=namespace,references=refs)
        assert result['status']=='VERIFIED'
        with market_data_sqlite.connect(target,references=refs) as c:
            assert {t:list(iter_raw_logical_rows(c,t,references=refs)) for t in RAW_TABLES}==before
            verify_raw_dependencies(c,references=refs)


def test_private_iterator_keeps_physical_mixed_marker_and_rejects_marker_laundering(tmp_path,monkeypatch):
    from polybot_observability.market_data_raw_links import iter_raw_private_rows
    from polybot_observability.market_data_mixed import is_mixed_payload
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True)
        collected(config,repo)
        with repo.connect() as c:
            private,=iter_raw_private_rows(c,'market_observations')
            from polybot_observability.market_data_private_packets import is_private_packet,resolve_private_packet
            assert is_private_packet(private['normalized_json'])
            assert is_mixed_payload(resolve_private_packet(c,private['normalized_json'],strategy='golden-black',table='market_observations',column='normalized_json'))
            assert not is_mixed_payload(next(iter_raw_logical_rows(c,'market_observations'))['normalized_json'])
        row=dict(source_rows(repo)['market_observations'][0]);row.pop('__rowid__')
        row.update(observation_id='forged',condition_id='forged-condition',
                   exclusion_reason=PayloadReferences(store,store).encode_many(['PRIVATE_DECISION'])[0])
        writes=[]
        monkeypatch.setattr(store,'put_public_projections',lambda groups:writes.append(groups))
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            with pytest.raises(ValueError,match='unapproved reference'):
                insert_raw_rows(c,'golden-black','market_observations',[row])
            assert writes==[]


def test_forged_group_reference_fails_read_and_closure_instead_of_using_local_prices(tmp_path,monkeypatch):
    from polybot_observability.market_data_raw_links import iter_raw_private_rows
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True)
        collected(config,repo)
        with repo.connect() as c:
            row=dict(next(iter_raw_private_rows(c,'orderbook_snapshots')))
            row.pop('__rowid__');row.update(snapshot_id='forged',run_id='forged-run')
            names=tuple(row)
            c.execute('INSERT INTO main.orderbook_snapshots('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row.values()))
            with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
                c.execute("SELECT best_bid FROM orderbook_snapshots WHERE snapshot_id='forged'").fetchone()
            with pytest.raises(ValueError,match='receipt ownership'):
                verify_raw_dependencies(c)


def _migrate(repo,config,target,refs,**kwargs):
    return migrate_raw_database(repo.path,target,source_sha256=file_sha256(repo.path),
        namespace=scalar_namespace('fixture-source','fixture-job','golden-black',config.job_name),references=refs,**kwargs)


@pytest.mark.parametrize('suffix',['-wal','-journal'])
def test_offline_migration_rejects_active_source_without_creating_output(tmp_path,monkeypatch,suffix):
    config,repo=repository(tmp_path,monkeypatch)
    collected(config,repo)
    marker=repo.path.with_name(repo.path.name+suffix);marker.write_bytes(b'active')
    with PayloadStore(tmp_path/'public.db') as store:
        refs=codec(monkeypatch,store)
        with pytest.raises(ValueError,match='offline source'):
            _migrate(repo,config,tmp_path/'target.db',refs)
        assert not (tmp_path/'target.db').exists()


def test_migration_exclusive_creation_preserves_racing_file(tmp_path,monkeypatch):
    from polybot_observability import market_data_raw_migrate as module
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    target=tmp_path/'target.db'; real_open=module.os.open
    def racing_open(path,flags,*args):
        if path==target: target.write_bytes(b'foreign file')
        return real_open(path,flags,*args)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=codec(monkeypatch,store);monkeypatch.setattr(module.os,'open',racing_open)
        with pytest.raises(FileExistsError): _migrate(repo,config,target,refs)
        assert target.read_bytes()==b'foreign file'


def test_migration_capacity_failure_preserves_source_and_marks_derivative_failed(tmp_path,monkeypatch):
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    source_sha=file_sha256(repo.path); target=tmp_path/'target.db'; calls=[]
    def gate():
        calls.append(1)
        if len(calls)==4: raise RuntimeError('capacity below floor')
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(RuntimeError,match='below floor'):
            _migrate(repo,config,target,codec(monkeypatch,store),batch_rows=1,storage_guard=gate)
    assert file_sha256(repo.path)==source_sha
    manifest=json.loads(target.with_suffix('.db.raw-migration.json').read_text())
    assert manifest['status']=='FAILED'
    with sqlite3.connect(target) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_migration_preserves_encoding_sequence_real_rowids_and_generated_cells(tmp_path,monkeypatch):
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    with sqlite3.connect(repo.path) as c:
        c.execute('CREATE TABLE private_notes(id INTEGER PRIMARY KEY AUTOINCREMENT, rowid TEXT, n INTEGER, doubled INTEGER GENERATED ALWAYS AS(n*2))')
        c.executemany('INSERT INTO private_notes(id,rowid,n) VALUES(?,?,?)',[(-7,'literal-rowid',3),(0,'zero',4),(99,'large',5),(1000,'removed',6)])
        c.execute('DELETE FROM private_notes WHERE id=1000')
        c.execute('CREATE TABLE private_pair(a TEXT,b INTEGER,PRIMARY KEY(a,b)) WITHOUT ROWID')
        c.executemany('INSERT INTO private_pair VALUES(?,?)',[('x',2),('y',1)])
        c.execute('PRAGMA application_id=42');c.execute('PRAGMA user_version=8')
        c.execute('PRAGMA journal_mode=DELETE')
    # VACUUM INTO cannot alter encoding, so copy source schema/data into UTF-16.
    encoded=tmp_path/'utf16.db'
    with sqlite3.connect(repo.path) as c, sqlite3.connect(encoded) as other:
        other.execute("PRAGMA encoding='UTF-16le'")
        other.executescript('\n'.join(c.iterdump()))
        other.execute('PRAGMA application_id=42');other.execute('PRAGMA user_version=8')
    repo.path=encoded
    with PayloadStore(tmp_path/'public.db') as store:
        result=_migrate(repo,config,tmp_path/'target.db',codec(monkeypatch,store),batch_rows=1)
        assert result['schema_evidence']['source']['encoding']=='UTF-16le'
        assert result['schema_evidence']['target']['encoding']=='UTF-16le'
        assert result['private_storage']['private_notes']['rows']==3
        with sqlite3.connect(tmp_path/'target.db') as c:
            assert c.execute('SELECT id,rowid,doubled FROM private_notes ORDER BY id').fetchall()==[(-7,'literal-rowid',6),(0,'zero',8),(99,'large',10)]
            c.execute("INSERT INTO private_notes(rowid,n) VALUES('next',7)")
            assert c.execute("SELECT id FROM private_notes WHERE rowid='next'").fetchone()[0]==1001


def test_private_marker_in_ordinary_source_fails_before_derivative_or_public_write(tmp_path,monkeypatch):
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=codec(monkeypatch,store)
        marker=refs.encode_many(['PRIVATE_ONLY'])[0]
        with sqlite3.connect(repo.path) as c:
            c.execute('CREATE TABLE private_notes(id INTEGER PRIMARY KEY,note TEXT)')
            c.execute('INSERT INTO private_notes VALUES(1,?)',(marker,))
        c.close()
        writes=[];monkeypatch.setattr(store,'put_public_projections',lambda groups:writes.append(groups))
        with pytest.raises(ValueError,match='unapproved reference'):
            _migrate(repo,config,tmp_path/'target.db',refs)
        assert not (tmp_path/'target.db').exists() and writes==[]


def test_public_body_marker_is_decoded_before_group_packing(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True);collected(config,repo)
        refs=codec(monkeypatch,store)
        row=dict(source_rows(repo)['market_observations'][0]);row.pop('__rowid__')
        row.update(observation_id='encoded',condition_id='encoded-condition')
        original=row['fee_schedule_json'];row['fee_schedule_json']=refs.encode_many([original])[0]
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE');insert_raw_rows(c,'golden-black','market_observations',[row])
            rid=c.execute("SELECT _public_record_id FROM main.market_observations WHERE observation_id='encoded'").fetchone()[0]
            record,=store.get_projection_records([rid],store.scalar_authority_identity())
            group=dict(zip(RAW_TABLES['market_observations'].public_columns,record.projection.values,strict=True))
            assert group['fee_schedule_json']==original


def test_second_public_receipt_batch_failure_rolls_back_all_local_rows(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True);collected(config,repo)
        prototype=dict(source_rows(repo)['orderbook_snapshots'][0]);prototype.pop('__rowid__')
        rows=[dict(prototype,snapshot_id=f'new-{n}',run_id=f'new-{n}') for n in range(503)]
        append=store.append_projection_receipts;calls=[]
        def fail_second(receipts,**kwargs):
            calls.append(len(receipts))
            if len(calls)==2: raise OSError('second ACK failed')
            return append(receipts,**kwargs)
        monkeypatch.setattr(store,'append_projection_receipts',fail_second)
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            before=c.execute('SELECT count(*) FROM main.orderbook_snapshots').fetchone()[0]
            with pytest.raises(OSError,match='second ACK'):
                insert_raw_rows(c,'golden-black','orderbook_snapshots',rows)
            assert c.in_transaction
            assert c.execute('SELECT count(*) FROM main.orderbook_snapshots').fetchone()[0]==before
        assert calls==[500,3]


@pytest.mark.parametrize("combined",[False,True])
def test_dependency_read_splits_byte_limit_without_skipping_records(tmp_path,monkeypatch,combined):
    from polybot_observability.market_data_store import StoreLimitError
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True);collected(config,repo)
        real=store.get_projection_bound_records if combined else store.get_projection_records;calls=[]
        def small_response(ids,*args,**kwargs):
            calls.append(len(ids))
            if len(ids)>1: raise StoreLimitError('fixture response byte limit')
            return real(ids,*args,**kwargs)
        monkeypatch.setattr(store,'get_projection_bound_records' if combined else 'get_projection_records',small_response)
        if not combined:monkeypatch.setattr(store,'get_projection_bound_records',None)
        with repo.connect() as c: verify_raw_dependencies(c)
        assert max(calls)>1 and 1 in calls


def test_old_daemon_rejected_before_empty_runtime_creation(tmp_path,monkeypatch):
    class OldDaemon:
        def require_capabilities(self,required):
            assert 'public-projection-nullable-subjects-v2' in required
            raise ValueError('required capability absent')
    from polybot.db import repository as module
    refs=PayloadReferences(writer=OldDaemon())
    monkeypatch.setenv('PUBLIC_MARKET_DATA_RAW','1')
    monkeypatch.setattr(module,'configured_references',lambda:refs)
    target=tmp_path/'new'/'trades.db'
    with pytest.raises(ValueError,match='capability absent'):
        ResearchRepository(target,busy_timeout_ms=1000,data_contract='unused')
    assert not target.parent.exists()


@pytest.mark.parametrize('identity',['JOB_NAME','PUBLIC_MARKET_DATA_SOURCE','runtime'])
def test_raw_reopen_rejects_active_route_change_before_publication(tmp_path,monkeypatch,identity):
    import shutil
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True);collected(config,repo)
        path=repo.path
        if identity=='runtime':
            path=repo.path.parent.parent/'other-runtime'/repo.path.name
            path.parent.mkdir();shutil.copy2(repo.path,path)
        else: monkeypatch.setenv(identity,'unrelated-owner')
        writes=[];monkeypatch.setattr(store,'put_public_projections',lambda groups:writes.append(groups))
        with pytest.raises(ValueError,match='source/job/runtime differs'):
            ResearchRepository(path,busy_timeout_ms=1000,data_contract=config.trading.data_contract,raw_profile_id=BLACK_PROFILE_ID)
        assert writes==[]


def test_raw_insert_rechecks_route_changed_after_connection_open(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True);collected(config,repo)
        row=dict(source_rows(repo)['orderbook_snapshots'][0]);row.pop('__rowid__')
        row.update(snapshot_id='wrong-owner',run_id='wrong-owner')
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE');monkeypatch.setenv('JOB_NAME','unrelated-owner')
            writes=[];monkeypatch.setattr(store,'put_public_projections',lambda groups:writes.append(groups))
            with pytest.raises(ValueError,match='source/job/runtime differs'):
                insert_raw_rows(c,'golden-black','orderbook_snapshots',[row])
            assert writes==[]


@pytest.mark.parametrize('replacement',['sidecar','target'])
def test_migration_output_swap_never_overwrites_foreign_file(tmp_path,monkeypatch,replacement):
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    target=tmp_path/'target.db';sidecar=target.with_suffix('.db.raw-migration.json')
    victim=tmp_path/'sentinel.txt';victim.write_text('DO NOT MODIFY')
    replaced=[]
    def swap(table,evidence):
        if replaced:return
        path=sidecar if replacement=='sidecar' else target
        path.unlink();path.symlink_to(victim);replaced.append(True)
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(ValueError,match='output path ownership changed'):
            _migrate(repo,config,target,codec(monkeypatch,store),progress=swap)
    assert victim.read_text()=='DO NOT MODIFY'


@pytest.mark.parametrize('dependent',['view','trigger'])
def test_unreviewed_persistent_sql_rejected_before_derivative_creation(tmp_path,monkeypatch,dependent):
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    with sqlite3.connect(repo.path) as c:
        if dependent=='view':
            c.execute('CREATE VIEW quote_view AS SELECT snapshot_id,best_bid FROM orderbook_snapshots')
        else:
            c.execute('CREATE TABLE private_updates(x TEXT)')
            c.execute('CREATE TRIGGER quote_trigger AFTER INSERT ON private_updates BEGIN SELECT best_bid FROM orderbook_snapshots; END')
    c.close()
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(ValueError,match='persistent'):
            _migrate(repo,config,tmp_path/'target.db',codec(monkeypatch,store))
        assert not (tmp_path/'target.db').exists()


def test_explicit_black_profile_keeps_legacy_layout_and_derivative_contract(tmp_path,monkeypatch):
    from polybot_observability.market_data_raw_profiles import BLACK_PROFILE_ID
    from polybot_observability.market_data_raw_links import CONTRACT
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=codec(monkeypatch,store)
        result=_migrate(repo,config,tmp_path/'explicit.db',refs,profile_id=BLACK_PROFILE_ID)
        assert result['contract']=='black-raw-parent-derivative-v1'
        assert result['public_projection_records']['contract']=='public-projection-closure-v3'
        assert 'raw_profile_id' not in result
        with market_data_sqlite.connect(tmp_path/'explicit.db',references=refs) as c:
            assert raw_layout_metadata(c)['contract']==CONTRACT


def test_explicit_black_initializer_does_not_create_an_unsupported_profile_epoch(tmp_path,monkeypatch):
    from polybot_observability.market_data_raw_profiles import BLACK_PROFILE_ID
    from polybot_observability.market_data_raw_links import CONTRACT
    config,repo=repository(tmp_path,monkeypatch)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=codec(monkeypatch,store)
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            initialize_raw_links(c,scalar_namespace('fixture-source','fixture-job','golden-black',config.job_name),
                store.scalar_authority_identity(),references=refs,profile_id=BLACK_PROFILE_ID)
            assert raw_layout_metadata(c)['contract']==CONTRACT


def test_unissued_black_profile_v2_marker_is_rejected_instead_of_verified(tmp_path):
    from polybot_observability.market_data_raw_profiles import BLACK_PROFILE_ID
    from polybot_observability.market_data_raw_links import PROFILE_LAYOUT_SQL,PROFILE_CONTRACT,source_schema_sha
    with sqlite3.connect(tmp_path/'invalid.db') as c:
        c.execute(PROFILE_LAYOUT_SQL)
        c.execute('INSERT INTO _public_raw_layout VALUES(1,?,?,?,?,?,?,?,?)',(
            PROFILE_CONTRACT,'golden-black',scalar_namespace('source','job','golden-black','runtime'),
            '00000000-0000-4000-8000-000000000001',source_schema_sha(),BLACK_PROFILE_ID,1,source_schema_sha()))
        with pytest.raises(ValueError,match='legacy v1 layout'):
            raw_layout_metadata(c)


def test_new_derivative_index_packing_preserves_negative_zero_sparse_raw_rowids(tmp_path,monkeypatch):
    from polybot_observability import market_data_raw_migrate as migration
    from polybot_observability.market_data_raw_links import immutable_sql
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    with sqlite3.connect(repo.path) as c:
        for table,new_ids in [('market_observations',[-7]),('outcome_observations',[0,99]),('orderbook_snapshots',[-12,1001])]:
            c.execute('DROP TRIGGER '+table+'_forbid_update')
            keys=[r[0] for r in c.execute('SELECT '+RAW_TABLES[table].primary_key+' FROM '+table+' ORDER BY rowid')]
            for key,new_id in zip(keys,new_ids,strict=True):
                c.execute('UPDATE '+table+' SET rowid=? WHERE '+RAW_TABLES[table].primary_key+'=?',(new_id,key))
            c.execute(immutable_sql(table,'UPDATE'))
    c.close()
    before=source_rows(repo);source_sha=file_sha256(repo.path)
    statements=[];original_connect=migration._connect_owned_target
    def traced(path,descriptor):
        c=original_connect(path,descriptor);c.set_trace_callback(statements.append);return c
    monkeypatch.setattr(migration,'_connect_owned_target',traced)
    with PayloadStore(tmp_path/'public.db') as store:
        refs=codec(monkeypatch,store)
        result=_migrate(repo,config,tmp_path/'packed.db',refs,batch_rows=1)
        maintenance=result['physical_maintenance']
        assert maintenance['status']=='PACKED'
        assert maintenance['before']['auto_vacuum']==2
        assert maintenance['after']['freelist_count']==0
        assert len(maintenance['reindexed'])>0
        assert not any(sql.lstrip().upper().startswith('VACUUM') for sql in statements)
        with market_data_sqlite.connect(tmp_path/'packed.db',references=refs) as after:
            assert {table:list(iter_raw_logical_rows(after,table,references=refs)) for table in RAW_TABLES}==before
            assert not after.execute('PRAGMA foreign_key_check').fetchall()
    assert file_sha256(repo.path)==source_sha


def test_capacity_failure_during_index_packing_never_marks_verified(tmp_path,monkeypatch):
    from polybot_observability import market_data_raw_migrate as migration
    config,repo=repository(tmp_path,monkeypatch);collected(config,repo)
    source_sha=file_sha256(repo.path);target=tmp_path/'packed.db'
    original_pack=migration._pack_new_derivative
    def fail_during_pack(connection,guard,evidence):
        calls=[]
        def low_capacity():
            guard();calls.append(1)
            if len(calls)==2:raise RuntimeError('packing capacity below floor')
        return original_pack(connection,low_capacity,evidence)
    monkeypatch.setattr(migration,'_pack_new_derivative',fail_during_pack)
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(RuntimeError,match='packing capacity'):
            _migrate(repo,config,target,codec(monkeypatch,store))
    manifest=json.loads(target.with_suffix('.db.raw-migration.json').read_text())
    assert manifest['status']=='FAILED'
    assert manifest['physical_maintenance']['status']=='FAILED'
    assert len(manifest['physical_maintenance']['reindexed'])==1
    assert file_sha256(repo.path)==source_sha
    with sqlite3.connect(target) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_views_and_logical_iterator_use_single_bound_read_without_legacy_pair(tmp_path,monkeypatch):
    with PayloadStore(tmp_path/'public.db') as store:
        config,repo=repository(tmp_path,monkeypatch,shared=store,raw=True);collected(config,repo)
        original=source_rows(repo)
        calls=[];bound=store.get_projection_bound_records
        def one_call(keys,**kwargs):calls.append(len(keys));return bound(keys,**kwargs)
        def forbidden(*args,**kwargs):raise AssertionError('legacy paired operation was used')
        monkeypatch.setattr(store,'get_projection_bound_records',one_call)
        monkeypatch.setattr(store,'get_projection_records',forbidden)
        monkeypatch.setattr(store,'get_projection_receipts',forbidden)
        with repo.connect() as c:
            verify_raw_dependencies(c)
            assert c.execute('SELECT best_bid FROM orderbook_snapshots ORDER BY token_id').fetchall()
            assert {t:list(iter_raw_logical_rows(c,t)) for t in RAW_TABLES}==original
        assert calls and max(calls)>1
