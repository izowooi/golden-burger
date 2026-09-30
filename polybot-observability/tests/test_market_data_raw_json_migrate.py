"""Audited standalone source copies migrate without normalizing JSON or scalars."""
import sqlite3

import pytest

from polybot_observability.market_data_bundle import reference_closure
from polybot_observability.market_data_migrate import file_sha256, migrate_public_bodies
from polybot_observability.market_data_refs import PayloadReferences, parse_reference
from polybot_observability.market_data_sqlite import connect
from test_market_data_mixed import MemoryStore


@pytest.mark.parametrize("strategy,table,columns", [
    ("golden-watermelon", "event_observations", ("sport_json", "tags_json", "series_json", "teams_json")),
    ("golden-black", "resolution_observations", ("evidence_json",)),
    ("golden-coconut", "event_observations", ("sport_json",)),
    ("golden-coconut", "game_lifecycle_observations", ("raw_lifecycle_json",)),
    ("golden-coconut", "resolution_observations", ("evidence_json",)),
    ("golden-pomegranate", "market_observations", ("fee_metadata_json",)),
    ("golden-guava", "events", ("clock_json",)),
])
def test_raw_json_migration_preserves_source_bytes_null_and_private_columns(tmp_path, strategy, table, columns):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    values = (' { "number" : 1e-02, "escaped":"\\u0031" } ', '[null,"source",-0.0]', '12.50', 'null', None)
    with sqlite3.connect(source) as db:
        db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY, {",".join(c + " TEXT" for c in columns)}, raw_scalar REAL, classification_json TEXT)')
        for index, value in enumerate(values):
            db.execute(f'INSERT INTO {table} VALUES({",".join("?" for _ in range(len(columns)+3))})',
                       (index, *(value for _ in columns), 12.5, '{"derived_fee":"PRIVATE","eligibility":false}'))
    digest = file_sha256(source)
    store = MemoryStore()
    refs = PayloadReferences(store, store)
    result = migrate_public_bodies(source, target, strategy=strategy, source_sha256=digest, references=refs)
    assert result["status"] == "VERIFIED" and file_sha256(source) == digest
    with sqlite3.connect(target) as db:
        rows = db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
    for index, row in enumerate(rows):
        assert row[0] == index and row[-2:] == (12.5, '{"derived_fee":"PRIVATE","eligibility":false}')
        assert all(parse_reference(value) if values[index] is not None else value is None for value in row[1:-2])
    with connect(target, references=refs) as db:
        decoded = db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY id').fetchall()
    assert decoded == [tuple(value for _ in columns) for value in values]
    assert reference_closure(target, strategy) == sorted(store.payloads)
