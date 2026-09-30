"""Recorder mutable source cache and exact original constraints through RAW."""
from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

from polybot_observability.market_data_bundle import reference_closure, verify_closure
from polybot_observability.market_data_migrate import file_sha256
from polybot_observability.market_data_projection_closure import verify_projection_closure
from polybot_observability.market_data_private_packets import PACKET_TABLE
from polybot_observability.market_data_raw_links import (
    initialize_raw_links, insert_raw_rows, iter_raw_receipts, logical_raw_schema_rows,
    raw_layout_metadata, verify_raw_dependencies,
)
from polybot_observability.market_data_raw_migrate import migrate_raw_database
from polybot_observability.market_data_raw_profiles import COCONUT_RECORDER_PROFILE_ID, raw_profile
from polybot_observability.market_data_raw_schema_recorder import RECORDER_SCHEMA_OBJECTS, RECORDER_LOGICAL_SCHEMA_SHA256
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadStore, MissingPayloadError, CorruptPayloadError


STRATEGY = 'golden-coconut'
NAMESPACE = scalar_namespace('fixture', 'polybot-white', STRATEGY, 'coconut-sports-recorder-1m-v2')


def native(path):
    c = sqlite3.connect(path)
    for kind in ('table', 'index', 'trigger'):
        for item in RECORDER_SCHEMA_OBJECTS:
            if item[0] == kind:
                c.execute(item[3])
    c.execute('PRAGMA application_id=0x43535231')
    c.execute('PRAGMA user_version=1')
    c.execute('INSERT INTO collection_contracts VALUES(1,?,?,?,?,?)',
              ('research-full-v1', 'sports-price-recorder-1m-v2', '2026-09-30',
               'coconut-sports-recorder-1m-v2', RECORDER_LOGICAL_SCHEMA_SHA256))
    c.commit()
    return c


def tracked(**changes):
    row = dict(event_id='event', family='PRIVATE_FAMILY', first_run_id='PRIVATE_FIRST', state='SCHEDULED',
               slots_json='[{"slot":"PRIVATE_SLOT","token_id":"token","outcome":"Yes","verified_role":"PRIVATE_ROLE"}]',
               scheduled_start='2026-09-30T00:00:00Z', end_anchor=None, end_basis=None, ever_live=0,
               terminal_json=None, next_due='PRIVATE_DUE', missing_count=0, anchor_json='{"request_id":"PRIVATE_REQUEST"}')
    return {**row, **changes}


@pytest.fixture
def raw_case(tmp_path):
    path = tmp_path/'private.db'
    native(path).close()
    store = PayloadStore(tmp_path/'public.db')
    refs = PayloadReferences(store, store, cache_bytes=0)
    c = connect(path, references=refs)
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('BEGIN IMMEDIATE')
    initialize_raw_links(c, NAMESPACE, store.scalar_authority_identity(), references=refs, profile_id=COCONUT_RECORDER_PROFILE_ID)
    c.commit()
    try:
        yield path, c, store, refs
    finally:
        c.close()
        store.close()


def publish(c, rows, refs):
    c.execute('BEGIN IMMEDIATE')
    try:
        insert_raw_rows(c, STRATEGY, 'tracked_events', rows, references=refs, namespace=NAMESPACE)
        c.commit()
    except BaseException:
        c.rollback()
        raise


def test_original_schema_constraints_and_mutable_rowid_current_closure(raw_case):
    path, c, store, refs = raw_case
    assert logical_raw_schema_rows(c) == list(RECORDER_SCHEMA_OBJECTS)
    assert hashlib.sha256(json.dumps(logical_raw_schema_rows(c), separators=(',', ':')).encode()).hexdigest() == RECORDER_LOGICAL_SCHEMA_SHA256
    row = tracked(__rowid__=-7)
    publish(c, [row], refs)
    original = c.execute('SELECT rowid,_public_record_id FROM main.tracked_events').fetchone()
    assert original[0] == -7
    # A private-only update keeps both the public record and its exact tuple.
    publish(c, [tracked(state='WINDOW', next_due='PRIVATE_LATER')], refs)
    assert c.execute('SELECT rowid,_public_record_id FROM main.tracked_events').fetchone() == original
    assert len(list(iter_raw_receipts(c))) == 1
    publish(c, [tracked(scheduled_start='2026-10-01T00:00:00Z', state='WAIT_SETTLEMENT')], refs)
    new = c.execute('SELECT rowid,_public_record_id FROM main.tracked_events').fetchone()
    assert new[0] == -7 and new[1] != original[1]
    assert len(list(iter_raw_receipts(c))) == 1
    assert c.execute('SELECT scheduled_start,state,slots_json,anchor_json FROM tracked_events').fetchone() == (
        '2026-10-01T00:00:00Z', 'WAIT_SETTLEMENT', row['slots_json'], row['anchor_json'])
    with closing(sqlite3.connect(path)) as physical:
        value = physical.execute('SELECT slots_json FROM tracked_events').fetchone()[0]
        assert value != row['slots_json'] and 'PRIVATE_ROLE' not in value
    verify_raw_dependencies(c, references=PayloadReferences(store, cache_bytes=0))
    closure = verify_projection_closure(store, path, STRATEGY)
    assert closure['record_count'] == closure['receipt_count'] == 1
    assert verify_closure(store, reference_closure(path, STRATEGY))['payload_count'] > 0
    plan = ' '.join(str(row[-1]) for row in c.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM tracked_events WHERE state IN ('SCHEDULED','WINDOW','WAIT_SETTLEMENT') AND next_due<=? ORDER BY next_due,event_id",
        ('PRIVATE_Z',)))
    assert 'tracked_due_idx' in plan


