# 010 — Golden Queen 저빈도 거래 진단 — 2026-08-12

작성일: 2026-08-12

대상: Jenkins `polybot-queen` / `queen-live-24h`, `polybot-king` /
`queen-live-12h`

## 0. 결론

```text
Decision: CHANGE_OPERATION / NO_PARAMETER_TUNING
Cadence: 두 job 모두 H/5 권장
선행 조건: CleanBeforeCheckout 제거 + 실제 TimerTrigger 활성화
Parameter: $100, 24h/12h, 0.90–0.94, 0.98/0.85, $25k/$2k 유지
Confidence: operational cause high / strategy profitability unknown
```

거래가 적은 주원인은 현재 parameter가 아니라 운영 상태다.

1. 두 Jenkins job 모두 description에만 `H/10 * * * *`이 있고 실제 TimerTrigger는 없다.
   마지막 실행은 2026-08-05이다.
2. 두 job 모두 현재 Git SCM `CleanBeforeCheckout`가 켜져 있다. 최신 build에서
   `git clean -fdx`가 live DB와 bot log를 다시 지웠다.
3. 현재 완화된 config는 지속 실행된 적이 없다. 보존 console에는 Queen 4회, King 3회의
   수동 run만 있고, 마지막 clean 뒤 동기화 DB에는 양쪽 모두 run 1개뿐이다.

Golden Queen은 persisted prior snapshot이 있어야 0.90 첫 상향 교차를 인정한다. 매 build
clean은 lineage를 매번 초기화하므로 threshold를 낮춰도 정상적인 후보 수집이 불가능하다.

## 1. Evidence boundary와 동기화

Timezone은 UTC이며 사람이 읽는 실행 시각만 KST를 병기한다.

### Queen 24h

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-queen/golden-queen/data/queen-live-24h/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-queen/strategies/golden-queen/runtime/queen-live-24h/databases/latest/trades.db`
- SHA-256: `8ac412d9ee4ccf9a07aa654651f21e32c64a9ccc4d67093bc6ca38e3c5f4e916`
- Latest sync: `3340540857f342afa4e9615a6200d636`, SUCCESS,
  `2026-08-12T11:45:55.650831Z`
- DB synced at: `2026-08-12T11:45:21.892460Z`
- Source cutoff/mtime: `2026-08-05T12:36:59.868402Z`
- Verify: SUCCESS, 1,715 checked, failure/retention skip/conflict 0

### King 12h

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-king/golden-queen/data/queen-live-12h/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-king/strategies/golden-queen/runtime/queen-live-12h/databases/latest/trades.db`
- SHA-256: `57899b5ec8b5312d36449c23b389da8fbbf5cdc405f735cd0baeae39b0ae4c5a`
- Latest sync: `80aebc606a3f47669fd91e65c1b034c6`, SUCCESS,
  `2026-08-12T11:47:00.072511Z`
- DB synced at: `2026-08-12T11:46:19.260194Z`
- Source cutoff/mtime: `2026-08-05T12:36:59.986918Z`
- Verify: SUCCESS, 1,714 checked, failure/retention skip/conflict 0

두 sync 모두 simulation·live runtime을 source/Jenkins job/runtime별로 분리했다. 성과 진단에는
canonical live `trades.db` 두 개만 사용했다.

## 2. Jenkins current state

2026-08-12T11:43:36Z 익명 config 재조회 결과:

| 항목 | Queen | King |
|---|---|---|
| Strategy/runtime | `golden-queen/queen-live-24h` | `golden-queen/queen-live-12h` |
| Mode | live | live |
| Config SHA-256 | `b8fadbaee369…` | `d8c6d725acfb…` |
| Description | `H/10 * * * *` | `H/10 * * * *` |
| Actual TimerTrigger | **없음** | **없음** |
| Concurrent build | false | false |
| Latest build | `#1701 SUCCESS` | `#1700 SUCCESS` |
| Latest build time | 2026-08-05 21:33 KST | 2026-08-05 21:33 KST |
| Current SCM cleanup | `CleanBeforeCheckout` | `CleanBeforeCheckout` |

description 문자열은 scheduler가 아니다. `Build periodically` trigger가 따로 있어야 한다.
또한 현재 cleanup extension은 checkout 전에 `git clean -fdx`를 실행하므로 untracked
SQLite와 bot log를 함께 삭제한다.

보안상 별도 blocker도 있다. 두 config에는 signer key가 inline assignment로 들어 있고,
config.xml은 익명 조회 가능하며 shell에는 첫 줄 shebang/`set +x`가 없다. 실제 값은 이
문서에 기록하지 않았다. 해당 signer는 노출 가능 상태로 취급해 교체하고 Jenkins
Credentials Binding으로 옮긴 뒤 live를 재개해야 한다.

## 3. 보존 console log 진단

raw console에서 secret과 임의 본문은 출력하지 않고, 구조화된 Queen run/candidate/cycle
marker만 집계했다.

| 지표 | Queen 24h | King 12h |
|---|---:|---:|
| First live run (KST) | 2026-07-24 22:08:54 | 2026-07-24 22:15:18 |
| Last live run (KST) | 2026-08-05 21:33:42 | 2026-08-05 21:33:28 |
| Live run | 1,698 | 1,695 |
| SUCCESS | 1,696 | 1,692 |
| Scanner-complete cycle | 1,696 | 1,692 |
| Candidate / BUY | **0 / 0** | **0 / 0** |
| Start gap p50 / p95 | 10.00m / 10.17m | 10.00m / 10.18m |
| Runtime p50 / p95 / max | 187s / 297.2s / 502s | 121s / 212.4s / 475s |

