# Golden Plum 실행금액·종목 증거 보정 v5 — 2026-09-02

## 변경 경계

- King/Queen의 축구 A/B 진입 범위 `[0.75,0.78]`, 3회 확인, 누적 `+0.02`,
  익절 `0.90/0.95`, 손절 `confirmed BUY VWAP-0.15`, 1분 cadence는 바꾸지 않는다.
- Silver 축구와 Gold MLB/NFL/NBA의 모집단·가상 경로도 바꾸지 않는다.
- 배포 전후는 `config_hash × strategy_source_digest × mode × job_name`으로 분리하며,
  기존 DB는 additive migration만 하고 clean·rewrite·backfill하지 않는다.

## 종목·리그 증거

- catalog, snapshot, trade마다 `sport_family`, `league_code`, `league_name`, 원본
  `market_tags_json`을 저장한다.
- 축구는 direct HOME/DRAW/AWAY YES·NO 여섯 호가, MLB/NFL/NBA/NHL은 검증된 최상위
  두 팀 direct moneyline 두 호가라는 기존 계약을 유지한다.
- Silver/Gold의 displayed-depth 자료는 종목·리그별 표본 수, 완전 체결 가능한 주문액,
  VWAP, 가격 충격을 별도로 집계할 수 있어야 한다. 표시 호가는 실제 체결이 아니다.

## 목표 금액과 안전한 축소

- 신호와 A/B 비교는 항상 baseline exact `$5` ask VWAP으로 유지한다.
- 현재 King/Queen의 목표 주문액은 계속 `$5`다. 운영자가 이후
  `POLYBOT_BUY_AMOUNT`를 높인 경우에만 같은 fresh book에서 목표 금액 이하로 전량
  체결 가능한 가장 큰 사다리 금액 하나를 선택한다.
- 사다리는 `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000`과
  사용자가 지정한 정확한 목표값이다. 목표 `$1,000` 중 `$214`만 가격 상한 안에서
  가능하면 `$200` FOK 한 건을 제출하고, `$5`도 불가능하면 주문하지 않는다.
- 이는 부분 체결 주문이 아니다. 선택한 주문은 FOK로 전량 체결 또는 0체결이며,
  trade에는 `target_buy_amount_usdc`, `selected_buy_amount_usdc`,
  `max_executable_buy_notional_usdc`, `buy_notional_fallback_reason`을 저장한다.
- 익절과 손절은 confirmed 실제 보유량 전량을 FOK로 제출한다. 일부만 팔고 완료로
  기록하지 않는다.

## 실패 격리와 실행시간

- 주문 하나의 제출 전 계약 오류나 event-local BUY/SELL 불확실성은 해당 event와
  capacity만 격리하고 다른 event 후보를 계속 처리한다.
- 방향을 알 수 없는 대사 오류, open BUY fill/fee 증거 공백, 일반 quarantine과
  경제손익 증거 공백은 계속 신규 진입을 전역 차단한다.
- 증액 계산은 이미 받은 fresh CLOB book을 재사용하며 API 호출을 추가하지 않는다.
- 모든 runtime은 1분보다 짧아야 한다. 50초 경고 또는 다음 trigger와 겹치는 lock skip이
  생기면 정기 trigger를 복구하지 않고 원인부터 수정한다.

이 보정은 종목별 최적 진입·익절·손절 또는 증액 단계를 선택하지 않는다. 종목별 해결
event 100개와 완전한 실제 fill/fee 증거가 쌓이기 전에는 표시 깊이만으로 주문액을 올리지
않는다.
