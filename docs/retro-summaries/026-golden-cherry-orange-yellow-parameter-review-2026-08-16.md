# 026 — Golden Cherry Orange·Yellow parameter/lifecycle 회고 — 2026-08-16

작성일: 2026-08-16 KST

대상:

- `polybot-orange` → `golden-cherry/data/default/trades.db`
- `polybot-yellow` → `golden-cherry/data/default/trades.db`

## 0. 결론

```text
Sync / verify: Orange·Yellow 모두 SUCCESS
현재 0.95 상단 확대의 수익성 판정: NOT READY
0.95 방향의 구조적 판정: 현재 +10% take-profit과 양립하지 않으므로 REJECT
가장 먼저 할 일: 두 job 신규 진입 close_only → shared lifecycle 수정·대사
이번 작업의 code/Jenkins 변경: 없음 (read-only 회고 범위)
```

Orange의 `POLYBOT_SELL_THRESHOLD=0.95`는 매도 가격이 아니라 **진입 가격 상한**이다.
현재 take-profit이 매수가 대비 `+10%`이므로 진입가가
`1 / 1.10 = 0.90909…`보다 높으면 가격이 1.00이 되어도 take-profit에 도달할 수 없다.
따라서 `0.90909~0.95`를 새로 허용한 방향은 표본 손익과 무관하게 현재 exit 구조와
모순된다.

더구나 Orange와 Yellow는 상단 하나만 다른 A/B가 아니다. 진입 하단, 유동성 하한,
최대 포지션, cycle당 신규 진입 수도 모두 다르다. Orange는 공통 32.5시간 cohort에서
Yellow보다 약 5.8배 많은 시장을 스캔하고 2.2배 많은 confirmed BUY를 만들었지만,
이는 edge가 아니라 **universe와 exposure를 동시에 넓힌 결과**다.

성과 수치로 최적값을 정하는 것도 아직 금지한다. 공통 cohort의 수동 exact 대사에서 실제
BUY/SELL 수량이 다른데도 `COMPLETED`가 된 행이 Orange 24건, Yellow 15건 확인됐다. Yellow 한 건은
6.36주를 매수하고 5.00주만 매도한 뒤 1.36주가 남았는데 완료 처리됐다. 또한 종료된
부분체결 BUY가 Orange 2건, Yellow 1건 `PENDING_BUY`에 남아 청산 관리가 시작되지 않았다.
이 evidence gate를 고치기 전 손익과 parameter sweep은 확정값이 아니다.

## 1. 저장공간·동기화·검증

작업 시 MacBook 여유 공간은 **355 GiB**였고, Mac mini 내부 여유는 scan 기준 약
**75.1 GiB**였다. `daily-rsync` safety floor 50 GiB를 모두 통과했다.

| 항목 | Orange | Yellow |
|---|---|---|
| Scan current strategy | `golden-cherry/default` | `golden-cherry/default` |
| Scan artifact 상한 | 2,068개 · 599.4 MiB | 2,067개 · 1.41 GiB |
| Plan | `bda3e35e2f642d46` | `e3bdfb8faca0056c` |
| Sync run | `846ec60f0d1b49b8bbca792af20a6da7` | `793afcc644d648cba1488b15931b83a2` |
| Result | SUCCESS · 574 transfer · 실패 0 | SUCCESS · 574 transfer · 실패 0 |
| Finished UTC | `2026-08-16T10:57:23.156241Z` | `2026-08-16T11:00:48.264913Z` |
| Verify | SUCCESS · 3,731 checked | SUCCESS · 4,895 checked |
| Retention skip / conflict / failure | 0 / 0 / 0 | 0 / 0 / 0 |

동기화된 local job tree는 Orange 173 MiB, Yellow 409 MiB다. scan의 전체 원격 artifact
상한과 local tree 크기가 다른 것은 incremental catalog와 보존된 artifact 범위가 다르기
때문이며, 이번 요청 구간에는 retention gap이 없다.

### Orange evidence

