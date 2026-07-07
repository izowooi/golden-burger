# golden-mango 회고(포스트모템) 가이드

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 대상 봇: **Patience Premium** — "거의 확실한" 계약의 settlement discount(자본 잠김 할인)를 연환산 캐리 수익률 단일 수식으로 걸러 수확하는 전략.

## 0. 복붙용 회고 프롬프트

```text
/Users/izowooi/git/t1/docs/retro/golden-mango.md 를 읽고 §3(실적 SQL)~§5(표본 주의)를 실행한 뒤,
§6 표 형식으로 파라미터 교정안을 제시해줘.

- 봇 자기 DB는 §2의 find 명령으로 찾아라 (job명은 바뀔 수 있음).
- 반사실(what-if) 가격 시계열은 mango 자기 스냅샷(보존 7일)이 아니라
  nectarine DB의 market_snapshots(60일 공용 아카이브)를 써라 (§2).
- §4(a) 청산 스윕(SL / TP캡 / exit_hours / 만기 redeem 보유)과
  §4(b) 노브 스윕(yield_min / entry_hours_max / momentum min_change / prob 밴드)을
  격자대로 돌리고, 격자별 총 P&L·거래수·승률 표를 제시해라.
- 캐리 수식 y = ((1-p)/p) × (8760/h)는 hours_left에 의존한다. 아카이브에는 end_date가
  없으므로 gamma API로 condition_id별 endDate를 조인하거나 §4(b)의 프록시를 써라.
- 같은 이벤트에서 파생된 시장들(negRisk 다후보, 시리즈물)은 이벤트 단위로 묶어 집계하고,
  배포 초기 cold-start/백필 코호트는 정상 신호 코호트와 분리해서 봐라 (§5).
- 이 전략은 고승률·소수익 구조다: 승률만 보지 말고 최악 거래 손실 vs 평균 수익 비율,
  stop_loss/EXPIRED 건의 tail 손실 기여를 반드시 별도 보고해라 (§5).
- NO-favorite 포지션의 아카이브 가격은 1-YES 근사임을 결과에 명시해라.
- 운영 env는 repo에 없다. Jenkins job 설정의 export 블록(실키 제외)을 아래에 붙여넣었다:
  [여기에 Jenkins env export 블록 붙여넣기 — POLYMARKET_PRIVATE_KEY 등 실키는 제외]
```

## 1. 전략 요약

**논지**: 예측시장 참가자는 자본이 잠기는 것을 싫어해(시간선호·조급함) "거의 확실한" 계약조차 액면(1.00)보다 싸게 판다 — settlement discount (arXiv 2605.31431 등, `STRATEGY.md` §2). 여기에 favorite-longshot bias의 favorite 저평가가 겹친다. mango는 "확률이 틀렸다"가 아니라 "대중의 조급함이 만든 할인"을 봇의 무한한 인내로 사들인다. 진입 판단은 단일 수식이다:

```
연환산 캐리 수익률 y = ((1 - p) / p) × (8760 / hours_left),  진입 ⇔ y >= yield_min (기본 2.0 = 연 200%)
```

시간과 확률을 각각 조건으로 두지 않고 y 하나로 환산하므로 시장이 스스로 진입 frontier를 형성한다: 같은 p=0.98이라도 24h 남으면 y=7.45로 진입, 336h 남으면 y=0.53으로 걸러진다.

**진입 (모두 AND, favorite 토큰 기준 가격 p)**: liquidity >= $20k (volume 필터는 기본 off) → favorite 토큰 선택 (YES/NO 중 높은 쪽, `--yes-only` 시 YES만) → 6h < hours_left <= 336h → p ∈ [0.85, 0.985] → y >= 2.0 → 6h 윈도우 유효(포인트 >= 5, 커버리지 >= 3h; invalid면 CLOB `/prices-history` 백필, 그래도 invalid면 진입 금지) → 모멘텀 가드: 최근 6h favorite 변화 >= -0.02 (급락 중 진입 금지 — 급락은 오라클 분쟁/반전 뉴스 신호일 수 있음) → HOLDING 없음 + 마지막 매도/skip 후 24h 경과. 매수 직전 CLOB midpoint로 캐리 조건 재검증(prob_max 초과 급등 시 `rapid_jump` skip). `entry_reason` 형식: `carry_y{y:.2f}_{h:.1f}h_mom{±.3f}`.

