# Golden Watermelon Live 회고 계약

공통 execution evidence 정의는 [EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md)를 따른다.

```text
REVIEW_START=2026-08-24T13:00:00Z
REVIEW_END=2026-08-31T13:00:00Z
FOLLOWUP_END=2026-09-07T13:00:00Z
```

`polybot-cat`은 exact `$5` ask VWAP `[0.98,0.999]`, `polybot-dog`은
`[0.99,0.999]`를 사용한다. threshold 외 계약은 동일하다. 분석 cohort는
`config_hash × strategy_source_digest × mode × job_name`으로 나누고 account/job 차이도
별도로 표시한다.

24시간 점검에서는 cadence, cursor completion, five-league identity, whole-match
HOME/DRAW/AWAY YES membership, exact `$5` ask depth, first episode, FOK submission,
order/fill/fee reconciliation, stop bid-depth evidence, DB integrity만 확인한다. 수익성이나
threshold 승자를 판단하지 않는다.

7일 entry 종료에는 다음을 arm별·league별로 기록한다.

- eligible unique event와 threshold-crossing event
- 한 event 한 entry 계약과 실제 FOK BUY/confirmed fill coverage
- `PENDING_BUY`, `HOLDING`, `PENDING_SELL`, `COMPLETED`, `RESOLVED`,
  `UNFILLED`, `QUARANTINED` 상태
- best-bid `0.70` trigger, full-depth executable VWAP, trigger-to-fill gap, zero-fill/depth 부족
- exact BUY/SELL fill size·VWAP·fee와 proven payout coverage
- manual wallet position 비편입 및 과거 Papaya DB epoch 분리

성과 판정은 follow-up cutoff까지 terminal evidence가 모인 뒤 수행한다. requested order,
accepted response, requested price/size, settlement assumption을 realized P&L로 바꾸지 않는다.
CRITICAL/HIGH gap, mixed cohort, fee 누락, unresolved open state, 표본 부족이 있으면 수익성·
scale-up 판단을 중단한다. 0.98/0.99는 선행 표본이 매우 작은 보수적 pilot이며 “최적값”으로
간주하지 않는다.

동기화 후 verified catalog DB 절대 경로만 audit에 넘긴다.

```bash
cd daily-rsync
uv run daily-rsync verify --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync verify --job polybot-dog --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-dog --strategy golden-watermelon-live

cd ..
uv run --project polybot-observability polybot-retro audit \
  --db <verified-cat-db> \
  --db <verified-dog-db> \
  --days 7 \
  --as-of 2026-08-31 \
  --output-dir <output-dir> \
  --strict
```
