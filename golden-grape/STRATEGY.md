# Golden Grape 전략 — Cascade Rider (정보 폭포 편승)

## 1. 한 줄 요약

**"소폭이지만 일관된 24h 드리프트 + 거래량 가속"이 확인된 시장에 편승해, 정보가 대중 전체에 확산되기 전 구간의 추가 이동을 수확한다.**

노리는 심리 편향: **정보 폭포(information cascade)와 과소반응(underreaction)** — 새 정보는 대중에게 천천히 확산되고, 가격은 그 확산 속도만큼만 서서히 움직인다.

## 2. 왜 이 전략인가

### 2.1 심리학적 논리

예측시장 참가자는 동시에 정보를 얻지 않는다. 정보에 가까운 소수가 먼저 베팅하면 가격이 소폭 움직이고, 그 가격 변화 자체가 신호가 되어 나머지 대중이 뒤따라 베팅한다(정보 폭포). 이 확산이 완료되기 전까지 가격은 새 정보를 **부분적으로만** 반영한 상태이므로, 초기 드리프트에 편승하면 잔여 이동을 수확할 수 있다. 반대로 급격한 대형 움직임은 이미 확산이 끝났거나 과잉반응이므로 편승 가치가 없다.

### 2.2 레포 리서치 문서 근거

`docs/polymarket-strategy-momentum.md` (banana/cherry 레포 공유 리서치):

- **"2-3% 일일 변화: 같은 방향으로 추가 이동할 확률 6-8% 상승 (가장 강한 모멘텀)"**
- **"10%+ 일일 변화: 모멘텀 약화, 평균회귀 가능성 높음"** — 대형 움직임은 이익실현과 역추세 베팅을 유발해 오히려 반전한다.

grape의 드리프트 밴드 `[+0.04, +0.10]`는 이 발견을 그대로 구현한 것이다. 하한 0.04는 노이즈(±1~2%p 일상 변동)를 걸러내고, 상한 0.10은 mean-revert 영역을 배제한다.

### 2.3 기존 봇에서 배운 것 — banana 골든크로스 실패의 교정

banana `STRATEGY_ANALYSIS.md`에서 확인된 골든크로스 전략의 실패 원인과 grape의 교정:

| # | banana의 실패 원인 | grape의 교정 |
|---|---|---|
| 1 | **도달 불가 threshold**: golden cross 임계값 0.02가 "스냅샷당 +2%p 기울기" 단위여서, 단기 윈도우 3개 기준 **15분에 약 6%p 급등**해야 발화한다. 85~97% 구간에서 이런 움직임은 극히 드물고 실로그의 diff는 ±0.007 이하 — 시그널이 사실상 한 번도 발화하지 않아 무전략으로 퇴화했다. | 임계값을 "스냅샷당 기울기"가 아니라 **윈도우 전체 변화량(%p)** 으로 재정의했다. 24h에 +4~10%p는 리서치 문서가 "가장 강한 모멘텀"으로 실측한, **실제로 도달 가능한** 밴드다. |
| 2 | **count 기반 윈도우**: "최신 N개 스냅샷"으로 윈도우를 정의해 timestamp 간격을 검증하지 않는다. Jenkins가 멈췄다 재개되면 "15분 윈도우"가 몇 시간을 커버해도 그대로 계산한다. | **timestamp 기반 윈도우** (`get_window`: ts >= now - 24h) + **커버리지 검증** (`is_window_valid`: 포인트 >= 5개 AND 실제 커버 시간 >= lookback의 50%). Jenkins 중단 시 윈도우가 자동으로 invalid 처리된다. |
| 3 | **관대한 cold-start 폴백**: 스냅샷 6개 미만이면 "단기 momentum > 0"만으로 진입 허용 — 실운영 매수의 전부가 이 우회 경로였고, 사실상 "확률 구간 + 방금 안 떨어짐" 전략으로 퇴화했다. | 폴백을 **제거**했다. 윈도우 invalid → CLOB `/prices-history` 백필 시도 → 그래도 invalid면 **진입하지 않는다**. 데이터 없이는 베팅하지 않는다. |
| 4 | **양 끝점 2개 값만 사용**: 끝점 노이즈에 민감하고 중간 경로를 무시한다. | **버킷 일관성 게이트** 추가: 24h를 4h 버킷 6개로 나눠 70% 이상의 버킷이 비음(>= 0) 변화여야 진입. 끝점만 우연히 벌어진 노이즈를 걸러낸다. |
| 5 | **확인 신호 부재**: 가격 기울기 하나로만 판단. | **거래량 가속 게이트** 추가: 현재 volume24hr가 24h 윈도우 평균의 1.2배 이상이어야 진입. "가격이 오르는데 거래량도 붙는다" = 진짜 정보 확산의 이중 확인. |

