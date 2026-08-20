# Golden Black — Sports Resolution Hold

## 검정 가설

스포츠 aligned two-outcome market에서 Gamma `endDate`까지 6시간 이내이고 market liquidity
`>=10,000`, cumulative volume `>=5,000`일 때, exact `$5` ask가 고확률 band에 들어온
outcome을 resolution까지 보유하면 비용 후 양의 기대값이 있는가?

두 arm은 같은 sweep과 같은 CLOB batch를 사용한다.

| arm | exact `$5` entry VWAP | terminal payoff |
|---|---:|---|
| B | `[0.92, 0.93]` | unique one-hot resolution |
| A | `[0.94, 0.95]` | unique one-hot resolution |

시장·clock·liquidity·volume·notional·cadence는 동일하다. threshold 외 차이를 만들지 않는다.
같은 token이 시간차를 두고 두 band를 각각 만족하면 paired counterfactual episode 둘을 허용한다.
팀명 moneyline(`negRisk=false`)과 Yes/No proposition(`negRisk=true`)은 모두 이진 payout이지만
시장 구조가 다르므로 `neg_risk`를 정규화해 별도 stratum으로 판정한다.

## 익절과 손절 정책

이 전략의 기본 익절은 조기 target 매도가 아니라 winner의 terminal `$1` payout이다. 진입
threshold의 효과와 조기 익절 효과를 섞지 않기 위해 0.98/0.99 target은 이번 primary
experiment에 넣지 않는다. 다만 매 5분 exact executable bid path를 저장하므로 후속 회고에서
조기 익절 sensitivity를 재생할 수 있다.

모든 episode에는 다음 네 exit policy를 동시에 만들며, 서로 실제 유동성을 경쟁하는 주문이
아니라 같은 book을 사용한 대안적 counterfactual이다.

| policy | trigger | 처리 |
|---|---:|---|
| `HOLD_TO_RESOLUTION` | 없음 | unique one-hot resolution까지 보유 |
| `STOP_0.80` | best bid `<=0.80` | 남은 share를 현재 bid depth에 taker 매도 |
| `STOP_0.70` | best bid `<=0.70` | 동일 |
| `STOP_0.60` | best bid `<=0.60` | 동일 |

stop 숫자는 보장 매도가가 아니라 **발동 기준**이다. 예를 들어 best bid가 0.81에서 0.79로
gap-down하면 0.80 policy가 발동하고, 보유 share 전량을 여러 bid level에 걸어 실제 VWAP와
proceeds를 계산한다. VWAP 0.78 체결을 0.80으로 기록하지 않는다. 전량 depth가 없으면 부분
fill, 잔여 share, fee와 retry를 append-only로 남기고 다음 cycle에도 남은 수량의 청산을
계속 모사한다. 최초 trigger bid, 직전 bid 대비 낙폭, stop 대비 VWAP gap을 모두 저장한다.

0.80/0.70/0.60은 현재 승자를 고른 값이 아니라 사전 고정한 sensitivity grid다. 30일과
resolution follow-up이 끝날 때까지 어느 stop도 실제 기본값으로 선택하지 않는다. 5분 polling은
intra-cycle 급락을 볼 수 없으므로 stop price 체결을 보장하지 않으며, 그 손실이 gap/slippage
지표에 그대로 드러나야 한다.

## 비용

각 episode는 source `feeSchedule.rate`를 저장한다. 누락된 fee-enabled 스포츠 시장에는 현재
공식 sports taker rate `0.05`를 보수적으로 쓴다. 기본 분석은 exact entry VWAP과 fee를 적용하고,
진입 `+1¢` sensitivity를 함께 보고한다. maker rebate나 fill probability는 이 simulation에 없다.

## Clock 한계

이 검정의 서버 filter는 Gamma `endDate`다. 스포츠 API에서 `endDate`와 `gameStartTime`이 같은
경우가 많아 이를 실제 경기 종료 6시간 전이라고 단정할 수 없다. 두 timestamp와 `PRE_GAME` /
`IN_PLAY` phase를 원자료에 보존하고, 후속 분석에서 clock stratum을 분리한다.

## 사전 판정 gate

- 24시간: cadence, terminal cursor, exact book coverage, DQ, DB quick check, storage만 판정.
- 7일: arm별 episode 수와 resolution coverage만 판정; threshold 변경 금지.
- 30일 entry 종료 후: arm별 evaluable 300, unique event 200, resolution coverage 90%, exact
  `$5` book coverage 100%가 모두 필요하다.
- event-cluster bootstrap/interval의 비용 후 ROI 하한이 0보다 커야 후보로 남긴다.
- stop policy별 full/partial/no-depth attempt coverage와 실제 exit VWAP gap을 함께 비교한다.
- 한 arm이 좋아 보여도 이 cohort에서 곧바로 live 승격하지 않는다. untouched prospective
  confirmatory cohort가 한 번 더 필요하다.

표본 부족은 기간을 조용히 늘리거나 과거 DB를 합쳐 해결하지 않는다. 새 window는 새 cohort다.
