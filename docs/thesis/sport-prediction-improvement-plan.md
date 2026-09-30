# Polymarket 스포츠 Prediction Market 연구 시스템 확장 요청

현재 저는 Polymarket의 스포츠 마켓을 대상으로 다음과 같은 연구를 진행하고 있습니다.

핵심 연구 목적은 다음과 같습니다.

- 스포츠 경기 중 시장 참여자들이 실제 경기 상황에 비해 승리 확률을 과대평가 또는 과소평가하는지 분석
- 득점, 실점, 퇴장, 경기 시간 변화 등의 실제 경기 이벤트 직후 Polymarket 가격이 어떻게 반응하는지 분석
- 급격한 가격 변화 이후 가격이 되돌림(mean reversion)을 보이는지 분석
- 시장 유동성, 거래량, 주문장 구조, 참여자 특성이 이러한 과잉반응에 영향을 미치는지 분석
- 향후 학술 논문에 사용할 수 있는 재현 가능한 데이터셋 구축

현재 시스템에는 이미 Polymarket 스포츠 시장의 가격을 약 1분 단위로 수집하는 기능이 존재합니다.

기존 시스템을 삭제하거나 대체하지 말고, 현재 구조를 먼저 분석한 뒤 기존 기능과 데이터의 하위 호환성을 유지하면서 아래 기능을 단계적으로 추가해 주세요.

---

# 1. 기존 시스템 먼저 분석

구현 전에 반드시 현재 프로젝트를 먼저 조사해 주세요.

다음을 파악합니다.

- 현재 Polymarket API 사용 방식
- 마켓 탐색 방법
- event_id / market_id / condition_id / token_id / asset_id 관리 방식
- 스포츠 경기 매칭 방식
- 현재 1분 가격 수집 로직
- 데이터베이스 구조
- Jenkins 또는 scheduler 구조
- 중복 데이터 방지 방식
- 재시작 및 장애 복구 방식
- 이미 저장되고 있는 timestamp의 timezone
- 현재 데이터가 스포츠 경기와 어떤 key로 연결되어 있는지

기존 구조에서 재사용할 수 있는 것은 최대한 재사용합니다.

대규모 rewrite보다 incremental migration을 우선합니다.

---

# 2. 최신 Polymarket 공식 SDK 적용

가능하면 Polymarket의 최신 공식 `py-sdk`를 우선 사용합니다.

특히 다음 기능을 조사하고 적용합니다.

- `PublicClient`
- historical price API
- trades API
- market metadata
- sports metadata
- live volume
- open interest
- holders
- leaderboard / trader statistics
- comments
- resolution 관련 API

공식 SDK로 제공되지 않는 기능이 필요한 경우에만 CLOB API / Gamma API / Data API를 직접 호출합니다.

각 기능이 어느 API에서 오는지 코드와 문서에 명시해 주세요.

예:

- Gamma API
- Data API
- CLOB API
- WebSocket
- py-sdk wrapper

---

# 3. Polymarket Sports Stream 적용

Polymarket 공식 Sports Stream / Sports WebSocket 기능을 조사하고 실시간 수집기에 추가해 주세요.

가능하면 다음 필드를 저장합니다.

- game_id
- sportradar_game_id
- league
- league_abbreviation
- home_team
- away_team
- status
- live
- ended
- score
- home_score
- away_score
- period
- elapsed
- finished_at
- turn
- received_at
- source_timestamp

특히 `sportradar_game_id`가 존재할 경우 반드시 저장합니다.

향후 외부 sports data provider와 join할 수 있도록 설계합니다.

Sports Stream에서 들어오는 원본 메시지도 가능하면 JSON 형태로 별도 저장할 수 있게 해 주세요.

---

# 4. 실시간 Market WebSocket 적용

기존 1분 polling과 별도로 Polymarket Market WebSocket을 이용한 실시간 데이터 수집 기능을 추가합니다.

수집 대상으로 다음을 고려합니다.

- price change
- best bid
- best ask
- midpoint
- spread
- last trade price
- order book update
- market lifecycle event
- market closed / resolved 상태

Polling을 제거하지 마세요.

Polling은 fallback 및 validation 용도로 계속 유지합니다.

---

# 5. Order Book 별도 저장

Order Book을 가격과 분리해서 저장합니다.

최소한 다음을 저장하고 싶습니다.

- timestamp
- asset_id
- condition_id
- best_bid
- best_ask
- midpoint
- spread
- bid_depth
- ask_depth
- bid_volume
- ask_volume

가능하다면 여러 depth level도 저장합니다.

예:

- bid level 1~10
- ask level 1~10

또는 raw orderbook snapshot을 JSON으로 저장합니다.

데이터 용량이 지나치게 커질 경우 다음 두 구조를 함께 고려합니다.

