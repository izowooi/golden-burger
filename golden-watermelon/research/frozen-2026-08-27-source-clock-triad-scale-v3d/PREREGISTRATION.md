# Frozen preregistration — Source Clock, Result Triad, and Scale v3d

- Frozen decision timestamp: `2026-08-27T14:55:00Z`.
- Entry window: `[2026-08-27T17:00:00Z, 2026-09-03T17:00:00Z)`.
- Resolution follow-up end: `2026-09-10T17:00:00Z`.
- First collection-health review: after `2026-08-28T17:00:00Z`.
- Data contract: `soccer-inplay-elite-competition-match-winner-v4`.
- Schema profile: `golden-watermelon-v3a-schema-v1` (tables unchanged).
- Universe profile: `soccer-elite-leagues-uefa-2026-08-v3d`.
- Classifier: `soccer-elite-competition-identity-v3`.
- Mode: accountless displayed-book counterfactual simulation only.

v3c와 이전 DB는 immutable evidence다. v3d는 같은 external workspace에
`watermelon-white-1m-v3d`와 `watermelon-grey-5m-v3d`라는 새 runtime DB를 만든다. 이전 DB를
clean, alter, migrate, copy, merge, backfill 또는 delete하지 않는다.

## Amendment rationale

v3c의 bounded Sports WebSocket 관측은 event-driven stream 특성상 대상 경기 update가 10초
창에 도착하지 않는 cycle이 많았다. 이 상태에서는 75/80/85분 strata의 denominator를 알 수
없다. 또한 라이브 후보가 HOME/DRAW/AWAY 중 일부만 수집되는 경우를 fail-closed로 탐지하고,
displayed depth grid를 사용자의 장기 목표인 `$1000`까지 같은 frozen collector에서 보존해야
한다. 이 amendment는 수집 계약만 고치며 threshold, stop, cadence 또는 live notional을
선택하지 않는다.

## Frozen universe and market identity

국내 리그 `epl/bun/fl1/lal/mls/sea`와 UEFA `ucl/uel`의 numeric tag, series, slug, team 및
resolution-host identity는 v3c와 동일하다. 공통 event tag `1/100639/100350`, exact competition
identity, parent 없는 live event, 정규 90분과 stoppage time만 결제하는 top-level
`sportsMarketType=moneyline`, explicit NegRisk YES market만 허용한다. e-sports, Conference
League, child game, draw-no-bet, qualification/advancement, extra-time 또는 penalty 포함 시장은
제외한다.

각 accepted event/run은 entry-eligible outcome identity가 정확히 세 개여야 한다.

- HOME YES 1개
- DRAW YES 1개
- AWAY YES 1개
- condition ID 3개와 token ID 3개가 모두 distinct

하나라도 누락·중복이면 `RESULT_TRIAD_COVERAGE_GAP` HIGH를 기록한다. 이 검사는 시장 가격이
진입 threshold를 넘었는지와 무관한 collection-health gate다.

## Explicit source-clock hierarchy

경기 분은 source가 명시한 값만 사용한다. 우선순위는 다음과 같다.

1. 같은 cycle의 Polymarket public Sports WebSocket update와 Gamma event를 exact game/slug
   identity로 join하고, source `elapsed` 또는 `clock`을 사용한다.
2. bounded WebSocket 창에 update가 없으면 같은 Gamma keyset response에 명시된
   `elapsed/clock`, `period`, `score`, `live`, `ended`, `gameStatus`, update timestamp를 사용한다.

킥오프 wall time으로 elapsed minute를 추정하지 않는다. WebSocket 누락은
`SPORTS_WEBSOCKET_COVERAGE_GAP` MEDIUM으로 남기되 Gamma explicit clock이 있으면 source-clock
coverage는 충족한다. 두 source 모두 명시적 clock evidence가 없으면
`SOURCE_CLOCK_COVERAGE_GAP` HIGH, clock row는 있지만 elapsed/clock minute field가 없으면
`SOURCE_CLOCK_MINUTE_FIELD_GAP` HIGH다. source와 raw provenance를 normalized evidence에 함께
보존한다.

사후 replay strata는 source regulation elapsed minute `>=75`, `>=80`, `>=85`다. 이는 실제
경기 종료까지 남은 wall-clock time을 뜻하지 않는다.

## Notional capacity evidence

실제 주문은 없고 primary episode notional은 exact `$5`다. 모든 eligible token의 displayed
full book을 저장해 다음 frozen ladder를 같은 snapshot에서 재생한다.

`$5, $10, $15, $20, $25, $30, $40, $50, $75, $100, $150, $250, $500, $750, $1000`

각 rung에서 full ask depth, ask VWAP/worst ask, `$5` 대비 slippage, 즉시 full bid depth와
round-trip haircut을 계산한다. displayed depth는 주문 가능성의 관측 근거일 뿐 guaranteed
fill, future exit depth 또는 realized P&L이 아니다. 독립 event 수가 충분하지 않으면 scale을
추천하지 않는다.

## Frozen treatment and gates

| runtime | arm | cadence |
|---|---|---:|
| `watermelon-white-1m-v3d` | FAST_1M | 1 minute |
| `watermelon-grey-5m-v3d` | CONTROL_5M | 5 minutes |

cadence 외 source/config/grid는 같다. entry threshold는 `0.95/0.96/0.97/0.98/0.99`, stop replay는
hold와 `0.95/0.93/0.90/0.85/0.80/0.70`, minute floor는 `75/80/85`다.

Machine-readable review labels는 entry `0.95, 0.96, 0.97, 0.98, 0.99`와
`STOP_0.95/STOP_0.93/STOP_0.90/STOP_0.85/STOP_0.80/STOP_0.70`이다.

첫 24시간에는 cursor/identity/result-triad/book/source-clock/DB/cohort/storage health만
판정한다. 수익성, threshold, stop, minute floor, cadence 승자 또는 live notional을 선택하지
않는다. 첫 7일과 follow-up이 끝나기 전에는 live 승격 또는 scale-up을 결론내리지 않는다.
