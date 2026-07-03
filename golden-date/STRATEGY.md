# Golden Date 전략: Conviction Ladder

## 1. 한 줄 요약 + 노리는 심리 편향

**"해결이 가까울수록 비싸게, 멀수록 싸게만 favorite을 산다"** — 마감 임박 확증 편향과 favorite-longshot bias가 만드는
사전 해결 수렴(pre-resolution convergence)을, 잔여 시간에 비례한 3단 확률 밴드(사다리)로 수확하는 전략.

## 2. 왜 이 전략인가

### 심리학적 논리

- **Favorite-longshot bias**: 예측시장 참가자는 복권성 longshot을 과대평가하고 favorite을 과소평가한다.
  70~95% 구간의 favorite은 실현 확률 대비 구조적으로 저평가 → favorite 매수는 +EV.
- **사전 해결 수렴**: 해결이 가까워질수록 불확실성이 사라지며 가격이 1.0으로 수렴한다.
  cherry가 인용한 Dune Analytics 데이터: 해결 24h 전 정확도 88.6% → 4h 전 94.2%.
  이 마지막 수렴 구간의 "시간가치 소멸"이 수익 원천이다.
- **시간-확신 비례**: 잔여 시간이 길수록 반전 리스크가 크다. 같은 0.93짜리 favorite이라도
  하루 뒤 해결과 일주일 뒤 해결은 기대값이 다르다 — 그런데 cherry는 같은 밴드로 같은 가격을 지불했다.

### cherry에서 배운 것 (수정한 5가지 허점)

| # | cherry 허점 | date의 수정 |
|---|------------|------------|
| ① | 잔여 시간과 무관한 고정 확률 밴드 (30일 남은 0.92도 매수) | 시간 사다리: 멀수록 낮은 밴드만 허용 |
| ② | `--yes-only` 운영으로 NO-favorite 시장 절반을 버림 | 기본은 favorite side 자동 선택 (YES/NO 중 높은 쪽). `--yes-only`는 A/B용 옵션으로 유지 |
| ③ | 하락 중인 favorite도 매수 (떨어지는 칼날) | 모멘텀 게이트: 최근 6h favorite 변화 >= -0.01 이어야 진입 |
| ④ | rapid_jump 영구 skip (기회 영구 상실) | skip 기록에 timestamp를 두고 24h 쿨다운 후 재평가 |
| ⑤ | 해결 12h 전 청산 → 정확도가 가장 올라가는 마지막 수렴 구간 포기 | time exit을 해결 2h 전으로 연장 |

### banana에서 배운 것

- 개수 기반 스냅샷 윈도우는 Jenkins 중단 시 "15분 윈도우"가 몇 시간을 커버하는 버그 → **timestamp 기반 윈도우 + 커버리지 검증**.
- cold-start 시 관대한 폴백으로 사실상 무전략 퇴화 → **윈도우 invalid면 진입하지 않는다.** 대신 CLOB `prices-history` 백필로 cold start 자체를 줄인다.

## 3. 진입/청산 규칙 정밀 명세

### 진입 (모두 충족)

| # | 조건 | 값 |
|---|------|-----|
| 1 | 유동성 / 24h 거래량 | liquidity >= $15,000 AND volume24hr >= $5,000 |
| 2 | favorite side 선택 | YES/NO 중 확률 높은 쪽 (`--yes-only` 시 index 0만) |
| 3 | 시간 사다리 (잔여 h, favorite 가격 p) | 6 < h <= 24: p ∈ [0.80, 0.95] / 24 < h <= 72: p ∈ [0.75, 0.92] / 72 < h <= 168: p ∈ [0.70, 0.88] (양끝 포함) |
| 4 | 모멘텀 게이트 | 최근 6h 윈도우 favorite 가격 변화 >= -0.01 (하락 추세 아님). 윈도우 invalid(포인트 < 5 또는 커버리지 < 3h)면 백필 시도, 그래도 invalid면 **진입 금지** |
| 5 | 재진입 쿨다운 | HOLDING 없음 AND 마지막 매도/skip이 24h 이전 |
| 6 | 주문 재검증 | 주문 직전 midpoint 재조회, 밴드 상한 초과 시 rapid_jump skip (쿨다운 TTL), 하한 미달 시 단순 skip, 5주 최소 주문 충족 |

### 청산 (우선순위 순, 매 사이클 검사)

| 순위 | 조건 | 값 | exit_reason |
|------|------|-----|-------------|
| 1 | 손절 | P&L <= -8% | `stop_loss` |
| 2 | 익절 | 현재가 >= min(매수가 × 1.12, **0.99**) | `take_profit` |
| 3 | 트레일링 스탑 | 현재가 < 최고가 × 0.95 | `trailing_stop` |
| 4 | 시간 청산 | 해결까지 < 2h | `time_exit` |
| - | 해결된 시장 leak | midpoint 조회 실패 + endDate 24h 이상 경과 → EXPIRED 마감 (P&L 미확정, 수동 redeem) | `resolved_unredeemed` |

## 4. 파라미터·env var 표

