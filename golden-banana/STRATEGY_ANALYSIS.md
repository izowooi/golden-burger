# Golden Banana 전략 분석 (STRATEGY_ANALYSIS)

> 분석 기준: 2026-07-03 시점 코드 (`pyproject.toml` v0.2.0, `src/polybot/**`, `config.yaml`, `docs/prd*.txt`, `data/default/logs/*.log`)

## 1. 전략 개요

Golden Banana는 Polymarket에서 확률 85~97% 구간의 고확률 outcome을 "모멘텀이 상승 중일 때만" 매수하는 momentum 전략 봇이다. 구버전(golden-apple 계열)의 "80% 매수 → 90% 매도" 정적 임계값 전략을 개선한 것으로(`docs/prd.txt`), 5분 주기의 Jenkins 실행마다 전체 시장 확률을 SQLite `market_snapshots` 테이블에 자체 축적하고, 이 스냅샷으로 단기(15분=3 스냅샷)·장기(6시간=72 스냅샷) momentum을 계산해 golden cross(단기-장기 ≥ +2%p/스냅샷) 발생 시 진입한다. 청산은 4중 조건(확률 97% 도달, 진입가 대비 +7% 이익실현, -10% 손절, dead cross)으로 다변화했다. 주문은 CLOB v2에 midpoint 가격의 GTC limit order로 실행되며, 한 번 거래한 시장은 영구히 재거래하지 않는다.

> 2026-07-11 안전 패치: 현재 구현은 단기 3개·장기 72개 전체와 각 명목 시간 구간의 90% 이상 timestamp 커버리지를 요구한다. 아래의 cold-start fallback 분석은 패치 전 운영 이력 설명이며, 현재는 장기 윈도우가 부족하면 `insufficient_long_data`로 진입을 거부한다.

## 2. 매수/매도 조건 정밀 명세

### 2.1 파라미터 표 (env var > config.yaml > 코드 기본값 순으로 적용)

| 파라미터 | config.yaml 값 | 코드 기본값 | env var | 의미 |
|---|---|---|---|---|
| `buy_threshold` | 0.85 | 0.85 | `POLYBOT_BUY_THRESHOLD` | 매수 최소 확률 |
| `sell_threshold` | 0.97 | 0.97 | `POLYBOT_SELL_THRESHOLD` | 매수 상한이자 확률 기준 매도 트리거 |
| `buy_amount_usdc` | 5.0 | 10.0 | `POLYBOT_BUY_AMOUNT` | 1회 매수 금액 (USDC) |
| `min_liquidity` | 50000 | 50000.0 | `POLYBOT_MIN_LIQUIDITY` | 최소 유동성 필터 ($) |
| `max_positions` | -1 (무제한) | -1 | `POLYBOT_MAX_POSITIONS` | 동시 보유 포지션 상한 |
| `take_profit_percent` | 0.07 | 0.07 | `POLYBOT_TAKE_PROFIT` | 진입가 대비 이익실현 (+7%) |
| `stop_loss_percent` | -0.10 | -0.10 | `POLYBOT_STOP_LOSS` | 진입가 대비 손절 (-10%) |
| `momentum.enabled` | true | true | `POLYBOT_MOMENTUM_ENABLED` | momentum 필터 on/off |
| `momentum.short_window` | 3 | 3 | `POLYBOT_MOMENTUM_SHORT_WINDOW` | 단기 윈도우 (3 스냅샷 ≈ 15분) |
| `momentum.long_window` | 72 | 72 | `POLYBOT_MOMENTUM_LONG_WINDOW` | 장기 윈도우 (72 스냅샷 ≈ 6시간) |
| `momentum.golden_cross_threshold` | 0.02 | 0.02 | `POLYBOT_GOLDEN_CROSS_THRESHOLD` | 진입: 단기-장기 ≥ 이 값 |
| `momentum.dead_cross_threshold` | -0.02 | -0.02 | `POLYBOT_DEAD_CROSS_THRESHOLD` | 청산: 단기-장기 ≤ 이 값 |
| `momentum.require_positive_long_momentum` | true | true | `POLYBOT_REQUIRE_POSITIVE_LONG_MOMENTUM` | golden cross여도 장기 momentum ≤ 0이면 진입 거부 |
| `excluded_categories` | Sports 계열 10종 | 동일 | (env 없음) | 제외 카테고리 |
| `simulation_mode` | false | false | (CLI `--simulate`로만 override) | 실주문 여부 |
| (상수) `MIN_ORDER_SIZE` | - | 5.0 | - | 최소 주문 수량 5주 (`trader.py`) |
| (상수) `DEFAULT_TICK_SIZE` | - | 0.01 | - | 가격 tick 반올림, [0.01, 0.99] clamp |

