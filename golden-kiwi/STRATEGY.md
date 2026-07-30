# Golden Kiwi 전략 명세 — Micro-Cascade

## 1. 결정

Golden Kiwi는 네 개의 독립 simulation 팔로 **짧은 연속 상승의 60분 지속성**을 검증한다.
현재 상태는 `RESEARCH_ONLY`이며 live execution은 source-level hard block이다.

이 전략을 폐쇄 전략들에서 자동으로 도출된 “최고 수익 전략”이라고 부르지 않는다. 사용
가능한 DB로 사전 등록 OOS 검정을 했을 때 primary B를 포함한 네 팔이 모두 승격 gate를
실패했기 때문이다. 지금 정당화할 수 있는 표현은 다음뿐이다.

> 실패 전략의 위험을 제거하고, 5분 데이터에서 다음으로 검증할 가치가 있는 단순하고
> 반증 가능한 하나의 심리 가설을 구현했다.

## 2. 행동 가설

시장 참여자는 같은 정보를 동시에 완전하게 해석하지 않는다. 누군가의 거래가 다른 사람의
정보가 되어 순차적으로 따라붙는다면, 한 번의 급등보다 **3~5회의 작고 연속적인 YES
상승**이 짧은 사회적 정보 cascade일 수 있다. 이 15~25분 staircase가 이후 60분의 실행
가능한 가격에도 남는지를 검정한다.

이 아이디어는 다음 시장행동 문헌과 방향은 맞지만, 문헌이 Polymarket의 수익성을
보증하지는 않는다.

