# Golden Lime 전략 문서 — Shock Follow (충격 뉴스 과소반응 편승)

## 1. 한 줄 요약

**"거래량 폭증을 동반하고 고점을 지키는 급변은 진짜 정보다 — 대중이 덜 반영한 나머지 구간에 편승한다."**

노리는 심리 편향: **과소반응(underreaction) + 불신("설마") + 앵커링(anchoring)**.
대형 서프라이즈가 터지면 대중은 기존 가격에 앵커링된 채 "설마 그 정도일까"라며 새 정보를 **일부만** 가격에 반영한다. 주식시장의 PEAD(Post-Earnings Announcement Drift, 실적 발표 후 드리프트)와 동일한 구조로, 충격 직후의 첫 점프는 끝이 아니라 시작인 경우가 많다.

## 2. 왜 이 전략인가

### 심리학적 논리

- **앵커링**: 보유자·관찰자 모두 "어제까지 30%였던 시장"이라는 기준점에 묶여 있다. 새 정보가 60%를 정당화해도 첫 반응은 45%에서 멈춘다.
- **불신 단계**: 충격 뉴스는 확인-확산-수용의 단계를 거친다. 뉴스를 먼저 본 소수가 가격을 밀어올리고, 대중 다수는 몇 시간 뒤에야 따라온다. 봇은 3~5분 주기로 돌기 때문에 이 확산 구간의 앞쪽에 설 수 있다.
- **거래량 = 정보의 서명**: 노이즈 스파이크(소액 주문, 오호가)는 거래량이 미약하고 고점을 못 지킨다. 진짜 정보는 거래량 폭증과 고점 유지를 동반한다. 이 둘을 게이트로 써서 노이즈를 걸러낸다.

### 레포 리서치 문서 근거

`golden-cherry/docs/polymarket-strategy-momentum.md` 계열 리서치는 "소폭(2-3%/일) 모멘텀은 +6-8% 지속, **10%+ 급변은 mean-revert**"를 보고한다. lime은 이 명제를 정면으로 받아들이되, **"10%+ 급변 전체가 회귀하는 것이 아니라, 거래량·고점 유지로 구분되는 하위 집단은 지속한다"**는 가설을 검증한다. 즉 리서치의 평균적 결론에 조건부 예외가 존재하는지를 실측하는 실험이다.

### elderberry와의 A/B 쌍 (핵심 설계)

golden-elderberry(Panic Fade)와 **의도적으로 정반대 트리거의 A/B 쌍**이다. 같은 이벤트 클래스("최근 몇 시간 내 ±0.10 이상 급변")에 대해:

| 급변의 성격 | 신호 | 담당 봇 | 액션 |
|---|---|---|---|
| **노이즈/공황** — 거래량 미약·불안정, 고점(저점) 반납 | 안정화 후 되돌림 | **elderberry** | 급락을 **역매수** (mean-revert 베팅) |
| **진짜 정보** — 거래량 폭증(24h 평균의 2배+), 고점 유지 | 점프 후 유지 | **lime** | 급변 방향에 **편승** (drift 베팅) |

두 봇의 트리거는 상호 배타적으로 설계되어 있다: elderberry는 "바닥 안정화 + 거래량 조건 없음 + 마감이 먼(>=48h) 급락"을, lime은 "거래량 2배 폭증 + 고점 유지(되돌림 <= 0.02)"를 요구한다. 4주 후 두 봇의 성적을 비교하면 **"Polymarket의 10%+ 급변은 회귀인가 지속인가, 그리고 거래량이 그 구분자인가"**라는 가설이 시장 데이터로 검증된다. 한쪽만 수익이면 그쪽 가설이 맞는 것이고, 둘 다 수익이면 거래량 필터가 유효한 구분자라는 강한 증거다.

### 기존 봇에서 배운 것 (STRATEGY_ANALYSIS 반영)

