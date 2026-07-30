# Golden Quince — Spread Harvest

Golden Quince는 `golden-queen`의 Crown Momentum 진입 신호를 고정하고,
**BUY 실행 측면만** `passive` / `nearest` / `cross`로 바꾸는 3팔 실험이다.
신호가 아니라 maker/taker 실행비용 차이가 처치다.

```text
진입 = strict standard binary YES
     + 직전 저장 YES < 0.90 <= 현재 저장 YES <= 0.94
     + 두 snapshot 간격 0분 초과 15분 이하
     + 과거 60일 안에 YES >= 0.90 관측이 없었음
     + 일반 시장: 0시간 초과, 종료까지 24시간 이하
     + 스포츠: gameStartTime 기준 경기 전 또는 시작 후 360분 이내
     + fresh spread <= 0.02
     + best ask부터 0.01 위까지의 실제 ask depth >= 주문 주식 수의 1.2배

청산 = 미해결 상태에서 YES >= 0.98이고 fresh bid >= 0.98
    또는 YES <= 0.85

실행 = BUY만 execution_mode 적용
     + A passive / B nearest / C cross
     + SELL은 모든 팔에서 nearest

그 외 = resolution, redeemable/실제 redeem, CLOB fill을 서로 다른 증거로 취급
```

스포츠는 기본 포함이다. `gameStartTime`이 있으면 경기 시작 시계를 쓰고, 없으면
`endDate`로 복귀한다. 환경변수로 스포츠를 명시적으로 제외하지 않는 한 Sports/NBA/축구
등의 category는 제외하지 않는다. 경기가 시작됐다는 이유만으로 BUY를 막지도 않는다.
Golden Quince는 resolution 결과는 기록하지만 redeemable 상태와 실제 redeem transaction을
아직 수집하지 않는다. 둘을 resolution이나 synthetic SELL로 추정하지 않는다.

## 중요한 결론

이 전략은 **보수적인 신규 가설**이지 과거 수익이 입증된 최적해가 아니다. 실측 진입
비용은 MAKER `-16.8 bps`, TAKER `+56.7 bps`로 약 73 bps 갈렸지만, 패시브 체결의
역선택이 그 할인을 상쇄할 가능성은 아직 기각되지 않았다.

따라서 accepted BUY를 `PENDING_BUY`로 두고 exact full `CONFIRMED` fill 이후에만
`HOLDING`으로 바꾼다. 실제 판정은 세 팔의 execution ledger를 30일 쌓은 뒤
MAKER 비중·진입 실효가·체결률로 내린다. 승률과 순손익은 1차 종점이 아니다.

## 왜 더 단순한가

- 진입 신호는 `0.90`의 첫 상향 교차 하나뿐이다.
- YES만 사고 strict Yes/No·non-negRisk 시장만 받는다.
- 일반 시장 시간창은 `(0h, 24h]` 하나다.
- 사전 해결 청산은 `0.98` 목표와 `0.85` 절대 stop 두 개뿐이다.
- trailing stop과 time exit은 없다.
- routine 조절값은 주문 금액 하나이며, 유동성·거래량·총 노출은 자동 확장된다.
- 한 cycle에서 최대 1개, event당 최대 1개만 새로 산다.

`0.90~0.94` 밴드가 우월하다는 주장은 하지 않는다. 이 실험에서 신호 horizon은 모든
팔에 **`(0h, 24h]` 하나로 고정**한다. horizon 변경은 이 실험의 비교축이 아니며,
실행 처치와 섞지 않는다.

## Quickstart

```bash
cd golden-quince
uv sync --frozen --extra dev

# CLOB 인증값과 계정별 signature type은 untracked .env 또는
# Jenkins Credentials Binding으로 미리 제공한다. shell history에 값을 붙여넣지 않는다.

# 기본 주문 금액은 $5. A/B/C에서는 override하지 않는다.

uv run polybot config
uv run pytest
uv run polybot run --simulate --job quince-sim
```

저장소 기본값은 simulation이다. 실제 주문은 `--live`를 명시해야만 활성화된다.