- 정보가 거래를 통해 점진적으로 집계될 수 있다는 예측시장 연구:
  [Bossaerts et al., Prediction Markets as an Information Aggregation Tool](https://arxiv.org/abs/2209.08778)
- 가격발견 속도와 주문 흐름을 분리해 보는 시장미시구조 관점:
  [Docherty & Easton, Order Flow and Price Discovery](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1772811)
- 정보 cascade와 타인의 선택을 따라가는 행동:
  [Nöth & Weber, Information Aggregation with Random Ordering](https://doi.org/10.1111/1468-0297.00091)

### 경쟁 설명

가설이 틀릴 이유를 먼저 둔다.

1. 5분 양의 step은 stale midpoint나 얇은 호가의 bounce일 수 있다.
2. 평균적으로 단기 return autocorrelation이 음수라 상승 뒤 되돌림이 더 강할 수 있다.
3. 뉴스 한 건이 여러 snapshot에 나뉘어 보일 뿐, 사람들의 cascade가 아닐 수 있다.
4. `best ask → best bid` 비용이 작은 drift를 전부 지울 수 있다.
5. 한 event의 여러 condition이 함께 움직여 signal 수만 부풀 수 있다.
6. 5분 Jenkins schedule이 실제로는 10분 이상 벌어져 다른 현상을 측정할 수 있다.

## 3. 폐쇄 전략에서 배운 설계 제약

| 폐쇄 전략의 실패 | Kiwi에서 강제한 제약 |
|---|---|
| Lime: 6시간 shock-follow가 비용을 넘지 못함 | 단일 shock 제외, 3~5개의 작은 양의 step만 검정 |
| Fig: 전 가격대 tail fade calibration 음수 | tail side를 고르지 않고 표준 이진 YES 0.20~0.80로 제한 |
| Mango: carry hurdle이 시간가치보다 손실확률을 탐지 | APY/carry score 제거, 고정 60분 outcome |
| Date: 음의 edge를 높은 turnover가 증폭 | $5 simulation, 최대 3 position, cycle당 1개 |
| Honeydew: snapshot replay와 confirmed fill 성과 부호 불일치 | quote counterfactual을 actual P&L로 부르지 않음 |
| Nectarine: 120h calendar mean reversion 음수·단일 event 의존 | 6시간 event cooldown, event-equal 평가, 60분 고정창 |

실패 전략의 threshold를 섞어 새 composite score를 만들지 않았다. 관측 가능한 두 변수
`연속 횟수 × 누적 상승 하한`만 처치한다.

## 4. Point-in-time universe

진입 snapshot에서 모두 참이어야 한다.

1. `SUCCESS` run과 cursor-complete Gamma sweep에 연결된 현재 관측
2. outcomes가 정확히 `["Yes", "No"]`
3. 서로 다른 CLOB token 두 개와 정확히 두 outcome price
4. `negRisk=false`가 명시된 표준 이진 시장
5. catalog가 entry 이후 정보로 보강되지 않은 point-in-time 시장
6. `endDate`까지 6시간 이상
7. 현재 YES `0.20 <= p <= 0.80`
8. liquidity ≥ $20,000
9. `volume24hr` ≥ $10,000
10. 유효한 `0 < best_bid <= best_ask < 1`, spread ≤ 0.02
11. `event_id`가 존재
12. 아래 exact tag가 하나도 없음

```text
sports
games
esports
crypto-prices
up-or-down
multi-strikes
5m
15m
1h
```

tag는 slug/label의 대소문자를 무시한 exact match다. `sports-politics`는 `sports`와 같지
않다. 이 제외 집합은 팔별로 바꿀 수 없다.

Archive는 진입 밴드보다 넓은 YES `[0.16, 0.84]`를 저장한다. Gamma keyset fetch에는
서버측 유동성 하한 `$1,000`을 적용한다. 이는 `$20,000` entry gate와 다른 수집 비용
하한이다. `$1,000` 아래에서 갑자기 진입 gate 위로 올라온 시장은 이전 관측을 추정하거나
backfill하지 않고 exact 3/5-step lineage가 새로 쌓일 때까지 제외한다. 60일 동안 실제
cadence row를 보존하고 cold rollup을 하지 않는다.

각 snapshot 시각은 전체 keyset sweep 종료시각이 아니라 해당 시장이 포함된 **Gamma
page를 로컬에서 받은 시각**이다. sweep이 수분 걸려도 모든 시장을 같은 시각에 본 것처럼
기록하지 않는다.

## 5. Frozen 2 × 2 treatment

공통 staircase:

```text
관측 개수 = confirmation_steps + 1
각 관측 gap = 3분 이상 10분 이하
각 step = 0보다 크고 0.02 이하
누적 step = arm 하한 이상 0.04 이하
마지막 YES = 0.20 이상 0.80 이하
```

| arm | `confirmation_steps` | `min_cumulative_move` | 사전 역할 |
|---|---:|---:|---|
| A | 3 | 0.01 | loose sensitivity |
| **B** | **3** | **0.02** | **primary** |
| C | 5 | 0.01 | longer-confirmation sensitivity |
| D | 5 | 0.02 | strict sensitivity |

B가 primary다. A/C/D는 B가 실패했을 때 observed winner로 교체할 후보가 아니라
희귀성·확인 길이·minimum move에 대한 동시 민감도 검사다.

## 6. Event 선택과 재진입

같은 collecting run에서 한 event의 여러 condition이 신호를 내면:

1. entry snapshot liquidity가 큰 condition
2. 동률이면 `condition_id` 오름차순

으로 하나만 선택한다. 같은 event는 condition이 달라도 6시간 동안 다시 진입하지 않는다.
그 다음 event별 승자를 전체 liquidity 내림차순, `condition_id` 오름차순으로 정렬한다.
그 순서대로 fresh book gate를 검사해 처음 통과한 최대 1개만 그 cycle에 진입한다.
동시에 event당 1 position, 전체 3 position, 신규 1 position/cycle만 허용한다. 이
cross-event 순서는 결과를 본 뒤 바꾸지 않는 frozen sampling rule이다.

scanner는 주문된 후보만 남기지 않는다. 정확한 staircase를 만든 모든 condition을
`micro_cascade_signal_decisions`에 append-only로 기록하고, sibling 수와 event rank,
event winner의 global rank, 6시간 raw-signal cooldown, 당시 position/open notional,
drawdown, fresh attempt 순서·book·depth·통과/실패 사유·trade ID를 분리한다.
`raw_selected`는 fresh execution 성공과 관계없이 frozen global 순서에서 처음
cooldown-eligible한 event winner 하나다. FAILED run의 행은 보존하되 결과 분석에서는
사용하지 않는다.

## 7. Fresh simulation entry

scanner의 persisted lineage만으로 가상 진입하지 않는다. execution 직전에:

1. CLOB order book을 한 번 읽어 bid, ask, spread와 ask depth를 함께 고정
2. 같은 book의 `(best bid + best ask) / 2`로 마지막 staircase price를 교체
3. book이 돌아온 로컬 시각으로 마지막 snapshot gap을 다시 계산
4. `endDate`까지 6시간 이상인지 같은 시각 기준으로 다시 계산
5. best ask부터 최대 `min(0.80, best ask + 0.01)`까지의 ask depth 검사
6. `$5 / limit_price`로 계산한 shares가 `5.0 + 0.1` shares 이상인지 검사
7. 실제 ask depth가 계산 shares의 1.2배 이상인지 검사

모두 통과하면 simulation order ID와 `HOLDING`을 기록한다. wallet 서명이나 실제 CLOB
POST는 없다. entry persisted snapshot IDs/시각/가격/gap과 최종 decision
시각/가격/gap/source, signal book, fresh book, config/run/Git provenance를 구분해
저장한다. simulation ledger와 trade row에는 tick 반올림하지 않은 같은 관측 가격을 쓴다.

## 8. Exit와 outcome

### 8.1 Time exit

```text
target = buy_timestamp + 60분
exit = target 이후 첫 실제 bot cycle의 fresh best bid
hypothetical_pnl = (exit best bid - entry limit price) × shares
```

price target, stop loss, trailing stop은 없다. 실제 elapsed와 `exit_delay_minutes`를
저장한다. target 이후 15분을 넘긴 outcome은 운영 기록에는 남기되 frozen 60~75분
promotion 분석에서 censor한다.

이 trade exit은 **secondary runtime diagnostic**이다. Position cap, cooldown, fresh
book과 depth 때문에 raw population보다 작기 때문이다. Primary counterfactual은
`raw_selected` 시점의 append-only `snapshot_best_ask`를 진입가로 쓰고, signal
+60분부터 +75분까지 condition ID를 Gamma에서 직접 조회해 얻은 첫 SUCCESS-run valid
`best_bid`를 종료가로 쓴다.

후속 조회는 main tradable/archive sweep의 확률·유동성·시간·closed 필터를 재사용하지
않는다. 시장이 종료됐거나 유동성이 $1,000 아래로 내려가도 condition lookup을 시도한다.
각 cycle의 부재·invalid quote·source 오류도
`micro_cascade_followup_observations`에 append-only로 기록하고, FAILED observing run은
분석에서 제외한다. window 안의 valid quote가 없으면 censor하며 0이나 마지막 가격으로
채우지 않는다.

### 8.2 무엇이 실제 수익이 아닌가

- simulation 결과는 `hypothetical_pnl`; `realized_pnl=NULL`
- top-of-book은 depth 전체, queue, latency, partial fill과 fee를 증명하지 않음
- order ID나 accepted response는 fill이 아님
- resolution payout은 CLOB SELL, redeemable 상태, actual redeem transaction과 다름
- quote가 없으면 마지막 가격을 복사하거나 0% return으로 채우지 않음

closed + final `[1,0]`, `[0,1]`, `[0.5,0.5]` Gamma evidence가 있으면 resolution을
별도로 기록하고 synthetic SELL을 만들지 않는다.

## 9. Risk contract

```text
simulation amount              = $5
assumed experiment capital     = $100
drawdown kill switch           = -20% = -$20 research economic P&L
max simultaneous positions     = 3
max open notional              = $15
max positions per event        = 1
max new positions per cycle    = 1
event reentry cooldown         = 6h
```

같은 `config_hash × git_commit × mode × job_name`의 SUCCESS entry/terminal run에
연결된 finite `hypothetical_pnl + settlement_pnl_assumption`만 시간순으로 합산한다.
현재 합계가 회복됐더라도 경로 중 최초 -$20 crossing이 있으면 이후 신규 simulation BUY를
차단한다. 이 평가는 후보 유무와 무관하게 cycle 시작에 실행된다.

감지 중인 RUNNING run은 먼저
`experiment_state.drawdown_kill_switch_pending:<detector-run>`만 기록한다. detector가
FAILED면 pending을 폐기하고, SUCCESS가 된 뒤에만 최초 terminal source run·시각·당시
P&L·자본·한도를 영구 `drawdown_kill_switch`로 원자적으로 승격한다. 성공 직후 프로세스가
끊겨도 다음 시작에서 SUCCESS pending을 확정한다. 이후 settlement로 합계가 -$20 위로
회복되거나 새 프로세스가 시작되어도 자동으로 풀리지 않는다. 손상된 latch는 OFF로
해석하지 않고 cycle을 fail closed한다. 같은 DB에서 row를 지우거나 값을 고쳐 재개하는
것은 금지하며, 새 실험에는 새 preregistration·canonical job·DB·검토가 필요하다. 이 값은
실제 지갑 drawdown이 아니라 finite research stopping rule이다.

원자적 archive/market sweep 또는 RunAudit 실패는 cycle 전체를 중단한다. 개별 시장의
lineage/book/event 결함은 그 시장만 제외한다.

## 10. 기존 OOS 결과와 증거 정정

2026-07-30에 arm 결과를 보기 전에 protocol을 SHA-256
`0a2e6537320f27254d3235629652afb97af15a25bc6304f2836cd618e1c28006`으로 고정했다.
주요 evidence는 동기화된 Honeydew DB의 full-cadence snapshot 1,293,610행이었다.

Primary metric:

```text
executable_return = exit_best_bid / entry_best_ask - 1
```

| arm | 과거 cooldown-carried OOS n/events | 과거 event-equal return | 과거 strict event-purged n/events | 판정 |
|---|---:|---:|---:|---|
| A | 4 / 2 | -1.0234% | 0 / 0 | FAIL |
| B | 2 / 2 | -1.8072% | 1 / 1, +0.1355% | FAIL |
| C | 1 / 1 | +0.5263% | 1 / 1, +0.5263% | FAIL |
| D | 0 / 0 | NA | 0 / 0 | FAIL |

이 표는 immutable historical output이다. 독립 재검토에서 strict C의 유일한 신호가
Git commit `4c69c9…`의 이전 관측과 `9b648b…`의 마지막 관측을 이은 것을 확인했다.
또한 원본 Honeydew snapshot에는 관측 당시 outcomes/token/tags/`negRisk` 사본이 없어
point-in-time strict-binary 계약을 증명할 수 없다. 따라서 C의 양수 해석은 철회하며
A/B/C/D 전체를 현재 promotion evidence로 사용하지 않는다.

Primary B는 낙관적인 과거 계산에서도 표본·CI·전반/후반 gate를 실패했으므로
`FAIL_NO_LIVE_RECOMMENDATION`은 변하지 않는다. 상세 정정은
[`research/2026-07-30-cohort-correction.md`](research/2026-07-30-cohort-correction.md)를
따른다.

## 11. 다음 30일 독립 검정

네 job은 같은 Git commit, 같은 고정 UTC 30일 반개구간, 명시적 5분 schedule을
사용하되 별도 job name·offset·SQLite를 쓴다.

| arm | job | 처치값 | trigger / offset |
|---|---|---|---|
| A | `kiwi-sim-a-3x1` | `3`, `0.01` | `0-59/5 * * * *` / `0` |
| B | `kiwi-sim-b-3x2` | `3`, `0.02` | `1-59/5 * * * *` / `1` |
| C | `kiwi-sim-c-5x1` | `5`, `0.01` | `2-59/5 * * * *` / `2` |
| D | `kiwi-sim-d-5x2` | `5`, `0.02` | `3-59/5 * * * *` / `3` |

각 DB의 `micro_cascade_experiment_contracts`는 canonical arm/job, schema/analyzer version,
preregistration hash, UTC `[start,end)`, 5분 cadence와 offset을 최초 한 번 저장하고
UPDATE/DELETE를 거부한다. 세 collection env가 없으면 smoke/archive mode이며 그
decision은 `collection_eligible=0`이다. 일부만 설정하거나, 같은 DB에서 계약을 바꾸거나,
30일이 아닌 window를 쓰면 시작하지 않는다.

분석 시 실제 SUCCESS run 시각이 전체 expected slot의 90% 이상인지 계산한다. 실제 gap
3~10분을 만족한 persisted lineage만 signal로 인정한다. lineage 전체는 같은
`config_hash × git_commit × mode × job_name`이어야 하며, 현재 run의 마지막 row와
이전 SUCCESS/cursor-complete row만 허용한다. 정규 offset 밖의 SUCCESS run 또는 같은
slot의 중복 SUCCESS run은 primary signal과 follow-up에서 제외하며, 존재 자체가 cadence
contract 실패이므로 promotion을 fail-closed한다.

### Primary B promotion gate

모두 만족해야 한다.

1. mature raw-selected signals ≥ 50
2. unique event clusters ≥ 30
3. event-cluster Bonferroni 98.75% lower CI > 0
4. 10.4bps cost stress 후 98.75% lower CI > 0
5. predeclared early/late half 모두 양수
6. earliest valid +60~75m target/quote coverage ≥ 90%
7. arm별 collection cohort가 정확히 하나이고 네 arm의 Git commit이 동일
8. run/sweep/catalog/lineage provenance와 고정 30일 5분 run cadence coverage 충족
9. strict retro audit CRITICAL/HIGH = 0이며 audit `database_sha256`과 분석 DB가 일치

통과 결과가 승인하는 것은 다음 단계의 **shadow execution review**뿐이다. 실제 주문을
허용하려면 별도 source change, preregistration, 계정/자본 승인과 다음 evidence가
필요하다.

- $5 양쪽 depth와 latency
- BUY/SELL exact confirmed full fill
- fee amount와 maker/taker role
- partial/zero/unknown fill
- reconciliation 및 uncertain intent coverage

B가 실패하거나 30일 뒤 50/30 표본을 못 채우면 threshold를 완화하지 않고
`STOP / UNRESEARCHABLE`로 판정한다.

## 12. 재현과 감사

고정 연구 산출물:

```bash
cd golden-kiwi/research/frozen-2026-07-30
shasum -a 256 -c MANIFEST.sha256
```

운영 DB 회고는 arm마다 별도 output directory와 exact-window JSON을 만든다. 아래 명령을
`A/B/C/D` 각각의 DB에 반복한다.

```bash
uv run --project polybot-observability polybot-retro audit \
  --db "$KIWI_A_DB" \
  --days 30 \
  --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT/A" \
  --strict
```

그 뒤 append-only raw evidence 분석을 실행한다.

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

분석 결과가 `NOT_EVALUABLE_FAIL_CLOSED`면 evidence부터 복구한다. 증거가 완전한 상태의
수치 실패는 `FAIL_NO_SHADOW_REVIEW`, 모든 gate 통과는
`ELIGIBLE_FOR_SHADOW_EXECUTION_REVIEW`이며 live 승인은 아니다. 평가 절차와 결과 표
형식은
[`docs/retro/golden-kiwi.md`](../docs/retro/golden-kiwi.md)를 따른다.
