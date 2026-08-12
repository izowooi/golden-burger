# 015 — Golden Quince 실행축 수정·클린 재시작 — 2026-08-13

작성일: 2026-08-13

대상: Jenkins `polybot-bear` / `polybot-eco` / `polybot-tiger`, strategy
`golden-quince`, runtime `polybot-quince-passive` / `polybot-quince-nearest` /
`polybot-quince-cross`

이 문서는 014 진단에서 확인한 “세 execution arm의 BUY가 모두 같은 TAKER 주문으로
나간 결함”을 수정하고, 기존 원격 DB를 한 번만 지운 뒤 새 cohort를 실제 Jenkins에서
기동·검증한 기록이다.

## 0. 결론

```text
Decision: IMPLEMENTED_AND_DEPLOYED
Code: b8947d2 (main/origin-main push 완료)
One-time clean: 3/3 SUCCESS
First natural H/5 cycle: 3/3 SUCCESS
Steady Jenkins config: enabled, H/5, concurrent=false, clean 없음
New DB: 3/3 quick_check=ok, sync/verify SUCCESS, 단일 config/commit
Live BUY evidence: 아직 0건 — maker/taker 분리는 첫 공통 후보 이후 검증 필요
Profitability: 판단 보류
```

요청한 구조대로 세 job 모두 첫 회에만 `Clean before checkout`을 사용해 기존 workspace의
untracked DB·bot log를 삭제했다. clean build 성공 직후 이 옵션을 제거하고 실제
`TimerTrigger`를 `H/5 * * * *`로 복원했다. 현재 세 job은 모두 활성 상태다.

새 DB에는 기존 trade/order/fill이 섞이지 않았다. 안정 경계에서 확인한 run은
Bear/Eco/Tiger 각각 5/5/4개이며 전부 `SUCCESS`, config와 Git commit은 arm별 하나뿐이다.
다만 이 짧은 구간에는 매수 후보가 한 건도 없어, 수정된 passive/nearest/cross가 실제
체결에서 MAKER/TAKER로 갈리는지는 아직 확인할 수 없다. 수익성도 판단할 수 없다.

## 1. 코드 수정

수정 commit:

- `b8947d2f694b23781b19ab49da02d69462000815`
- commit message: `Golden Quince 실행축과 주문 수명주기 수정`
- `main`과 `origin/main`에 push 완료

핵심 변경은 다음과 같다.

1. BUY 가격 선택을 실제 order book 기준으로 분리했다.
   - `passive`: best ask보다 엄격히 낮은 tick에만 제출한다. 가능한 경우 best bid에 합류한다.
   - `nearest`: midpoint를 가장 가까운 tick으로 반올림한다.
   - `cross`: 검증된 ask-depth cap을 사용하고 best ask 이상으로 제출한다.
2. Trader가 세 arm 모두에 공통 ask-depth 가격을 넘기던 경로를 제거했다. 실제 선택된
   order limit으로 `$5` 수량을 계산하고 제출가를 기록하며, 원래 depth cap은 별도
   `depth_limit_price_at_buy` evidence로 보존한다.
3. Trader config와 CLOB client의 execution mode가 다르면 주문 전에 fail closed한다.
4. GTC BUY 잔량의 수명을 기존 `max_snapshot_gap_minutes=15`와 맞췄다.
   - 15분 내 미종결 주문은 계속 대사한다.
   - TTL 뒤에는 취소 후 authoritative terminal 상태를 다시 대사한다.
   - zero fill은 `UNFILLED`, terminal partial/full fill은 정확한 confirmed share로
     `HOLDING`에 진입한다.
5. config와 runtime log에 execution mode, pending BUY TTL, 실험자금과 kill switch를
   명시해 배포 상태를 바로 확인할 수 있게 했다.

동일 book 예제의 회귀 테스트는 다음 분리를 보장한다.

```text
best bid 0.90 / midpoint 0.91 / best ask 0.92
passive 0.90 / nearest 0.91 / cross 0.92
```

## 2. 코드 검증

```text
golden-quince pytest: 343 passed
19-project strategy contract verifier: PASS
git diff --check: PASS
```

