# 002 — Live strategy fleet sync and postmortem — 2026-08-10

작성일: 2026-08-10

Jenkins: `macmini-m5` (`192.168.50.23:8080`)

판정 기준시각: `2026-08-10T00:00:00Z`

회고 timezone: UTC

> 후속 메모 (2026-08-10): 이 snapshot 이후 운영자가 `polybot-orange`의 주문액을
> `$500`에서 `$5`로 낮췄다. 아래 Orange 수치와 유동성 진단은 source cutoff 당시의
> `$500` cohort를 설명하며, 변경 후 구간은 별도 cohort로 평가해야 한다. Blueberry·Melon·
> Quince의 `PENDING_BUY` 결함 수정과 재가동 절차는
> [`003-blueberry-melon-quince-pending-buy-fix-2026-08-10.md`](003-blueberry-melon-quince-pending-buy-fix-2026-08-10.md)에 기록했다.

## 0. 요청과 결론

요청은 Jenkins에서 현재 timer로 실행 중이며 wallet credential이 있는 live 전략만 DB와
bot/Jenkins log를 동기화하고, 시작일을 inventory에 기록한 뒤 7일 이상 운영된 전략만
회고하는 것이었다. `golden-pomegranate`, `polybot-kiwi-a`~`d`, shadow/simulation,
timer가 없는 job은 제외했다.

결론은 다음과 같다.

1. **대상 12개 job의 동기화와 checksum 검증은 모두 성공했다.** simulation DB와 제외 job
   경로는 plan에 하나도 없었다.
2. 7일 이상 운영된 `golden-cherry`, `golden-elderberry`, `golden-date`의 strict audit은
   모두 `CRITICAL/HIGH` issue 때문에 실패했다. 정확한 net P&L과 파라미터 tuning은 이 상태에서
   주장할 수 없다.
3. 그래도 exact confirmed BUY/SELL 수량이 일치하는 부분집합의 **gross P&L은 네 DB 모두
   음수**였다. 수익 전략이라는 근거는 없고, Date의 기존 폐쇄 판정은 유지된다.
4. “진입 기준이 너무 엄격한가?”가 fleet 전체의 주원인은 아니다. Yellow는 `$5` 전환 뒤
   약 2.5일에 67건, Elderberry는 현재 안전 cohort 약 7일에 90건이 생성됐다.
5. 신규 Blueberry/Melon/Quince 8개 live arm은 더 심각하다. **MATCHED + CONFIRMED BUY 18건
   전부가 수량 반올림 비교 오류로 `PENDING_BUY`에 고착**돼 exit 관리가 시작되지 않았다.
   파라미터를 완화하기 전에 신규 BUY를 막고 이 lifecycle 결함을 고쳐야 한다.

이 문서는 이전
[`001-golden-cherry-postmortem-2026-08-08.md`](001-golden-cherry-postmortem-2026-08-08.md)의
후속 정정판이기도 하다. 001은 당시 `daily-rsync verify`가 실패한 snapshot에서 BUY/SELL 현금
흐름을 넓게 합산해 Cherry 손실을 과대계상했다. 아래 수치는 검증된 새 snapshot과 strict audit의
exact round-trip 정의를 사용한다.

## 1. Sync scope와 무결성

### 대상

| Strategy | Jenkins job / runtime | Mode |
|---|---|---|
| `golden-cherry` | `polybot-yellow/default`, `polybot-orange/default` | live active |
| `golden-elderberry` | `polybot-cherry/default` | live active |
| `golden-date` | `polybot-red/default` | live DB, `close_only` |
| `golden-blueberry` | `polybot-eagle/blueberry-live-a-2pp`, `polybot-fox/blueberry-live-b-5pp` | live A/B |
| `golden-melon` | `polybot-wolf/polybot-melon-low`, `polybot-lime/polybot-melon-mid`, `polybot-fruit/polybot-melon-high` | live A/B/C |
| `golden-quince` | `polybot-bear/polybot-quince-passive`, `polybot-eco/polybot-quince-nearest`, `polybot-tiger/polybot-quince-cross` | live A/B/C |

### 명시적 제외

- accountless research: `golden-pomegranate`
- simulation: `polybot-kiwi-a`, `polybot-kiwi-b`, `polybot-kiwi-c`, `polybot-kiwi-d`
- shadow research: `polybot-shadow`
- no timer: `polybot-cat`, `polybot-dog`, `polybot-king`, `polybot-queen`

