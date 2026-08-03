# golden-elderberry 파라미터 리뷰 — 2026-08-03

> **결론 먼저: 지금은 파라미터를 조정할 수 없다.**
> 봇의 자체 성적표(`+$156.57`)와 증거등급 성과(`-$28.40`)의 **부호가 다르고**,
> 유일하게 유망한 필터는 **관측 불가능한 EXPIRED 36건의 처리 가정에 따라 부호가 뒤집힌다.**
> 파라미터가 아니라 **측정과 운영이 병목**이다. §6에 오늘 바로 할 수 있는 수정을,
> §7에 파라미터 질문을 풀기 위한 해제 조건을 적었다.

---

## 0. Evidence 헤더 (Evidence Contract 준수)

| 항목 | 값 |
|---|---|
| 분석 구간 (UTC half-open) | **`[2026-07-11T00:00:00Z, 2026-07-29T00:00:00Z)`** — 18일 |
| timezone | 전부 UTC |
| source × job × strategy × runtime | `macmini-m5` × `polybot-cherry` × `golden-elderberry` × `default` |
| `remote_path` | `/Users/jongwoopark/.jenkins/workspace/polybot-cherry/golden-elderberry/data/default/trades.db` |
| verified local DB | `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-cherry/strategies/golden-elderberry/runtime/default/databases/latest/trades.db` |
| `local_sha256` | `24095bacb96b0a40f2915203ef7671715a5ed9ee43c0d1717842594809a0257d` (remote와 일치) |
| audit snapshot sha256 | `4de78caf3db72ac64c4f5b27847fc1e988801174ceff25ea26d517035170d2e9` (online backup — 원본과 다른 값이 정상) |
| `latest_successful_sync.finished_at` | `2026-08-03T13:01:23.603553Z` (SUCCESS, transferred 13,335, failed 0) |
| DB `synced_at` | `2026-08-03T13:00:46.826395Z` |
| **`source_completed_at` / `source_mtime_at`** | **`2026-07-28T15:38:14.014698Z`** |
| `verify` | **SUCCESS** — checked 4,287 / failed 0 / `skipped_retention_deleted` 0 |
| `quick_check` | `ok` |

### ⚠️ 한계 1 — 요청하신 "지난 몇일"은 덮이지 않는다

sync 자체는 오늘(2026-08-03) 성공했지만, **원본 데이터가 2026-07-28T15:38Z에서 끝난다.**
마지막 Jenkins build는 **#49268**이고 그 이후 build가 없다. bot log도 `20260729.log.gz`가
102KB(정상일 4MB대)로 잘려 있다.

→ **오늘 기준 약 6일의 evidence gap이 있다.** 임의 SSH/rsync는 하지 않았다.
`polybot-cherry`가 2026-07-29 이후 실제로 돌고 있다면 그 구간은 아직 동기화되지
않은 것이므로 재sync가 필요하고, 돌고 있지 않다면 "실행중"이라는 전제 자체를
확인해야 한다. **이 리뷰의 모든 수치는 `[07-11, 07-29)` 구간에 한정된다.**

### ⚠️ 한계 2 — audit이 FAIL이다

```
polybot-retro audit --strict → status: FAIL   CRITICAL 4 / HIGH 5 / MEDIUM 2
```

| 심각도 | code | 내용 |
|---|---|---|
| CRITICAL | `completed_trade_fill_gap` | COMPLETED trade의 BUY/SELL CONFIRMED fill 커버리지 **62.1%** |
| CRITICAL | `closed_trade_fill_quantity_mismatch` | BUY/SELL 실체결 수량 불일치 **20건** |
| CRITICAL | `fill_quantity_overflow` | CONFIRMED 합계가 `latest_size_matched` 초과 **5건** |
| CRITICAL | `uncertain_submission_outcome` | POST 전후 결과 미확정 intent **5건** |
| HIGH | `stale_order_reconciliation` | 1시간 초과 미완료 대사 **38건** |
| HIGH | `fill_fee_missing` | fee 확정 불가 비율 **79.7%** |
| HIGH | `failed_runs` | FAILED run **107건** |
| HIGH | `run_schedule_gap` | SUCCESS run 최대 간격 **21.07시간** |
| HIGH | `stale_running_runs` | 1시간 초과 RUNNING **1건** |

