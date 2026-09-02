# Golden Peach 다종목·거래규모 증거 계약 v3

동결일은 2026-09-02이다. 이 변경은 현재 Eco/Fruit 축구 A/B의 진입·익절·손절 수치를
바꾸지 않는다. 목적은 이후 종목 확대와 주문액 증액을 추측이 아니라 같은 시각의 직접 호가
증거로 판단할 수 있게 만드는 것이다.

## 현재 라이브 계약

- `peach-live-eco-3pp-1m-v1`: 축구, 목표 `$5`, TP `+0.03`.
- `peach-live-fruit-5pp-1m-v1`: 축구, 목표 `$5`, TP `+0.05`.
- 공통: source clock 0~10분, 진입 `[0.60,0.94]`, SL `-0.10`, 1분 cadence.
- 축구 event의 HOME/DRAW/AWAY 직접 YES/NO 여섯 호가가 모두 있어야 한다.
- 한 event에는 체결되었거나 결과가 불명확한 BUY를 한 번만 허용한다.

## 종목 확대 경계

- 지원 수집 family/runtime: `soccer`=`peach-shadow-1m-v1`,
  `mlb`=`peach-shadow-mlb-1m-v2`, `nba`=`peach-shadow-nba-1m-v2`,
  `nfl`=`peach-shadow-nfl-1m-v2`, `nhl`=`peach-shadow-nhl-1m-v2`.
- 축구는 3개 결과 명제의 직접 YES/NO 6개 token을 비교한다.
- MLB/NBA/NFL/NHL은 하나의 whole-game moneyline에 표시된 두 팀 token을 비교한다.
  `1-price`로 반대편을 합성하지 않는다.
- 직접 팀 종목은 예정 시작 시각 대비 경과시간만 남기며, sport-native clock 계약이 없다.
  따라서 이 버전에서는 `peach-shadow-1m-v1`만 허용하고 live 실행은 시작 전에 거부한다.
- minor/college/youth/prop/period/handicap/advancement market은 제외한다.
- direct sport별 익절·손절·진입 구간은 축구 수치를 승계했다고 가정하지 않고 별도 cohort에서
  검증한 후 동결한다.

## 거래규모 증거

simulation snapshot은 같은 원본 book에 대해 `$5, $10, $15, $20, $25, $30, $40,
$50, $75, $100, $150, $200, $250, $500, $750, $1000`의 매수 전량 체결 가능 여부,
ask VWAP, 필요 share, 마지막 ask 가격과 단계 수를 계산한다. 같은 share를 즉시 bid에
매도할 수 있는지와 수수료 전 왕복 손익도 별도로 저장한다. 이는 표시 호가 반사실이며 실제
체결 보장이 아니다.

live 목표 주문액은 `$5` 이상 `$1000` 이하 cent 단위만 허용한다. 신호와 선두 비교는 항상
baseline `$5` book으로 수행한다. 주문 직전 같은 fresh book에서 목표액부터 동결 ladder를
내려가며 진입 상한 안에서 전량 체결 가능한 가장 큰 금액을 선택해 FOK BUY 한 건만 제출한다.
`$5`도 전량 체결할 수 없으면 제출하지 않는다. 목표액·선택액·진입 상한 안의 표시 가능액과
축소 사유를 trade에 저장한다.

## DB 식별자

snapshot과 trade에는 다음을 저장한다.

- `sport_family`, `league_code`, `league_name`, 원본 `market_tags_json`
- snapshot: `sport_profile_version`, `book_shape`, `execution_capacity_json`
- trade: `target_buy_amount_usdc`, `selected_buy_amount_usdc`,
  `max_executable_buy_notional_usdc`, `buy_notional_fallback_reason`

종목·주문 목표가 다른 구간은 같은 결과로 합치지 않는다. 반드시
`config_hash × strategy_source_digest × mode × job_name`과 sport/profile을 함께 나눈다.

## 수집 건전성 판정

수익성 전에 다음을 확인한다.

1. 1분 실행이 겹치지 않고 각 cycle이 60초 미만인가.
2. event별 예상 market/token 수가 완전한가.
3. snapshot의 종목·리그·원본 태그와 sizing JSON이 비어 있지 않은가.
4. 목표 `$5` live 주문은 선택액도 `$5`이며 이전 A/B 계약이 유지되는가.
5. larger target 시험에서는 선택액이 목표 또는 동결 ladder 중 하나이고 `$5` 미만 주문이
   없는가.
6. 불명확 주문은 event-local로 격리되고 다른 event 처리와 기존 position 관리가 계속되는가.

## 변경 무효화 조건

- Eco/Fruit의 진입·TP·SL·리그 또는 현재 `$5` 목표가 의도치 않게 달라진다.
- direct sport가 live에서 실행된다.
- sizing 계산 때문에 추가 CLOB 요청을 발생시키거나 1분 cadence를 반복해서 넘는다.
- 표시 호가 계산을 실제 fill 또는 수익으로 보고한다.
- 작은 주문을 여러 건 제출해 event당 한 번이라는 계약을 우회한다.
