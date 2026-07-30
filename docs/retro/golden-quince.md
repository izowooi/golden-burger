# golden-quince 30일 실행 실험 회고 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽는다.
> UTC half-open range `[REVIEW_START, REVIEW_END_EXCLUSIVE)`를 고정하고,
> `polybot-retro audit --strict`의 `CRITICAL`/`HIGH` issue가 0이 되기 전에는
> 결론·tuning·증액을 만들지 않는다.

전략은 **Spread Harvest**다. 첫 관측 0.90 상향 교차, 0.90~0.94 BUY, 0.98 target,
0.85 absolute stop, **고정 24시간 signal horizon**은 세 팔에서 같다. 처치는
진입 BUY의 `execution_mode` 하나뿐이며 SELL은 모든 팔에서 `nearest`다.

## 0. 사전 등록된 A/B/C

| 팔 | Jenkins job = `--job` | DB | BUY mode | 주문 | signal horizon | SELL | cadence |
|---|---|---|---|---:|---:|---|---|
| A | `polybot-quince-passive` | `data/polybot-quince-passive/trades.db` | `passive` | $5 | 24h | `nearest` | `H/5 * * * *` |
| B | `polybot-quince-nearest` | `data/polybot-quince-nearest/trades.db` | `nearest` | $5 | 24h | `nearest` | `H/5 * * * *` |
| C | `polybot-quince-cross` | `data/polybot-quince-cross/trades.db` | `cross` | $5 | 24h | `nearest` | `H/5 * * * *` |

각 팔은 별도 wallet/account/funder/credential, Jenkins job, `--job`, DB를 사용한다.
같은 wallet이나 DB를 공유하면 live A/B/C 격리가 아니다. Git commit, schedule,
threshold, risk, sports/in-play, archive, lifecycle은 동일하게 유지한다.

선택적 D는 별도 wallet/job/DB에서 A와 같은 `passive`에 주문액만 $10으로 바꾼
size-effect cohort다. **A/B/C가 우선**이며 D를 1차 실행-mode 비교에 합치지 않는다.

## 1. 30일 판정 요약

이 전략의 판정은 승률이나 순손익이 아니라 **같은 신호의 진입 실행비용 차이**다.

| endpoint | 근거 | 사전 예측 / 규칙 |
|---|---|---|
| MAKER 체결 비중 | `order_fills.liquidity_role`, CONFIRMED BUY | **A > B > C** |
| 진입 실효가 − 결정 midpoint | BUY VWAP 기준 bps | **A < B < C**, A는 음수 |
| 체결률 | accepted BUY 대비 CONFIRMED BUY | **A < B < C** |
| A의 진입 체결 수 | exact CONFIRMED BUY | 30일에 30건 미만이면 **판정 불가** |
| 역선택 | 체결 후 15/60분 midpoint drift | A의 진입 할인보다 작아야 계속 |
| 순손익 | fee-complete actual / settlement 분리 | 2차 관측값; 1차 결론에 사용 금지 |

실측 MAKER/TAKER 진입 비용 차이는 약 73 bps지만 종결 손익은 해결 결과가 지배한다.
따라서 30일 표본의 승률·순손익으로 execution treatment를 판정하지 않는다.

상시 경제손익(확정+해결 추정)이 `-$40` 이하가 되면 kill switch가 신규 진입을
차단해야 한다. 발동 시각 전후를 별도 cohort로 나누고 청산은 계속 추적한다.

## 2. 복붙용 회고 프롬프트

```text
docs/retro/EVIDENCE_CONTRACT.md와 docs/retro/golden-quince.md를 순서대로 읽어라.

REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END_EXCLUSIVE=<YYYY-MM-DD UTC>

1) UTC half-open range와 일치하는 REVIEW_DAYS와 --as-of 포함 종료일을 계산한다.
2) daily-rsync catalog에서 passive/nearest/cross DB를 발견하고 verify한 절대 경로만 쓴다.
3) 세 DB를 반복 지정해 polybot-retro audit --strict를 실행한다.
4) CRITICAL/HIGH 또는 evidence gap이 있으면 비교·tuning·증액을 중단한다.
5) config_hash × git_commit × mode × job_name cohort를 분리한다.
6) 세 팔이 $5, 24h, 동일 commit/cadence/signal/risk이고 BUY mode만 다른지 검증한다.
7) SELL이 모든 arm에서 nearest인지 resolved config와 order evidence로 확인한다.
8) exact order ID의 CONFIRMED BUY fill만 1차 execution endpoint에 사용한다.
9) event_id × crossing time window로 pair/cluster하고 같은 event를 독립 n으로 세지 않는다.
10) 30일 MAKER 비중, 진입 bps, 체결률과 15/60분 drift를 보고한다.
11) 승률과 순손익은 2차 관측값으로만 기록한다.
12) optional D $10 cohort는 A/B/C 1차 비교와 분리한다.
```

