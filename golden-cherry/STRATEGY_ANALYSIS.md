# Golden Cherry 전략 분석 (Resolution Momentum)

> 분석 기준: 2026-07-03 코드 스냅샷 (`pyproject.toml` v0.2.0, `config.yaml`, `src/polybot/*`, `data/default/logs/*.log`).
> 이 문서 하나만 읽어도 봇의 동작을 재구성할 수 있도록 작성했다.

---

## 1. 전략 개요

Golden Cherry는 **"해결(resolution)이 가까워진 고확률 favorite은 1.0으로 수렴한다"**는 가정에 베팅하는 봇이다. Gamma API로 유동성 있는 활성 시장을 전수 스캔해서, 확률이 75~92% 구간(config 기준)이고 해결까지 24h~720h(1~30일) 남은 시장의 favorite 토큰을 시장당 1회, 고정 금액(USDC)으로 매수한다. 이후 Jenkins가 3~5분마다 봇을 재실행할 때마다 보유 포지션의 midpoint를 조회해 손절(-8%) → 익절(+10%) → 트레일링 스탑(최고점 대비 -5%) → 시간 청산(해결 12h 전) 순으로 청산 조건을 검사한다. 핵심 수익 원천은 "매수가 0.75~0.92 → 해결 직전 0.95~0.99 매도"의 수렴 구간 캡처이며, 한 번 거래(또는 skip)한 시장은 영구히 재진입하지 않는다.

원래 PRD(`docs/prd_old.txt`)는 "80% 매수 → 90% 매도" 단순 전략이었고, `docs/prd_legacy.txt`에서 모멘텀(골든크로스) 전략을 거쳐(2026-02-04 로그에 흔적 존재), 현재는 `docs/polymarket-strategy-last.md`의 Resolution Momentum(시간 기반 진입 + 4중 청산)으로 정착했다.

---

## 2. 매수/매도 조건 정밀 명세

### 2.1 매수 (Phase 2 스캔 → Phase 3 실행)

스캔(`strategy/scanner.py::scan_buy_candidates`) — 아래를 **모두** 통과해야 후보:

| # | 조건 | 구현 | 비고 |
|---|------|------|------|
| 1 | 활성 시장 | Gamma `GET /markets?active=true&closed=false` 페이지네이션(100개씩, offset≥5000 안전 중단, offset>0의 422는 정상 종료) | |
| 2 | 카테고리 필터 | `is_sports_market()` — `excluded_categories`가 **빈 배열이면 필터 완전 비활성화** | 현재 config는 `[]` → 스포츠 포함 전체 스캔 |
| 3 | 유동성 | `market.liquidity >= min_liquidity` (클라이언트 측 필터) | config 10,000 / 코드 기본 50,000 |
| 4 | side 선택 | `get_high_probability_outcome(market, yes_only)` — 기본: Yes/No 중 확률 높은 쪽. `--yes-only`: **무조건 index 0(Yes) 토큰만** 평가 | 아래 2.3 참고 |
| 5 | 확률 구간 | `buy_threshold <= p <= sell_threshold` (양끝 포함) | 0.75 ≤ p ≤ 0.92 |
| 6 | 시간 구간 | `entry_hours_min <= (endDate - now) <= entry_hours_max` (`time_based.enabled`일 때만). `endDate` 없음/과거면 제외 | 24h ~ 720h |

실행(`strategy/trader.py::execute_buy`) — 후보별 재검증:

| # | 조건 | 동작 |
|---|------|------|
| 1 | `is_already_traded(condition_id)` | trades 테이블 또는 skipped_markets에 있으면 skip (영구) |
| 2 | `max_positions` (>0일 때만) | HOLDING 수가 도달하면 skip. 기본 -1(무제한) |
| 3 | CLOB `get_midpoint(token_id)`로 현재가 재조회 | 조회 실패 시 skip |
| 4 | `current_price > sell_threshold` | **"rapid_jump"로 skipped_markets에 영구 등록** 후 skip |
| 5 | `current_price < buy_threshold` | skip (영구 아님, 다음 사이클 재도전 가능) |
| 6 | `buy_shares = buy_amount_usdc / current_price` 계산, `buy_shares < 5.0`이면 skip | Polymarket 최소 주문 5주(`MIN_ORDER_SIZE`) |
| 7 | `place_limit_order(price=midpoint를 0.01 tick으로 반올림 후 [0.01, 0.99] clamp, side=BUY, GTC)` | 성공 판정: `result.success or result.orderID`. 성공 시 즉시 `HOLDING`으로 DB 기록, `max_price = buy_price`로 초기화 |

