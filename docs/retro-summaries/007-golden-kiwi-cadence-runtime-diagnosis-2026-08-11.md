# 007 — Golden Kiwi cadence/runtime 진단 — 2026-08-11

작성일: 2026-08-11

Jenkins 관측: `2026-08-10T15:09:22Z` (`2026-08-11 00:09:22 KST`)

대상: `polybot-kiwi-a`, `polybot-kiwi-b`, `polybot-kiwi-c`, `polybot-kiwi-d`

범위: Jenkins config, 최근 성공 build 99개/job, 최신 completed console, Golden Kiwi
cadence/analyzer 계약 및 Polymarket 공식 API 제약의 read-only 진단

## 0. 결론

13~14분 build는 Jenkins 자체의 성공/실패 기준으로는 성공할 수 있지만 **Golden Kiwi
Micro-Cascade 실험에는 허용되지 않는다**.

1. 네 job 모두 최근 성공 build의 cycle runtime p95가 약 `9.8분`으로, 사전 등록한
   `p95 <= 5분` 계약을 위반한다.
2. 각 job은 `concurrent build=false`라 5분을 넘는 build 다음 trigger가 밀린다. 실제
   시작 간격은 최대 약 `14.57분`이 되었고 정규 offset 밖에서 시작한 SUCCESS run이
   이미 생겼다.
3. Golden Kiwi analyzer는 off-schedule 또는 duplicate-slot SUCCESS run이 하나라도
   있으면 그 run을 primary evidence에서 제외하고 전체 promotion을
   `NOT_EVALUABLE_FAIL_CLOSED`로 판정한다. 현재 `2026-08-06`~`2026-09-05` cohort는
   promotion 판정용으로 이미 cadence-invalid다.
4. 최신 completed cycle에서 네 job은 `00:01:43~44 KST`에 거의 동시에 시작해 각각
   같은 Gamma universe를 `267페이지`, `26,654개 raw market`으로 네 번 조회했다.
   같은 membership digest를 만들었고 각자 `snapshot_gap_too_long` 16건을 기록했다.
5. 단순히 cron을 10분/15분으로 늘리거나 snapshot gap을 완화하거나 concurrent build를
   켜면 안 된다. 이는 실험 가설과 사전 등록 계약을 바꾸거나 중복 DB writer를 만든다.
6. 네 timer를 일시 중지하고, 시장을 한 번만 수집해 네 arm이 같은 immutable snapshot을
   소비하는 구조로 바꾼 뒤 새 DB·새 30일 cohort로 재시작하는 것이 권고안이다.

## 1. Jenkins 구성

| Job | Arm | Trigger | Expected offset | Concurrent |
|---|---|---|---:|---|
| `polybot-kiwi-a` | A / 3 steps / +1%p | `0-59/5 * * * *` | 0 | false |
| `polybot-kiwi-b` | B / 3 steps / +2%p | `1-59/5 * * * *` | 1 | false |
| `polybot-kiwi-c` | C / 5 steps / +1%p | `2-59/5 * * * *` | 2 | false |
| `polybot-kiwi-d` | D / 5 steps / +2%p | `3-59/5 * * * *` | 3 | false |

네 job 모두 simulation-only이고 credential 환경변수를 명시적으로 unset한다. 각 shell은
`uv sync --frozen`, `polybot config`, `polybot run --simulate` 순서다. 최신 console에서
`uv sync`는 10~11ms라 병목이 아니다.

## 2. 최근 build runtime

Jenkins metadata에서 job별 최신 완료 SUCCESS 99개를 계산했다. 조회 시점에 각 job의 다음
build가 실행 중이어서 100개 요청 중 완료 표본은 99개였다.

| Job | Runtime p50 | Runtime p95 | Max | Runtime >5m | Start gap >10m | 정규 minute slot |
|---|---:|---:|---:|---:|---:|---:|
| Kiwi A | 1.75m | 9.80m | 14.56m | 14.1% | 4.1% | 90.9% |
| Kiwi B | 1.95m | 9.78m | 14.56m | 13.1% | 4.1% | 91.9% |
| Kiwi C | 2.39m | 9.78m | 14.57m | 13.1% | 4.1% | 89.9% |
| Kiwi D | 2.31m | 9.78m | 14.57m | 13.1% | 4.1% | 89.9% |

`2026-08-10T13:43:01Z` 이후 장시간 build는 네 job이 같은 시각에 시작하고 거의 같은
runtime을 보였다. 예를 들어 네 job 모두 약 `7.7m → 11.3m → 14.57m → 13.58m →
13.94m → 9.78m` 순서였다. 한 번 5분 경계를 넘은 뒤 각 job의 다음 trigger가 밀리고,
네 job이 같은 시각에 재시작하면서 동일한 대용량 수집을 동시에 반복하는 feedback loop다.

## 3. 최신 completed cycle