### 2.2 가격 이력(price history) 수집 방식 — 핵심 메커니즘

**외부 price-history endpoint를 쓰지 않는다.** CLOB의 `/prices-history` 같은 이력 API 호출은 없고, 봇이 스스로 이력을 만든다:

1. **수집 (Phase 0, 매 사이클)**: `scanner.save_market_snapshots()`가 Gamma API `GET https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset=N`을 100개씩 페이지네이션(offset ≥ 5000 안전 한도, offset>0에서 422 응답은 마지막 페이지로 간주)으로 전량 조회한다. `min_liquidity` 이상 + 스포츠 아님 조건을 통과한 시장마다 `outcomePrices` 중 높은 쪽 확률을 뽑아 `market_snapshots` 테이블에 1행 INSERT (`condition_id`, `probability`, `liquidity`, `volume_24h`, `timestamp=utcnow`). 실측 로그 기준 사이클당 약 30~35행.
2. **조회**: 판단 시 `repo.get_snapshots_for_condition(condition_id, limit=long_window+10)` → **최신 82개**를 `timestamp DESC LIMIT 82`로 가져와 뒤집어 오래된 순 리스트로 반환.
3. **granularity**: 스냅샷 간격 = Jenkins cron 간격(5분 가정). 현재 코드는 단기 3개·장기 72개 전체와 각 명목 구간의 90% 이상 timestamp 커버리지를 요구한다.
4. **보존**: Phase 4에서 7일 지난 스냅샷 삭제 (`cleanup_old_snapshots(days=7)`).

### 2.3 momentum / golden cross 계산 (`strategy/momentum.py`)

- **momentum 정의**: `(최신 확률 - 가장 오래된 확률) / len(snapshots)` — 이동평균(MA) cross가 아니라 **양 끝점 기울기(rate of change)** 비교다. 이름만 golden cross.
- **단기 momentum**: 최근 3개 스냅샷. 3개 미만이면 `None` → 진입 불가(`insufficient_short_data`).
- **장기 momentum**: 최근 72개. 72개 또는 90% 시간 커버리지 중 하나라도 부족하면 `None`.
- **golden cross**: `short - long >= 0.02`. 단, `require_positive_long_momentum=true`면 `long > 0`도 필요.
- **cold-start 처리**: 장기 momentum이 `None`이면 `insufficient_long_data`로 진입 거부. 패치 전 실운영 로그(20260205)의 매수는 전부 제거된 `short_momentum_positive` 경로였다.
- **dead cross**: `short - long <= -0.02` (둘 다 `None` 아니어야 판단).

### 2.4 매수 파이프라인 (조건 전부 AND, 순서대로)

1. Gamma 전량 조회에서 `liquidity >= 50,000`.
2. 스포츠 아님: tags의 slug/label이 `excluded_categories`에 없고, question+slug 텍스트에 excluded 카테고리 및 `SPORTS_KEYWORDS`(약 50개; "game", "match", "win the", "beat", "score" 등 포함)가 없어야 함.
3. `outcomePrices` 두 값 중 큰 쪽(outcome/token 선택)이 존재하고 `token_id`가 있어야 함. Yes/No 중 높은 쪽을 산다.
4. 확률 구간: `0.85 <= probability <= 0.97` (상한 **포함**).
5. momentum 진입 시그널: 유효한 단기·장기 윈도우의 `golden_cross` (momentum 비활성화 시 무조건 통과 `momentum_disabled`).
6. `is_already_traded(condition_id)` false — trades 또는 skipped_markets에 이미 있으면 영구 skip.
7. `max_positions` 제한 (기본 무제한).
8. CLOB `get_midpoint(token_id)` 재검증: midpoint > 0.97이면 `rapid_jump`로 **영구 skip 등록**, midpoint < 0.85이면 이번 사이클만 skip.
9. 수량 = `buy_amount_usdc / midpoint` ≥ 5주.
10. 주문: midpoint를 0.01 tick으로 반올림([0.01, 0.99] clamp)한 가격의 **GTC limit BUY**. 응답에 `success` 또는 `orderID`가 있으면 즉시 `HOLDING`으로 DB 기록 (체결 확인 없음). `entry_reason`, `short/long_momentum_at_buy` 저장.

### 2.5 매도 파이프라인 (보유 포지션마다 매 사이클, 우선순위 순)

