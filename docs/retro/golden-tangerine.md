# Golden Tangerine 회고 계약

공통 증거 정의는 [EVIDENCE_CONTRACT.md](EVIDENCE_CONTRACT.md)를 따른다. 실행 시 아래 값을
요청 UTC half-open range와 동일하게 고정한다.

```text
REVIEW_START=2026-08-21T00:00:00Z
REVIEW_END=2026-09-20T00:00:00Z
```

Golden Tangerine은 2026-08-21부터 시작하는 sports-resolution low-notional live A/B다.
`polybot-orange` arm A `[0.94,0.95]`와 `polybot-fox` arm B `[0.92,0.93]`를 threshold 외 동일하게
유지한다. 24시간·7일에는 cadence, cursor, exact `$5` book, first-observation episode, FOK order,
confirmed fill/fee, DB 무결성만 점검한다.

성과 판정은 `[2026-08-21T00:00:00Z, 2026-09-20T00:00:00Z)` entry cohort와
`2026-10-20T00:00:00Z` follow-up이 끝난 뒤 verified DB 절대 경로를 arm별로 분리해 수행한다.
requested order나 settlement assumption을 realized SELL P&L로 바꾸지 않는다. exact fill/fee 또는
resolution coverage에 CRITICAL/HIGH gap이 있거나 표본이 부족하면 threshold 선택과 규모 확대를
중단한다. 수동 wallet position은 모집단과 청산 대상에서 제외한다.
