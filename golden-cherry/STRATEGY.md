# Golden Cherry 전략: Resolution Momentum

## 1. 한 줄 요약 + 노리는 심리 편향

해결이 가까운(기준시각까지 0~120h) 시장에서 이미 75~92%로 앞서 있는 YES를 사서, 남은 불확실성이 해소되며 가격이 1.0으로 수렴하는 구간을 먹는다. 노리는 편향은 **favorite-longshot bias** — 사람들이 "거의 확실한" 결과에 마땅히 줘야 할 값보다 조금 덜 주는 경향이다.

## 2. 왜 이 전략인가

승률이 높으므로 심리적으로 유지하기 쉽고, 개별 손실이 드물게 발생한다. 다만 손실 한 건의 크기가 이익 한 건보다 훨씬 크다(0.85에 사면 이길 때 +0.15, 질 때 -0.85). 따라서 **승률이 진입가를 넘어야만** 수익이 난다.

### 2026-07 실측으로 확인된 것

`docs/retro/golden-cherry-2026-07-parameter-review.md` (체결 300건, Gamma 해결 결과 대조):

- **실현 YES율이 모든 밴드에서 진입가와 거의 같았다** (0.769→76.9%, 0.820→81.2%, 0.879→90.4%, 0.907→91.3%). 즉 편향은 이 구간에서 관측되지 않았고 시장은 효율적이었다.
- 수수료는 $0였다. 따라서 손익을 가르는 것은 **진입가가 아니라 청산 규칙**이다.
- 익절(+10%)은 해결까지 보유 대비 +$2,523을 벌었다. 일시적 상승을 파는 것은 실제로 유효하다.
- 손절은 해결까지 보유 대비 -$1,902를 까먹었다. 유일하게 파괴적인 규칙이다.

## 3. 진입/청산 규칙 정밀 명세

### 진입 (모두 충족)

1. `liquidity >= max(min_liquidity, buy_amount / max_order_liquidity_ratio)`
2. Gamma `outcomePrices` 기준 `buy_threshold <= p <= sell_threshold` (양끝 포함)
3. 스포츠(`sportsMarketType` 존재)면 `gameStartTime`이 있어야 한다 — 없으면 `reject_sports_without_game_start`가 fail closed
4. 기준시각까지 `entry_hours_min <= 잔여 <= entry_hours_max`. 기준시각은 비스포츠 `endDate`, 스포츠 `gameStartTime`
5. 경기 시작 후에는 `allow_in_play=true`이고 Gamma가 주문 가능하다고 보고할 때만 진입 (이때 시간창은 적용되지 않는다)
6. 주문 직전 CLOB midpoint 재확인 — 스캔은 Gamma 가격, 주문은 midpoint라 여기서 걸릴 수 있다

### 청산 (우선순위 순, 매 사이클 검사)

`current_price = CLOB midpoint`, `pnl = (current - buy) / buy` 기준의 `if / elif / elif` 체인이다. **앞의 것이 걸리면 뒤는 검사하지 않는다.**

1. `pnl <= stop_loss_percent` → `stop_loss`
2. `elif pnl >= take_profit_percent` → `take_profit`
3. `elif current < max_price * (1 - trailing_percent)` → `trailing_stop`
4. (별도 `if`) `0 < 잔여시간 <= exit_hours` → `time_exit`. `exit_hours=0`이면 비활성

**기하 구조상 주의할 점 두 가지:**

- `max_price`는 매수가로 초기화된다. 따라서 한 번도 오르지 않은 포지션은 트레일링(-5%)이 손절(-8%)보다 **항상 먼저** 걸린다. 손절이 발동하는 유일한 경우는 5분 사이클 사이에 가격이 -8%를 건너뛰고 떨어진 갭이다. → **`stop_loss` 값만 바꾸는 것은 효과가 없다.**
- 익절 목표는 `buy_price × (1 + take_profit_percent)`이고 상한 클램프가 없다. `take_profit=0.10`이면 **진입가 0.909 이상에서는 목표가 $1.00을 넘어 영원히 발동하지 않는다.**

매도는 midpoint 지정가 GTC다. 시장가가 아니며 재호가·추적이 없다. live 주문 접수는
체결이 아니므로 BUY는 `PENDING_BUY`, SELL은 `PENDING_SELL`에 기록하고 exact confirmed
수량 대사가 끝난 뒤에만 실제 체결 수량으로 `HOLDING`/`COMPLETED`로 전이한다. terminal
partial fill은 미체결 잔여를 체결로 가정하지 않으며, 실제 잔여 수량을 계속 보존한다. fee amount 누락은
명시적 zero rate 또는 builder-fee 경로가 없는 이 봇의 exact `MAKER` fill에 한해서만
known zero로 인정하며, `TAKER`·role 불명은 계속 fail closed한다.

## 4. 파라미터·env var 표

