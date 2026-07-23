# Golden Queen — Crown Momentum

Golden Queen은 `golden-cherry`의 “해결이 가까운 우세 YES가 수렴한다”는 뼈대를 계승하면서,
진입·청산·증거 처리를 하나의 작은 계약으로 줄인 전략이다.

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

그 외 = resolution, redeemable/실제 redeem, CLOB fill을 서로 다른 증거로 취급
```

스포츠는 기본 포함이다. `gameStartTime`이 있으면 경기 시작 시계를 쓰고, 없으면
`endDate`로 복귀한다. 환경변수로 스포츠를 명시적으로 제외하지 않는 한 Sports/NBA/축구
등의 category는 제외하지 않는다. 경기가 시작됐다는 이유만으로 BUY를 막지도 않는다.
현재 코드는 resolution 결과는 기록하지만 redeemable 상태와 실제 redeem transaction을
아직 수집하지 않는다. 둘을 resolution이나 synthetic SELL로 추정하지 않는다.

## 중요한 결론

이 전략은 `golden-cherry/data/default`를 분석해 만든 **보수적인 신규 가설**이지, 과거
수익이 입증된 최적해가 아니다. 2026-06-23~2026-07-23 UTC strict audit에서 Cherry DB는
658개 trade를 보유했지만 다음 이유로 파라미터 최적화에 사용할 수 없었다.

- `COMPLETED` BUY/SELL confirmed-fill coverage 45.6%
- confirmed BUY/SELL 수량 불일치 69건, fill quantity overflow 2건
- fee amount 미확정 87.6%
- 미완료 대사 70건, 불확실한 submission 19건
- 선택 기간 실패 run 215건, 최대 성공 run 간격 445.67시간
- `market_snapshots` 0행

따라서 legacy `realized_pnl`, 접수된 GTC 주문, 로그 문구를 실제 체결 수익으로 재해석하지
않았다. Queen은 이 결함을 반복하지 않도록 accepted BUY를 `PENDING_BUY`로 두고 **정확한
full confirmed fill** 이후에만 `HOLDING`으로 바꾼다. 실제 수익성은 Queen 자체 archive와
execution ledger를 한 달 쌓은 다음 확인해야 한다.

## 왜 더 단순한가

- 진입 신호는 `0.90`의 첫 상향 교차 하나뿐이다.
- YES만 사고 strict Yes/No·non-negRisk 시장만 받는다.
- 일반 시장 시간창은 `(0h, 24h]` 하나다.
- 사전 해결 청산은 `0.98` 목표와 `0.85` 절대 stop 두 개뿐이다.
- trailing stop과 time exit은 없다.
- routine 조절값은 주문 금액 하나이며, 유동성·거래량·총 노출은 자동 확장된다.
- 한 cycle에서 최대 1개, event당 최대 1개만 새로 산다.

`0.90~0.94`는 `0.95~0.97`보다 1달러 수렴까지 남은 폭이 크고, 첫 실패 한 건이 지우는
정상 승리 수를 줄인다. 그렇다고 이 밴드가 우월하다는 뜻은 아니다. 이를 검증하기 위해
24시간과 12시간 horizon만 다르게 한 A/B를 사전 등록한다.

## Quickstart

```bash
cd golden-queen
uv sync --frozen --extra dev

# 아래 두 값은 로컬 .env 또는 Jenkins Credentials Binding으로만 제공
export POLYMARKET_PRIVATE_KEY=...
export POLYMARKET_FUNDER_ADDRESS=...
export POLYMARKET_SIGNATURE_TYPE=3  # 실제 계정이 POLY_1271일 때만 3
export POLYBOT_BUY_AMOUNT=5

uv run polybot config
uv run pytest
uv run polybot run --simulate --job queen-sim
```

저장소 기본값은 simulation이다. 실제 주문은 `--live`를 명시해야만 활성화된다.

```bash
uv run polybot run --live --job queen-live-24h
```

`config`는 주문을 실행하지 않고 최종적으로 해석된 값, DB 경로, simulation 여부를
보여준다. Jenkins 적용 전 typo와 잘못된 계정 signature type을 확인하는 용도다.

## 최소 Jenkins shell

Private key와 funder는 Jenkins **Credentials Binding**으로 주입한다. Freestyle shell이
`-x`로 실행될 수 있으므로 secret을 참조하기 전부터 `set +x`를 둔다.

```bash
#!/bin/bash
set -euo pipefail
set +x

# POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER_ADDRESS:
# Jenkins Credentials Binding에서 주입
export POLYMARKET_SIGNATURE_TYPE=3
export POLYBOT_BUY_AMOUNT=5
export LOG_LEVEL=INFO

cd ./golden-queen
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job queen-live-24h
/Users/jongwoopark/.local/bin/uv run polybot run --live --job queen-live-24h
```

Jenkins timer는 `H/5 * * * *`, concurrent build는 비활성화를 권장한다. 실행 시간이 5분을
넘으면 중첩 실행을 허용하지 말고 다음 build가 queue에서 기다리게 한다.

## 12시간 대 24시간 A/B

두 계정, 두 Jenkins job, 두 `--job` DB를 사용한다. 같은 지갑·SQLite를 공유하면 A/B가
아니며 주문 충돌과 성과 귀속 오류가 생긴다.

고정할 값:

- Git commit과 schedule
- `POLYBOT_BUY_AMOUNT=5`
- 진입 `0.90~0.94`, 목표 `0.98`, stop `0.85`
- 유동성·volume·spread·depth·position/event cap
- sports 포함과 in-play 규칙

유일하게 바꿀 값:

```bash
# 대조군
export POLYBOT_ENTRY_HOURS_MAX=24
uv run polybot run --live --job queen-live-24h

