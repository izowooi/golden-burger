# Golden Honeydew — Night Watch 전략

## 1. 한 줄 요약

**한산 시간대(미 동부 새벽·주말)에 뉴스 없이 발생한 가격 이탈(dislocation)을 복원 방향으로 매수한다.**
노리는 심리 편향: **주의(attention)의 희소성** — 시장 참여자 대다수가 자는 시간에는 호가가 얇아져
소액 주문만으로도 가격이 밀리고, 아침에 주의가 돌아오면 원래 수준으로 복원된다.

## 2. 왜 이 전략인가

### 심리학적 논리

- Polymarket 참여자 대다수는 미국 시간대의 인간이다. UTC 06:00-13:00(미 동부 01-08시)와 주말에는
  orderbook이 얇아지고, 정보가 아닌 유동성 요인만으로 가격이 이탈한다.
- 주의가 없는 시간의 가격 변화는 "정보"가 아니라 "노이즈"일 확률이 높다. 노이즈는 평균으로 회귀한다.
- 반대로 거래량이 급증하며 움직이는 가격은 진짜 뉴스일 가능성이 크므로 **거래량 급증 시장은 배제**한다.

### 레포 리서치 근거

- `golden-cherry/docs/polymarket-strategy-momentum.md` 계열 리서치: "10%+ 급변은 mean-revert" —
  단기 과잉 이동의 회귀 성향은 이 레포에서 반복적으로 확인된 관찰이다. Night Watch는 그 회귀를
  "정보가 없는 시간대"로 한정해 신호 대 잡음비를 높인다.

### 구조적 우위

- 사용자의 봇은 Jenkins로 24/7, 3-5분 간격으로 돈다. **사람은 이 시간대에 깨어있을 수 없다** —
  이 전략은 그 인프라 우위를 정면으로 수익화한다.

### 기존 봇에서 배운 것 (STRATEGY_ANALYSIS.md 반영)

| 기존 문제 | Night Watch의 수정 |
|---|---|
| banana: 스냅샷 "개수" 기반 윈도우 → Jenkins 중단 시 왜곡 | timestamp 기반 윈도우 + 커버리지 검증 (`is_window_valid`) |
| banana: cold-start 폴백이 과도하게 관대 → 사실상 무전략 | 윈도우 invalid면 히스토리 백필 시도, 그래도 invalid면 **진입 안 함** |
| cherry/banana: condition_id당 영구 1회 거래 | 재진입 쿨다운(기본 24h)으로 대체 |
| cherry: 해결된 시장이 영구 HOLDING 좀비 포지션 | midpoint 실패 + endDate 24h 경과 시 `EXPIRED` 마감 + 수동 redeem 경고 |
| banana: take_profit 목표가 1.0 초과로 도달 불가 | 목표가 0.99 캡 |
| cherry: LOG_LEVEL env 무시 | `LOG_LEVEL` env 지원 (`--verbose`가 최우선) |
| banana: Gamma 전량 sweep 2회/사이클 | sweep 1회, Phase 0/2 공유 |
| banana: 트레일링 스탑이 손절을 사실상 대체 (whipsaw) | 빠른 회전 전략이므로 trailing 자체를 제거 |

## 3. 진입/청산 규칙 정밀 명세

### 진입 (모두 충족해야 매수)

| # | 조건 | 구현 |
|---|------|------|
| 1 | 한산 시간대 | UTC `quiet.hours_utc`(기본 06-13시, `[start, end)`) 또는 주말(토/일 UTC, `quiet.weekends=true`일 때). 사이클당 1회 판정 — 아니면 스캔 전체 skip |
| 2 | 유동성/시간 | liquidity >= $15,000, 해결까지 >= 24h. (`min_volume_24h`는 기본 0=비활성) |
| 3 | 편차 | 24h 스냅샷(YES 가격)의 **median**(`statistics.median`) 대비 \|현재 YES 가격 - median\| >= 0.05 |
| 4 | 뉴스 배제 | 최근 3h 스냅샷의 volume24hr 평균 < 24h 윈도우 평균 × 1.5 (급증 = 진짜 뉴스 → skip) |
| 5 | 복원 방향 | dev < 0 → **YES 매수**(반등 기대), dev > 0 → **NO 매수**(페이드, 매수가는 실제 NO 가격 `outcomePrices[1]` 기준) |
| 6 | 가격 밴드 | 매수할 토큰 가격 ∈ [0.30, 0.90] (양끝 포함) |
| 7 | 윈도우 유효성 | 포인트 >= 5개 AND 커버 시간 >= lookback의 50%. 부족하면 `prices-history` 백필 후 재평가, 그래도 부족하면 진입 안 함 |
| 8 | 재진입 쿨다운 | HOLDING 존재, 24h 내 COMPLETED 청산, 24h 내 skip 기록 중 하나라도 있으면 skip |