추가로 기존 봇 공통 버그도 수정했다: 영구 one-shot 거래 제한 → 24h 쿨다운 기반 재진입, rapid_jump 영구 skip → timestamp 기반 skip, 해결된 시장의 HOLDING 영구 잔류 → EXPIRED 처리, take_profit 도달 불가 → 목표가 0.99 캡, LOG_LEVEL env 무시 → 지원.

## 3. 진입/청산 규칙 정밀 명세

### 3.1 진입 (모두 AND, 드리프트 방향 토큰 기준 가격 p)

| # | 조건 | 값 | 근거 |
|---|------|-----|------|
| 1 | 유동성 / 거래량 / 잔여시간 | liquidity >= $20,000, volume24hr >= $10,000, 해결까지 >= 48h | 신호 신뢰성 + 러닝룸 확보 |
| 2 | 윈도우 유효성 | 24h 윈도우에 포인트 >= 5개, 커버리지 >= 12h. invalid면 `/prices-history` 백필 시도, 그래도 invalid면 진입 금지 | banana 교정 #2, #3 |
| 3 | 방향 결정 | YES 상승 드리프트 → YES 매수, YES 하락(= NO 상승) → NO 매수. 이하 조건은 매수 토큰 기준 | Polymarket에 숏 없음 |
| 4 | 가격 밴드 | p ∈ [0.40, 0.80] | 러닝룸 있는 중간 구간 |
| 5 | 24h 드리프트 | +0.04 <= (현재가 - 24h 전 가격) <= +0.10 | 리서치: 소폭 모멘텀 지속, 10%+ 회귀 |
| 6 | 일관성 | 4h 버킷 6개 중 >= 70%가 비음(>= 0) 변화 (판정 가능 버킷 절반 미만이면 데이터 부족으로 진입 금지) | banana 교정 #4 |
| 7 | 거래량 가속 | 현재 volume24hr >= 24h 윈도우 평균의 1.2배 | banana 교정 #5 |
| 8 | 재진입 | HOLDING 없음 + 마지막 청산/skip 후 24h 경과 | 영구 one-shot 제거 |

매수 직전 CLOB midpoint로 재검증: 밴드 상한(0.80) 초과 급등이면 skip(`rapid_jump`, 쿨다운 후 재평가), 하한(0.40) 미만 하락이면 이번 사이클만 skip.

### 3.2 청산 (우선순위 순, 매 사이클 검사)

| 순위 | 조건 | 값 | exit_reason |
|------|------|-----|-------------|
| 1 | 손절 | P&L <= -8% | `stop_loss` |
| 2 | 익절 | 현재가 >= min(진입가 × 1.15, **0.99**) | `take_profit` |
| 3 | 드리프트 소멸 | 최근 6h 매수 토큰 가격 변화 <= 0. 단, 포인트 >= 3이고 실제 커버 시간 >= 3h(50%)일 때만 판정 | `drift_death` |
| 4 | 트레일링 스탑 | 최고가 대비 -6% | `trailing_stop` |
| 5 | 시간 청산 | 해결까지 < 24h | `time_exit` |
| - | 해결 시장 | midpoint 조회 실패 + end_date 24h 경과 → EXPIRED 마감 (수동 redeem 필요) | `resolved_unredeemed` |