종전 config(`f324934ae3ae`, `1deff45922e7`)는 각각 1,694/1,692회 실행됐지만 후보가
0이었다. 이는 2026-08-05 clean restart 전에 문서화된 `$100k liquidity / $5k volume`
cohort다.

현재 config(`0e23378a894c`, `f37e520952ab`)는 `$100` 주문에서 다음 resolved 값이다.

```text
effective liquidity = max($5,000, $100 / 0.004) = $25,000
effective volume24h = max($1,000, $100 / 0.05) = $2,000
entry = prior < 0.90 → current [0.90, 0.94]
target / stop = 0.98 / 0.85
snapshot gap <= 15m
```

완화 후 보존 console은 Queen 4회, King 3회뿐이며 21:10~21:33 KST 약 23분에 불과하다.
마지막 build에서 다시 clean되어 현재 DB에는 다음만 남았다.

| DB | SUCCESS run | Cursor-complete sweep | Snapshot | Trade/order/fill |
|---|---:|---:|---:|---:|
| Queen | 1 | 1 | 70 | 0 / 0 / 0 |
| King | 1 | 1 | 70 | 0 / 0 / 0 |

따라서 완화한 gate가 여전히 엄격하다는 결론도, 12h/24h 중 어느 쪽이 낫다는 결론도 낼
수 없다.

## 4. Strict audit

1. `[2026-07-24T00:00Z, 2026-08-05T00:00Z)` 12일 audit는 HIGH 6으로 실패했다.
   clean 이후 DB에는 이 구간의 run/snapshot/trade가 모두 0이기 때문이다.
2. `[2026-08-05T00:00Z, 2026-08-06T00:00Z)` audit도 HIGH 8로 실패했다. source cutoff가
   12:37Z이고 DB당 run 1개뿐이라 window/five-minute coverage가 0.3%, 최대 schedule
   gap이 12.56시간이다. 이 두 번째 범위는 full-day source cutoff를 충족하지 않는
   진단용 audit이며 parameter 의사결정에는 사용하지 않았다.

Evidence Contract상 CRITICAL/HIGH가 남으면 threshold 조정·증액·성과 비교를 중단해야
한다. 이번 결론은 수익 판정이 아니라 운영 복구 판정이다.

## 5. 권장 재가동 순서

1. 노출 가능 signer key를 교체하고 두 job 모두 Jenkins Credentials Binding으로 옮긴다.
   shell 첫 줄부터 `#!/bin/bash`, `set +x`, `set -euo pipefail`을 적용한다.
2. Source Code Management → Git → Additional Behaviours에서
   **`Clean before checkout`을 제거**한다. 현재 DB는 이미 마지막 clean 뒤 1-run fresh
   상태이고 trade/open order가 0이므로 clean을 다시 실행할 이유가 없다.
3. 두 job 모두 Build Triggers → Build periodically를 실제로 켜고
   **`H/5 * * * *`**를 설정한다. 둘 중 하나만 5분으로 바꾸지 않는다.
4. `concurrentBuild=false`를 유지한다. 24h의 과거 p95는 297.2초로 5분에 가깝고 드물게
   넘으므로, 중첩 대신 Jenkins queue가 직렬화하게 한다.
5. strategy parameter는 그대로 둔다. 12h/24h만 다른 사전 등록 A/B를 유지하고
   `$100`, 현재 자동 파생 `$25k/$2k`, 0.90–0.94, 0.98/0.85를 함께 바꾸지 않는다.
6. 수동 off-cadence build 대신 timer가 자연스럽게 최소 2회 실행되게 한다. 15분 뒤
   재동기화해 각 DB의 `run_audits>=2`, DB reset 없음, start gap 약 5분, snapshot lineage
   증가를 확인한다.
7. 24시간 후 queue backlog, SUCCESS coverage, runtime p95, cursor-complete sweep,
   `prior_snapshot_missing/stale`, candidate funnel을 검사한다. p95가 지속적으로 5분을
   넘겨 queue가 누적되면 parameter를 완화하지 말고 universe 수집/runtime 구조부터
   최적화한다.
8. 7일은 운영/funnel 점검, 전략 판정은 최소 30일과 terminal event-effective n 30을
   모두 충족한 뒤 수행한다.

## 6. 왜 지금 parameter를 바꾸지 않는가

- 10분 cadence 자체는 과거 p95 10.18분으로 기존 `max_snapshot_gap=15m` 안이었다.
  5분은 좁은 0.90–0.94 band를 더 자주 관측하고 문서의 5분 archive 계약을 맞추기 위한
  개선이지만, 과거 무거래가 cadence 때문이었다는 반사실 증거는 없다.
- 종전 `$100k/$5k` metadata 병목은 이미 `$25k/$2k`로 완화했다. 이 변경은 연속
  23분조차 보존되지 않아 효과를 평가할 수 없다.
- `max_spread`·depth는 candidate가 나온 뒤 주문 가능성을 거르는 값이다. candidate가 0인
  현재 문제를 해결하는 첫 노브가 아니다.
- 0.90 하한, 0.94 상한, 12h/24h horizon을 지금 바꾸면 first-crossing 가설과 사전 등록
  A/B가 동시에 바뀐다.

다음 7일에 정상 archive가 쌓였는데도 candidate가 0이면 per-cycle entry rejection funnel과
immutable replay를 먼저 만든 뒤 한 축만 사전 등록한다. 현재 evidence로 추천할 추가 숫자는
없다.
