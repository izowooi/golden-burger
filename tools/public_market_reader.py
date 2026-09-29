"""Scoped read-only public payload access for repository analysis tools.

Installed ``polybot-observability`` is preferred. Direct repository script and
importlib execution may load that same checked-in package from its source tree;
this bootstrap never loads a strategy runtime or creates a database.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys

try:
    from polybot_observability.market_data_refs import PayloadReferences
    from polybot_observability.market_data_sqlite import connect as market_data_connect
    from polybot_observability.market_data_store import PayloadReader
except ModuleNotFoundError as error:
    if error.name != "polybot_observability":
        raise
    package_source = Path(__file__).resolve().parents[1] / "polybot-observability" / "src"
    if not package_source.is_dir():
        raise
    sys.path.insert(0, str(package_source))
    from polybot_observability.market_data_refs import PayloadReferences
    from polybot_observability.market_data_sqlite import connect as market_data_connect
    from polybot_observability.market_data_store import PayloadReader


def add_public_store_argument(parser):
    parser.add_argument(
        "--public-store", type=Path,
        help="Existing absolute public payload DB; defaults to PUBLIC_MARKET_DATA_DB",
    )


@contextmanager
def public_references(public_store=None):
    """Use one explicit read-only reader; never change process environment."""
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
