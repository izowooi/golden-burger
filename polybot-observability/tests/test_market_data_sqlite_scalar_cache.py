import sqlite3

from polybot_observability.market_data_sqlite import connect


def test_connection_and_explicit_cursor_shortcuts_clear_statement_cache():
    connection = connect(":memory:")
    calls = []
    connection._scalar_cache_clear = lambda: calls.append(True)
    try:
        connection.execute("CREATE TABLE values_fixture(value INTEGER)")
        connection.executemany("INSERT INTO values_fixture VALUES(?)", [(1,), (2,)])
        cursor = connection.cursor()
        cursor.execute("SELECT value FROM values_fixture ORDER BY value")
        assert cursor.fetchall() == [(1,), (2,)]
        cursor.executemany("INSERT INTO values_fixture VALUES(?)", [(3,), (4,)])
        assert len(calls) == 4
        assert connection.execute("SELECT COUNT(*) FROM values_fixture").fetchone() == (4,)
        assert len(calls) == 5
    finally:
        connection.close()


def test_row_factory_capture_and_nested_cursor_aliases_are_unchanged():
    connection = connect(":memory:")
    try:
        connection.executescript("CREATE TABLE x(value INTEGER);INSERT INTO x VALUES(1),(2),(3);")
        connection.row_factory = sqlite3.Row
        outer = connection.cursor()
        connection.row_factory = None
        outer.execute("SELECT value AS original_alias FROM x ORDER BY value")
        assert outer.fetchone()["original_alias"] == 1
        assert connection.execute(
            "SELECT value AS nested_alias FROM x WHERE value=2"
        ).fetchone() == (2,)
        assert [row["original_alias"] for row in outer.fetchall()] == [2, 3]
    finally:
        connection.close()


def test_script_disables_cache_between_unknown_statement_boundaries():
    connection = connect(":memory:")
    flags = []
    connection.create_function(
        "observe_cache", 0, lambda: flags.append(connection._scalar_cache_enabled) or 1
    )
    try:
        connection.executescript("SELECT observe_cache();SELECT observe_cache();")
        assert flags == [False, False]
        assert connection._scalar_cache_enabled is True
        connection.cursor().executescript("SELECT observe_cache();")
        assert flags[-1] is False
        assert connection._scalar_cache_enabled is True
    finally:
        connection.close()


def test_custom_native_cursor_keeps_compatibility_and_disables_scalar_cache():
    connection = connect(":memory:")
    try:
        cursor = connection.cursor(factory=sqlite3.Cursor)
        assert cursor.execute("SELECT 7 AS alias").fetchone() == (7,)
        assert connection._scalar_cache_enabled is False
    finally:
        connection.close()
