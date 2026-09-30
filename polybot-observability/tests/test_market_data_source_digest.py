import hashlib

import pytest

from polybot_observability.market_data_source_digest import MARKET_DATA_SOURCE_FILES,update_digest


def test_reader_and_writer_changes_split_source_cohort(tmp_path):
    for name in MARKET_DATA_SOURCE_FILES:
        (tmp_path/name).write_text('original\n')
    def digest():
        value=hashlib.sha256();update_digest(value,tmp_path);return value.hexdigest()
    initial=digest()
    (tmp_path/'market_data_store.py').write_text('writer changed\n')
    assert digest()!=initial
    second=digest()
    (tmp_path/'market_data_refs.py').write_text('reader changed\n')
    assert digest()!=second
    third=digest()
    (tmp_path/'unrelated_report.md').write_text('unrelated\n')
    assert digest()==third


def test_missing_runtime_file_cannot_claim_same_source(tmp_path):
    with pytest.raises(ValueError,match='missing'):
        update_digest(hashlib.sha256(),tmp_path)


@pytest.mark.parametrize('name', [
    'market_data_raw_profiles.py', 'market_data_raw_links.py', 'market_data_migrate.py',
    'market_data_private_packets.py', 'market_data_raw_schema_watermelon.py',
    'market_data_raw_schema_coconut.py',
    'market_data_raw_schema_pomegranate.py',
    'market_data_sql_schema.py',
])
def test_raw_projection_and_private_ownership_changes_split_source_cohort(tmp_path, name):
    assert name in MARKET_DATA_SOURCE_FILES
    for source in MARKET_DATA_SOURCE_FILES:
        (tmp_path / source).write_text('original\n')
    before = hashlib.sha256()
    update_digest(before, tmp_path)
    (tmp_path / name).write_text('changed storage behavior\n')
    after = hashlib.sha256()
    update_digest(after, tmp_path)
    assert before.hexdigest() != after.hexdigest()
