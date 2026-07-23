# Golden Queen 전략 명세 — Crown Momentum

## 1. 가설

해결 직전의 표준 이진 시장에서 YES가 0.90을 **처음 상향 교차**하는 사건은 단순한
고확률 수준보다 새로운 정보가 우세 방향으로 집약되는 순간일 수 있다. 24시간 이내의 이
교차를 실행 가능한 호가에서 소액 매수하면 0.98 또는 resolution 1로의 수렴을 얻을 수
있다는 것이 Crown Momentum의 가설이다.

이 가설은 아직 검증되지 않았다. 0.90 가격이 실제 10% tail risk를 정확히 반영한다면
비용 후 기대값은 0 이하일 수 있다. Cherry 자료는 이 가설을 발견하는 입력이었지만
execution evidence와 snapshot이 불완전해 수익성을 입증하지 못했다.

## 2. Cherry에서 계승한 것과 제거한 것

계승:

- 해결까지 남은 시간과 우세 YES의 수렴을 함께 본다.
- 스포츠를 포함하고 짧은 경기 중에도 계속 감시한다.
- resolution, redeemable 상태, 실제 token redeem을 CLOB SELL과 분리한다. 현재 구현은
  resolution까지만 적재하고 redeemable/실제 redeem transaction은 추정하거나 수집하지
  않는다.
- 한 계정의 실제 CLOB 주문·fill을 SQLite에 남긴다.

제거·강화:

- 넓은 75~92% inventory scan 대신 0.90 첫 상향 교차만 사용한다.
- trailing, 해결 12시간 전 time exit, 여러 momentum knob를 제거한다.
- accepted BUY를 보유로 간주하지 않고 full confirmed fill을 기다린다.
- metadata liquidity만 믿지 않고 같은 order-book snapshot의 spread와 ask depth를 본다.
- 부분 SELL 자동 재시도를 없애 원장 밖 residual position을 만들지 않는다.
- 주문 금액 외의 risk gate는 코드 기본값과 자동 scale 식으로 고정한다.

## 3. 분석 근거와 한계

2026-07-23에 `golden-cherry/data/default/trades.db`를 read-only로 strict audit했다.
기간은 2026-06-23 00:00Z 이상 2026-07-23 00:00Z 미만이다.

| 항목 | 관측 |
|---|---:|
| 전체 trade | 658 |
| 상태 | COMPLETED 182 / HOLDING 331 / QUARANTINED 54 / UNFILLED 91 |
| 성공 / 실패 run | 2,246 / 215 |
| COMPLETED confirmed BUY+SELL coverage | 45.6% |
| closed trade fill 수량 불일치 | 69 |
| uncertain submission | 19 |
| stale reconciliation | 70 |
| confirmed fill fee missing | 87.6% |
| market snapshot | 0 |
| 최대 SUCCESS run gap | 445.67시간 |

strict 결과는 CRITICAL 4, HIGH 6, MEDIUM 1로 실패했다. 따라서 다음을 하지 않았다.

- legacy `realized_pnl`을 actual P&L로 사용
- order ID 또는 `live` 응답을 fill로 사용
- 로그 문자열 발생 횟수를 독립 주문/시장 수로 사용
- 현재 남아 있는 시장만으로 과거 counterfactual을 복원
- snapshot이 없는 Cherry DB에서 0.90/0.94/0.98 최적값을 사후 탐색

Queen의 숫자는 과최적화 결과가 아니라 한 달 live falsification을 위한 보수적 사전 등록값이다.

## 4. Universe

필수 조건:

1. active, not closed, orderbook enabled, accepting orders
2. outcomes가 정확히 `["Yes", "No"]`
3. 서로 다른 CLOB token이 정확히 2개이며 YES token identity가 명확
4. `negRisk=false`
5. 신규 진입에는 Gamma `event_id` 존재
6. 명시적으로 제외한 category가 아님
7. 유효한 liquidity, volume24h, bid/ask metadata

스포츠는 기본 포함한다.

