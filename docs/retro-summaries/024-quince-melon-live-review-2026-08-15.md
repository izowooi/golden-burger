# 024 — Golden Quince·Melon live 저빈도 회고 — 2026-08-15

작성일: 2026-08-15 KST

대상:

- Quince: `polybot-bear` / `polybot-eco` / `polybot-tiger`
- Melon: `polybot-fruit` / `polybot-lime` / `polybot-wolf`
- 사용자 메모의 `fox`는 이 문서에서 계정명 **GOLDEN-FRUIT**를 뜻하며 Jenkins
  `polybot-fox`가 아니다. 해당 계정의 실제 job은 `polybot-fruit`다.

## 0. 결론

```text
Quince: WAIT_UNTIL_DAY_7 / 현재 parameter 변경 금지
Melon: UNDERPOWERED_CONTINUE_TO_DAY_30 / 현재 parameter 변경 금지
Cadence: 6개 모두 H/5 유지
Jenkins parameter/config 변경: 없음
Code 변경: Melon first-crossing decision evidence 누락만 보완
```

Quince 세 팔은 2026-08-13 00:26 KST clean 재시작 뒤 약 53시간이 지났지만
**candidate 0, BUY 0, fill 0**이다. passive/nearest/cross 실행 처치가 한 번도 작동하지
않았으므로 어느 팔이 좋은지뿐 아니라 체결률조차 비교할 수 없다. 첫 day-7 checkpoint인
**2026-08-20 00:27 KST 이후**까지 동일한 값으로 유지한다.

Melon은 2026-08-05 22:14 KST 시작 뒤 약 9일 7시간 동안 exact confirmed BUY가
high/mid/low 순서로 **1 / 2 / 2건**, 합계 **5개 포지션**이다. BUY와 SELL을 별도 fill
row로 세면 2 / 3 / 3건, 합계 8건이다. 그러나 독립 기회는 사실상 두 시장뿐이고 한 시장은
세 계정에 반복된 동일 signal이다. 현재까지 양수인 결과는 고무적이지만 수익성을 말할 표본은
아니다.

Melon의 저빈도는 Jenkins 5분 주기나 volume gate가 주원인이 아니다. `$20k` low와 `$50k`
mid가 같은 2건을 체결했고 `$20k~$50k` 구간에서 추가된 거래가 0건이다. 첫 0.85 상향 교차가
원래 드물고, 단 한 cycle에서 후보 2개가 동시에 나와 `max_new_positions_per_cycle=1` 때문에
팔당 1개를 선택하지 못한 것이 전부다. cap을 2로 바꿨어도 누적 BUY는 2 / 3 / 3건 수준이라
day-7 하한 7건에는 못 미친다.

이번에 고친 것은 거래 기준이 아니라 관측성이다. Melon 문서는 탈락한 첫 교차의
volume/liquidity/time을 보존한다고 했지만 실제 DB의 `skipped_markets`는 비어 있었고 그
테이블은 원래 재진입 cooldown 용도였다. 별도 `entry_signal_decisions`를 추가해 이후 첫
교차의 candidate/rejection과 적용 threshold를 남기도록 배포했다. 주문 금액, 확률, 시간,
volume, liquidity, spread, position cap과 Jenkins schedule은 바꾸지 않았다.

## 1. 저장공간과 동기화 가능성

작업 직전과 최종 확인 모두 safety floor를 통과했다.

| 항목 | 확인값 |
|---|---:|
| MacBook volume | 926 GiB |
| MacBook 사용 / 여유 | 551 GiB / **345 GiB** |
| `daily-rsync/data` | 약 11 GiB |
| Mac mini 내부 여유 | **85,004,808,192 bytes**, 약 79.2 GiB |
| `daily-rsync` 최소 여유 기준 | 50 GiB |
| 6개 canonical DB 합계 | 약 933 MiB |

따라서 6개 job의 DB·bot log·Jenkins console을 가져오는 데 용량상 문제가 없었다.
각 job을 독립 `sync-job`으로 처리했고 모든 최신 sync attempt가 `SUCCESS`다.

## 2. Evidence boundary

Timezone은 UTC이며 KST는 UTC+9다.

### Quince

- 새 평가 epoch:
  - Bear `2026-08-12T15:26:26.842488Z`
  - Eco `2026-08-12T15:26:32.639191Z`
  - Tiger `2026-08-12T15:26:34.043211Z`