1. **threshold**: CLOB midpoint ≥ `sell_threshold`(0.97) → 매도.
2. (momentum 활성 시 `get_exit_signal`) **stop_loss**: `(현재가-진입가)/진입가 <= -0.10` → 매도.
3. **take_profit**: `>= +0.07` → 매도.
4. **dead_cross**: `short - long <= -0.02` (스냅샷 충분할 때만) → 매도.
5. momentum 비활성 시에는 2·3만 검사.

매도 주문도 현재 midpoint 가격 GTC limit SELL이며, 접수 즉시 `COMPLETED` + `realized_pnl = (매도가-매수가)×수량` 기록. `exit_reason`, `short/long_momentum_at_sell` 저장.

## 3. 이용하는 대중 심리

- **정보 확산 지연 / underreaction**: 사실상 결정된 사건도 대중 전체가 인지·베팅하기까지 시간이 걸려 85~97% 구간에서 100%로 서서히 수렴한다는 가정(원 PRD의 핵심 가정). 15분 momentum이 6시간 추세를 앞지르는 순간 = "새 정보가 지금 가격에 반영되는 중"으로 보고 그 물결에 편승한다.
- **favorite-longshot bias의 역이용**: 대중은 낮은 확률(longshot)에 과대 베팅하고 고확률(favorite)을 상대적으로 저평가하는 경향이 있어, 고확률 outcome을 사는 쪽이 기대값상 유리하다는 전제.
- **군집 행동(herding) 편승**: golden cross 진입은 상승 cascade 초입에 올라타고, dead cross 청산은 반대 방향 herding이 시작되면 빠지겠다는 설계. 정적 임계값 전략이 당하던 "고점 매수 후 뉴스 반전" 손실을 momentum 반전 감지로 방어한다.

## 4. 아키텍처 요약

```
main.py (루트, src path 주입) → src/polybot/main.py (argparse CLI: run/status/config)
  → config.py  : .env 로드(dotenv), config.yaml 파싱, env>yaml>기본값 병합 → BotConfig
  → bot.py     : PolymarketBot 오케스트레이터 — 1회 사이클 실행 후 종료(Jenkins 5분 cron 전제)
      Phase 0: scanner.save_market_snapshots()   (Gamma 전량 → market_snapshots INSERT)
      Phase 1: 보유 포지션별 trader.execute_sell() (CLOB midpoint → 4중 청산 판정)
      Phase 2: scanner.scan_buy_candidates()      (Gamma 전량 재조회 → 필터 → momentum 판정)
      Phase 3: 후보별 trader.execute_buy()        (midpoint 재검증 → GTC limit BUY)
      Phase 4: repo.cleanup_old_snapshots(7일)
  api/gamma_client.py   : Gamma API (시장 메타데이터, 무인증, 페이지네이션+422 종료 처리)
  api/clob_client.py    : py-clob-client-v2 wrapper (lazy init, L1/L2 인증, tick 반올림, simulation mode)
  api/data_api_client.py: Data API (/positions, /activity, /trades) — 사이클에서는 미사용
  strategy/momentum.py  : MomentumCalculator (진입/청산 시그널의 단일 소스)
  strategy/scanner.py   : 시장 스캔 + 스냅샷 저장 + momentum 분석 로그
  strategy/trader.py    : 주문 실행 + Trade 기록
  strategy/filters.py   : 스포츠/유동성/확률 구간 필터
  db/models.py          : SQLAlchemy — trades, market_snapshots, skipped_markets
  db/repository.py      : TradeRepository (CRUD + 스냅샷 조회/정리 + 통계)
  notifications/slack_notifier.py : Slack webhook 리포트 — 사이클에서는 미사용
  utils/retry.py        : rate_limit_handler (429 Retry-After, 5xx/연결오류 지수 backoff)
  utils/logger.py       : data/{job}/logs/YYYYMMDD.log + console
데이터: data/{job_name}/trades.db (simulation은 trades_sim.db), job별 완전 분리
```

의존성: Python ≥ 3.11, `py-clob-client-v2`, `SQLAlchemy 2`, `requests`, `PyYAML`, `python-dotenv` (uv 관리, `uv run polybot`).

## 5. Env var 표