매수 실행 직전 CLOB midpoint로 재검증: 가격이 밴드 상한(0.90) 초과면 `rapid_jump`로 skip 기록
(쿨다운 동안만 차단 — 기존 봇의 영구 밴 아님), 하한(0.30) 미만이면 기록 없이 skip.

### 청산 (우선순위 순, 매 사이클 검사)

| 순위 | 조건 | 파라미터 | exit_reason |
|------|------|---------|-------------|
| 1 | P&L <= -6% | `stop_loss_percent=-0.06` | `stop_loss` |
| 2 | 현재가 >= min(매수가×1.06, **0.99**) | `take_profit_percent=0.06` | `take_profit` |
| 3 | 보유 >= 24h (복원 실패 → 회전) | `max_holding_hours=24` | `max_holding` |
| 4 | 해결까지 < 12h | `exit_hours=12` | `time_exit` |
| - | midpoint 조회 실패 + endDate 24h 경과 | - | `resolved_unredeemed` (status=EXPIRED, 수동 redeem 필요) |

**trailing stop 없음** — 목표 수익폭(+6%)이 작은 빠른 회전 전략이라 trailing이 손절을 대체해버리는
cherry/banana의 whipsaw 문제를 원천 차단한다.

## 4. 파라미터·env var 표

우선순위: **환경변수 > config.yaml > 코드 기본값**