| env | 기본 | 의미 |
|---|---|---|
| `POLYBOT_BUY_THRESHOLD` | 0.75 | 진입 확률 하한 |
| `POLYBOT_SELL_THRESHOLD` | 0.92 | 진입 확률 **상한** (매도 조건이 아니다) |
| `POLYBOT_TAKE_PROFIT` | 0.15 (yaml 0.10) | 익절, 진입가 대비 |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절, 진입가 대비 |
| `POLYBOT_TRAILING_STOP_ENABLED` / `_PERCENT` | true / 0.05 | 최고가 대비 하락률 |
| `POLYBOT_ENTRY_HOURS_MIN` / `_MAX` | 0 / 120 | 기준시각까지 진입 허용 창 |
| `POLYBOT_EXIT_HOURS` | 0 | 0이면 시간 청산 비활성 |
| `POLYBOT_GAME_START_FILTER_ENABLED` | true | 스포츠 기준시각을 `gameStartTime`으로 |
| `POLYBOT_ALLOW_IN_PLAY` | true | 경기 시작 후 진입 허용 |
| `POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START` | true | 킥오프 불명 스포츠 fail closed |
| `POLYBOT_MIN_LIQUIDITY` | 50000 | 정적 유동성 하한 |
| `POLYBOT_MAX_ORDER_LIQUIDITY_RATIO` | 0.002 | 동적 하한 = 주문액 / 이 값 |
| `POLYBOT_BUY_AMOUNT` / `_MAX_BUY_AMOUNT_USDC` | 5 / 100 | 건당 주문액과 하드캡 |
| `POLYBOT_MAX_POSITIONS` | 10 | 오픈 포지션 상한 |
| `POLYBOT_MAX_OPEN_NOTIONAL_USDC` | 5000 | 오픈 요청 원금 상한 |
| `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE` | 1 | 사이클당 신규 진입 |
| `POLYBOT_PENDING_BUY_TTL_MINUTES` | 30 | exact LIVE·0 fill BUY 취소 전 대기시간 |
| `POLYBOT_YES_ONLY` | false | index 0 토큰만 |
| `POLYBOT_LIFECYCLE_MODE` | active | `active`/`close_only`/`archive_only` |
| `POLYMARKET_SIGNATURE_TYPE` | 1 | 1=POLY_PROXY, 3=POLY_1271 |

전체 설명은 `README.md`와 `docs/retro/golden-cherry.md`에 있다. 런타임이 읽지만 이 표에 없는 `POLYBOT_DB_*`·`GIT_COMMIT`은 `AGENTS.md` 참조.

## 5. 이 전략이 실패하는 경우

- **효율적으로 가격이 매겨진 구간.** 2026-07 실측이 정확히 이 경우였다. 승률 = 진입가면 기대값은 0이고, 청산 규칙의 순효과만 남는다.
- **갭.** 이진 시장에서 favorite이 무너질 때 가격은 0.90 → 0.20으로 한 번에 간다. 5분 폴링과 지정가 매도로는 방어할 수 없다. 손절은 이 위험을 줄이지 못하면서 노이즈에는 반응한다.
- **익절 도달 불가 구간.** 진입가 0.909 이상은 수익 엔진 없이 하방만 진다.
- **미체결 누적.** 주문액이 커질수록 체결률이 급락하고($100 66.6% → $3,000 7.6%), 미체결 행이 `max_positions`를 잠식해 봇을 정지시킨다. 2026-07-22~28에 실제로 발생했다.

## 6. A/B 검증 방법

`docs/ab-retro-playbook.md`를 따른다. cherry 고유 주의사항:

- 성과는 **반드시** `order_fills.status='CONFIRMED'`로 계산한다. `trades.realized_pnl`은 요청가 기반이라 무의미하다.
- `strategy_configs`에서 `config_hash`를 확인해 단일 cohort로 자른다. 2026-07 구간은 `buy_amount`가 3000→2000→1000→100→500으로 바뀐 혼합 구간이었다.
- 시장 해결 결과는 Gamma에 `closed=true`를 붙여 재조회한다. 붙이지 않으면 해결된 시장이 반환되지 않는다.

## 7. 베리에이션 아이디어

- **진입 상한을 익절 도달 가능선에 묶기**: `sell_threshold <= 1/(1+take_profit) - ε`. `take_profit=0.10`이면 0.90 미만.
- **비스포츠 전용**: 2026-07 실측에서 비스포츠 +16.7% / 스포츠 -7.2%였다. 다만 이는 `game_start` 도입 이전 endDate 체제의 결과라 현재 체제에 그대로 적용되지 않는다.
- **해결까지 보유(청산 없음)**: 익절이 순기여였으므로 권하지 않는다.

## 8. 알려진 구현 한계

- `market_snapshots`가 0행이다. `save_snapshot` 호출부가 없어 가격 경로가 남지 않고, 경로 의존 반사실을 계산할 수 없다.
- 스포츠 진입 계측 컬럼 5개(`entry_time_reference`, `sports_phase_at_buy`, `hours_until_entry_deadline_at_buy`, `minutes_until_game_start_at_buy`, `market_game_start_time`)가 전부 NULL이다.
- 2026-08-14 이전 legacy 매도는 GTC 접수만으로 `COMPLETED`가 될 수 있다. 이후 live
  주문은 exact confirmed SELL full fill과 fee evidence가 완결될 때까지 `PENDING_SELL`이다.
- 해결된 시장의 포지션이 자동 정리되지 않는다. redeem 회계가 없다.
- `filters.py`의 `should_sell()`은 어디서도 호출되지 않는 죽은 코드다.
