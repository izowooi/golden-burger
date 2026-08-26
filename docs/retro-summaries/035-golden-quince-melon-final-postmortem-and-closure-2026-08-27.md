# 035 — Golden Quince·Melon 최종 회고와 운영 종료 — 2026-08-27

작성일: 2026-08-27 KST

## 0. 결론

두 전략 모두 현재 live 배포는 **폐쇄**하는 것이 타당하다. 파라미터를 완화하거나 주문액을
`$5 → $10`으로 올리는 것은 권고하지 않는다.

| 전략 | 최종 판정 | 핵심 근거 |
|---|---|---|
| Golden Quince | **운영 폐쇄, 연구 결과는 보존** | 실행 처치의 방향은 사전 예측과 일치했지만 passive CONFIRMED BUY가 4건뿐이고 세 arm의 확정·해결 포함 경제손익이 모두 음수다. 현재 발생 속도에서는 모든 거래가 최대 이익이어도 `$10`으로 월 10%에 도달할 수 없다. |
| Golden Melon | **운영 폐쇄 확정** | arm별 BUY가 1/5/5건뿐이다. 11개 arm row가 모두 이익이었으나 독립 event는 5개이고 손절 tail을 한 번도 관측하지 못했다. 실제 계좌 자금 기준 월 환산은 Mid 약 1.57%, Low 약 0.62%이며 `$10` 단순 배율도 목표에 못 미친다. |

이는 두 시장 가설을 통계적으로 완전히 기각했다는 뜻은 아니다. strict audit에 `HIGH`가
남았고 사전 등록 표본 30건도 충족하지 못했다. 다만 사용자의 운영 목표인 **월 +10%**를
현재 live 구조가 달성할 가능성은 거래 발생 속도의 낙관적 상한만으로도 배제된다.
표본을 만들기 위해 방어 조건을 완화하면 같은 실험의 개선이 아니라 다른 위험 가설이 된다.

## 1. Evidence 경계와 동기화

- Quince 성과 구간: `[2026-08-13T00:00:00Z, 2026-08-26T00:00:00Z)`, 13일
- Melon 성과 구간: `[2026-08-05T00:00:00Z, 2026-08-24T00:00:00Z)`, 19일
- timezone: 성과는 UTC half-open range, Jenkins 표시는 KST
- Quince는 2026-08-13의 one-time clean 재시작 cohort만 주 평가 대상으로 삼았다.
- Melon은 timer가 제거된 2026-08-24 이후를 수익 기간에 억지로 포함하지 않았다.
- `order_fills.status='CONFIRMED'`의 실제 size·VWAP·known fee만 체결 손익에 사용했다.
- final Gamma payout을 쓴 행은 actual SELL/redeem과 섞지 않고
  `settlement_pnl_assumption`으로 분리했다.
- 사용자 제공 Slack NAV는 계좌 전체 mark·cash·수동 거래·미상환 payout이 섞일 수 있어
  봇 DB 손익과 대체하지 않았다.

작업 전 MacBook 가용 공간은 약 203GiB, Mac Mini는 약 58.6GiB였다. scan 예상량은
Bear 266MiB, Eco 1.07GiB, Tiger 265MiB, Fruit 410MiB, Lime 562MiB, Wolf 375MiB로
안전하게 수용 가능했다. 최종 incremental sync 6건은 모두 `SUCCESS`, failed artifact는
0건이었다.

