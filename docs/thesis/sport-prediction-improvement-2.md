지금 하고 계신 “1분 가격 수집 → 경기 종료 후 백필 → 실제 경기 결과와 비교” 외에도 연구 가치가 꽤 큰 데이터가 여러 개 있습니다. 특히 하나는 지금 연구 방향과 거의 정확히 맞아떨어지는데, Polymarket 공식 SDK 자체가 실시간 스포츠 경기 상태 스트림을 제공하고 있습니다.

Polymarket이 2026년 7월 공개한 연구자용 안내에서도 데이터를 크게 Gamma API = 마켓/이벤트 구조, CLOB = 가격·호가·체결, Data API = 거래자 행동으로 나눠 연구할 것을 권하고 있습니다.  

제가 보기에는 아래 순서가 가장 중요합니다.

우선도	데이터	연구 가치
★★★★★	Sports 실시간 스트림	득점/시간/피리어드와 가격 반응 자동 연결
★★★★★	실제 Trades	1분 가격보다 훨씬 세밀한 시장 반응
★★★★★	Order Book / Bid-Ask	과잉반응인지 단순 저유동성인지 구별
★★★★☆	Open Interest / Volume	가격 변화의 신뢰도 측정
★★★★☆	Wallet/Trader 데이터	고수 vs 일반 참여자 행동 분석
★★★★☆	Resolution 데이터	calibration/Brier score 분석
★★★☆☆	Market metadata / type	ML의 feature로 사용
★★★☆☆	Comments	sentiment 연구 가능
★★★★★	외부 tick/orderbook dataset	수집 이전 시점까지 연구 확대

그리고 여기서 가장 놀라운 것은 첫 번째입니다.

⸻

1. 제가 가장 먼저 추가하라고 권하고 싶은 것: Polymarket Sports Stream

공식 최신 Python SDK 코드를 확인해보니 SportsSpec이라는 별도의 스포츠 WebSocket 스트림이 있습니다.

더 중요한 건 이벤트 안에 단순히 경기 이름만 오는 게 아닙니다.

공식 모델 SportsGameResult에는 다음 필드들이 있습니다.

game_id
sportradar_game_id
league_abbreviation
home_team
away_team
status
live
ended
score
period
elapsed
finished_at
turn

즉 이런 데이터입니다.

21:15:23
Brazil vs Australia
score = "1-0"
period = "1H"
elapsed = "34"

Polymarket Python SDK가 이 스포츠 스트림을 직접 지원하며, 구독자에게 서버가 방송하는 모든 스포츠 이벤트를 전달하도록 구현되어 있습니다.  

이건 지금 연구를 상당히 바꿀 수 있습니다.

현재 생각하시는 구조가

Polymarket
가격
   ↓
1분마다 저장
외부 사이트
경기 결과
   ↓
나중에 결합

이라면,

앞으로는:

          Polymarket
          │
 ┌────────┴────────┐
 │                 │
Sports Stream   Market Stream
 │                 │
score             bid
elapsed           ask
period            trade
status            orderbook
 │                 │
 └────────┬────────┘
          ↓
       timestamp
          ↓
       DB JOIN

가 가능합니다.

예를 들어:

20:37:14
score
0-0 → 1-0
20:37:15
YES 0.42
20:37:16
YES 0.55
20:37:18
YES 0.67
20:37:25
YES 0.71

이런 데이터가 만들어집니다.

특히 sportradar_game_id가 매우 중요합니다.

공식 스포츠 이벤트 모델에 **sportradar_game_id**가 실제로 들어 있습니다.  

그래서 향후 Sportradar 데이터나 이를 사용하는 다른 경기 데이터셋과도 조인할 가능성이 생깁니다.

이건 논문에서 굉장히 유용한 key가 될 수 있습니다.

⸻

2. 1분 가격보다 Trade를 모으는 것이 더 중요할 수도 있습니다

최신 공식 py-sdk를 보면 PublicClient에 다음 API가 있습니다.

list_trades(
    user=None,
    condition_id=None,
    event_id=None,
    side=None,
    taker_only=None,
    filter_type=None,
    filter_amount=None,
    start=None,
    end=None,
    full_history=False
)

그리고 Polymarket Institute 역시 연구자에게 Data API의 trades를 직접 사용하도록 안내합니다.

예를 들어:

/trades?market={condition_id}

에서

proxyWallet
side
asset
conditionId
size
price

등을 얻을 수 있습니다.  

이게 중요한 이유는 현재의

12:01  0.51
12:02  0.58

데이터만 보면 그 사이에 무슨 일이 있었는지 모릅니다.

실제 체결을 보면:

12:01:03  BUY  $150 @ 0.51
12:01:07  BUY  $20  @ 0.53
12:01:08  BUY  $800 @ 0.57
12:01:14  SELL $120 @ 0.55
12:01:26  BUY  $400 @ 0.58

