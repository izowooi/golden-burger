# Golden Elderberry 전략 문서 — Panic Fade

## 1. 한 줄 요약

**원래 favorite(≥70%)이던 토큰이 악재/루머에 공황 투매로 12%p 이상 급락한 뒤, 바닥 안정화가 확인되면 역매수해 과잉반응의 되돌림(+10%)을 수확한다.**

노리는 심리 편향: **손실 회피(loss aversion) + 공황 투매(panic selling)에 의한 오버슈팅**.

## 2. 왜 이 전략인가

### 심리학적 논리

- 손실 회피는 이득 대비 약 2배의 심리 가중을 갖는다 (Kahneman & Tversky의 prospect theory). 악재/루머가 터지면 보유자들은 "더 잃기 전에" 일단 던지고, 매수 대기자는 관망한다 → 가격이 펀더멘털 이하로 오버슈팅한다.
- 급락 직후는 유동성 공급자도 스프레드를 벌리는 구간이라 소량의 투매만으로도 가격이 과도하게 밀린다.
- 24/7 3-5분 간격으로 도는 봇의 구조적 우위: 사람들이 자는 새벽의 공황도 잡는다.

### 레포 리서치 문서 근거

- `golden-cherry/docs/polymarket-strategy-momentum.md` 계열 리서치의 직접 근거: **"10%+ 급변은 mean-revert"** (소폭 드리프트는 지속, 급변은 회귀). Panic Fade는 이 회귀 영역만 정조준한다.

### 기존 봇에서 배운 것 (STRATEGY_ANALYSIS 반영)

| 기존 약점 | elderberry의 수정 |
|---|---|
| banana: 스냅샷 "개수" 기반 윈도우 → Jenkins 중단 시 15분 윈도우가 몇 시간을 커버 | **timestamp 기반 윈도우** + 커버리지 검증 (`is_window_valid`), invalid면 진입 안 함 |
| banana: 관대한 cold-start 폴백으로 사실상 무전략 진입 | cold start는 **prices-history 백필**로 해결, 백필 실패 시 진입 금지 |
| cherry: rapid_jump **영구** skip | skip은 `reentry_cooldown_hours`(24h) 동안만 유효 |
| cherry: 한 번 거래한 시장 영구 재진입 금지 | HOLDING/쿨다운만 체크하는 **재진입 허용** 구조 |
| cherry: 해결된 시장이 영구 HOLDING 좀비로 남음 | midpoint 조회 실패 + 해결 24h 경과 시 **EXPIRED** 마감 + 수동 redeem 경고 |
| cherry: TP 목표가가 1.0을 넘어 도달 불가 | 목표가 **0.99 캡** |
| cherry: 트레일링 스탑이 실효 손절선을 대체(whipsaw) | 급락 매수는 진입 직후 변동이 크므로 **trailing 자체를 제거** |
| 공통: `LOG_LEVEL` env 무시 | `setup_logger`가 LOG_LEVEL을 읽음 (`--verbose`가 최우선) |

### 왜 마감이 먼(≥48h) 시장만인가

마감 직전 급락은 "진짜 정보"(사건 확정)일 확률이 높다. 해결까지 48시간 이상 남은 시장으로 한정해 정보에 의한 붕괴와 심리에 의한 오버슈팅을 분리한다. 급변 이벤트의 반대 가설(진짜 정보 + 거래량 폭증 = 편승)은 자매 봇 **golden-lime**이 검증한다 — 같은 이벤트 클래스에 대한 의도적 A/B 쌍이다.

## 3. 진입/청산 규칙 정밀 명세

### 진입 (모두 AND, favorite였던 쪽 토큰 기준 가격 p)

