"""SQLite readers that resolve shared public bodies before applying row factories.

sqlite3.Row, raw SQL joins, aliases and ORM queries receive original TEXT/BLOB
values. Public level/scalar layouts install connection-local read views while
the original main schema and private ledgers remain in their runtime database.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .market_data_refs import PayloadReferences, configured_references, is_external_value


def expand_private_values(connection, values):
    """Expand DB-local packet pointers without fetching public source fragments."""
    from .market_data_refs import _is_local_packet
    if not any(_is_local_packet(value) for value in values):
        return values
    from .market_data_private_packets import PrivatePacketReader
    reader = getattr(connection, '_private_packet_reader', None)
    if reader is None:
        reader = PrivatePacketReader(connection)
        if hasattr(connection, '_private_packet_reader'):
            connection._private_packet_reader = reader
    return [reader.resolve(value) if _is_local_packet(value) else value
            for value in values]


class ResolvingCursor(sqlite3.Cursor):
    def execute(self, *args, **kwargs):
        self.connection._clear_scalar_cache()
        return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        self.connection._clear_scalar_cache()
        return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        connection = self.connection
        connection._clear_scalar_cache()
        enabled, connection._scalar_cache_enabled = connection._scalar_cache_enabled, False
        try:
            return super().executescript(*args, **kwargs)
        finally:
            connection._scalar_cache_enabled = enabled
            connection._clear_scalar_cache()


class ResolvingConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._references: PayloadReferences | None = None
        self._consumer_row_factory = None
        self._scalar_cache_clear = None
        self._scalar_cache_enabled = True
        self._scalar_view_binding = None
        self._projection_cache_clear = None
        self._projection_view_binding = None
        self._catalog_cache_clear = None
        self._catalog_view_binding = None
        self._raw_cache_clear = None
        self._private_packet_reader = None
        sqlite3.Connection.row_factory.__set__(self, self._make_row_factory(None))

    def _clear_scalar_cache(self):
        if self._scalar_cache_clear is not None:
            self._scalar_cache_clear()
        if self._projection_cache_clear is not None:
            self._projection_cache_clear()
        if self._catalog_cache_clear is not None:
            self._catalog_cache_clear()
        if self._raw_cache_clear is not None:
            self._raw_cache_clear()
        if self._private_packet_reader is not None:
            self._private_packet_reader.clear()

    def cursor(self, factory=None):
        if factory is not None and not (
            isinstance(factory, type) and issubclass(factory, ResolvingCursor)
        ):
            self._scalar_cache_enabled = False
            self._clear_scalar_cache()
        return super().cursor(factory or ResolvingCursor)

    def execute(self, *args, **kwargs):
        return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self.cursor().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self.cursor().executescript(*args, **kwargs)

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
            row = tuple(expand_private_values(self, row))
            if any(is_external_value(value) for value in row):
                codec = self._references or configured_references()
                row = tuple(codec.decode_many(row))
            return factory(cursor, row) if factory else row
        return resolve_row


def connect(database, *args, references: PayloadReferences | None = None, **kwargs):
    if "factory" in kwargs:
        raise ValueError("custom connection factory must implement public payload resolution")
    # URI support also governs later ATTACH statements; scalar views must open
    # the shared database with mode=ro even when the main path is an ordinary path.
    kwargs.setdefault("uri", True)
    connection = sqlite3.connect(database, *args, factory=ResolvingConnection, **kwargs)
    connection._references = references
    try:
        from .market_data_levels import install_level_views
        install_level_views(connection, references=references)
        from .market_data_scalar_links import install_scalar_views
        install_scalar_views(connection, references=references)
        from .market_data_projection_links import install_projection_views
        install_projection_views(connection, references=references)
        from .market_data_catalog_links import install_catalog_views
        install_catalog_views(connection, references=references)
        from .market_data_raw_links import install_raw_views
        install_raw_views(connection, references=references)
    except BaseException:
        connection.close()
        raise
    return connection
