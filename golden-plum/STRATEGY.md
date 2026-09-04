# Golden Plum — 종목별 경기 전체 상승 확인

## 한 줄 가설

한 경기의 직접 결과 token 중 유일한 선두가 일정한 상승 경로로 기준점을 처음 통과하면,
단일 시점 가격보다 추가 상승할 가능성을 더 잘 선별할 수 있다. 축구는 승·무·패의 직접
YES/NO 여섯 token, MLB·NBA·NFL·NHL은 두 팀 moneyline의 직접 두 token을 사용한다.

## 왜 별도 전략인가

Golden Peach는 경기 시작 0~10분의 현재 선두를 즉시 산다. Golden Watermelon Live는
경기 막판 0.96/0.99에 들어간다. Golden Plum은 경기 시작부터 종료까지 같은 token의
최근 경로를 먼저 확인한다. 시간으로 강제 청산하지 않고 익절·손절·검증된 resolution
중 하나로만 끝낸다.

2026-08-31의 최초 v1은 5~75분 진입과 80분 청산을 사용했다. 이후 운영자 지시로 해당
시간 조건을 제거했으며, 기존 자료는 보존하고 v2 소스·사전등록 해시부터 별도 코호트로
분석한다. 열린 주문 대사 연속성을 위해 runtime job과 DB 경로는 유지한다.

2026-09-01 v3는 축구 live 계약을 바꾸지 않고 종목별 profile과 direct two-team book
경로를 추가했다. Silver는 축구, Gold는 MLB를 1분 cadence로 수집한다. 2026-09-02 v4는
같은 외장 Gold job에 NFL·NBA runtime을 별도 DB로 추가한다. 2026-09-03 v7은 NHL을
네 번째 Gold DB로 추가하고, Gold MLB 완결 15경기의 탐색 재생을 바탕으로 King/Queen에
별도 MLB 최소금액 runtime을 추가한다. direct collector runtime은 병렬 실행하며
NFL 플레이오프·Super Bowl, NBA Cup·play-in·플레이오프·Finals의 실제 1군 두 팀 경기를
포함한다. NHL의 Stanley Cup도 exact NHL identity일 때 포함한다. direct sport의 현재 수치는 최적값이
아니라 원자료와 해결 경로를 모으기 위한 사전 등록 primary다.

2026-09-02 실행 안전 보정은 현재 King/Queen 목표 `$5`와 A/B target을 유지하면서, 향후 목표
금액을 올렸을 때 0건 체결로 끝나지 않도록 같은 fresh book에서 전량 체결 가능한 가장 큰 금액의
원자적 FOK 한 건을 선택한다. catalog/snapshot/trade에 종목·리그·tag를, trade에 목표·선택·최대
표시 가능 금액과 축소 사유를 저장한다. 소스 해시가 달라지므로 보정 전후 자료는 분리한다.

같은 날 v6 실행 보정은 큰 보유량의 익절가 이상 bid가 일부만 있을 때 그 최대 수량을 FOK로
익절하고, exact confirmed fill 뒤 잔여 수량을 같은 Trade의 `HOLDING`으로 이어간다. 익절
수량과 잔여 수량은 각각 venue 최소 5 shares 이상이어야 하며, 손절은 계속 잔여 전량만
FOK로 제출한다. fresh book과 선택·잔여·최대 실행 가능 수량/금액은 append-only
`exit_execution_observations`에 주문 전에 저장한다. 진입·익절·손절 수치 자체는 바꾸지 않는다.

운영자가 제공한 탐색 보고서는 0.60/0.65/0.75 진입과
0.90/0.95 익절을 탐색 후보로 제시했다. 그러나 여섯 호가의 최고값은 세 결과 중
가장 낮은 확률의 반대편(NO) 때문에 이론적으로 대략 0.67 이상이므로 0.60 최초 교차는
구조적으로 부적합하다. 2026-08-31 Golden Peach direct six-book 17경기의 엄격 재생에서도
0.60 조건은 0건이었다. 0.75·3회·+0.02·stop 0.15 조건은 3건이었고, 표시 호가 기준
0.90 target은 -$0.11, 0.95 target은 +$0.54였다. 수수료·실제 체결이 아니며 표본이
지나치게 작으므로 이는 승격 근거가 아니라 앞으로 두 target을 비교할 탐색 근거다.

## 모집단

### 축구

- EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL
- regular-time HOME/DRAW/AWAY 세 binary 명제
- 각 명제의 direct YES와 direct NO, 정확히 여섯 token
- Gamma explicit `live=true`, `ended=false`
- source 경기 시계 0분부터 종료까지; minute 및 wall-clock age 상한 없음
- 누적 거래량 5,000, 유동성 5,000 이상
- exact `$5` full-depth ask/bid와 진입 spread 0.05 이하

합성 NO, 예정 kickoff, 로컬 벽시계, title substring만으로 identity나 경기 시간을
만들지 않는다. source 시계가 누락된 cycle은 raw 호가와 누락 사유를 저장하되 진입을
추정하지 않는다.