- 운영 관측 종료: 각 DB source cutoff `2026-08-14T20:23:43Z`~`20:28:57Z`
- strict closed range: `[2026-08-13T00:00:00Z, 2026-08-14T00:00:00Z)`
- audit output:
  `daily-rsync/data/analysis/quince-abc-20260813-20260814/retro-audit.json`

### Melon

- 첫 live run:
  - High `2026-08-05T13:14:21.685539Z`
  - Mid `2026-08-05T13:14:35.241241Z`
  - Low `2026-08-05T13:14:45.472797Z`
- 성과 설명용 전체 DB 관측: 위 시작부터 최초 분석 sync의 공통 cutoff
  `2026-08-14T20:23:45Z`까지
- strict full closed range: `[2026-08-06T00:00:00Z, 2026-08-14T00:00:00Z)`
- 최근 무결점 strict range: `[2026-08-12T00:00:00Z, 2026-08-14T00:00:00Z)`
- audit outputs:
  - `daily-rsync/data/analysis/melon-abc-20260806-20260814/retro-audit.json`
  - `daily-rsync/data/analysis/melon-abc-20260812-20260814/retro-audit.json`

배포 후 Melon DB는 다시 동기화했다. 아래의 체결 수와 손익은 배포 전후에 변하지 않았고,
배포 후 DB에는 새 table과 commit `ce515a6` RunAudit가 존재한다.

## 3. Verified DB·sync provenance

### Quince — 분석 sync

#### Bear / passive

- Remote:
  `/Users/jongwoopark/.jenkins/workspace/polybot-bear/golden-quince/data/polybot-quince-passive/trades.db`
- Verified local:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-quince/runtime/polybot-quince-passive/databases/latest/trades.db`
- SHA-256: `842b1c58c85f72ffe1a6b6139d16b1da4a1286f1bf5ed81d3188a8a5aff01532`
- Source cutoff: `2026-08-14T20:28:57.460029Z`
- Sync: `dd0f2be04c8948fb921e2ef239bb102f`, finished
  `2026-08-14T20:29:26.209688Z`
- Verify: SUCCESS, 2,236 checked, failure/retention skip/conflict 0

#### Eco / nearest

- Remote:
  `/Users/jongwoopark/.jenkins/workspace/polybot-eco/golden-quince/data/polybot-quince-nearest/trades.db`
- Verified local:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eco/strategies/golden-quince/runtime/polybot-quince-nearest/databases/latest/trades.db`
- SHA-256: `0c4fcfcb0ee08af11e96a1451a9e117f04b6a8f227d1dd8d95c886140aeefd3d`
- Source cutoff: `2026-08-14T20:23:43.295574Z`
- Sync: `92628d79f291404eac046334696e21a7`, finished
  `2026-08-14T20:24:41.512980Z`
- Verify: SUCCESS, 2,769 checked, failure/retention skip/conflict 0

#### Tiger / cross

- Remote:
  `/Users/jongwoopark/.jenkins/workspace/polybot-tiger/golden-quince/data/polybot-quince-cross/trades.db`
- Verified local:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-tiger/strategies/golden-quince/runtime/polybot-quince-cross/databases/latest/trades.db`
- SHA-256: `f3436422f8272867804409ae970251357c7c46a53914987e76b91eb00542bb08`
- Source cutoff: `2026-08-14T20:23:43.016137Z`
- Sync: `bd29c907a0d548c1a6bc218f97a78943`, finished
  `2026-08-14T20:25:26.597534Z`
- Verify: SUCCESS, 2,224 checked, failure/retention skip/conflict 0

### Melon — 배포 후 최종 sync

| Job / arm | Sync run · finished UTC | DB source cutoff | Current SHA-256 | Verify |
|---|---|---|---|---|
| `polybot-fruit` / high | `177cc638060c41bebefba7d5c146d5a7` · `2026-08-14T20:58:20Z` | `2026-08-14T20:56:59.342223Z` | `b99b315c2dc3f76556cc697d594997c553d983f80f2849126669ea021267d38c` | SUCCESS · 2,740 |
| `polybot-lime` / mid | `051bd1c66ae9463fbb2507046a805add` · `2026-08-14T20:59:15Z` | `2026-08-14T20:57:02.739645Z` | `26794ff1d429b889afad785d5c920ecf8cee7cd56a8b5f2e8de8b3f6d4e79266` | SUCCESS · 2,738 |
| `polybot-wolf` / low | `6917788885b142ec8bd728b784f7b005` · `2026-08-14T20:59:36Z` | `2026-08-14T20:57:03.255622Z` | `43248620afce24f2f3b1723ff2fae046ee2f7e46c8a01bcec84eb70b1f7bbdb1` | SUCCESS · 2,091; retention skip 17 |

Melon local DB 절대 경로는 각각 다음과 같다.

- High:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-fruit/strategies/golden-melon/runtime/polybot-melon-high/databases/latest/trades.db`
- Mid:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-lime/strategies/golden-melon/runtime/polybot-melon-mid/databases/latest/trades.db`
- Low:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-wolf/strategies/golden-melon/runtime/polybot-melon-low/databases/latest/trades.db`

