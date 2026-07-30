# golden-kiwi 회고(포스트모템) 가이드

> **현재 상태: research/simulation 전용, live 금지.**
> 기존 frozen OOS에서 A/B/C/D 모두 gate를 실패했다. 이 문서는 새 30일 독립 수집을
> 평가하는 절차이며, 결과가 좋아도 바로 live 주문을 승인하지 않는다.
> 과거 C 양수 신호의 cross-commit 결함과 snapshot-level catalog 부족은
> [증거 정정문](../../golden-kiwi/research/2026-07-30-cohort-correction.md)을
> 우선 적용한다.

먼저 [Evidence Contract](EVIDENCE_CONTRACT.md)와
[`golden-kiwi/STRATEGY.md`](../../golden-kiwi/STRATEGY.md)를 읽는다.

## 0. 복붙용 프롬프트

```text
docs/retro/EVIDENCE_CONTRACT.md, golden-kiwi/STRATEGY.md,
docs/retro/golden-kiwi.md를 순서대로 읽어라.

REVIEW_START=<YYYY-MM-DDT00:00:00Z>
REVIEW_END_EXCLUSIVE=<YYYY-MM-DDT00:00:00Z>
REVIEW_AS_OF=<REVIEW_END_EXCLUSIVE의 전날 YYYY-MM-DD>

1) A/B/C/D의 canonical DB 네 개를 명시하고 SHA-256·PRAGMA quick_check를 기록한다.
2) polybot-retro audit --strict를 실행한다.
3) CRITICAL/HIGH가 하나라도 있으면 arm 성과·threshold 변경·shadow 승격을 중단한다.
4) config_hash × git_commit × mode × job_name cohort를 분리한다.
5) 네 팔은 confirmation_steps와 min_cumulative_move 외의 값이 같은지 검증한다.
6) SUCCESS run, cursor-complete sweep, current-run 마지막 snapshot, lineage 전체의
   동일 config/Git/mode/job, 60일 raw cadence, point-in-time catalog/event와
   snapshot bid/ask coverage를 검사한다.
7) micro_cascade_signal_decisions의 raw_selected entry ask와
   micro_cascade_followup_observations의 +60~75분 첫 SUCCESS valid bid를 쓴다.
   window 밖/부재 quote는 censor하고 forward-fill/0-return 대체를 하지 않는다.
8) event 안에서 먼저 평균낸 뒤 event-equal arm 결과를 만들고, 같은 event/시간창은
   네 팔 사이에서 paired/clustered observation으로 취급한다.
9) primary B의 50 signals/30 events, 98.75% CI, 10.4bps stress, 양쪽 half,
   quote coverage와 cohort 안정성을 모두 판정한다.
10) B 실패를 A/C/D observed winner로 바꾸지 않는다.
11) 통과해도 SHADOW_REVIEW_ONLY이며 LIVE 승인으로 쓰지 않는다.
```

## 1. 고정 DB와 기간

네 Jenkins job과 SQLite가 서로 달라야 한다.

```bash
export REVIEW_START=2026-08-01T00:00:00Z
export REVIEW_END_EXCLUSIVE=2026-08-31T00:00:00Z
export REVIEW_AS_OF=2026-08-30
export REVIEW_DAYS=30
export KIWI_A_DB=/absolute/path/golden-kiwi/data/kiwi-sim-a-3x1/trades_sim.db
export KIWI_B_DB=/absolute/path/golden-kiwi/data/kiwi-sim-b-3x2/trades_sim.db
export KIWI_C_DB=/absolute/path/golden-kiwi/data/kiwi-sim-c-5x1/trades_sim.db
export KIWI_D_DB=/absolute/path/golden-kiwi/data/kiwi-sim-d-5x2/trades_sim.db
export RETRO_OUTPUT="$HOME/polybot-retro/kiwi-$REVIEW_AS_OF"
```

