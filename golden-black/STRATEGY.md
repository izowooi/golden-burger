# Golden Black — Sports Resolution Hold

## 검정 가설

스포츠 strict-binary market에서 Gamma `endDate`까지 6시간 이내이고 market liquidity
`>=10,000`, cumulative volume `>=5,000`일 때, exact `$5` ask가 고확률 band에 들어온
outcome을 resolution까지 보유하면 비용 후 양의 기대값이 있는가?

두 arm은 같은 sweep과 같은 CLOB batch를 사용한다.

| arm | exact `$5` entry VWAP | exit |
|---|---:|---|
| B | `[0.92, 0.93]` | unique one-hot resolution |
| A | `[0.94, 0.95]` | unique one-hot resolution |

시장·clock·liquidity·volume·notional·cadence는 동일하다. threshold 외 차이를 만들지 않는다.
같은 token이 시간차를 두고 두 band를 각각 만족하면 paired counterfactual episode 둘을 허용한다.

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
- 한 arm이 좋아 보여도 이 cohort에서 곧바로 live 승격하지 않는다. untouched prospective
  confirmatory cohort가 한 번 더 필요하다.

표본 부족은 기간을 조용히 늘리거나 과거 DB를 합쳐 해결하지 않는다. 새 window는 새 cohort다.
