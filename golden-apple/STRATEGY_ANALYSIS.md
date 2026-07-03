# Golden Apple 전략 분석 (STRATEGY_ANALYSIS)

> 분석 대상: `golden-apple/` (Polymarket 자동매매 봇, "80% 매수 / 90% 매도" 전략)
> 분석 기준: `src/polybot/` 전체 코드, `config.yaml`, `docs/prd.txt`, `data/default/logs/*.log` (2026-02 실행 로그)

---

## 1. 전략 개요

Golden Apple은 Polymarket의 바이너리 예측시장에서 **확률(가격)이 80% 이상 90% 미만인 outcome(Yes 또는 No 중 높은 쪽)을 매수하고, 90% 도달 시 매도**하여 거래당 약 10%의 확정 수익을 노리는 단순 threshold 전략 봇이다. 핵심 가정(`docs/prd.txt`)은 "정보의 확산 속도로 인해 80%를 돌파한 예측은 언젠가 90%에 도달한다"는 것과 "시간이 지날수록 시장 확률이 실제 사건 확률로 수렴한다"는 것이다. 손절(stop loss)은 의도적으로 없고, 한 번 거래(또는 급등으로 skip)한 시장은 영구히 재거래하지 않으며, 랜덤성이 큰 스포츠 시장은 전면 제외한다. Jenkins가 5분 주기로 `polybot run`을 실행하는 stateless one-shot 구조이고, 상태는 job별 로컬 SQLite(`data/{job}/trades.db`)에만 저장된다. 동일 코드베이스를 상수만 달리한 2개 Jenkins job으로 돌려 대시보드의 `GOLDEN-APPLE (1)`, `(2)` 계정을 운영한다.

---

## 2. 매수/매도 조건 정밀 명세

### 2.1 파라미터 표 (우선순위: env var > config.yaml > 코드 기본값)

| 파라미터 | env var | config.yaml 현재값 | 코드 기본값 | 의미 |
|---|---|---|---|---|
| `buy_threshold` | `POLYBOT_BUY_THRESHOLD` | 0.80 | 0.80 | 매수 최소 확률 |
| `sell_threshold` | `POLYBOT_SELL_THRESHOLD` | 0.90 | 0.90 | 매도 트리거 확률 = 매수 상한(미만) |
| `buy_amount_usdc` | `POLYBOT_BUY_AMOUNT` | 5.0 | 10.0 | 1회 매수 금액(USDC) |
| `min_liquidity` | `POLYBOT_MIN_LIQUIDITY` | 50,000 | 100,000 | Gamma `liquidity` 필드 최소값($) |
| `min_volume` | `POLYBOT_MIN_VOLUME` | 100,000 | 0 (비활성) | Gamma `volume`(누적) 최소값($) |
| `max_positions` | `POLYBOT_MAX_POSITIONS` | -1 (무제한) | -1 | 동시 HOLDING 수 상한 |
| `excluded_categories` | (env 없음, yaml만) | Sports/NFL/NBA 등 10종 | 동일 | 제외 카테고리(태그+키워드) |
| `simulation_mode` | (CLI `--simulate`가 override) | false | false | true면 주문 미전송, `trades_sim.db` 사용 |
| `MIN_ORDER_SIZE` | 하드코딩 (`trader.py`) | - | 5.0 (주) | Polymarket 최소 주문 수량 |
| tick size | 하드코딩 (`clob_client.py`) | - | 0.01 | 가격 반올림 단위, [0.01, 0.99]로 clamp |

### 2.2 매수 파이프라인 (`run_cycle` Phase 2→3)

1. **시장 수집**: Gamma API `GET /markets?active=true&closed=false&limit=100&offset=N` 페이지네이션(offset 최대 5,000, 초과 시 경고 후 중단. offset>0에서 HTTP 422는 정상 종료로 처리).
2. **1차 필터** (`scanner.scan_buy_candidates`, Gamma 데이터 기준):
   - `conditionId` 없으면 제외.
   - **스포츠/카테고리 필터** (`filters.is_sports_market`): tags(slug/label)가 `excluded_categories`에 있거나, question+slug 텍스트에 excluded_categories 또는 하드코딩된 `SPORTS_KEYWORDS`(약 60개: "nba", "match", "game", "win the", "beat", "score" 등)가 포함되면 제외.
   - `liquidity >= min_liquidity`, `volume >= min_volume` (Gamma 응답 필드, API 필터 + 클라이언트 이중 체크).
   - **고확률 outcome 선택**: `outcomePrices[0]`(Yes) vs `[1]`(No) 중 큰 쪽. 해당 `clobTokenIds` 토큰 선택.
   - **확률 범위**: `buy_threshold <= probability < sell_threshold` (즉 0.80 ≤ p < 0.90). 90% 이상은 후보 제외(급등장 진입 방지).
