# 033 — Fleet·Watermelon·Strawberry·Raspberry 운영 재점검 — 2026-08-24

작성일: 2026-08-24 KST

## 0. 결론

- Golden Watermelon의 공식 수집군은 EPL·Bundesliga·Ligue 1·LaLiga·MLS의 축구
  moneyline으로만 다시 시작했다. e-sports, cup, 2부 리그와 기타 종목은 Gamma 응답을
  받은 직후 CLOB 조회 전에 제외한다. e-sports인 Team Vitality 과거 사례는 official v3a cohort에
  포함하지 않는다.
- Jenkins에는 전략·연구 job 29개가 있으며, 별도 운영 job 10개를 합치면 39개다. 최종
  점검에서 29개 config fingerprint가 inventory와 일치했고 clean extension과 concurrent
  build는 0개였다. 주기 실행 중이 아닌 최근 완료 build는 모두 `SUCCESS`였다.
- **현재 계약으로 14일을 넘긴 live 수익 실험은 없다.** 14일을 넘긴 것은 주문과 P&L이
  없는 Golden Pomegranate collector뿐이다. 따라서 지금 폐기할 live 전략을 5% 목표만으로
  확정할 수 없다. 오래된 달력 기간과 여러 code/config cohort를 섞은 수익률로 폐기하지 않는다.
- Golden Strawberry v1 원본은 31,854,465,024 bytes로 동결됐지만, 기존 full census가
  매 cycle 너무 큰 DB를 재검증해 92~125분까지 늘었다. v1은 보존하고 unresolved
  path/resolution만 추적하는 follow-up runtime으로 교체했고 자연 cycle은 약 65초가 됐다.
- Golden Raspberry external-v2는 SQLite 무결성은 정상이지만 duplicate/off-slot과
  240초 초과 cycle 때문에 confirmatory cohort로는 무효다. 경제 가설과 5분 cadence는
  바꾸지 않고 slot claim과 225초 budget을 갖춘 새 v3 30일 cohort로 다시 시작했으며,
  첫 공식 자연 slot은 6/6 collection-health check를 통과했다.
- Golden Cherry Yellow는 resolved row 10개가 position cap 10개를 모두 점유하던 결함을
  복구했다. 자연 실행에서 exact confirmed BUY가 `HOLDING`으로 승격됐고, zero-fill BUY는
  정확히 30분 뒤 `UNFILLED`로 종결돼 active lifecycle이 정상임을 확인했다.

## 1. Evidence 경계와 보안

- Jenkins: `http://192.168.50.23:8080`
- source: `macmini-m5`
- fleet config/status observation: 2026-08-24 01:50~05:15 KST
- 수익 evidence는 `daily-rsync verify`를 통과한 DB의 exact confirmed fill만 사용했다.
  resolution payout은 `settlement assumption`으로 분리했고 realized SELL P&L에 섞지 않았다.
- 17개 credential-bearing job config는 익명 조회 가능한 inline assignment를 포함한다.
  값은 이 문서와 출력에서 모두 제거했다. 운영자가 LAN/local-only 위험을 수용한 기존
  방침에 따라 이번 작업에서 credential 형태는 바꾸지 않았다.
- 작업 중 MacBook 여유 공간은 약 207~219GiB였다. Mac Mini 내부는 Strawberry online
  snapshot 중 33GiB까지 줄었다가 cache 정리 후 약 62GiB로 복구됐다. T7은 약
  854~855GiB가 남았다.

## 2. 이번에 반영한 안전 수정

| commit | 대상 | 내용 |
|---|---|---|
| `e8b5df7` | Golden Watermelon | 축구 5개 league exact identity, e-sports/cup/2부리그 차단, v3a CREATE-only cohort |
| `e0e5a92` + `f38ccc1` + `374af02` + `4cf1e4d` + `c732b9b` | Golden Strawberry | v1 immutable handoff, atomic follow-up, 반복 full scan 제거, threshold 원본 무결성과 pinned fast validation |
| `09c96ac` | observability | maker zero-fee confirmed fill의 fee coverage 오판 제거 |
| `6b81a5f` | observability | BUY price improvement를 수량 불일치로 오판하던 strict audit 제거 |
| `3dfec1c` | Golden Cherry | exact token-aligned one-hot resolution, settlement/realized P&L 분리, resolved row cap 해제 |
| `b3b52aa` | observability | Blueberry `compact-v1/extrema` 계약 인식과 60일 archive gate 적용 |
| `2c9e701` | Golden Raspberry | v3 slot 선점, 225초 cooperative budget, deadline-aware HTTP와 새 DB epoch |

