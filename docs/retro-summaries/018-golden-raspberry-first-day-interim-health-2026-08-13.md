# 018 — Golden Raspberry 첫 24시간 전 중간 collection health — 2026-08-13

작성일: 2026-08-13

대상: Jenkins `polybot-do` / `polybot-re` / `polybot-mi`, strategy
`golden-raspberry`, runtime `raspberry-do-shard-0` / `raspberry-re-shard-1` /
`raspberry-mi-shard-2`

## 0. 결론

```text
Requested range: [2026-08-13T01:00:00Z, 2026-08-14T01:00:00Z)
Verified interim range: [2026-08-13T01:00:00Z, 2026-08-13T10:13:00Z)
Elapsed: 9h 13m / 24h (38.4%)
Decision: INTERIM_COLLECTION_HEALTH_PARTIAL_FAIL
Reason: polybot-mi cadence missing 1 / off-slot 1
Profitability: NOT EVALUATED
Parameter or threshold change: NONE
Code/Jenkins strategy deployment: NONE
```

요청한 24시간 종료점은 확인 시각보다 14시간 47분 뒤이므로, 이 문서는 완결된 24시간
판정이 아니라 세 shard가 모두 자기 마지막 예정 slot을 완료한 시각까지의 중간 판정이다.
`polybot-do`와 `polybot-re`는 모든 collection-health check를 통과했다. `polybot-mi`는
111개 예정 slot 중 110개가 정상 slot에 들어왔고, 한 build가 Jenkins executor 대기열에서
123.719초 기다리며 09:52 slot을 09:54:15에 시작해 `off-slot=0` frozen gate를 위반했다.

그 밖의 Gamma cursor, YES/NO pair, raw payload, run lifecycle, follow-up, opposite control,
cohort, DB 무결성과 저장공간 guard는 정상이다. neutral control은 0건이지만 수집·저장 버그가
아니라 현재 표본에서 frozen exact-match 조건을 모두 만족한 다른-event neutral이 없었던
결과다. threshold나 matching 조건은 바꾸지 않았다.

Analyzer의 top-level 문자열은 7일 미만이면 먼저 `HEALTH_ONLY_NOT_ENOUGH_DURATION`을
반환하므로, 이 범위에서는 그 문자열만 보면 안 된다. 실제 shard health에서 MI의
`no_off_slot_runs=false`를 확인해 이 문서의 판정을 `PARTIAL_FAIL`로 기록했다. frozen
analyzer를 관측 뒤 수정하면 source cohort가 바뀌므로 코드는 변경하지 않았다.

## 1. 동기화 evidence

세 job을 각각 `scan → plan → sync → verify`했고, 마지막 sync attempt와 마지막 successful
sync가 같은 run이다. local/remote SHA-256이 일치하며 failure, retention skip, open conflict는
모두 0이다.

| Jenkins | Runtime | Source cutoff UTC | Sync finished UTC | Verified DB SHA-256 | Verify |
|---|---|---|---|---|---:|
| `polybot-do` | `raspberry-do-shard-0` | `2026-08-13T10:10:13.644682Z` | `2026-08-13T10:12:23.725585Z` | `4e7aee83ebc0583bd7845f0b88c63ea0578ecca6fb259c0a203f707d22654481` | SUCCESS · 121 |
| `polybot-re` | `raspberry-re-shard-1` | `2026-08-13T10:11:13.516560Z` | `2026-08-13T10:12:29.784898Z` | `a3343f1d1274ab4049e90aa4297190ee1481c90de948306eeb910aa693eac89e` | SUCCESS · 122 |
| `polybot-mi` | `raspberry-mi-shard-2` | `2026-08-13T10:12:36.129657Z` | `2026-08-13T10:13:24.034839Z` | `16b8ec129eac00c2f5b961dd8f1ce84a092d58d17311fc25d022e453d9809b7f` | SUCCESS · 122 |

Verified DB 절대 경로:

- `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-do/strategies/golden-raspberry/runtime/raspberry-do-shard-0/databases/latest/trades_sim.db`
- `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-re/strategies/golden-raspberry/runtime/raspberry-re-shard-1/databases/latest/trades_sim.db`
- `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-mi/strategies/golden-raspberry/runtime/raspberry-mi-shard-2/databases/latest/trades_sim.db`

Offset-aware 분석 종료점 `10:13Z`는 DO 10:10, RE 10:11, MI 10:12 예정 slot이 각각
완료된 다음 분이다. 각 DB source cutoff는 자기 마지막 예정 slot 완료 이후다. 생성된 전체
analyzer JSON은 local-only
`docs/local/queue-echo-health-20260813T1013Z.json`에 두고 `.gitignore`로 제외했다.

## 2. Cadence와 Jenkins executor 지연

| Shard | Expected | Matched | Coverage | Missing | Off-slot | Duplicate |
|---|---:|---:|---:|---:|---:|---:|
| DO | 111 | 111 | 100.00% | 0 | 0 | 0 |
| RE | 111 | 111 | 100.00% | 0 | 0 | 0 |
| MI | 111 | 110 | 99.10% | 1 | 1 | 0 |