12개 개별 `scan → plan → sync → verify` 경로를 사용했다. 전체 job scan은 Pomegranate의
허용 root 밖 custom workspace를 발견해 fail closed했으므로, 그 뒤에는 확정된 live job만 개별
scan했다. 검토한 plan은 12개, 16,526 transfer artifact, 약 4.77 GB였고 `database_sim`,
`trades_sim`, 제외 job 경로는 모두 0개였다.

최종 결과:

- latest sync attempt: 12/12 `SUCCESS`
- verify: 12/12 `SUCCESS`
- checked artifact: 24,462
- checksum failure: 0
- `skipped_retention_deleted`: 0
- open artifact conflict: 0
- local `daily-rsync/data`: 약 19 GiB
- local free space: 약 268 GiB

### Strict 회고 evidence header

회고 기간은 완결된 UTC 일자만 사용해 `[2026-07-11T00:00:00Z,
2026-08-10T00:00:00Z)`로 고정했다. `--days 30 --as-of 2026-08-09`와 verified DB만
명시했다.

#### `polybot-yellow × golden-cherry × default`

- remote: `/Users/jongwoopark/.jenkins/workspace/polybot-yellow/golden-cherry/data/default/trades.db`
- local: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- SHA-256: `c295c9393eb89984a45461a113ae7c594b921ae163005ef77f6e251951ed8f5b`
- latest successful sync finished: `2026-08-10T12:18:41.754375Z`
- DB synced at: `2026-08-10T12:09:01.383014Z`
- source cutoff: `2026-08-10T12:08:42.318107Z`
- verify: `SUCCESS`, checked 3,197

#### `polybot-orange × golden-cherry × default`

- remote: `/Users/jongwoopark/.jenkins/workspace/polybot-orange/golden-cherry/data/default/trades.db`
- local: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- SHA-256: `95af001c1e074f33fba3791a25839b5903a09750d72dc39300de37e0a56103fc`
- latest successful sync finished: `2026-08-10T12:23:31.625302Z`
- DB synced at: `2026-08-10T12:19:45.384056Z`
- source cutoff: `2026-08-10T12:15:19.993784Z`
- verify: `SUCCESS`, checked 2,031

#### `polybot-cherry × golden-elderberry × default`

- remote: `/Users/jongwoopark/.jenkins/workspace/polybot-cherry/golden-elderberry/data/default/trades.db`
- local: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cherry/strategies/golden-elderberry/runtime/default/databases/latest/trades.db`
- SHA-256: `3a5a66943b7c5a85e1b0c3a922d719e5fe2084682ab636b3f1d9843b426eb544`
- latest successful sync finished: `2026-08-10T12:31:54.859444Z`
- DB synced at: `2026-08-10T12:31:52.122686Z`
- source cutoff: `2026-08-10T12:30:48.739540Z`
- verify: `SUCCESS`, checked 6,241

#### `polybot-red × golden-date × default`

- remote: `/Users/jongwoopark/.jenkins/workspace/polybot-red/golden-date/data/default/trades.db`
- local: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-red/strategies/golden-date/runtime/default/databases/latest/trades.db`
- SHA-256: `a66990db3f5689ba0abd263da9e56cc9f26714bae6412eff7d95f755cc7dcd75`
- latest successful sync finished: `2026-08-10T13:04:26.101068Z`
- DB synced at: `2026-08-10T13:04:20.125714Z`
- source cutoff: `2026-08-10T12:46:29.155314Z`
- verify: `SUCCESS`, checked 1,984

Raw audit bundle은 local-only 경로
`daily-rsync/data/retro/2026-08-09/<strategy>/retro-audit.{json,md}`에 보존했다.

### Sync 중 발견하고 수정한 catalog 결함

Yellow verify가 처음에는 실제 파일 SHA와 최신 catalog row가 일치하는데도 checksum mismatch를
냈다. 원인은 동일 원격 artifact/동일 local path를 가리키는 과거 source-key row가 2,359개
중복돼 stale checksum을 함께 검사한 것이었다.

- 적용 전 catalog backup:
  `daily-rsync/data/catalog-backups/catalog-before-source-key-coalesce-20260810T130746Z.sqlite3`
- 수정: catalog open 시 host-scoped canonical key를 재계산하고, 동일 identity는 가장 최근
  `synced_at` evidence로 병합하며 pin/conflict reference를 보존
