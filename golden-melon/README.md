# Golden Melon — Resolution Sprint

해결이 임박한(≤72h) 표준 이진 시장에서 YES 확률이 **처음으로** `[0.85, 0.93]`에 올라서고
24시간 거래량 gate를 통과하면 매수하고, `0.97` 목표 / `0.78` 절대 손절로 관리한다.

`golden-cherry`의 재설계다. 설계 근거와 cherry 671건 진단은 [`STRATEGY.md`](STRATEGY.md).

```text
진입 = strict standard binary YES
     + 직전 저장 YES < 0.85 <= 현재 저장 YES <= 0.93
     + 두 snapshot 간격 0분 초과 15분 이하
     + 과거 60일 안에 YES >= 0.85 관측이 없었음
     + 일반 시장: 0시간 초과, 종료까지 72시간 이하
     + 스포츠: gameStartTime 기준 경기 전 또는 시작 후 360분 이내
     + 24h 거래량 >= min_volume_24h        ← A/B/C 처치축
     + 유동성 >= max($20,000, 주문액/0.001)
     + fresh spread <= 0.02, ask depth >= 주문 주식 수의 1.2배

청산 = 미해결 상태에서 YES >= 0.97 이고 fresh bid >= 0.97
    또는 YES <= 0.78
    trailing stop 없음, time exit 없음
```

## 중요한 결론

**배리어로는 edge가 생기지 않는다.** 밴드 중앙 0.89에서 손익분기 승률 58.0%,
martingale 도달확률 57.9%로 같다. edge가 있다면 **거래량 gate의 진입 선별**에서만 온다.
그것이 A/B/C가 검정하는 유일한 것이다.

**$5는 선호가 아니라 하한이다.** CLOB 최소 주문이 5.0 shares라 $1은 1.1주가 되어 전량
거절된다. 이 제약이 진입 밴드 상한도 정한다.

**꼬리는 파라미터가 아니라 금액으로 막는다.** cherry의 손절 52건 중 31건(60%)이 매수 후
30분 이내에 평균 −24.6%(최악 −99.3%)로 발생했다. 5분 폴링으로는 못 잡는다.
$3,000짜리 −99%는 −$2,970이지만 $5짜리는 −$4.95다.

## Quickstart

```bash
cd golden-melon
uv sync --frozen
uv run pytest

uv run python main.py config --job polybot-melon-mid
uv run python main.py run --simulate --job melon-sim
```

저장소 기본값은 simulation이다. 실제 주문은 `--live`를 명시해야만 활성화된다.

> `main.py config`는 `--live`를 받지 않아 **항상 simulation 기준으로 출력**한다.
> 표시되는 `Simulation: True`와 `trades_sim.db`는 실제 run의 모드가 아니다.
> 실제 모드는 실행 로그의 `[RUN_AUDIT] ... mode=live`로 확인한다.

## 3팔 Jenkins 실험

세 팔은 **서로 다른 wallet/account, Jenkins job, `--job`, SQLite DB**를 쓴다.
같은 Git commit, cadence `H/5 * * * *`, concurrent build 비활성화.
**세 job을 같은 시각에 기동한다** — 팔마다 자기 archive에서 최초 교차를 판정하므로
시차를 두면 후보 집합이 갈린다.

| 팔 | Jenkins job = `--job` | `POLYBOT_MIN_VOLUME_24H` | 입금 |
|---|---|---:|---:|
| A | `polybot-melon-low` | 20000 | $100 |
| B | `polybot-melon-mid` | 50000 | $100 |
| C | `polybot-melon-high` | 150000 | $100 |

### A — low

```bash
#!/bin/bash
set -euo pipefail
set +x
: "${POLYMARKET_PRIVATE_KEY:?Credentials Binding 필요}"
: "${POLYMARKET_FUNDER_ADDRESS:?Credentials Binding 필요}"
export POLYMARKET_SIGNATURE_TYPE=1

export POLYBOT_MIN_VOLUME_24H=20000        # ← 팔마다 다른 유일한 값
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_EXPERIMENT_CAPITAL_USDC=100
export LOG_LEVEL=INFO

cd ./golden-melon
uv sync --frozen
uv run python main.py config --job polybot-melon-low
uv run python main.py run --live --job polybot-melon-low
```

### B — mid

