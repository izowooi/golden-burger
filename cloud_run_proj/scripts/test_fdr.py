#!/usr/bin/env python3
"""FinanceDataReader 간단 테스트 - 360750 (TIGER 미국S&P500) 가격 조회"""

import FinanceDataReader as fdr
from datetime import datetime, timedelta

# 최근 5일 데이터 조회
start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
df = fdr.DataReader('360750', start_date)

print("TIGER 미국S&P500 (360750) 최근 시세")
print("-" * 40)
print(df.tail())
print("-" * 40)
print(f"최신 종가: {int(df['Close'].iloc[-1]):,}원")