**청산 (우선순위 순, 매 사이클, `signals.evaluate_exit`)**: ① 손절 P&L <= -6% (`stop_loss`, 수렴 실패 신호) → ② 익절 현재가 >= min(진입가×(1+9.99), 0.99) = 사실상 **0.99 도달** (`take_profit`, 수렴 완료) → ③ 해결까지 < 2h (`time_exit`, 마지막까지 캐리 수확). **trailing stop 없음** — 0.99 수렴 보유가 본질이므로 조기 익절을 하지 않는다 (`max_price` 컬럼은 회고용 기록일 뿐 청산 판정에 쓰지 않음). 해결된 시장은 midpoint 조회 실패 + `market_end_date` 24h 경과 시 EXPIRED (`resolved_unredeemed`, realized_pnl NULL, 수동 redeem 필요).

### 파라미터 표 (env 이름은 `src/polybot/config.py`, 기본값은 `config.yaml` 실제 값)

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| carry.yield_min | `POLYBOT_YIELD_MIN` | 2.0 | 연환산 캐리 허들 (2.0 = 연 200%) — 유일한 실질 필터 |
| carry.prob_min | `POLYBOT_PROB_MIN` | 0.85 | favorite 가격 하한 |
| carry.prob_max | `POLYBOT_PROB_MAX` | 0.985 | favorite 가격 상한 (스프레드/수수료 여유) |
| carry.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | 6 | 잔여 시간이 이 값 이하이면 진입 금지 |
| carry.entry_hours_max | `POLYBOT_ENTRY_HOURS_MAX` | 336 | 잔여 시간이 이 값 초과이면 진입 금지 (14일) |
| momentum_gate.lookback_hours | `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | 모멘텀 가드 윈도우 (h) |
| momentum_gate.min_change | `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.02 | favorite 6h 변화 하한 (미만 급락 시 진입 배제) |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 9.99 | 사실상 미사용 — 목표가 min(buy×(1+tp), 0.99) 캡만 작동 |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.06 | 손절 % (수렴 실패 신호) |
| exit_hours | `POLYBOT_EXIT_HOURS` | 2 | 해결까지 이 시간 미만이면 청산 |
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0 = 필터 비활성, 조용한 near-certain 시장이 먹이) |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한; 실전은 제한 권장, STRATEGY.md §5 오라클 리스크) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입/skip 쿨다운 (h) |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | true | CLOB `/prices-history` 백필 |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | [] (비활성) | 제외 카테고리 (comma 구분) |
| yes_only_mode | `POLYBOT_YES_ONLY` | false | Yes(index 0) 토큰만 매수 (CLI `--yes-only`가 우선) |
| (로그) | `LOG_LEVEL` | INFO | 로그 레벨 |

코드 상수(env 아님): 윈도우 유효성 `min_points=5`, `min_coverage=0.5` (`signals.py DEFAULT_*`), 익절 목표가 캡 0.99 (`TAKE_PROFIT_PRICE_CAP`), 최소 주문 5주 (`trader.py MIN_ORDER_SIZE`), EXPIRED 판정 유예 24h (`RESOLVED_UNREDEEMED_GRACE_HOURS`), 경계 오차 `EPSILON=1e-9`.

**운영 env는 repo에 없다.** 실제 운용값은 Jenkins job 설정의 export 블록이 단일 소스다 — 회고 시 반드시 §0 프롬프트에 붙여넣을 것 (README 예시처럼 `POLYBOT_MAX_POSITIONS=10` 등 기본값과 다르게 운용 중일 가능성이 높다).

## 2. 데이터 위치와 스키마

