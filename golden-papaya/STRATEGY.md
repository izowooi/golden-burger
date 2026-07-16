# Golden Papaya — Final Five

## 1. 사전 등록 가설

표준 이진 시장의 YES가 해결까지 0시간 초과 72시간 이하로 남았을 때 archive cadence에서
처음 0.95 상향 돌파로 관측되면, 그 교차는 단순한 고확률 수준보다 강한 정보 집약
신호이며 해결까지 1.00으로 수렴할 가능성이 비용 후에도 충분히 높다. 이를 **Final Five**라
부른다. 봇은 `[0.95, 0.97]`에서 YES를 소액 매수하고, 수렴 가설이 깨졌음을 나타내는 절대
가격 0.90만 사전 해결 청산 신호로 사용한다.

이 가설은 아직 입증되지 않았다. 0.95는 시장이 실제 5% tail risk를 정확히 반영한 가격일
수 있고, 교차가 정보가 아니라 얇은 호가 한 건의 결과일 수도 있다. 따라서 최초 배포는
수익화가 아니라 falsification을 위한 simulation/소액 cohort다.

## 2. 기존 전략과의 구분

- `golden-cherry/date`: 더 넓은 고확률 밴드와 시간/모멘텀 규칙을 사용한다. papaya는
  **first observed 0.95 upward crossing**이라는 사건 하나만 검증한다. 실제 연속시간 교차는
  sweep 사이에 있어 interval-censored될 수 있다.
- `golden-mango`: 확률과 시간을 연환산 carry 식으로 환산하고 0.99/time exit을 사용한다.
  papaya는 carry hurdle, pre-resolution TP, time exit이 없다.
- `golden-fig`: 롱샷 YES를 페이드해 NO를 산다. papaya는 strict binary YES-only다.

## 3. Universe

다음을 모두 만족해야 한다.

1. active, not closed, orderbook enabled, accepting orders
2. outcomes를 정규화했을 때 정확히 `Yes`, `No` 두 개
3. token ID가 정확히 두 개이며 YES token을 명확히 식별 가능
4. 표준(non-negRisk) 이진 시장
5. 유동성 `>= $1,000`, 24h 거래량 `>= $0`
6. 종료시각이 있고 `0h < hours_left <= 72h`

같은 event 아래의 파생 시장은 서로 독립 표본이 아니다. `market_catalog.event_id` 기준
동시 노출을 1개로 제한하고 회고에서도 명목 시장 n과 event-cluster 유효 n을 함께 낸다.

## 4. 진입 규칙

시점 `t-1`의 유효한 직전 snapshot과 현재 `t`를 사용한다.

```text
current_snapshot_id is positive and persisted in the current sweep
0m < current_snapshot_at - previous_snapshot_at <= 30m
previous_yes < 0.95
no earlier persisted YES >= 0.95
0.95 <= current_yes <= 0.97
0 < hours_left <= 72
strict_standard_binary == true
event_open_positions == 0
portfolio_open_positions < 20
```

모두 참일 때만 BUY 후보가 된다. 경계는 inclusive이고 float 비교는 구현의 epsilon 계약을
따른다. current snapshot은 현재 sweep에서 실제 저장·commit된 양의 ID여야 한다. 직전
snapshot이 없거나 두 관측의 간격이 기본 30분을 넘으면 상향 교차를 증명할 수 없으므로 fail
closed한다. 이미 0.95 이상에서 발견된 시장이나 0.97을 넘어 gap-up한 시장도 first observed
crossing으로 소급하지 않는다. archive/run gap이 있으면 실제 최초 교차를 주장하지 않고 관측
구간을 interval로 보존한다.

first crossing은 주문이 아니라 **최초 threshold 관측 사건**이다. 현재 persisted snapshot이
처음 0.95에 도달한 순간 one-shot을 소진하며, 유동성·volume·잔여시간·portfolio/event cap·
fresh ask·최소 주문 크기 등 후속 gate가 거부해도 되돌리지 않는다. 이후 0.95 아래로 내려갔다
다시 오른 re-crossing은 후보가 아니다. 이 규칙은 주문 실패를 선택적으로 재시도해 표본을
왜곡하는 것을 막는다.

교차 후보가 생기면 fresh best ask를 다시 조회한다. ask가 없거나 실행 가능 ask가 0.97을
넘으면 진입하지 않는다. $5 주문 가격은 이 fresh ask를 기준으로 만든다. 전략 `trades` 행의
접수 가격·수량·`HOLDING`은 주문 intent/lifecycle 기록이지 fill 증거가 아니다. 실제 노출
size와 entry price는 exact order ID로 연결된 `order_fills.status='CONFIRMED'`만으로 확정한다.
live resolution 또한 confirmed BUY fill이 없으면 포지션 정산으로 승격하지 않는다.

