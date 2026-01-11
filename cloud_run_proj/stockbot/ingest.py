from __future__ import annotations
from datetime import date, timedelta
from typing import Iterable, Literal
from .config import Config
from .db import SupaDB
from .yf_client import fetch_daily_ohlc, FetchRange
from .fdr_client import fetch_daily_ohlc_kr


def _daterange_missing(last: date | None) -> tuple[date, date | None]:
    today = date.today()
    yesterday = today - timedelta(days=1)

    if last is None:
        return (yesterday - timedelta(days=400), yesterday)

    # 마지막 데이터 이후의 다음 날짜부터 어제까지
    next_date = last + timedelta(days=1)

    # 이미 최신 데이터인 경우 (다음 날짜가 어제보다 미래)
    if next_date > yesterday:
        return (yesterday, yesterday)  # 같은 날짜로 설정하여 빈 범위 생성

    return (next_date, yesterday)


def ingest_missing(
    conf: Config,
    only: Iterable[str] | None = None,
    market: Literal["us", "kr"] = "us"
) -> None:
    key = conf.supabase_service_role_key or conf.supabase_anon_key
    if not conf.supabase_url or not key:
        raise RuntimeError("Supabase URL/Key 설정이 필요합니다 (.env).")

    db = SupaDB(conf.supabase_url, key)

    # market에 따라 ticker 목록 및 fetch 함수 선택
    if market == "kr":
        tickers = tuple(only) if only else conf.kr_tickers
        fetch_func = fetch_daily_ohlc_kr
    else:
        tickers = tuple(only) if only else conf.tickers
        fetch_func = fetch_daily_ohlc

    db.ensure_tickers(tickers)

    for sym in tickers:
        last = db.get_last_date(sym)
        yesterday = date.today() - timedelta(days=1)

        # 어제 날짜 데이터가 이미 있는 경우 스킵
        if last is not None and last >= yesterday:
            print(f"[ingest] {sym}: already up to date (last: {last})")
            continue

        start, end = _daterange_missing(last)

        # FetchRange 생성 시 end가 None인 경우 처리
        fetch_end = end if end is None else end
        rows = fetch_func(sym, FetchRange(start=start, end=fetch_end))

        if rows:
            db.upsert_ohlc(rows)
            print(f"[ingest] {sym}: upsert {len(rows)} rows ({start}~{end or 'yesterday'})")
        else:
            print(f"[ingest] {sym}: no new rows")
