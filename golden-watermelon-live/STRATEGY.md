# Golden Watermelon Live — Major Sports A/B v3f

## 질문과 treatment

경기 중 승자 token이 높은 executable 가격에 도달했을 때 baseline `$5` 신호로 FOK 진입하고
`0.70` 재난 손절 또는 proven resolution까지 관리하면
confirmed fill·fee 후 양의 기대값이 있는가?

각 family에서 A는 `[0.96,0.999]`, B는 `[0.99,0.999]`다. Soccer는 Cat/Dog, MLB는
Bear/Tiger, NHL은 Lion/Wolf가 맡는다. `0.999`는 terminal `1.000`을 제외하는 공통 상한이다.
family와 하한 외 notional, cadence, execution, capacity와 safety는 동일하다.

## Universe

Gamma `/events/keyset`을 `closed=false`, `live=true`, `related_tags=false`, family numeric tag,
`liquidity_min=5000`, `volume_min=5000`으로 최대 4페이지 cursor-complete하게 읽는다.

| family | identity | whole-game result | max in-play age |
|---|---|---|---:|
| Soccer | EPL/Bundesliga/Ligue 1/LaLiga/MLS/Serie A/UCL/UEL | regulation HOME/DRAW/AWAY YES | 4h |
| MLB | tag 100381, sport 8, root series 3, exact MLB teams | direct two-team moneyline | 8h |
| NHL | tag 899, sport 35, root series 10346, exact NHL teams | direct two-team moneyline | 5h |

UCL/UEL는 exact competition tag/series/prefix/UEFA resolution host를 요구한다. World Series와
Stanley Cup Final은 exact major-league root/season/team metadata를 통과할 때 포함한다. 이름에
대회명이 있다는 이유만으로 승인하지 않는다. e-sports, MiLB/AHL/ECHL/NCAA, child/period,
spread/total/prop/future/advancement와 settlement scope가 불명확한 soccer market은 fail closed한다.

## Entry와 capacity

1. eligible winner token의 full ask book을 읽어 baseline exact `$5` shares, VWAP와 worst ask를 계산한다.
2. 한 event에서 여러 result가 동시에 threshold를 넘으면 anomaly로 fail closed한다.
3. 한 `event × arm`의 첫 threshold observation만 `entry_episodes`에 claim한다.
4. open Trade와 unresolved/untracked BUY intent를 capacity에 함께 넣는다.
5. 주문 직전 market/clock/full-depth book/fee identity를 다시 확인한다.
6. venue tick grid에서 현재 목표 이하의 signed maker amount와 taker shares가 venue precision에
   맞는 가장 낮은 limit을 찾는다. 목표 전량이 어렵다면 같은 fresh book에서 `$5` 이상인 가장 큰
   사다리 금액으로 축소한다. arm 상한을 넘거나 `$5`도 불가능하면 POST 없이 명시적으로 실패한다.
7. marketable FOK BUY를 제출하고 exact terminal fill·fee가 대사될 때까지 `PENDING_BUY`다.

실행 후보 전체는 첫 주문 전에 `QUEUED_NO_POST`로 남긴다. 앞 후보의 로컬 정밀도 오류로 cycle이
끝나도 뒤 후보는 다음 fresh in-band snapshot에서 안전하게 재청구된다. 현재 후보는 POST 직전
`SUBMISSION_IN_PROGRESS`가 되며, `PreSubmissionContractError` 또는 명시적인 no-POST rejection만
재시도한다. venue POST 가능성이 생긴 뒤의 예외·거절은 ledger 대사 없이 재제출하지 않는다.

account/event/cycle capacity는 `20/1/5`, 현재 cycle 신규 목표 원금은 최대 `$25`다. FOK는 선택된
금액의 full fill이 불가능하면 zero fill이며, accepted/order ID만으로 체결을 추정하지 않는다.
결과 불명 `PENDING_BUY`, orphan BUY, BUY 대사 오류는 해당 token/event와 한 capacity만 격리한다.
방향 불명 대사 오류, 일반 `QUARANTINED`, open BUY fill/fee 또는 경제손익 증거 공백은 전역 차단한다.
SELL-only intent·대사 실패는 동일 token/event에만 격리하고 다른 event의 신규 BUY는 계속한다.
bot DB가 만든 Trade만 관리한다.

## Stop, gap과 resolution

v3e effective trigger는 `0.70`(`max(0.70, confirmed entry VWAP-0.30)`)이며 체결가를 보장하지
않는다. White 자료의 모든 고정 손절이 해결 보유보다 나빴고, 최근 0.94/0.95 손절은 최종 승자를
손실로 끝냈다. 0.70은 최적값이 아니라 재난 방어선이다. irreversible SELL 직전에 current Gamma
event와 exact CLOB condition이 각각 OPEN임을 확인하고, 그 뒤 full bid book을 다시 읽어 FOK
SELL한다. spread는 `<=0.10`, cycle SELL은 1건으로 제한한다. 이 한도는 추가 주문 제출만
제한하며 다른 보유 포지션의 가격 확인·해결 상태 대사는 계속한다. 한도에 도달한 실제
추가 손절 주문은 다음 실행으로 미루고, 이미 해결된 포지션의 종결 처리는 막지 않는다.

