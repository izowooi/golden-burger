# Golden Blueberry — Closing Surge

해결 또는 경기 종료가 가까운 표준 이진 시장에서 YES 확률이 **처음 급등할 때** 진입하고,
`0.97` 익절 또는 `0.78` 절대 손절로 관리하는 소액 실거래 검증 봇이다. 스포츠는 기본
포함하며 경기 중에도 거래할 수 있다.

이 구현은 과거 `golden-cherry`의 아이디어가 수익성이 있다고 선언하지 않는다. 당시 DB는
legacy 체결 공백이 커서 운영자가 기억하는 월 10%를 확정할 수 없었다. Blueberry의 목적은
같은 가설을 **$5 주문, 명확한 A/B, exact fill, 거절 후보 기록**으로 다시 검증하는 것이다.

## 현재 고정 규칙

| 항목 | 값 |
|---|---|
| 시장 | outcomes가 정확히 `[Yes, No]`, token 2개, `negRisk=false` |
| 진입 교차 | 직전 저장 YES `<0.85` → 현재 YES `[0.85,0.93]` |
| 연속 관측 | `0 < gap <= 15분`; 과거 0.85 이상 관측 없음 |
| A/B 축 | A: 상승폭 `>=2%p`, B: 상승폭 `>=5%p` |
| 시간 | 일반/경기 전 `(0h,72h]`; 경기 중 kickoff 후 최대 360분 |
| 시장 gate | 유동성·24h 거래량 각각 `>= $10,000` |
| 주문 직전 | ask `<=0.93`, spread `<=2%p`, ask depth `>=1.2 × 주문수량` |
| 익절 | YES signal `>=0.97`이고 fresh bid도 `>=0.97` |
| 손절 | YES signal `<=0.78` |
| 없는 규칙 | trailing stop, time exit |
| 주문/자금 | 건당 `$5`, arm당 `$150`, 총 `$300` |
| 노출 | arm당 최대 10건 / `$50`, event당 1건, cycle당 신규 1건 |
| kill switch | arm의 경제손익이 `-$30` 이하이면 신규 진입 자동 차단 |

`hours_min=0`은 “0시간 남았을 때도 산다”가 아니라 **추가 최소시간이 없다**는 뜻이다.
scheduled/pregame은 반드시 미래 시각(`>0h`)이어야 한다. 스포츠 in-play는 별도 phase로
판정하므로 경기 시작 뒤에도 upstream이 거래 가능하고 360분 이내면 후보가 된다.

## 왜 $1이 아니라 $5인가

Polymarket CLOB 최소 주문은 5 shares다. 이 전략은 0.93까지 진입하므로:

```text
$1 / 0.93 = 1.08 shares  → 주문 불가
$5 / 0.93 = 5.38 shares  → 5.0 + 0.1 buffer 충족
```

따라서 `$5`는 공격적인 선택이 아니라 현재 밴드에서 가능한 최소 단위다. 초기 코드에는
`$5` hard cap이 있어 환경변수만으로 증액할 수 없다. 증액은 30일 회고를 통과한 뒤 별도
코드 변경과 새 cohort로 진행한다. 자세한 기준은 [SCALING_AND_TAIL_RISK.md](SCALING_AND_TAIL_RISK.md)를
본다.

## 설치와 검증

아래 검증은 배포 직후 **한 번만** 실행한다. 5분 주기 Jenkins shell에서 매번 `pytest`와
`--extra dev`를 실행할 필요는 없다.

```bash
cd golden-blueberry
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --simulate --job blueberry-sim-a-2pp
uv run polybot run --simulate --job blueberry-sim-a-2pp
uv run polybot status --simulate --job blueberry-sim-a-2pp
```

`config`와 `status`에도 실행 모드를 지정한다. `--live`면 `trades.db`, `--simulate`면
`trades_sim.db`를 본다. 모드 없이 `run`하면 안전하게 simulation이다.

### 새 DB와 로그 저장공간

새 코드 checkout 뒤 새 cohort를 시작할 때 Jenkins clean build는 **한 번만** 실행한다.
Blueberry는 존재하지 않는 DB를 처음부터 `compact-v1`로 만들므로 migration이나
`POLYBOT_DB_*` 환경변수가 필요 없다. 첫 실행 로그의 `새 SQLite DB를 compact-v1로
생성했습니다 - strategy=golden-blueberry`를 확인한 뒤 매-build clean 옵션은 끈다.

첫 1시간 snapshot은 원형으로, 이후 60일까지는 first-crossing에 필요한 extrema로 보존하고
전체 시장 sweep 상세는 24시간마다 한 번만 저장한다. entry/shadow decision이 참조하는
snapshot은 보존기간과 무관하게 보호한다. `shadow_observations`와 일일 bot log는 60일을
넘으면 정리된다. 저장소 `Jenkinsfile`은 console/build도 60일 보존하며, Freestyle job이면
Jenkins `Discard old builds`에 같은 값을 직접 설정한다.