세 research 전략의 실험 threshold 자체는 바꾸지 않았다.

## 3. Golden Watermelon v3a

### 3.1 현재 계약

| Jenkins | runtime | cadence | workspace | 상태 |
|---|---|---|---|---|
| `polybot-white` | `watermelon-white-1m-v3a` | `* * * * *` | `/Volumes/t7/jenkins/polybot-white` | scheduled research |
| `polybot-grey` | `watermelon-grey-5m-v3a` | `H/5 * * * *` | `/Volumes/t7/jenkins/polybot-grey` | scheduled control |

- entry window: `[2026-08-23T16:00:00Z, 2026-08-30T16:00:00Z)`
- follow-up end: `2026-09-06T16:00:00Z`
- official universe: EPL, Bundesliga, Ligue 1, LaLiga, MLS의 top-level moneyline
- common identity: soccer/live/event tags와 league별 sport/tag/series identity가 모두
  일치해야 한다. `related_tags=false`다.
- 계정 credential과 live 주문은 source level에서 차단한다.

Verified evidence cutoff:

| Jenkins | remote DB → verified local DB | SHA-256 | sync finished / source cutoff UTC |
|---|---|---|---|
| `polybot-white` | `/Volumes/t7/jenkins/polybot-white/golden-watermelon/data/watermelon-white-1m-v3a/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-white/strategies/golden-watermelon/runtime/watermelon-white-1m-v3a/databases/latest/trades_sim.db` | `008ba6bb3b6a7baea0a22d593ddbe440d9a37fa73b60a5f529943e27a3990019` | `2026-08-23T19:07:29.517543Z` / `2026-08-23T19:06:48.980751Z` |
| `polybot-grey` | `/Volumes/t7/jenkins/polybot-grey/golden-watermelon/data/watermelon-grey-5m-v3a/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-grey/strategies/golden-watermelon/runtime/watermelon-grey-5m-v3a/databases/latest/trades_sim.db` | `fc1a426bff998fe4b5c8294e273f30d6fb90a15298ece47adeeb954a44891c40` | `2026-08-23T19:08:30.821138Z` / `2026-08-23T19:07:31.332212Z` |

최신 자연 cycle은 두 arm 모두 `cursor_complete=true`, accepted event 18,
eligible market/outcome 6, league drift 0이었다. raw event에 draw와 하위 proposition이
있어도 `DRAW_OUTCOME_EXCLUDED`, `NOT_TOP_LEVEL_MONEYLINE`으로 빠졌다. official v3a DB에는
e-sports가 0건이어야 한다.

2026-08-24 03:07 KST에 외장 workspace의 active v3a DB를 read-only로 다시 확인했다.
White/Grey 모두 e-sports tag ID `64` 또는 slug `esports/e-sports`인 event row가 0건,
`ACCEPTED` e-sports가 0건이었고, `REJECTED` event에서 market observation으로 넘어간 행도
0건이었다. 전체 event observation 21,037/4,305건 중 allowlist `ACCEPTED`는
2,160/450건이며 accepted non-allowlist도 0건이다. 당시 실제 accepted league는
EPL·Ligue 1·LaLiga였으며 Bundesliga·MLS는
수집 대상 경기가 없었다. 이는 Team Vitality 과거 관측이 새 official cohort에 섞이지
않았다는 운영 증거다. 다만 Team Vitality 한 사례로 e-sports 전체의 손익을 추정하는 것이
아니라, 이번 일주일 실험의 사전 범위를 축구로 좁힌 운영 결정이다. 향후 Bundesliga·MLS
경기 부재를 league drift로 해석하지 않는다.

약 3시간 15분간 실제 DB 증가는 White 290,054,144 bytes, Grey 58,400,768 bytes였고,
analyzer 선형 투영은 각각 약 2.143GB/day와 0.430GB/day, 합계 약 2.57GB/day다. 이 값도
첫 24시간 실제 증가량으로 다시 계산한다. 두 workspace는
`/Volumes` 아래 별도 device와 50GiB free-space gate를 통과하므로 내부 디스크로
fallback하지 않는다.

### 3.2 아직 판단하지 않는 것

