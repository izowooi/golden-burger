#!/usr/bin/env python3
"""Development entry point for Golden Pomegranate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from polybot.main import main


if __name__ == "__main__":
    raise SystemExit(main())