```bash
POLYBOT_EXECUTION_MODE=passive \
  uv run polybot run --live --job polybot-quince-passive
```

`config`는 주문을 실행하지 않고 최종적으로 해석된 값, DB 경로, simulation 여부를
보여준다. Jenkins 적용 전 typo와 잘못된 계정 signature type을 확인하는 용도다.

## 3팔 Jenkins 실험 — 먼저 여기서 배포

A/B/C는 **서로 다른 wallet/account, Jenkins job, `--job`, SQLite DB**를 사용한다.
세 job 모두 같은 Git commit과 `H/5 * * * *` cadence를 쓰고 concurrent build를
비활성화한다. 신호 horizon은 24시간, 주문액은 $5, SELL은 `nearest`로 고정한다.

| 팔 | wallet / credentials | Jenkins job = `--job` | DB | BUY mode | cadence |
|---|---|---|---|---|---|
| A | A 전용 | `polybot-quince-passive` | `data/polybot-quince-passive/trades.db` | `passive` | `H/5 * * * *` |
| B | B 전용 | `polybot-quince-nearest` | `data/polybot-quince-nearest/trades.db` | `nearest` | `H/5 * * * *` |
| C | C 전용 | `polybot-quince-cross` | `data/polybot-quince-cross/trades.db` | `cross` | `H/5 * * * *` |

같은 wallet, credential, Jenkins job 또는 DB를 재사용하면 live A/B/C 격리가 아니다.
각 job에서 private key와 funder는 Jenkins **Credentials Binding**으로 주입한다.
Freestyle shell이 `-x`로 시작할 수 있으므로 secret을 참조하기 전부터 `set +x`를 둔다.
아래 세 shell에서 `POLYMARKET_SIGNATURE_TYPE`은 각 wallet에 맞는 `1` 또는 `3`을
Jenkins 환경에 별도로 설정한다.

### A — passive

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet A}"
export POLYBOT_EXECUTION_MODE=passive
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-passive
uv run polybot run --live --job polybot-quince-passive
```

### B — nearest

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet B}"
export POLYBOT_EXECUTION_MODE=nearest
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-nearest
uv run polybot run --live --job polybot-quince-nearest
```

