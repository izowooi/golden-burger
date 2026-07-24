# golden-queen 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽는다.
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정하고 `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` issue가 0이 되기 전에는 tuning·증액·promotion을 하지 않는다.

전략: **Crown Momentum** — strict standard binary YES의 첫 관측 0.90 상향 교차를
0.90~0.94에서 매수한다. 일반 시장은 종료까지 `(0h, 24h]`, 스포츠는 경기 전 또는 시작
후 360분 이내가 기본이다. 사전 해결 청산은 YES 0.98 목표와 0.85 절대 stop뿐이다.

## 0. 복붙용 프롬프트

```text
docs/retro/EVIDENCE_CONTRACT.md와 docs/retro/golden-queen.md를 순서대로 읽어라.

REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>

1) queen 24h/12h DB를 각각 명시해 polybot-retro audit --strict를 실행한다.
2) CRITICAL/HIGH가 하나라도 있으면 성과 비교·threshold 변경·증액을 중단한다.
3) config_hash × git_commit × mode × job_name cohort를 분리한다.
4) actual 결과는 exact order ID의 CONFIRMED BUY/SELL fill만 사용한다.
   full-size reconciliation과 fee completeness가 없는 행은 actual P&L에서 제외한다.
5) accepted/live/order ID와 simulation hypothetical P&L을 actual fill로 승격하지 않는다.
6) cursor-complete sweep, snapshot lineage, catalog/event ID, sports clock coverage를 검증한다.
7) 동일 event는 cluster 1개로 세고 12h/24h 공통 event는 paired analysis한다.
8) resolution payout과 CLOB SELL을 분리한다. 현재 Queen이 수집하지 않는 redeemable/
   실제 redeem transaction은 미수집으로 표시하고 resolution에서 추정하지 않는다.
9) 30일과 terminal event-effective n 30을 모두 충족한 뒤 KEEP/STOP/SCALE을 결정한다.
10) scale은 $100→$200→$400→$1,000 단계별 새 cohort로 하고 A/B와 동시에 변경하지 않는다.
```

## 1. 기간과 DB

```bash
export REVIEW_START=2026-08-01
export REVIEW_END=2026-08-31
export REVIEW_DAYS=31
export QUEEN_24_DB=/absolute/path/golden-queen/data/queen-live-24h/trades.db
export QUEEN_12_DB=/absolute/path/golden-queen/data/queen-live-12h/trades.db
export RETRO_OUTPUT="$HOME/polybot-retro/queen-$REVIEW_END"
```

두 A/B 계정은 private key, funder, Jenkins job, `--job`과 SQLite를 공유하면 안 된다.
분석 시작 시 두 DB의 SHA-256과 `PRAGMA quick_check`를 기록한다.

```bash
uv run --project polybot-observability polybot-retro audit \
  --db "$QUEEN_24_DB" \
  --db "$QUEEN_12_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_END" \
  --output-dir "$RETRO_OUTPUT" \
  --strict
```

## 2. 사전 등록된 고정값

| 항목 | 24h | 12h |
|---|---:|---:|
| first crossing | prior <0.90 → current 0.90~0.94 | 동일 |
| target / stop | 0.98 / 0.85 | 동일 |
| snapshot gap | ≤15분 | 동일 |
| order | $100 | $100 |
| liquidity / volume | $100k / $5k | 동일 |
| spread / depth | ≤0.02 / 1.2x | 동일 |
| sports | 포함, in-play 360분 | 동일 |
| entry hours max | **24** | **12** |

DB의 `strategy_configs`가 이 표와 일치하는지 확인한다. 표와 다른 config hash가 있으면 별도
cohort로 분리하고 A/B에 섞지 않는다.

## 3. Evidence gate

반드시 보고할 coverage:

- run SUCCESS/FAILED/RUNNING, 최대 schedule gap, unknown Git commit
- cursor-complete market sweep 비율과 membership digest
- entry trade의 current/prior snapshot ID join, gap≤15분, 이전 0.90 이상 관측 여부
- market catalog와 event ID join
- sports entry의 `entry_time_reference`, game-start fallback, in-play phase
- BUY/SELL submission→status→confirmed fill coverage
- partial fill, uncertain intent, stale reconciliation, terminal zero-fill
- BUY/SELL confirmed size 일치와 fee amount/role completeness
- resolution evidence coverage
- redeemable/actual redeem은 현재 ingestion 없음으로 명시하고 coverage 0 또는 missing을
  “미상환”으로 해석하지 않음

하나라도 CRITICAL/HIGH이면 아래 성과 SQL은 진단용으로만 실행하고 의사결정을 만들지 않는다.

## 4. Actual execution 집계

다음 원칙을 SQL 구현에 유지한다.

```text
live only
BUY and SELL order_id both present
order_fills.status = CONFIRMED
BUY confirmed size = SELL confirmed size
both orders fully reconciled
both-side fee amount known
```