첫 1주에는 수익성, stop level, 1분/5분 우열을 고르지 않는다. 먼저 cadence coverage,
league membership, e-sports 0, book/path/resolution coverage, integrity와 storage growth만
본다. 1분 arm이 더 많은 독립 crossing을 포착하지 못하면서 storage만 5배 늘리는지는
7 complete day 뒤 판단한다.

## 4. Golden Strawberry

### 4.1 v1 동결 판정

- runtime: `strawberry-shadow-one`
- data contract: `last-mile-clob-v1`
- last successful Jenkins: `#759`
- terminal cycle: 712
- 원본 DB: 31,854,465,024 bytes, 약 29.67GiB
- executable episode 17,230 / token 8,709 / condition 8,612
- unresolved episode 10,853 / token 5,763 / condition 5,750
- threshold event 51,929
- v1 sidecar와 writable holder: 0

`daily-rsync` run `aa4bd4b008e143458040a2955302cb54`가 31,854,465,024-byte 원본을
동기화했고 772 artifacts를 다시 검증했다. local/remote SHA-256은 모두
`c62d3dcef510bd8b20c0f9b3b362950b347f5f41269a6e36819c23f7a837eeb0`,
`quick_check=ok`였다. 고정 7일 범위 `[2026-08-15T04:00:00Z,
2026-08-22T04:00:00Z)`의 v1 health는 cursor와 membership blob 100%, duplicate 0,
episode path 100%, candidate metadata 98.60%, raw linkage 99.98%였다. 반면 successful unique
slot은 646/1,008(64.09%), off-slot은 315/693(45.45%), 성공 runtime p95 1,394초/max
1,830초였고 resolution coverage는 34.11%에 그쳤다. 따라서 v1은 collection-health gate를
통과하지 못했으며 수익성·parameter winner 선택은 허용되지 않는다.

`#759` 프로그램 runtime은 약 4,290초, Jenkins duration은 약 92분이었다. 전체 sampling
census와 거대한 SQLite 검사를 매 10분 반복하는 구조가 원인이므로 timer gap을 넓히는
방식이 아니라 v1을 immutable seed로 바꿨다.

### 4.2 follow-up v2a rollout

- runtime: `strawberry-shadow-one-followup-v2a`
- data/schema contract: `last-mile-clob-followup-v2a` / schema 4
- config hash: `46088ebd611fcea6369e063edb3574d84426f4c36119b0e9560fac07496ed867`
- source digest: `8ccbbcc6dcbc…`
- immutable v1 anchor: `b748763973b8cddbaa06b60d6cd6df4e2481223962b2162c2aeac5b266120b8a`
- Jenkins: `/Volumes/t7/jenkins/polybot-shadow-one`, `7-59/10 * * * *`, non-concurrent

초기 `#762`는 v1 path 약 496만 행을 Python에 전부 materialize해 1,804.5초 뒤 fail closed했고,
`#763`은 threshold import schema에서 source field 네 개가 빠진 것을 integrity check가
차단했다. 두 원인을 각각 latest-path SQL과 schema 4 exact round-trip으로 수정했다. 실패
DB는 삭제하지 않고 T7의
`data/archive/strawberry-shadow-one-followup-v2a-failed-20260824T040214KST`로 옮겼다.

`#764` one-time `FULL_SEED`는 121.60초에 성공했다. episode 17,230, condition 8,612,
terminal condition 2,862, threshold 51,929의 count/hash와 source anchor가 일치했다.
`#765` 수동 `PINNED_FAST`는 59.73초였다. 이어 timer를 켠 뒤 첫 두 자연 build
`#766`(04:17:27 KST)과 `#767`(04:27:27 KST)은 Jenkins duration 69.75/69.20초,
프로그램 runtime 65.87/64.62초로 모두 480초 SLA 안에서 `SUCCESS`였다. 두 번째 cycle은
새 resolution condition 5개를 게시했고 두 build 모두 source count/hash drift와 partial
publication이 0이었다. rollout health clock은 `2026-08-23T19:17:00Z`부터 센다.

최종 `daily-rsync` run `b5b39e42a63c4801b7ad6b7e6e4c4479`는 193,352,145 bytes를
전송하고 777개를 건너뛰며 실패 0건으로 끝났다. verify는 780/780 artifacts,
retention skip 0, conflict 0으로 `SUCCESS`였다. verified v2a DB는 193,343,488 bytes,
SHA-256 `1a30f6641fa8ce3dd7fe6be890670056fdcb174e25a24b5d60a5e59a3f71adf3`,
source cutoff `2026-08-23T19:28:37.021306Z`다.