- **banana**: 개수 기반 스냅샷 윈도우는 Jenkins 중단 시 왜곡된다 → timestamp 기반 윈도우 + 유효성 검증(§3 참조). 관대한 cold-start 폴백은 전략을 "방금 안 떨어짐 매수"로 퇴화시킨다 → invalid 윈도우면 **진입 금지**, 대신 prices-history 백필로 cold start 자체를 완화.
- **cherry**: rapid_jump 영구 밴은 기회를 영영 잃는다 → 모든 skip은 timestamp 기반 쿨다운(기본 24h). 해결된 시장이 영구 HOLDING 좀비가 된다 → EXPIRED 상태 추가. TP 목표가가 1.0을 넘어 도달 불가 → 0.99 캡.

### 점프 감지 방식: "윈도우 내 최저가 대비 현재가"

점프 감지는 **최근 6h 윈도우 내 최저가(base) 대비 현재가 상승폭**으로 판정한다. "N시간 전 시점 가격 대비"가 아니라 최저가 기준인 이유:

1. **단순**: 시계열 정렬·보간·시점 매칭이 필요 없다. min() 하나면 된다.
2. **견고**: 스냅샷 간격이 불균일하거나(Jenkins 지연) 일부 결손이어도 판정이 흔들리지 않는다. "6시간 전 정확히 그 시점"의 스냅샷이 없어도 된다.
3. **보수적이지 않음**: 최저가 대비이므로 점프를 놓치는 방향의 오차가 없다. 대신 base 구간 제한([0.15, 0.70])이 "원래부터 바닥이던 시장의 자연 반등"을 걸러준다.

## 3. 진입/청산 규칙 정밀 명세

### 3.1 진입 (모두 AND, 점프 방향 토큰 기준 가격 p)

| # | 조건 | 파라미터 (기본값) | 의도 |
|---|------|------------------|------|
| 1 | liquidity >= $20,000 | `min_liquidity` | 체결 가능성 확보 |
| 2 | volume24hr >= $10,000 | `min_volume_24h` | 죽은 시장 배제 |
| 3 | 해결까지 >= 24h | `entry_hours_min` | 마감 직전 정보성 급변(수렴)과 분리 |
| 4 | 윈도우 유효성: 6h 윈도우에 >= 5 포인트 AND 커버리지 >= 50% | `jump_window_hours` 등 | 데이터 부족 시 진입 금지 (백필 시도 후에도 invalid면 skip) |
| 5 | 점프: 6h 윈도우 최저가(base) 대비 현재가 상승 >= +0.10 | `jump_min` | 충격 감지 |
| 6 | base ∈ [0.15, 0.70] | `base_min`, `base_max` | 붕괴권 롱샷의 반등·이미 favorite인 시장 배제 |
| 7 | 현재가 <= 0.85 | `current_max` | 러닝룸 확보 (TP +12% 도달 가능) |
| 8 | 고점 유지: 최근 60분 고점 대비 되돌림 <= 0.02 | `hold_window_minutes`, `max_pullback` | 노이즈 스파이크 배제 (elderberry 영역) |
| 9 | 거래량 확인: 현재 volume24hr >= 24h 윈도우 평균 x 2.0 | `vol_mult_min` | 진짜 정보의 서명 |
| 10 | 재진입 쿨다운 통과 (HOLDING 아님, 마지막 매도/skip 24h 경과) | `reentry_cooldown_hours` | 영구 one-shot 제거, 과잉 재진입 방지 |

**방향**: 스냅샷은 항상 YES 가격으로 저장한다. YES 급등 → YES(index 0) 매수, YES 급락(=NO 급등) → NO(index 1) 매수. 조건 5~8은 매수할 토큰 기준 가격(NO면 1-p)으로 평가한다.

### 3.2 청산 (우선순위 순, 매 사이클 midpoint 재조회)