## 5. 청산·해결·상환

### 5.1 미해결 상태

YES current/signal midpoint가 **0.90 이하**이면 `absolute_stop`을 발생시키고 fresh best bid를
확인해 SELL을 시도한다. 진입가 대비 수익률이 아닌 절대 가격 기준이다. 실제 매도 성과는
fresh bid와 confirmed fill로 평가한다. 사전 해결 take-profit, trailing stop, max holding,
time exit은 없다.

### 5.2 해결 이후

resolution 결과와 payout, redeem 가능 여부, 실제 redemption transaction은 서로 다른
증거다.

- resolution만 확인: YES payout 1, NO payout 0, 드문 ambiguous payout 0.5를 outcome
  evidence로 기록하되 realized cash로 계산하지 않음
- redeemable 확인: 청구 가능한 상태로 분류, SELL 주문을 만들지 않음
- redemption 확인: transaction/지갑 증거와 금액을 별도 원장에 기록
- 해결 전 confirmed SELL: 실제 fill size/price/fee로 계산

DB trade를 임의로 1.00에 매도된 것으로 마감하거나 `EXPIRED`를 수익으로 간주하지 않는다.

## 6. Archive 계약

중앙 nectarine/honeydew archive의 유동성 하한은 papaya의 $1,000 universe를 덮지 못한다.
papaya가 자체로 다음 request envelope를 keyset cursor 끝까지 수집한다.

```text
YES probability >= 0.80
hours_left <= 168
liquidity >= 1,000
volume24h >= 0
retention >= 60 days
```

entry cohort의 liquidity/volume 하한을 높여도 archive request는 유동성 $1,000·volume $0
baseline을 유지한다. 그래야 높은 운영 gate에서 거부된 최초 crossing도 durable history에 남아
후속 re-crossing을 차단하고 반사실 분석에 포함할 수 있다.

각 sweep은 filter, cursor completion, qualifying count/digest, membership, snapshot/catalog
coverage를 남긴다. Jenkins schedule/run manifest에서 해당 cohort의 기대 cadence를 읽고
actual bucket coverage와 run gap을 측정한다. 이 envelope가 있어야 0.95 교차를 놓친 시장과
stop threshold의 반사실을 검증할 수 있다.

## 7. 왜 실패할 수 있는가

1. **정확한 tail risk**: 0.95가 과소평가가 아니라 정직한 95% 확률이면 비용 후 기대값이
   음수다. 0.95 진입의 gross return은 약 5.26%이고, 0으로 해결되는 1건은 약 19승을 지운다.
2. **교차의 허위 정밀도**: 얇은 orderbook, stale midpoint, 작은 주문이 0.95 교차를 만들 수
   있다. 이것은 정보 집약이 아니라 microstructure noise다.
3. **stop 실행 불가**: 유동성 $1,000/volume $0 기본값에서는 0.90을 관측해도 bid가 없거나
   spread가 넓어 SELL이 미체결·부분체결되거나 훨씬 낮은 가격에 체결될 수 있다. 0.90은
   손실 보장이 아니라 주문 트리거다.
4. **resolution ambiguity**: 문구 해석, UMA dispute, 시장 무효화가 마지막 5%에 집중될 수 있다.
5. **상관 집중**: 같은 경기/선거의 여러 파생 시장을 독립 승리로 세면 표본과 분산이 왜곡된다.
6. **비용과 자본 잠김**: spread, fee, maker/taker role, partial/unfilled, redeem 지연이 작은
   gross edge를 소거할 수 있다.

경쟁 설명은 “95% 교차 후 수렴”이 아니라 “대부분의 95% 계약은 원래 이기며, 실패한 계약과
미체결 stop이 데이터에서 누락된다”이다. 회고는 archive 전체와 confirmed execution으로 이
설명을 먼저 배제해야 한다.

## 8. 검증과 기각 기준

### Evidence gate

- `config_hash × git_commit × mode × job_name` cohort 분리
- run/config/catalog 및 first-observed-crossing snapshot lineage coverage 100%
- BUY/SELL confirmed-fill coverage와 fee/role coverage를 각각 보고
- pending/unknown submission, overfill, unresolved reconciliation 0
- resolution/redeem evidence를 order fill과 분리
- archive cursor completion과 거래 condition join coverage 확인