| Jenkins / runtime | verified local DB | SHA-256 | source cutoff / sync finished UTC | verify |
|---|---|---|---|---|
| `polybot-bear` / `polybot-quince-passive` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-bear/strategies/golden-quince/runtime/polybot-quince-passive/databases/latest/trades.db` | `9585c534b46a6772be4d7b14c7df00acb75bf7eae74a8677f67ae1c6d6774fc0` | `2026-08-26T17:55:14.589179Z` / `2026-08-26T17:58:35.415615Z` | SUCCESS · 5,235 |
| `polybot-eco` / `polybot-quince-nearest` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-eco/strategies/golden-quince/runtime/polybot-quince-nearest/databases/latest/trades.db` | `0cfc21aaccccd6df57bcb0647164cb906e9befde2e81e9aa33bf09113f31e09c` | `2026-08-26T17:55:12.404023Z` / `2026-08-26T18:03:29.776056Z` | SUCCESS · 6,116 |
| `polybot-tiger` / `polybot-quince-cross` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-tiger/strategies/golden-quince/runtime/polybot-quince-cross/databases/latest/trades.db` | `54ec8abc2b879c315696984eaa61915726f9fd29165fafe3564f7cd8b87de948` | `2026-08-26T17:55:16.153900Z` / `2026-08-26T17:58:35.980135Z` | SUCCESS · 5,058 |
| `polybot-fruit` / `polybot-melon-high` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-fruit/strategies/golden-melon/runtime/polybot-melon-high/databases/latest/trades.db` | `d724c87d1a2a2a6fe2ea267beedc50338d81d4b4df37a194e4be500cf28555a2` | `2026-08-26T17:56:19.063222Z` / `2026-08-26T18:02:20.666118Z` | SUCCESS · 5,442 |
| `polybot-lime` / `polybot-melon-mid` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-lime/strategies/golden-melon/runtime/polybot-melon-mid/databases/latest/trades.db` | `ef578327466d46ab28843b99825c8854fdd2dd1f713b60541a74e42a12a79845` | `2026-08-26T17:56:17.922574Z` / `2026-08-26T18:02:46.226951Z` | SUCCESS · 5,441 |
| `polybot-wolf` / `polybot-melon-low` | `daily-rsync/data/sources/macmini-m5/jobs/polybot-wolf/strategies/golden-melon/runtime/polybot-melon-low/databases/latest/trades.db` | `7e45e48e835897d77996ecfef13c76c3468fe59dc48f9cda1138a1a207131166` | `2026-08-26T17:56:19.991313Z` / `2026-08-26T17:58:40.588979Z` | SUCCESS · 4,476; 과거 console retention skip 17 |

모든 DB의 `PRAGMA quick_check`는 `ok`였다. Wolf의 17건은 Jenkins에서 이미 보존기간이
지나 삭제된 과거 console로, DB·bot log와 이번 종료 검증에는 영향을 주지 않는다.

## 2. Strict evidence gate

최종 동기화 DB로 `polybot-retro audit --strict`를 다시 실행했다.

| 전략 | 결과 | 주요 HIGH |
|---|---:|---|
| Quince | HIGH 15, MEDIUM 3 | arm별 FAILED run 93~94, 최대 SUCCESS gap 7.83h, historical sweep/catalog·compact archive coverage 부족 |
| Melon | HIGH 6, MEDIUM 3 | arm별 FAILED run 96~98, 최대 SUCCESS gap 13.24~13.25h |

가장 큰 공통 공백은 2026-08-20 Gamma HTTP 451 구간이다. 당시 약 7.83시간의 first-crossing을
복원할 수 없다. Melon audit의 더 큰 13.24시간 값에는 연속 SUCCESS 기준과 추가 네트워크
실패가 함께 반영됐다. `logs_missing` MEDIUM은 DB 단독 audit가 daily-rsync의 별도 log root를
자동 연결하지 못한 것이며, 실제 bot/Jenkins log는 catalog에 존재하고 별도로 검사했다.

따라서 이 문서에서는 threshold의 우열, 장기 기대수익 또는 live 증액을 통계적으로
추정하지 않는다. 아래 수치는 확인된 주문 lifecycle 진단과 “현재 운영 목표를 달성할 수
있는가”라는 throughput 상한 판정에만 쓴다.

## 3. Golden Quince — 실행 실험의 성공과 수익 전략의 실패

### 3.1 운영 건강성

clean cohort 이후 arm별 run은 대략 3,830건 성공, 93~94건 실패였다. 최대 open state는
arm별 0~2였고 `max_positions`, open-notional 또는 event cap에 걸린 cycle은 0건이었다.
거래가 적은 원인은 cap이 아니라 first crossing과 fresh spread/depth를 모두 만족하는
시장이 적었기 때문이다.

### 3.2 사전 등록 실행 endpoint

| Arm | BUY intent | CONFIRMED BUY | fill rate | BUY MAKER | decision midpoint 대비 entry cost | 최종 경제손익 |
|---|---:|---:|---:|---:|---:|---:|
| Bear / passive | 9 | 4 | 44.4% | 4/4 = 100% | **-38.0bps** | **-$1.141530** |
| Eco / nearest | 9 | 6 | 66.7% | 5/6 = 83.3% | **-6.5bps** | **-$0.307260** |
| Tiger / cross | 7 | 7 | 100% | 0/7 = 0% | **+47.3bps** | **-$3.579479** |

사전 예측인 `fill rate: passive < nearest < cross`, `MAKER: passive > nearest > cross`,
`entry cost: passive < nearest < cross`는 모두 관측 방향과 일치했다. 즉 execution mode
구현은 의도대로 작동했다. 그러나 primary gate인 passive CONFIRMED BUY 30건에는 크게
못 미치므로 execution edge 자체는 공식적으로 **INCONCLUSIVE**다.

체결 뒤 15/60분 midpoint drift도 arm당 3~4건뿐이라 역선택을 판정할 수 없다. 무엇보다
실행비용 수십 bps의 이점보다 방향성 결과와 stop tail이 손익을 더 크게 지배해 세 arm
모두 경제손익이 음수였다.

### 3.3 Tiger `PENDING_SELL` 두 건 복구

기존 코드는 SELL 주문 자체가 전량 체결됐더라도 그 주문 수량이 원래 BUY와 다르면 무조건
`PENDING_SELL`을 유지했다. 실제로 다음 두 유형이 고착됐다.

- BUY 5.31주 중 4.45주만 확정 매도되고 0.86주가 남은 terminal partial SELL
- BUY 5.37주 중 5.362807주가 확정 매도되고 0.007193주 dust가 남은 near-full SELL

commit `1dca5e6`에서 다음 증거 계약으로 수정했다.

1. 실제 매도분은 CONFIRMED SELL VWAP·fee로 계산한다.
2. 남은 수량만 Gamma의 `closed=true` + one-hot final `0/0.5/1` payout을 사용한다.
3. final proof가 없으면 계속 `PENDING_SELL`; SELL이 BUY를 초과하면 fail-closed한다.
4. 혼합 손익은 `settlement_pnl_assumption`에 두고 actual `realized_pnl`로 위장하지 않는다.

Tiger `#12822`에서 첫 행은 final NO로 `-$3.113800`, 둘째는 final YES로 `+$0.381341`을
기록하며 두 건 모두 `RESOLVED`가 됐다. 이어 commit `a47da22`에서 cycle summary가 이를
`sold`가 아니라 `resolved`로 분류하도록 고쳤다. 전체 355개 테스트가 통과했다.

