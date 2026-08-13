# 017 — Golden Raspberry Queue Echo 연구 수집기 배포 — 2026-08-13

작성일: 2026-08-13

대상: Jenkins `polybot-do` / `polybot-re` / `polybot-mi`, strategy
`golden-raspberry`, runtime `raspberry-do-shard-0` / `raspberry-re-shard-1` /
`raspberry-mi-shard-2`

## 0. 결론

```text
Decision: COLLECTION_STARTED / HEALTHY_PREFLIGHT / PROFITABILITY_NOT_EVALUABLE
Hypothesis: persistent YES/NO displayed-depth imbalance predicts +60m $5 ask-to-bid return
Primary: MI = 같은 방향 3회 지속; DO/RE는 1/2회 sensitivity
Jenkins topology: 3개 experimental arm이 아니라 deterministic source hash shard
Cadence: 0-59/5, 1-59/5, 2-59/5 * * * *
Safety: accountless + simulate + archive_only; --live와 credential 환경변수 source-level 차단
Frozen window: [2026-08-13T01:00:00Z, 2026-09-12T01:00:00Z)
Deployment: d99c011 → snapshot compatibility fix 9b78892
Final source digest: 7a65cef8118d353bfd604c1f5cf90ebb09c95149bca122d14bb55cf01c0cfb57
Preflight verdict: HEALTH_ONLY_NOT_ENOUGH_DURATION
```

세 Jenkins job 모두 실제 public Gamma/CLOB 데이터를 수집하고 append-only SQLite와 bot
log를 남기며, 5분마다 서로 1분씩 어긋나 실행된다. timer를 넣기 전 수동 build와 timer
활성화 후 복수 자연 build가 모두 성공했다. 최종 배포 commit의 첫 세 자연 slot을
`daily-rsync`로 동기화한 DB로 분석했을 때 cadence, cursor, pair, raw linkage, shard 분리,
cohort와 DB 무결성 gate가 모두 통과했다.

이 판정은 **수집 시스템이 정상이라는 뜻**이지 전략이 수익을 낸다는 뜻이 아니다.
24시간은 instrumentation health, 7일은 계속 수집할 가치가 있는지 판단하는 collection
health checkpoint다. 사전 등록한 수익 가설 판정은 최종 단일 cohort 30일, MI quote-complete
50건, event 30개, 20 UTC day 등 전체 gate가 충족돼야 가능하다.

## 1. 가설과 검증 설계

표준 이진 Polymarket에서 YES와 NO order book의 3-tick 가중 displayed depth가 같은
방향으로 여러 5분 snapshot에 걸쳐 지속되면, 마지막 관측에 `$5`를 displayed ask에서
매수하고 60분 뒤 displayed bid에서 전량 매도한 counterfactual return이 비용 stress 뒤에도
양수인지 검정한다.

- `DO`: 현재 snapshot 한 번의 방향 일치
- `RE`: 같은 방향 두 번 지속
- `MI`: 같은 방향 세 번 지속, 사전 등록 primary
- base cost stress: 10.4bps
- severe taker stress: 72.5bps
- follow-up: entry +60분부터 +75분 사이 첫 독립 request 정확히 한 번
- controls: same-slot neutral + same-condition opposite outcome
- 결과: displayed-book counterfactual이며 fill, realized P&L 또는 queue position 증거가 아님

세 Jenkins 이름을 DO/RE/MI arm으로 쓰면 request 시각과 missingness가 처치 차이로 섞인다.
그래서 각 job은 `sha256(condition_id) mod 3`으로 시장을 분할하는 source shard이고, 각 DB가
자기 raw stream에서 DO·RE·MI를 모두 계산한다. 분석에는 항상 세 DB를 같이 넘긴다.

고정 계약은 `golden-raspberry/research/frozen-2026-08-13/PREREGISTRATION.md`와
`MANIFEST.sha256`에 보존했다. 관측 결과를 보고 threshold, primary, follow-up window나
control을 바꾸지 않는다.

## 2. 수집 universe와 evidence

매 cycle은 Gamma keyset endpoint를 terminal cursor까지 읽고 다음 조건을 적용한다.

- liquidity ≥ `$20,000`, total volume ≥ `$10,000`, 24h volume ≥ `$2,000`
- exact `Yes/No`, `negRisk=false`, active/orderbook/accepting orders
- 두 outcome price 모두 `[0.20, 0.80]`
- 종료까지 6시간 이상 90일 이하
- event당 hash가 가장 작은 market 하나