**사이징 의미**: `POLYBOT_BUY_AMOUNT`/`buy_amount_usdc`는 **USDC 달러 단위**(센트 아님). 주수 = 금액 ÷ 가격. 예: $5 @ 0.80 → 6.25주. 가격 0.92에서는 5주 최소요건 때문에 최소 $4.6 필요.

### 2.2 매도 (Phase 1, 매 사이클 보유 포지션 전수 검사)

`execute_sell()` 검사 순서(우선순위). 매 사이클 midpoint 조회 → `max_price` 갱신(DB persist) → P&L = (현재가-매수가)/매수가:

| 순위 | 조건 | 파라미터 | config 값 | exit_reason |
|------|------|---------|-----------|-------------|
| 1 | P&L ≤ 손절선 | `stop_loss_percent` | -0.08 | `stop_loss` |
| 2 | P&L ≥ 익절선 | `take_profit_percent` | +0.10 (코드 기본 0.15) | `take_profit` |
| 3 | 현재가 < 최고가×(1-트레일링) | `trailing_stop.percent` (enabled) | 0.05 | `trailing_stop` |
| 4 | 해결까지 < 청산시간 | `time_based.exit_hours` | 12h | `time_exit` |

매도 = 현재 midpoint에 지정가 GTC SELL(보유 주수 전량). 성공 판정 즉시 `COMPLETED` + `realized_pnl = (매도가-매수가)×주수` 기록, 월별 CSV(`data/{job}/trades_YYYY-MM.csv`) append.

**중요한 상호작용**: `max_price`가 매수가로 초기화되므로, 가격이 오르지 못하면 트레일링 -5%가 손절 -8%보다 **먼저** 발동한다. 즉 실효 손절선은 -5%이고, -8% 손절은 한 사이클(3~5분) 사이 3%p 이상 갭 하락할 때만 작동하는 사실상의 백업이다. 또 TP +10%는 매수가 ≤ 0.909일 때만 도달 가능(가격 상한 1.0). 0.91~0.92 진입 건은 익절 불가 → 트레일링/시간 청산으로만 나간다. 실제 수익의 본류는 "수렴 후 해결 12h 전 `time_exit`으로 0.95+ 매도"다.

### 2.3 `--yes-only`와 side 선택

- **기본 모드**: `outcomePrices[0]`(Yes)와 `[1]`(No) 중 높은 쪽을 favorite으로 선택 → **NO 토큰도 매수한다** (longshot 시장의 No @ 0.9 등. 로그에서 다수 확인).
- **`--yes-only` 모드**(Jenkins 운영 모드): `filters.py::get_high_probability_outcome`이 **항상 index 0 토큰만** 반환 → **NO 토큰은 절대 매수 불가**. Yes 확률이 75% 미만인 시장(= No가 favorite)은 확률 필터에서 탈락해 아예 진입하지 않는다. negRisk 다후보 이벤트에서는 각 후보 시장의 index 0 = "그 후보 당선" 토큰이므로 "1위 후보만 산다"는 의미가 된다.
- 주의: CLI `--yes-only`는 **켜기만 가능**하다(`yes_only_mode=True if args.yes_only else None`). 끄려면 플래그 생략 + env/yaml이 false여야 한다. `--simulate`도 동일 구조라 yaml `simulation_mode: true`를 CLI로 끌 수 없다.

### 2.4 중복 진입 방지

- `trades.condition_id`가 `unique` 컬럼. `is_already_traded()` = trades 존재 OR skipped_markets 존재.
- Phase 3 루프와 `execute_buy` 양쪽에서 이중 체크. 완료(COMPLETED) 후에도 재진입 금지(PRD 요구사항).
- 단, **job별 DB 분리**(`data/{job}/trades.db`)라서 다른 job 이름으로 같은 지갑을 돌리면 서로의 거래를 모른다.

### 2.5 시뮬레이션 모드

- 진입 경로 3가지: `--simulate` 플래그, yaml `simulation_mode: true`, `scripts/simulate.py`(강제 True, 기본 job=`simulation`).
- DB가 `trades_sim.db`로 분리되어 실거래 기록과 섞이지 않음.
- `ClobClientWrapper`가 주문만 가짜 성공(`SIM_BUY_xxxx`)으로 리턴하고, **가격 조회(midpoint)는 실제 API를 사용** → CLOB 인증(create_or_derive_api_key)이 일어나므로 시뮬레이션에도 실키(.env)가 필요하다.