MI의 유일한 위반은 Jenkins `polybot-mi #116`이다.

- timer queue 진입 추정: `2026-08-13T09:52:11.373Z`
- 실제 build 시작: `2026-08-13T09:54:15.092Z`
- collector 시작: `2026-08-13T09:54:16Z`
- queue: 123.719초, build runtime: 19.726초
- 09:52 예정 slot 대비 collector 시작이 약 2분 16초 늦어 analyzer의 ±120초 허용폭을
  약 16초 넘겼다.

당시 built-in node의 executor는 16개였고 모두 점유됐다. Queen, Quince, Melon, Papaya,
Blueberry 등 여러 기존 build가 외부 API 지연 구간에서 12~22분 실행되면서 queue가
밀렸다. Raspberry 코드 runtime이나 DB lock이 원인은 아니다.

이후 MI 자연 build `#117`~`#120`의 queue 대기는 15.139 / 3.508 / 9.881 / 9.375초였고
모두 정상 slot에서 SUCCESS였다. 장애는 지속되지 않았다. 그러나 이미 append-only로 남은
off-slot 한 건은 지울 수 없고, frozen 7-day gate의 `off-slot=0`을 엄격히 적용하면 현재
window는 그대로는 최종 collection-health PASS가 될 수 없다.

전역 executor 증설은 다른 모든 전략의 동시성·API 부하를 바꾸고, Raspberry schedule 변경은
사전등록한 `0/1/2분` 계약을 바꾼다. 안전한 세-job 한정 수정이 아니므로 둘 다 수행하지
않았다. 새 confirmatory window를 시작하려면 먼저 Raspberry 전용 Jenkins capacity를
분리한 뒤 새 frozen window를 사전등록해야 한다.

## 3. Gamma, YES/NO pair와 raw evidence

| 지표 | DO shard | RE shard | MI shard |
|---|---:|---:|---:|
| Successful sweep | 111 | 111 | 111 |
| Expected / observed token | 1,284 / 1,284 | 322 / 322 | 1,340 / 1,340 |
| Pair token coverage | 100% | 100% | 100% |
| Same-request YES/NO pair | 100% | 100% | 100% |
| Raw payload linkage | 100% | 100% | 100% |
| Failed-run published sweep | 0 | 0 | 0 |
| HIGH / CRITICAL quality issue | 0 / 0 | 0 / 0 | 0 / 0 |

세 shard의 terminal cursor는 전부 complete다. Fleet에서 hash-selected condition은 28개이고
cross-shard condition overlap은 0이다. Runtime p95는 DO 8.110초, RE 8.261초, MI
8.220초이며 max는 각각 51.815 / 105.505 / 18.043초로 frozen 180/240초 gate 안이다.

## 4. Cohort와 decision lineage

범위 안에서 각 shard는 정확히 한 cohort만 사용한다.

| Runtime | Config hash | Strategy source digest |
|---|---|---|
| `raspberry-do-shard-0` | `41197d6e347b2476db9af3f83fd8b0df55f94766064da330bb5b0468b448bc11` | `7a65cef8118d353bfd604c1f5cf90ebb09c95149bca122d14bb55cf01c0cfb57` |
| `raspberry-re-shard-1` | `66ad110d8468e3bd04d44b6eee49726c4a83e2a68310b002159fa7d7ee5bce88` | `7a65cef8118d353bfd604c1f5cf90ebb09c95149bca122d14bb55cf01c0cfb57` |
| `raspberry-mi-shard-2` | `f8b136b7a19118439ad168a1fc539f45d9c2b3d1380939f59c7059633ac8edc9` | `7a65cef8118d353bfd604c1f5cf90ebb09c95149bca122d14bb55cf01c0cfb57` |

Shared preregistration, experiment window, source digest와 `queue-echo-v1` 계약은 모두
일치한다. 전체 DB에는 배포 전 config row도 보존되어 있지만 review range의 sweep에는 섞이지
않는다. 모든 run lifecycle은 `STARTED → SUCCEEDED`이고 failed/malformed lifecycle은 0이다.

범위의 qualified decision은 DO 25, RE 21, MI 20으로 총 66건이다. 66건 전부에서 다음을
확인했다.

- history snapshot 수 = confirmation steps
- 마지막 history snapshot = 현재 entry receipt
- 모든 history gap이 3~10분
- SIGNAL case 정확히 1개
- same-condition OPPOSITE case 정확히 1개

## 5. Follow-up과 control

### Follow-up

- SIGNAL case 66, OPPOSITE case 66, CONTROL case 0
- quote-complete: SIGNAL 58, OPPOSITE 58
- 아직 다음 due cycle 전인 pending: SIGNAL 8, OPPOSITE 8
- latest completed cycle 전에 이미 due였지만 attempt가 없는 case: 0
- duplicate first attempt: 0
- 60~75분 window 밖 attempt: 0
- `SOURCE_MISSING`, `INVALID_QUOTE`, `WINDOW_EXPIRED`: 0
- 실제 first attempt: entry 뒤 60.051~65.221분