- remote v1 → local v1:
  `/Volumes/t7/jenkins/polybot-shadow-one/golden-strawberry/data/strawberry-shadow-one/trades_sim.db`
  → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-shadow-one/strategies/golden-strawberry/runtime/strawberry-shadow-one/databases/latest/trades_sim.db`
- remote v2a → local v2a:
  `/Volumes/t7/jenkins/polybot-shadow-one/golden-strawberry/data/strawberry-shadow-one-followup-v2a/trades_sim.db`
  → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-shadow-one/strategies/golden-strawberry/runtime/strawberry-shadow-one-followup-v2a/databases/latest/trades_sim.db`
- sync finished: `2026-08-23T19:29:49.477468Z`

첫 두 자연 slot의 combined analyzer에서 v2a 자체는 `healthy=true`였다. cadence 2/2,
duplicate/off-slot 0, quick check와 foreign key 정상, atomic success 경계 정상, compact book
11,409건 무결성 정상, book attempt/path/resolution coverage 100%, displayed book은
11,409/11,418(99.92%; 9건 explicit censoring)이었다. top-level `healthy=false`는 동결 v1의
과거 cadence 결함을 의도적으로 함께 표시한 결과이지 v2a rollout 실패가 아니다. 약 11분
증가량을 환산한 5.43GB/day는 seed 직후의 매우 이른 상한이므로 첫 24시간 실측 전에는
용량 예측으로 사용하지 않는다.

v1 entry census는 계속 immutable이고 v2a는 신규 crossing을 만들지 않는다. timer는
follow-up 종료 전인 2026-09-21 13:00 KST까지 제거해야 한다. one-time `FULL_SEED`는
recurring SLA 분모에 넣지 않는다.

## 5. Golden Raspberry

### 5.1 external-v2 판정

공통 verified 분석 범위는
`[2026-08-13T12:00:00Z, 2026-08-19T14:15:00Z)`였다.

| shard | remote DB → verified external-v2 local DB | SHA-256 | source cutoff / sync finished UTC |
|---|---|---|---|
| DO | `/Volumes/t7/jenkins/polybot-do/golden-raspberry/data/raspberry-do-shard-0/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-do/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-do-shard-0/databases/latest/trades_sim.db` | `b3303a79caf366163a39f6d91eded644bd6c6db6d4ffcbe8800d1fb685dceba5` | `2026-08-23T17:40:34.872603Z` / `2026-08-23T18:28:26.226168Z` |
| RE | `/Volumes/t7/jenkins/polybot-re/golden-raspberry/data/raspberry-re-shard-1/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-re/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-re-shard-1/databases/latest/trades_sim.db` | `2e11fc25ea1a92c032aa969530bec3fd3a5ce09405b2f1aecd84b4db6b04ee31` | `2026-08-23T17:41:43.771998Z` / `2026-08-23T18:46:08.202262Z` |
| MI | `/Volumes/t7/jenkins/polybot-mi/golden-raspberry/data/raspberry-mi-shard-2/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-mi/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-mi-shard-2/databases/latest/trades_sim.db` | `8e993bae9639bbcc2e301aed2edfb6eacfec7e0d1938d7b55d11f1b54c37fcd9` | `2026-08-23T17:42:36.443217Z` / `2026-08-23T19:03:01.816579Z` |

| shard | matched / expected | coverage | duplicate | off-slot | success max runtime | failed |
|---|---:|---:|---:|---:|---:|---:|
| DO | 1,725 / 1,755 | 98.29% | 3 | 12 | 418.68s | 9 |
| RE | 1,682 / 1,755 | 95.84% | 12 | 29 | 278.33s | 23 |
| MI | 1,690 / 1,755 | 96.30% | 11 | 21 | 253.44s | 22 |

세 DB는 `quick_check=ok`이고 follow-up first attempt 중복도 0이었다. 그러나 duplicate와
off-slot은 계약상 0이어야 하고 max runtime은 240초 미만이어야 한다. 실패 54건은 모두
Gamma 429/ConnectionError였다. 따라서 coverage 95%만 넘었다고 건강한 confirmatory
cohort로 볼 수 없다.

