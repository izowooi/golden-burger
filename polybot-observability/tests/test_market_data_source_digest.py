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
