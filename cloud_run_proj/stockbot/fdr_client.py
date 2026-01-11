from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import pandas as pd
import FinanceDataReader as fdr
from .db import OHLC


@dataclass(frozen=True)
class FetchRange:
    start: date
    end: date | None  # inclusive end; None=through today

    def __post_init__(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("end must be >= start")


def fetch_daily_ohlc_kr(ticker: str, rng: FetchRange) -> list[OHLC]:
    """
    FinanceDataReader를 사용하여 한국 ETF OHLC 데이터 조회

    Args:
        ticker: 6자리 종목코드 (예: '360750')
        rng: 조회 기간

    Returns:
        list[OHLC]: OHLC 데이터 리스트
    """
    start = rng.start.isoformat()
    end = rng.end.isoformat() if rng.end else None

    df = fdr.DataReader(ticker, start, end)

    if df.empty:
        return []

    df = df.reset_index()

    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df["d"] = pd.to_datetime(df[date_col]).dt.date

    rows: list[OHLC] = []
    for rec in df.to_dict("records"):
        rows.append(OHLC(
            ticker=ticker,
            d=rec["d"],
            open=float(rec["Open"]) if pd.notna(rec.get("Open")) else None,
            high=float(rec["High"]) if pd.notna(rec.get("High")) else None,
            low=float(rec["Low"]) if pd.notna(rec.get("Low")) else None,
            close=float(rec["Close"]),
            volume=int(rec["Volume"]) if pd.notna(rec.get("Volume")) else None,
        ))

    return rows