## 3. 기간과 evidence discovery

예시 30일 구간:

```bash
export REVIEW_START=2026-08-01
export REVIEW_END_EXCLUSIVE=2026-08-31
export REVIEW_AS_OF=2026-08-30
export REVIEW_DAYS=30
export RETRO_OUTPUT="$HOME/polybot-retro/quince-execution-$REVIEW_END_EXCLUSIVE"
```

`REVIEW_AS_OF`는 exclusive end의 전날이다. 보고서 첫머리에 timezone, half-open range,
각 DB의 `remote_path`, 검증된 local 절대 경로, SHA-256, latest successful sync,
DB `synced_at`, source cutoff를 기록한다.

```bash
cd daily-rsync
uv run daily-rsync locate --job polybot-quince-passive
uv run daily-rsync locate --job polybot-quince-nearest
uv run daily-rsync locate --job polybot-quince-cross
uv run daily-rsync verify --job polybot-quince-passive --strategy golden-quince
uv run daily-rsync verify --job polybot-quince-nearest --strategy golden-quince
uv run daily-rsync verify --job polybot-quince-cross --strategy golden-quince
cd ..
```

검증 뒤 실제 catalog 절대 경로를 넣는다. 디렉터리명만 보고 성공을 추정하지 않는다.

```bash
export QUINCE_PASSIVE_DB=/absolute/path/golden-quince/data/polybot-quince-passive/trades.db
export QUINCE_NEAREST_DB=/absolute/path/golden-quince/data/polybot-quince-nearest/trades.db
export QUINCE_CROSS_DB=/absolute/path/golden-quince/data/polybot-quince-cross/trades.db

uv run --project polybot-observability polybot-retro audit \
  --db "$QUINCE_PASSIVE_DB" \
  --db "$QUINCE_NEAREST_DB" \
  --db "$QUINCE_CROSS_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_AS_OF" \
  --output-dir "$RETRO_OUTPUT" \
  --strict
```

분석 시작 시 각 DB의 SHA-256과 `PRAGMA quick_check` 결과를 함께 보존한다.

## 4. 고정값 검증

각 DB의 `strategy_configs`와 run provenance에서 다음을 확인한다.

| 항목 | A | B | C |
|---|---:|---:|---:|
| first crossing | prior <0.90 → current 0.90~0.94 | 동일 | 동일 |
| target / stop | 0.98 / 0.85 | 동일 | 동일 |
| signal horizon | **24h** | **24h** | **24h** |
| snapshot gap | ≤15분 | 동일 | 동일 |
| order | **$5** | **$5** | **$5** |
| liquidity / volume | $10k / $2k | 동일 | 동일 |
| spread / depth | ≤0.02 / 1.2x | 동일 | 동일 |
| sports | 포함, in-play 360분 | 동일 | 동일 |
| BUY execution | `passive` | `nearest` | `cross` |
| SELL execution | `nearest` | `nearest` | `nearest` |
| cadence | `H/5 * * * *` | 동일 | 동일 |

표와 다른 config hash, commit, mode, job, amount 또는 horizon은 별도 cohort로 분리한다.
처치 외 차이가 있는 cohort를 A/B/C 인과 비교에 섞지 않는다.

## 5. Evidence gate

반드시 보고할 coverage:

- run SUCCESS/FAILED/RUNNING, schedule gap, unknown Git commit
- cursor-complete market sweep와 membership digest
- current/prior snapshot ID join, `0 < gap <= 15분`, 이전 0.90 이상 관측 여부
- market catalog와 event ID join
- sports `entry_time_reference`, game-start fallback, in-play phase
- BUY/SELL submission → status → CONFIRMED fill coverage
- partial fill, uncertain intent, stale reconciliation, terminal zero-fill
- BUY/SELL confirmed size 일치와 fee amount/role completeness
- decision midpoint와 exact BUY fill join coverage
- resolution evidence coverage
- redeemable/actual redeem ingestion 부재를 “미상환”으로 오해하지 않았는지

하나라도 `CRITICAL`/`HIGH`면 아래 집계는 진단용으로만 실행하고 결론을 만들지 않는다.

## 6. 1차 execution endpoint

실행 지표의 기본 모집단은 live BUY intent다. accepted/live/order ID 자체는 fill이 아니다.