| # | 조건 | 값 | 구현 |
|---|------|-----|------|
| 1 | 유동성 | liquidity >= $20,000 | gamma `liquidity` |
| 2 | 거래량 | volume24hr >= $10,000 | gamma `volume24hr` |
| 3 | 잔여 시간 | hours_left >= 48h | gamma `endDate` |
| 4 | 윈도우 유효성 | 48h 윈도우에 >= 5 포인트, span >= 24h | `is_window_valid` (invalid면 백필 시도 후 재검증) |
| 5 | favorite 판별 | ref 윈도우의 YES 최고가 >= 0.5면 YES쪽, 아니면 NO쪽(1-p 환산) 평가 | `evaluate_panic_fade` |
| 6 | 기준가 | ref = 최근 48h(단 최근 3h 제외) 최고가, **ref >= 0.70** | 최근 3h 제외 = 급락 자체를 ref에 넣지 않기 위함 |
| 7 | 낙폭 | ref - p >= 0.12 | |
| 8 | 붕괴 배제 | 0.35 <= p <= 0.75 | 0.35 미만 붕괴는 진짜 정보로 간주 |
| 9 | 바닥 안정화 | 최근 45분(>= 3 스냅샷): 현재가 >= 직전 스냅샷들(최신 포인트 제외) min AND 구간 std <= 0.02 | 떨어지는 칼날 회피 (신저가 금지 — 현재가 자신은 Phase 0에서 이미 스냅샷으로 저장되므로 min 계산에서 제외) |
| 10 | 재진입 쿨다운 | HOLDING 없음 + 마지막 청산/skip 후 24h 경과 | `is_reentry_blocked` |

매수 직전 CLOB midpoint 재검증: 가격이 `current_max`를 넘었으면(반등 이미 완료) `rebound_before_entry`로 skip(쿨다운 시작), `current_min` 미만이면(붕괴 진행) 이번 사이클만 보류.

### 청산 (우선순위 순, trailing 없음)

| 순위 | 조건 | 값 | exit_reason |
|------|------|-----|-------------|
| 1 | 손절 | P&L <= -10% | `stop_loss` |
| 2 | 익절 | 현재가 >= min(진입가 x 1.10, **0.99**) | `take_profit` |
| 3 | 보유 시간 초과 | 보유 >= 48h (반등 실패) | `max_holding` |
| 4 | 시간 청산 | 해결까지 < 24h | `time_exit` |
| - | 해결 leak | midpoint 조회 실패 + 해결 24h 경과 | `resolved_unredeemed` (EXPIRED, 수동 redeem) |

## 4. 파라미터 / env var 표

우선순위: **env > config.yaml > 코드 기본값**

| env | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| `POLYBOT_TAKE_PROFIT` | 0.10 | 익절 +10% (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.10 | 손절 -10% |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 비활성) |
| `POLYBOT_REF_WINDOW_HOURS` | 48 | 기준가 산출 윈도우 |
| `POLYBOT_REF_EXCLUDE_RECENT_HOURS` | 3 | ref에서 제외할 최근 구간 |
| `POLYBOT_REF_MIN` | 0.70 | ref 최소값 (원래 favorite) |
| `POLYBOT_DROP_MIN` | 0.12 | 최소 낙폭 |
| `POLYBOT_CURRENT_MIN` | 0.35 | 진입 밴드 하한 (붕괴 배제) |
| `POLYBOT_CURRENT_MAX` | 0.75 | 진입 밴드 상한 |
| `POLYBOT_STAB_WINDOW_MINUTES` | 45 | 안정화 확인 윈도우 (분) |
| `POLYBOT_STAB_MAX_STD` | 0.02 | 안정화 최대 std |
| `POLYBOT_ENTRY_HOURS_MIN` | 48 | 진입 최소 잔여 시간 |
| `POLYBOT_MAX_HOLDING_HOURS` | 48 | 최대 보유 시간 |
| `POLYBOT_EXIT_HOURS` | 24 | 해결 이 시간 전 청산 |
| `LOG_LEVEL` | INFO | 로그 레벨 |

## 5. 이 전략이 실패하는 경우 (솔직한 리스크)

