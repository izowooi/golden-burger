# Golden Plum 사전 등록 — full-match-no-time-exit-v2

## 변경 사유와 코호트 경계

운영자 지시에 따라 2026-08-31 배포 이후에는 경기 시계 5~75분 제한과 source 80분
강제 청산을 사용하지 않는다. 기존
`frozen-2026-08-31-midgame-confirmation-v1` 자료는 수정하거나 삭제하지 않으며, v2는
`config_hash × strategy_source_digest × mode × job_name`과 실제 첫 성공 run 시각으로
분리한다. 열린 주문과 포지션의 대사 연속성을 보존하기 위해 runtime job과 DB 경로는
그대로 유지한다.

## 검증 가설

축구 인플레이 HOME/DRAW/AWAY 명제의 직접 YES·NO 여섯 호가 중 하나가 경기 중
처음으로 0.75를 상향 통과할 때, 직전 세 번의 1분 관측이 같은 token에서 대체로
상승하고 누적 상승폭이 2%p 이상이면 0.90 또는 0.95까지 추가 상승할 가능성을
선별할 수 있는지 검증한다. 이 가설은 아직 수익 전략으로 입증되지 않았다.

## 모집단과 시간 계약

- 종목: 축구만
- 대회: EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL
- 시장: regular-time HOME/DRAW/AWAY 세 명제
- 호가: 각 명제의 직접 YES와 직접 NO, event당 정확히 여섯 token
- 상태: Gamma `live=true`, `ended=false`, event/market active, order book enabled
- 시간: kickoff 이후 source minute `0`부터 source가 경기를 ended로 표시할 때까지
- source minute 상한: 없음
- wall-clock in-play age 상한: 없음
- Gamma 선필터: 누적 거래량 5,000 이상, 유동성 5,000 이상
- 실행 필터: exact `$5` full-depth ask/bid, 진입 spread 0.05 이하

진입에는 명시적인 source 경기 시계가 필요하다. 시계가 누락된 cycle은 여섯 raw book과
누락 사유를 수집할 수 있지만 진입으로 추정하지 않는다. 예정 kickoff나 로컬 벽시계로
경기 시계를 보간하지 않고, 합성 `1-YES`도 사용하지 않는다.

## 공통 진입

1. 같은 token의 최근 3개 snapshot이 각각 90초 이내 간격이어야 한다.
2. 세 exact `$5` ask VWAP의 누적 상승이 0.02 이상이어야 한다.
3. 인접 관측의 하락은 각각 최대 0.01까지만 허용한다.
4. 직전 값은 0.75 미만, 현재 값은 `[0.75, 0.78]`이어야 한다.
5. 현재 event의 직접 여섯 book이 모두 실행 가능하고, 유일한 선두와 2위 midpoint
   차이가 0.005 이상이어야 한다.
6. POST 직전에 source 상태, 여섯 fresh book, 선두 identity, spread와 VWAP을 다시
   확인한 뒤 exact `$5` FOK BUY를 제출한다.

## A/B와 종료

| arm | Jenkins/runtime | 절대 익절 |
|---|---|---:|
| A | `polybot-king/plum-live-king-90-1m-v1` | full-position bid VWAP 0.90 |
| B | `polybot-queen/plum-live-queen-95-1m-v1` | full-position bid VWAP 0.95 |

- 공통 손절: confirmed BUY VWAP `-0.15`
- 시간 강제 청산: 없음
- 종료: 익절, 손절 또는 unique one-hot resolution 증명
- SELL도 FOK이며 confirmed size/VWAP/fee 전에는 완료로 세지 않는다.

v1 DB 행의 `force_exit_minute_at_buy=80`은 과거 계약 증거로 보존하되 v2 실행기는 이를
청산 신호로 사용하지 않는다.

## 주문 실패 격리

한 BUY/SELL 실패는 해당 event에만 격리하고 남은 capacity에서 다른 event를 계속
처리한다. 연속 180분 동안 주문 노출을 확정하지 못하면 `QUARANTINED`로 남기며 체결,
0 exposure 또는 실현 손익으로 꾸미지 않는다. 이는 포지션 시간 청산이 아니라 불확실한
주문 증거의 격리 규칙이다.

## Silver 주문 증액 자료

`polybot-silver/plum-shadow-silver-1m-v1`만 각 direct book snapshot에서 다음 displayed
notional을 재생한다.

`5, 10, 25, 50, 100, 250, 500 USDC`

각 단위별로 full ask 체결 가능 여부, ask VWAP·limit·shares·사용 level 수, 같은 snapshot의
full bid 재매도 가능 여부, bid VWAP·proceeds·사용 level 수와 수수료 전 왕복 손익을
`execution_capacity_json`에 저장한다. 이는 실제 주문이나 실제 체결 가능률이 아니라
동일 시점 표시 호가의 반사실이다. King과 Queen은 raw full-depth `book_json`만 보존하고
추가 계산을 하지 않아 1분 live cycle의 지연을 늘리지 않는다.

## 기간과 판정

- 진입 기간: `[2026-08-31T00:00:00Z, 2026-09-14T00:00:00Z)`
- follow-up 종료: `2026-09-21T00:00:00Z`
- 첫 24시간: cadence, full-match coverage, six-book, capacity JSON, lineage, DB/order/fill
  무결성과 cycle runtime만 판정
- common eligible event 20개 전: A/B 방향 판단 금지
- arm당 confirmed closed 50개와 common event 30개 전: 금액 증액 금지
- Silver event 100개 전: 진입·익절·손절·시간 조건 사후 변경 금지
- CRITICAL/HIGH evidence issue, fill/fee gap 또는 cohort 혼합 시 성과 판정 중단

기존 v1과 v2는 한 성과 표본으로 합치지 않는다.