```text
arm = config_hash × git_commit × mode × job_name
ordered = exact BUY submission
confirmed = order_fills.status = CONFIRMED and side = BUY
entry_vwap = exact confirmed BUY fills only
decision_midpoint = order decision에 연결된 same-cycle bid/ask midpoint
entry_cost_bps = (entry_vwap - decision_midpoint) / decision_midpoint * 10,000
```

팔별로 다음 표를 만든다.

| 지표 | 정의 |
|---|---|
| raw signal n | first-crossing lineage가 증명된 condition |
| accepted BUY n | exact BUY submission이 accepted/live가 된 수 |
| CONFIRMED BUY n | exact order ID에 CONFIRMED BUY가 있는 수 |
| MAKER n / 비중 | `liquidity_role='MAKER'`인 CONFIRMED BUY |
| entry cost bps | CONFIRMED BUY VWAP − decision midpoint |
| fill rate | CONFIRMED BUY / accepted BUY |
| fill latency | submission → first/full CONFIRMED BUY |

role이나 decision midpoint가 누락된 fill을 임의 분류하거나 보간하지 않는다. 누락률을
별도 endpoint coverage로 보고하고 비교 모집단에서 제외한다.

## 7. Pairing과 역선택

독립 market 수 대신 `event_id × crossing time window`를 분석 단위로 쓴다.

1. A/B/C 모두 같은 event-window를 본 pair/cluster
2. 두 팔만 공통인 event-window
3. 한 팔에만 주문된 event-window
4. 세 팔 모두 archive했지만 주문되지 않은 crossing

각 cluster에서 decision time, entry VWAP, liquidity role, fill latency, phase를 나란히
보인다. arm별 cadence가 모두 `H/5`여도 Jenkins hash offset 차이가 있을 수 있으므로
timestamp가 같은 척하지 말고 crossing window를 명시한다.

역선택은 CONFIRMED BUY 뒤 15분과 60분의 midpoint drift로 잰다. snapshot이 정확한
horizon을 덮지 않으면 nearest observation의 허용 오차를 사전 명시하고 coverage를
보고한다. A의 유리한 entry bps보다 후속 하락이 크면 스프레드 할인은 수익원이 아니다.

## 8. 2차 손익 집계

actual roundtrip P&L은 다음 조건을 모두 만족할 때만 계산한다.

```text
live only
BUY and SELL order_id both present
both sides have exact CONFIRMED fills
BUY confirmed size = SELL confirmed size
both orders fully reconciled
both-side fee amount known
```

```text
actual net P&L =
  (SELL confirmed VWAP - BUY confirmed VWAP) * confirmed size
  - confirmed BUY fee - confirmed SELL fee
```

`trades.realized_pnl`, requested size, midpoint, GTC response를 actual 값으로 대체하지 않는다.
resolution settlement assumption은 actual cash realization과 분리한다. arm별 순손익,
승률, drawdown은 보고하되 execution-mode 1차 판정에는 쓰지 않는다.

## 9. 30일 결정

| 조건 | 결정 |
|---|---|
| 상시 경제손익 ≤ -$40 | kill switch 확인, 신규 진입 중단, 전후 cohort 분리 |
| A CONFIRMED BUY < 30 | **INCONCLUSIVE** — 표본 부족 |
| A MAKER 비중이 B보다 높지 않음 | **IMPLEMENTATION FAIL** — 처치가 작동하지 않음 |
| A entry cost bps가 B보다 낮지 않음 | **STOP** — 실행 측면 가설 기각 |
| `A > B > C` / `A < B < C` 순서가 반복적으로 깨짐 | **STOP/DIAGNOSE** — 비용 모형 재검토 |
| 실행 순서 성립 + A drift가 할인보다 작음 | **CONTINUE**, 선택적 D $10 검토 |

결론 형식:

```text
Decision: INCONCLUSIVE | IMPLEMENTATION_FAIL | STOP | CONTINUE | ADD_OPTIONAL_D_10
Evidence window [start, end):
Verified DB SHA-256:
Cohorts:
CONFIRMED BUY n by arm:
MAKER share by arm:
Entry cost bps by arm:
Fill rate by arm:
15m / 60m adverse-selection drift:
Actual net P&L (secondary):
Kill-switch status:
Primary limitation:
Next review date:
```

## 10. 보존과 secret

Quince DB와 로그는 repository에 commit하지 않는다. 실행 중 DB를 `cp`하지 말고 online
backup과 SHA-256 manifest를 workspace 밖 durable storage에 보관한다. private key,
funder 실제 값, credential identifier를 보고서·로그·명령 history에 기록하지 않는다.

```bash
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"
```