| env | 기본 | 의미 |
|-----|------|------|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 15000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 5000 | 최소 24h 거래량 $ |
| `POLYBOT_ENTRY_HOURS_MIN` | 6 | 진입 최소 잔여 시간 (이하 금지) |
| `POLYBOT_LADDER_H1` / `H2` / `H3` | 24 / 72 / 168 | 사다리 구간 상한 시간 |
| `POLYBOT_BAND1_MIN` / `BAND1_MAX` | 0.80 / 0.95 | 밴드1 (6~24h) |
| `POLYBOT_BAND2_MIN` / `BAND2_MAX` | 0.75 / 0.92 | 밴드2 (24~72h) |
| `POLYBOT_BAND3_MIN` / `BAND3_MAX` | 0.70 / 0.88 | 밴드3 (72~168h) |
| `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | 모멘텀 윈도우 |
| `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.01 | favorite 변화 하한 |
| `POLYBOT_TAKE_PROFIT` | 0.12 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | 0.05 | 최고점 대비 하락률 |
| `POLYBOT_EXIT_HOURS` | 2 | 시간 청산 기준 |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (빈 값 = 비활성) |
| `POLYBOT_YES_ONLY` | false | Yes(index 0) 토큰만 매수 (CLI `--yes-only`가 우선) |
| `LOG_LEVEL` | INFO | 로그 레벨 |

## 5. 이 전략이 실패하는 경우

1. **막판 반전 이벤트**: 해결 2h 전까지 보유하므로 cherry(12h 전 청산)보다 막판 반전에 더 노출된다. 0.9대 favorite이 뒤집히면 -8% 손절로는 못 막는 갭 하락(0.9 → 0.2)이 가능하다.
2. **트레일링이 손절을 대체하는 whipsaw**: max_price가 매수가로 초기화되므로 실효 손절선은 -5%다. 70~80% 구간의 일상 노이즈(±3~5%p)에 반복적으로 털리면 수수료 없는 시장이어도 스프레드 비용이 누적된다.
3. **모멘텀 게이트의 후행성**: 6h 윈도우 변화는 후행 지표다. 급락 직전의 "평온한" favorite은 통과시키고, 일시 조정 후 반등할 favorite은 걸러내는 양방향 오류가 있다.
4. **endDate 신뢰성**: Gamma endDate는 마감 예정일이지 실제 해결 시각이 아니다. 조기 해결되면 time exit 기회 없이 EXPIRED로 빠질 수 있다 (§3 leak 처리로 좀비는 안 남지만 P&L은 미확정).
5. **상관 리스크 무관리**: 같은 이벤트의 여러 시장(예: 한 선거의 후보별 시장)을 동시 매수해 동일 리스크를 중복으로 질 수 있다. `max_positions`는 총량 제한일 뿐이다.
6. **저변동 구간의 재진입 쿨다운**: 24h 쿨다운은 완만히 수렴하는 시장에서 두 번째 진입 기회를 놓치게 할 수 있다 (반대로 쿨다운이 없으면 손절 반복 위험 — 트레이드오프).

## 6. A/B 검증 방법

1. **시뮬레이션**: `uv run python main.py run --simulate --job sim`을 Jenkins 3~5분 주기로 1~2주 축적.
   `data/sim/trades_sim.db` + 월별 CSV에서 진입 사유별 성과 분리 집계.
   회고 전용 컬럼(`strategy_name`="date", `mode`="live"/"sim", `volume_24h_at_buy`,
   `ladder_band_at_buy` 1/2/3, `momentum_at_buy`)이 trades·CSV에 함께 기록되므로
   `entry_reason` 문자열 파싱 없이 사다리 단별·모멘텀 구간별 집계가 가능하다.
2. **소액 실전**: `POLYBOT_BUY_AMOUNT=5`로 실주문 2주. 시뮬레이션과의 체결 편차(midpoint 가정 vs 실제)를 확인.
3. **판단 기준**: 4주, 30+ 거래 기준 —
   - 승률 >= 70% AND 평균 손익 > 0 (수렴 전략은 소수 대형 손실 + 다수 소형 이익 구조이므로 평균이 중요)
   - 사다리 단별 승률 비교: 밴드3(멀리)이 밴드1(가까이)보다 유의미하게 나쁘면 h3 축소
   - `exit_reason` 분포: trailing_stop이 과반이면 whipsaw 의심 → 트레일링 지연/완화 검토
4. **cherry와의 A/B**: 같은 기간 cherry 계정 성과와 비교. 동일 GTC midpoint 체결 가정을 유지했으므로 회계 기준이 같다.

## 7. 베리에이션 아이디어

| 안 | 이름 | 변경 env |
|----|------|---------|
| A-1 | date-1: 사다리 좁게 (단기 집중) | `POLYBOT_LADDER_H3=24` (밴드1만 사용, h<=24만 진입) |
| A-2 | date-2: YES-only | CLI `--yes-only` 또는 `POLYBOT_YES_ONLY=true` (cherry 운영 모드와 직접 비교) |
| A-3 | date-3: trailing off + 홀드 | `POLYBOT_TRAILING_STOP_ENABLED=false` (수렴을 2h 전까지 끝까지 태우고 -8% 손절만 사용) |

## 8. 알려진 구현 한계

- **GTC limit 체결 가정**: midpoint에 지정가 주문을 내고 orderID만 받으면 체결로 간주한다. 미체결/부분체결 시 DB와 지갑이 어긋난다. cherry와의 A/B 비교 가능성을 위해 의도적으로 유지한 패턴이다.
- **midpoint 기준 P&L**: 실제 체결은 bid/ask 근처이므로 기록 P&L이 소폭 과대평가된다.
- **스냅샷 의존 cold start**: 모멘텀 게이트는 자체 스냅샷 6h 축적이 필요하다. `prices-history` 백필로 완화하지만, 이 endpoint는 문서 보증이 약해 실패 시 초기 사이클들은 진입이 없을 수 있다 (의도된 보수적 동작).
- **job 간 중복**: 중복 방지가 job DB 단위라 같은 지갑으로 다른 job을 돌리면 같은 시장을 이중 매수할 수 있다.
- **체결/redeem 자동화 없음**: EXPIRED 포지션은 수동 redeem이 필요하다 (WARNING 로그로 통지).