- Remote DB:
  `/Users/jongwoopark/.jenkins/workspace/polybot-orange/golden-cherry/data/default/trades.db`
- Verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-orange/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- Catalog/local SHA-256:
  `50815cb89399c1a2048b52fb446c8caf4bceb32b2690c0f9cc15032fe9bf261e`
- DB `synced_at`: `2026-08-16T10:54:16.943837Z`
- Source cutoff: `2026-08-16T10:51:30.892533Z`
- Log coverage: bot log 67개(2026-06-11~08-16), console 3,658개(08-03~08-16)

### Yellow evidence

- Remote DB:
  `/Users/jongwoopark/.jenkins/workspace/polybot-yellow/golden-cherry/data/default/trades.db`
- Verified local DB:
  `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-yellow/strategies/golden-cherry/runtime/default/databases/latest/trades.db`
- Catalog/local SHA-256:
  `a380e61795b9039c6742e829ac92d2e43126aafff758949b99781c231b7d05c0`
- DB `synced_at`: `2026-08-16T10:57:40.471342Z`
- Source cutoff: `2026-08-16T10:53:40.184463Z`
- Log coverage: bot log 71개(2026-06-07~08-16), console 4,818개(07-30~08-16)

## 2. 실제 Jenkins 구성

`inspect-jenkins-job`으로 2026-08-16 20:19 KST에 현재 config를 다시 읽었다. 두 job 모두
enabled, non-concurrent, `H/5 * * * *`, live/active이며 clean 명령은 없다. 최신 완료 build는
Orange `#54935`, Yellow `#49687`이고 둘 다 SUCCESS다.

| Resolved parameter | Orange | Yellow |
|---|---:|---:|
| 실제 주문액 | `$5` | `$5` |
| hard max buy amount | `$500` | `$500` |
| 진입 확률 | **0.85–0.95** | **0.75–0.88** |
| 최소 유동성 | **$30,000** | **$125,000** |
| max positions | **100** | **10** |
| cycle 신규 상한 | **5** | **1** |
| open notional 상한 | `$5,000` | `$15,000` |
| entry window | `(0h, 120h]` | `(0h, 120h]` |
| stop / TP / trailing | `-8% / +10% / 5%` | 동일 |
| sports in-play | 허용 | 허용 |

Orange Jenkins config SHA-256은
`d768bd67d06e1dc2b07b3f9685d54b3e4b9d2da57c5ad34962c0e61efa653fe4`, Yellow는
`b74293dcb2d0ef3c5ca0230c8d18e39b178eb8c0bcf2e2832be28e46831d22cf`다.

Orange의 Jenkins description은 아직 `24h~720h / exit 12h`라고 적혀 있지만 실제 shell과
DB resolved config는 `(0h,120h] / exit 0h`다. 이는 거래 로직 오류는 아니지만 operator가
잘못 읽을 수 있는 문서 drift다. 두 shell 모두 `uv sync`를 사용하므로 다음 운영 정비 때
`uv sync --frozen`으로 맞추는 것이 재현성에 더 안전하다.

## 3. 비교 cohort와 cadence

code/config 혼합을 피하기 위해 두 DB가 같은 최신 Git commit
`221e398763acf2bccba428ad190ecbfa2ecb5f99`를 사용한 공통 half-open range만 비교했다.

- UTC: `[2026-08-15T02:15:00Z, 2026-08-16T10:50:00Z)`
- KST: `[2026-08-15 11:15, 2026-08-16 19:50)`
- 길이: 약 32시간 35분
- Orange config hash:
  `c078cf0e8a390ece9c9f828705d87127b01de67de2a02c0f3a401c5e91b3ba33`
- Yellow config hash:
  `7dfa94598a04797714e942fb6478dc989fac948bb99b637d76a35c5a97baf242`