Universe pair coverage에 FOLLOWUP_ONLY missing을 섞어 HIGH로 만들던 계측 오판도
분리해야 한다. primary MI neutral control은 0/182이며, 새 cohort에서도 계속 0이면
조건을 사후 완화하지 않고 `UNRESEARCHABLE`로 중단한다.

### 5.2 v3 재시작

commit `2c9e701`의 새 `queue-echo-v3` epoch는 기존 DB를 migration하거나 clean하지 않고
각 외장 workspace에 다음 세 DB를 새로 만들었다.

| Jenkins | runtime | config hash | timer |
|---|---|---|---|
| `polybot-do` | `raspberry-do-v3-shard-0` | `8752eaaee463…` | `0-59/5 * * * *` |
| `polybot-re` | `raspberry-re-v3-shard-1` | `88b38b1e934d…` | `1-59/5 * * * *` |
| `polybot-mi` | `raspberry-mi-v3-shard-2` | `4096bbeeab90…` | `2-59/5 * * * *` |

공통 source digest는 `0e96e90c81d949e8084a6d6bb523c2e8a3fe0c6ba4ecb22fc41a3bb0da98a8f8`,
공식 confirmatory window는 `[2026-08-23T20:00:00Z, 2026-09-22T20:00:00Z)`다.
각 invocation은 public HTTP 전에 자기 5분 slot을 원자적으로 claim하고, 225초 cooperative
budget과 240초 hard limit 안에서 retry와 `Retry-After`를 제한한다. 같은 slot 중복 실행은
수집하지 않고 terminal audit로 끝난다.

공식 시작 전 자연 preflight에서는 세 DB 모두 `quick_check=ok`, book coverage 100%,
quality issue 0이었고 최근 cycle은 약 3.4~13.8초였다. 이 preflight row는 30일 수익성
분모에 넣지 않는다.

첫 공식 deployment health 범위는
`[2026-08-23T20:00:00Z, 2026-08-23T20:10:00Z)`로 고정했다. 자연 build는
DO `#3074/#3075`, RE `#3096/#3097`, MI `#3079/#3080`이며 6/6이 `SUCCESS`였다.
slot lateness는 29.7~31.0초, 프로그램 terminal runtime은 2.88~15.32초, Jenkins
duration은 최대 18.87초였다.

| shard | remote DB → verified local DB | SHA-256 | sync finished / source cutoff UTC | verify |
|---|---|---|---|---|
| DO | `/Volumes/t7/jenkins/polybot-do/golden-raspberry/data/raspberry-do-v3-shard-0/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-do/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-do-v3-shard-0/databases/latest/trades_sim.db` | `c1c6b55908b900e94a2f2ed0fdafcd4b3d31147e5481894e84cc87c6bd27b2eb` | `2026-08-23T20:12:41.762829Z` / `2026-08-23T20:10:34.324762Z` | 3,091/3,091 |
| RE | `/Volumes/t7/jenkins/polybot-re/golden-raspberry/data/raspberry-re-v3-shard-1/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-re/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-re-v3-shard-1/databases/latest/trades_sim.db` | `bb7d4683bed25051f781fd020f5c0b0235d01c9febc060f6a4cc9990c281284d` | `2026-08-23T20:12:52.571740Z` / `2026-08-23T20:11:33.915443Z` | 3,112/3,112 |
| MI | `/Volumes/t7/jenkins/polybot-mi/golden-raspberry/data/raspberry-mi-v3-shard-2/trades_sim.db` → `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-mi/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-mi-v3-shard-2/databases/latest/trades_sim.db` | `007db05ed89f64ebbbd79d9253fac67cf1b5bf46e016f008ddb3dcbd90c5e347` | `2026-08-23T20:13:02.196634Z` / `2026-08-23T20:12:34.495582Z` | 3,094/3,094 |

각 verify는 failure, retention skip, open conflict가 0이었다. 경로의 `external-v2`는
daily-rsync workspace marker 이름일 뿐이며 runtime/data contract는 위 v3 identity로
DB 내부에서 다시 확인했다.

`queue-echo-analyzer-v3` 결과는 fleet contract check와 세 shard의 모든 collection-health
check가 통과했다. 각 shard cadence 2/2, duplicate/late/invalid claim 0, cursor-complete
2/2, failed/orphan sweep 0, single cohort, `quick_check=ok`, CRITICAL/HIGH 0이었다. UNIVERSE
token은 DO 28/28, RE 12/12, MI 28/28이고 YES/NO same-request pair, normalized token,
raw payload linkage가 모두 100%였다. cross-shard condition overlap은 0이다.