3. **중복 체크** (`repo.is_already_traded`): `trades` 또는 `skipped_markets`에 해당 `condition_id`가 있으면 skip. → "한 번 거래한 시장은 재거래 금지" 구현.
4. **매수 직전 재검증** (`trader.execute_buy`, CLOB 실시간 가격 기준):
   - `max_positions > 0`이면 현재 HOLDING 수와 비교.
   - CLOB `GET /midpoint?token_id=...`로 현재가 재조회 (조회 실패 시 skip).
   - `current_price >= sell_threshold` → **rapid_jump**: `skipped_markets`에 기록하고 **영구 제외**.
   - `current_price < buy_threshold` → skip (기록 없음, 다음 사이클 재도전 가능).
   - `shares = buy_amount_usdc / current_price`; `shares < 5.0`이면 skip (최소 주문 수량 미달).
5. **주문**: **limit order GTC**를 midpoint 가격(0.01 tick 반올림)으로 제출 (`create_order` + `post_order(OrderType.GTC)`).
6. **기록**: 응답에 `success` 또는 `orderID`가 있으면 즉시 `status=HOLDING`으로 DB 기록 (체결 확인 없음).

### 2.3 매도 파이프라인 (`run_cycle` Phase 1 — 매수보다 먼저 실행)

1. `status=HOLDING`인 모든 trade에 대해 CLOB midpoint 조회.
2. `current_price >= sell_threshold` (0.90) → 보유 수량 전량을 midpoint 가격 limit SELL GTC 제출.
3. 응답에 `success`/`orderID` 있으면 `realized_pnl = (sell_price - buy_price) * shares` 계산 후 `status=COMPLETED`.
4. `current_price < 0.90`이면 무조건 보유 유지. **손절, 시간 기반 청산, 만기 처리 로직 없음.**

### 2.4 상태 기계 요약

```
(스캔) --[0.80<=p<0.90 & 필터 통과]--> HOLDING --[p>=0.90]--> COMPLETED
(스캔) --[매수 직전 p>=0.90]--> SKIPPED(rapid_jump, 영구 제외)
HOLDING --[p 하락/만기]--> HOLDING (영원히; redeem 로직 없음)
```
`PENDING_BUY` / `PENDING_SELL` 상태는 enum에 정의만 되어 있고 실제로 사용되지 않는다.

---

## 3. 이용하는 대중 심리

- **Favorite-longshot bias의 역이용**: 베팅 시장에서 대중은 longshot(저확률)을 과대평가하고 favorite(고확률)을 과소평가하는 경향이 학술적으로 잘 알려져 있다. 80~90% 구간의 가격이 실제 확률보다 낮게 형성되어 있다면, favorite 매수는 양(+)의 기대값을 가진다.
- **정보 확산의 관성(momentum) / herding**: 확률이 80%에 도달했다는 것은 컨센서스가 이미 한쪽으로 기울었다는 신호다. 이후 뉴스 확산과 추종 매수(herding)로 컨센서스가 강화되어 90%까지 밀려 올라간다는 가정. PRD가 명시한 핵심 가정이다.
- **해결 수렴(resolution convergence)**: 시간이 지날수록 시장 가격은 실제 사건 확률(대개 0 또는 1)로 수렴한다. 고확률 쪽을 들고 있으면 수렴의 방향이 유리할 확률이 높다.
- **군중의 과잉확신을 사지 않는 안전장치**: 90%를 이미 넘긴 시장(급등)은 사지 않음으로써, "이미 다 오른" 뉴스에 뒤늦게 올라타는 FOMO성 진입을 배제한다.
- **스포츠 배제**: 스포츠는 대중 참여가 많고 결과 랜덤성이 커서(단판 경기 분산) 위 편향들이 잘 작동하지 않는다는 판단.

---

## 4. 아키텍처 요약