L2 `AGENTS.md`는 이 상태에서 **"조정하지 않고 수집·대사부터 복구한다"** 고 정하고 있다.
본 리뷰는 그 계약을 따른다.

---

## 1. cohort 구조 — 다행히 파라미터 교란은 없다

`config_hash`는 전 구간에서 **단 2개**이고, 둘의 차이는 `lifecycle_mode: active`
추가뿐이다. **전략 파라미터는 07-11~07-28 내내 완전히 동일했다.**

| config_hash | 구간 | 전략 파라미터 |
|---|---|---|
| `7c9dae6aab…` | 07-11 08:45 ~ 07-14 11:25 | 아래와 동일 |
| `586080f48b…` | 07-14 11:30 ~ 07-28 15:35 | 아래와 동일 (+`lifecycle_mode`) |

git commit은 58개 버전이 지나갔지만 config는 안 바뀌었다. 즉 **구간을 쪼갤 이유가
없고, 대신 "파라미터를 바꿔본 적이 없으므로 비교 대조군도 없다"** 는 뜻이다.

실제 운영 파라미터 (DB `strategy_configs` 기록값이 authoritative):

```yaml
buy_amount_usdc: 100.0        # ← repo config.yaml은 5.0. §6-② 참조
max_positions: -1             # 무제한
min_liquidity: 20000
min_volume_24h: 10000
take_profit_percent:  0.10
stop_loss_percent:   -0.10
reentry_cooldown_hours: 24
strategy:
  ref_window_hours: 48 / ref_exclude_recent_hours: 3 / ref_min: 0.70
  drop_min: 0.12
  current_min: 0.35 / current_max: 0.75
  stab_window_minutes: 45 / stab_max_std: 0.02
  max_holding_hours: 48
time_based: { entry_hours_min: 48, exit_hours: 24 }
```

> **gate 위반은 없다.** `stabilization_range_at_buy`가 101건 중 52건에서 0.02를
> 넘어 처음엔 gate 위반으로 보였으나, 이 컬럼은 std가 아니라 **고저폭(max−min)**
> 이다(`db/models.py:74`). gate는 std ≤ 0.02이므로 정상이다. `drop_min`, 진입밴드,
> `ref_min`, 유동성·거래량 gate는 **101건 전부 위반 0건**이다.

---

## 2. 증거등급 성과 — 자체 성적표와 부호가 다르다

`trades.realized_pnl`은 **요청가 × 요청수량**이라 성과지표로 쓸 수 없다(L2 계약).
`order_fills.status='CONFIRMED'`의 실체결 size/price만으로 다시 계산했다.

| 지표 | 값 |
|---|---|
| 봇 자체 보고 (`trades.realized_pnl`) | **+$156.57** ← 쓰면 안 되는 값 |
| audit `confirmed_fill_gross_pnl_usdc` | **−$48.22** |
| 엄격 왕복 재구성 (양쪽 CONFIRMED + 수량 일치 0.5% 이내, n=101) | **−$28.40** / 투입 $10,120 = **−0.281%** |

엄격 왕복 101건의 건당 수익률:

```
평균 -0.295%   sd 19.38%   se 1.93%   t = -0.15
95% CI [-4.08%, +3.49%]      승률 49.5% (50/101)
```

**즉 실현 구간만 보면 "0과 구별되지 않는다".** 손실이 확정된 것도 아니다.
다만 sd가 19.4%라 이 표본의 MDE(80% 검정력)는 **약 5.4pp**다. 그보다 작은 edge는
애초에 보이지 않는다.

### 청산은 대칭 동전던지기다

| exit_reason | n | 평균 수익률 | 승률 |
|---|---:|---:|---:|
| `take_profit` | 44 | **+16.69%** | 100% |
| `stop_loss` | 47 | **−16.24%** | 0% |
| `max_holding` | 9 | −0.41% | 56% |
| `time_exit` | 1 | +2.94% | 100% |

44승 47패에 폭이 거의 대칭이다. **이 전략의 손익은 사실상 TP/SL 배리어에 대한
50:50 랜덤워크**이며, 그것이 −0.3%라는 결과의 전부다.

---

## 3. 유일하게 유망했던 것 — 그리고 그것이 왜 무효인가

### 3-1. "임계값 인접 후보가 가장 나쁘다" 패턴