까지 볼 수 있습니다.

그러면 단순한 가격 연구에서

Market microstructure 연구

로 확장됩니다.

⸻

3. Order Book은 반드시 따로 저장해볼 가치가 있습니다

이건 오히려 앞으로 모으셔야 합니다.

공식 API에는 현재:

get_order_book()
get_order_books()
get_price()
get_midpoint()
get_spread()
get_last_trade_price()

가 있습니다.  

실시간 Market WebSocket에는 주문장 업데이트도 있습니다.

최신 SDK의 MarketSpec은 CLOB asset에 대해 실시간 market update를 구독하며 추가 기능을 켜면 best bid/ask와 market lifecycle 이벤트도 받을 수 있습니다.  

예를 들어 단순히

Brazil 70%

라고 기록하는 것보다:

best bid = .68
best ask = .72
spread = .04
bid depth
.68  $2,400
.67  $5,100
.65  $9,800
ask depth
.72  $800
.73  $1,200
.75  $3,600

를 가지고 있으면 훨씬 강한 분석이 가능합니다.

⸻

4. 제가 이 데이터를 특히 중요하게 보는 이유

사용자님의 연구가 예를 들어 이런 가설이라고 하겠습니다.

“득점 직후 prediction market 참가자들이 승리 확률을 과도하게 높인다.”

가격만 보면:

득점 전   .45
득점 후   .73
5분 후    .65

이므로

+28% 상승 후 -8% 되돌림

이라는 결과를 낼 수 있습니다.

그런데 orderbook을 가지고 있으면 전혀 다른 설명을 검증할 수 있습니다.

가설 A — 실제 overreaction

많은 거래자들이 적극적으로 매수했다.

Volume ↑
Trade count ↑
Buy pressure ↑
Depth 정상

가설 B — 유동성 부족

사람들이 흥분해서 산 것이 아니라,

ask
0.48 $20
0.55 $15
0.65 $30
0.73 $20

밖에 없어서 작은 시장가 주문 하나가 가격을 .73까지 올려버린 것입니다.

두 현상은 완전히 다릅니다.

그래서 연구 논문 입장에서는

Price + Trade + Order Book

세 가지를 함께 가지고 있는 것이 상당히 강력합니다.

⸻

5. Open Interest와 Live Volume도 공식 API에 있습니다

이것도 놓치기 쉬운 기능입니다.

현재 SDK에는:

get_open_interests(
    condition_ids=...
)

그리고

get_event_live_volume(
    event_ids=...
)

가 있습니다.

get_event_live_volume()은 event의 taker volume과 market breakdown을 돌려줍니다.  

그래서

price_change

만 보는 대신

price_change
×
volume
×
open_interest

로 볼 수 있습니다.

예:

A 경기
득점
0.48 → 0.71
Volume $300

vs

B 경기
득점
0.48 → 0.71
Volume $43,000

똑같은 가격 움직임이라도 신뢰도는 상당히 다릅니다.

⸻

6. Wallet 단위 데이터가 예상보다 훨씬 많습니다

이건 새로운 논문 주제로도 발전할 수 있습니다.

공식 Data API / SDK에는:

get_user_stats()
get_user_pnl()
get_user_volume()
list_market_holders()
list_trader_leaderboard()
get_trader_leaderboard_standing()
list_biggest_winners()

등이 있습니다.  

그래서 예를 들어 이런 연구를 할 수 있습니다.

질문

득점 직후 가격이 급변할 때 누가 먼저 거래하는가?

Trader를 과거 성적 기준으로 나눕니다.

Top traders
Normal traders
New traders

그리고:

Goal + 0~10 sec
Goal + 10~30 sec
Goal + 30~60 sec
Goal + 1~5 min

동안 누가 사고 파는지를 측정합니다.

그러면

Information leadership

까지 연구할 수 있습니다.

예를 들어:

고성과 trader가 먼저 BUY
↓
20초 후 일반 trader BUY 증가
↓
가격 overshoot
↓
2분 후 일부 되돌림

같은 패턴이 발견된다면 꽤 재미있는 결과입니다.

⸻

7. holders도 상당히 흥미롭습니다

list_market_holders()는 각 outcome의 holder를 가져올 수 있고

min_balance
include_pnl

필터도 지원합니다.  

따라서 시장 집중도를 계산할 수도 있습니다.

예:

Top 1 holder share
Top 5 holder share
Top 10 holder share
HHI
Gini coefficient

그리고 이것과 overreaction을 비교합니다.

가설:

소수 대형 포지션 보유자에게 집중된 시장일수록 가격 조정 특성이 다른가?

이런 것도 가능합니다.

⸻

8. Resolution API도 있습니다