| 순위 | 조건 | 파라미터 (기본값) | exit_reason |
|------|------|------------------|-------------|
| 1 | P&L <= -8% | `stop_loss_percent` = -0.08 | `stop_loss` |
| 2 | 현재가 >= min(매수가 x 1.12, **0.99**) | `take_profit_percent` = 0.12 | `take_profit` |
| 3 | 현재가 < 최고가 x (1 - 0.06) | `trailing_stop.percent` = 0.06 | `trailing_stop` |
| 4 | 모멘텀 사망: 최근 3h 가격 변화 <= 0 | `death_window_hours` = 3 | `momentum_death` |
| 5 | 해결까지 < 12h | `exit_hours` = 12 | `time_exit` |
| - | midpoint 조회 불가 + endDate 24h 경과 | (고정) | `resolved_unredeemed` (EXPIRED, 수동 redeem 필요) |

모멘텀 사망은 "편승 근거(드리프트)가 소멸했다"는 신호다. 단, 매수 직후 데이터가 얕을 때(윈도우 포인트 < 2개 또는 커버리지 < 50%)는 판단을 보류해 즉시 청산을 방지한다.

## 4. 파라미터·env var 표

우선순위: **env > config.yaml > 코드 기본값**

### 공통 env (전 봇 동일 이름)

| env | 기본 | 의미 |
|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | (필수) | CLOB L1 인증 |
| `POLYMARKET_FUNDER_ADDRESS` | (필수) | funder 지갑 주소 |
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| `POLYBOT_TAKE_PROFIT` | 0.12 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 빈 값 = 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 (`--verbose`가 최우선) |

### 전략 전용 env

| env | 기본 | 의미 |
|---|---|---|
| `POLYBOT_JUMP_WINDOW_HOURS` | 6 | 점프 감지 윈도우 |
| `POLYBOT_JUMP_MIN` | 0.10 | 윈도우 내 최저가 대비 최소 상승폭 |
| `POLYBOT_BASE_MIN` | 0.15 | 점프 시작 기준가 하한 |
| `POLYBOT_BASE_MAX` | 0.70 | 점프 시작 기준가 상한 |
| `POLYBOT_CURRENT_MAX` | 0.85 | 현재가 상한 (러닝룸) |
| `POLYBOT_HOLD_WINDOW_MINUTES` | 60 | 고점 유지 확인 윈도우 |
| `POLYBOT_MAX_PULLBACK` | 0.02 | 고점 대비 최대 되돌림 |
| `POLYBOT_VOL_MULT_MIN` | 2.0 | 거래량 확인 배수 |
| `POLYBOT_DEATH_WINDOW_HOURS` | 3 | 모멘텀 사망 판정 윈도우 |
| `POLYBOT_ENTRY_HOURS_MIN` | 24 | 진입 최소 잔여 시간 |
| `POLYBOT_EXIT_HOURS` | 12 | 시간 청산 기준 |
| `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | 0.06 | 최고점 대비 하락률 |

## 5. 이 전략이 실패하는 경우 (솔직한 리스크)

1. **리서치의 평균이 맞는 경우**: "10%+ 급변은 회귀"가 거래량 조건부로도 성립하면 lime은 구조적으로 고점 매수 봇이 된다. 이 경우 elderberry가 수익을 내고 lime은 손절이 쌓인다 — A/B 설계상 이것도 유효한 실험 결과다.
2. **이미 다 반영된 점프**: 예측시장 참여자가 충분히 기민하면 점프는 한 번에 fair value로 간다. 편승분(드리프트)이 없으면 진입 직후 횡보 → `momentum_death`로 소손실 청산이 반복된다.
3. **점프의 되돌림 지연**: 고점 유지 60분은 짧다. 2~3시간에 걸쳐 천천히 되돌리는 가짜 뉴스(정정 보도, 루머 부정)에는 -8% 손절로 당한다.
4. **거래량 지표의 한계**: `volume24hr`는 24시간 누적이라 "지금 이 순간의 폭증"을 정확히 못 잡는다. 점프 몇 시간 후에야 2배 조건이 충족되어 늦게 진입할 수 있다.
5. **양방향 이벤트 상관 리스크**: 같은 이벤트의 여러 시장이 동시에 점프하면 (예: 후보 사퇴 뉴스로 관련 시장 5개 급변) 동일 리스크를 다중 포지션으로 진다. 이벤트 단위 노출 상한이 없다.
6. **박한 진입 빈도**: 6h 내 +0.10 점프 + 거래량 2배 + 고점 유지는 드문 조합이다. 거래 표본이 느리게 쌓여 판정(§6)까지 오래 걸릴 수 있다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job sim-lime`을 Jenkins 3~5분 주기로. `data/sim-lime/trades_sim.db`로 진입 빈도·가짜 신호 비율 확인.
2. **소액 실전 (4주)**: `POLYBOT_BUY_AMOUNT=5`로 실전 전환. elderberry와 같은 기간·같은 금액으로 병행 운영.
3. **판단 기준 (4주 후, 30+ 거래 확보 시)**:
   - 승률 >= 55% AND 평균 손익 > 0 → 금액 증액 검토
   - `momentum_death` 청산 비율 > 50% → 편승분이 없다는 신호, `jump_min` 상향 또는 전략 폐기 검토
   - elderberry와 비교: 같은 이벤트 클래스에서 어느 쪽 가설이 맞았는지 `entry_reason`/`exit_reason` 집계로 판정
   - 거래 표본이 30 미만이면 4주 연장 (진입 빈도 자체가 낮은 전략)