### 2.6 파라미터 총표 (우선순위: env > config.yaml > 코드 기본값)

| 파라미터 | env | yaml 키 | 코드 기본 | 현재 yaml | 의미 |
|---------|-----|--------|----------|-----------|------|
| 매수 하한 | `POLYBOT_BUY_THRESHOLD` | `trading.buy_threshold` | 0.75 | 0.75 | 이 확률 이상만 매수 |
| 매수 상한 | `POLYBOT_SELL_THRESHOLD` | `trading.sell_threshold` | 0.92 | 0.92 | 이 확률 초과 시 rapid_jump 영구 skip |
| 매수 금액 | `POLYBOT_BUY_AMOUNT` | `trading.buy_amount_usdc` | 5.0 | 5.0 | 건당 USDC (달러 단위) |
| 최소 유동성 | `POLYBOT_MIN_LIQUIDITY` | `trading.min_liquidity` | 50000 | 10000 | $ 기준 |
| 최대 포지션 | `POLYBOT_MAX_POSITIONS` | `trading.max_positions` | -1 | -1 | -1=무제한 |
| 익절 | `POLYBOT_TAKE_PROFIT` | `trading.take_profit_percent` | 0.15 | 0.10 | 진입가 대비 |
| 손절 | `POLYBOT_STOP_LOSS` | `trading.stop_loss_percent` | -0.08 | -0.08 | 진입가 대비 |
| 트레일링 on/off | `POLYBOT_TRAILING_STOP_ENABLED` | `trading.trailing_stop.enabled` | true | true | |
| 트레일링 % | `POLYBOT_TRAILING_STOP_PERCENT` | `trading.trailing_stop.percent` | 0.05 | 0.05 | 최고점 대비 |
| 시간필터 on/off | `POLYBOT_TIME_BASED_ENABLED` | `trading.time_based.enabled` | true | true | |
| 진입 최대 잔여 | `POLYBOT_ENTRY_HOURS_MAX` | `trading.time_based.entry_hours_max` | 24 | 720 | 시간 |
| 진입 최소 잔여 | `POLYBOT_ENTRY_HOURS_MIN` | `trading.time_based.entry_hours_min` | 4 | 24 | 시간 |
| 청산 잔여 | `POLYBOT_EXIT_HOURS` | `trading.time_based.exit_hours` | 4 | 12 | 시간 |
| YES-Only | `POLYBOT_YES_ONLY` (CLI `--yes-only`가 최우선) | `trading.yes_only_mode` | false | (없음) | |
| 제외 카테고리 | (env 없음) | `trading.excluded_categories` | 스포츠 10종 | `[]` (비활성) | |
| 시뮬레이션 | (CLI `--simulate`) | `simulation_mode` | false | false | |

> 로그(2026-05-20) 기준 실제 운영 인스턴스는 env override로 85%~95% / 유동성 $20k / buy $2,000 조합도 사용된 적이 있다. yaml은 baseline일 뿐, Jenkins env가 실제 운영값이다.

---

## 3. 이용하는 대중 심리

1. **Favorite-Longshot Bias**: 예측시장 참가자는 복권성 longshot을 과대평가하고 favorite을 과소평가하는 경향이 있다. 75~92% 구간의 favorite은 실제 실현 확률 대비 저평가되어 있어, favorite 매수는 구조적 +EV라는 가정.
2. **해결 직전 수렴(정보 확실성 증가)**: 해결이 가까워질수록 불확실성이 사라지며 가격이 0 또는 1로 수렴한다. `docs/polymarket-strategy-last.md`는 Dune Analytics 데이터(24h 전 정확도 88.6% → 4h 전 94.2%)를 근거로 인용. 봇은 이 마지막 수렴 구간(0.8 → 0.98)의 "시간가치 소멸"을 산다.
3. **관성/앵커링**: 이미 방향이 정해진 이벤트도 대중은 마지막까지 극단가(0.99) 매수를 주저하므로, 0.9 부근에 유동성이 남는다. 봇은 그 잔여 스프레드를 기계적으로 수취한다.
4. **해결 리스크 회피 프리미엄**: 해결 직전 보유는 "혹시나" 리스크가 있어 대중이 프리미엄을 요구한다. 봇은 12h 전 `time_exit`으로 그 리스크를 다음 사람에게 넘기면서 수렴분만 취한다.

