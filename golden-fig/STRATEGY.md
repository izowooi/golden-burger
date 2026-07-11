# Golden Fig — Hope Crusher 전략 문서

## 1. 한 줄 요약

**"D일까지 X가 일어날까" 롱샷(YES 5~25%) 시장에서 NO 토큰(75~95%)을 매수해, 희망 보유자들이 늦게 놓는 시간 가치 소멸(theta)을 수확한다.**

노리는 심리 편향: **favorite-longshot bias** — 대중은 낮은 확률에 복권 심리로 과지불하고(1%로 표시된 사건의 실제 확률은 0.3% 수준), 반대로 favorite은 과소평가한다.

## 2. 왜 이 전략인가

### 심리학적 논리

- favorite-longshot bias는 예측시장/베팅 문헌에서 가장 잘 문서화된 편향이다. 복권 심리(작은 돈으로 큰 보상 기대)는 낮은 확률 구간에서 YES를 체계적으로 과대평가하게 만든다.
- "D일까지 X가 일어날까" 형태의 시장은 시간이 지날수록 X가 일어날 시간 자체가 소진되어 YES의 정당 가치가 기계적으로 하락한다(theta decay). 그러나 YES 보유자는 **앵커링**(매수가 기준 사고)과 **희망적 사고** 때문에 포지션을 늦게 놓는다 → NO 가격이 정당 가치보다 낮게 유지되는 구간이 생긴다.
- 즉 NO 매수는 "시간의 흐름"이라는 확실한 방향성에 베팅하면서, 대중 심리가 만든 할인까지 얻는 구조다.

### 기존 봇에서 배운 것

- **cherry**가 같은 편향의 favorite 쪽(75~92% favorite 매수)을 수확해 왔다. fig는 같은 편향의 **미러**다: cherry가 "favorite 저평가"를 사면, fig는 "longshot 과대평가"를 판다(= NO 매수). 두 봇의 성과 비교가 이 편향의 어느 쪽이 더 두터운지에 대한 A/B 검증이 된다.
- cherry의 `--yes-only` 운영은 NO-favorite 시장 절반을 버렸다(STRATEGY_ANALYSIS 확인 사항). fig는 방향을 전략에 내장(항상 NO)해서 이 낭비 자체가 없다.
- **banana**의 개수 기반 스냅샷 윈도우 버그(Jenkins 중단 시 15분 윈도우가 수 시간을 커버), 관대한 cold-start 폴백(스냅샷 6개 미만이면 사실상 무전략 진입), 영구 one-shot(재진입 금지) 문제를 모두 수정해 반영했다.

## 3. 진입/청산 규칙 정밀 명세

### 3.1 진입 (모두 충족해야 매수)

| # | 조건 | 파라미터 (기본값) | 구현 |
|---|------|-------------------|------|
| 1 | 유동성 | liquidity >= $10,000 | Gamma `liquidity` |
| 2 | (옵션) 24h 거래량 | volume24hr >= $0 (0 = 비활성) | Gamma `volume24hr` |
| 3 | 시간 윈도우 | 24h <= 해결까지 <= 240h | `endDate` 기준 |
| 4 | YES 롱샷 밴드 | 0.05 <= YES <= 0.25 (양끝 포함) | `outcomePrices[0]` |
| 5 | 윈도우 유효성 | 24h 윈도우에 >= 5 포인트·커버리지 >= 12h이고, 6h spike 윈도우에도 별도로 >= 3 포인트·커버리지 >= 3h. 하나라도 invalid면 **진입 금지** | `is_window_valid` |
| 6 | 사건 진행 배제 ① | YES 24h 변화 <= +0.02 (윈도우 최고(最古) 스냅샷 대비) | `change_from_oldest` |
| 7 | 사건 진행 배제 ② | 최근 6h YES 급등 < 0.05 (6h 윈도우 저점 대비) | `rise_from_low` |
| 8 | 재진입 정책 | HOLDING 없음 AND 마지막 청산/skip 후 24h 경과 | `is_in_reentry_cooldown` |

진입 시 **NO 토큰(`clobTokenIds[1]`)을 매수**한다. NO 가격은 0.75~0.95 구간. 주문은 midpoint 가격 GTC limit BUY, `POLYBOT_BUY_AMOUNT`(USDC) ÷ 가격 = 주수 (최소 5주).

trader의 매수 직전 재검증: NO midpoint > 0.95(밴드 상단)면 `rapid_jump`로 skip 기록(쿨다운 24h), < 0.75면 이번 사이클만 skip.

### 3.2 청산 (우선순위 순, trailing 없음)