# 실험군
export POLYBOT_ENTRY_HOURS_MAX=12
uv run polybot run --live --job queen-live-12h
```

같은 `event_id`가 양쪽에 나타나면 독립 2건으로 세지 않고 paired event로 분석한다. 첫 한
달 동안 threshold·금액·유동성 기준을 동시에 바꾸지 않는다.

## 자체 archive와 “최초 관측”의 범위

Queen은 진입 gate보다 넓은 `YES >= 0.80`, scheduled/pregame `<= 72h` 시장을 60일
보존한다. Gamma request 유동성 하한은
`min(POLYBOT_MIN_LIQUIDITY, $1,000)`이므로 기본값은 $1,000이다. `event_id`가 빠진
observation도 archive에는 남겨 교차 이력을 소실하지 않지만, 신규 진입에서는 거부한다.
따라서 “최초 0.90 상향 교차”는 전 세계 과거 전체가 아니라 이 Queen archive envelope와
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
  Queen은 현재 첫 번째만 적재하며 redeemable/실제 redeem coverage는 아직 제공하지 않는다.

오류 범위도 분리한다. 원자적 market sweep/archive나 RunAudit 자체가 실패하면 cycle 전체를
중단한다. 개별 시장의 event/lineage/book/depth 누락은 해당 시장만 제외한다. 개별 과거
주문의 대사 오류는 해당 `token_id × side` 신규 주문만 격리하고 다른 시장의 cycle은
계속한다.

## 자동 scale

`POLYBOT_BUY_AMOUNT`만 바꾸면 metadata gate와 총 노출 상한이 같이 커진다.

| 주문 | 최소 liquidity | 최소 24h volume | open notional 상한 |
|---:|---:|---:|---:|
| $5 | $10,000 | $2,000 | $50 |
| $100 | $100,000 | $5,000 | $1,000 |
| $1,000 | $1,000,000 | $50,000 | $10,000 |

코드 hard cap은 `$1,000`이다. 표의 자동 계산은 주문이 안전하거나 전략이 수익이라는
보장이 아니다. `$5 → $100 → $1,000`은 한 달 strict audit, exact fill/fee coverage,
실제 depth/slippage, drawdown, event-effective 표본을 각 단계에서 통과한 뒤에만 진행한다.
상세 gate는 [SCALING_AND_TAIL_RISK.md](docs/SCALING_AND_TAIL_RISK.md)에 있다.

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

`close_only`는 강제 매도가 아니다. entry horizon을 24시간에서 12시간으로 바꿔도 기존
포지션을 팔지 않는다. 기존 포지션은 매수 당시 저장된 `0.98/0.85` 값과 resolution 증거로만
관리된다. 퇴역은 [전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)을 따른다.

## 환경변수

우선순위는 `env > config.yaml > 코드 기본값`이다. 정상 운용에는 앞의 4개 계정/금액 값과
선택적 `LOG_LEVEL`만 사용하고, 아래 나머지는 실험 cohort를 새로 만들 때만 변경한다.

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `POLYMARKET_PRIVATE_KEY` | 필수 | CLOB 서명 key. 로그·문서·commit 금지 |
| `POLYMARKET_FUNDER_ADDRESS` | 필수 | 이 Queen 계정의 funder. 로그·commit 금지 |
| `POLYMARKET_SIGNATURE_TYPE` | `1` | 계정에 맞는 `1` 또는 `3` |
| `POLYBOT_BUY_AMOUNT` | `5` | 한 진입의 USDC. hard cap `$1,000` |
| `LOG_LEVEL` | `INFO` | Python 로그 수준 |
| `POLYBOT_LIFECYCLE_MODE` | `active` | `active` / `close_only` / `archive_only` |
| `POLYBOT_ENTRY_HOURS_MAX` | `24` | A/B에서만 `12`로 변경 |
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
  --db data/queen-live-24h/trades.db \
  --days 30 \
  --output-dir "$HOME/polybot-retro/queen-24h" \
  --strict
```

`scripts/backtest.py`는 운영 DB나 네트워크를 열지 않고 immutable research CSV만 읽는다.
Queen DB에서 충분한 snapshot/catalog가 쌓인 뒤 사용한다.

```bash
uv run python scripts/backtest.py /absolute/path/queen-research.csv \
  --output-dir "$HOME/polybot-retro/queen-2026-08" \
  --review-start 2026-08-01 \
  --review-end 2026-08-31
```

replay의 grid는 사전 등록한 `12h`와 `24h` 두 줄뿐이다. full depth와 sports
`gameStartTime`가 CSV에 없으므로 offline 결과는 production depth/sports clock을 증명하지
않으며 observed bid/ask도 가상 체결이다. actual result는 exact confirmed fill을 별도로
join해야 한다. 월간 절차는 [golden-queen 회고 문서](../docs/retro/golden-queen.md)를 따른다.