```
main.py (루트 셸) → src/polybot/main.py (argparse CLI: run/status/config)
  → config.py   : .env + config.yaml + env var 병합 → BotConfig (dataclass)
  → bot.py      : PolymarketBot 오케스트레이터. run() = 단일 사이클
                  Phase 1 매도 → Phase 2 스캔 → Phase 3 매수 → 통계 로깅
  → strategy/
      scanner.py : Gamma 전체 시장 페이지네이션 + 필터 → 매수 후보 목록
      filters.py : 스포츠/유동성/거래량/확률범위 순수 함수
      trader.py  : 매수/매도 실행 + DB 기록 (MIN_ORDER_SIZE=5)
  → api/
      gamma_client.py   : Gamma API (읽기 전용, 인증 불필요) + 422 페이지네이션 종료 처리
      clob_client.py    : py-clob-client-v2 래퍼. lazy init, L1/L2 인증(create_or_derive_api_key),
                          midpoint/limit order/취소, tick 반올림, simulation mode 분기
      data_api_client.py: Data API (positions/activity/trades) — 현재 run flow에서 미사용
  → db/
      models.py     : SQLAlchemy — trades / market_snapshots / skipped_markets
      repository.py : TradeRepository CRUD + get_stats
  → notifications/slack_notifier.py : Slack webhook 리포트 — 현재 run flow에서 미사용
  → utils/ logger.py (data/{job}/logs/YYYYMMDD.log), retry.py (429/5xx/연결오류 backoff)
```

- **실행 모델**: Jenkins cron(5분)이 프로세스를 새로 띄우는 one-shot. 봇 내부에 루프/스케줄러 없음.
- **데이터 분리**: `--job` 이름별로 `data/{job}/trades.db`(시뮬레이션은 `trades_sim.db`)와 로그 폴더 분리 → 한 코드베이스로 다계정/다설정 운영.
- **의존성**: Python >= 3.11, `py-clob-client-v2`, `SQLAlchemy 2`, `requests`, `PyYAML`, `python-dotenv` (uv로 관리).

---

## 5. 환경변수 (env var) 표

| env var | 필수 | 기본값 | 효과 |
|---|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | O | - | 지갑 private key. 없으면 즉시 종료. `0x` prefix 자동 제거 |
| `POLYMARKET_FUNDER_ADDRESS` | O | - | funder(지갑) 주소. CLOB 인증에 사용 |
| `POLYBOT_BUY_THRESHOLD` | X | 0.80 | 매수 최소 확률 (yaml보다 우선) |
| `POLYBOT_SELL_THRESHOLD` | X | 0.90 | 매도 트리거 확률 (yaml보다 우선) |
| `POLYBOT_BUY_AMOUNT` | X | 10.0 | 1회 매수 USDC (yaml 현재 5.0) |
| `POLYBOT_MIN_LIQUIDITY` | X | 100000 | 최소 유동성 필터 (yaml 현재 50000) |
| `POLYBOT_MIN_VOLUME` | X | 0 | 최소 누적 거래량 필터 (yaml 현재 100000) |
| `POLYBOT_MAX_POSITIONS` | X | -1 | 동시 포지션 상한 (-1 = 무제한) |
| `SLACK_WEBHOOK_URL` | X | - | `SlackNotifier`용. 현재 매매 플로우에서는 사용되지 않음 |

Jenkins에서 job마다 env var만 달리 주면 같은 코드로 상이한 전략 인스턴스를 운영할 수 있다 (GOLDEN-APPLE (1)/(2)의 구현 방식).

---

## 6. 강점

- **전략 파라미터 완전 외부화**: env > yaml > 기본값 3단 병합(`_get_config_value`)으로, 코드 수정 없이 Jenkins 레벨에서 임계값 조정 가능. PRD 요구사항을 정확히 충족.
- **급등 방어 + 재거래 금지**: [0.80, 0.90) 범위 강제와 rapid_jump 영구 skip으로 whipsaw성 반복 진입과 고점 추격을 구조적으로 차단.
- **매수 직전 실시간 재검증**: 스캔 시점(Gamma) 가격과 별개로 CLOB midpoint를 재조회해 stale 데이터 매수를 방지.
- **운영 인프라 견고**: rate limit(429 Retry-After 존중)·5xx·연결오류 exponential backoff, Gamma 422 페이지네이션 종료 처리, tick size 반올림 + [0.01, 0.99] clamp, "No orderbook" 404를 debug로 강등하는 등 실전에서 겪은 오류들이 코드에 반영돼 있음 (로그로 확인: 2/2 midpoint dict 파싱 버그 → 수정됨, 2/3 v1 서명 오류 → v2 마이그레이션).
- **시뮬레이션 모드의 코드 경로 동일성**: 주문 전송만 mock하고 스캔~DB 기록은 동일 경로. 별도 `trades_sim.db`로 실거래 DB 오염 없음.
- **job 단위 완전 격리**: DB/로그/설정이 job별로 분리되어 다계정 운영과 사후 분석(SQLite 쿼리)이 쉬움.

