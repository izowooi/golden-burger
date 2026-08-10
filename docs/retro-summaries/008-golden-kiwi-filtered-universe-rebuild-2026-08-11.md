# 008 — Golden Kiwi filtered-universe 재작성 — 2026-08-11

작성일: 2026-08-11

대상: `golden-kiwi`, Jenkins `polybot-kiwi-a`~`polybot-kiwi-d`

요청: 중단한 네 Kiwi job의 267 page·26,654 market 전수 조회를 서버측 거래량/유동성
filter로 1/5 이하로 줄이고, 기존 DB migration 없이 새 30일 실험을 같은 Jenkins shell
형태로 시작할 수 있게 코드·계약·문서를 다시 작성한다.

## 0. 결론

선택한 Gamma request envelope는 다음과 같다.

```text
liquidity_num_min = 20,000
volume_num_min = 10,000 (Gamma cumulative volume)
max pages = 53
max raw markets = 5,330
max sweep elapsed = 120 seconds
```

전략 entry gate의 liquidity $20,000, `volume24hr` $10,000은 바꾸지 않았다. 누적
`volume_num_min`과 `volume24hr`는 다른 필드이므로 scanner가 둘을 각각 검증한다.

## 1. API threshold 실측

기준 장애 envelope는 arm당 267 page·26,654 raw market이었다. 2026-08-11 Gamma API
point-in-time grid와 complete keyset sweep 결과:

| request liquidity | cumulative volume | markets | pages | 비고 |
|---:|---:|---:|---:|---|
| 1k | 10k | 4,646 | 약 47 | 1/5 이하지만 budget 여유가 작음 |
| 10k | 10k | 3,366 | 약 34 | 중간안 |
| 20k | 1 | 4,162 | 42 exact | strict entry 17 |
| **20k** | **10k** | **2,182** | **22 exact** | **선택, strict entry 17** |
| 50k | 10k | 1,153 | 약 12 | 지나치게 좁음 |

선택안은 기존 pages와 markets의 약 8.2%다. cumulative volume 1 기준과 비교한 strict
entry condition set은 17/17로 동일했고 누락은 0이었다.

같은 snapshot에서 전략의 `volume24hr` threshold를 올렸을 때 strict 후보 수는
10k/15k/20k/25k/50k에서 각각 17/15/13/12/8이었다. Primary B는 최소 quote-complete
50 signals가 필요하므로 이번에는 24시간 threshold를 올리지 않았다. 실행시간 문제는
request filter로 해결하고 신호 표본은 불필요하게 줄이지 않는 판단이다.

측정 원본은
`golden-kiwi/research/frozen-2026-08-13/GAMMA_FILTER_BENCHMARK.json`에 보존했다.

## 2. 코드 계약

- Gamma keyset 요청에 liquidity와 cumulative volume을 pagination 전에 전달한다.
- 53 page·5,330 raw market·120초 중 하나를 넘으면 `GammaSweepBudgetExceeded`로
  fail closed하며 partial attestation/snapshot을 저장하지 않는다.
- `market_sweeps` schema v2에 request filter, budgets와 monotonic elapsed를 저장한다.
- repository는 schema/filter/budget/elapsed가 frozen 값과 다르면 저장을 거부한다.
- analyzer v3는 canonical SUCCESS run마다 sweep이 정확히 하나인지와 schema,
  cursor completion, filters, budgets, actual counts/elapsed를 다시 검증한다.
- direct +60~75분 condition lookup은 main request filter와 독립으로 유지한다.
- 실험 contract schema는 v2, analyzer는 v3으로 올리고 window를
  `[2026-08-13T00:00:00Z, 2026-09-12T00:00:00Z)`로 고정했다.
- 기존 cadence-invalid DB와 새 cohort를 합치지 않는다.

새 preregistration SHA-256:

```text
65e33146e018ff9b01495af515fd059ba5be33de15758ad438584427ea02223c
```

## 3. Jenkins 실행

새 filter 환경변수는 필요 없다. 사용자가 제시한 shell의 arm, offset, exact window만으로
코드가 frozen request envelope를 자동 적용한다. A~D 전체 shell과 clean-build 순서는
`golden-kiwi/GOLDEN_KIWI_FILTERED_4_ARM_30_DAY_RUNBOOK_2026-08-13.md`에 기록했다.

중요 운영 순서:

1. 2026-08-13 00:00Z 전에는 `polybot config`까지만 확인한다.
2. 각 job에서 첫 실제 run에만 clean build를 사용한다.
3. 첫 성공 직후 clean 옵션을 끄고 기존 0/1/2/3 offset trigger를 켠다.
4. 24시간 뒤 cycle p95<5분, schedule/cadence, sweep budgets를 확인한다.
5. budget 초과 시 threshold/gap/cron을 즉석 완화하지 않고 네 timer를 다시 중단한다.

## 4. 검증 결과

- Golden Kiwi unit/contract+coverage test: 286 passed, total coverage 72%
- API filter benchmark: 선택안 22 pages / 2,182 raw markets / 23.601s
- strict point-in-time entry parity: 17 retained / 0 lost
- 변경 코드 live API smoke: 22 pages / 2,124 raw markets / 20.632s, cursor complete
- 임시 clean DB end-to-end cycle: SUCCESS, 22 pages / 2,124 raw markets,
  sweep 7.580s / total cycle 8.059s / snapshot 76
- A/B/C/D exact Jenkins env config: 모두 성공, arm/offset/window/schema v2/analyzer v3 및
  shared strategy source digest 확인
- `uv build`: sdist/wheel 성공
- 19개 strategy contract verifier: PASS
- frozen artifact checksum: preregistration·benchmark manifest에 고정

## 5. 007과의 관계

007은 broad universe를 그대로 유지한다는 가정에서 one-sweep/four-evaluator 구조를 가장
안전한 개선안으로 권고했다. 이번 요청은 공식 server-side filter를 실측해 각 arm의
request envelope 자체를 1/5 이하로 줄이는 방식을 명시적으로 선택했다. exact benchmark가
strict 후보 parity와 충분한 runtime 여유를 보였으므로 먼저 더 단순한 filtered per-arm
구조로 새 cohort를 실행한다. 운영 p95가 다시 5분을 넘으면 007의 shared collector 구조를
다음 단계로 재검토한다.

## 6. 한계

- API 측정은 point-in-time이며 30일 p95 보장이 아니다.
- 현재 후보 17개 보존은 미래 모든 시장 분포의 보장이 아니다.
- simulation top-of-book 결과는 actual fill/fee 수익이 아니다.
- 이번 변경은 live hard block을 해제하지 않는다.