### 같은 호스트 A/B의 Gamma sweep 공유

두 A/B job이 같은 Mac에서 같은 시각에 시작하면 아래 owner-private 절대경로를 **둘 다
동일하게** 설정한다.

```bash
export POLYBOT_GAMMA_SHARED_CACHE_DIR=/Users/jongwoopark/.cache/golden-blueberry/gamma-sweeps-v1
```

Jenkins 표준 환경(`JENKINS_URL`)에서는 이 변수가 없어도 실행 사용자의
`~/.cache/golden-blueberry/gamma-sweeps-v1`을 자동 선택한다. 위 export는 경로를 명시적으로
고정하는 권장값이며, 예외적으로 독립 sweep이 필요하면 값을 `off`로 설정한다.

같은 5분 bucket과 동일한 Gamma filter 조합에서는 먼저 lock을 얻은 한 job만
`/markets/keyset`의 terminal cursor까지 전수 조회한다. 다른 job은 완료를 기다린 뒤
membership SHA-256, cursor-complete, market 집합을 다시 검증한 동일 payload를 사용한다.
이는 유동성·거래량·시간·가격 gate나 시장 universe를 줄이는 기능이 아니며 A/B가 같은
관측면을 쓰게 하는 운영 최적화다. 최근 3개 bucket만 남는 재생성 가능한 public market
data cache이며 DB evidence나 backup을 대신하지 않는다. lock은 12분에 fail closed하며,
경로를 설정하지 않으면 기존처럼 각 process가 독립 sweep을 수행한다.

## A/B simulation Jenkins shell

질문에 제시한 두 shell은 방향은 맞지만 다음 세 가지를 고쳐야 한다.

1. simulation은 계좌를 쓰지 않으므로 private key, funder address, signature type을 넣지 않는다.
2. B의 runtime job은 `blueberry-sim-**b**-5pp`여야 한다. job 이름 자체가 처치를 바꾸지는
   않지만 DB와 결과 파일을 식별하는 계약이다.
3. 정기 실행에서는 `uv sync --frozen`과 `config/run/status`만 실행한다. 테스트는 위의
   1회 preflight에서 수행한다.

A job:

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE

export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_EXPERIMENT_CAPITAL_USDC=150
export POLYBOT_MIN_SURGE=0.02
RUNTIME_JOB=blueberry-sim-a-2pp

cd ./golden-blueberry
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --simulate \
  --job "${RUNTIME_JOB}"
/Users/jongwoopark/.local/bin/uv run polybot run --simulate \
  --job "${RUNTIME_JOB}"
/Users/jongwoopark/.local/bin/uv run polybot status --simulate \
  --job "${RUNTIME_JOB}"
```

B job은 아래 두 줄만 다르고 나머지는 A와 같아야 한다.

```bash
export POLYBOT_MIN_SURGE=0.05
RUNTIME_JOB=blueberry-sim-b-5pp
```

명령의 `--job` 세 곳에 모두 같은 `${RUNTIME_JOB}`을 쓰므로 오타를 줄일 수 있다. 과거
Nectarine을 실행하던 Jenkins workspace라도 위처럼 새로운 runtime job을 쓰면 Blueberry는
`data/blueberry-sim-*/trades_sim.db`에 기록하므로 기존 DB와 섞이지 않는다.

## 계좌 없는 Research Shadow

`archive_only`는 시장 snapshot만 저장한다. 반면 새 `--shadow`는 실제 계좌와 주문 없이
최초 0.85 교차를 다음 고정 2×2 grid로 동시에 추적한다.

| 축 | 값 |
|---|---|
| 최소 연속 급등 | `2%p`, `5%p` |
| 진입 horizon | `72h`, `168h` |
| 공통 진입 | 동일한 0.85~0.93 band, 유동성·거래량, fresh ask/spread/depth |
| 가상 청산 | first observed `bid>=0.97`, `YES<=0.78` 시 fresh bid, 또는 proven resolution |

각 최초 교차는 `shadow_signals` 네 행으로 확장된다. 처치상 진입했을 행은 `OPEN`, 처치
때문에 제외됐지만 실행 가능했던 행은 `COUNTERFACTUAL_OPEN`으로 추적한다. 종결 후에는
`MISSED_PROFIT`과 `AVOIDED_LOSS`도 구분한다. `shadow_observations`에는 매 cycle의 가격,
fresh bid, 거래량·유동성, 당시 `endDate`/entry deadline이 남으므로 end date 변경도 나중에
복원할 수 있다.

Shadow 수익은 **가상 gross P&L이며 fee를 포함하지 않는다.** 실제 주문, `trades` 행,
`order_submissions`는 만들지 않으며 독립 DB
`data/blueberry-shadow-research/shadow.db`를 쓴다. 공개 Gamma/CLOB 조회만 사용하므로
private key와 funder address가 없어야 정상이다.

Jenkins의 Build periodically는 Shadow 한 job만 돌리므로 `H/5 * * * *`를 사용할 수 있다.

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE

export LOG_LEVEL=INFO

cd ./golden-blueberry
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --shadow \
  --job blueberry-shadow-research
/Users/jongwoopark/.local/bin/uv run polybot run --shadow \
  --job blueberry-shadow-research
/Users/jongwoopark/.local/bin/uv run polybot status --shadow \
  --job blueberry-shadow-research
```

