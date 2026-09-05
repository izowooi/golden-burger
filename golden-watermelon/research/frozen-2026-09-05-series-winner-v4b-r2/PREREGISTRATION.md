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

## 2026-09-05 실행·분석 정합성 수정판 r1

기존 v4b 동결 문서와 MANIFEST는 원문 그대로 보존한다. 이 수정판은 진입·손절 수치,
종목, 모집단, 수집 종료일, DB schema/runtime identity를 변경하지 않는다.
기존 DB의 raw/episode/decision/resolution 행을 수정·삭제·이관·재생성하지 않는다.
source digest와 preregistration digest가 바뀌는 새 코드 묶음으로 기록한다.

- 종료 판정은 API 배열 순서 대신 진입 원문과 동일한 condition·2token 집합으로 검증한다.
  배열 순서가 뒤집혀도 정확한 token payout을 사용하며 원래 index와 정규화 출처를 보존한다.
  누락·중복·잘못된 토큰 집합, zero/multiple winner는 종료 확정을 거부한다.
- 분석기는 원본을 읽기 전용으로 열고 token별 판정을 임시 테이블에 파생한다.
  과거 index 오류가 있으면 원래 resolution ID·raw hash·기존 index·교정 payout을
  보고서에 명시한다. 증거 부족은 분석 제외 사유로 남긴다.
- 결과 호가 완전성의 분모는 league ACCEPTED 전체가 아니라 실제 eligible whole-game
  event×run이다. 이닝·prop child는 분모에 포함하지 않는다.
- Gamma live 플래그가 먼저 켜진 경기 전 구간을 in-play 결과 누락으로 오판하지 않는다.
- 0.96을 부동소수점 계산 뒤 0.9599999999999999로 표현하는 오차만 1e-12로 처리한다.
  진입 기준 0.95/0.96/0.97/0.98/0.99는 변경하지 않는다.

배포 전 검증된 White MLB 16경기에서 종료 증거가 있는 11경기·50 episode의 token/index
불일치는 0건이었다. token 순서 결함은 격리 테스트에서 재현됐다. 반면 0.96 경계 오판은
이번 MLB 9경기의 41개 decision에서 확인됐다. 과거 episode는 소급해서 생성하지 않는다.

## 2026-09-05 World Series 전체 우승 제외 수정판 r2

이 수정판은 r1과 기존 동결 파일을 수정하지 않고 보존한다. 같은 v4b DB에 새 source/config
묶음으로 이어서 수집하며 원래 모집단·가격 grid·기간·schema·runtime 이름은 바꾸지 않는다.
기존 raw/decision/episode/resolution 행에 reset, migration, backfill, UPDATE/DELETE를 하지 않는다.

- 숫자 MLB root/season/team identity가 맞아도 market question/groupItemTitle/slug가
  `series winner`, `World Series winner/champion`, `win (the) (2026) World Series`처럼
  시리즈 전체 우승을 명시하면 `SERIES_WINNER_NOT_INDIVIDUAL_GAME`으로 제외한다.
- `isFuture`가 없거나 false인 응답에도 이 거절을 적용한다. 제목은 제외 사유로만 쓰며
  허용된 종목·리그의 권위를 새로 부여하지 않는다.
- `World Series Game 1`~`Game 7` 및 `Game1`처럼 개별 경기를 명시한 정상 whole-game
  moneyline은 유지한다. 기존 exact MLB root/team 검증과 child/prop 제외는 그대로 적용한다.
- 이 수정은 파라미터 최적화나 실험 연장이 아닌 모집단의 개별 경기 계약 보강이다.