추가된 테스트는 같은 candidate의 세 submitted price 분리, locked book fail-closed,
fresh/stale pending BUY, zero/partial/full terminal reconciliation, cancel과 fill race,
Trader/CLOB mode mismatch를 포함한다.

## 3. Jenkins 변경 절차와 결과

### 3.1 한 번만 적용한 clean 상태

세 job을 비활성화한 상태에서 다음 precondition을 확인한 후 임시 config를 적용했다.

- TimerTrigger 제거
- Git SCM extension `CleanBeforeCheckout` 정확히 1개
- `POLYBOT_EXPERIMENT_CAPITAL_USDC=100`을 세 팔 모두 `200`으로 정렬
- arm별 execution mode와 runtime은 유지
- 수동 build를 arm별 정확히 한 번 실행

| Jenkins job | Treatment / runtime | Clean build | Start UTC | KST | Duration | Result |
|---|---|---:|---|---|---:|---|
| `polybot-bear` | passive / `polybot-quince-passive` | `#7388` | `2026-08-12T15:26:14Z` | 2026-08-13 00:26:14 | 178.3s | SUCCESS |
| `polybot-eco` | nearest / `polybot-quince-nearest` | `#9278` | `2026-08-12T15:26:19Z` | 2026-08-13 00:26:19 | 172.9s | SUCCESS |
| `polybot-tiger` | cross / `polybot-quince-cross` | `#8893` | `2026-08-12T15:26:22Z` | 2026-08-13 00:26:22 | 170.4s | SUCCESS |

세 console 전체를 민감값을 출력하지 않고 검사했다. 모두 다음 evidence를 포함했다.

- `git clean -fdx`
- checkout commit `b8947d2`
- 새 live SQLite 생성
- `compact-v1`
- 실험자금 `$200`, drawdown kill switch `-$40`
- pending BUY remainder TTL 15분
- arm별 올바른 execution mode
- `RUN_AUDIT 성공`과 `Finished: SUCCESS`

첫 clean cycle은 arm별 raw market 31,963개, qualified market 18,071개,
research snapshot 58개였으며 후보와 BUY는 0건이었다.

### 3.2 steady-state 복원

clean build 3개가 모두 끝난 뒤 다음 상태로 즉시 복원했다.

- `CleanBeforeCheckout` 제거
- 실제 `TimerTrigger=H/5 * * * *`
- `disabled=false`
- `concurrentBuild=false`
- `$5`, 24h, 0.90–0.94, liquidity `$10,000`, volume24h `$2,000` 유지
- `experiment_capital_usdc=200` 유지

첫 자연 timer cycle은 다음과 같다.

| Jenkins job | First natural build | Cause | Duration | Clean 재실행 | 신규 DB 재생성 | Result |
|---|---:|---|---:|---|---|---|
| `polybot-bear` | `#7389` | Started by timer | 155.9s | 없음 | 없음 | SUCCESS |
| `polybot-eco` | `#9279` | Started by timer | 156.1s | 없음 | 없음 | SUCCESS |
| `polybot-tiger` | `#8894` | Started by timer | 108.9s | 없음 | 없음 | SUCCESS |

세 로그 모두 기존 DB를 이어 쓰고 `RUN_AUDIT 성공`, cursor-complete sweep, 후보/BUY 0건으로
끝났다. 따라서 “매 cycle clean으로 DB lineage가 끊기는” 과거 실수는 재발하지 않았다.

최종 read-only config 검사 시각은 `2026-08-12T15:55:37Z`다.

| Jenkins job | Final config SHA-256 | Enabled | Timer | Clean | Capital | Mode |
|---|---|---|---|---|---:|---|
| `polybot-bear` | `7d5ef41c237fcb0b74ee952db6696acb2876cb63b2c58ca51b67df20039c0ca9` | yes | `H/5` | 없음 | $200 | passive |
| `polybot-eco` | `faa63c5739638c98a8fe69c91a3acefdb5f65fb2de3d7e70cf91fdc772ed14a2` | yes | `H/5` | 없음 | $200 | nearest |
| `polybot-tiger` | `0e9bd7abe72643d7b3b407719c163cba222c7d92afbfec63ce7bb636ae205e6e` | yes | `H/5` | 없음 | $200 | cross |