DO 두 번째 slot에서 DO signal 1개와 research case 2개가 생성됐다. follow-up이 0인 것은
60~75분 창이 아직 시작되지 않았기 때문이며 누락으로 해석하지 않는다. analyzer verdict는
`HEALTH_ONLY_NOT_ENOUGH_DURATION`이고 수익성·threshold·MI primary gate는 판단하지 않았다.

첫 두 post-publish 사이 5분 증가량은 DO 471,040, RE 389,120, MI 450,560 bytes,
합계 1,310,720 bytes였다. 단순 환산은 약 0.377GB/day, 11.3GB/30일이지만 한 interval
추정일 뿐이다. 공식 구간 T7 최소 free는 916,235,698,176 bytes이고 storage guard는 모두
`OK`였다. 첫 24시간 실제 증가량으로 다시 계산한다.

external-v2는 삭제하지 않고 operational/exploratory evidence로 보존한다. v3에서도
가격·imbalance·DO/RE/MI·60~75분 follow-up·control·5분 cadence는 바꾸지 않는다.

## 6. 현재 Jenkins strategy/research topology

“epoch 시작”은 job 최초 생성일이 아니라 현재 universe/config/source 계약이 적용된
시점이다. 같은 이름의 과거 전략은 historical epoch다.

| Jenkins | 현재 전략/runtime | mode / timer | 현재 epoch 시작 KST | 판정 |
|---|---|---|---|---|
| `golden-pomegranate` | Pomegranate / `pomegranate-15m-v2` | research `H/15` | 08-07 07:46 | 16.8일, P&L 대상 아님 |
| `polybot-cat`, `polybot-dog` | Papaya 24h / 72h | live `H/10` | 08-19 08:24~08:26 | 4.7일 |
| `polybot-queen`, `polybot-king` | Queen 24h / 12h | live `H/5` | 08-19 08:24~08:26 | 4.7일 |
| `polybot-cherry` | Elderberry / `default` | close-only `H/5` | 08-14 19:31경 | wind-down |
| `polybot-red` | Date / `default` | close-only `H/5` | 08-14 19:31경 | 폐쇄 전략 |
| `polybot-eagle` | Blueberry A +2pp | live `*/5` | 08-19 01:16 | 5.0일, B historical |
| `polybot-shadow` | Blueberry shadow | shadow `H/5` | 08-19 01:19 | research |
| `polybot-kiwi-a`~`d` | Kiwi 3x1/3x2/5x1/5x2 | simulation offsets 0/1/2/3 | 08-13 09:00 | 10.7일, promotion gate 과거 실패 |
| `polybot-fruit`, `lime`, `wolf` | Melon high/mid/low | live `H/5` | 08-19 08:24~08:26 | 4.7일 |
| `polybot-bear`, `eco`, `tiger` | Quince passive/nearest/cross | live `H/5` | 08-19 08:24~08:26 | 4.7일 |
| `polybot-yellow` | Cherry / `default` | live `H/5` | 08-19 08:46 | 4.7일, resolution repair 완료 |
| `polybot-orange`, `fox` | Tangerine A94 / B92 | live `H/5` | 08-20 23:08 | 3.1일 |
| `polybot-black` | Black paired | research `H/5` | 08-20 23:08 | 약 3일 |
| `polybot-do`, `re`, `mi` | Raspberry v3 shard 0/1/2 | research offsets 0/1/2 | 08-24 05:00 | v2 무효, v3 6/6 첫 slot healthy |
| `polybot-shadow-one` | Strawberry follow-up v2a | research `7-59/10` | 08-24 04:17 | v1 동결, 첫 자연 2/2 healthy |
| `polybot-white`, `grey` | Watermelon v3a 1m/5m | research `1m` / `H/5` | 08-24 01:00 | 첫날 health 수집 |

현재 `polybot-fox`는 Blueberry-B가 아니라 Tangerine-B다. Orange/Fox frozen epoch의
정확한 시작은 메모의 8월 21일이 아니라 2026-08-20 23:08 KST다.

### 6.1 `golden-*` 폴더 기준 누락 확인