- `gameStartTime` 존재: 경기 전에는 해당 시점까지 남은 시간 사용
- 시작 후: market이 tradable이고 360분 이내면 in-play 진입 허용
- `gameStartTime` 누락: 기본은 `endDate`로 fallback
- `POLYBOT_EXCLUDED_CATEGORIES`에 실제 Gamma tag slug/label을 exact match로 명시하거나
  엄격 clock env를 설정한 경우만 추가 차단

## 5. Archive와 first-crossing lineage

진입보다 넓은 envelope를 60일 보존한다.

```text
strict binary YES >= 0.80
scheduled clock <= 72h
Gamma liquidity >= min(POLYBOT_MIN_LIQUIDITY, $1,000) request envelope
```

각 cursor-complete sweep은 catalog, snapshot, membership와 하나의 transaction으로
commit된다. 현재 sweep의 양의 snapshot ID와 그 직전 persisted snapshot ID가 있어야 한다.
archive는 missing `event_id` observation도 소실시키지 않고 보존하지만, 그런 observation은
진입할 수 없다. 따라서 event ID 누락 뒤의 re-cross를 “최초”로 다시 만들지 않는다.

first-crossing 조건:

```text
persisted_previous_yes < 0.90
0.90 <= persisted_current_yes <= 0.94
0m < current.timestamp - previous.timestamp <= 15m
Queen archive envelope의 이전 60일 persisted YES >= 0.90 없음
```

처음 0.90에 도달한 observation은 liquidity, volume, event cap, fresh book에서 탈락해도
소비된다. 가격이 내려갔다 다시 올라온 것을 새 최초 교차로 바꾸지 않는다. 첫 실행에서
이미 0.90 이상인 시장도 언제 교차했는지 모르므로 진입하지 않는다.

## 6. Entry

scheduled/pregame:

```text
0h < entry_clock_hours <= 24h
```

in-play:

```text
game_start_elapsed <= 360m
```

metadata gate:

```text
effective_min_liquidity = max(10,000, buy_amount / 0.001)
effective_min_volume24h = max(2,000, buy_amount / 0.02)
```

fresh order-book gate:

```text
midpoint/signal in [0.90, 0.94]
best ask <= 0.94
spread <= 0.02
depth_limit = min(0.94, best ask + 0.01)
ask shares at price <= depth_limit >= requested shares * 1.20
```

주문은 `depth_limit`의 GTC BUY다. live 접수 직후 상태는 `PENDING_BUY`다. exact order ID의
reconciled full confirmed size/VWAP이 확인된 뒤에만 `HOLDING`이 된다. terminal zero-fill이면
`UNFILLED`이다. partial/unknown이면 pending을 유지한다.

## 7. Exit 우선순위

### 7.1 Resolution evidence

midpoint를 조회할 수 없을 때 Gamma `closed`, 최종 outcomePrices, resolution status가 모두
일치하면 resolution을 기록한다. live에서는 exact confirmed BUY fill이 있는 포지션만
settlement assumption을 만들 수 있다. 이는 cash realization이 아니며 redeemable 상태 및
actual redeem transaction과 구분한다. 현재 Queen에는 actual redeem ingestion이 없으므로
resolution만으로 redeem coverage나 현금 실현을 주장하지 않는다.

### 7.2 목표

```text
current_yes >= immutable_take_profit_price_at_entry (기본 0.98)
fresh_best_bid >= immutable_take_profit_price_at_entry
```

두 조건이 모두 맞을 때만 full confirmed BUY size의 SELL을 낸다. signal만 0.98이고 bid가
낮으면 기다린다.

### 7.3 손절

```text
current_yes <= immutable_stop_price_at_entry (기본 0.85)
```

fresh best bid에 exact-size SELL을 낸다. `0.85`는 보장 체결가가 아니라 trigger다. trailing
stop과 time exit은 없다.

live SELL은 `PENDING_SELL`이다. BUY/SELL full confirmed size가 같고 양쪽 fee가 확정된
뒤에만 actual net P&L과 `COMPLETED`를 기록한다. terminal zero-fill SELL은 `HOLDING`으로
되돌린다.

## 8. Exposure

