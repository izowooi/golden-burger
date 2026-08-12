# 012 — Papaya·Queen Clean 제거와 정상 평가 기준일 — 2026-08-12

작성일: 2026-08-12

대상: `polybot-cat`, `polybot-dog`, `polybot-queen`, `polybot-king`

## 결론

```text
네 잡 정상 평가 시작일: 2026-08-12
Cat/Dog CleanBeforeCheckout: 제거 확인
Cat/Dog 실제 timer: H/10 확인
Queen/King CleanBeforeCheckout: 제거 확인
Queen/King 실제 timer: H/5 확인
Jenkins 수정: 수행하지 않음
```

과거 최초 live 시작일은 역사적 evidence로 유지하지만, clean 옵션으로 persisted DB와
snapshot lineage가 반복 초기화됐으므로 향후 정상 거래·성과 평가는 2026-08-12부터 시작한다.

| Jenkins job | 전략 / runtime | 정상 평가 시작 (KST) | UTC | 기준 build |
|---|---|---|---|---|
| `polybot-king` | Queen 12h / `queen-live-12h` | 2026-08-12 21:02:01 | `2026-08-12T12:02:01Z` | 수동 `#1701 SUCCESS` |
| `polybot-queen` | Queen 24h / `queen-live-24h` | 2026-08-12 21:02:07 | `2026-08-12T12:02:07Z` | 수동 `#1702 SUCCESS` |
| `polybot-cat` | Papaya 24h / `papaya` | 2026-08-12 21:17:10 | `2026-08-12T12:17:10Z` | 수동 `#3444 SUCCESS` |
| `polybot-dog` | Papaya 72h / `papaya` | 2026-08-12 21:17:26 | `2026-08-12T12:17:26Z` | 수동 `#3336 SUCCESS` |

## Cat / Dog 재검증

2026-08-12 21:22–21:24 KST에 `$inspect-jenkins-job`으로 read-only 재조회했다.

- 두 config 모두 SCM cleanup extension과 shell cleanup command가 없다.
- 실제 `TimerTrigger`는 둘 다 `H/10 * * * *`이다.
- 둘 다 live `golden-papaya/papaya`, lifecycle `active`, `concurrentBuild=false`다.
- Jenkins config SHA-256은 Cat `b9eb2f5cc9ce…`, Dog `e6b64d7f8a78…`다.
- 수동 재가동은 Cat `#3444` 143.7초, Dog `#3336` 128.3초로 성공했다.
- 양쪽 첫 cycle은 snapshot 100개를 저장했고 `RUN_AUDIT SUCCESS`, candidate/BUY 0이었다.
  이 한 cycle 결과로 gate나 수익성을 평가하지 않는다.
- Cat의 첫 자연 timer `#3445`도 245.0초로 성공했다. 첫 cycle의
  `prior_snapshot_missing: 23`이 사라져 이전 snapshot이 보존된 사실까지 확인했다.

Queen/King의 clean 제거, H/5, 수동 1회와 자연 timer 2회 성공, snapshot lineage 지속은
`011-jenkins-clean-and-fleet-config-audit-2026-08-12.md`에서 확인했다.

## 분석 경계

- 날짜 메모에는 네 전략 모두 **2026-08-12 시작**으로 표시한다.
- 정밀 분석에서는 위 표의 첫 정상 live run UTC 시각을 half-open range 시작으로 사용한다.
- config hash가 이전 마지막 clean build와 같을 수 있으므로 timestamp 경계도 반드시 함께 쓴다.
- 7일 운영 점검은 2026-08-19 이후, 30일 평가는 2026-09-11 각 시작 시각 이후에 수행한다.
  충분한 terminal event-effective 표본이 없으면 날짜가 지났더라도 수익 판정을 미룬다.
- 이번 기록을 위해 `daily-rsync`는 실행하지 않았다. 향후 회고 때 해당 범위를 포함해
  동기화·verify한다.

## 운영자 보안 결정

Jenkins가 로컬/LAN에서만 사용된다는 이유로 inline secret 관련 finding은 운영자가
accepted risk로 두기로 했다. 익명 config와 inline secret 조합이라는 기술적 finding 자체는
유지하지만 이번 재가동의 blocker로 사용하지 않는다. 네트워크 접근 범위가 달라지면 다시
검토한다. 실제 secret 값은 어떤 문서에도 기록하지 않았다.