현재 gate **바로 안쪽** 구간이 세 파라미터에서 **모두** 음수다:

| 구간 | n | 평균 | 나머지 | 차이 | t |
|---|---:|---:|---:|---:|---:|
| `drop` 0.12~0.15 (하한 인접) | 34 | −6.08% | +2.64% | −8.72pp | −2.06 |
| `buy_price` 0.70~0.75 (상한 인접) | 21 | −8.71% | +1.91% | −10.63pp | −1.98 |
| `liquidity` 20k~50k (하한 인접) | 56 | −3.63% | +3.85% | −7.48pp | −1.84 |

세 축이 독립인데 방향이 일치한다. 기계적으로도 그럴듯하다 — 한계 후보(겨우 통과한
것)가 가장 나쁘다는 것은 사전에 예측 가능한 패턴이다.

결합 필터 `drop≥0.15 AND price<0.70 AND liquidity≥50k`:

| | n | 평균 | t | 승률 | P&L |
|---|---:|---:|---:|---:|---:|
| 통과 | 33 | **+9.63%** | +2.88 | 70% | +$318.74 |
| 탈락 | 68 | −5.11% | −2.38 | 40% | −$347.14 |

부트스트랩 95% CI `[+3.18%, +16.12%]`, 평균≤0 비율 0.0016.

### 3-2. 선택편향 보정 — 통과하지만 간신히

2,240개 조합(변수 7개 중 3개 선택 × 임계값 4³)에 대해 **최대 t 통계량의 순열
귀무분포**를 구했다. 이것은 임계값 탐색뿐 아니라 **변수 선택까지** 보정한다.

```
관측 최대 t = +3.96   (price<0.70, liq>=100k, ref>=0.80), n=14, 평균 +16.06%
귀무하 최대 t:  중앙 +2.29   95분위 +3.95   최대 +9.30
→ 보정 p = 0.0433   (순열 300회, p의 se ≈ 0.012)
```

**관측값이 귀무 95분위(+3.95)를 겨우 0.01 넘는다.** p = 0.043 ± 0.023으로
0.05를 걸친다. 이 정도는 "탐색을 더 넓게 잡았으면 사라졌을" 수준이다.

> 이전 단계에서 임계값 탐색만 보정해 얻은 `p=0.0085`는 **변수 선택을 포함하지
> 않은 값**이므로 인용하지 않는다. 올바른 값은 **0.043**이다.

### 3-3. 그리고 생존편향이 이것을 무너뜨린다 ← 핵심

엄격 왕복 101건은 전체 244건의 **41%**뿐이다. 빠진 143건은 무작위가 아니다.

| 제외 사유 | n | **필터 통과율** |
|---|---:|---:|
| HOLDING (미청산) | 57 | 21.1% |
| **EXPIRED (해결·미상환)** | **36** | **58.3%** ← |
| COMPLETED인데 fill 증거 부족 | 35 | 31.4% |
| UNFILLED (유령 매수) | 15 | 33.3% |
| *(참고) 포함된 101건* | 101 | 32.7% |

**EXPIRED 36건의 58.3%가 필터를 통과한다.** 그리고 EXPIRED는 전부 낙폭
0.35~0.55의 대형 급락이다 — 필터가 정확히 선호하는 종류다. 즉 **필터는 결과를
관측할 수 없는 바구니로 노출을 집중시킨다.**

EXPIRED의 운명에 따라 결론이 뒤집힌다:

| EXPIRED 처리 가정 | 필터 통과 | 필터 탈락 | 전체 | 필터가 |
|---|---:|---:|---:|---|
| 제외 (= 3-1의 방식) | **+9.63%** (n=33) | −5.11% | −0.30% | **도움 (+14.7pp)** |
| 마지막 관측가로 mark | +6.75% (n=50, t=+1.14) | −6.43% | −2.26% | 도움 (+13.2pp, t=+1.86) |
| **전손(−100%) 가정** | **−33.01%** (n=54) | −22.26% | −26.49% | **해로움 (−10.8pp)** |

**세 번째 가정이 배제되지 않는다.** 원래 favorite(≥0.70)이던 토큰이 0.40대로
급락한 뒤 해결됐다면, 그 급락이 **정보였을** 가능성 — 즉 0으로 해결됐을 가능성이
높다. 그리고 그 경우 필터는 도움이 아니라 **해악**이다.