- 결과: duplicate identity group 0, Yellow를 포함한 12개 verify 모두 성공
- 회귀 테스트 추가: current key-version marker가 있어도 stale duplicate를 병합하는 경우

## 2. 최초 운영 시점과 회고 대상

상세 표는 local inventory
`docs/local/jenkins-job-strategy-inventory.md`에 기록했다. 기준은 다음과 같다.

| Strategy | First observed live evidence | 판정 |
|---|---|---|
| `golden-cherry` / Yellow | at least 2026-03-30 13:11 UTC, first DB BUY | 회고 |
| `golden-cherry` / Orange | at least 2026-04-24 00:01 UTC, first DB BUY | 회고 |
| `golden-elderberry` | 2026-07-05 09:51 UTC, first retained Panic Fade cycle log | 회고 |
| `golden-date` | 2026-07-05 09:43 UTC, first retained cycle log | 회고 |
| `golden-blueberry` | 2026-08-05 13:13 UTC, first live run audit | 제외: 7일 미만 |
| `golden-melon` | 2026-08-05 13:14 UTC, first live run audit | 제외: 7일 미만 |
| `golden-quince` | 2026-08-05 13:15 UTC, first live run audit | 제외: 7일 미만 |

## 3. 30-day strict postmortem

`trades.realized_pnl`은 요청가/요청수량 기반이므로 성과로 쓰지 않았다. 아래 gross P&L은
audit이 인정한 exact confirmed BUY/SELL 수량 일치 round-trip만의 값이다. 다만 coverage와 fee
증거가 불완전하므로 **부분집합 gross 수치이지 계좌 net P&L이 아니다.**

| Strategy / job | Period BUY / close | Exact fill coverage | Exact subset gross P&L | Fee-known fill ratio | Strict result |
|---|---:|---:|---:|---:|---|
| Cherry / Orange | 81 / 49 | 19/49 = 38.8% | **-$430.46** | 13.6% | FAIL · C4/H5/M2 |
| Cherry / Yellow | 1,141 / 718 | 324/718 = 45.1% | **-$440.74** | 14.8% | FAIL · C4/H5/M2 |
| Elderberry | 328 / 198 | 137/198 = 69.2% | **-$18.77** | 22.2% | FAIL · C4/H5/M2 |
| Date | 1,202 / 592 | 413/592 = 69.8% | **-$758.05** | 23.1% | FAIL · C4/H5/M3 |

공통 blocker는 다음과 같다.

- completed trade의 confirmed BUY/SELL coverage 부족
- BUY/SELL confirmed quantity 불일치
- confirmed fill overflow 또는 domain 오류
- 미확정 POST intent와 stale reconciliation
- fee evidence 대량 누락
- FAILED/stale run과 schedule gap

이 issue가 하나라도 CRITICAL/HIGH이면 threshold sweep, parameter tuning, 승격 판단을 중단한다는
Evidence Contract를 적용했다.

### Golden Cherry

판정: **수익 전략이라고 볼 증거가 없다. threshold 완화 금지.**

- 두 DB의 exact 부분집합 gross P&L은 합계 **-$871.20**다. coverage가 39~45%이고 net fee가
  확정되지 않아 정확한 총손실 숫자는 말할 수 없지만, 양수 수익성 주장은 더더욱 불가능하다.
- July 회고의 해결 결과에서도 진입 가격대별 실현 확률은 시장가격과 거의 같아 진입 edge가
  확인되지 않았다. 손절/트레일링의 갭 실행과 execution reconciliation이 더 큰 문제였다.
- Yellow는 사용자가 검토했던 `POLYBOT_BUY_AMOUNT=250 → 5`가 이미
  `2026-08-08T00:53Z`부터 적용됐다. 해당 current config에서 약 2.5일 동안 trade 67건이
  생겼고, exact round-trip 55건의 gross는 `+$2.68`(+0.97%)지만 42건은 fee-complete가
  아니며 기간도 너무 짧다. 이 숫자는 수익성 증거가 아니다.
- 따라서 Yellow의 현재 문제는 “기준이 너무 엄격해 거래가 없다”가 아니다. 8월 9일 로그에서도
  5분 cycle마다 1~3개 후보가 반복 관측됐다.
