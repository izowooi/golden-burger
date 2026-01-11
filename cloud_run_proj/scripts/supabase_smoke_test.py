from __future__ import annotations
import argparse
import os
import random
import string
from datetime import date, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv
from supabase import create_client, Client


def _rand_suffix(n: int = 6) -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))


def validate_env(supabase_url: str, service_role_key: Optional[str], anon_key: Optional[str]) -> list[str]:
    problems: list[str] = []
    if not supabase_url:
        problems.append("SUPABASE_URL is empty")
    elif not supabase_url.startswith("https://"):
        problems.append("SUPABASE_URL must start with https://")
    if supabase_url.endswith('/'):
        problems.append("SUPABASE_URL should not end with a trailing slash")
    if not service_role_key and not anon_key:
        problems.append("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY is required")
    return problems


def create_supabase_client(url: str, service_role_key: Optional[str], anon_key: Optional[str], prefer_service_role: bool = True) -> Client:
    key = service_role_key if (prefer_service_role and service_role_key) else (service_role_key or anon_key)
    if not url or not key:
        raise RuntimeError("Supabase URL/Key missing for client creation")
    return create_client(url, key)


def check_table_exists(client: Client, table: str) -> tuple[bool, str]:
    try:
        client.table(table).select("*").limit(1).execute()
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "404" in msg or "Resource not found" in msg:
            return False, "not_found"
        return False, msg


def upsert_sample_rows(client: Client, suffix: str) -> None:
    today = date.today()
    ticker_a = f"ZZZ_SMOKE_{suffix}_A"
    ticker_b = f"ZZZ_SMOKE_{suffix}_B"

    # tickers
    client.table("tickers").upsert([
        {"ticker": ticker_a, "name": "SMOKE A", "exchange": "TEST"},
        {"ticker": ticker_b, "name": "SMOKE B", "exchange": "TEST"},
    ], on_conflict="ticker").execute()

    # ohlc_daily
    client.table("ohlc_daily").upsert([
        {"ticker": ticker_a, "d": today.isoformat(), "close": 123.45},
        {"ticker": ticker_b, "d": (today - timedelta(days=1)).isoformat(), "close": 67.89},
    ], on_conflict="ticker,d").execute()

    # signals
    client.table("signals").upsert([
        {
            "ticker": ticker_a,
            "d": today.isoformat(),
            "signal_type": "golden_cross",
            "price": 123.45,
            "sma5": 120.0,
            "sma60": 110.0,
        }
    ], on_conflict="ticker,d,signal_type").execute()


def cleanup_sample_rows(client: Client, suffix: str) -> None:
    like_pattern = f"ZZZ_SMOKE_{suffix}%"
    client.table("signals").delete().like("ticker", like_pattern).execute()
    client.table("ohlc_daily").delete().like("ticker", like_pattern).execute()
    client.table("tickers").delete().like("ticker", like_pattern).execute()


def run_sql_via_postgres_api(url: str, service_role_key: str, sql: str) -> tuple[bool, str]:
    endpoint = url.rstrip('/') + "/postgres/v1/query"
    headers = {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(endpoint, headers=headers, json={"query": sql}, timeout=30)
        if resp.status_code >= 200 and resp.status_code < 300:
            return True, "ok"
        return False, f"{resp.status_code}: {resp.text}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


SCHEMA_SQL = """
create table if not exists public.tickers (
  ticker text primary key,
  name   text,
  exchange text default 'NASDAQ'
);

create table if not exists public.ohlc_daily (
  ticker text references public.tickers(ticker) on delete cascade,
  d date not null,
  open  double precision,
  high  double precision,
  low   double precision,
  close double precision not null,
  volume bigint,
  primary key (ticker, d)
);

create index if not exists idx_ohlc_daily_ticker_date_desc
  on public.ohlc_daily (ticker, d desc);

create table if not exists public.signals (
  id bigserial primary key,
  ticker text not null references public.tickers(ticker) on delete cascade,
  d date not null,
  signal_type text not null check (signal_type in ('golden_cross','dead_cross')),
  price double precision not null,
  sma5 double precision not null,
  sma60 double precision not null,
  created_at timestamptz not null default now(),
  unique (ticker, d, signal_type)
);
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Supabase smoke test (env, schema, CRUD)")
    parser.add_argument("--apply-schema", action="store_true", help="Apply schema via Postgres API (service role key required)")
    parser.add_argument("--schema-path", type=str, default="schema.sql", help="Path to schema.sql; if missing, use built-in schema")
    parser.add_argument("--use-anon", action="store_true", help="Force using ANON key for CRUD tests")
    parser.add_argument("--skip-writes", action="store_true", help="Skip write/upsert tests")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup sample rows after tests")
    args = parser.parse_args()

    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL", "")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    anon_key = os.getenv("SUPABASE_ANON_KEY")

    problems = validate_env(supabase_url, service_role_key, anon_key)
    if problems:
        print("[env] invalid:")
        for p in problems:
            print(f" - {p}")
        return 2

    prefer_service_role = not args.use_anon
    client = create_supabase_client(supabase_url, service_role_key, anon_key, prefer_service_role=prefer_service_role)
    print(f"[env] url ok; key used: {'service_role' if (prefer_service_role and service_role_key) else ('service_role' if service_role_key and not anon_key else 'anon')}\n")

    # Optionally apply schema
    if args.apply_schema:
        if not service_role_key:
            print("[schema] service role key required to apply schema")
            return 3
        sql = SCHEMA_SQL
        if args.schema_path and os.path.exists(args.schema_path):
            try:
                with open(args.schema_path, "r", encoding="utf-8") as f:
                    sql = f.read()
            except Exception as e:  # noqa: BLE001
                print(f"[schema] failed to read {args.schema_path}: {e}; using built-in schema")
        ok, msg = run_sql_via_postgres_api(supabase_url, service_role_key, sql)
        print(f"[schema] apply: {'ok' if ok else 'fail'} ({msg})\n")
        if not ok:
            return 4

    # Check table existence
    for t in ("tickers", "ohlc_daily", "signals"):
        exists, info = check_table_exists(client, t)
        print(f"[read] table '{t}': {'exists' if exists else 'missing'} ({info})")

    # CRUD writes
    suffix = _rand_suffix()
    if not args.skip_writes:
        try:
            upsert_sample_rows(client, suffix)
            print("[write] upsert sample rows: ok")
        except Exception as e:  # noqa: BLE001
            print(f"[write] upsert sample rows: fail ({e})")
            return 5

    # Simple reads back
    try:
        res = client.table("tickers").select("ticker").like("ticker", f"ZZZ_SMOKE_{suffix}%").execute()
        count = len(res.data or [])
        print(f"[read] fetched {count} sample tickers")
    except Exception as e:  # noqa: BLE001
        print(f"[read] fetch sample: fail ({e})")
        return 6

    # Optional cleanup
    if args.cleanup and not args.skip_writes:
        try:
            cleanup_sample_rows(client, suffix)
            print("[cleanup] sample rows removed")
        except Exception as e:  # noqa: BLE001
            print(f"[cleanup] failed: {e}")

    print("\n[done] Supabase smoke test completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


