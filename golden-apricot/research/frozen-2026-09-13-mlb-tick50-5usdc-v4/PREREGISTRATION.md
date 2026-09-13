# Golden Apricot MLB Tick50 $5 v4

## 결정

- `polybot-eco / apricot-live-eco-mlb-tick50-hold-v1`: full-holding bid VWAP `.98`
- `polybot-fruit / apricot-live-fruit-mlb-tick50-tp99-v1`: full-holding bid VWAP `.99`
- 공통: MLB direct HOME/AWAY, 첫 완전 공통 tick 후 `[50,52]`분, midpoint favorite,
  exact `$5` FOK BUY, TP 미도달 시 exact one-hot resolution
- confirmed SELL과 proven resolution의 누적 경제손실 한도: 팔별 절대 `$300`
- wallet 입출금, 요청 가격, `trades.realized_pnl`은 guard와 성과에서 제외

## 재교정 근거

기존 117경기를 앞 82 train/뒤 35 validation으로 고정하고 2026-09-12~13의 신규 경기는
파라미터 선택에 쓰지 않은 holdout으로 유지했다. 기존 Tick50은 TP `.98`에서 train
`+5.47%`, validation `-4.01%`, holdout `-14.57%`; TP `.99`에서 `+6.13%`, `-3.29%`,
`-14.00%`였다. 일자 cluster bootstrap 95% interval은 두 팔 모두 0을 포함했다.

Tick40/60/70/80, 가격 band, net TP 1/2/3/5%, tick150/160/170 강제청산을 탐색했으나
train·validation·holdout에서 방향이 안정적인 대안이 없었다. 따라서 진입·TP·resolution
정책을 사후 holdout에 맞춰 바꾸지 않는다. `$10`은 tail loss를 금액으로 두 배 확대했으므로
목표 금액만 `$5`로 되돌린다.

## 실행·관측 계약

- A/B의 유일한 차이는 TP `.98/.99`다.
- 신규 source digest와 config hash부터 v3 cohort로 센다. 과거 `$10` cohort를 합치지 않는다.
- 기존 DB는 연속 사용하되 매 거래의 진입 config/source로 cohort를 분리한다.
- 1분 live cycle은 compact, retention scan, execution-ledger DDL과 90일 legacy bootstrap을
  수행하지 않는다. 배포 preflight와 별도 off-cycle maintenance가 이를 담당한다.
- arm별 confirmed 종결 100건과 10개 독립 경기일 전에는 금액 재증액이나 새 parameter를
  승격하지 않는다.