- Orange는 반대다. current config가 `$500`, `min_liquidity=$30k`,
  `max_order_liquidity_ratio=0.002`여서 실효 하한이
  `max($30k, $500/0.002) = $250k`다. `2026-07-23T12:56Z` 이후 신규 trade가 0건이고,
  8월 9일에는 약 400개 시장을 매번 스캔해 후보 0개가 반복됐다.

권고:

- Yellow는 `$5`, `max_positions=10`, cycle 신규 1을 유지하고 증액하지 않는다.
- Orange는 `$500`로 계속 관측할 이유가 없다. 가장 안전한 선택은 ledger issue가 해소될 때까지
  `close_only`; 계속할 경우에도 `$5` hard-risk cohort로 재시작하는 편이 낫다. 이는 수익 개선
  tuning이 아니라 위험 축소이며, 유동성 universe까지 달라지는 새 cohort임을 명시해야 한다.
- `sell_threshold`, 시간창, 유동성 gate를 완화해 거래 수를 늘리지 않는다.

### Golden Elderberry

판정: **현재 데이터는 무수익/음수 쪽이지만 net 수익성 확정 불가. 진입 gate 완화 금지.**

- exact 부분집합 gross는 `-$18.77`; audit은 C4/H5로 실패했다.
- 안전 설정 `$5`, `max_positions=20`, `reentry_cooldown=168h`는
  `2026-08-03T14:12Z`부터 적용됐다. 이 current cohort에서 이미 trade 90건이 생겼으므로
  기준이 너무 엄격해 DB가 비는 상태가 아니다.
- current cohort의 exact round-trip 54건은 원가 `$267.84`, gross `-$9.61`
  (−3.59%)이고 fee-complete는 6건뿐이다. 조기 경고로는 음수지만 tuning 근거로 쓰기엔 부족하다.
- 기존 사전등록 H1(`drop_min/current_max/liquidity` 강화)은 해결 결과 편입 후에도 검정력이
  부족했다. 지금 gate를 좁히거나 넓히면 사후 선택이 된다.

권고:

- 현재 `$5 / max_positions 20 / cooldown 168h / TP 0.10 / SL -0.10` 유지.
- `drop_min`, `current_max`, `min_liquidity`, TP/SL을 바꾸지 않는다.
- 먼저 uncertain intent, stale reconciliation, quantity mismatch, fee coverage를 해결하고
  strict C/H=0인 새 cohort를 만든다. 그 전까지 신규 BUY를 막는 `close_only`도 합리적이다.

### Golden Date

판정: **폐쇄 판정 유지. 파라미터 추천 없음.**

- exact 부분집합 gross `-$758.05`; strict C4/H5.
- 기존 2026-07 verdict는 진입 edge −1.56pp, 14일 회전율 14.8배, 사전등록 gate 실패를 근거로
  폐쇄를 권고했다.
- Jenkins는 `2026-07-28T16:40Z`부터 `close_only`이고 그 뒤 신규 trade는 0건이다. 현재
  timer 실행은 전략 재가동이 아니라 wind-down/reconciliation 목적이다.

권고:

- `close_only`를 유지해 wallet/CLOB 대사와 redeem을 끝낸 뒤 TimerTrigger를 제거한다.
- 진입 band나 주문액을 조정해 다시 살리지 않는다.

## 4. 7일 미만 전략 health audit

성과 회고는 요청대로 하지 않았다. 대신 live safety 확인을 위해
`[2026-08-05T00:00Z, 2026-08-10T00:00Z)` 5-day strict health audit을 별도로 실행했다.
Raw bundle은 `daily-rsync/data/health/2026-08-09/<strategy>/`에 있다.

| Strategy / arm | Trades | Current status | Confirmed BUY |
|---|---:|---|---:|
| Blueberry A +2pp | 3 | `PENDING_BUY` 3 | 3 |
| Blueberry B +5pp | 1 | `PENDING_BUY` 1 | 1 |
| Melon low $20k | 1 | `PENDING_BUY` 1 | 1 |
| Melon mid $50k | 1 | `PENDING_BUY` 1 | 1 |
| Melon high $150k | 0 | none | 0 |
| Quince passive | 4 | `PENDING_BUY` 4 | 4 |
| Quince nearest | 4 | `PENDING_BUY` 4 | 4 |
| Quince cross | 4 | `PENDING_BUY` 4 | 4 |

### 공통 PENDING_BUY lifecycle 결함

18개 BUY 모두 다음 조건을 동시에 만족한다.