1. **급락이 진짜 정보인 경우**: 밴드 하한 0.35와 안정화 체크로 필터링해도, "천천히 확인되는 진짜 악재"는 45분 안정화를 통과한 뒤 계속 흘러내린다. 이때 -10% 손절이 유일한 방어선이다.
2. **계단식 붕괴**: 안정화 → 진입 → 2차 급락 패턴. 손절이 갭을 건너뛰면 -10%보다 크게 잃는다 (지정가 GTC 특성상 체결 미보장).
3. **낮은 유동성의 가짜 급락**: 스프레드가 넓은 시장에서 midpoint 급락은 실제 체결 가능 가격이 아닐 수 있다. liquidity/volume 필터가 1차 방어지만 완전하지 않다.
4. **반등이 느린 시장**: 48h 내 반등하지 않으면 `max_holding`으로 본전 부근 청산 → 수수료/스프레드만큼 잃는다.
5. **스냅샷 공백**: Jenkins 장기 중단 후에는 윈도우 invalid로 진입 자체가 멈춘다 (의도된 동작이지만 기회 손실). 백필이 실패하는 토큰은 데이터 축적까지 최소 24h 소요.
6. **양쪽 whipsaw 시장**: YES/NO가 번갈아 favorite이 되는 초변동 시장에서는 ref 판별(YES 최고가 >= 0.5)이 YES쪽으로 쏠려 NO쪽 기회를 놓치거나 노이즈에 진입할 수 있다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1-2주)**: `uv run python main.py run --simulate --job sim-test`를 Jenkins 3-5분 간격으로 실행. `data/sim-test/trades_sim.db` 축적.
2. **소액 실전 (4주)**: `POLYBOT_BUY_AMOUNT=1`로 시작, 문제 없으면 증액.
3. **판단 기준**: 4주 + 30건 이상 거래에서
   - 승률 >= 55% (TP:SL 1:1 구조이므로 55%가 손익분기 + 마진)
   - 평균 손익 > 0, `max_holding` 청산 비율 < 40%
   - `resolved_unredeemed`(EXPIRED) 발생 시 원인 분석 우선
4. 월별 CSV(`trades_YYYY-MM.csv`)의 `ref_price_at_buy`/`drop_at_buy`/`exit_reason`으로 낙폭 구간별 승률을 집계해 파라미터를 데이터로 조정한다.

## 7. 베리에이션 아이디어

| 안 | 변경 env | 가설 |
|---|---|---|
| A-1 (얕은 낙폭·빠른 회전) | `POLYBOT_DROP_MIN=0.08`, `POLYBOT_TAKE_PROFIT=0.06`, `POLYBOT_MAX_HOLDING_HOURS=24` | 작은 과잉반응이 더 자주, 더 빨리 복원된다 |
| A-2 (깊은 낙폭만) | `POLYBOT_DROP_MIN=0.18`, `POLYBOT_CURRENT_MIN=0.40` | 극단적 공황일수록 되돌림 폭이 크다 |
| A-3 (엄격한 안정화) | `POLYBOT_STAB_WINDOW_MINUTES=90`, `POLYBOT_STAB_MAX_STD=0.015` | 안정화를 오래 확인할수록 떨어지는 칼날 회피율이 오른다 (진입 횟수는 감소) |

## 8. 알려진 구현 한계

- **GTC limit order + 체결 가정**: midpoint에 지정가를 내고 orderID를 받으면 체결로 간주한다. 미체결/부분체결 시 DB와 지갑이 어긋난다. cherry와의 A/B 비교 가능성을 위해 의도적으로 유지한 패턴이다.
- **midpoint 기준 P&L**: 실체결은 ask/bid 근처이므로 기록 P&L은 과대평가될 수 있다.
- **스냅샷 의존 cold start**: 신규 배포 직후에는 prices-history 백필이 성공하는 시장에서만 진입 가능하다. 백필 endpoint는 비공식 지식이므로 언제든 실패할 수 있고, 실패 시 스냅샷 축적(약 24-48h)으로 자연 회복한다.
- **endDate 신뢰성**: gamma `endDate`는 실제 해결 시각과 다를 수 있다. 조기 해결 시장은 EXPIRED 처리로 좀비를 막지만 redeem은 수동이다.
- **negRisk 다후보 이벤트 상관관계**: 같은 이벤트의 여러 시장에 동시 진입할 수 있어 상관 리스크 제어가 없다.