remote 경로는 각 Jenkins workspace 아래
`golden-melon/data/polybot-melon-{high|mid|low}/trades.db`다. Wolf의 17건은 원격
LogRotator가 이미 지운 과거 Jenkins console의 명시적 retention skip이다. 요청 기간의 DB와
bot log는 존재하고 검증됐으므로 이번 거래 수 판정에는 영향을 주지 않는다.

## 4. Jenkins current configuration

`inspect-jenkins-job`으로 실제 config를 확인했다. 여섯 job 모두 enabled,
`concurrentBuild=false`, `H/5 * * * *`, live/active이고 clean extension이나 shell cleanup
명령이 없다.

| Job | Runtime / treatment | Config SHA-256 | 최종 확인된 timer build |
|---|---|---|---|
| `polybot-bear` | `polybot-quince-passive` | `9bfea9d33c2c36de8623e7780c062ec5bd9d80d9f9c7ec4d3b74c62057acf89c` | `#7993 SUCCESS` |
| `polybot-eco` | `polybot-quince-nearest` | `43645dc98a1b803e317b0f776129f4db69d2a48bb28e3ebf719241e40cf1c69f` | `#9883 SUCCESS` |
| `polybot-tiger` | `polybot-quince-cross` | `599219c8955b3ca57dd9e1ff9972ba3f2ca58e1a068b3e3e6d09f0b6f64eca32` | `#9496 SUCCESS` |
| `polybot-fruit` | `polybot-melon-high`, volume `$150k` | `895b182e6be10cecf4855df11a1e834de6b2e352383c37557d74f5dcec6ecbe6` | `#2730 SUCCESS` |
| `polybot-lime` | `polybot-melon-mid`, volume `$50k` | `60f1e65a5c840f5e835639a5764fe457d59b91b9b12bd8ad99d1663b933c2c2f` | `#9495 SUCCESS` |
| `polybot-wolf` | `polybot-melon-low`, volume `$20k` | `83dc86af1ab959d15b611329ff6cb251f062bc09e4657892abb8e8c9c65b1ab1` | `#8348 SUCCESS` |

Quince 최신 세 timer build는 같은 cursor-complete 389페이지 sweep을 저장하고
candidate/buy/holding 0, RunAudit SUCCESS로 끝났다. Melon 최신 세 timer build도 같은
389페이지 sweep, candidate/buy/holding 0, RunAudit SUCCESS다.

## 5. Quince post-mortem

### 운영 건강도

| 지표 | Bear passive | Eco nearest | Tiger cross |
|---|---:|---:|---:|
| 전체 RunAudit | 599 | 598 | 596 |
| SUCCESS / FAILED | 598 / **0** | 597 / **0** | 595 / **0** |
| 동기화 순간 RUNNING | 1 | 1 | 1 |
| Scanner candidate | **0** | **0** | **0** |
| Trade / confirmed BUY / SELL | **0 / 0 / 0** | **0 / 0 / 0** | **0 / 0 / 0** |
| Runtime p50 / p95 | 2.48m / 6.17m | 2.39m / 6.16m | 2.69m / 6.16m |
| Start gap p50 / p95 | 5.00m / 6.33m | 5.00m / 6.33m | 5.00m / 6.53m |
| Gap >15m | 2 | 2 | 3 |