공식 SDK에는 아예

get_resolutions(
    question_id=...
    condition_ids=...
    event_ids=...
)

가 있습니다.  

이것을 이용하면 단순한:

“가격이 움직였다.”

를 넘어

그 가격이 실제 결과와 얼마나 잘 맞았는가

를 계산할 수 있습니다.

대표적으로:

Brier score

(p-y)^2

예를 들어 실제 결과가 승리 1인데 시장가격이 0.70이었다면

(0.70-1)^2=0.09

입니다.

모든 경기를 모아:

가격구간     실제 승률
0.50~0.55    52%
0.55~0.60    57%
0.60~0.65    61%
0.65~0.70    66%
0.70~0.75    69%
...

를 만들면 calibration curve가 됩니다.

⸻

9. 이미 2026년에 매우 관련 있는 스포츠 논문도 나왔습니다

이건 꼭 한번 읽어보시는 것을 권합니다.

2026년 7월 공개된:

“Prices, Probabilities, and Parlays: Systematic Bias in Sports Prediction Markets”

이라는 연구가 있습니다.  

Polymarket이 아니라 Kalshi 스포츠 시장을 분석했는데, 무려 2,300만 건의 moneyline 거래를 이용했습니다.

흥미로운 결과가 하나 있습니다.

연구진은 시장 가격의 calibration이 만기까지 남은 시간(Time-to-Expiry)에 따라 변한다고 보고했습니다.

특히 종료 직전 구간에서 calibration 특성이 달라졌습니다.  

이게 사용자님의 연구와 상당히 겹칩니다.

다만 사용자님은 더 세밀하게:

경기 시간
+
실제 득점
+
Polymarket 가격

을 연결하려는 거라 연구 질문을 다르게 가져갈 여지가 충분합니다.

⸻

10. 특히 저는 Game Time 기준으로 연구하는 것을 권합니다

단순히:

경기 종료까지 남은 실제 시간

만 보는 게 아니라

경기 분
score differential
red card
goal occurrence
pre-goal probability
post-goal probability

를 넣는 겁니다.

예를 들어 축구라면:

score = 0:0
득점 시간
10분
30분
60분
80분
90분+

별로 분리합니다.

당연히 10분의 선제골과 89분의 선제골이 가지는 정보량이 다릅니다.

⸻

11. 스포츠 Market Metadata도 생각보다 자세합니다

현재 공식 SDK에서 sports market에는:

sports_market_type
line
game_id
game_start_time

가 따로 존재합니다.  

그리고:

get_sports()
get_sports_market_types()
list_teams()

도 지원합니다.  

그래서 자동으로

Soccer
 ├ Moneyline
 ├ Draw
 ├ Spread
 └ Total
NBA
 ├ Moneyline
 ├ Spread
 └ Total

등으로 dataset을 정규화하는 데 활용할 수 있습니다.

Gamma API는 이외에도 market/event의 tag, series, 날짜, liquidity, volume, best bid/ask, spread, last trade price 등 매우 많은 메타데이터를 제공합니다. Polymarket Institute의 예시 market 응답만 해도 90개가 넘는 필드가 소개돼 있습니다.  

⸻

12. Comments도 API가 있습니다

조금 별도 주제이지만 꽤 재미있습니다.

공식 SDK는:

list_comments()
list_comments_by_user_address()
get_comment_thread()

를 제공합니다.

심지어:

holders_only
get_positions

옵션도 있습니다.  

따라서 향후에는:

시장 sentiment
↓
가격변화
↓
실제 결과

를 연구할 수도 있습니다.

LLM을 써서 comment를:

positive
negative
uncertain
news
emotion

등으로 분류하는 것도 가능합니다.

다만 스포츠의 핵심 연구에는 우선순위가 낮습니다.

⸻

13. 그리고 중요한 제한: 과거 Order Book은 가격 History와 다릅니다

여기는 꼭 구분하셔야 합니다.

공식 API에서 과거 가격은

prices-history

로 백필할 수 있습니다. Polymarket Institute도 이 엔드포인트를 연구자용 예제로 직접 안내합니다.  

하지만:

과거의 완전한 L2 order book을 원하는 시점마다 공식 API로 자유롭게 복원하는 것

은 별개의 문제입니다.

그래서 앞으로는 실시간 Order Book WebSocket을 저장해두는 게 좋습니다.

가격은 나중에 백필해도 되지만:

bid/ask depth
orderbook shape

은 지금부터 저장하는 편이 안전합니다.

⸻

14. 다행히 이미 대규모 공개 Order Book 데이터셋도 있습니다

제가 검색하면서 사용자님 연구에 상당히 유용해 보이는 자료도 발견했습니다.

Rocklabs Polymarket Dataset