최종 Tiger DB는 `COMPLETED 4 / RESOLVED 3 / open 0`, 경제손익은
`realized -$1.276620 + settlement -$2.302859 = -$3.579479`다.

### 3.4 `$10`으로도 목표가 불가능한 이유

가장 낙관적으로 모든 진입이 `0.90 → 0.98`에 성공한다고 해도 `$5` 한 건의 최대 이익은
약 `$0.444`다. 13일 동안 4/6/7건의 체결 속도를 30일로 선형 환산하면 `$10` 주문의
절대 낙관 상한도 Bear 약 2.7%, Eco 약 4.1%, Tiger 약 4.8%/월이다(각 `$300` 계좌 기준).
손절·fee·partial fill을 모두 0으로 둔 상한이므로 실제 10%와의 차이는 더 크다.

따라서 `$10` arm D를 추가할 근거가 없다. 이는 단순히 표본이 모자란 문제가 아니라
현재 신호 빈도와 payout 폭의 곱이 사용자의 수익 목표보다 작은 문제다.

## 4. Golden Melon — 전승처럼 보이지만 운용 목표에는 부족

### 4.1 실제 결과

| Arm | CONFIRMED BUY | terminal 상태 | confirmed SELL P&L | resolution assumption | 경제손익 | 실제 계좌 기준 30일 단순 환산 |
|---|---:|---|---:|---:|---:|---:|
| Fruit / High `$150k` | 1 | COMPLETED 1 | +$0.753720 | $0 | **+$0.753720** | 약 **+0.40%** / `$300` |
| Lime / Mid `$50k` | 5 | COMPLETED 3, RESOLVED 2 | +$1.934310 | +$1.056230 | **+$2.990540** | 약 **+1.57%** / `$300` |
| Wolf / Low `$20k` | 5 | COMPLETED 3, RESOLVED 2 | +$1.949760 | +$1.132700 | **+$3.082460** | 약 **+0.62%** / `$780` |

11개 arm row는 모두 이익이지만 6개 condition, 5개 independent event뿐이고 stop loss는
0건이다. Golden Melon이 막으려던 급락 tail을 한 번도 관측하지 못했으므로 승률 100%를
장기 승률로 해석할 수 없다. Mid와 Low는 대부분 같은 event를 공유하며 경제손익 차이도
약 `$0.09`에 불과하다.

