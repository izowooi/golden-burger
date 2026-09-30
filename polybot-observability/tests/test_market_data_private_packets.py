"""Private PMMIX interning preserves owner, lexical bytes and transaction state."""
import gzip
import hashlib
import json
import sqlite3

import pytest

from polybot_observability.market_data_mixed import externalize_mixed_row, is_mixed_payload, resolve_mixed_payload
from polybot_observability.market_data_private_packets import (
    CACHE_BYTES, CONTRACT, LAYOUT_TABLE, PACKET_TABLE, PREFIX,
    PrivatePacketError, PrivatePacketOwnerError, PrivatePacketReader,
    initialize_private_packets, intern_private_packet, intern_private_row,
    is_private_packet, iter_private_packet_records, parse_private_packet,
    private_packet_layout, resolve_private_packet, validate_private_packets,
)
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_scalar_links import scalar_namespace
from polybot_observability.market_data_store import PayloadStore

STRATEGY = 'golden-black'
TABLE = 'market_observations'
COLUMN = 'normalized_json'
NAMESPACE = scalar_namespace('test-source', 'test-job', STRATEGY, 'test-runtime')


def namespace(strategy=STRATEGY, runtime='test-runtime'):
    return scalar_namespace('test-source', 'test-job', strategy, runtime)


@pytest.fixture
def stores(tmp_path):
    public = PayloadStore(tmp_path / 'public.db')
    refs = PayloadReferences(reader=public, writer=public, cache_bytes=0)
    private = sqlite3.connect(tmp_path / 'private.db')
    private.execute('BEGIN')
    initialize_private_packets(private, strategy=STRATEGY, namespace=NAMESPACE)
    try:
        yield private, refs
    finally:
        private.close()
        public.close()


def packet(refs, source=None):
    if source is None:
        source = '{ "condition_id":"provider-c", "liquidity": 1e+03, "fee_rate":1.00e-2, "unknown":"PRIVATE_\\u0042" }'
    original = {COLUMN: source}
    value = externalize_mixed_row(STRATEGY, TABLE, original, original, refs)[COLUMN]
    assert is_mixed_payload(value)
    return source, value


def intern(c, value, refs):
    return intern_private_packet(c, value, strategy=STRATEGY, namespace=NAMESPACE,
                                 table=TABLE, column=COLUMN, references=refs)


def test_exact_text_bytes_retry_compression_and_public_private_boundary(stores):
    c, refs = stores
    source, encoded = packet(refs)
    before = refs.reader.stats()
    marker = intern(c, encoded, refs)
    assert is_private_packet(marker) and len(marker) < len(encoded)
    assert intern(c, encoded, refs) == marker
    assert intern(c, marker, refs) == marker
    assert c.execute(f'SELECT count(*) FROM {PACKET_TABLE}').fetchone() == (1,)
    assert refs.reader.stats() == before
    expanded = resolve_private_packet(c, marker, strategy=STRATEGY, namespace=NAMESPACE,
                                      table=TABLE, column=COLUMN)
    assert type(expanded) is str and expanded.encode() == encoded.encode()
    assert resolve_mixed_payload(expanded, refs) == source
    assert c.execute(f'SELECT sha256,raw_size FROM {PACKET_TABLE}').fetchone() == (
        hashlib.sha256(encoded.encode()).digest(), len(encoded.encode()))
    assert resolve_private_packet(c, marker) == encoded
    assert resolve_private_packet(c, 'ordinary literal') == 'ordinary literal'
    assert resolve_private_packet(c, None) is None
    assert intern_private_row(c, STRATEGY, TABLE, {COLUMN: encoded, 'local_value': 9},
        namespace=NAMESPACE, references=refs) == {COLUMN: marker, 'local_value': 9}
    assert validate_private_packets(c, strategy=STRATEGY, namespace=NAMESPACE)
    assert private_packet_layout(c)['contract'] == CONTRACT
    for (digest,) in refs.reader._connection.execute('SELECT sha256 FROM payloads'):
        assert b'PRIVATE_' not in refs.reader.get_many([digest])[0]