### MLB·NFL·NBA·NHL 자료 수집

- 종목별 exact sport/tag/root-series/team-league identity
- whole-game 최상위 two-team moneyline 한 개와 두 팀의 직접 token
- Gamma explicit `live=true`, `ended=false`; 시작부터 종료와 extra innings까지 관측
- child/inning/prop/future/spread/total/minor league/esports 제외
- MLB World Series, NFL 플레이오프·Super Bowl, NBA Cup·play-in·플레이오프·Finals에서
  공식 season series와 해당 최상위 리그 두 팀이 확인된 실제 경기를 포함
- NHL 플레이오프·Stanley Cup Final도 exact NHL root/season/team identity일 때 포함
- 대회 우승자·conference/division winner 같은 futures는 제외
- 누적 거래량 5,000, 유동성 5,000 이상
- 이닝·quarter를 축구 minute로 변환하지 않으며 `source_elapsed_minutes`는 NULL로 보존
- snapshot UTC 간격과 명시적 live/ended lifecycle로 추세와 종료를 연결

NHL은 별도 versioned family profile과 direct two-team 계약으로 Gold에서 수집한다. NHL
live는 허용하지 않는다.

## 진입

1. current event의 완전한 direct book set을 같은 cycle에서 읽는다(축구 6, direct sport 2).
2. 각 token의 baseline exact `$5` ask VWAP snapshot을 token ID별로 저장한다.
3. current midpoint의 유일한 선두와 2위 margin이 0.005 이상인지 확인한다.
4. 선두 token의 최근 3개 snapshot이 각각 90초 이내인지 확인한다.
5. 세 가격의 누적 상승이 0.02 이상이고 인접 하락이 각각 0.01 이하인지 확인한다.
6. 두 번째 가격이 0.75 미만이고 current exact ask VWAP이 `[0.75,0.78]`이면 첫
   상향 교차로 인정한다.
7. POST 직전에 explicit live 상태, 필요한 경우 source clock, 완전한 fresh book set,
   선두 identity, spread, exact VWAP을 다시 읽는다. 목표가 `$5`보다 크면 같은 book에서 가격
   상한 안에 전량 체결 가능한 가장 큰 사다리 금액을 선택해 FOK BUY 한 건을 낸다.

event당 실제 체결이나 venue 도달 여부가 불확실한 BUY는 한 번만 허용한다.
명시적인 terminal zero-fill만 같은 event에서 재시도할 수 있다.

## A/B와 청산

| arm | Jenkins/runtime | 익절 |
|---|---|---:|
| A | `polybot-king/plum-live-king-90-1m-v1` | 절대 익절 하한 0.90 |
| B | `polybot-queen/plum-live-queen-95-1m-v1` | 절대 익절 하한 0.95 |

- 공통 stop: confirmed BUY VWAP -0.15
- 시간 강제 청산: 없음
- 종료 우선순위: target → stop; 둘 다 없으면 검증된 resolution까지 유지
- SELL도 FOK이며 confirmed size/VWAP/fee 전에는 완료로 세지 않는다.
- 목표 금액을 올려도 BUY는 선택된 금액 전체가 체결되거나 0체결이다. 거래소의 불명확한 부분
  체결을 전략 완료로 인정하지 않는다.
- TP는 각 bid가 목표가 이상인 최대 수량을 0.01 share 단위로 FOK 제출할 수 있다. 부분
  익절 뒤 실제 잔여가 있으면 완료가 아니라 `HOLDING`이며 다음 주기에 다시 평가한다.
- SL은 확인된 잔여 보유량 전량만 FOK로 제출한다. 전량 깊이가 없으면 부분 손절하지 않고
  event-local 재시도 상태로 남긴다.

같은 종목의 두 live arm 차이는 절대 target 하나뿐이다. 서로 다른 wallet의 수동 포지션은
DB에 편입하지 않는다. 기존 축구 runtime과 별도 MLB runtime은 DB·config cohort를 분리한다.
MLB는 `[0.55,0.58]` 첫 교차, 최근 5회 누적 `+0.01`, 진입가 `-0.15` 손절을 공통으로
고정하고 King `0.90`/Queen `0.95`만 비교한다. 이 값은 15경기 탐색 자료이므로 최소
100경기 전 최적값이나 증액으로 해석하지 않는다.

## 실패 격리

한 BUY/SELL 실패가 뒤의 event를 막지 않는다. 해당 event의 episode와 order ledger만
격리하고 남은 bounded capacity에서 다른 event를 계속 처리한다. 연속 180분 동안
BUY 또는 SELL 노출을 확정하지 못하면 `QUARANTINED`로 남겨 경제적 open capacity를
소모하게 하며, 성공 체결·0 exposure·realized P&L로 꾸미지 않는다.

