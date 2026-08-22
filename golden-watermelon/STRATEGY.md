# Golden Watermelon — In-Play Match Winner

## Hypothesis

경기 시작 후 whole-match winner의 executable ask가 0.95 이상으로 올라간 시점에는,
fee·spread·급반전 시 실제 bid depth를 반영해도 resolution까지 보유하거나 빠른 stop을
적용한 $5 counterfactual의 event-equal 기대수익이 양수인가?

가격 0.98은 “반드시 98% 승리”라는 보장이 아니다. 0.98에 매수한 무손절 정책은 fee 전에도
실제 승률이 98%를 넘어야 손익분기이며, spread·taker fee·gap-down이 있으면 필요한 승률은 더
높다. 이 실험은 그 calibration과 tail loss를 함께 측정한다.

## Mechanism

경기 후반에 정보가 점수와 남은 시간에 반영되면서 winner probability는 terminal 1로
수렴할 수 있다. 반면 시장이 이미 그 정보를 정확히 가격에 반영했다면 기대수익은 0 이하이고,
지연된 관측·spread·fee가 수익을 없앤다. 역전 때는 bid가 stop을 건너뛸 수 있으므로
“빠른 poll”은 trigger 가격 자체가 아니라 관측 지연과 executable VWAP gap을 줄일 때만
가치가 있다.

가장 강한 competing explanation은 다음 세 가지다.

1. 고확률 가격이 이미 완전 보정되어 edge가 없다.
2. high-price favorite의 rare loss가 작은 winner 수익을 모두 지운다.
3. 1분과 5분의 차이는 stop 개선이 아니라 서로 다른 entry timing/표본에서 생긴다.

같은 condition/token/threshold를 paired하고 event-cluster 단위로 집계해 이를 구분한다.

## Universe

- Source: Gamma `/events/keyset`, keyset cursor complete.
- Server envelope: `closed=false`, `live=true`, `tag_slug=sports`, page 500,
  max 4. nested market에서 `sportsMarketType=moneyline`을 client-side로 재검증한다.
- volume/liquidity 하한: **none**. 두 값은 feature로 저장하고 selection gate로 쓰지 않는다.
- Execution availability: active, not closed, accepting orders, order book enabled.
- Clock: `gameStartTime <= receipt time`; `event.ended=true` 또는 명시적
  `event.live=false`면 제외.
- 정확히 두 team과 두 outcome token을 요구한다.

### Whole-match winner 분류

- `sportsMarketType=moneyline`만 허용한다.
- `child_moneyline`은 이름에 winner가 있어도 map/game/set 부분경기이므로 제외한다.
- non-negRisk는 두 outcome label이 event의 두 team name/alias에 각각 정확히 대응해야 한다.
- negRisk는 `groupItemTitle`이 event team 하나에 정확히 대응하는 team-win market의
  `YES`만 허용한다.
- Draw/Tie, negRisk `NO`, score, spread, totals, goal, foul, corners, player prop은 제외한다.

축구에는 무승부가 있으므로 “모든 스포츠는 반드시 승/패”라는 전제를 데이터에 강제로
넣지 않는다. 무승부 outcome은 이 가설의 team winner가 아니므로 제외하고 exclusion count를
남긴다.

Gamma `endDate`는 실제 종료시각으로 가정하지 않는다. 어떤 종목은 `endDate`와
`gameStartTime`이 같기 때문이다. endDate는 bounded discovery에만 쓰고, phase와 resolution은
`gameStartTime`, event live/ended/status, CLOB one-hot winner로 판단한다.

## Entry

- Counterfactual notional: exact `$5`.
- Price: full displayed ask depth를 walk한 VWAP.
- Threshold grid: `0.95, 0.96, 0.97, 0.98, 0.99`.
- 첫 full-depth 관측이 X 이상이면 `FIRST_FULL_DEPTH_ABOVE`.
- 직전 full-depth VWAP가 X 미만이고 현재 X 이상이면 `UPWARD_CROSS`.
- 한 `condition × token × X`에는 episode 하나만 만든다.
- 한 번에 여러 X를 jump하면 crossed threshold를 모두 같은 시각·실제 VWAP로 기록한다.

