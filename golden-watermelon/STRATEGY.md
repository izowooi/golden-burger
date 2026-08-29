# Golden Watermelon — Major Sports In-Play Evidence v4a

## 검정 질문

Soccer, MLB, NHL 경기 중 승자 outcome의 executable ask가 `0.95..0.99`에 도달했을 때 fee,
spread, 급반전과 actual displayed bid depth를 반영한 `$5` counterfactual은 resolution hold 또는
stop 정책에서 양의 event-equal 기대값을 보이는가? 동일 snapshot의 depth는 어느 notional까지
급격한 VWAP 악화 없이 유지되는가? 1분 cadence는 5분보다 얼마나 많은 crossing/path를 포착하는가?

높은 가격은 승리를 보장하지 않는다. 0.98 무손절 매수도 fee 전 실제 승률이 98%를 넘어야
손익분기고 희귀 역전패 한 번이 여러 작은 승리를 지울 수 있다. polling 사이 jump와 closed book을
실제 체결로 보정하지 않는다.

## Frozen universe

각 family를 Gamma `/events/keyset`, page 500, 최대 4페이지, `closed=false`, `live=true`, exact
numeric tag, `related_tags=false`로 독립 수집한다.

| family | exact authority | result identity | in-play age |
|---|---|---|---:|
| Soccer | tag 100350 + EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL tuple | 3 distinct regulation HOME/DRAW/AWAY YES | 4h |
| MLB | tag 100381 + sport 8 + root series 3 + exact MLB teams | one condition, direct HOME/AWAY tokens | 8h |
| NHL | tag 899 + sport 35 + root series 10346 + exact NHL teams | one condition, direct HOME/AWAY tokens | 5h |

World Series와 Stanley Cup Final은 exact major-league season/root/team identity를 통과하면 포함한다.
title로 추정하지 않는다. e-sports, MiLB/AHL/ECHL/NCAA, child/period/spread/total/prop/future/
advancement는 `REJECTED`; frozen identity의 누락·충돌은 `DRIFT`로 기록하고 CLOB/episode를 막는다.

research universe에는 volume/liquidity 하한을 두지 않는다. 이 값과 full book 자체가 미래 live
eligibility 연구의 feature다.

## Entry, path와 depth

- Entry threshold: `0.95/0.96/0.97/0.98/0.99`.
- Primary counterfactual notional: exact `$5`.
- Notional ladder: `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`.
- `FIRST_FULL_DEPTH_ABOVE`와 진짜 `UPWARD_CROSS`를 구분한다.
- `condition × token × threshold`당 episode 하나.
- `HOLD_TO_RESOLUTION`과 stop `0.95/0.93/0.90/0.85/0.80/0.70`을 같은 path에서 replay한다.
- trigger bid, actual full-depth VWAP, gap, partial fill과 remaining retry를 분리한다.
- CLOB closed와 exactly one winning token의 one-hot `0/1`만 resolution으로 인정한다.

각 threshold/stop/notional은 같은 사건의 counterfactual이며 독립 거래로 합산하지 않는다.

Soccer는 public source의 regulation minute `>=75/>=80/>=85`만 late-entry replay에 사용한다.
kickoff wall clock으로 minute를 만들지 않는다. MLB/NHL의 period/inning/clock은 raw provenance로
보존하지만 Soccer minute strata와 합치지 않는다.

## Cadence와 timeline

| Jenkins | runtime | arm | cadence |
|---|---|---|---:|
| `polybot-white` | `watermelon-white-1m-v4a` | `FAST_1M` | 1분 |
| `polybot-grey` | `watermelon-grey-5m-v4a` | `CONTROL_5M` | 5분 |

두 DB는 config/source/universe/grid가 같고 cadence만 다르다.

- Freeze decision: `2026-08-29T00:00:00Z`.
- Entry: `[2026-08-29T04:00:00Z,2026-09-05T04:00:00Z)`.
- Follow-up end: `2026-09-12T04:00:00Z`.
- 첫 24시간 review: `2026-08-30T04:00:00Z` 이후.

## 판정 gate

24시간에는 family별 cursor, exact identity, market structure, CLOB full book, source clock,
cohort, DB integrity, runtime과 storage만 판정한다. 수익성, best family/threshold/stop/minute/notional은
고르지 않는다.

성과 판정은 follow-up과 resolution coverage 이후 family를 분리해 event-cluster 단위로 수행한다.
표본 부족은 성공도 실패도 아니다. CRITICAL/HIGH, mixed cohort, cursor/identity/path/resolution gap이
있으면 수익성·parameter 판단을 중단한다. live scale은 accountless displayed depth만으로 승인하지
않고 confirmed live fill/fee evidence와 함께 한 rung씩 검토한다.

v3d 이하 DB는 immutable archive다. v4a와 migration, `ALTER TABLE`, merge 또는 backfill하지 않는다.