1. 일정 간격 snapshot
2. WebSocket delta

복원 가능성과 저장 용량을 비교해 최적 구조를 제안해 주세요.

---

# 6. Historical Price Backfill

경기 종료 후 Historical Price API를 사용해 누락 데이터를 자동으로 백필합니다.

가능하면:

- 1분 bucket
- 경기 시작 전 일정 구간
- 경기 중
- 경기 종료 후 일정 구간

까지 가져옵니다.

예:

- kickoff - 60분
- kickoff
- match duration
- finish + 30분

기존 실시간 1분 데이터와 historical API 결과를 비교하여 누락 여부를 검사합니다.

이미 존재하는 데이터는 중복 삽입하지 않습니다.

백필된 데이터인지 실시간 수집 데이터인지 구분할 수 있도록 합니다.

예:

`source = live_polling | websocket | historical_backfill`

---

# 7. 실제 Trade 데이터 저장

가격 데이터와 실제 체결 데이터를 반드시 분리합니다.

PublicClient 또는 Data API의 trades 기능을 사용해 다음 정보를 저장합니다.

- timestamp
- event_id
- condition_id
- asset_id
- market_id
- wallet
- side
- price
- size
- USD notional
- taker/maker 여부
- transaction hash 또는 trade id가 있으면 저장

연구에서는 가격 변화뿐 아니라 실제 주문 흐름을 분석할 예정입니다.

---

# 8. Volume / Open Interest 저장

가능한 경우 다음 정보를 주기적으로 수집합니다.

- event live volume
- market volume
- open interest
- liquidity
- trade count

가격 변화와 거래량 변화를 함께 분석할 수 있도록 timestamp 기반으로 저장합니다.

---

# 9. Market Metadata 확장

각 스포츠 market에 대해 최대한 풍부한 metadata를 저장합니다.

예:

- event_id
- condition_id
- market_id
- question
- slug
- asset_id
- token_id
- outcome
- line
- sports_market_type
- game_id
- game_start_time
- league
- sport
- home_team
- away_team
- liquidity
- total volume
- created_at
- close_time
- resolved_at

특히 다음 market type을 구분할 수 있게 합니다.

- moneyline
- draw
- spread
- total
- 기타

---

# 10. Resolution 데이터 저장

경기 종료 후 실제 Polymarket resolution 결과를 저장합니다.

예:

- resolved
- winning outcome
- resolution timestamp
- resolution source
- payout

이 데이터를 이용해 향후 calibration 분석을 수행할 예정입니다.

예:

- Brier score
- calibration curve
- reliability diagram
- predicted probability vs actual result

---

# 11. Trader / Wallet 연구 데이터

Polymarket에서 공개적으로 제공되는 범위 내에서 trader 분석용 데이터를 수집할 수 있도록 합니다.

조사 대상:

- user stats
- user PnL
- user volume
- holders
- market holders
- leaderboard
- biggest winners
- wallet별 포지션

개인정보를 추론하는 것이 목적이 아니라 공개 wallet-level market behavior 분석이 목적입니다.

가능하면 trader를 다음과 같이 향후 분류할 수 있도록 데이터를 준비합니다.

- high-performing trader
- regular trader
- new/infrequent trader
- large holder
- small holder

---

# 12. Holder Concentration 분석 가능하도록 구성

market별 보유자 구조를 저장할 수 있다면 저장합니다.

향후 다음 지표를 계산할 수 있게 합니다.

- Top 1 holder share
- Top 5 holder share
- Top 10 holder share
- HHI
- Gini coefficient

가격 과잉반응과 시장 집중도의 관계를 연구할 수 있게 합니다.

---

# 13. Comments / Sentiment 데이터

우선순위는 낮지만 Polymarket Comments API가 존재한다면 선택적으로 수집 가능한 구조를 추가합니다.

가능하면:

- comment timestamp
- user/wallet
- market/event
- comment text
- parent comment
- holder 여부

를 저장합니다.

향후 LLM 또는 NLP로 다음을 분석할 예정입니다.

- positive
- negative
- uncertainty
- news
- emotion
- crowd sentiment

Comments 수집은 core collector와 분리하여 feature flag로 활성화할 수 있게 합니다.

---

# 14. 경기 이벤트와 시장 이벤트를 정확히 Join

가장 중요한 부분 중 하나입니다.

Sports Stream과 market data를 동일한 UTC timestamp 기준으로 맞춥니다.

예:

`goal at T`

에 대해 다음 window를 자동으로 추출할 수 있게 합니다.

- T - 10m
- T - 5m
- T - 1m
- T
- T + 10s
- T + 30s
- T + 1m
- T + 3m
- T + 5m
- T + 10m

향후 다음과 같은 지표를 계산합니다.