| env var | 필수 | 기본값 | 효과 |
|---|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | O | - | 지갑 private key (0x prefix 자동 제거). 없으면 즉시 종료 |
| `POLYMARKET_FUNDER_ADDRESS` | O | - | Polymarket 지갑 주소 (funder). 없으면 즉시 종료 |
| `POLYBOT_BUY_THRESHOLD` | X | 0.85 | 매수 최소 확률 override |
| `POLYBOT_SELL_THRESHOLD` | X | 0.97 | 매도/매수상한 확률 override |
| `POLYBOT_BUY_AMOUNT` | X | 10.0 (yaml: 5.0) | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | X | 50000 | 유동성 필터 |
| `POLYBOT_MAX_POSITIONS` | X | -1 | 동시 포지션 상한 |
| `POLYBOT_TAKE_PROFIT` | X | 0.07 | 이익실현 % |
| `POLYBOT_STOP_LOSS` | X | -0.10 | 손절 % |
| `POLYBOT_MOMENTUM_ENABLED` | X | true | momentum 필터 on/off |
| `POLYBOT_MOMENTUM_SHORT_WINDOW` | X | 3 | 단기 윈도우 |
| `POLYBOT_MOMENTUM_LONG_WINDOW` | X | 72 | 장기 윈도우 |
| `POLYBOT_GOLDEN_CROSS_THRESHOLD` | X | 0.02 | 진입 임계값 |
| `POLYBOT_DEAD_CROSS_THRESHOLD` | X | -0.02 | 청산 임계값 |
| `POLYBOT_REQUIRE_POSITIVE_LONG_MOMENTUM` | X | true | 하락 추세 골든크로스 진입 방지 |
| `SLACK_WEBHOOK_URL` | X | - | SlackNotifier용 (현재 사이클에 미연결) |

## 6. 강점

- **전략과 인프라의 분리**: 진입/청산 판단이 `momentum.py` 한 곳에 있어 sibling 봇에서 이 파일만 교체하면 된다 (golden-cherry가 실제로 이 방식으로 파생됨).
- **자체 스냅샷 축적**: 외부 이력 API 의존 없이 5분 해상도 확률 시계열을 확보. `entry_reason`/`exit_reason`/`*_momentum_at_buy(sell)` 컬럼으로 사후 전략 분석(README의 SQL 쿼리)이 가능하다.
- **다중 청산 조건**: 구버전의 "매도 조건 하나, 손절 없음" 문제를 threshold/take_profit/stop_loss/dead_cross 4중으로 해결.
- **운영 방어 로직**: 429 Retry-After 존중, 422 페이지네이션 종료 처리, tick 반올림 + [0.01,0.99] clamp, midpoint 재검증(rapid_jump skip), 최소 5주 검증, job별 DB/로그 격리, simulation mode 전 구간 지원.
- **가시성**: 매 사이클 momentum 분석 요약 표(진입/제외 사유, 단기/장기/차이, 스냅샷 수)를 로그로 남겨 파라미터 튜닝 근거가 된다.

## 7. 약점 / 허점 (구체적)