- venue response/latest order status: `MATCHED`
- `order_fills.status='CONFIRMED'`
- confirmed fill 합계 = `latest_size_matched` (오차 1e-6 이내)
- `needs_reconciliation=0`
- 그런데 `trades.status='PENDING_BUY'`

원인은 Blueberry/Melon/Quince의 `TradeRepository.get_exact_order_fill_evidence()`가 full fill을
판정할 때 venue가 실제로 quantize한 token amount가 아니라 quantize 전 `requested_size`와
`latest_size_matched`를 `abs_tol=1e-6`로 비교하기 때문이다.

예: 요청 `5.434782...` shares → venue MATCHED/CONFIRMED `5.43` shares. 실제 18건 모두
confirmed fill과 matched size는 같지만 requested size와는 `0.001818~0.007126` shares 차이가
났다. 그 결과 로그에는 `state=confirmed full=False detail=confirmed_partial_or_unreconciled`가
매 cycle 반복되고, HOLDING 전환과 exit check가 실행되지 않는다.

현재 확인되는 체결 원금은 18건 합계 약 **$88.93**다. 금액은 작지만 exit lifecycle이 꺼진
실포지션이므로 성과 실험은 무효 상태다.

운영 권고:

1. 8개 신규 live job을 우선 `close_only` 또는 timer pause로 바꿔 추가 BUY를 막는다.
2. 각 wallet의 실제 token balance와 18개 exact order ID를 대사한다.
3. full-fill 비교 기준을 BUY의 venue token amount(`taking_amount`) 또는 주문 직전 확정한
   quantized token size로 바꾸고, fractional `$5 / price` 회귀 테스트를 세 프로젝트에 넣는다.
4. 기존 DB를 삭제하지 말고 수정 commit/source digest로 새 cohort를 시작한다.
5. 정상 HOLDING/exit evidence가 쌓인 뒤 원래 사전등록 기간 30일과 arm당 confirmed BUY 30건
   gate를 다시 센다.

### 파라미터를 지금 바꾸지 않는 이유

- Blueberry의 3대1은 +2pp/+5pp 처치가 실제로 다른 빈도를 만드는 초기 관측이며 5일로 판정할
  수 없다.
- Melon high 0건은 `$150k` volume treatment 자체의 결과다. 지금 완화하면 A/B/C가 무효다.
- Quince는 세 arm 모두 같은 4개 signal을 받아 paired design이 작동했다. 처치축은 execution
  mode이므로 threshold를 바꾸면 안 된다.
- 세 전략 모두 pre-registered 30-day gate가 있고, 현재는 7일도 안 됐으며 exit가 하나도
  관리되지 않았다.

## 5. 최종 parameter/operation recommendation

| Strategy | 권고 |
|---|---|
| Cherry / Yellow | 이미 `$5` 적용. 그대로 유지하고 threshold 완화/증액 금지. ledger strict gate 복구 우선. |
| Cherry / Orange | `$500` 유지 근거 없음. `close_only` 우선; 계속 관측한다면 `$5` 새 cohort로 위험 축소. |
| Elderberry | `$5`, max 20, cooldown 168h, TP/SL 유지. 진입 gate 변경 금지. |
| Date | `close_only` 유지 후 wind-down 완료 시 timer 제거. 재튜닝하지 않음. |
| Blueberry/Melon/Quince | 즉시 신규 BUY 차단 → 수량 quantization lifecycle 수정 → 기존 사전등록 파라미터 그대로 새 cohort. |

따라서 질문에 대한 짧은 답은 이렇다: **거래가 적다는 이유로 기준을 완화하지 말아야 한다.**
Yellow와 Elderberry는 이미 충분히 자주 거래하고, Orange의 0건은 주문액이 유동성 gate를 끌어올린
결과이며, 신규 전략은 signal 부족보다 lifecycle bug가 먼저다.

## 6. 보안 관찰

Jenkins 22개 job 모두 anonymous `config.xml` 조회와 plaintext HTTP가 가능했고, 16개 config에서
inline credential-like assignment가 탐지됐다. 값은 어떤 보고서에도 기록하지 않았다.

권고는 Jenkins Credentials Binding, secret 참조 전 `set +x`, anonymous configure 차단,
LAN TLS/auth 적용이다. 이 작업에서는 Jenkins 설정이나 credential을 변경하지 않았다.