| 상태 | 전략 폴더 |
|---|---|
| current live | `golden-blueberry`, `golden-cherry`, `golden-melon`, `golden-papaya`, `golden-queen`, `golden-quince`, `golden-tangerine` |
| close-only / wind-down | `golden-date`, `golden-elderberry` |
| current research/simulation | `golden-black`, `golden-kiwi`, `golden-pomegranate`, `golden-raspberry`, `golden-strawberry`, `golden-watermelon` |
| 폐쇄, current Jenkins 없음 | `golden-fig`, `golden-honeydew`, `golden-lime`, `golden-mango`, `golden-nectarine` |
| current Jenkins mapping 없음 / 상태 미정 | `golden-apple`, `golden-banana`, `golden-grape`, `golden-orange` |

계정 별칭이나 Jenkins 이름에 `apple`, `orange`, `fox`, `lime`가 들어가도 같은 이름의
전략 폴더를 실행한다는 뜻은 아니다. 예를 들어 `polybot-orange`는 Golden Tangerine,
`polybot-lime`은 Golden Melon이다. current mapping이 없는 네 폴더는 실행 중이라고
추정하지 않는다.

## 7. Live 성과: 현재 말할 수 있는 범위

| 전략 | strict current cohort | exact evidence의 기술 통계 | 결론 |
|---|---|---|---|
| Quince A/B/C | 14일 미만 | clean 구간 confirmed BUY 1/3/4; closed net 약 -1.336/-0.474/-1.126 USDC | execution arm 선택 금지 |
| Papaya 24h/72h | 14일 미만 | confirmed round-trip 3건, 모두 stop, 합계 약 -1.120 USDC | 표본 부족, 현 수치 변경 금지 |
| Queen 24h/12h | 14일 미만 | 현재 $5 cohort round-trip 3/1건, net 약 -3.692/-2.443 USDC | 손실 신호지만 n=4라 폐기 판정 금지 |
| Melon high/mid/low | 14일 미만 current epoch | mixed old cohort closed 1/3/3건, 모두 TP; 합계 약 +4.638 USDC, distinct event 3 | 5% 증거 아님, cohort 혼합 금지 |
| Blueberry A/B | paired current cohort 없음 | A exact confirmed SELL 4건 net +2.610 USDC; resolution assumption 별도 +2.580 | B가 historical이라 A/B·5% 평가 불가 |
| Tangerine A94/B92 | 약 3일 | Orange 33 BUY, Fox 34 BUY; 각각 deliberate open cap 3/3 | 30일 전 threshold 변경 금지 |
| Cherry Yellow | repair 직후 | exact confirmed SELL 누적 -40.710 USDC, legacy/unproven P&L은 제외 | 새 repair cohort를 별도 관찰 |
| Elderberry/Date | close-only | 신규 진입 없음 | 수익 실험이 아니라 wind-down |

위 수치는 서로 기간·notional·event 수가 달라 단순 순위를 만들지 않는다. 월 5%는
단순 P&L/계좌 잔고가 아니라 동일 cohort의 비용 후 event-cluster ROI와 confidence lower
bound로 판정해야 한다. 현재는 그 조건을 만족하는 14일 live cohort가 0개다.

## 8. Position cap·runtime·clean audit

- Tangerine Orange/Fox의 open 3/3은 `max_open_positions=3`을 정확히 채운 의도한 risk
  cap이다. stale DB row 때문에 막힌 것이 아니므로 30일 전에 상한을 늘리지 않는다.
- Quince·Papaya·Queen·Melon·Blueberry current DB에서는 stale max-position blocker가
  확인되지 않았다.
- Yellow의 cap 10/10 blocker였던 closed rows 10개는 `RESOLVED`로 옮겼다. 이후 신규
  exact fill 2건 이상이 `HOLDING`으로 승격됐고 0-fill은 TTL 종결됐다.
- broad Gamma universe는 이미 strategy별 server-side volume floor와 shared sweep으로
  줄었다. 최신 strategy build는 대체로 10~95초이며 cadence 안이다. Strawberry v1과
  Raspberry의 tail latency는 별도 구조 수정 대상이다.
- 현재 strategy/research config에서 `CleanBeforeCheckout`, `git clean -fdx`, DB 삭제 shell은
  발견되지 않았다. 이번 Raspberry/Strawberry restart도 기존 DB를 clean하지 않고 새 runtime
  path를 사용한다.
- 이번 배포에서 바뀐 Jenkins config는 accountless research job뿐이다. live/close-only job의
  config SHA-256은 재조회한 inventory와 같아 지갑·주문 파라미터에는 변경이 없다.