5분보다 오래 걸리는 tail이 있지만 start gap 중앙값은 정확히 5분이고 실패 cycle이 없다.
후보 0건을 “Jenkins가 신호를 놓쳤다”로 설명할 evidence가 없다. H/3으로 줄이면 p95보다
짧아져 queue와 Gamma 부하만 늘 가능성이 높다.

최신 제외 집계의 큰 항목은 `neg_risk_or_unknown`, `not_standard_yes_no`,
`low_liquidity`, `low_volume`이다. 이것들은 모든 시장에 순차 적용한 broad count라서
“0.90 first crossing인데 volume 하나 때문에 탈락한 수”가 아니다. 이 숫자만 보고
`$10k/$2k`를 낮추면 안 된다. `first_crossing_already_observed` 20여 건도 재시작 시 이미
threshold 위에 있던 inventory가 주로 포함되어 현재 유효 신규 crossing 수가 아니다.

### Strict audit

closed 1일 strict audit는 exit 1, **HIGH 9 / MEDIUM 3**이다. 각 팔이 같은 항목을 갖는다.

| Severity | Issue | 해석 |
|---|---|---|
| HIGH | `archive_window_short` | 새 DB가 60일 archive 계약 중 약 2.3일만 축적 |
| HIGH | `market_sweep_attestation_missing` | compact 정책상 상세 membership checkpoint가 아직 희박 |
| HIGH | `market_catalog_missing` | strict metadata/qualified coverage가 audit gate 미달 |
| MEDIUM | `logs_missing` | auditor가 daily-rsync의 별도 log root를 자동 연결하지 못함 |

실제 bot/console log는 동기화·verify됐고, 해당 UTC 하루는 팔별 283/283 SUCCESS run이다.
그러나 archive 관련 HIGH는 실제 evidence maturity 부족이므로 Evidence Contract에 따라
parameter tuning과 실행 모드 우열 판정을 중단한다.

### Quince 결정

1. **2026-08-20 00:27 KST까지 그대로 둔다.** 53시간은 execution treatment를 평가하기에
   부족하다.
2. day 7에도 passive confirmed BUY가 7건 미만이면 `INCONCLUSIVE`다. 현재 속도가 0이면
   우선 30~60일 무변경 연장이 가장 깨끗하다.
3. 새 cohort를 만들고 싶다면 세 팔에 공통인 신호 gate 하나만 바꾼다. 어떤 gate인지는
   7일 이후 archive counterfactual로 정하고, 지금 특정 숫자를 임의 추천하지 않는다.
4. spread `0.02`, `$5`, H/5, 세 execution mode는 유지한다. 서로 다른 실행 방식이 처치축인
   실험에서 팔별로 다른 진입 값을 주면 실험이 무효가 된다.

## 6. Melon post-mortem

### 정확히 몇 건이 체결됐는가

| Account / Job | Volume arm | Confirmed BUY position | Confirmed SELL | 종결 상태 |
|---|---:|---:|---:|---|
| GOLDEN-FRUIT / `polybot-fruit` | high `$150k` | **1** | **1** | COMPLETED 1 |
| Lion / `polybot-lime` | mid `$50k` | **2** | **1** | COMPLETED 1, RESOLVED 1 |
| Wolf / `polybot-wolf` | low `$20k` | **2** | **1** | COMPLETED 1, RESOLVED 1 |
| 합계 |  | **5** | **3** | 5개 모두 종결, open/stuck 0 |

따라서 “거래 포지션 수”는 총 5건이고, BUY와 SELL 체결 row를 각각 세면 총 8건이다.
`PENDING_BUY`, `HOLDING`, `PENDING_SELL`은 모두 0이다.

### 체결별 결과

| Market | Arms | Entry volume / liquidity | Evidence | P&L |
|---|---|---:|---|---:|
| Bitcoin `$64k` | mid, low | `$82,644.70` / `$25,061.79` | BUY 5.43 @ 0.91, Yes 해결; SELL 없음 | 팔당 gross settlement assumption `+$0.4887` |
| Kai/Speed Aug 12 | high, mid, low | `$445,356.12` / `$96,295.17` | BUY·SELL exact confirmed, explicit zero fee | high `+$0.75372`, mid `+$0.74801`, low `+$0.74801` net |

strict confirmed BUY+SELL round trip의 합계 net은 **+$2.24974**다. Bitcoin 두 row의
`+$0.4887`씩은 confirmed BUY와 Yes resolution으로 계산한 gross assumption이며 fee/redeem이
완전히 입증되지 않아 strict net에 더하지 않는다.

