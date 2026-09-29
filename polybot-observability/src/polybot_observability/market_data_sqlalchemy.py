"""SQLAlchemy body references without changing SQL types or strategy ownership.

Only the explicit market_data_policy columns are wrapped. Install after model
declaration and before engine/schema creation. Raw SQL *reads* also need the
ResolvingConnection DBAPI factory; an untyped text() INSERT/UPDATE deliberately
does not gain write interception from a SQLAlchemy type decorator.

SQLAlchemy remains an optional consumer dependency: importing the base
polybot_observability package does not import this module.
"""

from __future__ import annotations

import os

from sqlalchemy import LargeBinary, String
from sqlalchemy.sql.schema import MetaData
from sqlalchemy.types import TypeDecorator, TypeEngine

from .market_data_policy import KNOWN_STRATEGIES, public_columns
from .market_data_refs import (
    MissingMarketDataConfiguration,
    configured_references,
    parse_reference,
)


class PublicPayloadType(TypeDecorator):
    """Preserve the original SQL DDL while externalizing exact TEXT/BLOB bytes."""

    impl = String
    cache_ok = True

    def __init__(self, original_type: TypeEngine, strategy: str,
                 table: str, column: str):
        if not isinstance(original_type, (String, LargeBinary)):
            raise TypeError(f"public body column must be TEXT/BLOB: {table}.{column}")
        super().__init__()
        self.original_type = original_type
        self.impl = original_type
        self.strategy = strategy
        self.table = table
        self.column = column

    def process_bind_param(self, value, dialect):
        codec = configured_references()
        if codec.writer is None:
            if (os.environ.get("PUBLIC_MARKET_DATA_REQUIRED") == "1"
                    or os.environ.get("PUBLIC_MARKET_DATA_DB")
                    or os.environ.get("PUBLIC_MARKET_DATA_SOCKET")):
                raise MissingMarketDataConfiguration(
                    "public body write requires the configured market-data writer"
                )
            if parse_reference(value):
                # Do not persist an unverified marker during legacy inline use.
                return codec.decode_many([value])[0]
            return value
        return codec.encode_many([value])[0]

    def process_result_value(self, value, dialect):
        if parse_reference(value):
            return configured_references().decode_many([value])[0]
        # ResolvingConnection may already have restored this value. Inlined
        # legacy values, empty bodies and NULL need no I/O or reinterpretation.
        return value


def original_public_type(type_: TypeEngine) -> TypeEngine:
    """Allow existing schema-affinity validators to inspect the original type."""
    return type_.original_type if isinstance(type_, PublicPayloadType) else type_


def install_public_types(metadata: MetaData, strategy: str) -> int:
    """Wrap existing allowlisted columns once; return the number newly wrapped.

    No columns or tables are created. A historical schema may expose only part
    of an allowlisted family. A conflicting owner is an error rather than an
    opportunity to relabel an existing metadata object.
    """
    if strategy not in KNOWN_STRATEGIES:
        raise ValueError(f"unknown market-data strategy: {strategy}")
    configured_references()  # REQUIRED=1 must fail before DB creation/maintenance.
    selected = []
    for table in metadata.tables.values():
        allowed = public_columns(strategy, table.name)
        for column in table.columns:
            existing = column.type
            if isinstance(existing, PublicPayloadType):
                if (existing.strategy, existing.table, existing.column) != (
                        strategy, table.name, column.name):
                    raise ValueError("SQLAlchemy public payload metadata has a different owner")
                continue
            if column.name in allowed:
                selected.append((column, PublicPayloadType(
                    existing, strategy, table.name, column.name,
                )))
    # Validate all targets before mutating metadata so a bad type cannot leave
    # a partially installed policy behind.
    for column, type_ in selected:
        column.type = type_
    return len(selected)
