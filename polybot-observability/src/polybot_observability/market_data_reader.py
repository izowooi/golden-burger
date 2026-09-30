"""Scoped read-only public payload access for analyzers and replay commands."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from .market_data_refs import PayloadReferences
from .market_data_sqlite import connect as market_data_connect
from .market_data_store import PayloadReader


def add_public_store_argument(parser):
    parser.add_argument(
        "--public-store", type=Path,
        help="Existing absolute public payload DB; defaults to PUBLIC_MARKET_DATA_DB",
    )


@contextmanager
def public_references(public_store=None):
    """Keep the reader scoped to the operation without changing environment."""
    configured = public_store if public_store is not None else os.environ.get("PUBLIC_MARKET_DATA_DB")
    if not configured:
        yield PayloadReferences()
        return
    path = Path(configured).expanduser()
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("public payload DB must be an existing canonical absolute path")
    with PayloadReader(path) as reader:
        yield PayloadReferences(reader=reader)


__all__ = ["add_public_store_argument", "market_data_connect", "public_references"]
