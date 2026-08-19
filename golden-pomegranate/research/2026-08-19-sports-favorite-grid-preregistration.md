# Sports Favorite 6h Grid — retrospective protocol freeze

작성 시각: 2026-08-19 20:57 KST

상태: 사용자가 제시한 `0.80→0.90`, `0.90→resolution`, `0.85→0.95` 계열 가설의
retrospective counterfactual 검정이다. 계산 결과를 보기 전에 아래 규칙과 선택 기준을
고정한다.

## 증거 수준과 기간

- 원자료: 검증된 `pomegranate-15m-v2` UTC daily shard 12개
- 전체 범위: `[2026-08-07T00:00:00Z, 2026-08-19T00:00:00Z)`
- parameter-selection 구간: `[2026-08-07T00:00:00Z, 2026-08-13T00:00:00Z)`
- temporal-validation 구간: `[2026-08-13T00:00:00Z, 2026-08-19T00:00:00Z)`
- 8월 6일 partial shard는 제외한다.
- 같은 원자료를 앞선 underdog 탐색에서 이미 열어봤으므로 뒤 6일을 `untouched holdout`이라고
  부르지 않는다. 이번 사용자 가설의 코드·grid는 결과를 보기 전에 고정하지만 최종 판정은
  어디까지나 retrospective discovery다.

## 시장과 시간 규칙

1. Gamma tag에 `sports`가 있고 `active=true`, `closed=false`,
   `enableOrderBook=true`, `acceptingOrders=true`인 2-outcome 시장만 쓴다.
2. outcome price 두 개의 합은 `[0.98, 1.02]`여야 한다.
3. `end_date`까지 남은 시간이 `>6h`에서 `(0h, 6h]`로 들어오는 연속 관측이 있어야 한다.
   두 관측의 `end_date`는 같고 receipt 간격은 5~30분이어야 한다. 이 조건으로 collector 시작
   또는 universe 유입 때문에 이미 진행 중인 시장을 뒤늦게 발견하는 left censoring을 줄인다.
4. `end_date`는 Polymarket market clock이다. DB에서 많은 일반 스포츠 market은
   `end_date == game_start_time`이므로 결과를 “경기 종료 전 6시간”이라고 과장하지 않고
   “Gamma endDate 전 6시간(대부분 경기 시작 전)”이라고 표기한다.
5. Pomegranate 수집 envelope에 의해 누적 volume `>=2,000`, liquidity `>=10,000`이 이미
   적용돼 있다. 추가 사후 volume/liquidity 최적화는 하지 않는다.

## point-in-time 가격과 진입

- outcome 0 executable proxy: `ask=best_ask`, `bid=best_bid`
- outcome 1 executable proxy: `ask=1-best_bid`, `bid=1-best_ask`
- `0 < bid <= ask < 1`, spread `<=0.03`인 관측만 사용한다.
- 각 outcome/entry threshold마다 직전 5~30분 관측의 ask가 threshold 미만이고 현재 ask가
  threshold 이상인 첫 상향 교차만 진입 후보로 쓴다.
- 15분 polling에서 큰 jump를 threshold 체결로 오인하지 않도록 실제 관측 ask가
  `threshold + 0.01` 이하여야 한다.
- condition/outcome/entry threshold당 진입은 한 번뿐이다.

Gamma `best_bid`/`best_ask`는 전 시장에 대한 실행가격 proxy다. 같은 cycle에 회전 표본으로
해당 token의 CLOB snapshot이 있을 때 exact-book subset을 별도로 계산하되, 없는 exact quote를
Gamma 값으로 채우지 않는다.

## 고정 grid와 청산

- entry threshold: `0.75, 0.76, ..., 0.97` (1 cent 간격, 23개)
- target exit: 각 entry보다 최소 2 cent 높은 1-cent level부터 `0.99`까지
- resolution exit: target 없이 확인된 one-hot resolution의 `0` 또는 `1`까지 보유
- target 전략은 진입보다 뒤의 공개 관측에서 executable proxy bid가 target 이상일 때 최초
  관측 bid로 taker 청산한다.
- target이 관측되지 않으면 확인된 resolution 값으로 정산한다.
- target도 resolution도 cutoff 전에 없으면 right-censored로 남기고 손익을 만들지 않는다.
- 같은 signal을 여러 target counterfactual에 재사용하지만 event-cluster를 독립 단위로 둔다.

사용자가 명시한 anchor는 별도 표로 반드시 공개한다.

- `0.80 → 0.90`
- `0.85 → 0.95`
- `0.90 → resolution`
- `0.95 → resolution`
- 보조 anchor: `0.80/0.85/0.90/0.95 → resolution`

## 비용과 민감도

- 가상 진입 notional은 신호당 `$5`다.
- baseline은 모든 거래를 taker로 가정한다.
- market observation의 `fees_enabled=0`이면 fee 0, `1` 또는 null이면 보수적으로 sports
  taker rate `0.03`을 적용한다.
- per-share fee는 `0.03 × p × (1-p)`이며 target exit에는 진입·청산 양쪽에 적용한다.
- resolution redemption에는 별도 trading fee를 넣지 않는다.
- primary sensitivity는 진입 ask `+1 cent`, target 청산 bid `-1 cent`의 adverse execution이다.
- 보조 sensitivity는 양쪽 `2 cent` adverse다.
- holding reward, maker rebate, latency 중 가격 변화, queue fill은 0으로 둔다.

## 선택과 판정

Primary 선택은 앞 6일만 사용한다.

1. evaluable signal 50건 이상
2. independent event 30개 이상
3. resolution-dependent signal의 label coverage를 별도 공개
4. event-equal `fee + 1 cent adverse ROI`가 가장 높은 조합
5. 동률이면 더 많은 event, 더 낮은 entry, 더 낮은 target 순

선택된 단 하나의 조합을 뒤 6일 temporal validation에 적용한다. 다음을 모두 통과해야
“전향적 simulation 후보”로만 인정한다.

- evaluable signal `>=50`, independent event `>=30`
- event-equal fee-net ROI `>0`
- event-equal fee + 1-cent adverse ROI `>0`
- event-cluster bootstrap 95% ROI CI lower bound `>0`
- 최악 event loss와 target miss/resolution loss를 공개
- 동일 entry의 resolution-only보다 나쁜 결과를 숨기지 않음

전체 validation grid에서 사후 최고 조합도 탐색 결과로 공개하지만 primary 결론에는 사용하지
않는다. 23개 entry와 수백 target을 비교하므로 사후 최고값은 과최적화 가능성이 매우 높다.

월 5% 목표는 이 12일 자료만으로 검증하지 않는다. 신호당 ROI와 `$5` 손익은 계산하지만,
월간 계좌 수익률에는 동시 포지션 수, 자본 상한, 재투자, 미해결 보유기간이 추가로 필요하다.
이번 결과가 통과해도 accountless 5분 prospective simulation을 30일 수행한 뒤 판단한다.