- pre-event probability
- immediate price reaction
- peak response
- overshoot
- 1m reversion
- 5m reversion
- 10m reversion
- volume reaction
- spread reaction
- orderbook imbalance reaction

---

# 15. Order Book Imbalance 계산

다음 지표를 계산할 수 있도록 합니다.

Order Book Imbalance:

`(BidVolume - AskVolume) / (BidVolume + AskVolume)`

가능하다면:

- level 1 imbalance
- top 5 levels imbalance
- top 10 levels imbalance
- total visible depth imbalance

를 각각 계산합니다.

추후 dataframe 또는 분석 테이블에서 쉽게 사용할 수 있게 합니다.

---

# 16. 연구용 Dataset / Feature Layer 구축

raw data를 직접 분석하지 않고 별도의 research feature layer를 만듭니다.

예를 들어 한 관측치를 다음과 같이 만들 수 있어야 합니다.

- sport
- league
- event_id
- game_id
- market_id
- condition_id
- market_type
- outcome
- timestamp
- game_minute
- period
- home_score
- away_score
- score_diff
- current_probability
- probability_change_10s
- probability_change_30s
- probability_change_1m
- probability_change_5m
- probability_change_10m
- volume_1m
- trade_count_1m
- buy_volume
- sell_volume
- net_order_flow
- bid
- ask
- spread
- midpoint
- orderbook_imbalance
- liquidity
- open_interest
- holder_concentration
- final_result

가능하면 Parquet 또는 분석용 SQL view 형태로도 export 가능하게 합니다.

---

# 17. 외부 Historical Dataset 조사

공식 Polymarket 데이터만으로 과거 order book 복원이 불가능할 경우 다음 데이터 소스도 조사합니다.

예:

- Rocklabs Polymarket Dataset
- blockchain 기반 Polymarket datasets
- academic public datasets
- Polygon on-chain data

단, 자동으로 외부 유료 서비스를 도입하지 않습니다.

사용 가능한 범위와 라이선스, 데이터 기간, 해상도만 문서화합니다.

---

# 18. Research Metrics 구현

다음 지표를 분석 가능하도록 helper 또는 analysis module을 작성합니다.

## Price reaction

- delta price 10s
- delta price 30s
- delta price 1m
- delta price 5m
- delta price 10m

## Reversion

- peak price
- post-event peak time
- reversion after 1m
- reversion after 5m
- reversion after 10m

## Liquidity

- spread
- depth
- order book imbalance

## Trading activity

- volume
- trade count
- buy/sell imbalance
- average trade size
- median trade size

## Calibration

- Brier score
- calibration bucket
- empirical win rate

---

# 19. 스포츠별 특성 고려

우선 축구 데이터를 가장 중요하게 봅니다.

축구에서는 다음 이벤트를 우선 분석합니다.

- goal
- score change
- red card
- halftime
- second-half start
- full time

특히 다음 변수를 중요하게 봅니다.

- game minute
- score differential
- pre-goal probability
- post-goal probability

예:

10분 선제골과 89분 선제골의 시장 반응은 분리해서 분석할 수 있어야 합니다.

향후 다음 스포츠로 확장할 수 있게 구조를 일반화합니다.

- MLB
- NBA
- NFL
- NHL
- tennis
- esports

---

# 20. 데이터 품질 검증

collector에 반드시 validation 기능을 넣습니다.

예:

- timestamp 역전
- 동일 trade 중복
- 가격 범위 오류
- bid > ask
- asset/market 매핑 실패
- Sports game과 market join 실패
- 지나치게 긴 데이터 gap
- WebSocket disconnect
- backfill 실패

특히 실시간 가격과 historical backfill 가격이 일정 수준 이상 차이날 경우 기록합니다.

---

# 21. 장애 복구

collector가 장시간 계속 운영될 것을 전제로 합니다.

다음을 고려합니다.

- WebSocket reconnect
- exponential backoff
- rate limit
- API timeout
- graceful shutdown
- process restart
- duplicate prevention
- checkpoint
- missed range detection
- automatic backfill

프로세스가 30분 죽었다가 살아나더라도 누락된 데이터를 자동으로 찾고 복구할 수 있어야 합니다.

---

# 22. 저장 용량 관리

Order Book과 Trades는 데이터가 빠르게 증가할 수 있습니다.

따라서 예상 데이터 크기를 계산하고 저장 전략을 제안합니다.

예:

- raw websocket
- normalized DB
- compressed Parquet
- daily partition
- monthly archive

가능하면 연구용 raw 데이터는 삭제하지 않는 방향을 우선합니다.

---

# 23. DB Schema 제안

기존 DB를 확인한 뒤 최소한 다음 conceptual table을 검토합니다.

