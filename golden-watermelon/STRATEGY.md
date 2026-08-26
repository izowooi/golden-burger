# Golden Watermelon — Elite Soccer In-Play Match Winner

## 검정 질문

축구 경기 중 HOME/DRAW/AWAY 결과의 executable ask가 0.95 이상으로 올라갔을 때, fee·spread·
급반전과 실제 bid depth를 반영한 `$5` counterfactual은 resolution 보유 또는 stop 정책에서
양의 event-equal 기대값을 보이는가? 진입을 regulation minute 75/80/85 이후로 늦추면 tail
loss가 줄어드는가? displayed depth는 어느 주문 규모까지 급격한 VWAP 악화 없이 유지되는가?

가격 0.98은 승리를 보장하지 않는다. 0.98 무손절 매수도 fee 전 실제 승률이 98%를 넘어야
손익분기이고, 희귀 역전패 한 번이 여러 작은 승리를 지울 수 있다. 1분 polling도 연속 감시나
0.97 체결 보장이 아니며, 두 cycle 사이 가격이 0.96에서 1.00으로 jump하거나 book이 닫힐 수
있다.

## Frozen universe v3c

Gamma `/events/keyset`을 page 500, 최대 4페이지로 cursor-complete하게 읽는다. server envelope은
`closed=false`, `live=true`, numeric `tag_id=100350`, `related_tags=false`다. liquidity/volume은
feature로 저장하지만 selection gate로 쓰지 않는다.

국내 리그 identity는 다음 exact tuple이다.

| code | sport id/name/primaryTagId | series id/slug | team league |
|---|---|---|---|
| `epl` | `2 / Premier League / 306` | `10188 / premier-league-2025` | `epl` |
| `bun` | `7 / Bundesliga / 1494` | `10194 / bundesliga-2025` | `bun` |
| `fl1` | `11 / Ligue 1 / 102070` | `10195 / ligue-1-2025` | `fl1` |
| `lal` | `3 / LaLiga / 780` | `10193 / la-liga-2025` | `lal` |
| `mls` | `33 / MLS / 100100` | `10189 / mls-2025` | `mls` |
| `sea` | `12 / Serie A / 100618` | `10203 / serie-a-2025` | `sea` |

UEFA cross-league competition은 team domestic league equality를 적용하지 않고 다음 exact
authority를 모두 요구한다.

| code | competition tag | series id/slug | event prefix | resolution host |
|---|---:|---|---|---|
| `ucl` | 100977 | `10204/ucl-2025` | `ucl-` | `www.uefa.com` |
| `uel` | 101787 | `10209/uel-2025` | `uel-` | `www.uefa.com` |

공통 numeric tags `1/100639/100350`, exactly two teams, exact single series relation이 필요하다.
e-sports tag 64, 비축구, 허용되지 않은 cup/league는 `REJECTED`; 허용 identity의 누락·충돌은
`DRIFT`이며 CLOB 조회와 episode 생성을 막고 HIGH issue를 남긴다.

market은 top-level `moneyline`, exact `[Yes,No]`, `negRisk=true`, open/orderbook/accepting이며
event team 또는 명시적 Draw에 해당하는 HOME/DRAW/AWAY YES만 허용한다. `child_moneyline`, Draw
No Bet, prop, advancement, extra time, penalty market은 제외한다. description은 첫 90분과
stoppage time만 settlement에 포함한다고 명시해야 한다.

## Entry와 path

- Entry threshold: `0.95/0.96/0.97/0.98/0.99`.
- Primary counterfactual notional: exact `$5`.
- 첫 full-depth ask VWAP가 X 이상이면 `FIRST_FULL_DEPTH_ABOVE`.
- 직전 VWAP가 X 미만이고 현재 X 이상이면 `UPWARD_CROSS`.
- `condition × token × threshold`당 episode 하나.
- 기본 `HOLD_TO_RESOLUTION`; stop은 `0.95/0.93/0.90/0.85/0.80/0.70`을 같은 path에서 replay.
- entry와 같은 book의 bid는 stop으로 쓰지 않고 다음 natural cycle부터 관측.
- trigger와 executable bid VWAP, gap, partial fill, remaining retry를 분리.
- CLOB closed와 exactly one winning token의 one-hot `0/1`만 resolution으로 인정.

각 threshold/stop은 같은 사건의 counterfactual이며 독립 거래로 합산하지 않는다.

## 경기 시계 evidence

public Sports WebSocket `wss://sports-api.polymarket.com/ws`는 subscription 없이 active sports
updates를 제공한다. 매 bounded cycle에서 target event slug와 일치하는 raw message를
`SPORTS_CLOCK_UPDATE`로 저장하고 `period + elapsed`를 정규화한다. 연결/coverage 실패는
kickoff 추정으로 메우지 않는다.

late-entry replay floors는 source regulation minute `>=75`, `>=80`, `>=85`다. 하프타임,
stoppage time과 source lag 때문에 이를 “실제 종료 15/10/5분 전”이라고 단정하지 않는다.
각 floor의 threshold observation을 후속 displayed path와 one-hot resolution에 join해
event-cluster 단위로 평가한다.

## 주문 규모 evidence

모든 eligible token의 full ask/bid levels를 저장하고 다음 ladder를 같은 snapshot에서 walk한다.

`$5, $10, $15, $20, $25, $30, $40, $50, $75, $100, $150, $250, $500`

각 rung의 full ask coverage, VWAP, worst ask, `$5` 대비 slippage, 같은 시점 full bid coverage와
instant haircut을 보고한다. displayed depth는 fill 보장도, 미래 stop depth의 대체물도 아니다.
향후 live scale은 한 rung씩만 올리며 최소 7일과 current-rung confirmed entry 30건 중 늦은
시점, fill/fee 100%, PENDING/QUARANTINED 0, CRITICAL/HIGH 0과 다음-rung path depth evidence를
별도로 통과해야 한다.

## Cadence와 timeline

| Jenkins | runtime | arm | cadence |
|---|---|---|---:|
| `polybot-white` | `watermelon-white-1m-v3c` | `FAST_1M` | 1분 |
| `polybot-grey` | `watermelon-grey-5m-v3c` | `CONTROL_5M` | 5분 |

두 DB는 config/source/universe/grid가 같고 cadence만 다르다. paired entry time/VWAP, stop delay,
coverage를 비교한다.

- Freeze decision: `2026-08-26T14:41:00Z`.
- Entry: `[2026-08-26T18:30:00Z, 2026-09-02T18:30:00Z)`.
- Follow-up end: `2026-09-09T18:30:00Z`.
- 첫 24시간 review: `2026-08-27T18:30:00Z` 이후.

## 판정 gate

24시간에는 cadence, cursor, exact identity, CLOB book, Sports clock join, cohort, DB integrity와
storage만 판정한다. 수익성, threshold/stop, late minute, 주문 규모는 고르지 않는다.

성과 판정은 follow-up과 resolution coverage 이후 event 내 equal → competition 내 event-equal →
8개 competition 동일 가중 macro로 한다. 하나라도 evaluable resolution이 없으면 macro와 CI는
`null`이다. confirmation에서 unique event 100, competition별 20, resolution ≥90%, exact book
100%, event-cluster bootstrap lower bound >0을 요구한다. 표본 부족은 성공도 실패도 아니며 같은
cohort에서 gate나 grid를 사후 변경하지 않는다.

이전 `soccer-inplay-major-league-match-winner-v2`/v1과 v3b DB는 immutable archive다. v3c에
migration, `ALTER TABLE`, merge 또는 backfill하지 않는다.
