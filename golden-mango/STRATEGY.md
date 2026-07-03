# Golden Mango — Patience Premium (해결 임박 캐리)

## 1. 한 줄 요약

**"거의 확실해진 계약이 만기까지 남은 기간만큼 할인되어 거래되는 settlement discount를,
연환산 캐리 수익률 단일 수식으로 걸러 수확한다."**

노리는 편향: **시간선호(조급함)와 자본 잠김 회피** — 인간 참여자는 돈이 묶이는 것을 싫어해
"거의 확실한" 포지션조차 액면(1.00)보다 싸게 팔아치운다. 여기에 favorite-longshot bias의
favorite 쪽 저평가가 겹친다.

## 2. 왜 이 전략인가

### 2.1 공개 문헌에서 독립적으로 도출된 전략

이 전략은 기존 골든 시리즈(apple/banana/cherry 및 date~lime)의 내부 아이디어와 **독립적으로,
공개 학술 문헌과 실증 연구에서 도출**되었다. 근거는 다음과 같다 (§9 출처 전체 목록).

1. **arXiv 2605.31431 "When Certainty Is Not Worth It: Capital Lock-Up and Settlement
   Discounting in Prediction Markets"** — 예측시장 가격의 기간구조를 실측해, 만기까지
   자본이 잠기는 기간에 비례하는 **settlement discount**가 체계적으로 존재함을 보였다.
   ASW(기간구조) 할인을 보정하면 근확실(near-certain) 구간의 가격 왜곡이 **48~88% 소거**된다.
   즉 0.95짜리 "거의 확실" 계약이 0.95인 이유의 절반 이상이 "확률이 95%라서"가 아니라
   "돈이 묶여서"다. **그 할인이 곧 우리가 살 물건이다.**
2. **arXiv 2602.21091 "Can Interest-Bearing Positions Solve the Long-Horizon Problem in
   Prediction Markets?"** — 담보에 이자가 붙지 않는 예측시장 구조가 장기 계약 가격을
   구조적으로 왜곡함을 이론화했다. Polymarket USDC 담보는 무이자이므로 이 왜곡이 그대로
   존재하고, 시장이 이 문제를 해결하기 전까지 캐리는 구조적으로 지속된다.
3. **"The cost of capital in a prediction market" (International Journal of Forecasting)** —
   예측시장 가격에 자본비용이 반영되어 있음을 보인 선행 연구. settlement discount의
   고전적 근거.
4. **CEPR VoxEU "The economics of the Kalshi prediction market"** — Kalshi 실증에서
   고가(고확률) 계약이 **양(+)의 초과수익**을 보였다 = favorite-longshot bias의 favorite 쪽.
   대중은 롱샷에 복권 심리로 과지불하고 favorite을 과소평가한다. 캐리 매수는 이 편향의
   수혜 방향과 정확히 일치한다.
5. **arXiv 2602.19520 "Decomposing Crowd Wisdom" (Kalshi+Polymarket 2.92억 거래 분석)** —
   대규모 거래 데이터에서 가격 구간별 수익 구조가 체계적으로 다름을 확인한 보조 근거.

요약하면: **"확률이 틀렸다"에 베팅하는 것이 아니라, "대중의 조급함이 만든 할인"을
봇의 무한한 인내로 사들이는 전략**이다. 봇은 자본 잠김을 심리적으로 괴로워하지 않는다.

### 2.2 cherry/date와의 차이 (같은 고확률 구간, 다른 논리)

cherry(Resolution Momentum)와 date(Conviction Ladder)도 고확률 favorite을 사지만,
**심리적 타이밍**(마감 임박 확증 편향, 시간 사다리)이 진입 논리다. mango는 다르다:

- 진입 판단이 **수익률 허들 단일 수식**이다. 시간과 확률을 각각 조건으로 두지 않고,
  `y = ((1-p)/p) × (8760/h)` 하나로 환산해 비교한다.
- 그 결과 **시장이 스스로 진입 frontier를 형성한다**: 같은 0.98이라도 24h 남으면
  y=7.45로 진입하고, 336h 남으면 y=0.53으로 걸러진다. 파라미터를 늘리지 않고도
  "가격 대비 남은 시간이 수지 맞는" 시장만 자동 선별된다. banana의 골든크로스처럼
  도달 불가능한 threshold로 퇴화할 여지가 없다 — 수식이 항상 계산 가능하다.
- 청산도 다르다: 조기 익절(trailing/작은 TP) 없이 **0.99 수렴까지 보유**한다.
  캐리는 만기 수렴이 수익의 원천이므로 중간에 파는 것은 전략 부정이다.

### 2.3 기존 봇에서 배운 것 (공통 개선 반영)

- 스냅샷 윈도우를 **timestamp 기반**으로 검증 (banana의 개수 기반 윈도우 버그 수정).
- 윈도우 invalid → prices-history 백필 → 그래도 invalid면 **진입 금지** (관대한 폴백 금지).
- 재진입 쿨다운(24h) 방식 — 영구 one-shot 금지.
- 해결된 시장의 midpoint 조회 실패 → EXPIRED 마감 처리 (좀비 HOLDING leak 수정).
- 회고 로깅 표준: `strategy_name`/`mode`/`volume_24h_at_buy` + 시그널 수치 컬럼 기록.