| 지표 | Orange | Yellow |
|---|---:|---:|
| RunAudit | 391 | 391 |
| SUCCESS / FAILED | **391 / 0** | **391 / 0** |
| 최대 start gap | 5.354분 | 5.259분 |
| 평균 / 최대 runtime | 40.63초 / 88.59초 | 27.14초 / 35.91초 |
| cursor-complete sweep | 391 / 391 | 391 / 391 |
| 평균 pages | 43.68 | 7.97 |
| 평균 unique markets | 4,317.67 | 748.41 |
| candidate | 10,957 | 1,783 |
| BUY submitted / activated | 276 / 205 | 109 / 95 |
| pending SELL checks | 1,231 | 241 |
| reconciliation errors | **391** | **6,256** |

5분 cadence는 충분하다. runtime이 주기를 침범하지 않았고 sweep도 전부 cursor-complete다.
Yellow 16건/cycle, Orange 1건/cycle의 reconciliation error가 계속 반복되는 것이 문제지
schedule이 느려 거래를 놓친 증거는 없다.

Orange가 평균 시장 5.77배, candidate 6.15배, activated BUY 2.16배인 것은 0.95 상단만의
효과가 아니다. 유동성 `$30k`, 100 positions, 5 new/cycle도 동시에 적용되어 인과를 분리할
수 없다.

## 4. Strict evidence audit

진단용 strict closed range는 `[2026-08-15T00:00:00Z, 2026-08-16T00:00:00Z)`로 고정했다.
결과는 다음 local-only 산출물에 저장했다.

- `daily-rsync/data/analysis/golden-cherry-orange-yellow-20260815/retro-audit.json`
- `daily-rsync/data/analysis/golden-cherry-orange-yellow-20260815/retro-audit.md`

strict audit는 exit 1이다.

| Issue | Orange | Yellow | 판정 |
|---|---:|---:|---|
| COMPLETED exact fill coverage | 85.1% | 80.0% | CRITICAL |
| closed BUY/SELL quantity mismatch | 18 | 15 | CRITICAL |
| generic fee amount missing | 66.7% | 60.6% | HIGH 표시, 아래와 같이 보정 필요 |

generic auditor는 `fee_amount_usdc`의 직접 존재만 본다. 그러나 공통 cohort의 confirmed
fill은 Orange에서 `MAKER/null` 251건과 `TAKER/fee_rate=0` 140건, Yellow에서 각각
133건과 86건이었다. Golden Cherry의 전략별 계약에서는 maker fee 누락 또는 명시적인
`fee_rate=0`을 known zero로 처리한다. 현재 Polymarket 문서도 order/trade의 fee rate를
별도 필드로 제공하고 maker platform fee가 0이라고 명시한다. 따라서 이번 generic fee
HIGH는 adapter false positive이며 auditor를 전략별 fee contract와 맞춰야 한다.

반면 BUY/SELL 수량 mismatch는 실제 결함이다. 이를 fee 경고와 함께 무시하면 안 된다.

## 5. 실제 lifecycle 결함

### 5.1 `MATCHED`를 무조건 full fill로 본다

`golden-cherry/src/polybot/db/fill_evidence.py`는 confirmed fill 합과
`latest_size_matched`가 같으면, order status가 `MATCHED`라는 이유만으로
`requested_size`와 차이가 얼마든 full fill로 인정한다. Polymarket API는
`original_size`와 `size_matched`를 명시적으로 분리하며 `size_matched`를 실제 체결량으로
정의한다. 따라서 status 이름이 수량 비교를 대체할 수 없다.

### 5.2 완료 기준이 실제 잔량이 아니다

`golden-cherry/src/polybot/strategy/trader.py`는 SELL 제출 전에 balance clamp가 계산한
`pending_sell_remaining_shares`만 보고 `< 5`면 완료 처리한다. 실제로는 다음을 계산해야
한다.

```text
actual_remaining = cumulative_confirmed_buy_size - cumulative_confirmed_sell_size
```

현재 공통 cohort의 잘못 닫힌 잔량은 다음과 같다.

| Job | mismatch COMPLETED | 잔여 shares 합 | 마지막 SELL가 평가액 | 최대 단건 잔량 |
|---|---:|---:|---:|---:|
| Orange | 24 | 0.128488 | 약 `$0.108915` | 0.011466 |
| Yellow | 15 | 1.453127 | 약 `$1.239434` | **1.360000** |

