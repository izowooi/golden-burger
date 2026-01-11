# 상장 ETF 기준가 API 조회 가이드

## 핵심 요약

상장 ETF는 KRX(한국거래소)에서 실시간 거래되므로 여러 API를 통해 시세 조회가 가능합니다.

| 방법 | API Key | 계좌 필요 | 난이도 | 추천도 |
|------|---------|-----------|--------|--------|
| FinanceDataReader | ❌ | ❌ | ⭐ | ⭐⭐⭐⭐⭐ |
| 공공데이터포털 | ✅ (무료) | ❌ | ⭐⭐ | ⭐⭐⭐⭐ |
| 한국투자증권 KIS | ✅ | ✅ | ⭐⭐⭐ | ⭐⭐⭐ |

---

## 연금저축계좌 매수 가능한 대표 ETF 목록

아래 ETF들은 대부분의 증권사 연금저축계좌에서 매수 가능합니다.

### 국내 지수 추종
| ETF명 | 종목코드 | ISIN | 운용사 | 총보수 |
|-------|----------|------|--------|--------|
| KODEX 200 | 069500 | KR7069500007 | 삼성자산운용 | 0.15% |
| TIGER 200 | 102110 | KR7102110004 | 미래에셋자산운용 | 0.05% |
| KODEX 코스닥150 | 229200 | KR7229200003 | 삼성자산운용 | 0.25% |

### 미국 지수 추종 (연금저축 인기 ETF)
| ETF명 | 종목코드 | ISIN | 운용사 | 총보수 |
|-------|----------|------|--------|--------|
| TIGER 미국S&P500 | 360750 | KR7360750004 | 미래에셋자산운용 | 0.07% |
| KODEX 미국S&P500TR | 379800 | KR7379800005 | 삼성자산운용 | 0.05% |
| TIGER 미국나스닥100 | 133690 | KR7133690008 | 미래에셋자산운용 | 0.07% |
| KODEX 미국나스닥100TR | 379810 | KR7379810004 | 삼성자산운용 | 0.05% |
| TIGER 미국테크TOP10 INDXX | 381170 | KR7381170001 | 미래에셋자산운용 | 0.49% |

### 테마/섹터
| ETF명 | 종목코드 | ISIN | 운용사 | 총보수 |
|-------|----------|------|--------|--------|
| KODEX 2차전지산업 | 305720 | KR7305720008 | 삼성자산운용 | 0.45% |
| TIGER 반도체 | 091230 | KR7091230003 | 미래에셋자산운용 | 0.46% |

> **확인 방법**: 증권사 앱 → 연금저축 계좌 선택 → 해당 종목코드 검색 → "매수" 버튼 활성화 여부 확인

---

## 방법 1: FinanceDataReader (가장 간단, 추천)

API Key 없이 바로 사용 가능합니다. KRX 데이터를 스크래핑하여 제공합니다.

### 설치

```bash
pip install finance-datareader
```

### 테스트 코드

