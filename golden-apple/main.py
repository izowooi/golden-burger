#!/usr/bin/env python3
"""Entry point for the Polymarket trading bot.

Usage:
    python main.py run              # Run trading cycle
    python main.py run --simulate   # Run in simulation mode
    python main.py status           # Check status
    python main.py config           # Show configuration
"""
import sys
import os

# Add src to path for development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from polybot.main import main

if __name__ == "__main__":
    main()