시작과 끝은 `[REVIEW_START, REVIEW_END_EXCLUSIVE)` UTC 반개구간이다.
`polybot-retro --as-of`는 포함 종료일 `YYYY-MM-DD`를 받아 그 다음 날 00:00Z를
exclusive end로 만들기 때문에 `REVIEW_AS_OF`에는 exclusive end의 전날을 넣는다. 각 DB:

```bash
shasum -a 256 "$KIWI_A_DB" "$KIWI_B_DB" "$KIWI_C_DB" "$KIWI_D_DB"
sqlite3 "$KIWI_A_DB" 'PRAGMA quick_check;'
sqlite3 "$KIWI_B_DB" 'PRAGMA quick_check;'
sqlite3 "$KIWI_C_DB" 'PRAGMA quick_check;'
sqlite3 "$KIWI_D_DB" 'PRAGMA quick_check;'
```

실행 중 DB는 `cp`하지 않는다. online backup과 manifest를 만든 후 그 immutable copy를
분석한다.

```bash
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"
```

Strict audit는 DB마다 별도 JSON으로 만든다. 분석기는 `database_count=1`, exact DB path,
exact window, DB `status=PASS`, aggregate CRITICAL/HIGH=0이며
`database_sha256`이 분석할 immutable DB 실제 바이트와 일치하는 JSON만 받는다. audit 뒤
DB가 한 바이트라도 바뀌면 기존 PASS JSON을 재사용하지 않고 backup부터 다시 만든다.

```bash
uv run --project polybot-observability polybot-retro audit \
  --db "$KIWI_A_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT/A" \
  --strict
uv run --project polybot-observability polybot-retro audit \
  --db "$KIWI_B_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT/B" \
  --strict
uv run --project polybot-observability polybot-retro audit \
  --db "$KIWI_C_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT/C" \
  --strict
uv run --project polybot-observability polybot-retro audit \
  --db "$KIWI_D_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT/D" \
  --strict
```

네 audit가 모두 PASS일 때만 v2 분석기를 실행한다.

```bash
uv run --project golden-kiwi python golden-kiwi/scripts/analyze_experiment.py \
  --db "A=$KIWI_A_DB" --db "B=$KIWI_B_DB" \
  --db "C=$KIWI_C_DB" --db "D=$KIWI_D_DB" \
  --strict-audit "A=$RETRO_OUTPUT/A/retro-audit.json" \
  --strict-audit "B=$RETRO_OUTPUT/B/retro-audit.json" \
  --strict-audit "C=$RETRO_OUTPUT/C/retro-audit.json" \
  --strict-audit "D=$RETRO_OUTPUT/D/retro-audit.json" \
  --start "$REVIEW_START" --end "$REVIEW_END_EXCLUSIVE" \
  --output "$RETRO_OUTPUT/kiwi-analysis-v2.json"
```

## 2. 팔별 불변 계약

| 항목 | A | B primary | C | D |
|---|---:|---:|---:|---:|
| positive steps | 3 | 3 | 5 | 5 |
| cumulative floor | +1%p | +2%p | +1%p | +2%p |
| job | `kiwi-sim-a-3x1` | `kiwi-sim-b-3x2` | `kiwi-sim-c-5x1` | `kiwi-sim-d-5x2` |

다음은 전 팔에서 같아야 한다.

- mode=`sim`, order amount $5, 실제 credential 없음
- YES `[0.20,0.80]`, resolution ≥6h
- 각 step `(0,0.02]`, cumulative cap 0.04, gap `[3m,10m]`
- liquidity ≥$20k, volume24h ≥$10k, spread ≤0.02
- exact excluded tags 집합
- hold 60m, target 이후 +15m outcome window
- event 1개, 6h cooldown, 전체 position 3, open notional $15, 신규 1/cycle
- 60일 raw cadence archive, no cold rollup
- 네 arm이 같은 Git commit을 사용하고, arm별 explicit Jenkins trigger와 contract offset이
  `A=0`, `B=1`, `C=2`, `D=3`으로 일치