세 팔의 Kai/Speed 거래는 동일 underlying signal의 계정별 반복이므로 독립 표본 세 건으로
세면 안 된다. 독립 시장 수는 두 개뿐이다. 2개가 모두 유리하게 끝났다는 사실만으로
58.0% 손익분기 승률을 이겼다고 주장할 수 없다.

### 진입 퍼널과 “너무 소극적인가”

| 지표 | High | Mid | Low |
|---|---:|---:|---:|
| RunAudit | 2,548 | 2,548 | 2,556 |
| SUCCESS / FAILED | 2,544 / 4 | 2,544 / 3 | 2,551 / 4 |
| Scanner candidate | **2** | **3** | **3** |
| Submitted / confirmed BUY | **1** | **2** | **2** |
| Runtime p50 / p95 | 2.44m / 6.33m | 2.03m / 6.36m | 2.73m / 6.32m |
| Start gap p50 / p95 | 5.00m / 6.48m | 5.00m / 6.51m | 5.00m / 6.45m |

volume axis가 실제로 나눈 것은 Bitcoin 한 건뿐이다. 이 시장의 volume `$82.6k`는 low와
mid를 통과하고 high를 통과하지 않는다. 반면 `$20k~$50k` 구간에서 low에만 들어온 거래는
0건이라 `$20k` 아래로 더 내릴 근거가 없다.

2026-08-12T21:27Z 한 cycle에는 세 팔 모두 두 candidate가 있었다.

1. Kai/Speed Aug 12: prior 0.821 → current 0.866, volume `$445.4k`
2. Kai/Speed Aug 13: prior 0.848 → current 0.873, volume `$305.6k`

`max_new_positions_per_cycle=1` 때문에 첫 시장만 주문했다. 둘째 시장은 이후 snapshot에서
0.9995까지 관측됐지만 실제 주문/fill이 없으므로 수익으로 계산하지 않는다. 이 한 건의
collision은 cap을 향후 검토할 이유는 되지만, 지금 cap을 2로 바꿔도 누적 BUY가
high/mid/low 2 / 3 / 3건에 불과하다. 저빈도의 본질은 첫 교차 자체가 드문 것이다.

FAILED run은 8월 5일과 11일에 몰린 Gamma `ChunkedEncodingError`/`ReadTimeout`이다.
그 다음 cycle에서 회복했고 최근 완결 2일 strict range는 팔별 실패 0이다. H/5보다 짧은
주기는 이 transport 문제를 고치지 못하고 API pressure만 높인다.

### Strict audit

- 8일 closed range: **HIGH 3 / MEDIUM 3**
  - 팔별 HIGH `failed_runs` 1개: 8월 11일 동시간대 timeout 2건
  - 팔별 MEDIUM `logs_missing` 1개: 실제 log 부재가 아니라 auditor path 연결 한계
- 최근 2일 closed range: **HIGH/CRITICAL 0**, MEDIUM `logs_missing` 3개만 존재
- 최근 2일의 completed round trip은 exact size/price와 fee coverage 100%다.

따라서 현재 체결의 lifecycle/P&L evidence는 사용할 수 있지만, full-period HIGH와 극소 표본
때문에 volume arm winner, 수익성, 신규 threshold를 정할 수 없다.

### Melon 결정

1. **2026-09-04 22:15 KST 전후의 day-30까지 현재 세 값을 유지한다.**
2. day 30에도 팔당 confirmed BUY가 30건 미만이면 사전 등록대로 `INCONCLUSIVE`다.
   곧바로 low arm 승리나 high arm 실패로 판정하지 않는다.
3. `$20k`보다 volume을 더 낮추지 않는다. 지금 low와 mid가 같아 volume이 추가 빈도를
   만들지 못했다.
4. H/5를 줄이지 않는다. start gap 중앙값은 5분이고 p95 runtime이 이미 6.3분이다.
5. 다음 cohort에서 cap 2를 검토하려면 새 `entry_signal_decisions`에서 candidate인데 trade로
   이어지지 않은 동시 signal이 반복되는지 먼저 센다. 이번 1회만으로 바꾸지 않는다.
6. 신호 envelope를 완화하려면 `entry_prob_min`, `hours_max` 등 **한 축만** 세 팔에 공통으로
   변경하고 새 cohort로 시작한다. 현재 데이터로 특정 완화값은 추천하지 않는다.