def test_mutable_accepts_owned_physical_packets_but_not_private_cas(raw_case):
    path, c, store, refs = raw_case
    publish(c, [tracked()], refs)
    with closing(sqlite3.connect(path)) as physical:
        physical.row_factory = sqlite3.Row
        row = dict(physical.execute('SELECT * FROM tracked_events').fetchone())
    row.pop('_public_record_id')
    row['scheduled_start'] = tracked()['scheduled_start']
    publish(c, [{**row, 'state': 'WINDOW'}], refs)
    assert c.execute('SELECT slots_json FROM tracked_events').fetchone()[0] == tracked()['slots_json']
    bad = {**tracked(), 'anchor_json': refs.encode_many(['PRIVATE_ANCHOR'])[0]}
    with pytest.raises(ValueError, match='unapproved reference'):
        publish(c, [bad], refs)


def test_mutable_wrong_ack_rolls_back_private_cache(raw_case, monkeypatch):
    path, c, store, refs = raw_case
    publish(c, [tracked()], refs)
    before = c.execute('SELECT * FROM tracked_events').fetchone()
    monkeypatch.setattr(store, 'put_public_projections', lambda rows: [])
    with pytest.raises(ValueError, match='ACK'):
        publish(c, [tracked(state='DONE', scheduled_start='2026-10-01T00:00:00Z')], refs)
    assert c.execute('SELECT * FROM tracked_events').fetchone() == before


def test_mutable_native_required_columns_and_rowid_are_not_relaxed(raw_case):
    _, c, _, refs = raw_case
    publish(c, [tracked(__rowid__=0)], refs)
    with pytest.raises(ValueError, match='rowid'):
        publish(c, [tracked(__rowid__=9)], refs)
    missing = tracked()
    del missing['anchor_json']
    with pytest.raises(ValueError, match='complete native'):
        publish(c, [missing], refs)
    with pytest.raises(sqlite3.IntegrityError):
        publish(c, [tracked(family=None)], refs)


@pytest.mark.parametrize('damage', ['receipt', 'identity', 'rowid', 'body_missing', 'body_corrupt'])
def test_current_cache_missing_or_corrupt_public_evidence_fails_closed(raw_case, damage):
    path, c, store, refs = raw_case
    publish(c, [tracked()], refs)
    with closing(sqlite3.connect(store.path)) as shared:
        if damage == 'receipt':
            guards = shared.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='projection_receipts'").fetchall()
            for name, _ in guards:
                shared.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            shared.execute('DELETE FROM projection_receipts')
            for _, ddl in guards:
                shared.execute(ddl)
        elif damage == 'body_missing':
            shared.execute('DELETE FROM payloads')
        elif damage == 'body_corrupt':
            shared.execute("UPDATE payloads SET body=X'010203'")
        shared.commit()
    if damage in {'identity', 'rowid'}:
        with closing(sqlite3.connect(path)) as local:
            local.execute("UPDATE tracked_events SET event_id='other'" if damage == 'identity'
                          else 'UPDATE tracked_events SET rowid=rowid+10')
            local.commit()
    with pytest.raises((ValueError, sqlite3.Error, MissingPayloadError, CorruptPayloadError)):
        if damage.startswith('body_'):
            verify_closure(store, reference_closure(path, STRATEGY))
        else:
            verify_projection_closure(store, path, STRATEGY)


def test_whole_cache_batch_rolls_back_after_late_native_constraint_failure(raw_case):
    path, c, _, refs = raw_case
    publish(c, [tracked()], refs)
    with closing(sqlite3.connect(path)) as local:
        before = local.execute('SELECT * FROM tracked_events').fetchall()
        packets = local.execute('SELECT COUNT(*) FROM ' + PACKET_TABLE).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        publish(c, [tracked(state='DONE', slots_json='[{"token_id":"new-public-token","slot":"NEW_PRIVATE"}]'),
                    tracked(event_id='invalid', family=None)], refs)
    with closing(sqlite3.connect(path)) as local:
        assert local.execute('SELECT * FROM tracked_events').fetchall() == before
        assert local.execute('SELECT COUNT(*) FROM ' + PACKET_TABLE).fetchone()[0] == packets


