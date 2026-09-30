폴리마켓은 실제로 과거 가격 데이터를 별도로 보관하고 있으며, 공식 API/SDK에서 조회할 수 있습니다. 말씀하신 것처럼 종료된 마켓 웹페이지에서 마우스를 움직일 때 과거 가격이 보이는 이유도 이 시계열 데이터가 남아 있기 때문이라고 볼 수 있습니다.

특히 2026년 9월 현재 공식 Python SDK에는 아예 list_price_history()가 있고, 구형/호환 CLOB API에도 GET /prices-history가 있습니다.  

1. 가장 직접적인 방법: prices-history

CLOB 쪽에는 다음 엔드포인트가 있습니다.

GET /prices-history

주요 파라미터는 대략 이렇게 구성됩니다.

market   = YES 또는 NO 토큰의 token_id
startTs  = 시작 Unix timestamp
endTs    = 종료 Unix timestamp
fidelity = 데이터 해상도

또는 기간을 직접 지정할 수도 있습니다.

interval = 1h / 6h / 1d / 1w / 1m / max

공식 py-clob-client-v2에도 정확히 이 호출이 구현되어 있습니다.  

예를 들어 개념적으로는:

params = {
    "market": token_id,
    "startTs": start_ts,
    "endTs": end_ts,
    "fidelity": 1,
}
history = client.get_prices_history(params)

응답은 이런 형태입니다.

{
  "history": [
    {
      "t": 1790688000,
      "p": 0.42
    },
    {
      "t": 1790688060,
      "p": 0.45
    },
    {
      "t": 1790688120,
      "p": 0.51
    }
  ]
}

즉,

t = 시간
p = 가격

입니다.

가격 0.51은 대략 51¢ = 시장이 반영한 51% 확률이라고 보면 됩니다.

⸻

2. 지금 하시는 연구에는 이게 상당히 중요합니다

지금처럼 스포츠 폴리마켓을 1분마다 직접 수집하고 계셨다면, 이 API를 이용해서 상당 부분을 **과거 데이터로 백필(backfill)**할 가능성이 있습니다.

예를 들어 경기 하나가

20:00 경기 시작
21:50 경기 종료

였다면,

19:00
19:01
19:02
...
20:00
20:01
...
21:50

형태의 가격 변화를 받을 수 있는지를 확인해볼 수 있습니다.

공식 Polymarket 자료에서도 price history는 {timestamp, price} 형태의 시계열이며, SDK 예시는 fidelity=60으로 60분 단위 데이터를 가져오는 식으로 설명합니다.  

다만 여기서 한 가지 매우 중요한 주의점이 있습니다.

fidelity=1이라고 해서 모든 오래된 종료 마켓에서 반드시 완전한 1분 데이터가 영구적으로 보존된다고 단정하면 안 됩니다.

API 버전, 마켓 생성 시기, 종료 후 보관 정책에 따라 조회 가능한 세밀도가 달라질 가능성이 있습니다. 따라서 실제 연구용으로 쓰려면 샘플 종료 마켓 몇 개를 가지고 1분·5분·1시간 단위 조회 결과를 검증하는 것이 좋습니다.

⸻

3. 오히려 최신 공식 Python SDK가 더 흥미롭습니다

2026년 9월 10일 Polymarket 공식 Python SDK 0.10.0에서 Data API v2로 데이터 조회 계층이 이전됐습니다.  

현재 공식 SDK에는:

client.list_price_history(
    asset_id=...,
    start=...,
    end=...,
    bucket_seconds=60
)

같은 인터페이스가 있습니다.

특히 여기서 눈에 띄는 게

bucket_seconds

입니다.

즉 기존의

fidelity = 분

보다 훨씬 명확하게

bucket_seconds=60

→ 60초 버킷

식으로 요청할 수 있게 되어 있습니다.

공식 SDK 설명상:

list_price_history(
    asset_id: str,
    interval: PriceHistoryInterval | None = None,
    start: int | datetime | None = None,
    end: int | datetime | None = None,
    as_of: int | datetime | None = None,
    bucket_seconds: int | None = None,
    page_size: int | None = None,
)

이며 가격 데이터를 오래된 순으로 반환한다고 명시합니다. 명시적 start/end 구간은 한 요청당 최대 15일이고 기본 페이지 크기는 10,000입니다.  

스포츠 경기처럼 길어야 몇 시간짜리 데이터라면 15일 제한은 사실상 문제가 없습니다.

⸻

4. 가격 이력과 실제 체결 이력은 구분해야 합니다

여기서 연구 관점에서 꽤 중요한 차이가 하나 있습니다.

사용자가 웹사이트에서 본 그래프는 주로:

Price History

입니다.

하지만 실제 체결을 보고 싶다면:

Trade History

가 필요합니다.

둘은 다릅니다.

데이터	의미
Price history	시간별 시장 가격
Trades	실제 체결된 주문
Order book	그 순간의 매수/매도 호가
Resolution	최종 결과
Volume	거래량

공식 SDK는 가격뿐 아니라 list_trades()로 과거 거래도 조회할 수 있습니다. 현재 SDK는 condition_id, event_id, 시간 범위 등을 이용한 과거 trades 조회를 지원합니다.  

그래서 논문 데이터를 만든다면 저는 가격 이력만 가져오는 것보다 가능하면

timestamp
event_id
market_id
asset_id
team
side
price
trade_volume
game_clock
score_home
score_away

형태로 구성하는 것을 권합니다.

그러면 예를 들어:

브라질 득점
↓
Polymarket 가격
0.61 → 0.84
↓
3분 후
0.81

처럼 실제 스포츠 이벤트 발생과 시장 과잉반응/과소반응을 분석할 수 있습니다.

⸻

5. 지금 하시는 1분 수집 시스템에는 굉장히 좋은 소식입니다

기존에는 아마 Jenkins나 서버에서

매 1분
↓
현재 가격 API 호출
↓
DB 저장

하는 구조로 데이터를 쌓고 계실 텐데요.

과거 가격 API가 충분한 해상도를 제공한다면 앞으로는 구조를 다음처럼 만들 수 있습니다.

                    Polymarket
                       │
           ┌───────────┴───────────┐
           │                       │
       실시간 API              History API
           │                       │
       1분 수집                  백필
           │                       │
           └───────────┬───────────┘
                       ↓
                      DB
                       ↓
        경기 시각 + 득점 시각 결합
                       ↓
      Overreaction / Underreaction 분석

이게 특히 좋은 이유는 수집기를 놓쳤거나 서버가 죽었던 경기까지 복구할 가능성이 생긴다는 것입니다.

그리고 웹 UI가 이미 종료된 경기에서도 가격 그래프를 표시한다는 사용자님의 관찰은 아주 중요한 단서입니다. 프런트엔드 역시 어딘가의 historical endpoint에서 그 데이터를 받아오고 있을 가능성이 높습니다.

제가 다음으로 해보고 싶은 검증

사용자님이 보고 계셨던 이미 종료된 폴리마켓 경기 URL 하나만 주시면, 제가 그 마켓을 기준으로 실제로

① event_id
② condition_id
③ YES/NO asset_id
④ CLOB token_id
⑤ 경기 시작~종료 가격
⑥ 1분 단위 조회 가능 여부
⑦ 최대 어느 정도 과거까지 내려가는지

를 실제 API 기준으로 한번 찍어서 확인해볼 수 있습니다.

특히 사용자님의 Polymarket 스포츠 연구에는 **“종료된 스포츠 마켓의 1분 데이터를 API로 얼마나 백필할 수 있느냐”**가 핵심이라, 이걸 실제 종료 경기 하나로 검증해보는 게 제일 의미 있습니다.  