| env | yaml 키 | 기본값 | 의미 |
|---|---|---|---|
| `POLYBOT_QUIET_HOURS_UTC` | `trading.quiet.hours_utc` | `"6-13"` | 진입 허용 UTC 시간대. 자정 넘는 `"22-4"` 지원 |
| `POLYBOT_QUIET_WEEKENDS` | `trading.quiet.weekends` | `true` | 주말(토/일 UTC) 전체 진입 허용 |
| `POLYBOT_MEDIAN_LOOKBACK_HOURS` | `trading.signal.median_lookback_hours` | 24 | median 계산 윈도우 (시간) |
| `POLYBOT_DEV_MIN` | `trading.signal.dev_min` | 0.05 | 최소 편차 \|현재가 - median\| |
| `POLYBOT_VOL_SPIKE_BLOCK` | `trading.signal.vol_spike_block` | 1.5 | 거래량 급증 차단 배수 |
| `POLYBOT_ENTRY_PROB_MIN` | `trading.signal.entry_prob_min` | 0.30 | 매수 토큰 가격 하한 |
| `POLYBOT_ENTRY_PROB_MAX` | `trading.signal.entry_prob_max` | 0.90 | 매수 토큰 가격 상한 |
| `POLYBOT_ENTRY_HOURS_MIN` | `trading.time_based.entry_hours_min` | 24 | 해결까지 최소 잔여 시간 |
| `POLYBOT_MAX_HOLDING_HOURS` | `trading.time_based.max_holding_hours` | 24 | 최대 보유 시간 |
| `POLYBOT_EXIT_HOURS` | `trading.time_based.exit_hours` | 12 | 해결 N시간 전 청산 |
| `POLYBOT_TAKE_PROFIT` | `trading.take_profit_percent` | 0.06 | 익절 (+6%, 목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | `trading.stop_loss_percent` | -0.06 | 손절 (-6%) |
| `POLYBOT_BUY_AMOUNT` | `trading.buy_amount_usdc` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | `trading.min_liquidity` | 15000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | `trading.min_volume_24h` | 0 | 최소 24h 거래량 $ (0=비활성) |
| `POLYBOT_MAX_POSITIONS` | `trading.max_positions` | -1 | 최대 동시 포지션 (-1=무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | `trading.reentry_cooldown_hours` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | `trading.history_backfill` | true | prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | `trading.excluded_categories` | `""` | 제외 카테고리 (comma 구분, 기본 비활성) |
| `LOG_LEVEL` | - | INFO | 로그 레벨 (`--verbose`가 최우선) |

## 5. 이 전략이 실패하는 경우 (리스크)

1. **"조용한 이탈"이 사실은 느린 정보 반영일 때**: 거래량 급증 없이 스마트머니가 조용히 포지션을
   쌓는 경우, 봇은 노이즈로 오판해 역방향(복원 방향)을 산다. 손절 -6%가 유일한 방어선이다.
2. **한산 시간대의 낮은 유동성 = 체결 리스크**: 이탈을 발견한 바로 그 이유(얇은 호가) 때문에
   GTC limit 주문이 미체결될 수 있고, midpoint 기준 P&L이 스프레드만큼 과대평가된다.
3. **median이 이미 오염된 경우**: 24h 윈도우 안에 큰 추세 변화가 있으면 median 자체가 "옛날 가격"이라
   복원 목표가 잘못 설정된다. 추세장에서는 연속 손절이 날 수 있다.
4. **주말 = 한산이라는 가정의 예외**: 주말에 해결되는 이벤트(스포츠·선거 개표 등)는 주말이 오히려
   피크 시간대다. 거래량 급증 필터가 1차 방어지만 완벽하지 않다.
5. **+6%/-6% 대칭 손익 구조**: 승률이 50%를 유의하게 넘지 못하면 스프레드·미체결 비용 때문에
   기대값이 음수가 된다. 승률 자체가 전략의 전부다.
6. **시간대 가정의 취약성**: Polymarket 참여자 구성이 글로벌화되면 "미 동부 새벽 = 한산" 가정이
   약해진다. `POLYBOT_QUIET_HOURS_UTC`로 조정 가능하지만 가정 자체는 검증 대상이다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1-2주)**: `uv run python main.py run --simulate --job sim-test`를 Jenkins에 3-5분
   간격으로 등록. `data/sim-test/trades_sim.db`에 기록 축적.
2. **소액 실전 (4주)**: `POLYBOT_BUY_AMOUNT=5` 수준으로 실전 전환. 다른 골든 봇들과 동일한
   대시보드 계정 체계(`GOLDEN-HONEYDEW`)로 잔고 추적.
3. **판단 기준**: 4주 경과 + **30건 이상 COMPLETED 거래** 축적 후:
   - 승률 >= 55% AND 평균 손익 > 0 → 금액 증액
   - 승률 50-55% → 파라미터 조정 후 4주 재검증 (아래 베리에이션)
   - 승률 < 50% 또는 총손익 < -10% → 중단
   - `exit_reason`별 분포 확인: `max_holding` 비중이 50%를 넘으면 복원 가설 자체가 약한 것.
   - CSV의 `deviation_at_buy` 버킷별(0.05-0.07 / 0.07-0.10 / 0.10+) 승률 비교로 dev_min 조정.
   - `deviation_at_exit`(청산 시점 median 대비 편차)로 복원 완료/미완료 청산 구분.
   - 교차 봇 비교는 trades의 `strategy_name`("honeydew") / `mode`("live"/"sim") /
     `volume_24h_at_buy`로 UNION 쿼리 (A/B 포스트모템 공통 계약).

## 7. 베리에이션 아이디어

| 버전 | 변경 env | 가설 |
|---|---|---|
| A-1 (엄격) | `POLYBOT_DEV_MIN=0.08`, `POLYBOT_VOL_SPIKE_BLOCK=1.3` | 더 큰 이탈 + 더 민감한 뉴스 필터 → 승률 상승, 빈도 하락 |
| A-2 (심야 집중) | `POLYBOT_QUIET_HOURS_UTC="7-11"`, `POLYBOT_QUIET_WEEKENDS=false` | 가장 한산한 코어 시간만 → 가설의 순수 검증 |
| A-3 (회전 가속) | `POLYBOT_TAKE_PROFIT=0.04`, `POLYBOT_MAX_HOLDING_HOURS=12` | 작은 복원만 먹고 빠르게 회전 → 체결 실패 리스크 감소 |

## 8. 알려진 구현 한계

1. **GTC limit @ midpoint 체결 가정**: 주문 접수 즉시 HOLDING/COMPLETED로 기록한다. 미체결·부분
   체결 시 DB와 지갑이 어긋난다. cherry와의 A/B 비교 가능성을 위해 의도적으로 유지한 패턴이다
   (§3.8). 특히 한산 시간대 전략이라 이 한계의 영향이 다른 봇보다 클 수 있다.
2. **스냅샷 의존 cold start**: 봇 최초 기동 시 24h median에 필요한 데이터가 없다.
   `prices-history` 백필이 1차 보완이지만, 백필 endpoint는 외부 지식 기반이라 실패할 수 있다
   (실패 시 조용히 스냅샷 축적 대기 — 최대 하루면 자연 회복).
3. **volume 급증 판정은 DB 스냅샷에만 의존**: 백필 데이터에는 volume이 없어, cold start 직후에는
   뉴스 필터의 판별력이 낮다 (volume 데이터가 전혀 없으면 급증 아님으로 처리).
4. **EXPIRED 포지션은 수동 redeem 필요**: 봇은 해결된 시장을 EXPIRED로 마감만 하고 온체인
   redeem은 하지 않는다. `status` 명령과 로그 WARNING으로 노출된다.
5. **endDate 신뢰성**: Gamma `endDate`는 실제 해결 시각과 다를 수 있다 (cherry 분석 §6.2-6와 동일).
