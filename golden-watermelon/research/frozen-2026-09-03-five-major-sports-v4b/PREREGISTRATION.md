# Frozen preregistration — 축구·MLB·NBA·NFL·NHL 수집 v4b

- 결정 시각: `2026-09-03T12:00:00Z`.
- 수집 구간: `[2026-09-03T12:00:00Z, 2026-10-03T12:00:00Z)`.
- 해결 후속 관찰 종료: `2026-10-10T12:00:00Z`.
- 첫 수집 상태 점검: 첫 정상 배포 후 24시간.
- Data contract: `watermelon-five-major-sports-inplay-match-winner-v6`.
- Schema profile: `golden-watermelon-v4b-schema-v1`.
- Universe: `watermelon-soccer-mlb-nba-nfl-nhl-2026-09-v4b`.
- Classifier: `watermelon-major-sports-identity-v2`.
- Mode: 계정 없는 표시 호가 반사실(`displayed-book counterfactual`) 시뮬레이션 전용.

v4a 이하 DB는 변경하지 않는 보관 자료다. v4b는
`watermelon-white-1m-v4b`와 `watermelon-grey-5m-v4b`라는 새 runtime DB를 사용한다.
이전 DB를 clean, alter, migrate, copy, merge, backfill 또는 delete하지 않는다.

## 목적

1. live 주문액을 `$5`에서 `$10..$1000`으로 단계적으로 키울 때 종목별 full displayed
   ask/bid depth, VWAP, slippage와 즉시 왕복 손실을 측정한다.
2. Soccer·MLB·NHL에 NBA·NFL을 추가해 동일한 whole-game 승자 계약으로 확장 가능한지
   검증한다.

White와 Grey는 동일한 모집단과 탐색 격자를 수집하고 실행 주기만 1분/5분으로 다르다.
현재 실제 Jenkins 배포 대상은 White이며, Grey v4b 이름은 향후 동일 주기 대조를 위한
예약 계약이다. Golden Peach용 `polybot-grey`의 현재 역할을 임의로 바꾸지 않는다.

## 동결 모집단

각 family를 숫자 Gamma tag로 독립적으로 끝까지 cursor 수집한다.

| family | tag | exact identity | 최대 in-play age |
|---|---:|---|---:|
| Soccer | 100350 | EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL | 4h |
| MLB | 100381 | sport 8, root series 3, MLB 두 팀 | 8h |
| NBA | 745 | sport 34, root series 10345, NBA 두 팀 | 5h |
| NFL | 450 | sport 10, root series 10187, NFL 두 팀 | 6h |
| NHL | 899 | sport 35, root series 10346, NHL 두 팀 | 5h |

Soccer는 정규시간 HOME/DRAW/AWAY YES 세 결과를 수집한다. 나머지 네 종목은 한
condition의 direct HOME/AWAY 두 outcome을 수집한다. World Series, NBA Finals,
Super Bowl, Stanley Cup Final은 같은 1부 리그 root/season/team identity를 통과할 때 포함한다.
MiLB/G League/대학 football/AHL/ECHL/NCAA, e-sports, period/quarter/inning, spread,
total, prop, future, advancement는 제외한다.

모집단에는 volume/liquidity 하한을 두지 않는다. 이 값과 전체 호가 깊이 자체가 미래
live eligibility를 정할 연구 feature이기 때문이다. 한 family cursor가 끝나지 않으면 그
cycle은 불완전으로 기록하고 성과 판단에 사용하지 않는다.

## 탐색 격자와 판정

- Entry threshold: `0.95/0.96/0.97/0.98/0.99`.
- Primary displayed notional: `$5`.
- Notional ladder: `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$250/$500/$750/$1000`.
- Exit replay: hold와 `0.95/0.93/0.90/0.85/0.80/0.70` stop.
- Soccer만 source-explicit `75/80/85` minute strata를 사용한다. 다른 종목 clock은 원문
  증거로 저장하되 Soccer 시간층과 합치지 않는다.

표시 호가는 실제 체결이나 실현 손익이 아니다. 같은 경기의 threshold, stop, notional
rung을 독립 거래처럼 합산하지 않는다. 첫 24시간에는 family cursor, exact identity,
whole-game market 구조, CLOB depth, source clock, cohort, DB 무결성, 실행시간과 저장공간만
판정한다. 수익성·최적 종목·진입/손절·주기·주문액은 표본 및 해결 coverage가 충분하기
전에 선택하지 않는다.

요청 구간의 strict 검증에서 `CRITICAL` 또는 `HIGH` 증거 문제가 하나라도 남으면
수익성·파라미터·주문액 판단을 중단하고 수집 계약부터 복구한다.