Yellow의 최대 건은 BUY 6.36, SELL requested 6.36, API/CONFIRMED SELL 5.00인데
`MATCHED`, `needs_reconciliation=0`, `COMPLETED`가 됐다. 1.36주는 현재 최소 주문 5주보다
작아 단독 CLOB SELL이 어렵더라도 사라진 것이 아니다. `DUST/RESIDUAL`로 보존하거나
resolution/redeem 및 wallet reconciliation 대상에 넣어야 한다.

### 5.3 terminal partial BUY/SELL 고착

source cutoff 시점의 오래된 부분체결은 다음과 같다.

| Job | Trade state | requested | confirmed | CLOB state | 문제 |
|---|---|---:|---:|---|---|
| Orange | `PENDING_BUY` | 5.780347 | 4.720000 | `CANCELED_MARKET_RESOLVED` | 실제 token을 HOLDING으로 활성화하지 않음 |
| Orange | `PENDING_BUY` | 5.494505 | 5.000000 | `CANCELED_MARKET_RESOLVED` | 동일 |
| Yellow | `PENDING_BUY` | 6.172840 | 5.263156 | `CANCELED_MARKET_RESOLVED` | 동일 |
| Orange | `PENDING_SELL` | 5.710000 | 4.337347 | `INVALID` | 실제 잔량 약 1.373주 |
| Orange | `PENDING_SELL` | 5.810000 | 1.392404 | `CANCELED_MARKET_RESOLVED` | 실제 잔량 약 4.418주 |

최신 자연 build에서도 이 terminal partial 행들이 계속 `full=False`로 남았다. Yellow에는
별도로 0 fill `LIVE` SELL 한 건이 약 9시간 이상 `PENDING_SELL`에 머물렀다. GTC가 아직
살아 있을 수 있으므로 즉시 실패라고 단정하지는 않지만, cancel/reprice 또는 max-age
정책이 필요하다.

현재 `test_exact_lifecycle.py`와 `test_time_and_order_safety.py` 30개는 모두 통과한다.
즉 기존 동작이 우연히 깨진 것이 아니라, `MATCHED` partial size와 실제 residual을 함께
검증하는 회귀 case가 test suite에 없었던 문제다.

## 6. 성과 기술 통계 — 확정 수익성 판정이 아님

아래 net은 BUY와 SELL confirmed size가 `±0.000001` 안에서 같은 closed trade만 사용하고,
전략별 known-zero fee 계약을 적용한 기술 통계다. mismatch, open carry, legacy
`trades.realized_pnl`은 제외했다.

| 지표 | Orange | Yellow |
|---|---:|---:|
| confirmed entry | 206 | 94 |
| 아직 open | **57** | 4 |
| exact closed | 125 | 75 |
| mismatch closed | 24 | 15 |
| exact-closed net | **-$12.04459** | **-$5.74182** |
| source-cutoff 전체 confirmed open 원가 | 약 **$395.09** | 약 **$20.11** |

Orange의 현재 band를 entry-time `buy_probability`로 나누면 다음과 같다.

| Orange signal band | confirmed | open | exact closed | mismatch | exact-closed net |
|---|---:|---:|---:|---:|---:|
| 0.85–0.88 | 85 | 8 | 66 | 11 | -$3.28532 |
| >0.88–0.90909 | 48 | 4 | 34 | 10 | +$2.58279 |
| **>0.90909–0.95** | **73** | **45** | 25 | 3 | **-$11.34206** |

고가 band는 73건 중 45건이 아직 open이므로 `-$11.34`를 최종 기대값으로 해석하면
closed-position selection bias가 생긴다. 하지만 이 band의 +10% take-profit이 불가능하다는
산술은 open 결과와 관계없이 확정이다.

같은 condition을 두 계정이 모두 거래했고 양쪽 모두 exact closed인 paired subset은 32건뿐이다.