def test_blob_packet_preserves_gzip_and_sqlite_type(tmp_path):
    strategy = 'golden-raspberry'
    ns = namespace(strategy)
    c = sqlite3.connect(tmp_path / 'private.db')
    with PayloadStore(tmp_path / 'public.db') as public:
        refs = PayloadReferences(reader=public, writer=public)
        source = gzip.compress(b'[{"condition_id":"c", "rank":-0.0, "secret":"private"}]', mtime=123)
        row = {'membership_blob': source}
        encoded = externalize_mixed_row(strategy, 'market_sweeps', row, row, refs)['membership_blob']
        c.execute('BEGIN')
        initialize_private_packets(c, strategy=strategy, namespace=ns)
        marker = intern_private_packet(c, encoded, strategy=strategy, namespace=ns,
            table='market_sweeps', column='membership_blob', references=refs)
        assert type(marker) is bytes
        assert resolve_private_packet(c, marker) == encoded
        assert resolve_mixed_payload(resolve_private_packet(c, marker), refs) == source
        with pytest.raises(PrivatePacketError, match='SQLite type'):
            resolve_private_packet(c, marker.decode())
    c.close()


def test_insert_is_rollback_atomic_and_reader_cache_survives_id_reuse(stores):
    c, refs = stores
    c.commit()
    _, first = packet(refs)
    _, second = packet(refs, '{"condition_id":"other", "fee_rate":-0.0}')
    c.execute('BEGIN')
    old = intern(c, first, refs)
    reader = PrivatePacketReader(c)
    assert reader.resolve(old) == first
    c.rollback()
    with pytest.raises(PrivatePacketError, match='missing'):
        reader.resolve(old)
    c.execute('BEGIN')
    new = intern(c, second, refs)
    assert parse_private_packet(new)[1] == parse_private_packet(old)[1]
    assert reader.resolve(new) == second
    assert reader.cache_bytes <= CACHE_BYTES
    c.rollback()
    with pytest.raises(PrivatePacketError, match='missing'):
        resolve_private_packet(c, new)


def test_initialization_requires_transaction_and_rejects_unowned_data(tmp_path):
    c = sqlite3.connect(tmp_path / 'private.db')
    with pytest.raises(PrivatePacketError, match='caller transaction'):
        initialize_private_packets(c, strategy=STRATEGY, namespace=NAMESPACE)
    c.execute('CREATE TABLE ledger(id INTEGER PRIMARY KEY, value TEXT)')
    c.execute("INSERT INTO ledger VALUES(1,'private')")
    with pytest.raises(PrivatePacketOwnerError, match='unowned'):
        initialize_private_packets(c, strategy=STRATEGY, namespace=NAMESPACE)
    assert private_packet_layout(c) is None
    c.rollback()
    c.execute('BEGIN')
    initialize_private_packets(c, strategy=STRATEGY, namespace=NAMESPACE)
    c.rollback()
    assert validate_private_packets(c) == frozenset()
    c.close()


def test_owner_type_column_and_namespace_rebinding_are_rejected(stores, tmp_path):
    c, refs = stores
    _, encoded = packet(refs)
    marker = intern(c, encoded, refs)
    for kwargs in ({'namespace': namespace(runtime='other')}, {'strategy': 'golden-guava'},
                   {'table': 'orders'}, {'column': 'fee'}):
        with pytest.raises(PrivatePacketOwnerError):
            resolve_private_packet(c, marker, **kwargs)
    with pytest.raises(PrivatePacketError, match='SQLite type'):
        resolve_private_packet(c, marker.encode())
    with pytest.raises(PrivatePacketOwnerError):
        initialize_private_packets(c, strategy=STRATEGY, namespace=namespace(runtime='other'))
    other = sqlite3.connect(tmp_path / 'other.db')
    other.execute('BEGIN')
    initialize_private_packets(other, strategy=STRATEGY, namespace=NAMESPACE)
    assert intern(other, encoded, refs) != marker
    with pytest.raises(PrivatePacketOwnerError, match='local owner'):
        resolve_private_packet(other, marker)
    other.close()


@pytest.mark.parametrize('value', [PREFIX, '\x1ePMPKT2:fake', PREFIX + '0'*32 + ':0',
    PREFIX + '0'*32 + ':01', PREFIX + '0'*32 + ':' + str(2**63), b'\x1ePMPKT1:\xff'])