## 7. 관측성 결함 수정과 배포 검증

### 문제

`golden-melon/STRATEGY.md`는 첫 교차에서 탈락한 volume/liquidity/time을 DB에 남긴다고
정의했지만 실제 scanner는 broad 제외 사유를 로그에 합산만 했다. `skipped_markets`는
재진입 cooldown table이라 반사실 evidence를 넣으면 거래 동작까지 바뀌므로 사용할 수 없다.

### 수정

commit `ce515a656090cc920515ceadf112366cea5363ce`에서 다음을 적용했다.

- `entry_signal_decisions` 신규 table
- `(run_id, condition_id)` unique identity
- prior/current snapshot, price, gap, hours, sports clock, liquidity, volume,
  적용 min liquidity/volume, entry probability band, decision/reason 보존
- candidate가 아니어도 실제 첫 crossing이면 `low_liquidity`, `low_volume`,
  `price_out_of_band`, `missing_event_id` 등 최초 판정을 저장
- decision row가 참조한 prior/current snapshot을 compact/retention에서 보호
- below-threshold 시장은 기존 cheap gate 순서를 유지해 DB lineage 조회 부하를 늘리지 않음
- `skipped_markets`의 cooldown 의미와 모든 거래 parameter는 그대로 유지

과거 9일의 누락 row를 추정해 backfill하지 않았다. 새 table은 배포 시점 이후 evidence만
권위 있게 가진다.

### 검증

- `golden-melon`: **334 tests passed**
- 변경 파일 Ruff: PASS
- root strategy contract: **PASS (20 strategies)**
- 세 동기화 DB 사본에 migration 실행:
  - SQLite integrity `ok`
  - 기존 trades/order fills/sweeps 보존
  - trade snapshot reference 보존
  - 기존 compact policy의 예상 snapshot roll-up만 발생
- 배포 전 `polybot-fruit/lime/wolf`를 일시 중지하고 실행 중 cycle 종료 확인
- 세 Jenkins config는 복귀 후 적용 전 SHA-256과 byte-for-byte 동일
- 수동 deploy build:
  - Fruit `#2728 SUCCESS`
  - Lime `#9493 SUCCESS`
  - Wolf `#8346 SUCCESS`
- post-deploy 자연 timer build:
  - Fruit `#2730 SUCCESS`
  - Lime `#9495 SUCCESS`
  - Wolf `#8348 SUCCESS`
- 세 DB 재동기화 후 `entry_signal_decisions` table 존재, `quick_check=ok`, 기존
  trade 1 / 2 / 2와 confirmed fill 2 / 3 / 3 보존 확인

수동 세 build를 같은 초에 시작해 Gamma 429가 일시 발생했지만 기존 6회 retry 안에서 모두
완전한 389페이지 sweep으로 회복했다. 자연 timer build도 각각 RunAudit SUCCESS로 끝났다.

## 8. 다음 점검 프롬프트

Quince day 7 이후:

```text
polybot-bear, polybot-eco, polybot-tiger를 daily-rsync로 다시 동기화하고
2026-08-13 00:26 KST 새 Quince cohort의 day-7 health를 검증해줘.
CONFIRMED BUY, candidate, execution mode별 submitted price/fill role,
entry_signal funnel, cadence, strict audit를 확인하고 parameter는 evidence gate를
통과한 경우에만 제안해줘.
```

Melon day 30 이후:

```text
polybot-fruit(GOLDEN-FRUIT/high), polybot-lime(mid), polybot-wolf(low)를
daily-rsync로 다시 동기화하고 2026-08-05 22:14 KST 시작 Melon A/B/C의
day-30 회고를 해줘. CONFIRMED BUY/SELL, fee-complete net P&L,
독립 event cluster, entry_signal_decisions의 volume counterfactual,
candidate-to-order collision과 strict audit를 기준으로 판단해줘.
```

## 9. 보안 관찰

여섯 job 모두 anonymous `config.xml` read와 plaintext HTTP가 가능하고 live credential이
inline인 finding이 남아 있다. 실제 값은 출력하거나 문서에 기록하지 않았다. 사용자가 이전에
LAN/local accepted risk로 둔 항목이므로 이번 저빈도 판정이나 재가동 blocker로 사용하지 않았다.