```python
import FinanceDataReader as fdr
from datetime import datetime, timedelta

def get_etf_price_fdr(symbol: str, days: int = 60) -> dict:
    """
    FinanceDataReader를 이용한 ETF 시세 조회
    
    Args:
        symbol: 종목코드 (예: '360750')
        days: 조회할 일수
    
    Returns:
        dict: 시세 데이터
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # 데이터 조회
    df = fdr.DataReader(symbol, start_date.strftime('%Y-%m-%d'))
    
    if df.empty:
        raise ValueError(f"데이터를 찾을 수 없습니다: {symbol}")
    
    # 최근 데이터 반환
    latest = df.iloc[-1]
    
    return {
        "symbol": symbol,
        "date": df.index[-1].strftime('%Y-%m-%d'),
        "close": int(latest['Close']),
        "open": int(latest['Open']),
        "high": int(latest['High']),
        "low": int(latest['Low']),
        "volume": int(latest['Volume']),
        "history": df.tail(10).to_dict('records')  # 최근 10일
    }


def get_moving_averages(symbol: str, short_period: int = 5, long_period: int = 60) -> dict:
    """
    이동평균 계산 (5-60 크로스 알림용)
    
    Args:
        symbol: 종목코드
        short_period: 단기 이동평균 기간
        long_period: 장기 이동평균 기간
    
    Returns:
        dict: 이동평균 및 크로스 신호
    """
    # 충분한 데이터 확보를 위해 long_period * 2 일치 조회
    df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=long_period * 2)).strftime('%Y-%m-%d'))
    
    if len(df) < long_period:
        raise ValueError(f"데이터 부족: {len(df)}일 < {long_period}일 필요")
    
    # 이동평균 계산
    df['MA_short'] = df['Close'].rolling(window=short_period).mean()
    df['MA_long'] = df['Close'].rolling(window=long_period).mean()
    
    # 크로스 신호 감지
    df['signal'] = 0
    df.loc[df['MA_short'] > df['MA_long'], 'signal'] = 1   # 골든크로스 상태
    df.loc[df['MA_short'] < df['MA_long'], 'signal'] = -1  # 데드크로스 상태
    
    # 크로스 발생 시점
    df['cross'] = df['signal'].diff()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    cross_type = None
    if latest['cross'] == 2:
        cross_type = "GOLDEN_CROSS"  # 매수 신호
    elif latest['cross'] == -2:
        cross_type = "DEAD_CROSS"    # 매도 신호
    
    return {
        "symbol": symbol,
        "date": df.index[-1].strftime('%Y-%m-%d'),
        "close": int(latest['Close']),
        "ma_short": round(latest['MA_short'], 2),
        "ma_long": round(latest['MA_long'], 2),
        "signal": "BULLISH" if latest['signal'] == 1 else "BEARISH",
        "cross_today": cross_type,
        "diff_percent": round((latest['MA_short'] - latest['MA_long']) / latest['MA_long'] * 100, 2)
    }


# ===== 테스트 실행 =====
if __name__ == "__main__":
    # 테스트할 ETF 목록
    test_etfs = [
        ("360750", "TIGER 미국S&P500"),
        ("379810", "KODEX 미국나스닥100TR"),
        ("069500", "KODEX 200"),
        ("381170", "TIGER 미국테크TOP10"),
    ]
    
    print("=" * 60)
    print("ETF 시세 조회 테스트 (FinanceDataReader)")
    print("=" * 60)
    
    for symbol, name in test_etfs:
        try:
            # 기본 시세 조회
            price = get_etf_price_fdr(symbol)
            print(f"\n[{name}] ({symbol})")
            print(f"  날짜: {price['date']}")
            print(f"  종가: {price['close']:,}원")
            print(f"  거래량: {price['volume']:,}")
            
            # 이동평균 조회
            ma = get_moving_averages(symbol)
            print(f"  5일 이평: {ma['ma_short']:,.0f}원")
            print(f"  60일 이평: {ma['ma_long']:,.0f}원")
            print(f"  상태: {ma['signal']} ({ma['diff_percent']:+.2f}%)")
            if ma['cross_today']:
                print(f"  🚨 오늘 {ma['cross_today']} 발생!")
                
        except Exception as e:
            print(f"\n[{name}] ({symbol}) - 오류: {e}")
    
    print("\n" + "=" * 60)
```

### 실행 결과 예시

```
============================================================
ETF 시세 조회 테스트 (FinanceDataReader)
============================================================

[TIGER 미국S&P500] (360750)
  날짜: 2025-01-10
  종가: 18,250원
  거래량: 1,234,567
  5일 이평: 18,180원
  60일 이평: 17,890원
  상태: BULLISH (+1.62%)

[KODEX 미국나스닥100TR] (379810)
  날짜: 2025-01-10
  종가: 21,350원
  거래량: 987,654
  5일 이평: 21,200원
  60일 이평: 20,500원
  상태: BULLISH (+3.41%)
```

---

## 방법 2: 공공데이터포털 API

### API Key 발급 방법

1. https://www.data.go.kr 접속
2. 회원가입/로그인
3. 검색: "금융위원회_주식시세정보" 또는 "증권상품시세정보"
4. **금융위원회_증권상품시세정보** (서비스ID: 15094806) 선택
5. "활용신청" 클릭 → 자동 승인 (즉시)
6. 마이페이지 → API Key 확인

### 테스트 코드