| 항목 | 관측값 |
|---|---|
| 네 arm run 시작 | `00:01:43~44 KST` |
| Gamma sweep 완료 | `00:06:51 KST` |
| RunAudit SUCCESS | `00:08:00 KST` |
| Sweep | arm별 267 pages, 26,654 raw, 17,107 qualified |
| Membership digest | 네 arm 모두 동일 `81d698438d7a...` |
| Persisted snapshot | arm별 463 |
| Signal exclusion | arm별 `snapshot_gap_too_long: 16` |
| Candidate | arm별 0 |

Gamma sweep만 약 5분 8초였고 총 cycle은 약 6분 16초였다. 코드상 각 cycle은
`scanner.fetch_markets()`로 complete market sweep을 한 뒤 snapshot 저장과 signal scan을
수행한다. 네 arm의 차이는 confirmation steps와 cumulative move뿐인데 source 수집까지
독립적으로 반복하고 있다.

Polymarket 공식 [`GET /markets/keyset`](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination)
페이지 크기는 최대 100이므로 현재 `limit=100`을 더 키울 수 없다. 공식
[Gamma rate limit](https://docs.polymarket.com/api-reference/rate-limits)은 `/markets`에
300 requests/10s다. 관측된 요청 속도만 보면 공개 한도 소진의 직접 증거는 없고, 네 개의
동일한 큰 response 다운로드·JSON 처리·저장을 동시에 수행하는 자원 경합이 더 유력하다.

## 4. 전략 evidence에 미치는 영향

Golden Kiwi의 frozen 계약은 다음을 요구한다.

- 5분 cadence
- snapshot gap 3~10분
- p95 cycle runtime 5분 이하
- arm별 minute offset 0/1/2/3
- concurrent build 비활성화
- off-schedule/duplicate-slot SUCCESS 0건

signal 코드는 snapshot gap이 10분을 넘으면 `snapshot_gap_too_long`으로 진입을 거절한다.
analyzer는 SUCCESS run의 시작 minute이 arm offset과 다르면 off-schedule로 제외하며, 네 arm
중 하나라도 cadence invalid이면 전체 promotion evidence를 fail-closed한다. 따라서 현재
데이터는 병목 진단이나 탐색적 분석에는 보존할 가치가 있지만 frozen promotion 판정에는
사용할 수 없다.

## 5. 권고안

### 즉시 운영

1. 네 Kiwi timer를 일시 중지한다. 현재 cohort는 더 실행해도 promotion validity가
   회복되지 않는다.
2. 현재 DB와 console log는 삭제하거나 덮어쓰지 않고 invalid cohort evidence로 보존한다.
3. concurrent build를 켜지 않고, gap 상한을 10분보다 크게 바꾸지 않는다.
4. cron만 10분/15분으로 늘리지 않는다. 3-step/5-step signal의 시간 horizon까지 바뀌어
   더 이상 같은 Micro-Cascade 실험이 아니다.

### 코드/수집 구조

권고 구조는 **one sweep, four deterministic evaluations**다.

1. 한 collector가 5분 source slot마다 Gamma universe를 한 번만 cursor-complete하게
   수집한다.
2. 결과를 slot, page receipt timestamp, cursor completeness, schema version, checksum과
   함께 immutable artifact로 atomic publish한다. stale/partial fallback은 금지한다.
3. A/B/C/D evaluator가 정확히 같은 artifact를 읽고 각자의 독립 DB에 snapshot, decision,
   follow-up과 source digest를 기록한다.
4. analyzer는 process 시작시각 대신 immutable source slot과 evaluator completion을 분리해
   검증한다.
5. single-collector p95가 5분 미만인지 Mac Mini에서 먼저 benchmark한다.
6. 새 구조·analyzer·preregistration hash·새 DB로 새로운 30일 UTC cohort를 시작한다.

추가 최적화 후보로 공식 문서가 complete active discovery에 권고하는
[`/events` pagination](https://docs.polymarket.com/market-data/fetching-markets)을 benchmark할
수 있다. 다만 기존 `/markets/keyset`과 condition ID·tag·point-in-time field가 완전히 같은지
parity audit를 통과하기 전에는 교체하면 안 된다.

## 6. 하지 말아야 할 임시 조치

- `concurrent build=true`: 같은 job/DB의 동시 writer와 duplicate slot을 만든다.
- `max_snapshot_gap_minutes=15`: 결측을 정상 관측처럼 바꾸고 frozen hypothesis를 훼손한다.
- trigger만 `*/10` 또는 `*/15`: confirmation horizon과 표본 정의가 달라진다.
- 네 job의 offset만 더 벌리기: 각 arm이 여전히 5분마다 full sweep해야 하므로 근본 해결이
  아니며 5분 안에 네 offset을 배치할 공간도 없다.
- 현재 DB에서 contract/window/offset 수정: immutable experiment contract가 시작을 거부하거나
  cohort lineage를 섞는다.
