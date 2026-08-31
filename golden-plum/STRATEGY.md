# Golden Plum — 축구 경기 중반 상승 확인

## 한 줄 가설

축구 승·무·패의 직접 YES/NO 여섯 token 중 하나가 일정한 상승 경로로 0.75를 처음
통과하면, 단일 시점의 0.75보다 0.90 또는 0.95까지 추가 상승할 가능성을 더 잘
선별할 수 있다.

## 왜 별도 전략인가

Golden Peach는 경기 시작 0~10분의 현재 선두를 즉시 산다. Golden Watermelon Live는
경기 막판 0.96/0.99에 들어간다. Golden Plum은 두 전략 사이의 5~75분에서 같은 token의
최근 경로를 먼저 확인하고, 80분 전에 반드시 포지션을 끝낸다. 따라서 기존 DB와
runtime job을 섞지 않는다.

운영자가 제공한 탐색 보고서는 0.60/0.65/0.75 진입과
0.90/0.95 익절을 탐색 후보로 제시했다. 그러나 여섯 호가의 최고값은 세 결과 중
가장 낮은 확률의 반대편(NO) 때문에 이론적으로 대략 0.67 이상이므로 0.60 최초 교차는
구조적으로 부적합하다. 2026-08-31 Golden Peach direct six-book 17경기의 엄격 재생에서도
0.60 조건은 0건이었다. 0.75·3회·+0.02·stop 0.15 조건은 3건이었고, 표시 호가 기준
0.90 target은 -$0.11, 0.95 target은 +$0.54였다. 수수료·실제 체결이 아니며 표본이
지나치게 작으므로 이는 승격 근거가 아니라 앞으로 두 target을 비교할 탐색 근거다.

## 모집단

- EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL
- regular-time HOME/DRAW/AWAY 세 binary 명제
- 각 명제의 direct YES와 direct NO, 정확히 여섯 token
- Gamma explicit in-play와 source 경기 시계 5~75분
- 누적 거래량 5,000, 유동성 5,000 이상
- exact `$5` full-depth ask/bid와 진입 spread 0.05 이하

합성 NO, 예정 kickoff, 로컬 벽시계, title substring만으로 identity나 경기 시간을
만들지 않는다.

## 진입

1. current event의 여섯 direct book을 같은 cycle에서 읽는다.
2. 각 token의 exact `$5` ask VWAP snapshot을 token ID별로 저장한다.
3. current midpoint의 유일한 선두와 2위 margin이 0.005 이상인지 확인한다.
4. 선두 token의 최근 3개 snapshot이 각각 90초 이내인지 확인한다.
5. 세 가격의 누적 상승이 0.02 이상이고 인접 하락이 각각 0.01 이하인지 확인한다.
6. 두 번째 가격이 0.75 미만이고 current exact ask VWAP이 `[0.75,0.78]`이면 첫
   상향 교차로 인정한다.
7. POST 직전에 source clock, 여섯 fresh book, 선두 identity, spread, exact VWAP을
   다시 읽고 exact `$5` FOK BUY를 낸다.

event당 실제 체결이나 venue 도달 여부가 불확실한 BUY는 한 번만 허용한다.
명시적인 terminal zero-fill만 같은 event에서 재시도할 수 있다.

## A/B와 청산

| arm | Jenkins/runtime | 익절 |
|---|---|---:|
| A | `polybot-king/plum-live-king-90-1m-v1` | full-position bid VWAP 0.90 |
| B | `polybot-queen/plum-live-queen-95-1m-v1` | full-position bid VWAP 0.95 |

- 공통 stop: confirmed BUY VWAP -0.15
- 공통 time exit: source minute 80의 첫 실행 가능한 full-position bid VWAP
- 우선순위: target → stop → minute-80 exit
- SELL도 FOK이며 confirmed size/VWAP/fee 전에는 완료로 세지 않는다.

두 live arm의 차이는 절대 target 하나뿐이다. 서로 다른 wallet의 수동 포지션은
DB에 편입하지 않는다.

## 실패 격리

한 BUY/SELL 실패가 뒤의 event를 막지 않는다. 해당 event의 episode와 order ledger만
격리하고 남은 bounded capacity에서 다른 event를 계속 처리한다. 연속 180분 동안
BUY 또는 SELL 노출을 확정하지 못하면 `QUARANTINED`로 남겨 경제적 open capacity를
소모하게 하며, 성공 체결·0 exposure·realized P&L로 꾸미지 않는다.

총 open 10, event open 1, cycle 신규 5, cycle emergency SELL 10이다. confirmed SELL과
proven resolution의 누적 손실이 10 USDC에 도달하거나 execution evidence gap이 있으면
신규 진입을 막는다.

## Silver 자료 수집

`polybot-silver/plum-shadow-silver-1m-v1`은 credential-free simulation이다. 경기 중
여섯 direct full-depth book, source clock, market identity, trend lineage, path와
resolution을 저장한다. `scripts/replay_direct_six_book.py`는 같은 event에서 entry,
target, stop, trend 길이와 누적 움직임 grid를 재생한다.

Silver의 반사실 행은 실제 주문이나 P&L이 아니다. 같은 경기의 여러 grid cell을
독립 표본으로 세지 않는다.

## 반증·승격 기준

- 첫 24시간: cadence, source clock, six-book, lineage, DB/order/fill integrity만 검사
- common eligible event 20개 전: A/B 방향 판단 금지
- arm당 confirmed closed 50개와 common event 30개 전: 금액 증액 금지
- 100경기 전: Silver 결과로 live threshold를 사후 변경하지 않음
- CRITICAL/HIGH evidence issue, fill/fee gap, cohort 혼합: 성과 판정 중단
- paired fee 포함 95% 신뢰구간이 0을 포함하면 우승 arm 없음

전체 동결값과 기간은
`research/frozen-2026-08-31-midgame-confirmation-v1/PREREGISTRATION.md`가 권위다.