| Paired subset | Orange | Yellow |
|---|---:|---:|
| 평균 entry signal | 0.883594 | 0.816563 |
| exact net 합 | -$0.07942 | +$2.12076 |
| 양수 trade | 16 / 32 | 17 / 32 |

Orange는 같은 시장을 평균 6.70%p 더 높은 가격에 샀고, 이 짧은 subset에서는 Yellow보다
평균 `$0.068756` 낮았다. 이는 높은 가격 추종을 지지하지 않는 약한 신호지만 32건이고
시장 cluster도 독립이 아니므로 통계적 결론으로 사용하지 않는다.

## 7. “다른 값이면 어땠나”의 한계와 기술적 sensitivity

Golden Cherry DB의 `market_snapshots`는 여전히 **0행**이다. 따라서 과거 각 cycle의
가격 경로, 후보 순서, 포지션 cap 충돌을 재생할 수 없고 진짜 counterfactual parameter
sweep은 불가능하다. 실제 진입 중 일부만 사후 필터한 아래 표는 sensitivity일 뿐 인과 추정이
아니다.

| Orange 상단 사후 필터 | confirmed | open | exact closed | mismatch | exact-closed net |
|---|---:|---:|---:|---:|---:|
| 0.88 | 85 | 8 | 66 | 11 | -$3.28532 |
| 0.90 | 121 | 11 | 91 | 19 | -$0.85157 |
| 0.90909 | 133 | 12 | 100 | 21 | -$0.70253 |
| 0.92 | 157 | 23 | 111 | 23 | -$4.68742 |
| 0.94 | 191 | 44 | 124 | 23 | -$11.73835 |
| 0.95 current | 206 | 57 | 125 | 24 | -$12.04459 |

이 표는 `0.90`이 최적이라는 뜻이 아니다. 상단이 높아질수록 아직 끝나지 않은 trade가
같이 늘고, 실제로 낮은 cap을 썼다면 빈 slot에 다른 후보가 들어갔을 것이므로 결과가 달라진다.
다만 0.92 이상에서 exposure와 미성숙 carry가 급격히 늘고, 0.90909를 넘으면 TP가
사라진다는 위험은 명확하다.

유동성 하한도 Orange 실제 trade를 사후 필터하면 `$125k` 이상은 82 entry, 24 open,
47 exact closed, net `-$6.19302`였다. 이 역시 `$30k`보다 우월/열등하다는 증거가 아니다.
후보 순서와 cap이 달라지는 반사실을 저장하지 않았기 때문이다.

## 8. 권고 액션 플랜

### P0 — 신규 노출부터 제한

1. Orange와 Yellow는 같은 `golden-cherry` lifecycle 코드를 쓴다. 수정 전 두 job의
   `POLYBOT_LIFECYCLE_MODE`를 `close_only`로 바꿔 신규 BUY만 멈추는 것이 안전하다.
2. Orange는 특히 `max_positions=100`, `new/cycle=5`라 결함이 있는 상태에서 노출 증가가
   빠르다. parameter 실험보다 이 제한이 우선이다.
3. DB를 clean하지 않는다. 부분체결·잔량·wallet 대사에 필요한 유일한 evidence다.

이번 요청은 분석/회고이므로 실제 Jenkins 변경은 하지 않았다.

### P1 — shared code 수정

1. `MATCHED` 예외를 제거한다. confirmed fill 합, `size_matched`, `requested/original_size`를
   명시적 quantization tolerance 안에서 모두 대사해야 full fill이다.
2. SELL 완료 기준을
   `cumulative confirmed BUY - cumulative confirmed SELL`로 바꾼다.
3. 잔량이 시장 최소 주문보다 크면 `HOLDING`으로 복귀해 나머지를 다시 제출한다.
   작으면 `COMPLETED`로 flat 처리하지 말고 `DUST/RESIDUAL` 또는 별도 잔량 원장에 보존한다.