`strategy_configs`에서 다른 값이 하나라도 보이면 protocol deviation으로 별도 cohort에
격리한다. 사후에 “거의 같은 설정”으로 합치지 않는다.

## 3. Evidence gate

각 팔과 cohort별로 아래 coverage를 표로 남긴다.

### Run과 sweep

- expected build 수, SUCCESS/FAILED/RUNNING
- `micro_cascade_experiment_contracts`의 shared UTC window, canonical arm/job,
  preregistration hash, schema/analyzer version, 5분 cadence와 job offset
- window의 expected 5분 slot 수, offset에 맞는 SUCCESS run 수, coverage, duplicate/missed
  slot, 최대/중앙/p95 run gap과 cycle runtime. off-schedule 또는 duplicate SUCCESS
  run이 있으면 해당 signal/follow-up을 제외하고 전체 promotion 판정을 fail-closed
- unknown Git commit/config hash
- cursor-complete sweep 비율, membership digest, 원자적 catalog+snapshot commit

### Raw cadence와 lineage

- 최근 60일 `market_snapshots`가 raw cadence 정책인지
- eligible condition의 3~10분 gap coverage ≥90%
- current snapshot이 해당 run에 생성됐는지
- 모든 lineage row가 현재 row와 같은 config hash, Git commit, mode, job인지
- arm별 exact `steps+1` snapshot ID가 오름차순·동일 condition인지
- price `[0.16,0.84]` archive envelope 위반
- Gamma page 로컬 수신시각이 snapshot clock으로 보존됐는지
- server-side archive liquidity floor `$1,000` 아래에서 entry universe로 급등해
  lineage가 없는 시장을 backfill하지 않고 censor했는지
- compact metadata가 60일 hot window보다 작지 않은지

### Entry/exit identity

- strict standard binary, explicit `negRisk=false`
- point-in-time catalog first-seen ≤ entry
- event ID join과 same-event sibling 선택
- event별 승자를 liquidity 내림차순·condition ID 오름차순으로 전역 정렬한 뒤
  fresh gate를 처음 통과한 최대 1개/cycle이라는 cross-event sampling rule
- append-only raw funnel의 sibling/event/global rank, cooldown, portfolio/drawdown,
  fresh attempt/book/depth/pass/fail/selection/trade link 완전성
- raw-selected population의 point-in-time `snapshot_best_ask`와, main sweep의
  확률/유동성/horizon/closed 필터와 독립적인 condition lookup으로 얻은 +60~75분 첫
  SUCCESS-run valid `best_bid` coverage
- FAILED source/observing run 행이 denominator/numerator에서 제외됐는지, quote 부재와
  source 오류가 명시적 reason으로 남았는지
- simulation entry subset의 단일 CLOB book midpoint/bid/ask/spread/depth coverage
- archive snapshot probability와 execution 직전 단일-book midpoint 차이
- persisted lineage 시각/가격/gap과 fresh decision 시각/가격/gap/source의 분리
- 60분 target 이후 첫 실행 시각, `exit_delay_minutes`
- target+15분 이내 quote coverage
- resolution과 simulation SELL의 분리
- `experiment_state.drawdown_kill_switch` 최초 trip 시각·run·수치와 trip 이후 BUY 0건

Kiwi는 live submission이 없어야 한다. non-`SIM_` order ID, live mode run, confirmed live
fill, actual wallet credential 의존이 하나라도 있으면 안전 계약 위반이다.

## 4. 분석 population

세 층을 분리한다.

| 층 | 정의 | 용도 |
|---|---|---|
| archived eligible | archive envelope에 저장된 condition | coverage denominator |
| raw signal | exact persisted staircase가 성립한 append-only decision | funnel·희귀성 |
| raw-selected | frozen event/global rank와 cooldown에서 선택된 최대 1개/run | primary denominator |
| quote-complete outcome | raw-selected + entry ask + 60~75m 첫 valid bid | primary return |