### C — cross

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Jenkins Credentials Binding required}"
: "${POLYMARKET_FUNDER_ADDRESS:?Jenkins Credentials Binding required}"
: "${POLYMARKET_SIGNATURE_TYPE:?set 1 or 3 for wallet C}"
export POLYBOT_EXECUTION_MODE=cross
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_ENTRY_HOURS_MAX=24
export LOG_LEVEL=INFO
cd ./golden-quince
uv sync --frozen
uv run polybot config --job polybot-quince-cross
uv run polybot run --live --job polybot-quince-cross
```

선택적 D는 별도 wallet/job/DB에서 `passive`와 `$10`만 사용해 크기 효과를 본다.
`polybot-quince-passive-10`처럼 A와 분리하며, **A/B/C가 먼저**다.

같은 `event_id × crossing time window`가 여러 팔에 나타나면 독립 관측으로 세지 않고
paired/clustered analysis를 한다. 첫 30일 동안 mode 외 threshold·horizon·금액·유동성
기준을 동시에 바꾸지 않는다.

## 자체 archive와 “최초 관측”의 범위

Golden Quince는 진입 gate보다 넓은 `YES >= 0.80`, scheduled/pregame `<= 72h` 시장을 60일
보존한다. Gamma request 유동성 하한은
`min(POLYBOT_MIN_LIQUIDITY, $1,000)`이므로 기본값은 $1,000이다. `event_id`가 빠진
observation도 archive에는 남겨 교차 이력을 소실하지 않지만, 신규 진입에서는 거부한다.
따라서 “최초 0.90 상향 교차”는 전 세계 과거 전체가 아니라 이 Quince archive envelope와
보존기간 안에서 처음 관측했다는 뜻이다.

## 스포츠 시간 계약

| 상태 | 기본 동작 |
|---|---|
| 스포츠 + `gameStartTime` 존재, 경기 전 | 시작까지 남은 시간이 24시간 이내면 진입 가능 |
| 스포츠 + 경기 시작 후 360분 이내 | 시장이 active/tradable이면 진입 가능 |
| 스포츠 + `gameStartTime` 없음 | `endDate`로 fallback하여 포함 |
| 비스포츠 | `endDate`까지 `(0h, 24h]` |
| 명시적 category 제외 | `POLYBOT_EXCLUDED_CATEGORIES`에 일치하는 tag를 넣을 때만 제외 |

`POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START=true`는 clock 증거가 없는 스포츠를 특별히
차단하고 싶은 운영자용 엄격 모드다. 기본값은 `false`다.

category 제외는 Gamma tag slug/label의 대소문자를 무시한 **exact match**다.
`sports` 하나가 `nba`, `soccer`, `football` 같은 별도 tag까지 자동 포함하지 않으므로,
제외가 필요할 때만 실제 제외할 tag를 comma로 각각 적는다. 환경변수를 생략하면 스포츠를
포함한 모든 category가 대상이다.

## 주문·포지션 계약

- Gamma liquidity/volume은 1차 metadata gate다. 실제 주문 가능성은 같은 CLOB snapshot의
  bid/ask/spread와 ask depth로 다시 검사한다.
- BUY limit은 `min(0.94, best ask + 0.01)`이며, 그 가격 이하 ask depth가 주문 수량의
  1.2배 이상이어야 한다.
- GTC `live`/`accepted`/order ID는 fill이 아니다.
- live BUY는 `PENDING_BUY`로 시작한다. exact order ID의 full reconciled fill이 확인된
  뒤에만 실제 보유로 승격한다.
- live SELL도 `PENDING_SELL`이며 BUY·SELL full confirmed size와 fee가 모두 대사된 뒤에만
  `COMPLETED`와 actual net P&L을 만든다.
- 위 actual fill/P&L 계약은 live cohort에만 적용한다. simulation 결과는 별도
  hypothetical cohort이며 실제 수익으로 합치지 않는다.
- token 잔고가 요청량보다 작다는 오류가 나도 더 작은 SELL을 자동 재시도하지 않는다.
  잔여 포지션을 원장 밖에 남기는 부분 청산을 피하기 위해서다.
- resolution payout 관측, redeemable 상태, 실제 redeem transaction은 각각 별도 사실이다.
  Golden Quince는 현재 첫 번째만 적재하며 redeemable/실제 redeem coverage는 아직
  제공하지 않는다.

오류 범위도 분리한다. 원자적 market sweep/archive나 RunAudit 자체가 실패하면 cycle 전체를
중단한다. 개별 시장의 event/lineage/book/depth 누락은 해당 시장만 제외한다. 개별 과거
주문의 대사 오류는 해당 `token_id × side` 신규 주문만 격리하고 다른 시장의 cycle은
계속한다.

## 실험 금액 계약

A/B/C의 주문액은 모두 **$5**다. 이 값에서는 metadata gate가 liquidity $10,000,
24h volume $2,000 바닥에 묶이고 open-notional 상한은 $50이다.

| 팔 | 주문액 | 목적 |
|---|---:|---|
| A/B/C | **$5** | BUY execution mode의 1차 실험 |
| 선택적 D | **$10** | A와 같은 `passive`에서 크기 효과만 분리 |

D는 별도 wallet/job/DB cohort이며 A/B/C 실행 종점의 30일 판정을 방해하지 않아야 한다.
그 밖의 증액 경로는 이 사전 등록 실험의 계약이 아니다.

`POLYBOT_MAX_POSITIONS=20`은 누적 거래 수나 지갑 전체 포지션 수가 아니라 해당
`--job` DB의 `PENDING_BUY`, `HOLDING`, `PENDING_SELL` 동시 open state 합계다. 기본
open-notional 상한이 주문액 10배라 full-size 신규 포지션은 보통 10개에서 먼저 막힌다.

## Lifecycle

| 모드 | archive | 기존 포지션 관리 | 신규 BUY |
|---|---:|---:|---:|
| `active` | O | O | O |
| `close_only` | O | O | X |
| `archive_only` | O | X* | X |

\* `archive_only`도 cycle 시작 시 execution ledger의 읽기·대사는 수행한다. 그 뒤에는 신규
주문이나 trade-position lifecycle mutation을 하지 않고 archive만 갱신한다.

`close_only`는 강제 매도가 아니다. 설정을 바꾸더라도 기존 포지션은 매수 당시 저장된
`0.98/0.85` 값과 resolution 증거로만 관리된다. 퇴역은
[전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)을 따른다.

## 실행 모드 (이 전략의 처치축)

`POLYBOT_EXECUTION_MODE`는 틱 반올림 방향을 정한다. 이것이 golden-quince의 전부다.

| 값 | BUY | SELL | 역할 |
|---|---|---|---|
| `passive` (기본) | 내림 | **nearest** | 매수호가 합류 — 처치군 |
| `nearest` | 반올림 | **nearest** | 기존 14개 봇과 동일 — 대조군 |
| `cross` | 올림 | **nearest** | BUY 크로스 — 비용 상한 |

`execution_mode`는 **BUY에만 적용**된다. SELL은 손절 체결성을 보존하기 위해 모든 팔에서
`nearest`다. 실측 진입 비용은 MAKER `-16.8 bps`, TAKER `+56.7 bps`로 약 73 bps
갈린다. 근거와 A/B/C 판정 기준은 `STRATEGY.md` §2·§6.

```bash
export POLYBOT_EXECUTION_MODE=passive   # A안
export POLYBOT_EXECUTION_MODE=nearest   # B안 (대조)
export POLYBOT_EXECUTION_MODE=cross     # C안 (비용 상한)
```

## 낙폭 kill switch

확정손익이 `POLYBOT_EXPERIMENT_CAPITAL_USDC x POLYBOT_MAX_DRAWDOWN_STOP`
(기본 $200 x 20% = $40) 이하가 되면 **코드가 신규 진입을 차단**한다. 청산은 계속된다.
golden-date가 판정 기준을 문서에만 두고 -52%까지 간 실패를 반복하지 않기 위한 것이다.

## 환경변수

우선순위는 `env > config.yaml > 코드 기본값`이다. 정상 운용에는 계정 3개 값과 선택적
`LOG_LEVEL`만 필요하다. A/B/C는 기본 주문 금액 `$5`와 24시간 signal horizon을
고정하고 `POLYBOT_EXECUTION_MODE`만 팔별로 다르게 설정한다.

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `POLYMARKET_PRIVATE_KEY` | 필수 | CLOB 서명 key. 로그·문서·commit 금지 |
| `POLYMARKET_FUNDER_ADDRESS` | 필수 | 해당 Quince arm의 funder. 로그·commit 금지 |
| `POLYMARKET_SIGNATURE_TYPE` | `1` | 계정에 맞는 `1` 또는 `3` |
| `POLYBOT_BUY_AMOUNT` | `5` | A/B/C 건당 주문 금액. 선택적 D만 `10` |
| `POLYBOT_EXECUTION_MODE` | `passive` | A=`passive`, B=`nearest`, C=`cross`; BUY 전용 처치 |
| `LOG_LEVEL` | `INFO` | Python 로그 수준 |
| `POLYBOT_LIFECYCLE_MODE` | `active` | `active` / `close_only` / `archive_only` |
| `POLYBOT_ENTRY_HOURS_MAX` | `24` | 전 팔 고정 signal horizon; experiment axis가 아님 |
| `POLYBOT_ENTRY_HOURS_MIN` | `0` | scheduled 진입 하한. 실제 조건은 `> 0` |
| `POLYBOT_ENTRY_PROB_MIN` | `0.90` | 첫 상향 교차와 진입 밴드 하한 |
| `POLYBOT_ENTRY_PROB_MAX` | `0.94` | signal·fresh ask 상한 |
| `POLYBOT_TAKE_PROFIT_PRICE` | `0.98` | signal과 fresh bid가 모두 도달해야 매도 |
| `POLYBOT_STOP_PRICE` | `0.85` | 절대 YES stop signal |
| `POLYBOT_MIN_LIQUIDITY` | `10000` | metadata liquidity 바닥; 금액에 따라 자동 상향 |
| `POLYBOT_MAX_ORDER_LIQUIDITY_RATIO` | `0.001` | 주문/liquidity 최대 0.1% |
| `POLYBOT_MIN_VOLUME_24H` | `2000` | 24h volume 바닥; 금액에 따라 자동 상향 |
| `POLYBOT_MAX_ORDER_VOLUME_RATIO` | `0.02` | 주문/24h volume 최대 2% |
| `POLYBOT_MAX_SPREAD` | `0.02` | fresh CLOB spread 상한 |
| `POLYBOT_DEPTH_PRICE_WINDOW` | `0.01` | best ask 위 depth 측정 폭 |
| `POLYBOT_DEPTH_SAFETY_MULTIPLE` | `1.20` | 필요한 실제 ask depth 배수 |
| `POLYBOT_MAX_POSITIONS` | `20` | 해당 job DB의 동시 open state 상한 |
| `POLYBOT_MAX_EVENT_POSITIONS` | `1` | 같은 event 동시 노출 상한 |
| `POLYBOT_MAX_OPEN_NOTIONAL_MULTIPLE` | `10` | open notional=`BUY_AMOUNT × 10` |
| `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE` | `1` | 한 실행에서 만드는 최대 신규 position |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | `168` | 같은 condition 재진입 최소 간격 |
| `POLYBOT_MAX_SNAPSHOT_GAP_MINUTES` | `15` | first-crossing 연속 관측 최대 간격 |
| `POLYBOT_ALLOW_IN_PLAY` | `true` | 경기 중 진입 허용 |
| `POLYBOT_MAX_IN_PLAY_MINUTES` | `360` | 경기 시작 후 진입 허용 시간 |
| `POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START` | `false` | true일 때만 clock 누락 스포츠 차단 |
| `POLYBOT_EXCLUDED_CATEGORIES` | 빈 값 | 명시한 comma 구분 Gamma tag를 exact match로 제외 |
| `POLYBOT_ARCHIVE_PROB_MIN` | `0.80` | 자체 반사실 archive YES 하한 |
| `POLYBOT_ARCHIVE_HOURS_MAX` | `72` | scheduled archive 상한 |
| `POLYBOT_SNAPSHOT_RETENTION_DAYS` | `60` | snapshot 최소 보존일 |

## 회고와 offline replay

```bash
uv run --project ../polybot-observability polybot-retro audit \
  --db data/polybot-quince-passive/trades.db \
  --db data/polybot-quince-nearest/trades.db \
  --db data/polybot-quince-cross/trades.db \
  --days 30 \
  --output-dir "$HOME/polybot-retro/quince-execution-30d" \
  --strict
```

`scripts/backtest.py`는 운영 DB나 네트워크를 열지 않고 immutable research CSV만 읽는다.
Quince DB에서 충분한 snapshot/catalog가 쌓인 뒤 보조 진단에만 사용한다.

```bash
uv run python scripts/backtest.py /absolute/path/quince-research.csv \
  --output-dir "$HOME/polybot-retro/quince-2026-08" \
  --review-start 2026-08-01 \
  --review-end 2026-08-31
```

offline replay는 A/B/C 처치를 대신하지 않는다. 세 팔 모두 signal horizon은 24시간으로
고정하며, actual execution endpoint는 exact `CONFIRMED` BUY fill과 결정 midpoint를
join해 계산한다. full depth와 sports `gameStartTime`가 CSV에 없으면 production gate를
증명하지 못한다. 30일 절차는
[golden-quince 회고 문서](../docs/retro/golden-quince.md)를 따른다.