```text
max positions = 20
max event positions = 1
max open requested notional = buy_amount * 10
max new positions per cycle = 1
same-condition cooldown = 168h
buy amount hard cap = $1,000
```

`max positions=20`은 누적 trade 수나 지갑 전체 포지션 수가 아니다. 해당 `--job` DB에서
`PENDING_BUY/HOLDING/PENDING_SELL`인 동시 state 상한이다. open notional 상한이 더 먼저
작동할 수 있다.

## 9. A/B 사전 등록

대조군과 실험군은 아래 한 줄만 다르다.

| 항목 | 대조군 | 실험군 |
|---|---:|---:|
| `POLYBOT_ENTRY_HOURS_MAX` | 24 | 12 |

그 밖의 commit, schedule, 계정 규모, 진입/청산, liquidity/volume/depth, sports, cap은
고정한다. 각각 별도 계정·Jenkins job·`--job` DB를 사용한다.

평가 단위:

- 같은 event의 여러 condition은 event-cluster 1개
- 양쪽에 나온 같은 event는 paired observation
- carry-in/carry-out은 분리
- actual P&L은 exact confirmed fill + known fee만
- resolution-only position은 cash P&L과 분리

## 10. Falsification

최소 30일과 terminal event-effective n 30을 모두 충족하기 전에는 승격·증액하지 않는다.
다음 중 하나면 `STOP` 또는 계측 복구다.

- strict audit에 CRITICAL/HIGH issue 존재
- run/config provenance, cursor-complete sweep, entry lineage coverage가 불완전
- BUY/SELL full confirmed fill 또는 fee coverage가 분석 가능한 수준에 못 미침
- event-cluster 비용 후 EV <= 0
- 한 terminal loss event를 제거하면 edge가 생기거나, 반대로 한 event가 이익 대부분을 설명
- 12h와 24h 양쪽 모두 비용 후 음수
- target/stop 주문의 actual slippage·partial/unknown 비율이 사전 손실 모델을 무효화
- 동일 이벤트 상관을 제거하면 유효 표본이나 edge가 소멸

## 11. Competing explanations

- 0.90 교차는 정보가 아니라 stale midpoint/얇은 호가의 노이즈일 수 있다.
- 우세 확률은 정확하며 남은 upside는 fee·spread·tail loss를 보상하지 못할 수 있다.
- 짧은 horizon의 좋은 결과는 스포츠 category mix나 특정 event 한두 개의 효과일 수 있다.
- 12h 우위는 진짜 시간 효과가 아니라 낮은 체결률과 작은 표본의 결과일 수 있다.

구분할 측정치:

- current snapshot↔fresh CLOB price 차이
- spread/depth/filled fraction과 latency
- event/category별 paired 결과
- 12h/24h 공통 event의 차이
- fee 포함 actual P&L과 resolution assumption의 차이

## 12. Offline replay의 역할

`scripts/backtest.py`는 immutable CSV에서 사전 등록한 12h/24h 두 parameter row만
재생한다. observed ask/bid를 hypothetical fill로 사용하고 confirmed-fill 열은 `null`이다.
CSV에 full depth와 `gameStartTime`가 없으므로 production 실행 가능성을 증명하지 않는다.
이 스크립트는 unit test가 아니라 leakage·기간·first crossing을 재현하는 research artifact다.

## 13. 공식 API 계약

- [CLOB API introduction](https://docs.polymarket.com/api-reference/introduction)
- [Get CLOB market info](https://docs.polymarket.com/api-reference/markets/get-clob-market-info)
- [Get order book](https://docs.polymarket.com/api-reference/market-data/get-order-book)
- [Post a new order](https://docs.polymarket.com/api-reference/trade/post-a-new-order)
- [Orderbook overview](https://docs.polymarket.com/trading/orderbook)
- [Fees](https://docs.polymarket.com/trading/fees)

이 문서들은 token/orderbook/order 처리 계약의 primary source다. Crown Momentum의 edge를
입증하는 자료가 아니며, 수수료는 시장·category·liquidity role에 따라 달라질 수 있으므로
실제 fill의 fee evidence를 사용한다.