signal이 position/event/cycle cap 때문에 주문되지 않은 경우도 counterfactual signal 표에는
남긴다. 다만 simulation entry subset과 섞지 않고 selection rate를 별도로 보고한다.
같은 run에서 event별 승자는 frozen cross-event 순서인 liquidity 내림차순,
`condition_id` 오름차순으로 번호를 매긴다. fresh book 실패로 다음 순위가 진입한 경우도
원래 순위와 탈락 이유를 보존한다.

Primary raw/counterfactual outcome:

```text
top_of_book_return = exit_best_bid / entry_best_ask - 1
cost_stressed_return = top_of_book_return - 0.00104
```

entry quote는 `micro_cascade_signal_decisions.snapshot_best_ask`, exit quote는
`micro_cascade_followup_observations.best_bid`다. Source decision과 observing run 모두
SUCCESS이고, `target=scan_evaluated_at+60m` 이후 +75분까지 기록된 valid quote 중 가장
이른 행만 quote-complete다. analysis end 전에 +75분이 지나지 않은 signal은 coverage
denominator에서도 빼는 right-censoring 대상이다.

simulation `trades`만 denominator로 쓰면 cap/cooldown/fresh selection 탈락 신호가
빠지므로 promotion 판정을 금지한다. execution 직전 CLOB 단일-book 결과와 runtime 가상
P&L은 별도 secondary selection/TOCTOU 진단이다.

DB의 `$5` 가상 P&L은:

```text
(exit_best_bid - entry_limit_price) × simulated_shares
```

두 metric은 목적이 다르므로 하나로 대체하지 않는다. 첫 번째는 frozen 비교 metric,
두 번째는 runtime diagnostic이다. 둘 다 actual fill P&L이 아니다.

## 5. Dependence와 통계

1. signal 수와 unique event 수를 함께 보고한다.
2. event 안의 signal return을 먼저 평균한다.
3. event mean을 같은 가중치로 평균한다.
4. seed `20260730`, event-cluster bootstrap 20,000회를 사용한다.
5. 네 팔 Bonferroni 98.75% percentile lower bound를 보고한다.
6. event가 1개면 `[관측값, 관측값]`으로 꾸미지 않고 CI `NA`로 둔다.
7. OOS 기간을 미리 정한 midpoint로 둘로 나누고 양쪽 부호를 보고한다.
8. arm 간 paired table은 설명용 secondary 분석으로만 추가할 수 있고 primary gate를
   바꾸지 않는다.

사전 고정한 arm별 offset이 다르므로 동일 condition이라도 몇 분 차이가 날 수 있다.
이를 네 개의 완전 독립 시장 사건으로 세지 않는다. event × 사전 정의 시간창을 cluster
key로 사용하고 entry timestamp 차이도 공개한다.

## 6. Funnel 표

각 arm에 대해 아래를 채운다.

| arm | archived | raw signal | event-selected | raw-selected | simulated entry | 60~75m quote | events | coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A |  |  |  |  |  |  |  |  |
| B |  |  |  |  |  |  |  |  |
| C |  |  |  |  |  |  |  |  |
| D |  |  |  |  |  |  |  |  |

탈락 이유도 `excluded_category`, `resolution_too_close`, `low_liquidity`,
`low_volume`, `lineage`, `spread`, `depth`, `event/cooldown/cap`, `late_exit`로
분해한다. threshold를 낮춰 funnel을 사후에 채우지 않는다.

## 7. 결과 표

| arm | signals | events | quote n | event-equal return | 98.75% lower | -10.4bps lower | early | late |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A |  |  |  |  |  |  |  |  |
| **B** |  |  |  |  |  |  |  |  |
| C |  |  |  |  |  |  |  |  |
| D |  |  |  |  |  |  |  |  |

보조 표:

- entry probability 10%p bucket
- liquidity/volume quintile
- spread bucket
- 3-step/5-step elapsed window
- event/category
- config/Git/run collection cohort
- 60~65m, 65~70m, 70~75m exit delay

