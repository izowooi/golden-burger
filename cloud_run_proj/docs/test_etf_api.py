#!/usr/bin/env python3
"""
ETF 시세 조회 및 이동평균 크로스 테스트
- FinanceDataReader 기반 (API Key 불필요)
- 연금저축계좌 매수 가능한 ETF 대상

실행: python test_etf_api.py
"""

import FinanceDataReader as fdr
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd


# ================================================================
# 연금저축계좌 매수 가능한 대표 ETF 목록
# 증권사 앱에서 종목코드로 검색하여 확인 가능
# ================================================================
PENSION_ETFS = [
    # 미국 지수 (연금저축 인기 ETF)
    {"symbol": "360750", "name": "TIGER 미국S&P500", "expense": "0.07%"},
    {"symbol": "379800", "name": "KODEX 미국S&P500TR", "expense": "0.05%"},
    {"symbol": "133690", "name": "TIGER 미국나스닥100", "expense": "0.07%"},
    {"symbol": "379810", "name": "KODEX 미국나스닥100TR", "expense": "0.05%"},
    {"symbol": "381170", "name": "TIGER 미국테크TOP10 INDXX", "expense": "0.49%"},
    
    # 국내 지수
    {"symbol": "069500", "name": "KODEX 200", "expense": "0.15%"},
    {"symbol": "102110", "name": "TIGER 200", "expense": "0.05%"},
    {"symbol": "229200", "name": "KODEX 코스닥150", "expense": "0.25%"},
    
    # 섹터/테마
    {"symbol": "305720", "name": "KODEX 2차전지산업", "expense": "0.45%"},
    {"symbol": "091230", "name": "TIGER 반도체", "expense": "0.46%"},
]


def get_etf_price(symbol: str, days: int = 90) -> pd.DataFrame:
    """
    ETF 일별 시세 조회
    
    Args:
        symbol: 종목코드 (예: '360750')
        days: 조회할 일수
    
    Returns:
        DataFrame: OHLCV 데이터
    """
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    df = fdr.DataReader(symbol, start_date)
    return df


def calculate_moving_averages(
    df: pd.DataFrame, 
    short_period: int = 5, 
    long_period: int = 60
) -> pd.DataFrame:
    """
    이동평균 계산
    
    Args:
        df: OHLCV DataFrame
        short_period: 단기 이동평균 기간
        long_period: 장기 이동평균 기간
    
    Returns:
        DataFrame: 이동평균이 추가된 DataFrame
    """
    df = df.copy()
    df['MA_short'] = df['Close'].rolling(window=short_period).mean()
    df['MA_long'] = df['Close'].rolling(window=long_period).mean()
    
    # 신호 계산
    df['Signal'] = 0
    df.loc[df['MA_short'] > df['MA_long'], 'Signal'] = 1   # Bullish
    df.loc[df['MA_short'] < df['MA_long'], 'Signal'] = -1  # Bearish
    
    # 크로스 감지 (신호 변화)
    df['Cross'] = df['Signal'].diff()
    
    return df


def detect_cross_signal(df: pd.DataFrame) -> Optional[str]:
    """
    최근 크로스 신호 감지
    
    Args:
        df: 이동평균이 계산된 DataFrame
    
    Returns:
        str: 'GOLDEN_CROSS', 'DEAD_CROSS', 또는 None
    """
    if df.empty or len(df) < 2:
        return None
    
    latest_cross = df['Cross'].iloc[-1]
    
    if latest_cross == 2:
        return "GOLDEN_CROSS"  # 매수 신호: 단기 > 장기로 전환
    elif latest_cross == -2:
        return "DEAD_CROSS"    # 매도 신호: 단기 < 장기로 전환
    
    return None


def analyze_etf(symbol: str, name: str, short_ma: int = 5, long_ma: int = 60) -> dict:
    """
    ETF 분석 (시세 + 이동평균 + 크로스 신호)
    
    Args:
        symbol: 종목코드
        name: ETF명
        short_ma: 단기 이동평균 기간
        long_ma: 장기 이동평균 기간
    
    Returns:
        dict: 분석 결과
    """
    # 데이터 조회 (충분한 기간)
    df = get_etf_price(symbol, days=long_ma * 2 + 30)
    
    if df.empty:
        raise ValueError(f"데이터를 찾을 수 없습니다: {symbol}")
    
    # 이동평균 계산
    df = calculate_moving_averages(df, short_ma, long_ma)
    
    # 최신 데이터
    latest = df.iloc[-1]
    
    # 크로스 신호
    cross = detect_cross_signal(df)
    
    # 이평선 간 거리 (%)
    diff_pct = (latest['MA_short'] - latest['MA_long']) / latest['MA_long'] * 100
    
    return {
        "symbol": symbol,
        "name": name,
        "date": df.index[-1].strftime('%Y-%m-%d'),
        "close": int(latest['Close']),
        "volume": int(latest['Volume']),
        "ma_short": round(latest['MA_short'], 2),
        "ma_long": round(latest['MA_long'], 2),
        "signal": "BULLISH" if latest['Signal'] == 1 else "BEARISH",
        "diff_pct": round(diff_pct, 2),
        "cross_today": cross,
        "data_points": len(df),
    }


def print_analysis(result: dict):
    """분석 결과 출력"""
    print(f"\n{'='*50}")
    print(f"📊 {result['name']} ({result['symbol']})")
    print(f"{'='*50}")
    print(f"  📅 날짜: {result['date']}")
    print(f"  💰 종가: {result['close']:,}원")
    print(f"  📈 거래량: {result['volume']:,}")
    print(f"  ─────────────────────────────")
    print(f"  📉 5일 이평: {result['ma_short']:,.0f}원")
    print(f"  📉 60일 이평: {result['ma_long']:,.0f}원")
    print(f"  📊 이평 차이: {result['diff_pct']:+.2f}%")
    print(f"  ─────────────────────────────")
    
    signal_emoji = "🟢" if result['signal'] == "BULLISH" else "🔴"
    print(f"  {signal_emoji} 상태: {result['signal']}")
    
    if result['cross_today']:
        cross_emoji = "🚀" if result['cross_today'] == "GOLDEN_CROSS" else "📉"
        print(f"  {cross_emoji} ⚠️  오늘 {result['cross_today']} 발생!")


def main():
    """메인 실행"""
    print("\n" + "=" * 60)
    print("  ETF 이동평균 크로스 분석기 (5-60 MA)")
    print("  연금저축계좌 매수 가능 ETF 대상")
    print("=" * 60)
    
    # 분석할 ETF 선택 (전체 또는 일부)
    target_etfs = PENSION_ETFS[:6]  # 상위 6개만 테스트
    
    alerts = []
    
    for etf in target_etfs:
        try:
            result = analyze_etf(etf['symbol'], etf['name'])
            print_analysis(result)
            
            # 크로스 발생 시 알림 수집
            if result['cross_today']:
                alerts.append(result)
                
        except Exception as e:
            print(f"\n❌ [{etf['name']}] 오류: {e}")
    
    # 크로스 발생 요약
    print("\n" + "=" * 60)
    print("  📢 크로스 발생 요약")
    print("=" * 60)
    
    if alerts:
        for a in alerts:
            emoji = "🚀" if a['cross_today'] == "GOLDEN_CROSS" else "📉"
            print(f"  {emoji} {a['name']}: {a['cross_today']}")
    else:
        print("  오늘 크로스 발생 없음")
    
    print("\n" + "=" * 60)
    print("  ℹ️  증권사 앱에서 종목코드로 검색하여 매수 가능 여부 확인")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
