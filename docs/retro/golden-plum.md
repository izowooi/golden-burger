# Golden Plum 회고 가이드

먼저 [Evidence Contract](EVIDENCE_CONTRACT.md)를 읽는다. 이 문서는 분석 절차이며 현재
수익성 판정이나 증액 권고가 아니다. Golden Peach Grey의 과거 재생은 파라미터 탐색 자료이고,
배포 뒤 Golden Plum의 앞으로 수집하는 cohort와 합치지 않는다.

## 분석 경계

```bash
export REVIEW_START=2026-08-31T00:00:00Z
export REVIEW_END=2026-09-14T00:00:00Z
export FOLLOWUP_END=2026-09-21T00:00:00Z
```

- 범위는 UTC half-open `[REVIEW_START, REVIEW_END)`다.
- King `plum-live-king-90-1m-v1`, Queen `plum-live-queen-95-1m-v1`, Silver
  `plum-shadow-silver-1m-v1`을 각각 독립 DB로 읽는다.
- `config_hash × strategy_source_digest × mode × job_name`이 다른 행은 별도 cohort다.
- 과거 Queen/Quince, Peach/Watermelon DB나 Jenkins job 이름만으로 전략 epoch를 추정하지 않는다.

## 증거 선행 검사

```bash
cd daily-rsync
uv run daily-rsync locate --job polybot-king
uv run daily-rsync locate --job polybot-queen
uv run daily-rsync locate --job polybot-silver
uv run daily-rsync verify --job polybot-king --strategy golden-plum
uv run daily-rsync verify --job polybot-queen --strategy golden-plum
uv run daily-rsync verify --job polybot-silver --strategy golden-plum

cd ..
uv run --project polybot-observability polybot-retro audit \
  --db <verified-king-trades.db> \
  --db <verified-queen-trades.db> \
  --days <exact-days> \
  --as-of <inclusive-end-date> \
  --output-dir <output-dir> \
  --strict
```

각 DB의 절대 경로, SHA-256, sync/source cutoff, `PRAGMA quick_check`, 최신 successful run,
resolved config, source digest와 preregistration hash를 보고서 첫머리에 기록한다.
`CRITICAL`/`HIGH` 문제, BUY/SELL fill·fee 공백, 부분 체결 미대사 또는 cohort 혼합이 있으면
성과·파라미터 판단을 중단한다. Silver는 주문·체결·실현 손익 자료가 아니다.

## 첫 24시간 수집·실행 건전성

1. 1분 cadence의 기대 slot, 성공 run, 겹침/lock skip, p50/p95 runtime을 job별로 계산한다.
2. Gamma cursor completion, membership digest, 허용 8개 대회 분류와 metadata drift를 확인한다.
3. source `live/ended/elapsed/period`와 5~75분 coverage를 점검한다. 예정 kickoff로 시계를 보간하지 않는다.
4. event별 HOME/DRAW/AWAY triad와 직접 YES/NO 여섯 token/book, raw depth와 exact `$5` VWAP을 확인한다.
5. 같은 token의 정확한 3개 snapshot ID, 간격 ≤90초, 누적 +2%p, pullback ≤1%p와 첫 0.75 교차를 재계산한다.
6. live POST 직전의 source clock, 여섯 fresh book, 선두 identity, `[0.75,0.78]` 가격 재검증을 확인한다.
7. King/Queen의 resolved config가 TP 하나 외에는 같은지 확인하고 Silver credential이 없음을 검증한다.
8. DB·bot log·console log 증가량으로 7/14/30일 저장공간을 추정한다.

첫 24시간에는 수익성, arm 승자, SL·TP 수정 또는 거래금액 확대를 판단하지 않는다.

## 종료·체결 검사

- TP와 SL은 confirmed BUY VWAP과 전체 sellable shares의 full-depth executable bid VWAP으로 재계산한다.
- 우선순위 `target → entry−0.15 stop → source minute 80 exit`을 실제 경로로 재생한다.
- accepted/DELAYED 주문은 fill이 아니다. exact confirmed size·VWAP·fee와 signed residual을 사용한다.
- filled 또는 venue 도달 여부가 불확실한 BUY 뒤 같은 event의 두 번째 BUY가 없는지 확인한다.
- 한 event의 BUY/SELL 실패가 다른 event의 관리·진입을 전역 차단하지 않았는지 확인한다.
- 180분 뒤 `QUARANTINED`가 성공 매도·0 exposure·realized P&L로 위장되지 않았는지 검사한다.
- confirmed resolution은 선택한 직접 YES/NO token payout과 정확히 일치해야 한다.

## A/B 판정

paired unit은 같은 `event_id`다. King/Queen의 후보 시각·직접 여섯 book·source minute가 비교
가능한지 먼저 검증하고, fee 포함 actual execution으로 TP 0.90 대 0.95만 비교한다.

- common eligible event 20개 전에는 방향을 말하지 않는다.
- arm당 confirmed closed trade 50개와 common event 30개 전에는 증액하지 않는다.
- 평균·중앙값 순손익, 95% 신뢰구간, 큰 손실 한 건이 작은 익절 몇 건을 상쇄하는지 본다.
- target/stop/minute-80/resolution exit 비중, slippage, 리그·YES/NO·결과·진입 분별 결과를 본다.
- 두 arm 모두 손실이거나 신뢰구간이 0을 포함하면 우승 arm이 없는 것으로 판정한다.

Silver는 100경기 전 파라미터를 바꾸지 않는다. 이후에도 같은 기간을 파라미터 선택과 최종
검증에 동시에 쓰지 말고, 새 동결 기간으로 다시 검증한다.

## 과거 탐색 재현

```bash
uv run --project golden-plum python \
  golden-plum/scripts/replay_direct_six_book.py \
  --db <verified-peach-grey-trades_sim.db>
```

표시 호가 전체 깊이(displayed full-depth) 반사실이며 수수료·실제 주문·actual P&L이 아니다.
증거 경계와 checksum은
`golden-plum/research/frozen-2026-08-31-midgame-confirmation-v1/HISTORICAL_REPLAY.md`를 따른다.