---

## 7. 약점 / 허점 (구체적)

1. **체결 확인이 전혀 없다 (가장 치명적)**: `place_limit_order` 응답에 `orderID`만 있으면 매수는 즉시 `HOLDING`, 매도는 즉시 `COMPLETED`로 기록한다. GTC limit order는 접수와 체결이 별개다. midpoint 가격 BUY limit은 best ask보다 낮아 **체결되지 않은 채 열려 있을 가능성이 높고**, 이 경우 DB상 포지션·`realized_pnl`은 허구가 된다. `PENDING_BUY`/`PENDING_SELL` 상태와 `cancel_order`, `get_open_orders`가 구현돼 있음에도 사용되지 않는다.
2. **해결(resolved)된 시장의 출구 없음**: 90% 미도달 상태로 시장이 해결되면 orderbook이 사라져 `get_midpoint`가 "No orderbook" 예외 → `execute_sell` 실패 → **영구 HOLDING**. 승리 토큰의 redeem(정산 회수) 로직이 없어 이긴 포지션의 원금+수익 회수조차 수동이다. DB P&L에도 반영되지 않는다.
3. **손절 없음 = 꼬리 리스크 그대로 노출**: 80%대 사건이 뒤집히면 -100%. 89%에 사서 90%에 파는 거래(허용됨)는 최대 이익 ~1.1%(실제 로그: buy 89.5% → sell 90.5% = +1.1%)인데 최대 손실은 -100%다. **진입 구간 상단일수록 risk/reward가 극단적으로 나빠지는 비대칭**이 방치되어 있다.
4. **스포츠 키워드 필터의 오폭(false positive)**: `SPORTS_KEYWORDS`에 "match", "game", "win the", "beat", "score", "racing", "finals", "championship"이 포함된다. 예: *"Will X win the 2026 election?"*은 "win the" 때문에, *"Will the peace deal beat expectations?"*는 "beat" 때문에 제외된다. 실제 로그에서도 정치·경제 시장이 주력인데, 이 필터가 우량 후보를 소리 없이 걸러내고 있을 가능성이 높다 (제외 로그는 debug 레벨이라 평소 보이지 않음).
5. **페이지네이션 상한 5,000 도달**: 로그에 "최대 페이지네이션 한도 도달" 경고가 실제로 찍힌다. Gamma 정렬 순서상 offset 5,000 이후의 시장은 영원히 스캔되지 않는다. `order`/`ascending` 파라미터나 `volume_num_min` 서버 필터를 쓰지 않아 전체 페이지를 순회하며 대부분을 버린다 (723개 수집에 50페이지 = 사이클당 ~50 API 호출).
6. **자본 관리 부재**: `max_positions=-1` + 후보 50개(로그 확인) + 사이클당 전 후보 매수 → 한 사이클에 잔고 소진 가능. USDC 잔고 조회가 없어 잔고 부족은 주문 실패 로그로만 발견된다. 후보 우선순위(정렬)도 없어 발견 순서대로 산다.
7. **midpoint 기준 매매의 슬리피지 왜곡**: 시뮬레이션은 midpoint 즉시 체결을 가정하므로 스프레드가 넓은 시장에서 시뮬 P&L이 체계적으로 과대평가된다. 실거래에서는 반대로 미체결(1번 문제)로 나타난다.
8. **Gamma `volume`은 누적 거래량**: 과거에만 활발했던 좀비 시장이 필터를 통과한다. `volume24hr` 같은 최근 활동성 지표를 쓰지 않는다.
9. **미실현 손익·스냅샷 부재**: `get_stats`의 `total_pnl`은 realized만 합산. `MarketSnapshot` 테이블은 정의만 있고 `save_snapshot` 호출이 없어 사후 전략 검증용 시계열 데이터가 쌓이지 않는다.
10. **rapid_jump 영구 제외의 기회비용**: 한 번 90% 위에서 목격된 시장이 이후 80~90 구간으로 되돌아와도 영영 진입하지 않는다 (PRD 의도이긴 하나, 되돌림 후 재수렴이라는 이 전략의 최적 시나리오를 스스로 배제).
11. **기타 소소한 것들**: `datetime.utcnow()` (deprecated, naive); 매수 성공 판정 `result.get("success") or result.get("orderID")`가 API 에러 형태 변화에 취약; `buy_probability`에 스캔 시점이 아닌 재조회 가격을 저장해 스캔→주문 간 가격 변화 분석 불가; `tests/`가 빈 패키지(테스트 0개).

