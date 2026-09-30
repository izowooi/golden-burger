"""Runtime maintenance checks its own DB, not every attached public store."""
import sqlite3

import pytest

from polybot_observability.sqlite_maintenance import _quick_check


def connection_with_attachment():
    connection = sqlite3.connect(':memory:')
    connection.execute("ATTACH ':memory:' AS public_data")
    for schema in ('main', 'public_data'):
        connection.execute(f'CREATE TABLE {schema}.checked(value INTEGER CHECK(value>0))')
        connection.execute(f'INSERT INTO {schema}.checked VALUES(1)')
    return connection


def test_runtime_check_does_not_walk_unrelated_attached_database():
    connection = connection_with_attachment()
    try:
        connection.execute('PRAGMA ignore_check_constraints=ON')
        connection.execute('INSERT INTO public_data.checked VALUES(-1)')
        connection.execute('PRAGMA ignore_check_constraints=OFF')
        assert connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]
        _quick_check(connection)
    finally:
        connection.close()


def test_runtime_corruption_still_fails_with_public_attachment_present():
    connection = connection_with_attachment()
    try:
        connection.execute('PRAGMA ignore_check_constraints=ON')
        connection.execute('INSERT INTO main.checked VALUES(-1)')
        connection.execute('PRAGMA ignore_check_constraints=OFF')
        with pytest.raises(RuntimeError, match='quick_check failed'):
            _quick_check(connection)
    finally:
        connection.close()