## 3. 진입/청산 규칙 정밀 명세

### 핵심 수식

```
연환산 캐리 수익률 y = ((1 - p) / p) × (8760 / hours_left)
진입 ⇔ y >= y_min          (기본 y_min = 2.0 = 연 200%)
```

p에 사서 만기에 1.00을 받으면 수익률이 (1-p)/p. 이를 잔여 시간으로 연환산한 값이 y다.

### 진입 (모두 충족)

| # | 조건 | 기본값 |
|---|------|--------|
| 1 | 유동성 | liquidity >= $20,000 |
| 2 | 방향 | favorite 토큰 (YES/NO 중 높은 쪽; `--yes-only` 지원) |
| 3 | 확률 밴드 | p ∈ [0.85, 0.985] |
| 4 | 시간 창 | 6h < hours_left <= 336h (14일) |
| 5 | 수익률 허들 | y >= 2.0 |
| 6 | 모멘텀 가드 | 최근 6h favorite 변화 >= -0.02 (급락 중 진입 금지) |
| 7 | 데이터 유효성 | 6h 윈도우 invalid → 백필 시도 → 그래도 invalid면 진입 금지 |
| 8 | 재진입 | HOLDING 없음 + 마지막 매도/skip 후 24h 경과 |

모멘텀 가드의 이유: 캐리 수확은 "가격이 그대로 만기에 수렴한다"가 전제다. 최근 급락은
새 정보(오라클 분쟁, 반전 뉴스)의 신호일 수 있으므로 떨어지는 favorite은 사지 않는다.

### 청산 (우선순위 순)

| 순위 | 조건 | exit_reason |
|------|------|-------------|
| 1 | P&L <= -6% | `stop_loss` (수렴 실패 신호 — 즉시 이탈) |
| 2 | 현재가 >= 0.99 | `take_profit` (수렴 완료; 목표가 0.99 캡 고정) |
| 3 | 해결까지 < 2h | `time_exit` (마지막까지 캐리 수확 후 이탈) |
| — | trailing stop | **없음** — 수렴 보유가 본질, 조기 익절 없음 |

`take_profit_percent`는 env로 남겨두되 기본 9.99로 두어 목표가 캡(0.99)만 작동한다.
즉 코드상 익절 조건은 "0.99 도달" 하나다.

## 4. 파라미터·env var 표

우선순위: **env > config.yaml > 코드 기본값**

| env | 기본 | 의미 |
|-----|------|------|
| `POLYBOT_YIELD_MIN` | 2.0 | 연환산 캐리 허들 (2.0 = 연 200%) |
| `POLYBOT_PROB_MIN` | 0.85 | favorite 가격 하한 |
| `POLYBOT_PROB_MAX` | 0.985 | favorite 가격 상한 (스프레드/수수료 여유) |
| `POLYBOT_ENTRY_HOURS_MIN` | 6 | 잔여 시간 하한 (이하이면 진입 금지) |
| `POLYBOT_ENTRY_HOURS_MAX` | 336 | 잔여 시간 상한 (14일 초과 진입 금지) |
| `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | 모멘텀 가드 윈도우 |
| `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.02 | favorite 변화 하한 (미만이면 진입 배제) |
| `POLYBOT_TAKE_PROFIT` | 9.99 | 사실상 미사용 — 목표가 0.99 캡 고정 |
| `POLYBOT_STOP_LOSS` | -0.06 | 손절 % |
| `POLYBOT_EXIT_HOURS` | 2 | 해결 N시간 전 청산 |
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0 = 비활성) |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (실전은 제한 권장, §5) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (빈 값 = 비활성) |
| `POLYBOT_YES_ONLY` | false | Yes(index 0) 토큰만 매수 |
| `LOG_LEVEL` | INFO | 로그 레벨 |

## 5. 이 전략이 실패하는 경우 (솔직한 리스크)

1. **UMA 오라클 분쟁 — 최대 리스크.** favorite이 해결 직전에 뒤집힌 실사례가 있다:
   **Ukraine Minerals 시장이 9% → 100%로 반전**된 사건처럼, 오라클 분쟁/재해석 한 방이
   0.95 매수 포지션을 0으로 만든다. 캐리 전략의 기대수익은 건당 수 %이므로 **전액 손실
   1회가 수십 회의 정상 수익을 지운다.** 방어책:
   - `POLYBOT_MAX_POSITIONS`를 유한하게 설정하고 **소액 사이징**(포트폴리오 대비 건당
     소액)을 유지할 것. 무제한 포지션 + 큰 사이징은 이 전략에서 자멸이다.
   - SL -6%는 분쟁이 가격에 반영되기 시작하면 이탈하게 해주지만, 갭 하락(한 번에 0.9→0.1)은
     막지 못한다.
