"""Scoped read-only public payload access for repository analysis tools.

Installed ``polybot-observability`` is preferred. Direct repository script and
importlib execution may load that same checked-in package from its source tree;
this bootstrap never loads a strategy runtime or creates a database.
"""

from __future__ import annotations

from pathlib import Path
import sys

try:
    from polybot_observability.market_data_reader import (
        add_public_store_argument, market_data_connect, public_references,
    )
except ModuleNotFoundError as error:
    if error.name != "polybot_observability":
        raise
    package_source = Path(__file__).resolve().parents[1] / "polybot-observability" / "src"
    if not package_source.is_dir():
        raise
    sys.path.insert(0, str(package_source))
    from polybot_observability.market_data_reader import (
        add_public_store_argument, market_data_connect, public_references,
    )


__all__ = ["add_public_store_argument", "market_data_connect", "public_references"]