4. terminal partial BUY는 실제 confirmed size를 관리 가능한 partial holding으로 활성화하거나
   명시적인 `QUARANTINED/RESOLVED_CLAIMABLE` 상태로 전이한다. 무기한 `PENDING_BUY`는 금지한다.
5. 회귀 test에 최소한 다음을 추가한다.
   - SELL requested 6.36 / confirmed 5.00 → flat `COMPLETED` 금지, residual 1.36 보존
   - 작은 quantization dust → residual provenance 보존
   - terminal partial BUY → 무한 `PENDING_BUY` 금지
   - terminal partial SELL → 실제 remaining 계산
6. `polybot-retro`의 generic fee check를 Golden Cherry의 maker/explicit-zero fee 계약과 맞춘다.

### P2 — 배포 후 재가동 gate

1. 두 Jenkins의 자동 timer를 멈춘 상태에서 코드와 test를 배포한다.
2. `close_only` 1회 실행 후 DB·wallet·order catalog를 대사한다.
3. 아래 조건을 모두 만족해야 active로 복귀한다.
   - terminal `PENDING_BUY=0`
   - 오래된 zero-fill `PENDING_SELL=0` 또는 열린 GTC로 명시적 설명
   - 새 `COMPLETED`의 BUY/SELL exact 수량 coverage 100%
   - residual/dust가 flat P&L에 누락되지 않음
   - strict audit CRITICAL/HIGH 0(전략별 fee adapter 보정 후)

### P3 — parameter는 새 cohort에서 한 축만 비교

현재 `0.95`는 유지 권고하지 않는다. lifecycle 복구 후 목적에 따라 다음 중 하나를 선택한다.

- **운영 안전 rollback:** Orange 상단을 `0.88` 또는 최대 `0.90`으로 낮추고,
  `$125k / max positions 10 / new per cycle 1 / $5`로 Yellow와 비슷한 노출 상한을 둔다.
  이는 edge 최적화가 아니라 위험 제한이다.
- **깨끗한 A/B:** 두 arm 모두 lower `0.85`, min liquidity, `$5`, position cap,
  new/cycle, 시간/exit를 동일하게 두고 upper만 `0.88` 대 `0.90`으로 나눈다.
  `0.95`는 현재 +10% TP와 모순되어 treatment 후보에서 제외한다.

`0.95`를 꼭 검증하려면 take-profit을 바꾸는 수준이 아니라 “resolution까지 보유하는
last-mile 전략”으로 별도 사전등록해야 한다. Golden Cherry의 stop/trailing/TP 구조 안에서
상단만 0.95로 올리면 전략 가설 자체가 달라진다.

### P4 — 평가 시점

현재 공통 cohort는 32.5시간뿐이고 entry window는 최대 120시간이다. lifecycle 수정 후 새
cohort를 시작하고 최소 7일, 가능하면 30일을 유지하되 평가 시에는
`buy_timestamp <= review_end - 120h`인 mature entry만 사용한다. 현재 고정 entry cohort를
참고용으로 재검사한다면 늦어도 **2026-08-22 KST 이후**에 다시 sync해 open/resolution을
확인한다.

## 9. 출처와 재현성 메모

- Polymarket `Get single order by ID`: `original_size`와 `size_matched`를 별도 필드로 정의한다.
  <https://docs.polymarket.com/api-reference/trade/get-single-order-by-id>
- Polymarket order overview: `size_matched`는 실제 filled amount다.
  <https://docs.polymarket.com/trading/orders/overview>
- Polymarket fee 문서: fee는 market별이며 maker platform fee는 0, taker fee는 별도로
  확인해야 한다. <https://docs.polymarket.com/trading/fees>
- Golden Cherry 기존 계약과 2026-07 정정 회고:
  `golden-cherry/STRATEGY.md`,
  `docs/retro/golden-cherry-2026-07-parameter-review.md`

Jenkins config와 console은 `inspect-jenkins-job`의 redacted read-only 조회로 확인했고,
credential 값은 이 문서에 기록하지 않았다. 이번 작업에서 live code, Jenkins config,
wallet, DB row는 변경하지 않았다.