| 순위 | 조건 | 파라미터 (기본값) | exit_reason |
|------|------|-------------------|-------------|
| 1 | P&L <= -10% | `stop_loss_percent` -0.10 | `stop_loss` (NO 하락 = YES 급등 = 사건 발생 신호) |
| 2 | 현재가 >= min(매수가×1.06, **0.99**) | `take_profit_percent` +0.06 | `take_profit` |
| 3 | 해결까지 < 2h | `exit_hours` 2 | `time_exit` |
| - | midpoint 조회 불가 + 해결 24h 경과 | - | `resolved_unredeemed` (status=EXPIRED, 수동 redeem 필요) |

NO는 만기 수렴이 본질이므로 TP가 +6%로 작아도 회전율로 수익을 쌓는다. 매수가 0.934 이상이면 원목표가가 0.99를 넘으므로 0.99 도달 시 익절(캡).

## 4. 파라미터 · env var 표

우선순위: **env > config.yaml > 코드 기본값**

| env | yaml 키 | 기본값 | 의미 |
|---|---|---|---|
| `POLYBOT_BUY_AMOUNT` | `trading.buy_amount_usdc` | 5.0 | 1회 매수 USDC |
| `POLYBOT_MIN_LIQUIDITY` | `trading.min_liquidity` | 10000 | 최소 유동성 $ |
| `POLYBOT_MIN_VOLUME_24H` | `trading.min_volume_24h` | 0 | 최소 24h 거래량 $ (0 = 비활성) |
| `POLYBOT_TAKE_PROFIT` | `trading.take_profit_percent` | 0.06 | 익절 % (목표가 0.99 캡) |
| `POLYBOT_STOP_LOSS` | `trading.stop_loss_percent` | -0.10 | 손절 % |
| `POLYBOT_MAX_POSITIONS` | `trading.max_positions` | -1 | 최대 동시 포지션 (-1 무제한) |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | `trading.reentry_cooldown_hours` | 24 | 재진입 쿨다운 |
| `POLYBOT_HISTORY_BACKFILL` | `trading.history_backfill` | true | prices-history 백필 |
| `POLYBOT_EXCLUDED_CATEGORIES` | `trading.excluded_categories` | "" | 제외 카테고리 (comma 구분, 빈 값 = 비활성) |
| `POLYBOT_YES_MIN` | `trading.strategy.yes_min` | 0.05 | YES 롱샷 밴드 하한 |
| `POLYBOT_YES_MAX` | `trading.strategy.yes_max` | 0.25 | YES 롱샷 밴드 상한 |
| `POLYBOT_YES_RISE_BLOCK_24H` | `trading.strategy.yes_rise_block_24h` | 0.02 | 24h YES 상승 차단 임계 |
| `POLYBOT_YES_SPIKE_BLOCK_6H` | `trading.strategy.yes_spike_block_6h` | 0.05 | 6h YES 급등 차단 임계 |
| `POLYBOT_RISE_LOOKBACK_HOURS` | `trading.strategy.rise_lookback_hours` | 24 | 24h 게이트 lookback |
| `POLYBOT_SPIKE_LOOKBACK_HOURS` | `trading.strategy.spike_lookback_hours` | 6 | 6h 게이트 lookback |
| `POLYBOT_ENTRY_HOURS_MIN` | `trading.time_based.entry_hours_min` | 24 | 진입 최소 잔여 시간 |
| `POLYBOT_ENTRY_HOURS_MAX` | `trading.time_based.entry_hours_max` | 240 | 진입 최대 잔여 시간 |
| `POLYBOT_EXIT_HOURS` | `trading.time_based.exit_hours` | 2 | 해결 N시간 전 청산 |
| `LOG_LEVEL` | - | INFO | 로그 레벨 (`--verbose`가 최우선) |

## 5. 이 전략이 실패하는 경우 (솔직한 리스크)