live `HOLDING`은 confirmed BUY size/VWAP/fee가 빠지면 신규 진입을 전역 fail-closed로
막는다. simulation `HOLDING`은 displayed-book 가정이라 venue fill field가 없는 것이 정상이며
그 사유와 mode를 cycle evidence에 남기되 신규 진입을 막지 않는다. 두 mode 모두 open row와
미해결 live BUY intent를 `max_positions` capacity에서 제외하지 않는다.

부분 익절의 `sell_shares`·`realized_pnl`은 누적 confirmed 값이고 `buy_shares`는 현재 잔여
수량이다. 원래 매수량은 `buy_confirmed_size`로 보존한다. 해결 시에는 이미 실현된 손익을
유지하고 잔여 수량의 payout 손익만 별도로 더해 중복 계산을 막는다.

총 open 10, event open 1, cycle 신규 5, cycle emergency SELL 10이다. confirmed SELL과
proven resolution의 누적 손실이 10 USDC에 도달하거나 execution evidence gap이 있으면
신규 진입을 막는다.

## Silver·Gold 자료 수집

`polybot-silver/plum-shadow-silver-1m-v1`은 credential-free simulation이다. 경기 전체의
여섯 direct full-depth book, source clock, market identity, trend lineage, path와
resolution을 저장한다. 추가로 각 snapshot의
`$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000` displayed
ask를 걸어 산 shares를 같은 시점 bid에 전량 팔 수 있는지와 양쪽 VWAP·level 수·수수료 전
왕복 손익을 `execution_capacity_json`에 저장한다.

`polybot-gold`는 같은 방식으로 MLB·NFL·NBA·NHL의 두 팀 direct book을 각 runtime DB에
수집한다. Gamma는 각 family tag·누적 거래량·유동성을 server-side로 먼저 거르고 종목당
최대 2페이지까지만 허용한다. CLOB full books는 batch로 한 번 읽고 증액 ladder는 cached
book을 로컬 계산하므로 추가 book 요청이 없다. 네 runtime은 병렬 실행하며, 이 계산은
simulation 전용이라 King/Queen의 실거래 cycle 시간을 늘리지 않는다.

경기 종료 뒤 live Gamma 응답에서 condition이 사라지면 같은 condition의 public CLOB
market을 보완 조회한다. 기존 catalog와 condition·두 token·outcome이 정확히 일치하고,
closed 상태에서 한 token만 1이고 다른 token은 0인 one-hot 또는 winner가 없고 두 token이
각각 정확히 `0.5`인 void만 append-only terminal evidence로 기록한다. Gamma 자체의
`0.5/0.5`는 `closed=true`와 `umaResolutionStatus=resolved`를 함께 요구한다. 불일치·미해결·
열린 상태는 계속 후속 추적하며, 이 자료로 가상의 SELL이나 실현 손익을 만들지 않는다.
live trade는 terminal BUY fill/fee evidence가 완전할 때만 settlement를 적용한다.

`scripts/replay_direct_six_book.py --sport-family <family>`는 같은 event에서 entry, target,
stop, trend 길이와 누적 움직임 grid를 재생한다. 종목별 profile은 별도로 versioning하므로
향후 MLB 수치를 바꿔도 축구 수치가 함께 바뀌지 않는다.

append-only DB에 여러 배포 세대가 함께 있으면 `--config-hash`로 정확히 한 cohort를
선택한다. 실패 run의 snapshot은 제외하며, 후속 배포가 확인한 terminal 결과는 성공 run,
exact condition·token, 동일 sport/profile/protocol/classifier/mapping이 모두 맞을 때만 선택한
snapshot cohort에 연결한다.

Silver·Gold의 반사실 행은 실제 주문이나 P&L이 아니다. 같은 경기의 여러 grid cell을
독립 표본으로 세지 않는다.

## 반증·승격 기준

- 첫 24시간: cadence, full-game lifecycle, 완전한 direct book set, capacity ladder, lineage,
  DB/order/fill integrity와 1분 runtime만 검사
- common eligible event 20개 전: A/B 방향 판단 금지
- arm당 confirmed closed 50개와 common event 30개 전: 금액 증액 금지
- 종목별 해결 event 100개 전: simulation 결과로 live threshold를 사후 변경하지 않음
- CRITICAL/HIGH evidence issue, fill/fee gap, cohort 혼합: 성과 판정 중단
- paired fee 포함 95% 신뢰구간이 0을 포함하면 우승 arm 없음

MLB 동결값과 기간은
`research/frozen-2026-09-01-multisport-mlb-shadow-v3/PREREGISTRATION.md`, NFL·NBA는
`research/frozen-2026-09-02-nba-nfl-shadow-v4/PREREGISTRATION.md`가 권위다.
MLB live A/B와 NHL 수집은
`research/frozen-2026-09-03-mlb-live-ab-v7/PREREGISTRATION.md`,
`research/frozen-2026-09-03-nhl-shadow-v7/PREREGISTRATION.md`가 권위다.
공통 실행 보정은
`research/frozen-2026-09-02-partial-profit-exit-v6/PREREGISTRATION.md`를 따른다.
과거 v1·v2 계약은 원래 폴더에 변경 없이 보존한다.
