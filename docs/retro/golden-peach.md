# Golden Peach 회고 가이드

먼저 [Evidence Contract](EVIDENCE_CONTRACT.md)를 읽는다. 이 문서는 분석 절차이며 현재
수익성 판정이 아니다. 배포 전 Watermelon 재생은 직접 NO book과 actual fill이 없는 탐색
자료이므로 live cohort와 섞지 않는다.

## 분석 경계

```bash
export REVIEW_START=2026-08-30T00:00:00Z
export REVIEW_END=2026-09-13T00:00:00Z
export FOLLOWUP_END=2026-09-20T00:00:00Z
```

- 범위는 UTC half-open `[REVIEW_START, REVIEW_END)`다.
- Eco `peach-live-eco-3pp-1m-v1`, Fruit `peach-live-fruit-5pp-1m-v1`, Grey
  `peach-shadow-1m-v1`을 각각 독립 DB로 읽는다.
- `config_hash × strategy_source_digest × mode × job_name`이 다른 행은 별도 cohort다.
- Jenkins job 이름, 과거 Quince/Melon/Watermelon DB 또는 wallet 계정명으로 전략 epoch를
  추정하지 않는다.

## 증거 선행 검사

```bash
cd daily-rsync
uv run daily-rsync locate --job polybot-eco
uv run daily-rsync locate --job polybot-fruit
uv run daily-rsync locate --job polybot-grey
uv run daily-rsync verify --job polybot-eco --strategy golden-peach
uv run daily-rsync verify --job polybot-fruit --strategy golden-peach
uv run daily-rsync verify --job polybot-grey --strategy golden-peach

cd ..
uv run --project polybot-observability polybot-retro audit \
  --db <verified-eco-trades.db> \
  --db <verified-fruit-trades.db> \
  --days <exact-days> \
  --as-of <inclusive-end-date> \
  --output-dir <output-dir> \
  --strict
```

각 DB는 SHA-256, sync/source cutoff, `PRAGMA quick_check`, 최신 successful run, resolved config,
source digest와 preregistration hash를 기록한다. `CRITICAL`/`HIGH` 문제, BUY/SELL fill·fee 공백,
부분 체결 미대사 또는 cohort 혼합이 있으면 성과·파라미터 판단을 중단한다.

## 첫 24시간 수집·실행 건전성

1. Jenkins 1분 cadence의 기대 slot, 성공 run, 겹침/lock skip, p50/p95 runtime을 job별로 계산한다.
2. Gamma cursor completion, event membership digest, 허용 8개 대회 분류와 metadata drift를 본다.
3. source `live/ended/elapsed/period` coverage와 0~10분 entry gate를 점검한다. 예정 kickoff로
   source clock 누락을 보간하지 않는다.
4. event별 HOME/DRAW/AWAY triad와 직접 YES/NO 6개 token/book coverage, raw `book_json`, exact
   `$5` depth, spread와 leader margin을 확인한다.
5. entry episode의 `OBSERVED → QUEUED_NO_POST → SUBMISSION_IN_PROGRESS → TRADE_CREATED` 전이를
   보고, exact terminal zero-fill 외 event당 두 번째 BUY가 없는지 검사한다.
6. Eco/Fruit의 common config가 TP 하나 외에는 같은지 확인한다. Grey는 credential-free
   simulation이어야 한다.
7. DB·bot log·console log 증가량과 7/14/30일 저장공간을 추정한다.

첫 24시간에는 수익성, arm 승자, SL·TP 수정 또는 거래금액 확대를 판단하지 않는다.

## 종료·체결 검사

- TP와 SL은 confirmed BUY VWAP과 전체 sellable shares의 full-depth executable bid VWAP으로
  재계산한다. best bid만 통과한 경우를 성공으로 세지 않는다.
- source minute 80 이전 SL, 80분 이후 half-TP, 80분 이후 손실 hold-to-resolution 우선순위를
  재생한다. clock 누락 상태에서 제출된 late stop은 결함이다.
- accepted/DELAYED order는 fill이 아니다. `order_fills.status='CONFIRMED'`의 exact size, VWAP,
  fee와 signed SELL residual을 사용한다.
- SELL 실패는 해당 event에만 국소화되어야 한다. 다른 event가 전역 차단되었는지,
  연속 180분 뒤 `QUARANTINED`가 성공 매도나 `realized_pnl`로 위장되지 않았는지 검사한다.
- confirmed resolution은 선택한 직접 YES/NO token payout과 정확히 맞아야 한다.

## A/B 판정

paired unit은 같은 `event_id`다. Eco/Fruit 주문을 독립 경기 두 건으로 세지 않는다. 공통 event의
후보 시각, 직접 6-book, source minute가 비교 가능한지 먼저 검증하고 다음을 fee 포함 actual
execution 기준으로 비교한다.

- event-effective 표본수와 후보→제출→confirmed fill 감소율
- 평균·중앙값 순손익, 손익분기 승률, 큰 손실 한 건이 작은 익절 몇 건을 상쇄하는지
- TP/SL/late-half-target/resolution exit 비중과 full-depth slippage
- 리그, YES/NO, HOME/DRAW/AWAY, entry price, source minute별 결과
- 최대 낙폭, 동시 노출, capital lock, SELL 실패·격리율

두 arm 모두 손실이거나 한두 경기 꼬리에 결론이 좌우되면 기각한다. 표본이 부족하면 기간을
연장하되 중간에 threshold를 바꾸지 않는다. Grey raw six-book으로 다른 grid를 재생할 때도
live 기간을 parameter 선택과 검증에 동시에 쓰지 않는다.

## 과거 탐색 재현

```bash
uv run --project golden-peach python \
  golden-peach/scripts/replay_watermelon_kickoff_leader.py \
  --db <verified-watermelon-white-v4a.db> \
  --db <verified-watermelon-grey-v4a.db>
```

이 출력의 synthetic NO와 displayed-book 수익은 actual fill/P&L이 아니다. 근거와 checksum은
`golden-peach/research/frozen-2026-08-30-kickoff-leader-v1/HISTORICAL_REPLAY.md`를 따른다.