2026년부터 수집된 Polymarket tick-level order book 데이터셋입니다.

현재 공개 설명 기준:

31.6B+ order book records
45M+ trades
2026-01/02 ~
220K+ markets

수준이고,

book snapshots
price changes
cancellations
last_trade_price

등을 포함한다고 설명합니다.  

특히 연구자에게는 무료 접근을 제공한다고 명시하고 있습니다.  

이건 사용자님에게 상당히 중요해 보입니다.

왜냐하면:

“나는 올해부터 collector를 만들었는데 과거 tick orderbook이 없다.”

라는 문제가 상당 부분 해결될 수도 있기 때문입니다.

⸻

15. 또 1.1 billion record짜리 공개 연구 데이터도 있습니다

Shanghai Innovation Institute / 여러 대학 연구자들이 만든:

Polymarket Data

프로젝트도 있습니다.

공개 설명 기준:

107 GB
1.1 billion records
268K+ markets

이며 Polygon blockchain에서 직접 데이터를 가져옵니다.  

특히:

block_number
maker/taker
fees
order_hash
market_id
direction
price
USD amount

등이 들어 있습니다.

그래서 blockchain 수준의 재현성을 확보하고 싶다면 상당히 좋은 자료입니다.

⸻

16. 사용자님 연구용 DB를 이렇게 만드는 것을 추천합니다

지금의 1분 collector를 버리는 게 아니라 Level 1로 유지하고 위에 데이터를 추가하는 방식입니다.

EVENT
event_id
game_id
sportradar_game_id
sport
league
home
away
start_time
result
          │
          ├────────────────────────┐
          │                        │
GAME_STATE                 MARKET
timestamp                  condition_id
elapsed                    asset_id
period                     market_type
home_score                 line
away_score                 outcome
status
          │                        │
          └───────────┬────────────┘
                      │
                 MARKET_STATE
                      │
      ┌───────────────┼───────────────┐
      │               │               │
    PRICE           TRADE           ORDERBOOK
 timestamp          timestamp        timestamp
 price              price            best_bid
                    size             best_ask
                    side             bid_depth
                    wallet           ask_depth
                                     spread
      │               │               │
      └───────────────┼───────────────┘
                      │
                  RESULT
                      │
                resolved_outcome

그러면 앞으로는 거의 모든 것을 재분석할 수 있습니다.

⸻

제가 생각하는 가장 강한 연구 방향

사용자님이 원래 생각하셨던

“사람들은 스포츠 진행 중 확률을 체계적으로 과대평가/과소평가하는가?”

를 조금 더 발전시키면:

Event-driven mispricing in live sports prediction markets

가 됩니다.

그리고 feature를:

P(t)                  Polymarket probability
ΔP_goal               득점 직후 변화
reversion_1m
reversion_5m
reversion_10m
score_diff
game_minute
trade_volume
trade_count
bid_ask_spread
orderbook_imbalance
market_liquidity
open_interest
trader_quality

로 가져갑니다.

특히

OrderBookImbalance
=
\frac{BidVolume-AskVolume}
{BidVolume+AskVolume}

까지 계산할 수 있습니다.

그런 다음:

득점 후 가격이 20% 올랐는데 다시 5% 내려간다

수준에서 끝나는 게 아니라,

어떤 조건에서 overshoot가 발생하는가?

까지 설명할 수 있습니다.

⸻

그래서 지금 당장 수집기를 바꾼다면

저라면 기존 1분 polling은 그대로 유지합니다.

거기에 딱 세 개를 추가합니다.

① Sports WebSocket
   score / elapsed / period 저장
② Market WebSocket
   bid/ask + orderbook update 저장
③ Trades
   실제 체결 이벤트 저장

그리고 경기 끝나면:

Price History → 백필/검증
Resolution → 최종 outcome
Trades → 누락 검증

을 합니다.

그러면 실시간 데이터 + 백필 데이터가 서로 검증하는 구조가 됩니다.

이 정도면 단순 개인 분석 프로젝트가 아니라, 이전에 말씀하셨던 CS + AI/금융 쪽 논문 데이터셋으로 가져가기에 훨씬 좋은 형태입니다. 특히 이번에 확인한 공식 Sports WebSocket에 score·period·elapsed·Sportradar game ID가 있다는 점은 지금 하시는 프로젝트에서는 상당히 큰 발견이라고 봅니다.  

원하시면 다음 단계에서는 제가 현재 공식 py-sdk 0.11.x 기준으로 “연구용 Polymarket 수집 아키텍처 v2”를 실제 Python 코드와 MySQL/PostgreSQL 테이블 설계까지 만들어드릴 수 있습니다. 기존의 1분 collector와 충돌하지 않도록 sports_events, market_ticks, trades, orderbook_snapshots 식으로 분리하면 좋겠습니다.