## 4. 파라미터·env var 표

우선순위: **env > config.yaml > 코드 기본값**.

### 전략 파라미터

| env var | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_PROB_MIN` | 0.40 | 매수 토큰 가격 하한 |
| `POLYBOT_PROB_MAX` | 0.80 | 매수 토큰 가격 상한 |
| `POLYBOT_DRIFT_LOOKBACK_HOURS` | 24 | 드리프트 판정 윈도우 (h) |
| `POLYBOT_DRIFT_MIN` | 0.04 | 드리프트 하한 (+4%p) |
| `POLYBOT_DRIFT_MAX` | 0.10 | 드리프트 상한 (mean-revert 배제) |
| `POLYBOT_BUCKET_HOURS` | 4 | 일관성 버킷 크기 (h) |
| `POLYBOT_CONSISTENCY_MIN` | 0.70 | 비음 버킷 비율 하한 |
| `POLYBOT_VOL_ACCEL_MIN` | 1.2 | 거래량 가속 배수 하한 |
| `POLYBOT_DEATH_WINDOW_HOURS` | 6 | 드리프트 소멸 판정 윈도우 (h) |
| `POLYBOT_DEATH_WINDOW_MIN_POINTS` | 3 | 소멸 판정 최소 스냅샷 수 |
| `POLYBOT_DEATH_WINDOW_MIN_COVERAGE` | 0.5 | 소멸 윈도우 최소 시간 커버리지 |
| `POLYBOT_ENTRY_HOURS_MIN` | 48 | 해결까지 최소 잔여시간 (h) |
| `POLYBOT_EXIT_HOURS` | 24 | 시간 청산 기준 (h) |
| `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 스탑 on/off |
| `POLYBOT_TRAILING_STOP_PERCENT` | 0.06 | 트레일링 스탑 % |

### 공통 파라미터 (전 봇 동일 이름)

| env var | 기본값 | 의미 |
|---|---|---|
| `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| `POLYBOT_TAKE_PROFIT` | 0.15 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (h) |
| `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | "" | 제외 카테고리 (comma 구분, 기본 비활성) |
| `LOG_LEVEL` | INFO | 로그 레벨 |

## 5. 이 전략이 실패하는 경우

