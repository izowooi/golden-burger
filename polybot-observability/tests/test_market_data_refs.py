import hashlib
import sqlite3

import pytest

from polybot_observability.market_data_refs import (
    MissingMarketDataConfiguration, PayloadReferences, parse_reference,
)
from polybot_observability.market_data_sqlite import connect


class MemoryStore:
    def __init__(self):
        self.payloads = {}
        self.reads = []

    def put_many(self, values):
        hashes = [hashlib.sha256(value).hexdigest() for value in values]
        self.payloads.update(zip(hashes, values))
        return hashes

    def get_many(self, hashes):
        self.reads.append(hashes)
        return [self.payloads[digest] for digest in hashes]


def test_exact_text_blob_null_empty_roundtrip_and_dedup():
    store = MemoryStore()
    writer = PayloadReferences(store, store)
    original = ['{"경기":"home","prob":0.91}', b'\x1f\x8b\x00\xff', None, '', b'']
    encoded = writer.encode_many(original)
    assert encoded != original
    assert encoded[2] is None
    assert parse_reference(encoded[0])[0] == 'T'
    assert parse_reference(encoded[1])[0] == 'B'
    assert len(store.payloads) == 3  # empty TEXT and BLOB share body, not type
    assert PayloadReferences(reader=store).decode_many(encoded) == original


def test_sqlite_row_alias_join_and_custom_factory_receive_original_values():
    store = MemoryStore()
    encoded = PayloadReferences(store, store).encode_many(['{"Yes":0.7}', b'book'])
    c = connect(':memory:', references=PayloadReferences(reader=store))
    c.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY, raw TEXT, book BLOB)')
    c.execute('CREATE TABLE ledger(id INTEGER, cash REAL)')
    c.execute('INSERT INTO observations VALUES(1,?,?)', encoded)
    c.execute('INSERT INTO ledger VALUES(1,5.0)')
    c.row_factory = sqlite3.Row
    cursor = c.execute('SELECT o.raw AS renamed,o.book,l.cash FROM observations o JOIN ledger l USING(id)')
    c.row_factory = None
    row = cursor.fetchone()
    assert isinstance(row, sqlite3.Row)
    assert dict(row) == {'renamed': '{"Yes":0.7}', 'book': b'book', 'cash': 5.0}
    c.row_factory = lambda cursor, row: {column[0]: value for column, value in zip(cursor.description, row)}
    assert c.execute('SELECT raw FROM observations').fetchone() == {'raw': '{"Yes":0.7}'}
    c.row_factory = None
    assert c.execute('SELECT raw FROM observations').fetchone() == ('{"Yes":0.7}',)
    c.close()


def test_missing_reader_and_bad_digest_are_not_silently_returned():
    store = MemoryStore()
    marker = PayloadReferences(store, store).encode_many(['raw'])[0]
    with pytest.raises(MissingMarketDataConfiguration):
        PayloadReferences().decode_many([marker])
    store.payloads[parse_reference(marker)[1]] = b'corrupted'
    with pytest.raises(ValueError, match='digest mismatch'):
        PayloadReferences(reader=store).decode_many([marker])


def test_rollback_does_not_leave_a_local_dangling_reference():
    store = MemoryStore()
    codec = PayloadReferences(store, store)
    c = connect(':memory:', references=codec)
    c.execute('CREATE TABLE public(id INTEGER PRIMARY KEY,raw TEXT)')
    c.execute('BEGIN')
    c.execute('INSERT INTO public VALUES(1,?)', codec.encode_many(['received']))
    c.rollback()
    assert c.execute('SELECT COUNT(*) FROM public').fetchone()[0] == 0
    assert list(store.payloads.values()) == [b'received']
    c.close()


def test_failed_shared_write_cannot_publish_local_reference():
    class FailedStore(MemoryStore):
        def put_many(self, values):
            raise OSError('disk full')
    codec = PayloadReferences(writer=FailedStore())
    with pytest.raises(OSError, match='disk full'):
        codec.encode_many(['book'])


def test_tiny_cache_and_duplicate_columns_preserve_order():
    store = MemoryStore()
    encoded = PayloadReferences(store, store).encode_many(['a'*100, 'b', 'a'*100, None])
    reader = PayloadReferences(reader=store, cache_bytes=2)
    assert reader.decode_many(encoded) == ['a'*100, 'b', 'a'*100, None]
    assert len(store.reads[-1]) == 2
    assert reader._cached_bytes <= 2


def test_migration_body_batch_uses_bounded_durable_transactions(monkeypatch):
    from polybot_observability import market_data_store
    from polybot_observability.market_data_refs import externalize_rows
    class CountedStore(MemoryStore):
        def __init__(self):super().__init__();self.batches=[]
        def put_many(self,values):
            self.batches.append(values)
            return super().put_many(values)
    store=CountedStore();codec=PayloadReferences(store,store)
    monkeypatch.setattr(market_data_store,'MAX_BATCH_BYTES',8)
    rows=[{'raw_gzip':b'abcd','private_value':n} for n in range(5)]
    encoded=externalize_rows('golden-coconut','requests',rows,references=codec)
    assert list(map(len,store.batches))==[2,2,1]
    assert [row['private_value'] for row in encoded]==list(range(5))
    assert codec.decode_many([row['raw_gzip'] for row in encoded])==[b'abcd']*5


def test_failed_later_batch_never_returns_publishable_local_rows(monkeypatch):
    from polybot_observability import market_data_store
    from polybot_observability.market_data_refs import externalize_rows
    class FailsSecond(MemoryStore):
        calls=0
        def put_many(self,values):
            self.calls+=1
            if self.calls==2:raise OSError('write failed')
            return super().put_many(values)
    store=FailsSecond();codec=PayloadReferences(store,store)
    monkeypatch.setattr(market_data_store,'MAX_BATCH_BYTES',4)
    with pytest.raises(OSError,match='write failed'):
        externalize_rows('golden-coconut','requests',[{'raw_gzip':b'abcd'},{'raw_gzip':b'efgh'}],references=codec)
    assert len(store.payloads)==1