`--shadow`가 `simulation=true`, `lifecycle=shadow_only`, `shadow.db`를 함께 강제하므로
`POLYBOT_LIFECYCLE_MODE`나 `POLYBOT_MIN_SURGE`를 이 job에 설정하지 않는다. `--shadow`와
`--live`는 동시에 사용할 수 없다.

## 권장 A/B 실거래

두 arm은 **서로 다른 Polymarket 계좌, Jenkins job, `--job`, DB**를 쓴다. 같은 계좌나
DB를 공유하면 독립 실거래 비교가 아니다. 두 job은 같은 날 시작하고 둘 다 `*/5 * * * *`,
concurrent build 금지를 사용한다. `H/5`는 job 이름별 hash offset 때문에 서로 다른 분에
실행될 수 있어 이 A/B에는 사용하지 않는다.

| Arm | runtime job 예시 | 유일한 차이 | 배정 자금 |
|---|---|---:|---:|
| A | `blueberry-live-a-2pp` | `POLYBOT_MIN_SURGE=0.02` | $150 |
| B | `blueberry-live-b-5pp` | `POLYBOT_MIN_SURGE=0.05` | $150 |

저장소의 `Jenkinsfile`을 쓰는 것이 가장 단순하다. Freestyle shell을 쓴다면 각 job에서
Credentials Binding으로 `POLYMARKET_PRIVATE_KEY`와 `POLYMARKET_FUNDER_ADDRESS`를 주입하고
아래 공통 블록을 사용한다. 비밀값을 shell에 직접 적지 않는다.

```bash
#!/bin/bash
set +x
set -euo pipefail

export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_EXPERIMENT_CAPITAL_USDC=150
export POLYBOT_MIN_SURGE=0.02       # B job만 0.05
export POLYBOT_GAMMA_SHARED_CACHE_DIR=/Users/jongwoopark/.cache/golden-blueberry/gamma-sweeps-v1
export POLYMARKET_SIGNATURE_TYPE=3 # legacy 이메일 계정이면 1

: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"

cd ./golden-blueberry
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --live \
  --job blueberry-live-a-2pp
/Users/jongwoopark/.local/bin/uv run polybot run --live \
  --job blueberry-live-a-2pp
```

B job은 `POLYBOT_MIN_SURGE=0.05`와 `--job blueberry-live-b-5pp`만 바꾼다. 나머지 값을
다르게 두면 무엇이 결과를 만들었는지 알 수 없으므로 A/B가 무효다.

## 주요 환경변수

우선순위는 `environment > config.yaml > code default`다.

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `POLYBOT_MIN_SURGE` | `0.02` | A/B 처치축. 허용값은 `0.02`, `0.05`뿐 |
| `POLYBOT_BUY_AMOUNT` | `5` | 주문 notional; 초기 hard cap도 $5 |
| `POLYBOT_EXPERIMENT_CAPITAL_USDC` | `150` | arm 자금 및 kill-switch 기준 |
| `POLYBOT_MAX_DRAWDOWN_STOP` | `0.20` | 경제손익 -20%에서 신규 BUY 차단 |
| `POLYBOT_ENTRY_PROB_MIN/MAX` | `0.85/0.93` | 최초 교차와 현재 진입 band |
| `POLYBOT_STOP_PRICE` | `0.78` | 미해결 절대 손절 signal |
| `POLYBOT_TAKE_PROFIT_PRICE` | `0.97` | signal과 bid가 모두 넘어야 하는 익절 |
| `POLYBOT_ENTRY_HOURS_MIN/MAX` | `0/72` | scheduled/pregame 진입 시계 |
| `POLYBOT_MIN_LIQUIDITY` | `10000` | metadata 유동성 floor |
| `POLYBOT_MIN_VOLUME_24H` | `10000` | 최근 24h 거래량 floor |
| `POLYBOT_MAX_POSITIONS` | `10` | arm당 open position 개수 상한 |
| `POLYBOT_MAX_EVENT_POSITIONS` | `1` | 한 event 동시 노출 상한 |
| `POLYBOT_MAX_SNAPSHOT_GAP_MINUTES` | `15` | 연속 관측 허용 간격 |
| `POLYBOT_ALLOW_IN_PLAY` | `true` | 스포츠 경기 중 진입 허용 |
| `POLYBOT_MAX_IN_PLAY_MINUTES` | `360` | kickoff 뒤 최대 후보 시간 |
| `POLYBOT_GAMMA_SHARED_CACHE_DIR` | 미설정 | 같은 호스트 A/B의 검증된 5분 sweep 공유 절대경로 |
| `POLYBOT_LIFECYCLE_MODE` | `active` | `active`, `close_only`, `archive_only`, `shadow_only` |
| `POLYBOT_EXCLUDED_CATEGORIES` | 빈 목록 | exact tag 제외; 기본은 스포츠 포함 |
| `POLYMARKET_SIGNATURE_TYPE` | 계정별 | `3`=POLY_1271, `1`=legacy POLY_PROXY |