### 자기 DB

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-mango/data*" -name "trades.db" 2>/dev/null
# 시뮬레이션은 같은 디렉터리의 trades_sim.db (별도 파일)
# 같은 디렉터리에 trades_YYYY-MM.csv(완료 거래 월별 CSV, 시그널 컬럼 포함),
# logs/YYYYMMDD.log(사이클 로그)도 있다
```

job명은 바뀔 수 있으니 반드시 find로 찾는다 (알려진 슬롯 매핑은 다른 봇 기준 2026-07-07: date=polybot-red, elderberry=polybot-cherry 워크스페이스, nectarine=polybot-fox, honeydew=polybot-eco — mango 슬롯은 find로 확인). Jenkins 콘솔/파일 로그에는 사이클마다 `제외 사유 요약 - reason: count` 한 줄이 남는다 — 어느 게이트(`yield_below_min_*`, `too_early_*`, `too_late_*`, `price_out_of_band_*`, `window_invalid`, `momentum_down_*`, `insufficient_momentum_data`, `no_end_date` 등)가 스캔 병목인지 파악할 때 사용.

테이블 (`src/polybot/db/models.py` 기준, SQL은 이 컬럼만 사용):

- **trades**: `condition_id`, `market_slug`, `question`, `outcome`("Yes"/"No" — favorite 방향), `token_id`, `buy_price`, `buy_amount`(USDC), `buy_shares`, `buy_order_id`, `buy_timestamp`, `buy_probability`, `sell_price`, `sell_shares`, `sell_order_id`, `sell_timestamp`, `sell_probability`, `realized_pnl`(USD 절대액), `status`, `entry_reason`(예: `carry_y4.21_48.0h_mom+0.005`), `exit_reason`, `strategy_name`("mango"), `mode`("live"/"sim"), **`volume_24h_at_buy`**, **`carry_yield_at_buy`**(진입 판정 y), **`momentum_6h_at_buy`**(진입 시점 6h favorite 변화), **`carry_yield_at_exit`**(청산 시점 midpoint 기준 재계산 y, 계산 불가 시 NULL), `max_price`(진입 후 최고가 — 회고 경로 분석용, 청산 판정 미사용), `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`, `market_tags`, `created_at`, `updated_at`
  - `status`는 **enum 이름 문자열**로 저장: `PENDING_BUY` / `HOLDING` / `PENDING_SELL` / `COMPLETED` / `SKIPPED` / `EXPIRED`
  - `exit_reason` 값 (trader.py/signals.py): `stop_loss`, `take_profit`, `time_exit`, `resolved_unredeemed`(EXPIRED 전용)
  - `buy_price`는 **favorite 토큰 기준** 가격 (NO-favorite이면 NO 토큰 가격). `buy_probability`도 동일 값. **token_index 컬럼은 없다** — 방향은 `outcome`으로 판별 ("Yes"=index 0 / "No"=index 1).
  - 재진입 허용 전략이라 `condition_id`는 unique가 아니다 (쿨다운 24h 후 새 row).
- **market_snapshots**: `condition_id`, `probability`(**항상 YES 가격** — favorite이 NO면 signals가 부호를 뒤집음), `liquidity`, `volume_24h`, `timestamp`(naive UTC)
- **skipped_markets**: `condition_id`, `reason`("rapid_jump" 등), `skipped_at` — timestamp 기반 쿨다운용

모든 DateTime은 **naive UTC**.

### 중앙 가격 아카이브 (반사실 분석용) — 필수

mango는 자체 market_snapshots를 갖지만(liq >= $20k 유니버스) **보존이 7일뿐이다** (`bot.py`: `max(7, ceil(lookback_hours×3/24))` = max(7, 1) = 7일 cleanup). 한 달 회고의 what-if 가격 시계열로는 부족하므로 **nectarine DB의 market_snapshots를 공용 아카이브로 쓴다**:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브: honeydew DB (liq >= $15k, 60일, job=polybot-eco)
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존**. 모든 봇이 같은 gamma sweep(가장 오래된 활성 시장 ~2100개)을 스냅샷하므로 mango 유니버스(liq >= $20k, volume 필터 off)는 아카이브의 부분집합이다 — 아카이브 쪽 `liquidity >= 20000` 필터만으로 재현.
- **NO 토큰 가격은 1-YES 근사** (스프레드 무시 근사임을 결과에 명시).
- 시장이 해결되면 스냅샷이 끊긴다 → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1 (redeem 가정).
- mango의 최근 7일 자체 스냅샷은 `momentum_6h_at_buy` 재검산·아카이브 대조(sanity check)에만 쓴다.

## 3. 실적 분석 SQL (mango trades.db에 그대로 실행)

```sql
-- 3.0 상태 분포 (EXPIRED 잔여분·미청산 확인)
SELECT status, COUNT(*) AS n FROM trades GROUP BY status;

-- 3.1 완결 거래 총괄: 건수 / 승률 / 총·평균 P&L / 평균 수익률
SELECT COUNT(*)                                                       AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd,
       ROUND(AVG(realized_pnl), 4)                                    AS avg_pnl_usd,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0;

-- 3.1b 중앙 수익률 (SQLite median)
SELECT ROUND(sell_price / buy_price - 1, 4) AS median_ret
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
ORDER BY sell_price / buy_price - 1
LIMIT 1 OFFSET (SELECT (COUNT(*) - 1) / 2 FROM trades
                WHERE status = 'COMPLETED' AND buy_price > 0);

-- 3.1c tail 손실 체크 (STRATEGY.md §6: 최악 거래 손실이 평균 수익의 20배 이내인가)
SELECT ROUND(MIN(realized_pnl), 4)                                    AS worst_pnl_usd,
       ROUND(AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END), 4) AS avg_win_usd,
       ROUND(-MIN(realized_pnl)
             / AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END), 1) AS worst_to_avgwin_ratio