```python
import requests
from datetime import datetime, timedelta
from typing import Optional
import os

# 환경변수 또는 직접 입력
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY", "YOUR_API_KEY_HERE")


def get_etf_price_data_go_kr(
    isin_code: str,
    base_date: Optional[str] = None
) -> dict:
    """
    공공데이터포털 API를 이용한 ETF 시세 조회
    
    Args:
        isin_code: ISIN 코드 (예: 'KR7360750004')
        base_date: 조회 기준일 (YYYYMMDD), None이면 최근일
    
    Returns:
        dict: 시세 데이터
    """
    base_url = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo"
    
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "resultType": "json",
        "numOfRows": 100,
        "pageNo": 1,
        "isinCd": isin_code,
    }
    
    if base_date:
        params["basDt"] = base_date
    
    response = requests.get(base_url, params=params, timeout=10)
    response.raise_for_status()
    
    data = response.json()
    
    # 응답 구조 확인
    if "response" not in data:
        raise ValueError(f"잘못된 응답: {data}")
    
    result_code = data["response"]["header"]["resultCode"]
    if result_code != "00":
        raise ValueError(f"API 오류: {data['response']['header']['resultMsg']}")
    
    items = data["response"]["body"]["items"]["item"]
    
    if not items:
        raise ValueError(f"데이터 없음: {isin_code}")
    
    # 단일 항목이면 리스트로 변환
    if isinstance(items, dict):
        items = [items]
    
    # 최신 데이터 (첫 번째)
    latest = items[0]
    
    return {
        "isin": latest.get("isinCd"),
        "name": latest.get("itmsNm"),
        "date": latest.get("basDt"),
        "close": int(latest.get("clpr", 0)),
        "open": int(latest.get("mkp", 0)),
        "high": int(latest.get("hipr", 0)),
        "low": int(latest.get("lopr", 0)),
        "volume": int(latest.get("trqu", 0)),
        "market_cap": int(latest.get("mrktTotAmt", 0)),
        "nav": float(latest.get("nav", 0)) if latest.get("nav") else None,
    }


def get_etf_history_data_go_kr(
    isin_code: str,
    days: int = 60
) -> list:
    """
    공공데이터포털 API를 이용한 ETF 과거 시세 조회
    
    Args:
        isin_code: ISIN 코드
        days: 조회할 일수
    
    Returns:
        list: 시세 데이터 리스트 (최신순)
    """
    base_url = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo"
    
    # 날짜 범위 설정
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "resultType": "json",
        "numOfRows": days + 20,  # 휴장일 고려 여유분
        "pageNo": 1,
        "isinCd": isin_code,
        "beginBasDt": start_date.strftime("%Y%m%d"),
        "endBasDt": end_date.strftime("%Y%m%d"),
    }
    
    response = requests.get(base_url, params=params, timeout=10)
    response.raise_for_status()
    
    data = response.json()
    items = data["response"]["body"]["items"]["item"]
    
    if isinstance(items, dict):
        items = [items]
    
    # 날짜순 정렬 (오래된 것 → 최신)
    items.sort(key=lambda x: x.get("basDt", ""))
    
    return [
        {
            "date": item.get("basDt"),
            "close": int(item.get("clpr", 0)),
            "volume": int(item.get("trqu", 0)),
            "nav": float(item.get("nav", 0)) if item.get("nav") else None,
        }
        for item in items
    ]


# ===== 테스트 실행 =====
if __name__ == "__main__":
    # API Key 확인
    if DATA_GO_KR_API_KEY == "YOUR_API_KEY_HERE":
        print("⚠️  DATA_GO_KR_API_KEY 환경변수를 설정하거나 코드에 직접 입력하세요.")
        print("   발급: https://www.data.go.kr → '금융위원회_증권상품시세정보' 검색")
        exit(1)
    
    test_etfs = [
        ("KR7360750004", "TIGER 미국S&P500"),
        ("KR7379810004", "KODEX 미국나스닥100TR"),
        ("KR7069500007", "KODEX 200"),
    ]
    
    print("=" * 60)
    print("ETF 시세 조회 테스트 (공공데이터포털)")
    print("=" * 60)
    
    for isin, name in test_etfs:
        try:
            price = get_etf_price_data_go_kr(isin)
            print(f"\n[{name}]")
            print(f"  ISIN: {price['isin']}")
            print(f"  날짜: {price['date']}")
            print(f"  종가: {price['close']:,}원")
            print(f"  NAV: {price['nav']:,.2f}원" if price['nav'] else "  NAV: N/A")
            print(f"  거래량: {price['volume']:,}")
            
        except Exception as e:
            print(f"\n[{name}] - 오류: {e}")
```

---

## 방법 3: 한국투자증권 KIS Developers API

