#!/usr/bin/env python3
"""Compatibility entry point; the installed ``polybot`` command is canonical."""

from polybot.main import main


if __name__ == "__main__":
    raise SystemExit(main())