1. **꼬리 리스크 비대칭**: NO @ 0.85 매수의 최대 이익은 +0.14, 최대 손실은 -0.85. 사건이 실제로 일어나면 한 방에 익절 여러 건을 지운다. 손절 -10%가 이론상 방어선이지만, 진짜 뉴스는 한 사이클(3~5분) 안에 -10%를 건너뛰어 갭으로 폭락할 수 있다 — 손절가 체결 보장이 없다.
2. **롱샷이 항상 과대평가인 것은 아니다**: 정보 비대칭이 있는 시장(내부자성 매수가 YES를 끌어올리는 중)에서는 YES 5~25%가 "싼 복권"이 아니라 "스마트 머니의 초기 진입"일 수 있다. 24h/6h 상승 차단 게이트가 이를 거르지만, 게이트 임계(+0.02/+0.05) 아래로 서서히 오르는 정보는 통과한다.
3. **밴드 하단의 스프레드 함정**: YES 0.05 근처 시장은 NO가 0.95로 TP 여지가 거의 없고(0.99 캡까지 +4.2%), 호가 스프레드와 유동성 부족이 수익률을 잠식한다.
4. **바이너리 뉴스 시장**: "X가 일어날까"가 단일 발표(판결, 회담 등)에 좌우되는 시장은 theta 수확이 아니라 이벤트 도박이 된다. 발표 시각이 알려진 시장은 사람이 목록으로 걸러내는 편이 낫다.
5. **집단 상관 리스크**: 같은 이벤트(예: 한 인물의 여러 파생 시장)에서 여러 NO 포지션을 들면 사실상 한 베팅이다. 현재 구현은 이벤트 단위 노출 제한이 없다.
6. **체결 가정**: GTC limit 주문 접수 = 체결로 가정한다(§8 참고). 미체결 주문이 남으면 실제 포지션과 DB가 어긋난다.

## 6. A/B 검증 방법

1. **시뮬레이션 (1~2주)**: `uv run python main.py run --simulate --job fig-sim`을 Jenkins 3~5분 주기로 실행. `data/fig-sim/trades_sim.db` 축적.
2. **소액 실전 (4주)**: `POLYBOT_BUY_AMOUNT`를 최소 수준(밴드 특성상 5주 최소 주문 충족 필요, NO 0.95 기준 약 $4.75 이상)으로 실행.
3. **판단 기준**: 4주 경과 + **30건 이상** 완결 거래에서
   - 승률 >= 75% (TP+time_exit 비중)
   - 평균 손익 > 0 (stop_loss 1건이 TP 몇 건을 지우는지 확인: 손익비가 약 1:1.7 불리한 구조이므로 승률이 관건)
   - `exit_reason` 분포에서 `stop_loss`가 25% 초과하면 게이트 임계 재조정 또는 중단.
4. cherry(같은 편향의 favorite 쪽)와 동일 기간 수익률 비교 → 편향의 어느 쪽이 더 수확 가능한지 판단.

## 7. 베리에이션 아이디어

- **fig-1 (보수)**: `POLYBOT_YES_MAX=0.15` — 더 깊은 롱샷만. TP 여지가 줄어드는 대신 사건 발생 확률도 낮아진다.
- **fig-2 (단기 집중)**: `POLYBOT_ENTRY_HOURS_MAX=72` — 해결 3일 이내만 진입. theta가 가장 가파른 구간만 수확.
- **fig-3 (게이트 강화)**: `POLYBOT_YES_RISE_BLOCK_24H=0.00`, `POLYBOT_YES_SPIKE_BLOCK_6H=0.03` — YES가 조금이라도 오르는 중이면 진입 금지. 진입 수는 줄고 안전도는 올라간다.

각각 `--job fig-1` 식으로 job을 분리하면 DB가 분리되어 동시 A/B 운영이 가능하다.

## 8. 알려진 구현 한계

- **GTC limit 체결 가정**: midpoint 가격의 GTC limit 주문이 접수되면 체결된 것으로 간주하고 즉시 HOLDING/COMPLETED 기록한다. 실제 체결 확인 로직이 없다. cherry와의 A/B 비교 가능성을 위해 의도적으로 유지한 패턴이다 (스펙 §3.8).
- **스냅샷 의존 cold start**: 24h 게이트는 자체 축적 스냅샷에 의존한다. 신규 배포 직후에는 CLOB `/prices-history` 백필로 메우지만, 이 endpoint는 공식 문서화가 얇아 언제든 응답이 바뀔 수 있다. 백필 실패 시 "데이터 부족 = 진입 금지"로 안전하게 동작하며 스냅샷 축적(약 하루)으로 자연 회복된다.
- **24h 변화의 근사**: 윈도우 유효성 기준이 "커버리지 >= 12h"이므로, "24h 변화"가 실제로는 12~24h 변화일 수 있다 (보수적 방향의 근사).
- **YES 가격 기준 스냅샷**: 스냅샷/게이트는 전부 YES 가격 단위다. NO 가격은 1-YES 근사가 아니라 `outcomePrices[1]` 실측값을 쓰지만, 진입 밴드 판정은 YES 기준이다.
- **이벤트 단위 노출 제한 없음**: 같은 이벤트의 파생 시장 다수에 동시 진입할 수 있다 (§5-5).
