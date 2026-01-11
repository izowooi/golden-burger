from __future__ import annotations
import argparse
from stockbot.config import Config
from stockbot.ingest import ingest_missing
from stockbot.signals import run_signal_detection

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MA(5/60) Cross Signal Bot with Supabase")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="Fetch & upsert missing OHLC rows")
    p_ing.add_argument("--tickers", type=str, help="Comma-separated symbols (e.g., MSFT,AMZN or 360750,133690)")
    p_ing.add_argument("--market", type=str, default="us", choices=["us", "kr"],
                       help="Market: 'us' (NASDAQ via Yahoo Finance) or 'kr' (Korea ETF via FinanceDataReader)")

    p_sig = sub.add_parser("signals", help="Detect cross signals & notify")
    p_sig.add_argument("--tickers", type=str, help="Comma-separated symbols")
    p_sig.add_argument("--market", type=str, default="us", choices=["us", "kr"],
                       help="Market: 'us' (NASDAQ) or 'kr' (Korea ETF)")
    p_sig.add_argument("--dry-run", action="store_true", help="Do not send notifications")
    p_sig.add_argument("--debug", action="store_true", help="Enable debug mode (force cross signals)")

    return p.parse_args()

def _split(s: str | None, upper: bool = True) -> list[str] | None:
    if not s:
        return None
    if upper:
        return [x.strip().upper() for x in s.split(",") if x.strip()]
    return [x.strip() for x in s.split(",") if x.strip()]

def main() -> None:
    args = parse_args()
    conf = Config.from_env()

    market = getattr(args, 'market', 'us')
    upper_case = (market == "us")  # KR은 숫자이므로 upper 불필요

    if args.cmd == "ingest":
        ingest_missing(conf, _split(args.tickers, upper=upper_case), market=market)
    elif args.cmd == "signals":
        run_signal_detection(
            conf,
            _split(args.tickers, upper=upper_case),
            dry_run=args.dry_run,
            debug_mode=getattr(args, 'debug', False),
            market=market
        )
    else:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