보조 slice는 설명용이며 primary arm이나 threshold를 바꾸는 근거가 아니다.

## 8. 판정

### STOP / UNRESEARCHABLE

다음 중 하나면 종료한다.

- 30일 뒤 primary B quote-complete n<50 또는 events<30
- B 98.75% lower CI≤0 또는 추정 불가
- 10.4bps stress 후 lower CI≤0
- early/late 중 하나가 0 이하
- quote coverage<90%
- arm 안에 둘 이상의 collection cohort가 존재하거나 네 arm의 Git commit이 다름
- 한 event를 제거하면 edge 소멸
- strict audit CRITICAL/HIGH 존재
- drawdown 영구 latch가 손상됐거나 trip 시각 이후 신규 BUY가 존재
- 5분 cadence를 rollup 또는 forward-fill로 복원해야만 결과가 생김

A/C/D가 좋아도 B 실패를 구제하지 않는다. 새 가설을 원하면 기존 결과와 분리된 새
preregistration·새 코드·새 기간으로 시작한다.

### SHADOW_REVIEW_ONLY

Primary B가 아래를 모두 충족할 때만 이 상태를 열 수 있다.

- signals≥50, events≥30
- 98.75% lower CI>0
- 10.4bps stress lower CI>0
- early/late 양수
- quote coverage≥90%
- arm별 단일 cohort와 네 arm의 shared Git commit
- strict evidence gate 통과

이는 live가 아니다. 다음 단계에서 depth, queue, latency, exact confirmed fill, fee/role,
partial fill과 reconciliation을 설계할 권한만 준다.

분석기 verdict는 다음 세 값만 사용한다.

- `NOT_EVALUABLE_FAIL_CLOSED`: contract, strict audit, cadence, cohort 증거 중 하나라도 불완전
- `FAIL_NO_SHADOW_REVIEW`: 증거는 완전하지만 primary B 수치 gate 실패
- `ELIGIBLE_FOR_SHADOW_EXECUTION_REVIEW`: 모든 gate 통과. live 승인 아님

### LIVE

이 문서로는 선택할 수 없는 상태다. source hard block을 제거하려면 별도 사용자 승인,
새 risk budget, shadow 결과, reviewed code change와 새 preregistration이 필요하다.

결론은 다음 형식으로 남긴다.

```text
Decision: STOP | UNRESEARCHABLE | CONTINUE_COLLECTION | SHADOW_REVIEW_ONLY
Evidence window:
Primary arm: B
Signals / event clusters:
Quote coverage:
Event-equal return:
98.75% CI:
10.4bps-stressed 98.75% CI:
Early / late:
Strict audit:
Largest event contribution:
Primary failure mode:
Next action:
```

## 9. 기존 frozen 결과의 역할

[`golden-kiwi/research/frozen-2026-07-30/`](../../golden-kiwi/research/frozen-2026-07-30/)
구간은 이미 열어 본 data다. 새 30일 결과와 합쳐 표본을 늘리거나 threshold를 다시 고르는
test set으로 쓰지 않는다. immutable 산출물에는 당시 계산을 그대로 남기되, 독립
재검토 정정문을 함께 적용한다.

1. 기존 evidence에서 네 팔 모두 실패했다.
2. 다음 검정의 primary, metric과 gate는 B 기준으로 이미 정해졌다.
3. C의 `+0.5263%`는 서로 다른 Git commit을 이은 무효 lineage라 promotion 해석에서
   철회됐다.
4. 당시 DB는 snapshot-level strict-binary/`negRisk`를 증명하지 못하므로 A/B/C/D
   수치를 현재 evidence gate에 합격한 표본으로 재사용하지 않는다.

Frozen artifact 검증:

```bash
cd golden-kiwi/research/frozen-2026-07-30
shasum -a 256 -c MANIFEST.sha256
```
