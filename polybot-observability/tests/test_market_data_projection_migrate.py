import sqlite3

import pytest

from polybot_observability.market_data_index import ReceiptContext
from polybot_observability.market_data_migrate import file_sha256, migrate_public_bodies
from polybot_observability.market_data_projection_profiles import SNAPSHOT_PROFILES, snapshot_profile
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_sqlite import connect
from polybot_observability.market_data_store import PayloadReader, PayloadStore


def source_database(path, strategy):
    profile = snapshot_profile(strategy)
    columns = list(profile.columns)
    # Model declaration order and historic ALTER order can differ.
    columns = [column for column in columns if column.name in ('id', 'condition_id', 'probability', 'timestamp')] + [
        column for column in columns if column.name not in ('id', 'condition_id', 'probability', 'timestamp')]
    clauses = [column.name + ' ' + column.affinity + (' PRIMARY KEY' if column.name == 'id' else ' NOT NULL' if not column.nullable else '')
               for column in columns]
    clauses += ['UNIQUE(' + ','.join(keys) + ')' for keys in profile.unique_constraints]
    with sqlite3.connect(path) as database:
        database.execute('CREATE TABLE market_snapshots(' + ','.join(clauses) + ')')
        database.execute('CREATE INDEX snapshot_condition_time ON market_snapshots(condition_id,timestamp)')
        database.execute('CREATE TABLE private_ledger(id INTEGER PRIMARY KEY,owner TEXT,pnl REAL)')
        database.execute("INSERT INTO private_ledger VALUES(7,'PRIVATE_ACCOUNT_SENTINEL',-12.5)")
        for number in (7, 8):
            values = dict.fromkeys(profile.column_names)
            values.update(id=number, condition_id='public-condition', probability=.81,
                          timestamp=f'2026-09-29 00:00:0{number}.000000', run_id=f'PRIVATE_RUN_{number}')
            if 'token_id' in values:
                values['token_id'], values['outcome'] = 'public-token', 'Yes'
            for name in profile.body_columns:
                values[name] = ' {"bids":[], "asks":[]} '
            for name in ('catalog_outcomes_json', 'catalog_outcome_prices_json', 'catalog_token_ids_json', 'catalog_tags_json'):
                if name in values:
                    values[name] = ' [ "공개", "원형" ] '
            names = [column.name for column in columns]
            database.execute('INSERT INTO market_snapshots VALUES(' + ','.join('?' for _ in names) + ')',
                             tuple(values[name] for name in names))


@pytest.mark.parametrize('strategy', sorted(SNAPSHOT_PROFILES))
def test_all_reviewed_projection_schemas_migrate_without_changing_context_or_ledger(tmp_path, strategy):
    source = tmp_path / 'source.db'
    source_database(source, strategy)
    digest = file_sha256(source)
    with sqlite3.connect(source) as original:
        expected = original.execute('SELECT * FROM market_snapshots ORDER BY id').fetchall()
        ledger = original.execute('SELECT * FROM private_ledger').fetchall()
    target = tmp_path / 'target.db'
    with PayloadStore(tmp_path / 'public.db') as writer, PayloadReader(tmp_path / 'public.db') as reader:
        refs = PayloadReferences(reader=reader, writer=writer)
        manifest = migrate_public_bodies(source, target, strategy=strategy, source_sha256=digest,
            references=refs, include_projections=True, receipt_context=ReceiptContext('source', 'default', 'job'))
        assert manifest['status'] == 'VERIFIED'
        assert manifest['tables']['market_snapshots']['externalized_projection_rows'] == 2
        assert manifest['public_projection_records']['receipt_count'] == 2 * len(snapshot_profile(strategy).groups)
        assert file_sha256(source) == digest
        restored = connect(target, references=PayloadReferences(reader=reader))
        try:
            assert restored.execute('SELECT * FROM market_snapshots ORDER BY id').fetchall() == expected
            assert restored.execute('SELECT * FROM private_ledger').fetchall() == ledger
        finally:
            restored.close()
        with sqlite3.connect(target) as raw:
            assert raw.execute('SELECT COUNT(*) FROM market_snapshots').fetchone()[0] == 0
            assert raw.execute('SELECT * FROM private_ledger').fetchall() == ledger


def test_projection_second_generation_requires_explicit_scope_and_preserves_public_membership(tmp_path):
    source = tmp_path / 'original.db'
    source_database(source, 'golden-blueberry')
    with PayloadStore(tmp_path / 'public.db') as writer, PayloadReader(tmp_path / 'public.db') as reader:
        refs = PayloadReferences(reader=reader, writer=writer)
        first = tmp_path / 'first.db'
        initial = migrate_public_bodies(source, first, strategy='golden-blueberry', source_sha256=file_sha256(source),
            references=refs, include_projections=True, receipt_context=ReceiptContext('source', 'default', 'job'))
        with pytest.raises(ValueError, match='explicit projection migration'):
            migrate_public_bodies(first, tmp_path / 'unapproved.db', strategy='golden-blueberry',
                                  source_sha256=file_sha256(first), references=refs)
        with pytest.raises(ValueError, match='existing source owner'):
            migrate_public_bodies(first, tmp_path / 'wrong-owner.db', strategy='golden-blueberry',
                                  source_sha256=file_sha256(first), references=refs, include_projections=True,
                                  receipt_context=ReceiptContext('other-source', 'default', 'job'))
        final = migrate_public_bodies(first, tmp_path / 'second.db', strategy='golden-blueberry',
            source_sha256=file_sha256(first), references=refs, include_projections=True)
        assert final['public_projection_records'] == initial['public_projection_records']
        assert final['tables']['market_snapshots']['logical_sha256'] == initial['tables']['market_snapshots']['logical_sha256']


def test_projection_body_only_declares_remaining_rows(tmp_path):
    source = tmp_path / 'original.db'
    source_database(source, 'golden-blueberry')
    with PayloadStore(tmp_path / 'public.db') as store:
        result = migrate_public_bodies(source, tmp_path / 'body-only.db', strategy='golden-blueberry',
            source_sha256=file_sha256(source), references=PayloadReferences(store, store))
    assert result['remaining_public_projection_rows'] == 2
    assert 'public_projection_records' not in result