actual net P&L:

```text
(SELL confirmed VWAP - BUY confirmed VWAP) * confirmed size
- confirmed BUY fee - confirmed SELL fee
```

`trades.realized_pnl`, requested size, order response의 `makingAmount/takingAmount`, midpoint는
actual 값의 대체물이 아니다. `RESOLVED`는 settlement assumption으로 별도 집계하고, 현재
수집되지 않는 actual redeem transaction을 추정해 cash realization에 더하지 않는다.

## 5. Cohort 표

각 `config_hash × git_commit × mode × job_name`에 대해 다음을 만든다.

| 지표 | 정의 |
|---|---|
| raw signal n | 첫 crossing lineage가 증명된 condition |
| ordered n | BUY submission이 생긴 condition |
| full BUY n | exact full confirmed BUY |
| terminal n | full SELL 또는 final resolution evidence |
| event-effective n | event ID로 중복 제거한 terminal 표본 |
| actual net P&L | fee-complete confirmed roundtrip만 |
| settlement assumption | confirmed BUY + final payout, cash realization과 별도 |
| capital-hours | confirmed BUY 시각부터 confirmed SELL/resolution까지 |
| max drawdown | actual cash-flow sequence 기준 |

category, sports/non-sports, pregame/in-play/fallback, entry price 1센트 bucket별로도 같은 표를
만들되 최소 표본이 작으면 탐색 결과라고 표시한다.

## 6. 12h 대 24h paired analysis

독립 표본 비교와 paired 비교를 모두 보여준다.

1. 24h에만 진입한 event
2. 12h에만 진입한 event
3. 양쪽 모두 진입한 같은 event
4. 양쪽 어느 곳에도 주문되지 않은 archived crossing

3번은 event별 차이를 계산한다.

```text
paired_delta =
  12h actual net return - 24h actual net return
```

진입 시각, entry VWAP, phase, capital-hours, exit reason, fill latency도 함께 비교한다. 한
event의 여러 condition이나 두 bot의 같은 event를 독립 n으로 세지 않는다.

## 7. Counterfactual replay

운영 DB를 복사해 수정하지 않는다. immutable CSV export와 checksum을 만든 뒤 offline
script를 사용한다.

```bash
uv run --project golden-queen python golden-queen/scripts/backtest.py \
  /absolute/path/queen-research.csv \
  --output-dir "$RETRO_OUTPUT/offline" \
  --review-start "$REVIEW_START" \
  --review-end "$REVIEW_END"
```

grid는 `hours_max=12/24` 두 줄이며 그 외 값은 고정이다. observed book 결과는 hypothetical
fill이고 actual fill 열은 exact ledger join 전까지 `null`이다. CSV에 full depth와
`gameStartTime`가 없다면 해당 production gate는 재현되지 않았다고 명시한다.

## 8. Falsification과 결정

### STOP

- event-cluster 비용 후 EV≤0
- 12h/24h 모두 비용 후 음수
- target/stop의 partial/unknown/no-book이 손실 모델을 무효화
- leave-one-event-out에서 edge 소멸
- category 하나가 이익을 전부 설명하고 그 category 표본이 부족
- exact evidence가 한 달 뒤에도 strict gate를 통과하지 못함

### KEEP

- strict gate 통과
- 최소 30일, terminal event-effective n≥30
- paired/independent 결과가 같은 방향
- fee·slippage 후 EV>0이고 drawdown이 사전 자본 한도 안
- 한 event 제거 후에도 방향 유지

### SCALE

KEEP 조건에 더해 [Queen 증액 가이드](../../golden-queen/docs/SCALING_AND_TAIL_RISK.md)의
단계별 gate를 충족해야 한다. 첫 결정은 `$100 → $200`뿐이며 `$400`이나 `$1,000`을
동시에 승인하지 않는다.

결론은 다음 형식으로 남긴다.

```text
Decision: KEEP | STOP | SCALE_TO_200 | SCALE_TO_400 | SCALE_TO_1000
Selected horizon: 12h | 24h | undecided
Confidence: low | medium | high
Evidence window:
Effective n:
Actual net P&L:
Max drawdown:
Primary failure mode:
Rollback trigger:
Next review date:
```

## 9. Cherry baseline의 취급

Queen 설계 당시 Cherry 30일 audit는 CRITICAL 4/HIGH 6/MEDIUM 1로 실패했고
`market_snapshots`가 0행이었다. 따라서 Cherry의 legacy P&L과 Queen의 actual fill P&L을
직접 우열 비교하지 않는다. Cherry는 failure-mode와 instrumentation 요구사항을 만든
historical input일 뿐 Queen의 수익 baseline이 아니다.

## 10. 보존

Queen DB와 로그는 repository에 commit하지 않는다. 실행 중 DB를 `cp`하지 말고 online
backup과 SHA-256 manifest를 workspace 밖 durable storage에 보관한다.

```bash
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"
```