선택된 시장의 YES/NO book은 같은 CLOB `/books` request에 atomic pair로 넣는다. raw Gamma
membership와 CLOB response는 gzip + SHA-256으로 보존하며, token missing/error를 depth 0으로
채우지 않는다. DB는 config/source digest, run lifecycle, API receipt, raw payload,
market/snapshot/level, DO·RE·MI decision, case/follow-up/censoring과 storage metric을 append-only로
남긴다.

## 3. 프로젝트와 안전 경계

신규 프로젝트: `golden-raspberry/`

- CLI: `polybot config/run/status/health`
- public client: Gamma keyset + CLOB books
- DB: `data/<runtime>/trades_sim.db`
- log: `data/<runtime>/logs/YYYYMMDD.log`
- analyzer: `scripts/analyze_experiment.py`
- 실주문 SDK, wallet, position, order, fill, realized P&L 경로 없음
- `--live` 즉시 거부
- private key/funder/signature/API credential 9개 중 하나라도 존재하면 DB/log/HTTP session 전 실패
- filesystem free 30GiB 미만 또는 사용률 90% 이상이면 network 전 STOP

SQLite는 short-lived single writer와 `.raspberry.lock`에 맞춰 rollback `DELETE` journal을
사용한다. 이는 process가 종료된 WAL DB에서 read-only backup이 shared-memory sidecar를 만들지
못하는 macOS 문제를 피하면서 `daily-rsync`가 원본을 `mode=ro`로만 열게 한다.

## 4. Jenkins 최종 구성

| Job | Runtime | Shard | TimerTrigger | Config SHA-256 | 최종 배포 build |
|---|---|---:|---|---|---|
| `polybot-do` | `raspberry-do-shard-0` | 0/3 | `0-59/5 * * * *` | `d39d1c5d60c…` | `#4 SUCCESS` |
| `polybot-re` | `raspberry-re-shard-1` | 1/3 | `1-59/5 * * * *` | `ad2265eb3366…` | `#5 SUCCESS` |
| `polybot-mi` | `raspberry-mi-shard-2` | 2/3 | `2-59/5 * * * *` | `6523da132916…` | `#5 SUCCESS` |

공통 설정:

- `disabled=false`, `buildable=true`, `concurrentBuild=false`
- `LogRotator(daysToKeep=14, numToKeep=-1)`
- Git `main`, canonical golden-burger remote
- clean/wipe 없음
- credentials 9개 명시적 `unset`
- `POLYBOT_LIFECYCLE_MODE=archive_only`, `POLYBOT_SIMULATION_MODE=true`
- frozen start/end와 shard count/index/offset 고정
- `uv sync --frozen`, preregistration manifest checksum, config/run/status/health 순서

배포 순서는 timer 없는 상태의 수동 #1 세 건 성공 → timer 추가 → 자연 실행 관찰 순서였다.
수동 #1은 5.0~13.4초, 이후 확인한 자연 build는 모두 `Started by timer`, SUCCESS였고 같은 job
중첩이나 queue 누적이 없었다.

최종 `9b78892` 자연 build의 collector runtime은 DO 9.190초, RE 1.461초, MI 1.193초다.
모두 5분 주기보다 충분히 짧다.

## 5. 첫 실제 수집과 preflight 분석

최종 단일 source cohort의 첫 자연 slot 범위는
`[2026-08-13T00:35:00Z, 2026-08-13T00:38:00Z)`다. confirmatory window 전 배포 smoke이므로
경제적 outcome에는 포함하지 않는다.

| 지표 | DO shard | RE shard | MI shard |
|---|---:|---:|---:|
| Expected/matched cadence slot | 1/1 | 1/1 | 1/1 |
| Off-slot / duplicate / missing | 0/0/0 | 0/0/0 | 0/0/0 |
| Shard market | 5 | 2 | 7 |
| Expected/observed token | 10/10 | 4/4 | 14/14 |
| Pair/raw linkage | 100%/100% | 100%/100% | 100%/100% |
| Runtime | 9.190s | 1.461s | 1.193s |
| Quality HIGH/CRITICAL | 0/0 | 0/0 | 0/0 |
| Health | PASS | PASS | PASS |

Fleet 검사:

- all three shards: PASS
- shared preregistration/window/source digest: PASS
- `queue-echo-v1`: PASS
- selected condition 14개, cross-shard overlap 0
- Gamma terminal cursor 3/3
- failed run이 publish한 sweep 0
- DB quick check 3/3 `ok`
- verdict: `HEALTH_ONLY_NOT_ENOUGH_DURATION`

signal case가 0인 것은 이 범위가 frozen experiment start `01:00Z` 전이라
`experiment_window_eligible=false`인 의도된 결과다. 이를 근거로 threshold를 완화하지 않는다.

### 첫 confirmatory cycle