실시간 시세와 더 상세한 정보가 필요한 경우 사용합니다.

### API Key 발급 방법

1. https://apiportal.koreainvestment.com 접속
2. 한국투자증권 계좌 필요 (비대면 개설 가능)
3. 회원가입 → 앱 등록
4. **모의투자** 또는 **실전투자** 선택
5. App Key, App Secret 발급

### 테스트 코드

```python
import requests
import json
from datetime import datetime
import os

# 한국투자증권 API 설정
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "YOUR_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "YOUR_APP_SECRET")
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"  # 실전
# KIS_BASE_URL = "https://openapivts.koreainvestment.com:29443"  # 모의투자


def get_kis_access_token() -> str:
    """
    KIS OAuth 토큰 발급
    """
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    
    body = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }
    
    response = requests.post(url, json=body, timeout=10)
    response.raise_for_status()
    
    return response.json()["access_token"]


def get_etf_price_kis(symbol: str, access_token: str) -> dict:
    """
    한국투자증권 API를 이용한 ETF 현재가 조회
    
    Args:
        symbol: 종목코드 (예: '360750')
        access_token: OAuth 토큰
    
    Returns:
        dict: 시세 데이터
    """
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010100",  # 주식현재가 시세
    }
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",  # 주식/ETF
        "FID_INPUT_ISCD": symbol,
    }
    
    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    
    data = response.json()
    
    if data["rt_cd"] != "0":
        raise ValueError(f"API 오류: {data['msg1']}")
    
    output = data["output"]
    
    return {
        "symbol": symbol,
        "name": output.get("hts_kor_isnm"),
        "price": int(output.get("stck_prpr", 0)),
        "change": int(output.get("prdy_vrss", 0)),
        "change_rate": float(output.get("prdy_ctrt", 0)),
        "volume": int(output.get("acml_vol", 0)),
        "high": int(output.get("stck_hgpr", 0)),
        "low": int(output.get("stck_lwpr", 0)),
    }


def get_etf_daily_price_kis(
    symbol: str,
    access_token: str,
    period: str = "D"
) -> list:
    """
    한국투자증권 API를 이용한 ETF 일별 시세 조회
    
    Args:
        symbol: 종목코드
        access_token: OAuth 토큰
        period: D(일), W(주), M(월)
    
    Returns:
        list: 일별 시세 리스트
    """
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010400",
    }
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_PERIOD_DIV_CODE": period,
        "FID_ORG_ADJ_PRC": "0",  # 수정주가
    }
    
    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    
    data = response.json()
    
    if data["rt_cd"] != "0":
        raise ValueError(f"API 오류: {data['msg1']}")
    
    return [
        {
            "date": item.get("stck_bsop_date"),
            "close": int(item.get("stck_clpr", 0)),
            "open": int(item.get("stck_oprc", 0)),
            "high": int(item.get("stck_hgpr", 0)),
            "low": int(item.get("stck_lwpr", 0)),
            "volume": int(item.get("acml_vol", 0)),
        }
        for item in data.get("output", [])
    ]


# ===== 테스트 실행 =====
if __name__ == "__main__":
    if KIS_APP_KEY == "YOUR_APP_KEY":
        print("⚠️  KIS_APP_KEY, KIS_APP_SECRET 환경변수를 설정하세요.")
        print("   발급: https://apiportal.koreainvestment.com")
        exit(1)
    
    print("=" * 60)
    print("ETF 시세 조회 테스트 (한국투자증권 KIS)")
    print("=" * 60)
    
    try:
        # 토큰 발급
        print("\n토큰 발급 중...")
        token = get_kis_access_token()
        print("✅ 토큰 발급 성공")
        
        # 시세 조회
        test_symbols = ["360750", "379810", "069500"]
        
        for symbol in test_symbols:
            price = get_etf_price_kis(symbol, token)
            print(f"\n[{price['name']}] ({symbol})")
            print(f"  현재가: {price['price']:,}원")
            print(f"  등락: {price['change']:+,}원 ({price['change_rate']:+.2f}%)")
            print(f"  거래량: {price['volume']:,}")
            
    except Exception as e:
        print(f"오류: {e}")
```

---

## cron job 구현 예시 (Google Cloud Functions)