FROM trades WHERE status = 'COMPLETED';

-- 3.2 exit_reason별 분해 (STRATEGY.md §6: stop_loss 비중 <= 15%가 합격선)
SELECT exit_reason,
       COUNT(*)                                                       AS n,
       ROUND(COUNT(*) * 1.0 / (SELECT COUNT(*) FROM trades
                               WHERE status = 'COMPLETED'), 3)        AS share,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(realized_pnl), 4)                                    AS avg_pnl_usd,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY exit_reason ORDER BY n DESC;

-- 3.3 진입가 버킷별 성과 (prob 밴드 [0.85, 0.985] 교정 근거)
SELECT CASE WHEN buy_price < 0.90 THEN '0.85-0.90'
            WHEN buy_price < 0.95 THEN '0.90-0.95'
            ELSE '0.95-0.985' END                                     AS price_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY price_bucket ORDER BY price_bucket;

-- 3.4 carry_yield_at_buy 버킷별 성과 (yield_min 2.0 교정 근거 — STRATEGY.md §6 구간)
SELECT CASE WHEN carry_yield_at_buy < 4  THEN 'y 2-4'
            WHEN carry_yield_at_buy < 8  THEN 'y 4-8'
            ELSE 'y 8+' END                                           AS yield_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
  AND carry_yield_at_buy IS NOT NULL
GROUP BY yield_bucket ORDER BY yield_bucket;
-- 해석: y 8+ 버킷의 승률이 유의하게 낮으면 "높은 y = 시장이 아는 진짜 위험의 프리미엄"
-- 신호 (STRATEGY.md §5.3 선택 편향) → yield 상한 도입 검토. 반대면 y_min 상향이 유리.

-- 3.5 진입 시점 잔여시간 버킷 (entry_hours 창 [6, 336] 교정 근거)
SELECT CASE WHEN hours_until_resolution_at_buy <= 24  THEN 'h 6-24'
            WHEN hours_until_resolution_at_buy <= 72  THEN 'h 24-72'
            WHEN hours_until_resolution_at_buy <= 168 THEN 'h 72-168'
            ELSE 'h 168-336' END                                      AS hours_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
  AND hours_until_resolution_at_buy IS NOT NULL
GROUP BY hours_bucket ORDER BY MIN(hours_until_resolution_at_buy);
-- 해석: 장기 버킷(72h+)의 성과가 나쁘면 A-2(단기 집중, ENTRY_HOURS_MAX=72) 변형 근거.

-- 3.6 모멘텀 가드 버킷 (min_change -0.02 교정 근거)
SELECT CASE WHEN momentum_6h_at_buy < -0.01 THEN 'mom -0.02~-0.01'
            WHEN momentum_6h_at_buy < 0     THEN 'mom -0.01~0'
            WHEN momentum_6h_at_buy < 0.01  THEN 'mom 0~+0.01'
            ELSE 'mom +0.01+' END                                     AS mom_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
  AND momentum_6h_at_buy IS NOT NULL
GROUP BY mom_bucket ORDER BY MIN(momentum_6h_at_buy);

-- 3.7 방향별 (YES-favorite vs NO-favorite) — NO 시장 해결 기준 모호성 리스크 확인 (A-3 변형 근거)
SELECT outcome, COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY outcome;

-- 3.8 보유시간 분석 (exit_reason별 평균/최소/최대 보유 h — 자본 회전율 체크)
SELECT exit_reason, COUNT(*) AS n,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h,
       ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_hold_h,
       ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_h
FROM trades
WHERE status = 'COMPLETED' AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason ORDER BY n DESC;