## 9. 다음 점검 일정과 그대로 쓸 프롬프트

### 24시간

2026-08-25 09:00 KST 이후:

> `polybot-white와 polybot-grey를 daily-rsync로 동기화하고 Golden Watermelon v3a의 첫 24시간 collection health를 검증해줘. 수익성과 stop/1분·5분 우열은 판단하지 말고, EPL·Bundesliga·Ligue1·LaLiga·MLS membership, e-sports/cup/2부리그 0건, cadence, cursor, exact book, path/resolution, DB 무결성과 실제 1일 저장공간 증가량을 확인해줘.`

2026-08-25 05:10 KST 이후:

> `polybot-do, polybot-re, polybot-mi의 Raspberry v3 DB와 로그를 daily-rsync로 동기화하고 첫 24시간 collection health를 검증해줘. 수익성과 threshold는 판단하지 말고 slot claim, duplicate/late skip, 225초 budget, failed run 포함 runtime, YES/NO pair, follow-up claim, control, cohort와 DB 무결성을 확인해줘.`

2026-08-25 04:30 KST 이후:

> `polybot-shadow-one을 daily-rsync로 동기화하고 Golden Strawberry follow-up v2a의 첫 24시간 collection health를 검증해줘. 수익성은 판단하지 말고 v1 불변, seed count/hash, source anchor, 10분 cadence, PINNED_FAST runtime 480초 미만, path/resolution publication 원자성, DB 무결성과 실제 저장공간 증가량만 확인해줘.`

### 7일

2026-08-31 01:00 KST 이후:

> `Golden Watermelon v3a의 7 complete day collection health를 검증해줘. 아직 수익 threshold를 고르지 말고 1분 arm이 5분 control보다 포착한 독립 crossing·경기·resolution coverage의 증분과 저장비용을 비교하고, e-sports가 0인지 확인해줘.`

2026-08-31 05:10 KST 이후:

> `Golden Raspberry v3의 7 complete UTC day health를 검증해줘. external-v2와 섞지 말고 v3 source digest만 사용해서 cadence/deadline/slot/follow-up/control coverage를 판정해줘. MI neutral control이 계속 0이면 파라미터를 완화하지 말고 UNRESEARCHABLE 여부를 판단해줘.`

### 14일과 30일

- 2026-09-02 08:30 KST 이후: Papaya·Queen·Quince·Melon current 8월 19일 epoch의
  14일 strict audit. 단일 cohort가 실제로 14일을 덮는지 먼저 확인한다.
- 2026-09-03 23:08 KST 이후: Tangerine A94/B92 첫 14일 중간 health. 30일 전 arm을
  고르거나 cap을 바꾸지 않는다.
- Raspberry v3 시작 30일 뒤: frozen 30일 cohort의 collection gate를 먼저 통과한 뒤에만
  Queue Echo 수익성과 threshold를 분석한다.
- Strawberry follow-up은 2026-09-21 13:00 KST 전 timer를 제거하고, 미해결 episode를
  이익으로 추정하지 않는다.

14일 fleet prompt:

> `현재 live Jenkins를 다시 inspect하고 daily-rsync로 동기화해, 2026-08-19 이후 단일 config/source cohort가 14일을 채운 전략만 strict audit해줘. exact confirmed fill과 비용 후 event-cluster ROI만 사용하고 CRITICAL/HIGH나 evidence gap이 있으면 5% 판정과 폐기를 중단해줘.`

## 10. 운영자에게 남은 선택

- Raspberry 전용 Jenkins agent/label과 3개 executor를 별도로 만드는 것은 Mac Mini Jenkins
  node 관리 권한이 필요한 선택사항이다. 현재 built-in node는 16 executors이고 배포
  점검 시 queue가 비어 있어 즉시 필요한 조치는 아니다. v3의 225초 deadline은
  429/connection tail을 막지만, 24시간 health에서 late slot이 재발하면 전용 node가
  더 확실한 분리 수단이다.
- Date는 이미 폐쇄됐고 Elderberry는 close-only다. account-wide 수동 position이 없음을
  운영자가 확인한 뒤에만 두 timer를 완전히 제거한다.
- inline credential을 Jenkins Credentials Binding으로 옮기는 것은 이번 전략 로직 변경과
  분리된 보안 작업으로 남긴다.