```python
# main.py - Google Cloud Functions용
import functions_framework
import FinanceDataReader as fdr
from datetime import datetime, timedelta
from google.cloud import firestore
import requests

# Firestore 클라이언트
db = firestore.Client()

# 알림 설정 (Slack/Discord/Telegram 등)
WEBHOOK_URL = "YOUR_WEBHOOK_URL"

# 모니터링할 ETF 목록
WATCH_LIST = [
    {"symbol": "360750", "name": "TIGER 미국S&P500"},
    {"symbol": "379810", "name": "KODEX 미국나스닥100TR"},
    {"symbol": "381170", "name": "TIGER 미국테크TOP10"},
]

SHORT_MA = 5
LONG_MA = 60


def calculate_cross_signal(symbol: str) -> dict:
    """이동평균 크로스 계산"""
    df = fdr.DataReader(symbol, (datetime.now() - timedelta(days=LONG_MA * 2)).strftime('%Y-%m-%d'))
    
    df['MA_short'] = df['Close'].rolling(window=SHORT_MA).mean()
    df['MA_long'] = df['Close'].rolling(window=LONG_MA).mean()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 크로스 감지
    cross = None
    if prev['MA_short'] <= prev['MA_long'] and latest['MA_short'] > latest['MA_long']:
        cross = "GOLDEN_CROSS"
    elif prev['MA_short'] >= prev['MA_long'] and latest['MA_short'] < latest['MA_long']:
        cross = "DEAD_CROSS"
    
    return {
        "symbol": symbol,
        "date": df.index[-1].strftime('%Y-%m-%d'),
        "close": int(latest['Close']),
        "ma_short": round(latest['MA_short'], 2),
        "ma_long": round(latest['MA_long'], 2),
        "cross": cross,
    }


def send_alert(message: str):
    """웹훅으로 알림 전송"""
    if not WEBHOOK_URL or WEBHOOK_URL == "YOUR_WEBHOOK_URL":
        print(f"[ALERT] {message}")
        return
    
    requests.post(WEBHOOK_URL, json={"text": message}, timeout=10)


def save_to_db(data: dict):
    """Firestore에 저장"""
    doc_ref = db.collection("etf_prices").document(f"{data['symbol']}_{data['date']}")
    doc_ref.set(data)


@functions_framework.http
def check_ma_cross(request):
    """HTTP 트리거 함수 (Cloud Scheduler에서 호출)"""
    results = []
    alerts = []
    
    for etf in WATCH_LIST:
        try:
            signal = calculate_cross_signal(etf["symbol"])
            signal["name"] = etf["name"]
            
            # DB 저장
            save_to_db(signal)
            
            # 크로스 발생 시 알림
            if signal["cross"]:
                emoji = "🚀" if signal["cross"] == "GOLDEN_CROSS" else "📉"
                msg = f"{emoji} [{etf['name']}] {signal['cross']} 발생!\n"
                msg += f"종가: {signal['close']:,}원\n"
                msg += f"5일 이평: {signal['ma_short']:,.0f}원\n"
                msg += f"60일 이평: {signal['ma_long']:,.0f}원"
                alerts.append(msg)
            
            results.append(signal)
            
        except Exception as e:
            results.append({"symbol": etf["symbol"], "error": str(e)})
    
    # 알림 전송
    for alert in alerts:
        send_alert(alert)
    
    return {"status": "ok", "results": results, "alerts_sent": len(alerts)}
```

### Cloud Scheduler 설정

```bash
# 매일 오후 6시 (KST) 실행
gcloud scheduler jobs create http etf-ma-cross-check \
    --schedule="0 18 * * 1-5" \
    --time-zone="Asia/Seoul" \
    --uri="https://YOUR_REGION-YOUR_PROJECT.cloudfunctions.net/check_ma_cross" \
    --http-method=GET
```

---

## 비교 요약

| 항목 | FinanceDataReader | 공공데이터포털 | KIS API |
|------|-------------------|----------------|---------|
| API Key 필요 | ❌ | ✅ (무료) | ✅ (계좌필요) |
| 설치 | `pip install` | 없음 | 없음 |
| 일별 시세 | ✅ | ✅ | ✅ |
| 과거 데이터 | ✅ (수년) | ✅ (제한적) | ✅ (100건) |
| 실시간 | ❌ | ❌ | ✅ |
| 안정성 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| cron 적합성 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |

**권장**: 일일 cron job 목적이라면 **FinanceDataReader**가 가장 간단하고 효과적입니다. 공식 API가 필요하면 **공공데이터포털**을 사용하세요.