---

## 8. 개선 아이디어

1. **주문 라이프사이클 도입**: 매수 → `PENDING_BUY` 기록 → 다음 사이클에 `get_open_orders()`로 체결 확인 → 체결 시 `HOLDING`, N사이클 미체결 시 `cancel_order` 후 재평가. 매도도 동일하게 `PENDING_SELL` 경유. (이미 정의된 enum과 API 래퍼를 쓰기만 하면 됨.)
2. **체결 가능 가격 사용**: 매수는 best ask, 매도는 best bid 기준으로 limit 가격을 잡거나 (`get_best_bid/ask` 이미 구현됨), 소액이므로 FOK market order(`place_market_buy` 이미 구현됨)로 전환해 미체결 문제를 원천 제거.
3. **해결 시장 처리**: midpoint 404가 반복되는 HOLDING은 Gamma `closed`/`umaResolutionStatus`를 확인해 `RESOLVED_WIN`/`RESOLVED_LOSS` 상태로 정산하고, 승리 토큰 redeem(CTF `redeemPositions`) 자동화 또는 최소한 Slack 알림.
4. **진입 구간 상단 축소 또는 수익률 기반 필터**: 예를 들어 `p <= 0.85`로 제한하거나 `(sell_threshold - p) / p >= 최소기대수익률` 조건을 추가해 89%→90% 같은 1% 먹기·100% 리스크 거래를 배제.
5. **스포츠 필터 정밀화**: 광범위 동사 키워드("win the", "beat", "game", "match")를 제거하고 Gamma tags 기반 필터를 우선. 제외 사유를 INFO 로그·DB에 남겨 오폭을 관측 가능하게.
6. **자본/포지션 관리**: USDC 잔고 조회 후 사이클당 매수 상한(`max_buys_per_cycle`) 및 `max_positions` 기본값을 유한하게. 후보를 유동성×기대수익으로 정렬해 상위부터 매수.
7. **서버사이드 필터 활용**: Gamma `/markets`에 `liquidity_num_min`, `volume_num_min` 파라미터를 넘겨 페이지 수를 줄이고 5,000 offset 상한 문제를 완화. `volume24hr` 기반 활동성 필터 추가.
8. **관측성 강화**: 사이클마다 `save_snapshot` 호출로 후보/보유 시장 확률 시계열 축적, 미실현 P&L을 status 출력에 포함, 매매 발생 시 Slack 알림 (`SlackNotifier` 이미 존재).
9. **테스트 추가**: `filters.py`는 순수 함수라 즉시 단위 테스트 가능 (스포츠 오폭 케이스, 확률 경계값 0.80/0.90, tick 반올림). 골든 케이스를 고정해 회귀 방지.
10. **cherry에서 역이식 검토**: `excluded_categories` 빈 리스트 시 필터 전체 해제, 월별 CSV export, snapshot cleanup 등 golden-cherry에 이미 있는 운영 편의 기능.

---

## 9. 참고: golden-cherry와의 차이 (동일 골격의 sibling)

| 항목 | golden-apple | golden-cherry |
|---|---|---|
| 진입 | 0.80 ≤ p < 0.90 | 0.75 ≤ p ≤ 0.92 (**상한 포함**) + **해결까지 4~24h인 시장만** |
| 청산 | p ≥ 0.90 단일 조건 | stop_loss -8% / take_profit +15% / trailing_stop 고점 대비 -5% / 해결 4h 전 time_exit (4중) |
| DB | 기본 스키마 | `entry_reason`, `exit_reason`, `max_price`, `market_end_date` 등 컬럼 추가 + 월별 CSV export |
| 옵션 | `min_volume` 필터 | `yes_only_mode`(`--yes-only`, `POLYBOT_YES_ONLY`), trailing/time 설정 블록 |
| 기타 | gamma_client가 리팩토링됨(422 처리) | scanner에 시간 계산·스캔 요약 로깅, 빈 excluded_categories 시 필터 해제 |

apple은 "손절 없는 buy-and-hold-to-target", cherry는 "해결 직전 모멘텀 + 적극적 리스크 관리"로 철학이 다르다.
