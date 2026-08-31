# Golden Plum 사전 등록 — midgame-confirmation-v1

## 검증 가설

축구 인플레이 HOME/DRAW/AWAY 명제의 직접 YES·NO 여섯 호가 중 하나가 경기 중
처음으로 0.75를 상향 통과할 때, 직전 세 번의 1분 관측이 같은 token에서 대체로
상승하고 누적 상승폭이 2%p 이상이면 단일 순간 가격보다 추가 상승을 더 잘 예측한다.
이 가설은 아직 수익 전략으로 입증되지 않았다.

반증 사례를 적극적으로 포함한다.

- 0.75 진입 뒤 0.90/0.95에 도달하지 못하고 15%p 하락하는 경로
- 상승 확인 직후 반전하는 경로
- 80분 강제 청산 시 손실인 경로
- 직접 NO가 선택된 경로와 YES가 선택된 경로
- 주문 실패·부분 체결·대사 불확실성으로 실제 실행이 반사실 경로와 달라지는 사례

## 모집단

- 종목: 축구만
- 대회: EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UCL, UEL
- 시장: regular-time HOME/DRAW/AWAY 세 명제
- 호가: 각 명제의 직접 YES와 직접 NO, event당 정확히 여섯 token
- source 상태: `live=true`, `ended=false`, 명시적 경기 시계 5~75분
- Gamma 선필터: 누적 거래량 5,000 이상, 유동성 5,000 이상
- 실행 필터: exact `$5` full-depth ask/bid, 진입 spread 0.05 이하

예정 kickoff나 로컬 벽시계로 source 경기 시계를 보간하지 않는다. 합성 `1-YES`는
실거래 결정과 Silver의 직접 호가 근거로 사용하지 않는다.

## 공통 진입

1. 같은 token의 최근 3개 snapshot이 각각 90초 이내 간격이어야 한다.
2. 세 exact `$5` ask VWAP의 첫 값에서 마지막 값까지 누적 상승이 0.02 이상이어야 한다.
3. 인접 관측의 하락은 각각 최대 0.01까지만 허용한다.
4. 직전 값은 0.75 미만, 현재 값은 `[0.75, 0.78]`이어야 한다.
5. 현재 event의 직접 여섯 book이 모두 실행 가능하고, 선택 token의 midpoint가 여섯
   token 중 유일한 1위이며 2위보다 0.005 이상 높아야 한다.
6. event당 filled 또는 결과 불확실 BUY는 한 번만 허용한다.

## A/B 처치

| Jenkins | runtime job | mode | 익절 |
|---|---|---|---:|
| `polybot-king` | `plum-live-king-90-1m-v1` | live A | 절대 bid VWAP 0.90 |
| `polybot-queen` | `plum-live-queen-95-1m-v1` | live B | 절대 bid VWAP 0.95 |
| `polybot-silver` | `plum-shadow-silver-1m-v1` | simulation/raw | primary 0.95 + 전체 재생 자료 |

공통 손절은 confirmed BUY VWAP에서 0.15 하락한 full-position bid VWAP이다.
source 80분에는 손익과 무관하게 실행 가능한 전체 bid VWAP으로 강제 청산한다.
익절값 외에 두 live arm의 코드, 분류, 진입, 손절, 금액, cadence와 저장 계약은 같다.

## 실행·노출 안전장치

- 주문 금액은 exact `$5`, FOK만 사용한다.
- 총 open 10, event open 1, cycle 신규 5, cycle emergency SELL 10이다.
- 수동 wallet position은 봇 DB에 편입하거나 청산하지 않는다.
- BUY/SELL 실패는 해당 event에만 격리한다. 다른 event의 관리와 남은 bounded entry를
  계속하며, 180분 뒤에도 노출을 증명할 수 없으면 성공으로 꾸미지 않고
  `QUARANTINED`로 보존한다.
- confirmed fill·fee evidence가 없으면 actual P&L로 세지 않는다.
- confirmed SELL과 proven resolution의 합산 손실이 10 USDC에 도달하면 신규 진입을
  중지한다.

## Silver 수집 계약

Silver는 credential이 없는 simulation runtime이다. 인플레이 동안 여섯 token의 raw
full-depth book, exact `$5` VWAP, source clock, Gamma identity, snapshot lineage와
resolution을 보존한다. 다음 반사실 grid를 같은 event cohort에서 재생할 수 있어야 한다.

- entry: 0.55, 0.60, 0.65, 0.70, 0.75, 0.80
- target: 0.85, 0.90, 0.95
- stop delta: 0.05, 0.10, 0.15, 0.20
- trend observations: 2, 3, 5
- trend cumulative move: 0.01, 0.02, 0.03, 0.05

grid cell을 독립 경기처럼 세지 않고 `event_id`를 paired unit으로 유지한다.

## 시간·판정

- 신규 진입: `[2026-08-31T00:00:00Z, 2026-09-14T00:00:00Z)`
- follow-up: `[2026-09-14T00:00:00Z, 2026-09-21T00:00:00Z)`
- 첫 24시간: cadence, six-book/source-clock coverage, DB·주문·fill 무결성만 판정
- 첫 파라미터 비교: common eligible event 20개 이상
- 승격 또는 금액 증액: arm당 confirmed closed trade 50개 이상, common event 30개 이상,
  fee 포함 paired 결과와 95% 신뢰구간, CRITICAL/HIGH evidence gap 0일 때만 검토
- 100경기 전에는 Silver grid를 보고 사후적으로 live 기준을 바꾸지 않는다.

과거 Golden Watermelon YES-only 자료와 Golden Peach 직접 six-book 자료는 탐색 근거다.
배포 뒤 Golden Plum cohort와 합쳐 actual 성과를 만들지 않는다.

여섯 호가 최고값은 세 결과 중 최소 확률 결과의 NO 때문에 정상적인 보완 가격에서는
약 0.67 이상이다. 따라서 0.60 최고값 최초 교차를 live 진입으로 쓰지 않는다. 동기화한
Golden Peach Grey DB(`sha256=08fba892...b801b`, source cutoff
`2026-08-31T01:20:13Z`)의 10,499 snapshot/17 event 엄격 재생에서 primary 0.75 조건은
target별 3건뿐이었다. 이 소표본을 수익 증거로 해석하지 않는다.