정상적으로 연속 관측된 book에서는 worst level/VWAP `>= effective stop-0.05`, projected gross loss `<=35%`를
강제한다. 그러나 PSG–Lille처럼 가격이 두 cycle 사이에서 0.70을 크게 건너뛰어도 event와 CLOB이
독립적으로 OPEN이고 fresh complete book이면 두 cap이 손절 자체를 영구 차단하지 않는다. 이
경우 `gap stop`으로 분류해 trigger, prior bid, worst bid, VWAP, spread와 손실을 보존한다. 종료 후
cleanup/dust `0.001`은 OPEN proof에 실패하므로 팔지 않는다.

동일 event에서는 동시에 한 포지션만 허용한다. stop SELL은 먼저 exact confirmed fill로 종결되어야
하며, 그 다음 cycle에 다른 HOME/DRAW/AWAY token이 fresh arm 안에 있으면 한 번만 진입할 수 있다.
같은 token 재매수와 두 번째 전환은 720시간 동안 막는다. 이는 상호배타 결과의 겹친 노출과
왕복 매매를 막는 대신 최소 한 cadence의 지연을 감수하는 규칙이다.

`DELAYED` FOK BUY/SELL은 exact order와 전체 인증 token trade에 체결이 없고 cancellation 응답까지
terminal 부재를 증명할 때만 2분 뒤 0체결로 종결한다. 모호한 주문은 계속 PENDING으로 두며,
zero-fill SELL은 체결을 꾸미지 않고 기존 position을 HOLDING으로 되돌린다.

손절 제출이 계속 거절되거나 accepted SELL 대사가 끝나지 않으면 첫 실패 시각부터 180분 뒤
`QUARANTINED`로 자동 격리 종결한다. 이는 성공한 매도나 포지션 부재가 아니므로 손익을 만들지
않고 account/event capacity를 유지한다. exact order ID가 있는 격리 행은 뒤늦은 confirmed fill
또는 terminal zero-fill 증거를 계속 반영한다. execution ledger와 Trade 결합 자체가 실패하면
중복 SELL을 피하기 위해 즉시 같은 국소 격리 상태로 전환한다.

confirmed SELL P&L과 proven one-hot resolution settlement P&L을 안전 판정에만 합산해 `-$10`에
도달하면 신규 BUY를 차단한다. 모든 live `CONFIRMED` SELL은 execution ledger에서 exact order나
유일한 confirmed BUY로 Trade에 연결한다. 중복·모호한 연결, fee 결손, 범위 밖 size/price는
추정하지 않는다.

## 종목·체결 금액 증거

catalog, snapshot, trade에 종목군, 리그 코드·이름, 원본 tag JSON을 저장한다. 실제 trade에는
목표 USDC, 선택 USDC, 진입 상한 안에서 표시 호가로 가능한 최대 USDC, 축소 사유를 저장한다.
증액 사다리는 `$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000`이다.
이는 기존 fresh book의 로컬 계산이라 네트워크 요청이나 1분 cycle 시간을 늘리지 않는다. 익절은
이 전략에 없고, 손절은 확인된 보유 전량을 `FOK SELL`로 처리한다. 일부만 팔고 완료로 표시하지 않는다.

## Cadence와 overlap

모든 live job은 1분 cadence다. cycle 경과시간은 신호가 아니며 42초 이후 request를 금지하거나
50초에 process를 kill하지 않는다. Gamma/CLOB 요청마다 finite connect/read timeout을 두고,
50초를 넘은 cycle은 warning evidence로 남긴다. DB별 nonblocking run lock 때문에 이전 cycle이
아직 실행 중이면 다음 trigger는 주문·DB 접근 전에 안전하게 skip한다.

Gamma server gate와 lockfile 기반 dependency stamp로 정상 Jenkins end-to-end 시간을 1분 아래로
유지한다. runtime/Jenkins p95, max, overlap skip 수를 첫 24시간 health에서 별도로 확인한다.

## 판정

- 첫 24시간: family별 cursor/identity/opportunity/order/fill/fee/pending, runtime, overlap, DB
  integrity만 확인한다.
- 7일 entry 종료: family와 arm별 unique event funnel, stop/gap execution evidence를 확인한다.
- follow-up: event-clustered fee-net interval과 rare-loss tail을 비교한다.
- CRITICAL/HIGH, mixed cohort, incomplete fill/fee 또는 수동 position 혼입 시 수익성·scale 판단을
  중단한다.

entry `[2026-08-29T04:00:00Z,2026-09-05T04:00:00Z)`, follow-up
`2026-09-12T04:00:00Z`; 현재 Jenkins target은 `$5`로 유지한다. future scale은 accountless
White/Grey displayed-depth evidence와 live confirmed fill evidence를 함께 보고 한 rung씩만 검토한다.
0.92→0.99 조합은 별도 향후 수집에서 최소 100개 독립 경기 전에는 판정하지 않는다.

v3e는 명시적 미주문 503 오분류, BUY 전역 차단, 촘촘한 손절과 증액 시 0건 체결 문제를 보정한
새 source/config 묶음이다. 정확한 변경 계약은
`research/frozen-2026-09-02-order-isolation-sizing-stop-v3e/PREREGISTRATION.md`를 따른다.

v3f는 거래 이력을 수정하지 않고 MLB 경제손익 진입 차단 구간만 v3e 첫 정상 실행 직전
`2026-09-02T12:12:00Z`부터 계산한다. 폐기한 촘촘한 손절의 과거 손실은 성과 증거로
보존하되 교정된 묶음의 `$10` 안전 예산과 합치지 않는다. 실행 원장의 SELL과 증거 공백도
같은 매수 구간에 연결된 것만 집계한다. 세부 계약은
`research/frozen-2026-09-03-mlb-guard-epoch-v3f/PREREGISTRATION.md`를 따른다.
