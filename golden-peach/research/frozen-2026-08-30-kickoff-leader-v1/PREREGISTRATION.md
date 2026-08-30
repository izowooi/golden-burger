# Golden Peach 사전 등록 — kickoff-leader-v1

## 검증 가설

실제 경기가 시작된 뒤 첫 10분 안에 HOME/DRAW/AWAY 각 명제의 직접 YES와
직접 NO, 총 6개 토큰 중 표시 호가 중간값이 유일하게 가장 높은 토큰을 exact
`$5`로 매수하면 작은 가격 상승을 포착할 수 있는지 검증한다. 이 문서는 수익성을
주장하지 않는다.

## 고정 모집단

- 축구만 포함한다.
- EPL, Bundesliga, Ligue 1, LaLiga, Serie A, MLS, UEFA Champions League,
  UEFA Europa League만 포함한다.
- Gamma event가 `live=true`, `ended=false`이고 source 경기 시간이 0~10분일 때만
  신규 진입을 허용한다. 예정 시작시각만으로 경기 시작을 추정하지 않는다.
- 세 결과 명제와 6개 직접 CLOB 토큰이 모두 식별되고, 각 토큰의 exact `$5`
  full-depth book이 있을 때만 비교한다.
- 선두와 2위의 중간값 차이는 최소 0.005, 진입 full-depth VWAP은 0.60~0.94,
  표시 spread는 최대 0.05다.

## A/B와 실행

- `polybot-eco` / `peach-live-eco-3pp-1m-v1`: 익절 +0.03.
- `polybot-fruit` / `peach-live-fruit-5pp-1m-v1`: 익절 +0.05.
- 공통 손절은 confirmed entry VWAP -0.10이다.
- source 80분부터는 정상 익절 폭의 절반만 충족해도 이익 청산한다.
- source 80분부터 손실 중이면 신규 손절을 제출하지 않고 증명된 resolution까지
  보유한다. 정상 익절은 계속 허용한다.
- 한 event에서 실제 또는 불확실 BUY가 한 번 생기면 TP/SL 이후에도 재진입하지
  않는다. exact terminal zero-fill만 fresh retry를 허용한다.
- 모든 BUY/SELL은 FOK이며 live notional은 exact `$5`다.
- 한 실패한 SELL은 다른 event를 막지 않는다. 해결되지 않은 SELL 노출은 180분
  뒤 성공 체결로 꾸미지 않고 `QUARANTINED`로 격리한다.

## Shadow 수집

`polybot-grey` / `peach-shadow-1m-v1`은 credential 없이 simulation으로 실행한다.
각 1분 sweep에서 6개 직접 토큰의 정규화된 전체 bid/ask levels, exact `$5` ask
VWAP, best bid/ask, spread, Gamma metadata와 source 경기 시간을 보존한다. 이 자료의
표시 호가 손익을 actual fill 또는 realized P&L로 해석하지 않는다.

## 기간과 판정

- 신규 진입: `[2026-08-30T00:00:00Z, 2026-09-13T00:00:00Z)`.
- 후속 resolution 관측: `2026-09-20T00:00:00Z`까지.
- 과거 35-event 재생은 탐색 자료다. 두 live arm 비교는 배포 후 단일
  `config_hash × strategy_source_digest × mode × job_name` cohort만 사용한다.
- 체결·수수료가 완전 대사되지 않은 row로 수익성을 판정하지 않는다.
