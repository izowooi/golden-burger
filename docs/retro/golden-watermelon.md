# Golden Watermelon review contract

Golden Watermelon은 actual order/fill/P&L이 아니라 두 cadence에서 관측한 displayed-book
counterfactual evidence다. 회고 전에 반드시 `docs/retro/EVIDENCE_CONTRACT.md`와
`golden-watermelon/research/frozen-2026-08-23/PREREGISTRATION.md`를 읽는다.

## Review boundary

- `REVIEW_START`: 두 Jenkins 잡 중 해당 DB의 첫 successful source run receipt time
- 첫 health 점검 `REVIEW_END`: `2026-08-24T10:00:00Z` exclusive
- entry cohort end: `2026-09-05T16:15:00Z` exclusive
- resolution follow-up end: `2026-09-19T16:15:00Z` exclusive
- timezone: UTC half-open `[REVIEW_START, REVIEW_END)`; 화면에는 KST를 병기한다.

배포가 preregistration boundary보다 늦었으므로 존재하지 않는 과거 표본을 채우지 않는다.
White와 Grey의 source cutoff는 각각 첫 성공 빌드 시각으로 기록하고, 공통 비교 구간은 더 늦은
cutoff부터 시작한다.

## Evidence discovery

```bash
cd daily-rsync
uv run daily-rsync scan --job polybot-white
uv run daily-rsync scan --job polybot-grey
uv run daily-rsync verify --job polybot-white --strategy golden-watermelon
uv run daily-rsync verify --job polybot-grey --strategy golden-watermelon
uv run daily-rsync locate --job polybot-white --strategy golden-watermelon
uv run daily-rsync locate --job polybot-grey --strategy golden-watermelon
```

두 job 모두 latest sync와 latest successful sync가 `SUCCESS`이고 DB artifact가 `SYNCED`이며
`verify`가 성공해야 한다. 보고서 첫머리에 remote path, verified local absolute path,
SHA-256, source cutoff, sync cutoff를 각각 적는다. 그 경로만 다음 analyzer에 반복 전달한다.

```bash
cd ../golden-watermelon
uv run polybot analyze --simulate --job watermelon-white-1m \
  --db /absolute/white/trades_sim.db \
  --db /absolute/grey/trades_sim.db \
  --output /tmp/golden-watermelon-review.json
```

## First health review only

첫 점검에서는 수익성, 우수 entry X, 우수 stop Y, live 승격을 판단하지 않는다. 다음만 확인한다.

1. White 1분과 Grey 5분 natural cadence, queue overlap, runtime p95
2. Gamma terminal cursor와 동일 request envelope
3. strict whole-match classifier 및 제외 사유 분포
4. eligible outcome CLOB attempt, exact `$5` full-depth book coverage
5. entry crossing provenance, path, stop partial/retry/gap 계측
6. one-hot resolution attempt/coverage(아직 미해결이면 정상 censoring)
7. `config_hash × strategy_source_digest × mode × job_name` cohort
8. SQLite quick check, foreign key check, DQ issue, storage 증가량과 외장 여유 공간
9. `condition_id × token_id × entry_threshold` cadence-paired coverage

White와 Grey의 같은 episode는 두 독립 표본이 아니다. actual fill 또는 realized P&L이라는 표현을
사용하지 않는다. health 실패는 계측/runtime을 복구할 사유이지 universe나 threshold를 바꿀
근거가 아니다. 수익성 판정은 frozen entry와 resolution follow-up window 및 preregistration의
confirmatory gate를 모두 충족한 뒤 별도로 수행한다.