1. **의도한 시그널(golden cross)이 사실상 발화 불가능**: 임계값 0.02는 "스냅샷당 +2%p" 기울기 차이인데, 단기 윈도우 3개 기준 15분에 약 6%p 이상 급등해야 한다. 85~97% 구간에서 이런 움직임은 극히 드물며, 실로그(20260205)의 diff는 ±0.007 이하다. **실제 매수는 전부 cold-start fallback(`short_momentum_positive`) 경로**로 발생했다.
2. **cold-start 무신호 기간**: 관대한 fallback은 제거됐다. DB 초기화·신규 시장에서는 장기 72개와 시간 커버리지가 찰 때까지 진입이 없어 안전하지만 기회를 놓친다.
3. **고정 5분 주기 가정**: timestamp 커버리지는 검증하지만 기대 간격은 5분 상수다. Jenkins 주기를 바꾸면 코드 상수도 함께 조정해야 한다.
4. **체결 가정 오류**: GTC limit 주문을 접수 즉시 HOLDING/COMPLETED로 기록한다. 미체결 주문 추적·취소가 없어 (a) 매수 미체결이면 유령 포지션, (b) **손절 매도 미체결이면 DB상 손절 완료지만 실제로는 계속 보유** — 급락장에서 stop loss가 보장되지 않는다.
5. **해결(resolved)된 시장의 포지션이 DB에 영구 잔류**: 시장 해결 후 orderbook이 사라지면 `get_midpoint`가 404로 실패하고 `execute_sell`이 영원히 False를 반환한다. 온체인에서는 redeem되어도 DB `realized_pnl`에는 반영되지 않아 통계가 왜곡된다.
6. **take_profit 도달 불가 구간**: 0.94 이상에서 진입하면 +7% 목표가 1.0을 넘어 take_profit이 수학적으로 불가능하다. threshold(0.97)나 dead cross에만 의존하게 된다. 반대로 0.97 정확히에서 진입하면 다음 사이클에 threshold 매도로 즉시 청산되는 churn 엣지도 있다.
7. **스포츠 키워드 필터 과잉 차단**: "game", "match", "win the", "beat", "score" 같은 일반 단어가 포함되어 비스포츠 시장(예: 수상·선거 관련 "win the" 포함 질문)까지 배제한다. 기회비용이 크다.
8. **momentum 계산의 통계적 취약성**: 양 끝점 2개 값만 사용(중간 노이즈 무시가 아니라 끝점 노이즈에 민감)하고, 분모가 구간 수(n-1)가 아닌 개수(n)라 기울기를 (n-1)/n만큼 과소평가한다.
9. **영구 1회 거래 제한**: `condition_id`당 평생 1회. `rapid_jump`로 한 번 skip되면 그 시장은 영구 차단된다. 수개월짜리 시장에서 정당한 재진입 기회를 모두 버린다.
10. **자금 관리 부재**: `max_positions=-1` + 고정 5 USDC. 잔고 확인, 일일 매수 한도, 변동성 기반 sizing이 없다. 시장이 요동치는 날 무제한으로 포지션을 늘릴 수 있다.
11. **Gamma 전량 조회 2회/사이클**: Phase 0과 Phase 2가 같은 `get_all_tradable_markets`를 중복 호출한다 (API 비용 2배).
12. **legacy simulation 안전성**: `scripts/simulate.py`도 config 로드 시 `simulation_mode=True`를 전달하고 `trades_sim.db`가 아니면 중단한다.
13. **미사용/불일치 코드**: `DataAPIClient`·`SlackNotifier`는 사이클에 연결되지 않은 dead code이고, data_api_client의 User-Agent가 `GoldenApple-PolyBot/1.0`으로 남아 있다. `excluded_categories`는 env override가 없다.
14. **시뮬레이션의 낙관 편향**: sim 체결이 midpoint 기준 100% 체결 가정 — spread·슬리피지·수수료가 모델링되지 않아 sim P&L이 실거래보다 항상 좋게 나온다.

## 8. 개선 아이디어

1. **임계값 재보정 또는 단위 변경**: golden cross 임계값을 "스냅샷당 기울기"가 아니라 "윈도우 전체 변화량(%p)"으로 재정의하고, 백테스트로 0.005~0.01 수준부터 탐색한다. 현행 0.02는 사실상 전략을 비활성화시킨다.
2. **cold-start 백필(후속)**: fallback 제거와 엄격한 최소 개수는 적용됐다. 필요하면 CLOB `/prices-history`로 72개 장기 윈도우를 안전하게 백필한다.
3. **주기 설정화(후속)**: 90% timestamp 커버리지 검증은 적용됐다. 현재 5분 상수를 Jenkins 주기 설정과 연결한다.
4. **주문 lifecycle 관리**: 주문 후 `get_open_orders`로 체결 확인 → 미체결이면 다음 사이클에 재가격/취소. 매수는 `PENDING_BUY`, 매도는 `PENDING_SELL` 상태를 실제로 활용한다(모델에 이미 정의돼 있으나 미사용).
5. **해결 시장 정리 로직**: Gamma `closed`/`umaResolutionStatus`를 조회해 해결된 보유 포지션을 `COMPLETED`(결과에 따라 pnl=±)로 정산하는 Phase를 추가한다.
6. **진입 상한 분리**: 매수 상한을 `sell_threshold`와 분리(예: 0.94)해 take_profit 불가 구간 진입과 0.97 churn을 막는다.
7. **스포츠 필터 정밀화**: 일반 단어 키워드를 제거하고 Gamma tags 기반 판정을 우선시. 오탐 로그를 남겨 주기적으로 검수한다.
8. **자금 관리**: 잔고 조회(DataAPIClient 활용) 기반 일일 매수 한도, Kelly 축소판 같은 확률 기반 sizing, `max_positions` 유한값 기본화.
9. **momentum 계산 개선**: 끝점 비교 대신 최소자승 기울기 또는 진짜 SMA(단기 MA vs 장기 MA) cross로 교체하고 분모를 (n-1)로 수정한다.
10. **운영 효율**: 사이클당 Gamma 조회 1회로 통합(스냅샷 저장과 스캔이 같은 응답 공유), Slack notifier를 매수/매도/에러 이벤트에 연결.