- sports_events
- sports_game_state
- polymarket_events
- polymarket_markets
- market_assets
- price_ticks
- trades
- orderbook_snapshots
- orderbook_levels
- market_metrics
- market_volume
- open_interest
- holders
- trader_stats
- resolutions
- comments
- collector_checkpoints

기존 테이블과 겹치는 경우 새 테이블을 무조건 만들지 말고 통합 가능한지 먼저 판단합니다.

---

# 24. 시간 기준

모든 원본 timestamp는 UTC로 저장합니다.

표시할 때만 local timezone으로 변환합니다.

다음 시간을 구별합니다.

- source_event_time
- exchange/server timestamp
- received_at
- inserted_at

latency 연구가 가능하도록 source time과 local receive time을 가능하면 동시에 저장합니다.

---

# 25. Raw Data 보존

향후 API parser가 잘못됐다는 사실이 발견될 수도 있으므로 중요한 WebSocket/API 데이터는 raw JSON 형태도 선택적으로 저장합니다.

normalized data와 raw data를 모두 보존할 수 있게 합니다.

---

# 26. Collector와 Analysis 분리

수집 프로그램과 연구 분석 프로그램을 강하게 분리합니다.

예:

`collector/`
- sports stream
- market stream
- trades
- historical backfill

`storage/`
- models
- repositories

`analysis/`
- event study
- calibration
- microstructure
- trader analysis

`jobs/`
- reconciliation
- backfill
- validation

Collector 내부에 연구 통계 로직을 과도하게 넣지 않습니다.

---

# 27. 기존 1분 Polling은 유지

현재 운영 중인 1분 가격 수집은 삭제하지 마세요.

오히려 다음 역할로 유지합니다.

- fallback
- WebSocket validation
- historical API validation
- 장애 탐지

최종적으로 같은 timestamp에 여러 source가 있을 경우 어떤 값을 canonical value로 선택할지도 정책을 제안해 주세요.

---

# 28. 단계별 구현

한 번에 모든 기능을 구현하지 말고 다음 순서로 작업합니다.

## Phase 1
현재 시스템 분석

## Phase 2
Sports Stream

## Phase 3
Market WebSocket + Order Book

## Phase 4
Trades

## Phase 5
Historical backfill

## Phase 6
Volume / Open Interest / Metadata / Resolution

## Phase 7
Trader / Holder data

## Phase 8
Research feature layer

## Phase 9
Validation / recovery / monitoring

각 Phase가 끝날 때 실제 데이터를 이용하여 작동 여부를 검증합니다.

---

# 29. 최종적으로 제가 알고 싶은 연구 질문

시스템 설계 판단에서 다음 연구 질문을 우선 고려해 주세요.

### 핵심 질문

"스포츠 경기 중 새로운 정보가 발생했을 때 prediction market은 정보를 효율적으로 반영하는가, 아니면 체계적인 overreaction / underreaction을 보이는가?"

특히:

- 득점 직후 확률이 지나치게 움직이는가?
- 이후 가격이 되돌아오는가?
- 경기 후반일수록 반응이 다른가?
- score difference에 따라 다른가?
- low liquidity 시장에서 현상이 더 큰가?
- bid-ask spread가 큰 시장에서 현상이 더 큰가?
- order book imbalance가 선행 신호가 되는가?
- 큰 trader가 먼저 반응하는가?
- 일반 trader가 뒤늦게 따라가는가?
- volume surge가 price overshoot와 관계가 있는가?
- trader concentration이 가격 효율성에 영향을 주는가?
- Polymarket 가격은 실제 결과와 calibration되어 있는가?

이 질문들을 나중에 분석할 수 있도록 현재 데이터 모델을 설계해 주세요.

---

# 30. 결과물

작업 후 다음을 제공해 주세요.

1. 현재 시스템 구조 분석
2. 현재 시스템에서 부족한 점
3. 적용 가능한 Polymarket API 목록
4. 공식 API / 비공식 API 구분
5. 새로운 데이터 아키텍처
6. DB schema 변경안
7. migration plan
8. 실제 코드 변경
9. 실행 방법
10. 데이터 검증 방법
11. 예상 API rate / storage usage
12. 장애 복구 전략
13. 향후 연구 가능한 feature 목록
14. 아직 확보할 수 없는 데이터 목록
15. 추가하면 좋은 연구 아이디어

중요:

기능이 실제 Polymarket API에서 지원되는지 추측하지 말고 반드시 최신 공식 문서, 공식 GitHub SDK 또는 실제 API response를 확인한 뒤 구현합니다.

API나 SDK의 field 이름도 추측하지 말고 현재 설치 버전 또는 최신 공식 소스에서 확인합니다.

가능하면 간단한 test script를 먼저 작성해 실제 응답을 확인한 후 production collector에 통합합니다.

그리고 기존 정상 동작 중인 collector를 망가뜨리지 않는 것을 최우선으로 합니다.