**결정적으로, 이것을 확인할 방법이 지금 없다.** `market_snapshots` 보존이
2026-07-21부터라 **EXPIRED 36건 중 28건은 사후 가격 관측 자체가 불가능**하다.

> **그래서 파라미터 권고를 하지 않는다.** 이건 권고에 붙는 단서가 아니라
> 권고가 성립하지 않는 이유다. 이 상태에서 `drop_min`을 0.15로 올리면
> golden-date가 문서에만 판정 기준을 적어두고 계좌 절반을 잃은 것과 같은
> 구조의 실수가 된다.

---

## 4. 안정성 — 여기는 증거가 명확하다

P&L 질문과 **무관하게** 확정되는 사실들이다.

### 4-1. `max_positions: -1`이 실제로 폭주했다

```
동시 오픈 최대 123개 @ 2026-07-27 16:16:55Z
최대 동시 노출 ≈ $12,300 (건당 $100)
마지막 사이클 시점 미청산 93개
```

폐쇄한 fig·mango·date와 **같은 결함**이다. 증거등급 성과가 음수인 전략이 무제한으로
포지션을 늘렸다.

### 4-2. 봇이 15.3%의 시간 동안 꺼져 있었고, 그 사이 노출은 관리되지 않았다

| 공백 | 길이 | 공백 내내 열려있던 포지션 |
|---|---:|---:|
| 07-18 13:41Z → 07-19 10:45Z | **21.07h** | **72개 (≈$7,200)** |
| 07-19 11:41Z → 07-20 00:45Z | 13.06h | 81개 (≈$8,100) |
| 07-17 21:40Z → 07-18 08:45Z | 11.07h | 71개 (≈$7,100) |
| 07-20 01:41Z → 07-20 10:25Z | 8.74h | 81개 (≈$8,100) |
| 07-20 10:25Z → 07-20 14:29Z | 4.07h | 81개 (≈$8,100) |

**누적 62.7시간 / 전체 410시간 = 15.3%.**

### 4-3. 손절이 손절로 작동하지 않는다 (4-2의 직접 귀결)

설정은 `stop_loss_percent: -0.10`인데:

```
n=47   중앙 -12.00%   평균 -16.24%
-10%보다 나쁘게 체결된 것  42/47 = 89%
-20%보다 나쁜 것 9건,  최악 -97.2%
```

**89%가 설정값보다 나쁘게 체결된다.** 5분 cadence로도 갭을 못 막는데, §4-2처럼
21시간 꺼져 있으면 −10% stop은 존재하지 않는 것과 같다. **−97.2%는 그 구조의 산물**
이며, 이것이 §2의 `stop_loss` 평균(−16.24%)을 끌어내려 TP/SL 대칭을 깨뜨린다.

### 4-4. 매도 무한 재시도 루프가 살아 있다

```
SELL submission 총 97,437건 중 latest_order_status = NULL 이 97,437건
같은 token/side 반복 제출 상위: 3,370회 / 3,072회 / 3,016회 / 3,001회 / 2,873회 …
needs_reconciliation=1  39건
reconciliation_error 존재  26건
```

`docs/sell-retry-loop-defense.md`가 기술한 바로 그 실패다. 2026-07-28 build #49267에서
격리 intent **47건을 수동 해제**한 기록이 콘솔 로그에 남아 있다.

### 4-5. 자본의 44%가 미회수·미확정 상태다

| 상태 | n | 요청 기준 금액 |
|---|---:|---:|
| COMPLETED | 136 | $13,600 |
| HOLDING | 57 | $5,700 |
| **EXPIRED (수동 redeem 필요)** | **36** | **$3,600** |
| UNFILLED (유령 매수) | 15 | $1,500 |
| **미회수·미확정 합계** | **108** | **$10,800 / $24,400 = 44%** |

마지막 사이클 로그에도 `WARNING - EXPIRED 포지션 36개 - 수동 redeem 필요`가 남아 있다.

### 4-6. FAILED run 107건

| error_type | n |
|---|---:|
| `RuntimeError` | 58 |
| `UnresolvedSubmissionOutcomeError` | 30 |
| `SubmissionEvidenceError` | 10 |
| `HTTPError` | 8 |
| `ChunkedEncodingError` | 1 |