def test_malformed_family_is_recognized_and_never_literal_passthrough(stores, value):
    c, _ = stores
    assert is_private_packet(value)
    with pytest.raises(PrivatePacketError):
        resolve_private_packet(c, value)


def test_only_registered_ownership_verified_packets_can_be_interned(stores):
    c, refs = stores
    _, encoded = packet(refs)
    for value in ('ordinary string', '{"condition_id":"not external"}', '\x1ePMMIX9:broken'):
        with pytest.raises(ValueError):
            intern(c, value, refs)
    for table, column in [('orders', COLUMN), (TABLE, 'private_fee')]:
        with pytest.raises(PrivatePacketOwnerError):
            intern_private_packet(c, encoded, strategy=STRATEGY, namespace=NAMESPACE,
                table=table, column=column, references=refs)
    assert c.execute(f'SELECT count(*) FROM {PACKET_TABLE}').fetchone() == (0,)
    row = {'event_json': '{"raw":{"id":"source"},"fee":"private"}'}
    foreign = externalize_mixed_row('golden-guava', 'events', row, row, refs)['event_json']
    with pytest.raises(ValueError, match='profile'):
        intern(c, foreign, refs)


def mutate(c, table, statement):
    trigger = f'{table}_no_update'
    sql = c.execute('SELECT sql FROM sqlite_master WHERE name=?', (trigger,)).fetchone()[0]
    c.execute('DROP TRIGGER ' + trigger)
    c.execute(statement)
    c.execute(sql)


@pytest.mark.parametrize('damage', ['sha', 'body', 'profile', 'column', 'size'])
def test_corrupted_dictionary_cannot_use_stale_decompressed_cache(stores, damage):
    c, refs = stores
    _, encoded = packet(refs)
    marker = intern(c, encoded, refs)
    reader = PrivatePacketReader(c)
    assert reader.resolve(marker) == encoded
    sql = {'sha': 'sha256=zeroblob(32)', 'body': "body=x'00'", 'profile': 'profile=8',
           'column': "origin_column='private_fee'", 'size': 'raw_size=1'}[damage]
    mutate(c, PACKET_TABLE, f'UPDATE {PACKET_TABLE} SET {sql}')
    with pytest.raises(ValueError):
        reader.resolve(marker)


@pytest.mark.parametrize('damage', ['missing-layout', 'missing-trigger', 'missing-row', 'foreign-owner'])
def test_partial_or_foreign_layout_is_not_repaired(stores, damage):
    c, refs = stores
    _, encoded = packet(refs)
    marker = intern(c, encoded, refs)
    if damage == 'missing-layout':
        c.execute(f'DROP TABLE {LAYOUT_TABLE}')
    elif damage == 'missing-trigger':
        c.execute(f'DROP TRIGGER {PACKET_TABLE}_no_delete')
    elif damage == 'missing-row':
        trigger = c.execute('SELECT sql FROM sqlite_master WHERE name=?', (LAYOUT_TABLE+'_no_delete',)).fetchone()[0]
        c.execute(f'DROP TRIGGER {LAYOUT_TABLE}_no_delete')
        c.execute(f'DELETE FROM {LAYOUT_TABLE}')
        c.execute(trigger)
    else:
        c.execute('CREATE TABLE _public_raw_layout(strategy TEXT, namespace TEXT)')
        c.execute('INSERT INTO _public_raw_layout VALUES(?,?)', (STRATEGY, namespace(runtime='original-raw-owner')))
    with pytest.raises(PrivatePacketError):
        resolve_private_packet(c, marker)
    with pytest.raises(PrivatePacketError):
        initialize_private_packets(c, strategy=STRATEGY, namespace=NAMESPACE)