frozen start 뒤 정확한 첫 범위 `[2026-08-13T01:00:00Z, 2026-08-13T01:03:00Z)`도
자연 timer로 수집하고 다시 동기화했다.

| Job / build | Runtime | Shard market | Token coverage | Qualified signal case | Health |
|---|---:|---:|---:|---:|---|
| DO `#9 SUCCESS` | 1.536s | 4 | 8/8 | DO 0 / RE 0 / MI 0 | PASS |
| RE `#10 SUCCESS` | 9.277s | 2 | 4/4 | DO 2 / RE 1 / MI 1 | PASS |
| MI `#10 SUCCESS` | 1.287s | 6 | 12/12 | DO 1 / RE 1 / MI 1 | PASS |

- selected condition 12개, cross-shard overlap 0
- cadence 3/3, off-slot/duplicate/missing 0
- terminal cursor 3/3, same-request pair와 raw payload linkage 100%
- quality issue 0, common source digest `7a65cef8118d…`, shard별 single cohort
- fleet signal은 DO 3 / RE 2 / MI 2건이며, SIGNAL 7건과 OPPOSITE 7건의 follow-up case를 생성
- 첫 3분에는 strict neutral match가 0건이다. 표본 부족 상태이므로 gate를 완화하지 않고
  24시간 checkpoint에서 neutral missingness를 먼저 판정한다.
- follow-up target은 RE shard `02:01:21Z`, MI shard `02:02:13Z`; cadence상 첫 due cycle은
  대략 `02:06Z` / `02:07Z`이며 각 case의 window end는 `02:16:21Z` / `02:17:13Z`다.

MI 두 entry는 각각 `01:01:21Z`와 `01:02:13Z`로 window 안이다. 앞의 두 history snapshot은
`00:51/00:56Z`, `00:52/00:57Z`의 warm-up이지만 모두 final source digest와 정상
`STARTED→SUCCEEDED` run이다. frozen 계약의 “entry는 현재 receipt, MI는 backdate하지 않음”과
일치하고 미래정보·다른 cohort를 사용하지 않는다.

## 6. Daily Rsync 결함 발견과 수정

초기 sync에서 bot/Jenkins log는 전송됐지만 DB online snapshot은
`sqlite3.OperationalError: unable to open database file`로 실패했다.

원인은 두 단계였다.

1. Raspberry DB header는 WAL이지만 short-lived writer 종료 뒤 `-wal/-shm`이 없었다.
   SQLite `mode=ro` 연결은 생성이 필요한 shared memory를 만들 수 없어 backup transaction이
   실패했다.
2. live WAL source를 backup한 private destination은 WAL mode와 빈 `-shm`을 남길 수 있었다.
   실제 전송 계약은 `snapshot.db` 한 파일만 이동하므로 snapshot을 self-contained file로
   정규화해야 했다.

해결:

- Raspberry canonical DB를 single-writer `journal_mode=DELETE`로 변경
- daily-rsync private destination은 backup 직후 `journal_mode=DELETE`로 변환
- destination 연결 종료 뒤 staging-only WAL/SHM을 제거하고 read-only quick check 재실행
- 원본 source connection은 계속 `mode=ro`; `mode=rw + query_only` fallback은 원본 main file을
  checkpoint할 수 있어 테스트 후 폐기
- active WAL source의 원본 바이트/mtime 불변과 self-contained snapshot을 회귀 테스트로 고정

검증은 Raspberry 23 tests, daily-rsync 전체 test suite, 20-strategy contract, package build와
frozen manifest 모두 통과했다.

## 7. 최종 동기화 evidence

세 match 모두 latest sync attempt와 latest successful sync가 같은 `SUCCESS` run이고,
`analysis_ready=true`, local/remote SHA 일치, retention skip·failure·open conflict 0이다.

| Job | Runtime | Sync finished UTC | Source cutoff UTC | DB SHA-256 | Verify |
|---|---|---|---|---|---|
| `polybot-do` | `raspberry-do-shard-0` | `2026-08-13T01:02:53Z` | `2026-08-13T01:00:14Z` | `0836d72bb9d066cba1eb7665e4e69ce637ecd50d5e5c8a85c7b088e6feb39753` | SUCCESS · 11 |
| `polybot-re` | `raspberry-re-shard-1` | `2026-08-13T01:03:03Z` | `2026-08-13T01:01:21Z` | `87c9be691414164ca5036422abfac03ede85356c9aa2176795d5ebbd8e45f50e` | SUCCESS · 12 |
| `polybot-mi` | `raspberry-mi-shard-2` | `2026-08-13T01:03:13Z` | `2026-08-13T01:02:13Z` | `69d005847f169b813ab16c2c747388f4055601e0cb0a78402fea6c47e5d5c2f5` | SUCCESS · 12 |