---

## 5. 부수 소득 — quince의 미해결 질문 일부 해소

### 5-1. 수수료는 실제로 0이다 (단, 증거는 부분적)

```
CONFIRMED fill 556건
  fee_amount_usdc  값 있음 0건        → 전부 NULL
  fee_rate_bps     값 있음 111건, 그 111건 전부 0.0   (0이 아닌 것 0건)
```

**`fee_rate_bps`가 명시적으로 `0.0`으로 기록된 111건**은 단순 누락이 아니라
**거래소가 요율 0을 보고했다는 양의 증거**다. Gamma 메타데이터가 94% 시장에
`fee_rate` 0.04~0.07을 선언하는 것과 배치되며, **"선언은 있으나 부과는 없다"**
쪽을 지지한다. `docs/golden-quince-abc-runbook.md` §6 판독표의 2행에 해당한다.

다만 445건은 여전히 NULL이므로 **완결된 답은 아니다.** quince 첫 체결에서
재확인은 그대로 필요하다.

### 5-2. 역선택 가설에 불리한 관측

elderberry의 CONFIRMED BUY는 **251건 MAKER / 72건 TAKER (77.7% maker)** 이다.
엄격 왕복에서 진입 leg별 성과:

| 진입 leg | n | 평균 수익률 |
|---|---:|---:|
| MAKER | 56 | **−3.98%** |
| TAKER | 45 | **+4.29%** |

**quince가 검정하려는 방향과 반대다.** 패시브(maker) 체결이 오히려 나빴다는 것은
`STRATEGY.md` §2의 경쟁 가설 — **"패시브가 얻는 1틱 할인은 역선택으로 상쇄된다"**
— 와 정합적이다.

> 단정하지 않는다. 이건 실행 처치를 무작위 배정한 실험이 아니라 **관측 자료**이고,
> maker/taker 여부는 시장 상황(급락 직후 유동성)과 교란돼 있다. 그래도
> **quince A/B/C의 사전 예측이 깨질 수 있다는 첫 실측 신호**이므로 30일 판정 때
> 이 표를 함께 봐야 한다. `docs/retro/golden-quince.md` §7의 역선택 측정이
> 정확히 이것을 겨냥한다.

---

## 6. 지금 바로 할 수 있는 것 (파라미터 아님)

증거가 확정된 것만 적는다. **P&L 질문이 풀리지 않아도 전부 유효하다.**

### ① `max_positions`를 유한하게 — 최우선

```yaml
max_positions: 20          # -1 → 20. 실측 최대 123개였다
max_new_positions_per_cycle: 1
```

폐쇄한 fig·mango·date와 같은 결함이고, 실제로 123개까지 갔다.

### ② repo config와 운영 config의 20× 불일치 해소 — 이번 리뷰 최대 발견

| 출처 | `buy_amount_usdc` |
|---|---:|
| repo `golden-elderberry/config.yaml` (생성 이래 무변경) | **5.0** |
| DB `strategy_configs` 실제 운영값 | **100.0** |

Jenkins env(`POLYBOT_BUY_AMOUNT`)가 repo를 20배로 덮고 있다. **저장소를 읽는
누구도 실제로 얼마가 거래되는지 알 수 없다.** 둘 중 하나로 정렬하고, env override를
유지하려면 그 사실을 `config.yaml` 주석과 L3 문서에 명시한다.

### ③ EXPIRED 36건 redeem과 redeem 증거 수집

$3,600이 묶여 있고, **더 중요하게는 이것이 §3-3의 판정 불가를 만든 원인**이다.
resolution 결과만 적재하고 redeemable/실제 redeem transaction을 수집하지 않으면
다음 리뷰에서도 같은 벽에 부딪힌다.

### ④ `market_snapshots` 보존기간 연장

현재 보존이 **7일**(07-21~07-28)이라 EXPIRED 36건 중 28건을 사후 평가할 수 없다.
**최소 리뷰 구간(30일) 이상**으로 늘린다. 이것을 고치지 않으면 §3의 질문은
영원히 못 푼다.

### ⑤ 스케줄 공백 대응

21시간 정지 중 $7,200이 무방비였다. 공백 자체를 없애는 게 우선이고, 그게 어렵다면
**긴 공백 후 첫 사이클에서 stop 조건을 먼저 평가**하도록 순서를 보장한다.