2026-08-15 이후 decision instrumentation에서는 High/Mid/Low의 주요 거절이 각각
low volume 19/15/12, low liquidity 31/30/28, price band 21/23/22, too early 17/18/18건이었다.
실제 후보도 fresh spread `0.064~0.081`, fresh price `0.849`, 또는 `0.932`처럼 주문 직전
방어 검사를 통과하지 못했다. 이를 완화하면 거래 수는 늘지만 급락·비실행 위험도 함께
늘어난다. max position은 한 번도 병목이 아니었다.

### 4.2 `$10`과 volume 완화가 답이 아닌 이유

가장 낙관적인 `0.85 → 0.97`도 `$5` 한 건당 최대 약 `$0.706`이다. 19일에 5건인
Mid/Low 속도에서 모든 거래가 이 최대 이익을 낸다고 가정한 `$10` 상한은 30일 약
`$11.15`다. 이는 `$300` 계좌의 3.72%, `$780` 계좌의 1.43%에 불과하다.

`min_volume_24h`를 낮추거나 spread·price band를 완화하면 현재 A/B/C의 연장이 아니라
새 위험 노출을 가진 실험이 된다. 손실 사례가 0건인 현 자료로 그 값을 사후 선택하면
전승 표본에 과적합한다. 그러므로 live 파라미터 추천값은 없다.

## 5. Jenkins 종료 조치와 최종 검증

DB·로그를 먼저 검증한 뒤 신규 주문 경로를 차단했다.

| Jenkins | lifecycle | timer | 최종 build | commit | 최종 cycle |
|---|---|---|---|---|---|
| `polybot-bear` | `close_only` | 없음 | `#11318 SUCCESS` | `a47da22` | buys 0, open 0 |
| `polybot-eco` | `close_only` | 없음 | `#13207 SUCCESS` | `a47da22` | buys 0, open 0 |
| `polybot-tiger` | `close_only` | 없음 | `#12823 SUCCESS` | `a47da22` | buys 0, open 0 |
| `polybot-fruit` | `close_only` | 없음 | `#5420 SUCCESS` | `a47da22` | buys 0, open 0 |
| `polybot-lime` | `close_only` | 없음 | `#12186 SUCCESS` | `a47da22` | buys 0, open 0 |
| `polybot-wolf` | `close_only` | 없음 | `#11035 SUCCESS` | `a47da22` | buys 0, open 0 |

모든 job은 disabled가 아니라 buildable 상태를 보존했다. TimerTrigger가 없어서 자동 신규
cycle은 없고, 실수로 수동 build해도 `close_only`가 Phase 2/3 BUY를 건너뛴다. DB open이
모두 0이므로 자동 매도 관리도 더 이상 필요하지 않다. Slack의 Position 값이 남아 있다면
봇의 CLOB open state가 아니라 resolved/redeemable 또는 account-wide 수동 position일 수
있으므로 DB 행을 조작하지 말고 Polymarket UI에서 claim/보유 내역을 확인한다.

최종 config SHA-256:

- Bear `5901fb9f65cf…`, Eco `7ecf54fdc66b…`, Tiger `67f3b2d587a1…`
- Fruit `1fbc0cfab074…`, Lime `ea210e24be81…`, Wolf `4ae394a88df1…`

local-only `docs/local/jenkins-job-strategy-inventory.md`도 여섯 job을 `NO_TIMER`,
`close_only`, strategy closed로 갱신했다.

## 6. 최종 권고

1. 두 전략의 기존 wallet/job/DB에서 parameter를 바꿔 재개하지 않는다.
2. `$10` 증액 arm을 만들지 않는다.
3. Quince의 maker/taker 구현 결과는 실행 연구 자료로 보존하되 수익 전략으로 승격하지 않는다.
4. Melon의 5-event 전승은 tail 미관측 표본으로 기록하고 threshold를 선택하는 근거로 쓰지 않는다.
5. 다시 검토하려면 live가 아니라 accountless simulation에서 독립 event 30건 이상,
   손실 tail, full-depth 실행가격과 하나의 사전 등록 축을 먼저 수집한다.
6. 현재 여섯 Jenkins workspace·DB·log는 회고 증거이므로 clean/delete하지 않는다.

운영상 사용자에게 남은 필수 조치는 없다. Polymarket UI에 claimable balance가 보일 때만
수동 claim하고, 그 행위를 봇의 actual SELL P&L로 소급 기록하지 않는다.