-- 3.8b 실현 연환산 수익률 vs 진입 시점 y (캐리 논지 검증의 핵심)
SELECT ROUND(AVG(carry_yield_at_buy), 2)                              AS avg_y_at_buy,
       ROUND(AVG((sell_price / buy_price - 1)
             * (8760.0 / ((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24))), 2)
                                                                      AS avg_realized_annualized
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0
  AND carry_yield_at_buy IS NOT NULL
  AND (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 > 0.5;
-- 해석: 실현 연환산이 y_at_buy에 크게 못 미치면 (i) tail 손실이 캐리를 먹었거나
-- (ii) time_exit(0.99 미도달 상태 2h 전 청산)가 마지막 수렴분을 놓치는 것.

-- 3.9 미해결 자산: HOLDING 현황 + EXPIRED(수동 redeem 필요, realized_pnl NULL → 총계에서 빠짐!)
SELECT id, condition_id, outcome, buy_price, buy_timestamp, status, exit_reason,
       hours_until_resolution_at_buy, market_end_date
FROM trades WHERE status IN ('HOLDING', 'EXPIRED') ORDER BY buy_timestamp;

-- 3.10 주간 진입 빈도 (신호 빈도 체크 — 캐리 기회가 특정 주에 몰렸는지)
SELECT strftime('%Y-%W', buy_timestamp) AS week, COUNT(*) AS entries
FROM trades WHERE buy_timestamp IS NOT NULL
GROUP BY week ORDER BY week;

-- 3.11 교차 봇 비교 (선택): cherry/date DB를 ATTACH해 같은 고확률 구간 성과 비교
-- 주의: cherry(구세대 봇) trades에는 strategy_name 컬럼이 없다 → 리터럴로 대체.
--       date 등 회고 로깅 표준(§A) 적용 봇은 strategy_name 컬럼을 그대로 SELECT 가능.
-- ATTACH DATABASE '<cherry trades.db 경로>' AS cherry;
-- SELECT strategy_name, COUNT(*), ROUND(AVG(sell_price/buy_price-1),4)
-- FROM (SELECT strategy_name, sell_price, buy_price FROM trades WHERE status='COMPLETED'
--       UNION ALL
--       SELECT 'cherry' AS strategy_name, sell_price, buy_price FROM cherry.trades
--       WHERE status='COMPLETED')
-- GROUP BY strategy_name;
```

주의: `realized_pnl`은 USD 절대액(= (sell_price - buy_price) × buy_shares), 수익률은 `sell_price/buy_price - 1`로 계산한다. EXPIRED 거래는 realized_pnl이 NULL이라 3.1~3.8 집계에서 빠진다 — 3.9로 반드시 별도 확인하고, redeem 결과(favorite 적중이면 1.0, 뒤집혔으면 0)를 수동 반영해 총 P&L을 보정할 것. **mango는 EXPIRED 1건의 전액 손실이 수십 건의 캐리 수익을 지울 수 있는 구조라 이 보정이 결론을 바꿀 수 있다.**

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

공통 준비: mango `trades.db`의 각 거래(COMPLETED + HOLDING + EXPIRED)에 대해, nectarine 아카이브에서 `condition_id`로 스냅샷 시계열을 조회해 붙인다.

```sql
-- 아카이브 DB에서 (Python sqlite3로 실행, ? 바인딩)
SELECT timestamp, probability, liquidity, volume_24h
FROM market_snapshots
WHERE condition_id = ? AND timestamp >= ?   -- buy_timestamp 이후
ORDER BY timestamp;
```

- favorite 토큰 가격: `outcome = 'Yes'`면 `probability` 그대로, `'No'`면 `1 - probability` (**1-YES 근사**).
- 시계열이 끊긴 시장 = 해결됨. 최종가: 실제 청산됐으면 `trades.sell_price`, 아니면 마지막 스냅샷이 0.95+/0.05- 쪽으로 수렴했는지로 승패 판정 후 1/0 (redeem 가정) — 판정 애매하면 gamma API로 결과 확인.
- **hours_left 재구성이 필수다**: 캐리 수식과 time_exit 모두 잔여시간에 의존한다. 자기 거래분은 `trades.market_end_date`를 조인하면 되고(스냅샷 timestamp와의 차 = hours_left), §4(b)의 가상 진입은 gamma API endDate 조인 또는 프록시가 필요하다.

### (a) 청산 스윕 — SL / TP캡 / exit_hours / 만기 보유 격자

각 거래의 favorite 가격 시계열(5분 간격)을 buy_timestamp부터 시간순으로 걷고, **trader.py와 동일한 우선순위**로 첫 트리거에서 청산한다:

1. `stop_loss`: (p / buy_price - 1) <= SL
2. `take_profit`: p >= TP_CAP (buy×(1+9.99)는 항상 캡 초과이므로 캡 자체가 노브)
3. `time_exit`: `trades.market_end_date` - timestamp < EXIT_H
4. 시계열 종료(해결)까지 미청산이면 redeem 가정 최종가(1/0) 적용

격자 (현행: SL -0.06 / TP_CAP 0.99 / EXIT_H 2h / trailing 없음):

| 노브 | 격자 |
|---|---|
| SL (`POLYBOT_STOP_LOSS`) | -0.03, **-0.06**, -0.10, off(만기까지 보유) |
| TP_CAP (코드 상수 `TAKE_PROFIT_PRICE_CAP`; 낮추기는 `POLYBOT_TAKE_PROFIT`로 불가, 코드 수정 필요) | 0.985, **0.99**, 0.995, off(redeem까지 보유) |
| EXIT_H (`POLYBOT_EXIT_HOURS`) | 0(redeem 가정), 1, **2**, 6 |
| trailing (현행 없음 — 전략 논지와 상충하지만 정량 확인용) | off(**현행**), 0.03, 0.05 |

풀 격자는 4×4×4×3 = 192조합. 계산이 부담되면 현행 기준 **one-at-a-time** 스윕(4+4+4+3 = 15조합) 먼저, 상위 조합만 교차 확인. 출력: 격자별 `총 P&L / 거래수 / 승률 / 평균 보유 h / exit_reason 분포` 표.

해석 포인트 (mango 특화):

- **"SL off + redeem 보유"가 현행보다 좋으면** SL -6%가 노이즈에 털리는 것 (near-certain 구간에서 -6%는 0.95→0.893 수준의 흔들림) → SL 완화 근거. 반대로 SL off에서 전액 손실이 튀어나오면 SL이 오라클 분쟁 방어로 작동한 것 → 유지/강화.
- **EXIT_H=0(만기 redeem)이 현행보다 좋으면** 마지막 2h의 캐리(0.99→1.00)를 버리고 있는 것. 단 redeem 가정은 수동 redeem 운영 부담과 EXPIRED 리스크를 동반함을 명시.
- trailing이 P&L을 올리면 "수렴 보유" 논지 자체를 재검토할 신호 — 단 이 경우 mango는 cherry/date와의 차별점이 흐려지므로 §6에서 신뢰도를 보수적으로.

### (b) 전략 고유 노브 스윕 — 진입 규칙 재생

핵심 노브: **yield_min**(`POLYBOT_YIELD_MIN`, 유일한 실질 필터), **entry_hours_max**(`POLYBOT_ENTRY_HOURS_MAX`, 오라클 노출 시간), **momentum min_change**(`POLYBOT_MOMENTUM_MIN_CHANGE`), 보조로 **prob 밴드**(`POLYBOT_PROB_MIN`/`POLYBOT_PROB_MAX`).

재생 방법 — 아카이브 전체(60일)에서 mango 유니버스를 근사해 가상 진입을 생성한다:

1. 아카이브에서 `liquidity >= 20000`인 (condition_id, timestamp) 지점만 후보로 (mango는 volume 필터 off).
2. 평가 시점은 1시간 간격으로 다운샘플 (5분 전부 돌리면 과도).
3. **hours_left 결정** — 캐리 수식의 입력이므로 생략 불가:
   - 우선: gamma API `GET /markets?condition_ids=...`로 condition_id별 `endDate`를 받아 조인.
   - 프록시(오프라인): 시장의 **마지막 스냅샷 timestamp를 해결 시각으로 가정**하고 hours_left = last_ts - eval_ts로 계산 (사후 정보임을 명시; 조기 해결 시장에서 hours_left가 과소 추정되어 y가 과대 계산될 수 있음).
4. 각 평가 시점에서 `signals.py`의 `evaluate_entry`와 동일 로직 적용 (mango repo를 import해서 쓰면 로직 복제 오류가 없다 — `Snap(timestamp, probability)`로 변환해 넘기면 됨):
   - favorite 결정: YES 가격 p_yes >= 0.5면 favorite=YES(가격 p_yes), 아니면 NO(가격 1-p_yes, **1-YES 근사**)
   - `check_carry_entry`: 6h < hours_left <= entry_hours_max, p ∈ [prob_min, prob_max], y >= yield_min
   - 6h 윈도우 유효성 (포인트 >= 5, 커버리지 >= 3h — 아카이브는 5분 간격이라 사실상 통과)
   - `check_momentum_gate`: favorite 6h 변화 >= min_change
5. 시장당 재진입 쿨다운 24h + "보유 중 중복 진입 금지"를 적용해 가상 포지션 시퀀스 생성. `POLYBOT_MAX_POSITIONS`를 유한하게 운용했다면 그 값도 재현 (동시 보유 수 제한이 진입 순서에 의존함을 명시).
6. 각 가상 진입을 (a)의 현행 청산 규칙(SL -0.06 / TP_CAP 0.99 / EXIT_H 2)으로 청산해 가상 P&L 산출.

격자 (현행 굵게):

| 노브 | 격자 |
|---|---|
| yield_min | 1.0, 1.5, **2.0**, 3.0, 4.0(=A-1 보수) |
| entry_hours_max | 72(=A-2 단기 집중), 168, **336** |
| momentum min_change | -0.04, **-0.02**, -0.01, 0.0(상승 중만) |
| prob_min | 0.80, **0.85**, 0.90(=A-1) |
| prob_max | 0.97, **0.985** |

풀 격자 대신 yield_min × entry_hours_max (5×3 = 15조합)를 주 격자로 돌리고, momentum/prob 밴드는 one-at-a-time. 출력: 노브 값별 `가상 진입 수 / 승률 / 총·평균 P&L / 이벤트 단위 유효 n`.

검증 순서: (i) **현행 설정의 가상 진입이 실제 진입과 대략 일치하는지 먼저 확인** (재생 로직 sanity check — `carry_yield_at_buy`/`momentum_6h_at_buy` 컬럼과 mango 자체 스냅샷 7일치로 대조), (ii) "완화했다면(y_min 1.0~1.5) 추가로 잡혔을 진입"의 성과가 기존보다 나쁜지 — 나쁘면 허들이 제 역할, (iii) "강화했다면(y_min 3~4, hours_max 72) 걸러졌을 진입"이 실제 손실 거래를 포함하는지. STRATEGY.md §7의 변형(A-1 보수 / A-2 단기 집중 / A-3 YES 한정)이 이 격자의 특정 조합에 해당한다 — A-3은 §3.7의 outcome별 분해로 대신 평가.

### (c) 데이터 한계 (결과 보고서에 반드시 명시)

- **NO-favorite = 1-YES 근사**: 스프레드 무시. NO 포지션 P&L·가상 진입 판정 모두 실제보다 낙관될 수 있다.
- **체결 가정**: 실봇도 GTC limit @ midpoint 즉시 체결 가정(미체결 추적 없음) — 반사실도 슬리피지/스프레드/미체결을 모델링하지 않는다. 특히 mango는 volume 필터 off라 조용한 시장의 stale midpoint 위험(STRATEGY.md §5.5)이 있어, 반사실 P&L은 상대 비교용이지 절대 수익 예측이 아니다.
- **hours_left 재구성**: 아카이브에 end_date가 없다. 자기 거래분은 `trades.market_end_date`로 정확하지만, §4(b) 가상 진입은 gamma 조인 또는 "마지막 스냅샷 = 해결 시각" 프록시(사후 정보, 조기 해결 시 y 과대 계산)에 의존한다.
- **갭 하락 미해상**: 아카이브는 5분 간격이라 0.9→0.1 갭은 잡히지만, 봇 사이클(3~5분) 사이 실제 체결 가능 가격과는 다르다. SL 스윕의 체결가는 "트리거 시점 스냅샷 가격"으로 가정 — 실제 갭 하락에서는 SL이 그 가격에 못 판다 (STRATEGY.md §5.1).
- **백필 미반영**: 실봇은 cold-start 시 CLOB `/prices-history` 백필을 병합해 모멘텀을 판정하지만 아카이브에는 그 포인트가 없다 — 배포 초기 진입은 재생이 정확히 일치하지 않는다 (§5 코호트 분리).
- **carry_yield_at_exit NULL**: 해결 직후(음수 잔여시간) 등 계산 불가 상황에서 NULL — 집계 시 제외 처리.
- **아카이브 보존 60일**: 회고 시점에서 60일보다 오래된 거래는 시계열이 없다. 4주 회고면 문제없지만 회고가 늦어지면 먼저 아카이브를 백업해둘 것.

## 5. 표본 주의사항

- **고승률·소수익 구조의 착시 — mango 최대 함정**: 캐리 전략은 승률 85%+가 정상이고 tail 손실 1건이 수십 건의 수익을 지운다 (STRATEGY.md §5.1~5.2). 4주간 오라클 분쟁·반전이 한 번도 없었다면 "승률 95%, P&L 양호"는 **tail risk가 아직 실현되지 않았다는 뜻일 뿐 안전하다는 증거가 아니다**. n=30, 무패여도 건당 3% 파산 확률과 통계적으로 구분되지 않는다. §3.1c의 worst/avg-win 비율과 stop_loss·EXPIRED 건의 내용(오라클 분쟁이었나, 노이즈였나)을 정성 확인해 보고서에 별도 섹션으로 남겨라.
- **상관 클러스터**: 같은 이벤트에서 파생된 near-certain 시장들(negRisk 다후보의 "다른 후보 NO"들, 같은 경기/발표에 걸린 시리즈물)은 y 허들을 동시에 넘어 mango가 한꺼번에 진입할 수 있다 — 그리고 오라클 분쟁도 동시에 맞는다. `market_slug` 접두어·`question` 유사도·`market_tags`로 이벤트 단위로 묶고 이벤트 단위 집계를 병기하라. **명목 n != 유효 n** — 같은 이벤트 5개 시장의 5승은 1승에 가깝다.
- **코호트 분리**: ① 배포 직후 cold-start 구간(모멘텀 윈도우가 백필로 채워진 진입 — buy_timestamp가 배포 후 첫 1~2일) + 배포 시점에 이미 y 허들을 넘고 있던 **초기 백로그** 진입과 ② 정상 스냅샷 축적 후 신규 신호 진입을 분리해서 봐라. 초기 백로그는 "그 시점 시황"의 표본이지 전략의 정상 신호 빈도가 아니다.
- **잔여시간 편중**: y 허들은 단기 시장일수록 넘기 쉬워(같은 p면 h가 작을수록 y가 큼) 표본이 h 6~24 버킷에 몰릴 수 있다. §3.5 버킷별 n을 확인하고, 장기 버킷 n이 한 자릿수면 entry_hours_max 교정 제안의 신뢰도를 "낮음"으로.
- **EXPIRED 누락 편향**: realized_pnl NULL인 EXPIRED를 빼고 집계하면 생존 편향이 생긴다. mango에서 EXPIRED는 대부분 "0.99 근처로 수렴했으나 2h 창을 놓친 승리 포지션"일 가능성이 높지만, **뒤집힌 패배 포지션일 수도 있다** — redeem 결과를 반영해 보정하라 (§3.9).
- **금리/시황 환경**: settlement discount 크기는 기회비용 환경에 비례한다 (STRATEGY.md §5.4). 4주 표본의 y 분포(§3.4)가 특정 주에 몰렸다면(§3.10) 그 주의 시황이 성과를 지배했을 수 있다 — 파라미터 일반화에 주의.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| 예: `POLYBOT_YIELD_MIN` | 2.0 | 3.0 | §4(b): y 2~3 구간 가상 진입 18건 승률 72% vs y 3+ 구간 91%, 제외분 P&L -$2.1 | 중간 |
| 예: `POLYBOT_ENTRY_HOURS_MAX` | 336 | 72 | §3.5: h 72+ 버킷 n=9 avg_ret -0.8%, §4(b) 스윕 총 P&L +$1.4 | 낮음 |
| ... | | | | |

라운드 절차:

1. **1차(이번 회고)**: 위 표 확정 → Jenkins job env만 수정 (코드 변경 불필요, env > yaml > 기본값 우선순위. 단 TP_CAP 0.99는 코드 상수라 변경 시 `signals.py` 수정 필요).
2. **2차 테스트(4주)**: 제안값으로 운용. 변경 노브는 최대 2~3개로 제한 — 전부 바꾸면 귀속 불가.
3. 확신이 낮은 제안은 cherry처럼 **기본/변형 병행 슬롯**으로 A/B: 같은 코드, 다른 env의 Jenkins job 2개 (예: 현행 vs STRATEGY.md §7 A-1 보수 `POLYBOT_YIELD_MIN=4.0 + POLYBOT_PROB_MIN=0.90 + POLYBOT_MAX_POSITIONS=5`, 또는 A-2 단기 집중 `POLYBOT_ENTRY_HOURS_MAX=72`). DB는 job명으로 자동 분리(`data/<job>/trades.db`)되어 비교가 깨끗하다.
4. **3차 교정**: 2차 종료 후 이 문서로 재회고 → 표 갱신 → 수렴하면 `POLYBOT_BUY_AMOUNT` 증액 검토. 증액 전 STRATEGY.md §6 판단 기준 충족 확인: 승률 >= 85%, stop_loss 비중 <= 15%, `resolved_unredeemed` 0건, 최악 손실 <= 평균 수익 20배. **증액하더라도 `POLYBOT_MAX_POSITIONS` 유한 유지 + 소액 사이징은 이 전략의 생존 조건이다 (STRATEGY.md §5.1).**

## 7. 기준 정보

- 이 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (t1 monorepo main)
- 전략 문서: `/Users/izowooi/git/t1/golden-mango/STRATEGY.md` (논지·문헌 근거·실패 모드·A/B 변형 아이디어)
- 코드 기준: `golden-mango/src/polybot/config.py`(env 파싱), `db/models.py`(스키마), `strategy/signals.py`(캐리 수식·진입/청산 판정 순수 함수), `strategy/trader.py`(청산 우선순위·EXPIRED 처리)
- 운영 절차 문서: `/Users/izowooi/git/t1/docs/ab-retro-playbook.md`