def test_profile_rejects_wrong_epoch_and_namespace(tmp_path):
    path = tmp_path/'native.db'
    with closing(native(path)) as c:
        profile = raw_profile(COCONUT_RECORDER_PROFILE_ID)
        profile.validate_authority(c)
        with pytest.raises(ValueError, match='namespace'):
            profile.validate_namespace(c, scalar_namespace('fixture', 'polybot-silver', STRATEGY, 'coconut-sports-recorder-1m-v2'))
        c.execute('DROP TRIGGER collection_contracts_update')
        c.execute("UPDATE collection_contracts SET data_contract='sports-price-recorder-1m-v1'")
        with pytest.raises(ValueError, match='authority'):
            profile.validate_authority(c)


def test_offline_derivative_preserves_sparse_rowids_nested_carry_and_source_bytes(tmp_path):
    source, target = tmp_path/'source.db', tmp_path/'derivative.db'
    with closing(native(source)) as c:
        row = tracked()
        c.execute('INSERT INTO tracked_events(rowid,'+','.join(row)+') VALUES('+','.join('?' for _ in range(len(row)+1))+')', (-7, *row.values()))
        state = json.dumps(row, sort_keys=True, indent=1)
        digest = hashlib.sha256(state.encode()).hexdigest()
        c.execute('INSERT INTO registry_carryovers(rowid,event_id,source_shard,source_state_sha256,state_json) VALUES(?,?,?,?,?)',
                  (0, row['event_id'], 'trades_sim_20260929.db', digest, state))
        c.commit()
    sha = file_sha256(source)
    with PayloadStore(tmp_path/'public.db') as store:
        refs = PayloadReferences(store, store, cache_bytes=0)
        result = migrate_raw_database(source, target, source_sha256=sha, namespace=NAMESPACE,
                                      references=refs, profile_id=COCONUT_RECORDER_PROFILE_ID)
        assert result['status'] == 'VERIFIED' and file_sha256(source) == sha
        assert result['tables']['tracked_events']['implicit_rowid_preserved'] is True
        with closing(connect(target, references=refs)) as c:
            assert c.execute('SELECT rowid FROM main.tracked_events').fetchone()[0] == -7
            restored = c.execute('SELECT rowid,state_json,source_state_sha256 FROM registry_carryovers').fetchone()
            assert restored == (0, state, digest)
            assert raw_layout_metadata(c)['profile_id'] == COCONUT_RECORDER_PROFILE_ID


def test_migration_rejects_foreign_runtime_namespace_before_creating_output(tmp_path):
    source, target = tmp_path/'source.db', tmp_path/'derivative.db'
    native(source).close()
    with PayloadStore(tmp_path/'public.db') as store:
        with pytest.raises(ValueError, match='namespace'):
            migrate_raw_database(source, target, source_sha256=file_sha256(source),
                namespace=scalar_namespace('fixture', 'polybot-silver', STRATEGY, 'coconut-sports-recorder-silver-1m-v1'),
                references=PayloadReferences(store, store), profile_id=COCONUT_RECORDER_PROFILE_ID)
        assert not target.exists()
        assert not target.with_suffix('.db.raw-migration.json').exists()


def test_immutable_parent_composite_keys_and_original_guards_remain_enforced(raw_case):
    _, c, _, refs = raw_case
    row = dict(run_id='run', event_id='event', slot='condition:0', condition_id='condition',
               token_id='token', outcome='Yes', result_kind='PRIVATE_RESULT', verified_role='PRIVATE_ROLE',
               team_name='Team', status='MISSING', request_id=None, requested_at=None, received_at=None,
               book_sha256=None, book_gzip=None, fee_json='{}', point_in_time_valid=0, reason='missing')
    c.execute('BEGIN IMMEDIATE')
    insert_raw_rows(c, STRATEGY, 'book_observations', [dict(row, __rowid__=-99)], references=refs, namespace=NAMESPACE)
    c.commit()
    assert c.execute('SELECT outcome,team_name,result_kind FROM book_observations').fetchone() == ('Yes', 'Team', 'PRIVATE_RESULT')
    with pytest.raises(sqlite3.IntegrityError):
        c.execute('BEGIN IMMEDIATE')
        try:
            insert_raw_rows(c, STRATEGY, 'book_observations', [row], references=refs, namespace=NAMESPACE)
        finally:
            c.rollback()
    for statement in ("UPDATE main.book_observations SET status='FULL'", 'DELETE FROM main.book_observations'):
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            c.execute(statement)
        c.rollback()