2. **tail risk의 체계적 과소평가 가능성.** 시장 가격 0.95가 실제 확률 0.97의 할인이
   아니라 정확한 0.95일 수 있다. 이 경우 20번 중 1번 전액 손실로 캐리가 상쇄된다.
   settlement discount 문헌이 "할인이 존재한다"를 보였지만 모든 시장·모든 구간에서
   할인 > 실제 리스크임을 보장하지는 않는다.
3. **선택 편향(어떤 시장이 y를 넘는가).** y >= 2.0을 넘는 시장은 "시장이 유독 할인을
   크게 매긴" 시장인데, 그 할인이 조급함이 아니라 **우리가 모르는 진짜 위험**(해결 기준
   모호함, 분쟁 가능성)의 프리미엄일 수 있다. 모멘텀 가드가 일부를 거르지만 전부는 못 거른다.
4. **금리 환경 변화.** settlement discount의 크기는 기회비용(금리·DeFi 수익률)에 비례한다.
   무위험 수익률이 낮아지면 할인 자체가 축소되어 y >= 2.0을 넘는 시장이 드물어진다
   (손실이 아니라 기회 소멸).
5. **저거래량 시장의 stale 가격.** volume 필터가 기본 비활성이므로 조용한 시장의 midpoint가
   실제 체결 가능 가격과 다를 수 있다. GTC limit이 어느 정도 방어하지만 체결 가정의 한계(§8)와
   결합하면 시뮬레이션 성과가 과대평가될 수 있다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job sim`을 Jenkins
   3~5분 간격으로 실행. `data/sim/trades_sim.db` + 월별 CSV로 검토.
2. **소액 실전 (4주)**: `POLYBOT_BUY_AMOUNT=5`(기본), `POLYBOT_MAX_POSITIONS=10` 권장.
3. **판단 기준**: 4주 / 30+ 거래 기준 —
   - 승률 >= 85% (캐리 전략은 고승률·소수익 구조가 정상)
   - `stop_loss` 비중 <= 15%, `resolved_unredeemed`(EXPIRED) 0건 유지
   - 건당 평균 수익 > 0 이고 최악 거래 손실이 평균 수익의 20배 이내
   - `carry_yield_at_buy` 구간별(2~4 / 4~8 / 8+) 성과 비교 → y_min 조정 근거
4. **교차 봇 비교**: trades에 `strategy_name`/`mode`가 기록되므로 cherry/date와
   UNION 쿼리로 같은 기간 고확률 구간 성과를 직접 비교할 수 있다.

## 7. 베리에이션 아이디어

- **A-1 (보수)**: `POLYBOT_YIELD_MIN=4.0`, `POLYBOT_PROB_MIN=0.90`, `POLYBOT_MAX_POSITIONS=5`
  — 허들을 높여 진짜 싼 캐리만. 거래 수 감소, 건당 마진 증가.
- **A-2 (단기 집중)**: `POLYBOT_ENTRY_HOURS_MAX=72` — 3일 이내 해결 시장만.
  오라클 분쟁에 노출되는 보유 시간 자체를 줄인다.
- **A-3 (YES 한정)**: `--yes-only` — date-2와 동일 패턴의 방향 절반 A/B.
  NO-favorite 시장의 해결 기준 모호성 리스크를 회피하는 대신 모수 절반 포기.

## 8. 알려진 구현 한계

- **GTC limit @ midpoint 체결 가정**: 주문을 내면 체결됐다고 가정하고 DB에 기록한다.
  실제로는 미체결·부분체결이 가능하다. cherry와의 A/B 비교 가능성을 위해 기존 봇과 동일한
  가정을 유지한다 (semi-passive 근사).
- **스냅샷 의존 cold start**: 모멘텀 가드는 자체 스냅샷 축적이 필요하다. prices-history
  백필로 완화하지만, 백필 endpoint는 비공식이라 실패할 수 있고 그 경우 초기 사이클은
  진입을 하지 않는다 (의도된 보수적 동작).
- **EXPIRED 수동 redeem**: 해결 후 청산하지 못한 포지션은 EXPIRED로 마감만 하고 자금
  회수는 하지 않는다. 로그 WARNING을 보고 Polymarket 웹에서 수동 redeem해야 한다.
- **carry_yield_at_exit**: 청산 시점 midpoint 기준 재계산 값이다. 해결 직후(음수 잔여시간)
  등 계산 불가 상황에서는 NULL이 기록된다.

## 9. 참고 출처

- arXiv 2605.31431 — *When Certainty Is Not Worth It: Capital Lock-Up and Settlement
  Discounting in Prediction Markets* (ASW 기간구조 실측: 할인 보정 시 근확실 구간 왜곡 48~88% 소거)
- arXiv 2602.21091 — *Can Interest-Bearing Positions Solve the Long-Horizon Problem in
  Prediction Markets?* (무이자 담보 구조의 장기 계약 왜곡)
- *The cost of capital in a prediction market*, International Journal of Forecasting
- CEPR VoxEU — *The economics of the Kalshi prediction market* (고가 계약 양의 수익률
  = favorite-longshot bias 실증)
- arXiv 2602.19520 — *Decomposing Crowd Wisdom* (Kalshi+Polymarket 2.92억 거래 분석)
