"""SQLite readers that resolve shared public bodies before applying row factories.

No SQL or schema rewriting occurs. sqlite3.Row, raw SQL joins, aliases and ORM
queries receive the original TEXT/BLOB values, while ledger values are untouched.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .market_data_refs import PayloadReferences, configured_references, parse_reference


class ResolvingConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._references: PayloadReferences | None = None
        self._consumer_row_factory = None
        sqlite3.Connection.row_factory.__set__(self, self._make_row_factory(None))

    @property
    def row_factory(self):
        return self._consumer_row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._consumer_row_factory = value
        sqlite3.Connection.row_factory.__set__(self, self._make_row_factory(value))

    def _make_row_factory(self, factory):
        # sqlite3 captures row_factory when each cursor is created. Keep that
        # behavior when callers change the connection factory mid-iteration.
        def resolve_row(cursor: sqlite3.Cursor, row: tuple):
            if any(parse_reference(value) for value in row):
                codec = self._references or configured_references()
                row = tuple(codec.decode_many(row))
            return factory(cursor, row) if factory else row
        return resolve_row


def connect(database, *args, references: PayloadReferences | None = None, **kwargs):
    if "factory" in kwargs:
        raise ValueError("custom connection factory must implement public payload resolution")
    connection = sqlite3.connect(database, *args, factory=ResolvingConnection, **kwargs)
    connection._references = references
    try:
        from .market_data_levels import install_level_views
        install_level_views(connection, references=references)
    except BaseException:
        connection.close()
        raise
    return connection