고정 계약 때문에 `POLYBOT_EXECUTION_MODE`는 `nearest`만, `POLYBOT_YES_ONLY`는 `true`만
허용한다. 전체 목록과 안전 불변조건은 [STRATEGY.md](STRATEGY.md)에 있다.

`close_only`는 archive/reconciliation/청산을 유지하고 신규 BUY만 막는다. `archive_only`는
주문 lifecycle mutation 없이 archive만 수집한다. 계좌나 전략을 종료할 때는 임의로 DB 상태를
바꾸지 말고 [strategy-wind-down-playbook.md](../docs/strategy-wind-down-playbook.md)를 따른다.
`shadow_only`는 직접 env로 쓰기보다 `--shadow`로 실행해야 simulation 전용 DB 계약까지 함께
적용된다.

## 어떤 데이터가 남는가

- `market_snapshots`, `market_catalog`, `market_sweeps`: point-in-time 시장과 sweep 완전성
- `entry_signal_decisions`: **주문하지 않은 최초 교차도** arm, 상승폭, 시간, 유동성,
  거래량과 signal/metadata 거절 이유를 함께 저장
- `shadow_signals`, `shadow_observations`: 2×2 treatment별 가상 진입/종결과 광범위한
  가격·deadline 경로. 실제 체결 성과와 합산하지 않음
- fresh-book 거절 상세: sanitization된 실행 로그에 기록하고, DB에서는
  candidate→submitted attrition으로 함께 대사
- `strategy_configs`, `run_audits`: resolved config와 run 결과
- `order_submissions`, `order_status_events`, `order_fills`: 주문 intent부터 exact fill까지
- `trades`: 의사결정과 position lifecycle; `realized_pnl`만으로 성과를 판단하지 않음

GTC `accepted`/`live`와 order ID는 체결 증거가 아니다. 실제 성과는 exact order의
`order_fills.status='CONFIRMED'` size/VWAP/fee로만 계산한다. resolution payout 추정은
`settlement_pnl_assumption`에 따로 남고 synthetic SELL로 만들지 않는다.

## 1주·30일 회고

1주차는 승자 선택이 아니라 다음만 확인한다: 5분 실행 공백, cursor-complete sweep,
두 arm의 source digest/config 고정, first-crossing decision 적재, 주문/체결/fee 대사,
kill switch, backup. 수익이 좋거나 나빠도 threshold를 바꾸지 않는다.

30일차에는 online backup한 두 DB로 읽기 전용 분석을 실행한다.

```bash
uv run python scripts/analyze_experiment.py \
  --arm-a /absolute/backup/a/trades.db \
  --arm-b /absolute/backup/b/trades.db \
  --review-start 2026-08-10 \
  --review-end 2026-09-08 \
  --output-dir "$HOME/polybot-retro/blueberry-2026-09-08"
```

분석기는 DB를 수정하지 않고 checksum, cohort, 최초 교차/거절, confirmed round trip과 fee
coverage를 보고한다. arm당 confirmed closed 20건 미만이면 `INCONCLUSIVE`다. 표본이 충분해도
자동으로 승자를 고르지 않는다. 공식 절차는 [docs/retro/golden-blueberry.md](../docs/retro/golden-blueberry.md)를
따른다.

## Backtest

`scripts/backtest.py`는 immutable CSV만 읽는 research 도구다. production DB나 API를 열지
않으며 ask/bid 결과는 hypothetical이라고 표시한다.

```bash
uv run python scripts/backtest.py /absolute/research.csv \
  --review-start 2026-01-01 --review-end 2026-07-31 \
  --output-dir "$HOME/polybot-research/blueberry"
```

입력 provenance와 제약은 [research/2026-08-04-origin-and-preregistration.md](research/2026-08-04-origin-and-preregistration.md)에
기록돼 있다.
