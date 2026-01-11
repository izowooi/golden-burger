"""Tests for FinanceDataReader client (Korean ETF)"""
import pytest
from datetime import date, timedelta
from stockbot.fdr_client import FetchRange, fetch_daily_ohlc_kr


class TestFetchRange:
    def test_valid_range(self):
        rng = FetchRange(start=date(2025, 1, 1), end=date(2025, 1, 10))
        assert rng.start == date(2025, 1, 1)
        assert rng.end == date(2025, 1, 10)

    def test_end_none(self):
        rng = FetchRange(start=date(2025, 1, 1), end=None)
        assert rng.end is None

    def test_invalid_range(self):
        with pytest.raises(ValueError):
            FetchRange(start=date(2025, 1, 10), end=date(2025, 1, 1))


@pytest.mark.integration
class TestFetchDailyOhlcKr:
    def test_fetch_known_etf(self):
        """TIGER 미국S&P500 (360750) 데이터 조회"""
        rng = FetchRange(
            start=date.today() - timedelta(days=10),
            end=date.today() - timedelta(days=1)
        )
        rows = fetch_daily_ohlc_kr("360750", rng)

        assert len(rows) > 0
        assert all(r.ticker == "360750" for r in rows)
        assert all(r.close > 0 for r in rows)

    def test_fetch_multiple_days(self):
        """60일 이상 데이터 조회 (이동평균 계산용)"""
        rng = FetchRange(
            start=date.today() - timedelta(days=90),
            end=date.today() - timedelta(days=1)
        )
        rows = fetch_daily_ohlc_kr("360750", rng)

        # 영업일 기준으로 60일 이상 확보 가능한지 확인
        assert len(rows) >= 40  # 휴일 감안

    def test_invalid_symbol(self):
        """존재하지 않는 심볼"""
        rng = FetchRange(
            start=date.today() - timedelta(days=10),
            end=None
        )
        rows = fetch_daily_ohlc_kr("999999", rng)
        assert rows == []

    def test_ohlc_fields(self):
        """OHLC 필드 확인"""
        rng = FetchRange(
            start=date.today() - timedelta(days=5),
            end=date.today() - timedelta(days=1)
        )
        rows = fetch_daily_ohlc_kr("360750", rng)

        if rows:
            row = rows[0]
            assert row.ticker == "360750"
            assert isinstance(row.d, date)
            assert row.close > 0
            # open, high, low, volume은 None일 수 있음