1. **드리프트가 이미 완성된 정보일 때**: 24h 드리프트가 새 정보의 "초기 확산"이 아니라 이미 반영이 끝난 결과라면, 진입 시점이 곧 고점이다. 거래량 가속 게이트가 일부 걸러주지만 보장은 없다.
2. **뉴스 반전**: 편승 직후 반대 방향 뉴스가 나오면 드리프트가 급반전한다. 손절 -8%와 드리프트 소멸 청산이 방어선이지만 갭 하락에는 무력하다.
3. **낮은 신호 빈도**: 조건 8개가 모두 AND라서 후보가 드물 수 있다. 특히 일관성 70% + 거래량 1.2배 조합은 보수적이다. 거래가 너무 적으면 통계적 검증 자체가 늦어진다.
4. **횡보 시장에서의 왕복 손실**: 드리프트가 밴드 안에서 생겼다 죽었다를 반복하면, 진입 → drift_death 청산 → 쿨다운 후 재진입의 왕복 비용(스프레드)이 누적된다.
5. **거래량 지표의 한계**: gamma `volume24hr`는 시장 전체 거래량이지 방향별 거래량이 아니다. 반대 방향 매물 폭증도 "가속"으로 잡힌다.
6. **스냅샷/백필 데이터 품질**: 백필 실패 + 스냅샷 부족 구간에서는 진입 자체가 불가능하다 (의도된 동작이지만 기회비용이다).
7. **negRisk 다후보 이벤트**: YES/NO 2-outcome 가정으로 판정하므로, 다후보 이벤트의 개별 시장에서는 "그 후보" 토큰 기준으로 동작한다. 후보 간 자금 이동이 드리프트로 오인될 수 있다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job sim-grape`를 Jenkins 3~5분 주기로 실행. `data/sim-grape/trades_sim.db`로 신호 빈도와 가상 손익 확인. 신호가 주 3건 미만이면 `POLYBOT_CONSISTENCY_MIN`/`POLYBOT_VOL_ACCEL_MIN` 완화를 검토.
2. **소액 실전 (4주)**: `POLYBOT_BUY_AMOUNT=5`로 실전 전환. 대시보드(`GOLDEN-GRAPE`)와 daily-report로 잔고 추적.
3. **판단 기준**: 4주 & 30+ 거래 시점에 평가 —
   - 승률 >= 55% AND 평균 손익 > 0 → 금액 증액
   - exit_reason 분포 확인: `drift_death`/`trailing_stop` 비중이 70%를 넘으면 진입 신호가 늦다는 뜻 → 드리프트 하한 완화 검토
   - 승률 < 45% → 전략 기각 또는 파라미터 재설계

```sql
-- 검증 쿼리 (data/{job}/trades.db) — status는 enum 이름으로 저장됨 (COMPLETED/EXPIRED)
SELECT exit_reason, COUNT(*) cnt, ROUND(AVG(realized_pnl), 4) avg_pnl,
       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) win_rate
FROM trades WHERE status = 'COMPLETED' GROUP BY exit_reason;
```

## 7. 베리에이션 아이디어

- **grape-1 (엄격)**: `POLYBOT_CONSISTENCY_MIN=0.85`, `POLYBOT_VOL_ACCEL_MIN=1.5` — 신호는 줄지만 확산 확신이 높은 것만.
- **grape-2 (민감)**: `POLYBOT_DRIFT_MIN=0.03`, `POLYBOT_CONSISTENCY_MIN=0.60` — 더 이른 편승, 노이즈 리스크 증가.
- **grape-3 (장기 보유)**: `POLYBOT_DEATH_WINDOW_HOURS=12`, `POLYBOT_TRAILING_STOP_PERCENT=0.08`, `POLYBOT_TAKE_PROFIT=0.25` — 드리프트 소멸 판정을 느슨하게 해 큰 추세를 끝까지.

## 8. 알려진 구현 한계

1. **GTC limit 체결 가정**: midpoint 가격 GTC limit 주문을 접수 즉시 체결로 가정하고 HOLDING/COMPLETED를 기록한다 (미체결 추적 없음). cherry와의 A/B 비교 가능성을 위해 의도적으로 유지한 패턴이며, 급락장에서 손절 미체결 리스크가 있다.
2. **스냅샷 의존 cold start**: 배포 직후에는 24h 윈도우가 비어 있다. `/prices-history` 백필이 이를 메우지만, 이 endpoint는 공식 문서화가 불완전한 public API라 실패할 수 있다. 실패 시 스냅샷이 자연 축적되는 약 12h(커버리지 50%) 동안은 진입이 없다 — 의도된 안전 동작.
3. **시뮬레이션 낙관 편향**: sim 체결이 midpoint 100% 체결 가정 — spread/슬리피지/수수료 미모델링으로 sim P&L이 실거래보다 좋게 나온다.
4. **volume24hr 스냅샷 해상도**: 거래량 가속은 사이클 시점의 24h 누적값 비교라서 단시간 폭증/급감의 타이밍을 정밀하게 잡지 못한다.
5. **자금 관리 부재**: 잔고 조회·일일 매수 한도·확률 기반 sizing 없음. `POLYBOT_MAX_POSITIONS`로만 제한 가능.
