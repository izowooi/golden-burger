# Golden Black retro register

Evidence 해석은 [`EVIDENCE_CONTRACT.md`](EVIDENCE_CONTRACT.md)를 따른다.

- REVIEW_START: `2026-08-20T14:08:00Z`
- REVIEW_END: `2026-09-19T14:08:00Z` (entry window exclusive end)
- FOLLOWUP_END: `2026-10-19T14:08:00Z`
- FIRST_HEALTH_CHECK: `2026-08-22T10:00:00Z` (`2026-08-22 19:00 KST`)
- DATA_CONTRACT: `sports-resolution-paired-v1`
- STATUS: simulation preregistered; no live approval

첫 24시간에는 cadence, cursor, exact book, raw payload, cohort, DB integrity와 storage만 기록한다.
7일에는 표본/coverage만 추가하고, 수익성과 parameter를 판단하지 않는다. 30일 entry window와
follow-up이 끝난 뒤 verified immutable DB의 SHA-256, sync/source cutoff, config hash, source digest,
job/runtime을 고정해 arm과 `HOLD_TO_RESOLUTION/STOP_0.80/STOP_0.70/STOP_0.60` 정책별 회고를
작성한다. stop은 trigger bid와 실제 exit VWAP gap, partial/no-depth attempt, retry 횟수와 잔여
resolution payout을 모두 포함한다.
