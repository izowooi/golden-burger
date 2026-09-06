from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
from tests.test_repository import repository


def test_writer_reuse_preserves_commits_rollback_and_thread_serialization(tmp_path):
    path = tmp_path / 'test.db'
    repo = repository(path)
    try:
        with repo.connect() as con:
            first = con
            con.execute('CREATE TABLE writer_fixture (id INTEGER PRIMARY KEY)')
            con.execute('INSERT INTO writer_fixture VALUES (1)')
        with sqlite3.connect(path) as check:
            assert check.execute('SELECT count(*) FROM writer_fixture').fetchone()[0] == 1
        with pytest.raises(ValueError):
            with repo.connect() as con:
                assert con is first
                con.execute('INSERT INTO writer_fixture VALUES (2)')
                raise ValueError('fixture rollback')
        def insert(i):
            with repo.connect() as con:
                assert con is first
                con.execute('INSERT INTO writer_fixture VALUES (?)', (i,))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(insert, range(3, 23)))
        with repo.connect() as con:
            assert con.execute('SELECT count(*) FROM writer_fixture').fetchone()[0] == 21
        repo.close()
        with pytest.raises(sqlite3.ProgrammingError):
            first.execute('SELECT 1')
        with repo.connect() as con:
            assert con is not first
            assert con.execute('SELECT count(*) FROM writer_fixture').fetchone()[0] == 21
    finally:
        repo.close()


def test_collector_can_defer_full_scan_without_claiming_integrity(tmp_path, monkeypatch):
    repo = repository(tmp_path / 'deferred.db')
    try:
        monkeypatch.setattr(repo, 'quick_check', lambda: pytest.fail('full DB scan inside collection'))
        result = repo.scheduled_database_check('test-run', allow_full_check=False)
        assert result['full_check_performed'] is False
        assert result['full_check_due'] is True
        assert result['mode'] == 'LIGHTWEIGHT_PROBE'
    finally:
        repo.close()