각 X episode는 독립 시장이 아니라 같은 path의 counterfactual이다. threshold별 합계를
서로 독립 표본처럼 검정하지 않는다.

## Exit와 실행 모델

기본 정책은 `HOLD_TO_RESOLUTION`이다. 조기 take-profit은 넣지 않아 entry/stop 가설과
섞이지 않게 한다. 각 episode에 다음 stop을 동시에 만든다.

| policy | trigger |
|---|---:|
| `STOP_0.95` | best bid ≤ 0.95 |
| `STOP_0.93` | best bid ≤ 0.93 |
| `STOP_0.90` | best bid ≤ 0.90 |
| `STOP_0.85` | best bid ≤ 0.85 |
| `STOP_0.80` | best bid ≤ 0.80 |
| `STOP_0.70` | best bid ≤ 0.70 |

진입에 사용한 동일 book은 path나 stop 판정에 재사용하지 않는다. ask 진입가와 같은 시점의
bid 차이는 진입 이후 가격 하락이 아니라 spread이기 때문이다. 최초 path/stop 관측은 다음
natural cadence cycle부터 시작한다. 이후 0.95처럼 민감한 stop이 실제 후속 관측에서 얼마나
자주 작동하는지는 그대로 측정한다.
trigger 가격은 fill 가격이 아니다. 현재 bid depth를 original shares만큼 walk해 full/partial,
VWAP, gap, fee, remaining shares와 다음 cycle retry를 저장한다. resolution은 CLOB market이
closed이며 두 token 중 winner가 정확히 하나일 때만 인정한다.

## Cadence treatment

| Jenkins | runtime job | arm | timer |
|---|---|---|---|
| `polybot-white` | `watermelon-white-1m-v2` | `FAST_1M` | 매분 |
| `polybot-grey` | `watermelon-grey-5m-v2` | `CONTROL_5M` | 5분 |

두 job은 cadence 외 config/source/universe/grid가 같다. 동일 episode key를 paired하고,
entry time·VWAP 차이, stop first-trigger delay, executable exit gap과 ROI 차이를 비교한다.
두 DB를 합쳐 표본을 두 배로 만들지 않는다.

## Frozen timeline

- Entry: `[2026-08-22T16:15:00Z, 2026-09-05T16:15:00Z)`.
- Calibration: start부터 `2026-08-29T16:15:00Z` 미만.
- Confirmation: `[2026-08-29T16:15:00Z, entry end)`.
- Resolution follow-up: `2026-09-19T16:15:00Z`까지.
- 실제 수집 시작은 각 Jenkins의 첫 successful build와 DB source receipt time으로 보고한다.

## Falsification과 판정 gate

### 첫 health review

수익성이나 parameter를 판단하지 않는다.

- 두 DB `quick_check=ok`, data contract/config/source digest 일치.
- cursor-complete sweep 100%.
- successful cadence coverage: FAST ≥95%, CONTROL ≥90%.
- eligible outcome의 CLOB attempt coverage ≥95%.
- incomplete cursor, credential, DB lock, storage CRITICAL/HIGH issue 0.
- 외장 volume free space ≥50GiB, used ratio <90%.
- FAST natural build p95가 45초 미만. 초과하면 timer를 끄고 범위를 조용히 자르지 않은 채
  runtime 원인을 수정한다.

### Outcome gate

entry와 follow-up이 끝난 뒤에만 X/Y 후보를 고른다.

- 각 X의 confirmation 구간 resolved unique event ≥100.
- resolution coverage ≥90%, exact $5 entry quote 100%.
- event-cluster bootstrap 95% lower bound가 fee 후 ROI 0보다 커야 한다.
- calibration에서만 좋고 confirmation에서 0 이하인 조합은 폐기한다.
- cadence pair coverage ≥80%. FAST가 CONTROL보다 stop gap/tail loss를 줄이지 못하면
  1분 cadence의 추가 비용을 정당화하지 못한 것으로 본다.
- 어떤 X/Y도 gate를 통과하지 못하면 가설을 기각한다. 같은 cohort에서 grid를 보고 새 값을
  추가해 성공으로 바꾸지 않는다.

표본 부족은 성공도 실패도 아니다. 사전 기준을 조용히 낮추지 않고 새 preregistered cohort가
필요하다고 기록한다.