def test_dictionary_append_only_and_exact_compressed_length(stores):
    c, refs = stores
    _, encoded = packet(refs)
    marker = intern(c, encoded, refs)
    for table in (LAYOUT_TABLE, PACKET_TABLE):
        for operation in ('DELETE', 'UPDATE'):
            statement = f'DELETE FROM {table}' if operation == 'DELETE' else f'UPDATE {table} SET rowid=rowid'
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                c.execute(statement)
        # REPLACE deletes do not run ordinary DELETE triggers with SQLite's
        # default recursive_triggers=OFF; protect insertion conflicts explicitly.
        assert c.execute('PRAGMA recursive_triggers').fetchone() == (0,)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}')
    assert c.execute(f'SELECT codec FROM {PACKET_TABLE}').fetchone() == ('zlib',)
    mutate(c, PACKET_TABLE, f"UPDATE {PACKET_TABLE} SET body=CAST(body||x'00' AS BLOB)")
    with pytest.raises(PrivatePacketError, match='trailing'):
        resolve_private_packet(c, marker)


def test_reachable_iteration_query_only_no_source_mutation(stores):
    c, refs = stores
    packets = [packet(refs, json.dumps({'condition_id': str(i), 'fee_rate': i/100}))[1] for i in range(12)]
    markers = [intern(c, value, refs) for value in packets]
    c.commit()
    before = c.total_changes
    c.execute('PRAGMA query_only=ON')
    records = list(iter_private_packet_records(c, markers=(m for m in [markers[7],markers[1],markers[7],markers[4]]), batch_size=2))
    assert [r.record_id for r in records] == [2,5,8]
    assert [r.packet for r in records] == [packets[1],packets[4],packets[7]]
    assert all(r.table == TABLE and r.column == COLUMN for r in records)
    assert [r.packet for r in iter_private_packet_records(c, batch_size=3)] == packets
    assert c.total_changes == before and not c.in_transaction
    assert c.execute("SELECT name FROM sqlite_temp_master").fetchall() == []
    assert list(iter_private_packet_records(c, markers=[])) == []
    missing = PREFIX + private_packet_layout(c)['owner_token'] + ':99999'
    with pytest.raises(PrivatePacketError, match='missing'):
        list(iter_private_packet_records(c, markers=[missing]))
    with pytest.raises(PrivatePacketError, match='SQLite type'):
        list(iter_private_packet_records(c, markers=[markers[0],markers[0].encode()]))
    assert c.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


def test_raw_cursors_avoid_decoder_recursion(tmp_path):
    class ForbiddenCursor(sqlite3.Cursor):
        def execute(self, *args, **kwargs):
            raise AssertionError('resolving cursor must not be used')
    class ForbiddenConnection(sqlite3.Connection):
        def execute(self, *args, **kwargs):
            raise AssertionError('connection resolver must not be used')
        def cursor(self, *args, **kwargs):
            return super().cursor(ForbiddenCursor)
    c = sqlite3.connect(tmp_path / 'private.db', factory=ForbiddenConnection)
    sqlite3.Connection.execute(c, 'BEGIN')
    initialize_private_packets(c, strategy=STRATEGY, namespace=NAMESPACE)
    assert private_packet_layout(c)['namespace'] == NAMESPACE
    c.close()


def test_multi_column_row_failure_rolls_back_its_dictionary_inserts_only(tmp_path):
    strategy, table, ns = 'golden-guava', 'book_attempts', namespace('golden-guava')
    c = sqlite3.connect(tmp_path / 'private.db')
    c.execute('BEGIN')
    initialize_private_packets(c, strategy=strategy, namespace=ns)
    c.execute('CREATE TABLE local_state(value TEXT)')
    c.execute("INSERT INTO local_state VALUES('keep earlier caller work')")
    with PayloadStore(tmp_path / 'public.db') as public:
        refs = PayloadReferences(reader=public, writer=public)
        row = {
            'book_json': '{"raw":{"bids":[]},"status":"PRIVATE"}',
            'fee_evidence_json': '{"raw":{"fee_rate":0.01},"effective":0.02}',
        }
        encoded = externalize_mixed_row(strategy, table, row, row, refs)
        encoded['fee_evidence_json'] = '\x1ePMMIX9:corrupt-second-column'
        with pytest.raises(ValueError):
            intern_private_row(c, strategy, table, encoded, namespace=ns, references=refs)
    assert c.in_transaction
    assert c.execute(f'SELECT count(*) FROM {PACKET_TABLE}').fetchone() == (0,)
    assert c.execute('SELECT value FROM local_state').fetchone() == ('keep earlier caller work',)
    c.close()