## 7. 베리에이션 아이디어

- **A-1 (민감)**: `POLYBOT_JUMP_MIN=0.07`, `POLYBOT_VOL_MULT_MIN=1.5` — 더 작은 충격도 편승. 노이즈 유입 증가 리스크.
- **A-2 (보수)**: `POLYBOT_JUMP_MIN=0.15`, `POLYBOT_MAX_PULLBACK=0.01`, `POLYBOT_HOLD_WINDOW_MINUTES=90` — 확실한 충격만. 진입 빈도 급감.
- **A-3 (빠른 회전)**: `POLYBOT_TAKE_PROFIT=0.06`, `POLYBOT_DEATH_WINDOW_HOURS=2`, `POLYBOT_TRAILING_STOP_PERCENT=0.04` — 드리프트 초반만 먹고 이탈.

셋 다 코드 수정 없이 Jenkins env로 별도 job(`--job lime-a1` 등)을 만들면 된다. job별 DB가 분리되어 성적 비교가 가능하다.

## 8. 알려진 구현 한계

- **GTC limit at midpoint + 체결 가정**: 주문 접수 = 체결로 기록한다 (미체결/부분체결 시 DB와 지갑이 어긋남). cherry와의 A/B 비교 가능성을 위해 의도적으로 유지한 패턴이며, 실제 체결가는 midpoint보다 불리해 P&L이 과대평가될 수 있다.
- **스냅샷 의존 cold start**: 진입 판정은 자체 축적 스냅샷에 의존한다. prices-history 백필이 완화하지만, 이 endpoint는 문서화되지 않은 외부 지식이라 언제든 실패할 수 있다 (실패 시 "데이터 부족"으로 진입하지 않고 스냅샷 축적으로 자연 회복). **거래량 확인은 백필이 불가능**해 실축적 스냅샷이 최소 수 시간 쌓여야 진입이 시작된다.
- **volume24hr의 해상도**: Gamma의 24h 누적 지표라 순간 거래량 폭증 감지가 지연된다.
- **endDate 신뢰성**: Gamma endDate는 실제 해결 시각과 다를 수 있다. 조기 해결되면 EXPIRED 처리(수동 redeem 필요)로 넘어간다.
- **redeem 미구현**: EXPIRED 포지션의 승리 토큰 상환은 수동이다. `expired` 카운트가 뜨면 지갑에서 직접 redeem해야 한다.