### ⑥ 매도 재시도 루프 방어 확인

`docs/sell-retry-loop-defense.md`의 방어가 이 봇에 실제로 적용됐는지 확인한다.
동일 token 3,370회 재제출은 방어가 작동하지 않았다는 뜻이다.

---

## 7. 파라미터 질문을 푸는 방법 (사전 등록 제안)

**지금 적용하지 않는다.** 아래는 §6이 끝난 뒤 검정할 가설이다.

### 사전 등록 가설 H1

> 현재 gate 바로 안쪽의 한계 후보를 배제하면 건당 수익률이 개선된다.
> 구체적으로 `drop_min: 0.12 → 0.15`, `current_max: 0.75 → 0.70`,
> `min_liquidity: 20000 → 50000`.

- 관측된 효과크기: **+14.7pp** (실현 기준) / **+13.2pp** (mark 포함)
- 선택편향 보정 p = **0.043** (2,240 조합, 순열 300회) — **간신히 통과**
- **반증 조건**: EXPIRED를 전손으로 가정하면 효과가 **−10.8pp로 역전**

### 승격 gate (전부 충족해야 검정 개시)

- [ ] `polybot-retro audit --strict`에서 **CRITICAL / HIGH = 0**
- [ ] COMPLETED trade의 fill coverage **≥ 95%** (현재 62.1%)
- [ ] **EXPIRED redeem 증거 적재** — H1의 부호를 결정하는 유일한 변수
- [ ] `market_snapshots` 보존 **≥ 리뷰 구간**
- [ ] `max_positions` 유한 + repo/운영 config 정렬 (§6-①②)

### 검정 설계

gate 충족 후, **A/B로 동시 운용**한다. 순차 비교(전후 대조)는 시장 국면과 교란되므로
쓰지 않는다.

| 팔 | 변경 | 지갑/job/DB |
|---|---|---|
| A | 현행 (`drop 0.12` / `max 0.75` / `liq 20k`) | 분리 |
| B | H1 (`drop 0.15` / `max 0.70` / `liq 50k`) | 분리 |

- 1차 종점: **건당 수익률 차이** (실현 + redeem 증거 포함)
- 필요 표본: sd 19.4%, 목표 효과 14pp → 팔당 **약 30~40 왕복**
  (§2의 MDE 5.4pp보다 목표 효과가 크므로 이 표본으로 판정 가능)
- 금액은 **$5로 낮춰 시작한다.** $100 × 무제한 포지션은 §4-1의 재현이다.

---

## 8. 한 줄 요약

**golden-elderberry의 문제는 파라미터가 아니다.** 증거등급 성과는 −0.28%로 0과
구별되지 않고, 가장 유망한 필터는 **관측할 수 없는 EXPIRED 36건이 결정한다.**
지금 필요한 것은 파라미터 조정이 아니라 **`max_positions` 유한화, repo/운영 config
20× 불일치 해소, redeem 증거 수집, snapshot 보존 연장** 네 가지다. 그것이 끝나면
§7의 H1을 A/B로 검정할 수 있다.

---

## 부록 — 재현 절차

```bash
cd daily-rsync
uv run daily-rsync locate --strategy golden-elderberry
uv run daily-rsync verify --job polybot-cherry --strategy golden-elderberry
cd ..

uv run --project polybot-observability polybot-retro audit \
  --db "$(pwd)/daily-rsync/data/sources/macmini-m5/jobs/polybot-cherry/strategies/golden-elderberry/runtime/default/databases/latest/trades.db" \
  --days 18 --as-of 2026-07-28 \
  --output-dir "$HOME/polybot-retro/elderberry-2026-08" --strict
```

성과 재구성은 `trades.realized_pnl`을 쓰지 않고 아래 정의를 따른다.

```text
엄격 왕복 = BUY/SELL 양쪽에 order_fills.status='CONFIRMED'가 있고
            두 CONFIRMED size의 상대오차 <= 0.5%
entry_vwap = CONFIRMED BUY의 sum(size*price)/sum(size)
exit_vwap  = CONFIRMED SELL의 동일 계산
gross P&L  = (exit_vwap - entry_vwap) * min(buy_size, sell_size)
```