Analyzer가 보여주는 arm별 raw outcome coverage 84.0% / 90.5% / 90.0%에는 아직 due가 오지
않은 마지막 8개 signal도 분모에 들어간다. 따라서 현재 follow-up collection missingness는
`due 58 / attempted 58 / quote-complete 58 = 100%`로 해석해야 한다. 경제적 return 값은 이
회고에서 읽거나 판정하지 않았다.

### Opposite와 neutral control

OPPOSITE는 qualified signal 66건 전부에 1:1로 생성되어 coverage 100%다. Neutral은
same-slot에서 총 206개의 neutral market-slot을 관측했지만 최종 CONTROL case는 0건이다.

Frozen matching funnel을 DB로 재구성한 결과는 다음과 같다. 숫자는 각 arm의 qualified
signal 중 해당 단계에 후보가 하나 이상 있던 건수다.

| Arm | Qualified | Other-event neutral | Prior-move match | Price-bin match | Horizon match | Depth ≤2x final |
|---|---:|---:|---:|---:|---:|---:|
| DO | 25 | 14 | 6 | 1 | 1 | 0 |
| RE | 21 | 11 | 10 | 1 | 1 | 0 |
| MI | 20 | 12 | 10 | 1 | 1 | 0 |

즉 neutral row가 저장되지 않았거나 matcher가 호출되지 않은 것이 아니다. 다른 event,
prior-15m move bin, 10pp price bin, horizon bin까지 맞는 후보는 있었지만 마지막 displayed
depth 2배 이내 조건을 통과하지 못했다. 이 조건을 완화하면 사후 parameter 변경이 되므로
수정하지 않고 더 수집한다.

## 6. DB 무결성

세 DB 모두 다음을 통과했다.

- daily-rsync checksum verify SUCCESS
- local/remote SHA-256 일치
- SQLite `quick_check=ok`, `integrity_check=ok`
- foreign key violation 0
- canonical/snapshot `journal_mode=delete`, WAL bytes 0
- snapshot manifest `quick_check=[ok]`
- online snapshot 전후 remote source fingerprint 동일
- local snapshot은 단일 self-contained DB

Preregistration manifest도 `(cd research/frozen-2026-08-13 && shasum -a 256 -c
MANIFEST.sha256)`에서 `PREREGISTRATION.md: OK`다.

## 7. 저장공간 증가량

`cycle_stats.db_bytes`의 범위 내 첫/마지막 값을 비교하면 세 DB는 9시간 13분 동안 합계
107,536,384 bytes, 즉 102.555MiB 증가했다. 현재 세 DB physical size 합계는
119,214,080 bytes(113.691MiB)다.

- 단순 시간당 증가율: 11.127MiB/h
- 단순 24시간 외삽: 약 267.1MiB/day
- 단순 30일 외삽: 약 7.82GiB/30d
- 현재 bot log 합계: 221,153 bytes
- 현재 Jenkins console 합계: 2,759,186 bytes; 14일 retention 적용
- Mac Mini 최신 free: 약 86.10GiB, used ratio 약 62.28%, guard `OK`
- MacBook local free: 약 246GiB

시장 수와 raw payload 크기가 변하므로 선형 외삽은 용량 계획용 추정치다. 현재 속도는
30일 수집에 충분하며, 이전 9~10GiB 추정보다 조금 낮다.

## 8. 변경과 다음 checkpoint

이번 작업에서 strategy code, threshold, control gate, follow-up timing, Jenkins job config는
변경하지 않았다. 전역 executor 경합은 확인했지만 다른 live job의 동시성에 영향을 주는
전역 mutation은 수행하지 않았다. 세 job은 장애 뒤 자연 build가 정상 slot으로 회복했고,
마지막으로 다시 sync/verify했다.

정확한 24시간 범위는 `2026-08-14 10:00 KST` 이후 다시 동기화해야 한다. 다만 현재 frozen
window에는 MI off-slot 1건이 이미 남아 있으므로, 24시간 보고서에서도 strict cadence 항목은
FAIL로 유지된다. 현재 자료는 instrumentation diagnostics와 exploratory collection에는 쓸 수
있지만, frozen `off-slot=0`을 요구하는 최종 confirmatory healthy cohort로는 사용할 수 없다.

다음 요청 예시:

> golden-raspberry 세 shard를 다시 동기화하고
> `[2026-08-13T01:00:00Z, 2026-08-14T01:00:00Z)` 정확한 24시간 범위를 재검증해줘.
> 수익성·파라미터는 보지 말고, 이미 확인된 polybot-mi #116 off-slot을 별도로 유지하면서
> 이후 cadence 재발 여부, due follow-up, neutral control, DB 무결성과 실제 24시간 저장공간
> 증가량만 판정해줘.