`polybot-retro --strict`에 CRITICAL/HIGH가 있으면 parameter tuning과 증액을 중단한다.

### 최소 판단 단위

최소 4주와 terminal event-cluster 유효 n 30개를 모두 요구한다. 이 기준 전에는 `KEEP`도
수익성 승인으로 해석하지 않고 계측 유지 결정으로만 사용한다.

### STOP 조건

다음 중 하나면 전략 중단 또는 재사전등록이다.

- confirmed fill/fee와 resolution/redeem을 반영한 비용 후 event-cluster 기대값 `<= 0`
- terminal loss 1건의 실제 손실을 정상 승리 평균으로 나눈 ruin ratio가 사전 위험한도 초과
- 0.90 stop의 fill률/슬리피지 때문에 설계한 손실 제한이 재현되지 않음
- strict binary/first-observed-crossing lineage가 운영 데이터에서 안정적으로 증명되지 않음
- 성과가 특정 event/category 한 곳에만 집중되고 leave-one-event-out에서 사라짐

## 9. 월간 회고 grid

현재값은 baseline으로 고정하고 동일 evidence에서 한 번에 한 축만 반사실 비교한다.

| 축 | 후보 |
|---|---|
| crossing/entry lower | 0.94 / **0.95** / 0.96 |
| entry upper | 0.96 / **0.97** / 0.98 |
| absolute stop | 0.85 / **0.90** / 0.93 |
| min liquidity | **1k** / 5k / 10k / 20k |
| min volume24h | **0** / 500 / 2k / 5k |
| hours max | 24 / 48 / **72** |

look-ahead 없이 각 snapshot 시점에 당시 알려진 값만 사용한다. 동일 event의 여러 시장을
train/test에 나누지 않고 event 단위로 bootstrap 또는 leave-one-event-out한다. fill
sensitivity는 midpoint, observed bid/ask, confirmed fill의 세 가지로 따로 제시한다.

저장소의 `scripts/backtest.py`는 이 여섯 축의 1,296개 조합과 UTC
`REVIEW_START`/`REVIEW_END` entry cohort를 offline CSV에서 재현한다. 입력은
`outcomes=["Yes","No"]`, 서로 다른 두 token, YES token identity, `neg_risk=false`를
행마다 증명해야 한다. 스크립트가 직접 계산하는 것은 midpoint와 observed book의
**가상 체결** 결과다. confirmed-fill 결과는 동일 산출물을 exact execution ledger와
order ID로 join한 뒤에만 채우며, 증거가 없으면 `null`로 남긴다.

## 10. 월간 결정 형식

```text
REVIEW_START / REVIEW_END (UTC):
audit bundle / DB checksum:
config_hash × git_commit × mode × job:
nominal markets / effective events / terminal events:
confirmed BUY/SELL fill coverage:
fee/role coverage:
archive first-observed-crossing/catalog coverage:
resolution / redeem / unresolved counts:
gross / fee / spread-adjusted net:
stop trigger → confirmed fill rate and slippage:
competing explanation tests:
decision: KEEP | CHANGE | STOP
confidence / rollback condition:
```

`CHANGE`는 다음 cohort에 적용할 단일 knob, 예상 효과, rollback threshold를 명시한다. evidence
gate가 실패하면 수치 변경 대신 복구·계측 계획만 작성한다.

## 11. 공식 처리 계약과 가설의 지위

아래는 구현의 시장 구조·가격 side·resolution/redeem 처리를 확인하는 Polymarket primary
reference다. **Final Five의 “first observed 0.95 crossing 이후 비용 후 edge가 있다”는 핵심
가설을 입증하는 출처가 아니다.** 그 가설은 사용자 직관에서 출발한 미검증 가설이며 §8–§10의
실측 falsification을 통과해야 한다.

- [Resolution](https://docs.polymarket.com/concepts/resolution) — resolution rule, dispute,
  YES/NO payout과 드문 Unknown/50-50 payout
- [Redeem Tokens](https://docs.polymarket.com/trading/ctf/redeem) — resolution 이후 CTF token
  redemption과 payout vector
- [Positions & Tokens](https://docs.polymarket.com/concepts/positions-tokens) — YES/NO outcome
  token, position, resolution 전 매매와 resolution 후 상환
- [Get market price](https://docs.polymarket.com/api-reference/market-data/get-market-price) —
  token ID와 BUY/SELL side를 지정하는 market price API 계약