---

## 4. 아키텍처 요약

```
main.py (루트, sys.path에 src 추가)
 └─ src/polybot/main.py      CLI(argparse): run / status / config
     └─ config.py            .env 로드 → yaml 파싱 → env>yaml>기본값 병합 → BotConfig
     └─ bot.py               PolymarketBot: 1회 사이클 오케스트레이터 (Jenkins 친화, 루프 없음)
         ├─ Phase 1: repo.get_holding_trades() → Trader.execute_sell() → CSV append
         ├─ Phase 2: MarketScanner.scan_buy_candidates()
         └─ Phase 3: is_already_traded 체크 → Trader.execute_buy()
     ├─ api/gamma_client.py  시장 메타데이터 (무인증, requests + 페이지네이션)
     ├─ api/clob_client.py   py-clob-client-v2 래퍼 (lazy init, tick 반올림, sim 모드)
     ├─ api/data_api_client.py  포지션/PnL 조회 (⚠ 봇 사이클에서 미사용, 리포팅용 잔재)
     ├─ strategy/scanner.py  endDate 파싱, 시간창 필터, 스캔 요약 로그
     ├─ strategy/trader.py   매수/매도 실행 + 4중 청산 로직
     ├─ strategy/filters.py  스포츠 필터, favorite 선택(yes_only), 확률 구간 검사
     ├─ db/models.py         SQLAlchemy: trades / market_snapshots / skipped_markets
     ├─ db/repository.py     CRUD + 통계 + 월별 CSV
     ├─ notifications/slack_notifier.py  (⚠ 봇 사이클에서 미사용)
     └─ utils/ logger.py(일자별 파일 로그), retry.py(429/5xx 지수 백오프)
데이터: data/{job}/trades.db, trades_sim.db, logs/YYYYMMDD.log, trades_YYYY-MM.csv
실행: Jenkins cron 3~5분 → `polybot run --yes-only` (job별 DB 분리)
```

- 의존성: Python ≥3.11, `py-clob-client-v2`, `python-dotenv`, `requests`, `SQLAlchemy`, `PyYAML` (uv 관리).
- 상태는 전부 로컬 SQLite. 지갑 실잔고와의 대조(reconciliation)는 없다.

---

## 5. 환경변수 표