기존 signature-type 한글 주석이 Jenkins XML 왕복 중 깨져 실행에 영향 없는 ASCII 주석으로만
정리했다. 환경변수 값과 실행 동작은 바꾸지 않았다.

## 4. 새 평가 cohort 경계

clean build가 만든 DB의 첫 live RunAudit을 정확한 시작 경계로 사용한다.

| Arm | Start UTC | KST | Config hash |
|---|---|---|---|
| passive | `2026-08-12T15:26:26.842488Z` | 2026-08-13 00:26:26 | `510bb2f76da52c8589faeef43c9abab14da1d89a92b08c2fd14d33fd96fb2253` |
| nearest | `2026-08-12T15:26:32.639191Z` | 2026-08-13 00:26:32 | `55dfcd6ccf7eb2bc917e4220a2a99090285faa88f4530568d19cfe9603d0876d` |
| cross | `2026-08-12T15:26:34.043211Z` | 2026-08-13 00:26:34 | `0ab45d416eab6f80f22864616385410781f7015e375d03e2f4b00bbc50b782ef` |

운영 메모에서는 세 팔 모두 **2026-08-13 KST 시작**으로 간주한다. 최초 점검은
2026-08-20 00:27 KST 이후, 30일 점검은 2026-09-12 00:27 KST 이후가 적절하다.
과거 2026-08-05 시작 DB는 원격 workspace에서 의도적으로 삭제됐으며 이번 cohort에
마이그레이션하지 않았다. 기존 `daily-rsync` catalog/log 이력은 로컬 provenance로 남지만
최신 canonical DB와 성과 cohort에는 섞지 않는다.

## 5. daily-rsync 동기화와 무결성

세 job을 안정 경계에서 별도 scan/plan/sync/verify했다. 첫 Bear 시도는 다음 H/5 cycle이
DB에 쓰는 순간 source fingerprint가 변해 `PARTIAL`로 안전 중단됐다. 기존 local latest는
덮어쓰지 않았고, cycle 종료 후 새 plan으로 재시도해 최신 시도와 최신 성공 시도 모두
아래 `SUCCESS` 상태가 됐다.

### Bear / passive

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-bear/golden-quince/data/polybot-quince-passive/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-quince/runtime/polybot-quince-passive/databases/latest/trades.db`
- SHA-256: `d2a8d0c2529d15d3cbd9505e015ea91d39efcae24ee83464b52aa2679f0f9809`
- Sync run: `2549817874dd4c589f333d514e45c305`, SUCCESS
- Sync finished: `2026-08-12T15:53:50.263074Z`
- Source cutoff: `2026-08-12T15:53:25.137579Z`
- DB synced at: `2026-08-12T15:53:49.038896Z`
- Verify: SUCCESS, 1,641 checked, failure/retention skip/conflict 0

### Eco / nearest

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-eco/golden-quince/data/polybot-quince-nearest/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-eco/strategies/golden-quince/runtime/polybot-quince-nearest/databases/latest/trades.db`
- SHA-256: `104c3ec7ef2e4a33a29b152b3b0f8022a60f454522e96bae19d0d67917a6f9e6`
- Sync run: `57590441f1ef410b952ff96cefa28ded`, SUCCESS
- Sync finished: `2026-08-12T15:54:24.783149Z`
- Source cutoff: `2026-08-12T15:53:25.742123Z`
- DB synced at: `2026-08-12T15:54:23.200588Z`
- Verify: SUCCESS, 2,176 checked, failure/retention skip/conflict 0

### Tiger / cross

- Remote DB: `/Users/jongwoopark/.jenkins/workspace/polybot-tiger/golden-quince/data/polybot-quince-cross/trades.db`
- Verified local DB: `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-tiger/strategies/golden-quince/runtime/polybot-quince-cross/databases/latest/trades.db`
- SHA-256: `2592a2aac7b1e18a198ff52c2d1b10413ec46e17441821363f4ca757f6194ad7`
- Sync run: `9d9006b27b5c4ab694f91708978bcfa8`, SUCCESS
- Sync finished: `2026-08-12T15:54:35.077480Z`
- Source cutoff: `2026-08-12T15:45:54.706857Z`
- DB synced at: `2026-08-12T15:54:33.945425Z`
- Verify: SUCCESS, 1,632 checked, failure/retention skip/conflict 0

