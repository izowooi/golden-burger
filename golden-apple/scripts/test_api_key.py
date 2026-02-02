#!/usr/bin/env python3
"""Test script for Polymarket API credentials.

This script verifies:
1. Environment variables are set
2. Gamma API (public) is accessible
3. CLOB API authentication works
4. Order query functionality

Usage:
    python scripts/test_api_key.py
"""
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv


def print_result(success: bool, message: str):
    """Print test result with status indicator."""
    status = "[O]" if success else "[X]"
    print(f"  {status} {message}")


def test_api_connection() -> bool:
    """Run API connection tests."""
    load_dotenv()

    print("=" * 50)
    print("Polymarket API Connection Test")
    print("=" * 50)
    print()

    all_passed = True

    # Test 1: Environment Variables
    print("[1] Checking environment variables...")

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS")

    if not private_key:
        print_result(False, "POLYMARKET_PRIVATE_KEY is not set")
        print("\n    Set it in .env file:")
        print("    POLYMARKET_PRIVATE_KEY=your_private_key_here")
        all_passed = False
    else:
        # Mask key for display
        masked = private_key[:10] + "..." + private_key[-4:] if len(private_key) > 14 else "***"
        print_result(True, f"Private Key: {masked}")

    if not funder_address:
        print_result(False, "POLYMARKET_FUNDER_ADDRESS is not set")
        print("\n    Set it in .env file:")
        print("    POLYMARKET_FUNDER_ADDRESS=0xYourAddress")
        all_passed = False
    else:
        print_result(True, f"Funder Address: {funder_address}")

    if not all_passed:
        print("\n[!] Fix environment variables and try again")
        return False

    print()

    # Test 2: Gamma API (no auth required)
    print("[2] Testing Gamma API (public)...")

    try:
        import requests

        resp = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 1, "active": "true"},
            timeout=10
        )
        resp.raise_for_status()

        markets = resp.json()
        print_result(True, f"Market data retrieved: {len(markets)} market(s)")

        if markets:
            sample = markets[0]
            print(f"      Sample: {sample.get('question', 'N/A')[:50]}...")

    except Exception as e:
        print_result(False, f"Gamma API error: {e}")
        all_passed = False

    print()

    # Test 3: CLOB Client Authentication
    print("[3] Testing CLOB API authentication...")

    try:
        from py_clob_client.client import ClobClient

        # Remove 0x prefix if present
        key = private_key[2:] if private_key.startswith("0x") else private_key

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=key,
            chain_id=137,  # Polygon Mainnet
            signature_type=1,  # Magic.Link
            funder=funder_address,
        )

        # Create/derive API credentials
        api_creds = client.create_or_derive_api_creds()
        print_result(True, f"API Key derived: {api_creds.api_key[:20]}...")

        # Set credentials
        client.set_api_creds(api_creds)
        print_result(True, "Credentials set successfully")

    except ImportError:
        print_result(False, "py-clob-client not installed")
        print("\n    Install with: pip install py-clob-client")
        all_passed = False
    except Exception as e:
        print_result(False, f"CLOB authentication error: {e}")
        all_passed = False

    print()

    # Test 4: Order Query (requires auth)
    if all_passed:
        print("[4] Testing order query...")

        try:
            orders = client.get_orders()
            print_result(True, f"Open orders: {len(orders)}")

        except Exception as e:
            print_result(False, f"Order query error: {e}")
            all_passed = False

    print()

    # Summary
    print("=" * 50)
    if all_passed:
        print("All tests passed! API connection is working.")
        print()
        print("You can now run the bot:")
        print("  python -m polybot run --simulate  # Simulation mode")
        print("  python -m polybot run             # Live trading")
    else:
        print("Some tests failed. Please check your configuration.")
    print("=" * 50)

    return all_passed


if __name__ == "__main__":
    success = test_api_connection()
    sys.exit(0 if success else 1)