| 변수 | 필수 | 기본값 | 효과 |
|------|------|--------|------|
| `POLYMARKET_PRIVATE_KEY` | O | - | CLOB L1 인증. 0x 접두사 자동 제거. 없으면 즉시 종료 |
| `POLYMARKET_FUNDER_ADDRESS` | O | - | 주문 funder 지갑 주소 |
| `POLYBOT_BUY_THRESHOLD` | - | yaml 0.75 | 매수 하한 확률 |
| `POLYBOT_SELL_THRESHOLD` | - | yaml 0.92 | 매수 상한 + rapid_jump 기준 |
| `POLYBOT_BUY_AMOUNT` | - | yaml 5.0 | 건당 매수 금액(USDC 달러) |
| `POLYBOT_MIN_LIQUIDITY` | - | yaml 10000 (코드 50000) | 최소 유동성 $ |
| `POLYBOT_MAX_POSITIONS` | - | -1 | 동시 포지션 상한 |
| `POLYBOT_TAKE_PROFIT` | - | yaml 0.10 (코드 0.15) | 익절 비율 |
| `POLYBOT_STOP_LOSS` | - | -0.08 | 손절 비율 |
| `POLYBOT_TRAILING_STOP_ENABLED` | - | true | 트레일링 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | - | 0.05 | 최고점 대비 하락률 |
| `POLYBOT_TIME_BASED_ENABLED` | - | true | 시간 필터 on/off (⚠ false 시 매수 로그 f-string이 None 포맷으로 crash — 6.2-#9) |
| `POLYBOT_ENTRY_HOURS_MAX` | - | yaml 720 (코드 24) | 진입 최대 잔여시간 |
| `POLYBOT_ENTRY_HOURS_MIN` | - | yaml 24 (코드 4) | 진입 최소 잔여시간 |
| `POLYBOT_EXIT_HOURS` | - | yaml 12 (코드 4) | 시간 청산 기준 |
| `POLYBOT_YES_ONLY` | - | false | Yes(index 0) 토큰만 매수. CLI `--yes-only`가 우선 |
| `SLACK_WEBHOOK_URL` | - | - | SlackNotifier용. **봇 사이클에서는 사용되지 않음** |
| `LOG_LEVEL` | - | - | `.env.example`에 있으나 **코드가 읽지 않음** (로그 레벨은 `--verbose`로만 제어) |

---

## 6. 강점 / 약점

### 6.1 강점

1. **단순하고 검증 가능한 상태 머신**: 스캔 → 매수 → 4중 청산. 사이클당 1회 실행이라 Jenkins 장애 시에도 상태가 DB에 남는다.
2. **수렴 구간 캡처 구조가 실제로 돈이 되는 설계**: TP에 안 걸려도 `time_exit`이 해결 12h 전 0.95+에서 강제로 이익을 실현시켜, favorite 승리 시 8~20% 구간 수익을 체계적으로 회수한다.
3. **rapid_jump skip**: 스캔~주문 사이 급등한 시장을 사지 않고 영구 제외 — PRD의 "계단식 급변 시 아무것도 안 한다" 요구를 그대로 구현.
4. **운영 편의**: env>yaml>기본값 3단 설정, job별 DB/로그 분리, 월별 CSV, `report.py` HTML 리포트, 페이지네이션 422 정상 종료 처리, 429/5xx 백오프 등 운영 디테일이 좋다.
5. **중복 진입 방지**가 DB unique + skip 테이블로 견고하다 (동일 job 내에서는).

### 6.2 약점 / 허점 (구체적)

1. **체결 확인 없음 (가장 큰 회계 허점)**: 지정가 GTC 주문을 midpoint에 내고 `orderID`만 받으면 즉시 HOLDING/COMPLETED로 기록한다. 미체결·부분체결이면 DB와 지갑이 영구히 어긋난다. 미체결 주문 취소/재시도 로직도 없다 (`get_open_orders`, `cancel_order`는 구현만 되어 있고 사이클에서 미호출).
2. **midpoint 기준 P&L 과대평가**: 실제 매수는 ask, 매도는 bid 근처에서 체결되는데 봇은 mid로 사고 mid로 판 것으로 기록. 스프레드 2틱이면 $5 포지션에서 왕복 ~2-4%가 허수다.
3. **해결된 시장 처리 부재**: 보유 중 시장이 endDate보다 먼저 해결되면 orderbook 404 → `execute_sell`이 매 사이클 False만 반환하며 **영구 HOLDING 좀비 포지션**이 된다 (2026-05-20 로그에서 실제 발생). 승리 토큰 redeem 로직도, 패배 확정 시 realized_pnl 반영도 없다. 총 P&L 통계가 실제와 어긋나는 주원인.
4. **트레일링 스탑이 손절을 사실상 대체**: `max_price`가 매수가로 초기화되므로 실효 손절선은 -5%다. 75~85% 구간 favorite의 일상 노이즈(±3~5%p)에 whipsaw로 털리기 쉽다. -8% 손절 파라미터는 장식에 가깝다.
5. **rapid_jump 영구 밴의 부작용**: 매수 검증 시점에 한 번이라도 `sell_threshold`를 넘으면 영구 skip. 이후 가격이 구간으로 되돌아와도 기회를 영영 잃는다. (PRD 의도이긴 하나, 30일 진입창에서는 과도하게 보수적.)
6. **endDate 신뢰성**: Gamma `endDate`는 실제 해결 시각이 아니라 마감 예정일인 경우가 많다. 조기 해결(스포츠/뉴스)이면 time_exit이 발동할 기회 자체가 없고(→ #3), 늦은 해결이면 12h 전 청산이 무의미하게 일찍 나간다. `no_end_date` 시장은 전부 배제.
7. **노출 관리 없음**: `max_positions=-1` + 고정 금액이라 후보가 100개면 100건을 산다. 카테고리/이벤트 상관관계 제어도 없어, 같은 선거의 "A 당선 Yes"와 "B 당선 No"를 동시 매수(2026-05-20 로그의 Texas primary 사례)해 동일 리스크를 2배로 진다.
8. **job 간 중복 매수**: 중복 방지가 job DB 단위라, 같은 지갑으로 다른 job을 돌리면 같은 시장을 이중 매수한다.
9. **`time_based.enabled=false`면 매수가 crash**: `trader.py:114`의 `f"해결까지 {hours_until_resolution:.1f}h"`가 None에 `.1f` 포맷을 적용해 TypeError → 사이클 전체 실패. 확률-only 모드는 현재 코드로는 동작 불가.
10. **스키마 마이그레이션 부재**: `init_database`는 `market_tags` 컬럼만 best-effort ALTER. 2026-02-04 로그에 `no such column: trades.max_price`로 사이클이 실패한 기록이 있다. 컬럼 추가 시마다 수동 DB 조치 필요.
11. **yes-only의 기회 손실**: No가 favorite인 시장(Yes 8% / No 92%)은 진입 자체가 불가능. 로그 기준 후보의 과반이 No side였으므로 `--yes-only` 운영은 진입 유니버스를 크게 줄인다 (의도된 트레이드오프이긴 함).
12. **config.yaml·README·문서 간 드리프트**: README 본문은 4~24h 창을 설명하지만 실제 yaml은 24~720h, 로그의 실운영은 85~95%로 또 다르다. "Resolution Momentum"이라는 이름과 달리 30일 창은 사실상 "시간 필터 있는 favorite 매수"다.
13. **스포츠 필터 비활성**: PRD는 "스포츠 제외"가 명시 요구였으나 현재 `excluded_categories: []`로 완전 꺼져 있고, 실제 NHL/월드컵/테니스 시장을 스캔·매수하고 있다 (2026-05-20 로그: Zverev French Open No 매수).
14. **알림 부재**: SlackNotifier가 있으나 미연결. 주문 실패·좀비 포지션·사이클 crash가 로그 파일에만 남는다.
15. **tick size 하드코딩(0.01)**: 0.001 tick 시장에서는 반올림이 과도해 유리한 가격을 버린다. clamp 상한 0.99로 고가 매도 시 1틱 손해 가능.

---

## 7. 개선 아이디어

1. **주문 라이프사이클 관리**: 주문 후 `get_open_orders`로 체결 확인 → 미체결 시 다음 사이클에 취소·재호가. `PENDING_BUY`/`PENDING_SELL` enum이 이미 있으니 상태 전이만 구현하면 된다.
2. **해결/redeem 처리**: midpoint 404가 N회 연속이면 Gamma에서 `closed`/`umaResolutionStatus` 확인 → 승리면 redeem(또는 수동 알림), 패배면 realized_pnl=-buy_amount로 COMPLETED 처리해 좀비 포지션 제거.
3. **트레일링 활성화 지연**: `max_price > buy_price * (1 + X%)`가 된 뒤에만 트레일링을 켜서(armed trailing), 초기 노이즈 whipsaw를 줄이고 -8% 손절이 실제로 일하게 만든다.
4. **이벤트 단위 노출 상한**: Gamma `events` 정보로 같은 이벤트의 시장을 묶어 이벤트당 1포지션/금액 상한을 두면 상관 리스크 제거. 사이클당 신규 매수 건수 상한도 추가.
5. **호가 기반 진입/청산**: 매수는 best ask, 매도는 best bid를 기준으로 판단·기록 (`get_best_bid/ask`가 이미 구현되어 있음). P&L 회계가 현실화된다.
6. **rapid_jump에 TTL**: 영구 밴 대신 24~48h 재평가. 또는 급등 후 되돌림(예: 구간 재진입 + 최근 고점 대비 -3%)을 오히려 진입 신호로 사용.
7. **Slack 연동**: 매수/매도/실패/좀비 감지 시 `SLACK_WEBHOOK_URL`로 즉시 알림 (코드 이미 존재, 배선만 필요).
8. **버그 수정**: `time_based` off일 때 f-string crash(§6.2-#9), `LOG_LEVEL` env 미사용, `--simulate`/`--yes-only`가 켜기만 되는 비대칭, `datetime.utcnow()` naive/aware 혼용.
9. **지갑 대조 잡**: Data API `/positions`(구현 완료, 미사용)로 일 1회 DB↔지갑 reconciliation을 돌려 회계 드리프트를 조기 탐지.
10. **파라미터 실증**: 월별 CSV에 `hours_until_resolution_at_buy`·`entry_reason`이 쌓이므로, 잔여시간 버킷별(24-72h/72-240h/240-720h) 승률·수익을 집계해 진입창을 데이터로 좁히기. 문서의 4-24h 창 주장 vs 현재 720h 운영의 우열을 검증할 수 있다.