첫 confirmatory sync의 세 DB 합계는 약 11.2MiB다. cycle 1→최종 관측의 초기 증가율은
fleet 5분당 약 1.1MiB,
단순 외삽 약 0.3GiB/day, 9~10GiB/30일이다. 표본이 9~10 cycle뿐이고 시장 수가 변하므로
24시간 checkpoint에서 실제 증가량으로 다시 계산한다. 현재 양쪽 disk guard는 통과한다.

## 8. 24시간 뒤 요청 문장

`2026-08-14 10:00 KST` 이후 다음 문장을 그대로 요청한다.

> polybot-do, polybot-re, polybot-mi를 daily-rsync로 다시 동기화하고,
> golden-raspberry Queue Echo의 정확한 범위
> `[2026-08-13T01:00:00Z, 2026-08-14T01:00:00Z)` 첫 24시간 collection health를
> 검증해줘. 수익성 판정이나 파라미터 튜닝은 하지 말고, 세 hash shard의
> cadence/off-slot/duplicate, Gamma terminal cursor, YES/NO same-request pair와 raw payload
> coverage, 단일 config/source cohort, DO/RE/MI decision lineage, +60~75분 첫 follow-up과
> neutral/opposite control missingness, DB quick_check, journal mode, 저장공간 증가량을
> 확인해줘. HIGH/CRITICAL evidence gap이 있으면 원인을 고치고 Jenkins 재배포·자연 실행·
> 재동기화 검증까지 해줘.

24시간에는 수익성 숫자가 보여도 전략을 선택하거나 threshold를 바꾸지 않는다.

## 9. 7일 뒤 요청 문장

`2026-08-20 10:00 KST` 이후 다음 문장을 요청한다.

> golden-raspberry의 polybot-do/re/mi를 daily-rsync로 동기화하고 정확한 범위
> `[2026-08-13T01:00:00Z, 2026-08-20T01:00:00Z)`의 7×24시간 collection health gate를
> 실행해줘. 세 DB를 하나의 fleet raw event stream으로 합치되 source shard 경계와
> config_hash × strategy_source_digest × mode × job_name cohort를 보존해줘. cadence 95%,
> pair/outcome/control coverage, runtime p95/max, follow-up censoring, cross-shard overlap,
> disk growth를 판정하고 30일까지 계속 수집할지 권고해줘. 경제적 결과는 preliminary로만
> 표시하고 MI 승격, DO/RE 사후 winner 선택, threshold 변경은 하지 마.

## 10. 30일 판정 경계

수익 가설 판정은 `2026-09-12T01:00:00Z` 이후 다음 조건을 모두 요구한다.

- 최종 단일 healthy cohort 30일
- MI quote-complete signal ≥50
- event cluster ≥30
- distinct UTC day ≥20
- raw/10.4bps/72.5bps event-cluster familywise 98.33% lower bound 모두 >0
- outcome coverage ≥90%, neutral match ≥80%
- signal−neutral clustered 95% lower bound >0
- early/late severe-stress mean 모두 >0
- 같은 episode의 MI−DO severe-stress clustered 95% lower bound >0

통과해도 verdict는 `SHADOW_REVIEW_ONLY`다. live 전환은 wallet risk·실체결/fee evidence·별도
사전등록을 가진 새 live-capable 프로젝트로 검증한다.

## 11. 보안과 운영 제한

- 세 Jenkins job은 plaintext LAN HTTP에서 anonymous config read가 가능하다.
- 이번 작업에서는 anonymous build와 config save도 가능했으므로 LAN 내부 사용자 누구나
  job 실행·변경을 할 수 있는 상태다. accountless collector에는 secret이 없지만 Jenkins
  전체 권한 경계로는 HIGH risk다.
- 사용자 요청 범위는 세 research job 배포였으므로 global security realm/authorization은
  변경하지 않았다. 추후 익명은 read-only로 제한하고 Configure/Build는 인증 계정으로
  분리하는 것이 권고된다.
- 임시 rollback XML은 최종 자연 build·sync·verify 뒤 macOS 휴지통으로 옮겼다. 현재는
  복구 가능하며 Jenkins의 현재 config나 build/data에는 영향이 없다.

## 12. 변경 이력

- `d99c011` — Queue Echo 연구 수집기, frozen preregistration, analyzer, 23 tests,
  root inventory/contract 등록
- `9b78892` — Raspberry rollback journal과 self-contained daily-rsync snapshot 호환성
- Jenkins config SHA는 위 표와 local-only inventory에 보존
- 최종 문서/인덱스 commit은 이 문서 작성 뒤 별도 기록