`POLYBOT_MIN_VOLUME_24H=50000`, `--job polybot-melon-mid`. 나머지는 A와 동일.

### C — high

`POLYBOT_MIN_VOLUME_24H=150000`, `--job polybot-melon-high`. 나머지는 A와 동일.

`POLYMARKET_SIGNATURE_TYPE`은 지갑에 맞는 `1` 또는 `3`을 각 job에 설정한다. 틀리면
CLOB이 전 주문을 `maker address not allowed`로 거절한다.

## 첫 체결에서 확인할 것

```bash
sqlite3 data/polybot-melon-mid/trades.db \
  "SELECT order_id, side, status, liquidity_role, size, price,
          fee_rate_bps, fee_amount_usdc
     FROM order_fills ORDER BY matched_at DESC LIMIT 5;"
```

`fee_amount_usdc`가 0이 아니면 배리어 산술을 다시 계산해야 한다.

## 환경변수

우선순위는 `env > config.yaml > 코드 기본값`이다.

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `POLYMARKET_PRIVATE_KEY` | 필수 | CLOB 서명 key. 로그·commit 금지 |
| `POLYMARKET_FUNDER_ADDRESS` | 필수 | 해당 arm의 funder |
| `POLYMARKET_SIGNATURE_TYPE` | `1` | 계정에 맞는 `1` 또는 `3` |
| **`POLYBOT_MIN_VOLUME_24H`** | `50000` | **A/B/C 처치축** |
| `POLYBOT_BUY_AMOUNT` | `5` | 건당 주문액. 하한이며 증액은 판정 후 |
| `POLYBOT_EXPERIMENT_CAPITAL_USDC` | `100` | kill switch 분모 (3팔 동일) |
| `POLYBOT_MAX_DRAWDOWN_STOP` | `0.20` | → 경제손익 −$20에서 신규 진입 차단 |
| `POLYBOT_ENTRY_PROB_MIN` | `0.85` | 최초 상향 교차·진입 밴드 하한 |
| `POLYBOT_ENTRY_PROB_MAX` | `0.93` | signal·fresh ask 상한 |
| `POLYBOT_ENTRY_HOURS_MAX` | `72` | 해결까지 남은 시간 상한 |
| `POLYBOT_TAKE_PROFIT_PRICE` | `0.97` | signal과 fresh bid가 모두 도달해야 매도 |
| `POLYBOT_STOP_PRICE` | `0.78` | 절대 YES stop |
| `POLYBOT_MIN_LIQUIDITY` | `20000` | metadata liquidity 바닥 |
| `POLYBOT_MAX_ORDER_LIQUIDITY_RATIO` | `0.001` | 주문/liquidity 최대 0.1% |
| `POLYBOT_MAX_POSITIONS` | `20` | 해당 job DB의 동시 open state 상한 |
| `POLYBOT_MAX_EVENT_POSITIONS` | `1` | 같은 event 동시 노출 상한 |
| `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE` | `1` | 한 실행 최대 신규 position |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | `168` | 같은 condition 재진입 최소 간격 |
| `POLYBOT_EXECUTION_MODE` | `nearest` | **melon의 처치가 아니다.** 고정 |
| `POLYBOT_ARCHIVE_PROB_MIN` | `0.75` | 반사실 archive YES 하한 |
| `POLYBOT_ARCHIVE_HOURS_MAX` | `168` | archive 시계 상한 |
| `POLYBOT_LIFECYCLE_MODE` | `active` | `active` / `close_only` / `archive_only` |
| `LOG_LEVEL` | `INFO` | Python 로그 수준 |

## 퇴역

`close_only`는 강제 매도가 아니다. 설정을 바꿔도 기존 포지션은 매수 당시 저장된
`0.97/0.78` 값과 resolution 증거로만 관리된다. 퇴역 절차는
[전략 퇴역 플레이북](../docs/strategy-wind-down-playbook.md)을 따른다.

## 회고

30일 절차는 [golden-melon 회고 가이드](../docs/retro/golden-melon.md)를 따른다.

```bash
uv run --project ../polybot-observability polybot-retro audit \
  --db data/polybot-melon-low/trades.db \
  --db data/polybot-melon-mid/trades.db \
  --db data/polybot-melon-high/trades.db \
  --days 30 --output-dir "$HOME/polybot-retro/melon-30d" --strict
```