## 6. 새 DB 내부 결과

| Metric | Bear passive | Eco nearest | Tiger cross |
|---|---:|---:|---:|
| SQLite `quick_check` | ok | ok | ok |
| Config cohort | 1 | 1 | 1 |
| Git commit | 1 | 1 | 1 |
| RunAudit SUCCESS / total | 5 / 5 | 5 / 5 | 4 / 4 |
| Cursor-complete sweep | 5 / 5 | 5 / 5 | 4 / 4 |
| Pages per sweep | 314–320 | 314–320 | 314–320 |
| Raw markets per sweep | 31,332–31,963 | 31,332–31,963 | 31,332–31,963 |
| Snapshots | 294 | 294 | 236 |
| Trades | 0 | 0 | 0 |
| Order submissions | 0 | 0 | 0 |
| Order fills | 0 | 0 | 0 |

세 DB 모두 `compact-v1`, live, lifecycle active, `$5`, capital `$200`, 24h,
liquidity `$10,000`, volume24h `$2,000`, pending TTL 15분이다. config hash와 mode만
사전 등록한 arm별로 다르다.

## 7. 로그 관측과 남은 제한

- 모든 검사 cycle은 cursor-complete였고 RunAudit 실패가 없다.
- `POST /auth/api-key` 400 뒤 `GET /auth/derive-api-key` 200으로 초기화되는 fallback이
  반복된다. build와 CLOB 초기화는 성공하므로 이번 배포 blocker는 아니다.
- 일부 cycle에서 Gamma 429 retry가 있었지만 retry 후 성공했다.
- Bear `#7392`와 Eco `#9282`는 Gamma full scan이 느려 각각 435.3s, 430.9s가 걸렸다.
  `concurrentBuild=false`라 중첩 실행은 없지만 이 구간에는 유효 cadence가 10분으로
  늘어날 수 있다. 이번 요청대로 H/5를 유지하되, 향후 빈도 분석에서는 실행시간과
  start gap을 함께 본다.
- 아직 candidate/BUY가 0건이라 passive=MAKER, cross=TAKER라는 1차 종점을 live evidence로
  확인하지 못했다. 단위·통합 테스트만 통과한 상태이므로 첫 공통 BUY 직후 반드시
  submitted price, best bid/ask, fill role, partial-fill lifecycle을 세 팔에서 대조한다.
- 거래가 없으므로 strict 성과 audit과 parameter tuning은 수행하지 않았다. 현재 gate를
  동시에 바꾸면 구현 수정의 효과와 signal 변경 효과를 분리할 수 없으므로 그대로 둔다.

## 8. 다음 점검 기준

현재 운영자가 추가로 Jenkins를 수정할 필요는 없다. 세 job은 이미 steady state다.

첫 candidate 또는 BUY가 생긴 뒤 다음을 확인한다.

1. 같은 signal에서 passive/nearest/cross의 submitted limit이 실제로 다르다.
2. passive는 best ask 미만이고 cross는 best ask 이상이다.
3. `order_fills.liquidity_role`이 passive와 cross에서 의도대로 갈린다.
4. passive partial fill이 15분을 넘으면 잔량 취소 후 exact confirmed shares만
   `HOLDING`으로 남고, zero fill은 `UNFILLED`가 된다.
5. 세 팔의 후보 분모가 같은지, 장시간 cycle로 arm별 signal 시각이 벌어지지 않았는지 본다.

day-7에도 passive CONFIRMED BUY가 7건 미만이면 사전 등록 runbook대로 60일까지 무변경
연장하거나, 세 팔에 동일한 한 축만 완화하는 새 cohort를 설계한다. 첫 체결 evidence를
확인하기 전에는 liquidity/volume/spread/entry threshold를 조정하지 않는다.

재점검 요청 예시:

```text
Golden Quince 세 잡을 다시 동기화해서 새 cohort의 첫 공통 BUY를 찾아주세요.
passive/nearest/cross의 submitted price, best bid/ask, MAKER/TAKER,
partial-fill과 PENDING_BUY 15분 lifecycle을 비교해주세요.
```
