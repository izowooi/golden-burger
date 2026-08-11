#!/usr/bin/env python3
"""Jenkins entry point for the daily host filesystem monitor."""

from pathlib import Path

from dotenv import load_dotenv

from polybot_reporter.storage.host_storage import main

load_dotenv(Path(__file__).parent / ".env")


if __name__ == "__main__":
    raise SystemExit(main())
