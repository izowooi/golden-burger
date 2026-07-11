# golden-lime 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 전략: **Shock Follow** — 6h 내 +0.10 점프 중 거래량 폭증 + 고점 유지가 확인된 "진짜 정보"에만 편승. golden-elderberry(Panic Fade)와 정반대 트리거의 A/B 쌍.

## 0. 복붙용 회고 프롬프트

```text
docs/retro/golden-lime.md 를 읽고 §3~§5를 실행해 §6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.

- 내 DB 위치: find /Users/jongwoopark/.jenkins/workspace -path "*golden-lime/data*" -name "trades.db" 2>/dev/null
  (job명은 바뀔 수 있으니 find로 확인. 시뮬레이션은 trades_sim.db 별도)
- 가격 시계열: lime 자체 market_snapshots는 보존 7일뿐이므로, 반사실 분석은 반드시
  nectarine DB의 market_snapshots(중앙 아카이브, 60일)를 써줘 (§2 참조).
- 상관 클러스터 주의: 같은 이벤트에서 파생된 여러 시장이 동시에 점프해 다중 진입될 수 있다.
  market_tags/question/buy_timestamp(±6h)로 클러스터를 묶어 이벤트 단위로도 집계하고,
  유효 표본 수(n_effective)를 명목 n과 함께 보고해줘.
- 코호트 분리: (1) 운영 초기 스냅샷 축적 전 구간(거래량 게이트는 백필 불가라 vol_mult가
  얕은 윈도우 평균 기반) vs 정상 신호 구간, (2) mode='sim' vs 'live', (3) env 변경 전후.
- A/B 판정: golden-elderberry DB와 교차 비교해 "10%+ 급변은 회귀인가 지속인가,
  거래량이 그 구분자인가" 가설을 §3.7 UNION 쿼리로 판정해줘.
- momentum_death 청산 비율이 50%를 넘으면 STRATEGY.md §6에 따라 jump_min 상향 또는
  전략 폐기를 교정안에 명시해줘.
- Jenkins job 설정의 export 블록(키 제외)을 그대로 붙여넣는다: [여기 붙여넣기]
```

## 1. 전략 요약

**논지**: 대형 서프라이즈 뉴스에 대중은 앵커링된 채 새 정보를 일부만 반영한다(underreaction, PEAD 구조). 노이즈 스파이크는 거래량이 미약하고 고점을 못 지키지만, 진짜 정보는 거래량 폭증 + 고점 유지를 동반한다. 리서치("10%+ 급변은 mean-revert")의 평균적 결론에 대해, 거래량·고점 유지로 구분되는 하위 집단은 지속(drift)한다는 조건부 예외 가설을 실측한다. elderberry(급락 역매수)와 상호 배타적 트리거로 설계된 A/B 쌍.

**진입** (모두 AND, 점프 방향 토큰 기준 가격): liquidity >= $20k, volume24hr >= $10k, 해결까지 >= 24h, 6h 윈도우 유효(>= 5포인트 AND 커버리지 >= 50% — 코드 상수, env 없음), 윈도우 최저가(base) 대비 +0.10 점프, base ∈ [0.15, 0.70], 현재가 <= 0.85, 최근 60분 고점 대비 되돌림 <= 0.02, volume24hr >= 24h 윈도우 평균 x2.0, 재진입 쿨다운 24h 통과. YES 급등 → YES 매수, YES 급락(=NO 급등) → NO 매수.

**청산** (우선순위 순, 매 사이클): 손절 -8% → 익절 +12%(목표가 0.99 캡) → 트레일링(최고가 대비 -6%) → 모멘텀 사망(최근 3h 변화 <= 0) → 시간 청산(해결 12h 전). midpoint 조회 불가 + endDate 24h 경과 시 EXPIRED(수동 redeem).

**파라미터 표** (우선순위: env > config.yaml > 코드 기본값):

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 0.12 | 익절 % (목표가 0.99 캡) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history 백필 |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | [] (비활성) | 제외 카테고리 (comma 구분) |
| trailing_stop.enabled | `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 on/off |
| trailing_stop.percent | `POLYBOT_TRAILING_STOP_PERCENT` | 0.06 | 최고점 대비 하락률 |
| time_based.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | 24 | 진입 최소 잔여 시간 |
| time_based.exit_hours | `POLYBOT_EXIT_HOURS` | 12 | 시간 청산 기준 |
| shock.jump_window_hours | `POLYBOT_JUMP_WINDOW_HOURS` | 6 | 점프 감지 윈도우 (시간) |
| shock.jump_min | `POLYBOT_JUMP_MIN` | 0.10 | 윈도우 최저가 대비 최소 상승폭 |
| shock.base_min | `POLYBOT_BASE_MIN` | 0.15 | 점프 시작 기준가 하한 |
| shock.base_max | `POLYBOT_BASE_MAX` | 0.70 | 점프 시작 기준가 상한 |
| shock.current_max | `POLYBOT_CURRENT_MAX` | 0.85 | 현재가 상한 (러닝룸) |
| shock.hold_window_minutes | `POLYBOT_HOLD_WINDOW_MINUTES` | 60 | 고점 유지 확인 윈도우 (분) |
| shock.max_pullback | `POLYBOT_MAX_PULLBACK` | 0.02 | 고점 대비 최대 되돌림 |
| shock.vol_mult_min | `POLYBOT_VOL_MULT_MIN` | 2.0 | 거래량 확인 배수 |
| shock.death_window_hours | `POLYBOT_DEATH_WINDOW_HOURS` | 3 | 모멘텀 사망 판정 윈도우 (청산) |

env 없는 코드 상수(`signals.ShockParams`): `vol_lookback_hours=24`, `min_window_points=5`, `min_window_coverage=0.5`, TP 캡 0.99, EXPIRED 판정 grace 24h(`trader.RESOLVED_GRACE_HOURS`). 인증: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE`(기본 1).

post-instrumentation 실제 운영값은 `strategy_configs`와 `run_audits`의 config hash/Git cohort가
source of truth다. Jenkins export는 secret을 제거한 current/legacy cross-check로만 사용한다.

## 2. 데이터 위치와 스키마

### 2.1 자기 DB

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-lime/data*" -name "trades.db" 2>/dev/null
```

job명은 바뀔 수 있으니 반드시 find로 찾는다 (2026-07-07 기준 알려진 슬롯 매핑에 lime은 미등재). 시뮬레이션은 같은 폴더의 `trades_sim.db` 별도. Jenkins 콘솔 로그 사본은 `data/<job>/logs/YYYYMMDD.log`.

**`trades`** (핵심 컬럼, models.py 기준):

- 식별: `id`, `condition_id`, `market_slug`, `question`, `outcome`("Yes"/"No"), `token_id`, `token_index`(0=YES, 1=NO), `market_tags`
- 매수: `buy_price`, `buy_amount`, `buy_shares`, `buy_timestamp`, `buy_probability`, `liquidity_at_buy`
- 매도: `sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability`, `realized_pnl`
- 상태: `status` — **SQLAlchemy Enum은 이름으로 저장**되므로 SQL에서는 `'PENDING_BUY' / 'HOLDING' / 'PENDING_SELL' / 'COMPLETED' / 'SKIPPED' / 'EXPIRED'` 를 쓴다
- 전략: `entry_reason`(`"jump_up_0.14"` / `"jump_down_0.12"` 형식 — 방향 태그 + 점프 폭), `exit_reason`(`take_profit` / `stop_loss` / `trailing_stop` / `momentum_death` / `time_exit` / `resolved_unredeemed`), `jump_size_at_buy`, `base_price_at_buy`, `max_price`, `market_end_date`, `hours_until_resolution_at_buy`
- 회고 계약(교차 봇 UNION용): `strategy_name`(상수 `"lime"`), `mode`(`"live"` / `"sim"`), `volume_24h_at_buy`, `vol_mult_at_buy`(매수 시점 거래량 배수 = 현재 volume24hr / 24h 윈도우 평균)

**`market_snapshots`** (`condition_id`, `probability`, `liquidity`, `volume_24h`, `timestamp`): probability는 **항상 YES 가격**. 단, lime의 자체 보존은 **기본 파라미터 기준 7일**(`bot._snapshot_retention_days` = max(7, 24h lookback x3 / 24))이라 **월간 회고의 가격 시계열로는 부족하다 → 아래 중앙 아카이브를 쓴다.** 자체 스냅샷은 최근 1주 검증과 volume_24h 확인용으로만.

**`skipped_markets`** (`condition_id`, `reason`, `skipped_at`): 스캔~주문 사이 추가 급등 시 `"post_scan_jump"` 등. 영구 밴이 아닌 쿨다운 기준 타임스탬프.

### 2.2 중앙 가격 아카이브 (반사실 분석의 가격 시계열)

Gamma keyset cursor를 끝까지 순회한 당시 qualifying universe를 수집하므로,
**nectarine DB의 market_snapshots를 공용 아카이브**로 쓴다. 고정 시장 수 대신 run별
cursor completion과 catalog/snapshot coverage를 확인한다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존**. lime의 게이트(liq >= $20k)는 이 유니버스의 부분집합이므로 아카이브의 `liquidity` 컬럼으로 필터하면 된다.
- 보조 아카이브: honeydew DB (liq >= $15k, 60일, job=polybot-eco)
- NO 토큰 가격은 **1-YES 근사** (스프레드 무시 근사임을 결과에 명시)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1 (redeem)
- 아카이브에도 `volume_24h`가 있으므로 **lime의 거래량 게이트(vol_mult)를 아카이브로 재생할 수 있다** — 이 봇 반사실 분석의 핵심 이점.

### 2.3 스캔 병목 파악 (Jenkins 콘솔 로그)

사이클마다 `제외 사유 요약 - reason: count` 한 줄이 남는다. lime의 진입 판정 사유 키: `window_invalid`, `no_jump`, `pullback_too_deep`, `volume_unconfirmed` (수치 접미사는 집계 시 제거됨).

```bash
grep -h "제외 사유 요약" <data/<job>/logs/*.log 경로> | tail -30
```

`volume_unconfirmed`가 압도적이면 vol_mult_min 스윕(§4b)의 우선순위가 높다는 신호, `window_invalid`가 많으면 cold-start/스냅샷 축적 문제다.

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

`sqlite3 <trades.db 경로>` 에서 그대로 실행. 모든 쿼리는 `mode='live'` 기준 — sim 분석 시 `'sim'`으로 교체.

### 3.1 상태 개요 + EXPIRED/보유 점검

```sql
SELECT status, mode, COUNT(*) AS n FROM trades GROUP BY status, mode;

-- 수동 redeem 필요분 (realized_pnl NULL이 정상)
SELECT id, condition_id, outcome, buy_price, buy_shares, market_end_date
FROM trades WHERE status = 'EXPIRED';

-- 현재 보유분 (반사실 §4a의 대상에 포함)
SELECT id, condition_id, outcome, buy_price, max_price, buy_timestamp, market_end_date
FROM trades WHERE status = 'HOLDING' AND mode = 'live';
```

### 3.2 완결 거래 요약: 건수 / 승률 / 평균·중앙 수익률

```sql
SELECT COUNT(*)                                            AS n,
       SUM(realized_pnl > 0)                               AS wins,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(SUM(realized_pnl), 4)                         AS total_pnl_usdc,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live';

-- 중앙 수익률 (SQLite median)
SELECT ROUND(AVG(ret_pct), 2) AS median_ret_pct FROM (
  SELECT (sell_price - buy_price) / buy_price * 100 AS ret_pct
  FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
  ORDER BY ret_pct
  LIMIT 2 - (SELECT COUNT(*) FROM trades WHERE status = 'COMPLETED' AND mode = 'live') % 2
  OFFSET (SELECT (COUNT(*) - 1) / 2 FROM trades WHERE status = 'COMPLETED' AND mode = 'live')
);
```

### 3.3 exit_reason별 분해 — 전략 판정의 1차 지표

```sql
SELECT exit_reason,
       COUNT(*)                                            AS n,
       ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1)  AS share_pct,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct,
       ROUND(SUM(realized_pnl), 4)                         AS pnl_usdc
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY exit_reason ORDER BY n DESC;
```

판정 기준 (STRATEGY.md §6): **`momentum_death` 비율 > 50% → 편승분(드리프트)이 없다는 신호 → `jump_min` 상향 또는 전략 폐기 검토.** `trailing_stop` 다수 + 평균 수익 양수면 드리프트는 있으나 짧다는 뜻 → TP 하향/트레일링 강화(§4a)로 교정.

### 3.4 진입 특성 버킷 — 밴드 교정 근거

```sql
-- (1) 진입가 버킷 (진입 가능 범위는 base_min+jump_min=0.25 ~ current_max=0.85)
SELECT CASE WHEN buy_price < 0.40 THEN '0.25-0.40'
            WHEN buy_price < 0.55 THEN '0.40-0.55'
            WHEN buy_price < 0.70 THEN '0.55-0.70'
            ELSE '0.70-0.85' END                           AS buy_bucket,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct,
       ROUND(SUM(realized_pnl), 4)                         AS pnl_usdc
FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY buy_bucket ORDER BY buy_bucket;

-- (2) 점프 폭 버킷 (jump_min 교정 근거)
SELECT CASE WHEN jump_size_at_buy < 0.12 THEN '0.10-0.12'
            WHEN jump_size_at_buy < 0.15 THEN '0.12-0.15'
            WHEN jump_size_at_buy < 0.20 THEN '0.15-0.20'
            ELSE '0.20+' END                               AS jump_bucket,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND jump_size_at_buy IS NOT NULL
GROUP BY jump_bucket ORDER BY jump_bucket;

-- (3) 거래량 배수 버킷 (vol_mult_min 교정 근거 — 핵심 가설 검증)
SELECT CASE WHEN vol_mult_at_buy < 3 THEN '2-3x'
            WHEN vol_mult_at_buy < 5 THEN '3-5x'
            ELSE '5x+' END                                 AS vol_bucket,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND vol_mult_at_buy IS NOT NULL
GROUP BY vol_bucket ORDER BY vol_bucket;

-- (4) base(점프 시작 기준가) 버킷 (base_min/base_max 교정 근거)
SELECT CASE WHEN base_price_at_buy < 0.30 THEN '0.15-0.30'
            WHEN base_price_at_buy < 0.50 THEN '0.30-0.50'
            ELSE '0.50-0.70' END                           AS base_bucket,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND base_price_at_buy IS NOT NULL
GROUP BY base_bucket ORDER BY base_bucket;

-- (5) 방향별 (YES 급등 편승 vs NO 급등=YES 급락 편승)
SELECT CASE WHEN entry_reason LIKE 'jump_up%' THEN 'jump_up(YES)'
            WHEN entry_reason LIKE 'jump_down%' THEN 'jump_down(NO)'
            ELSE entry_reason END                          AS direction,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct,
       ROUND(SUM(realized_pnl), 4)                         AS pnl_usdc
FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY direction;
```

### 3.5 보유시간 분석

```sql
SELECT exit_reason,
       COUNT(*) AS n,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h,
       ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_hold_h,
       ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_h
FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY exit_reason ORDER BY n DESC;

-- 보유시간 버킷 x 성과 (death_window/트레일링 교정 참고)
SELECT CASE WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 6  THEN '<6h'
            WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 24 THEN '6-24h'
            WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 72 THEN '24-72h'
            ELSE '72h+' END                                AS hold_bucket,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY hold_bucket ORDER BY hold_bucket;
```

### 3.6 진입 빈도 (표본 축적 속도 점검)

```sql
SELECT DATE(buy_timestamp) AS d, COUNT(*) AS entries
FROM trades WHERE mode = 'live' AND buy_timestamp IS NOT NULL
GROUP BY d ORDER BY d;
```

STRATEGY.md §6: 4주 후 거래 표본 30 미만이면 4주 연장 (진입 빈도 자체가 낮은 전략).

### 3.7 A/B 교차 비교 — elderberry와 가설 판정

```sql
-- elderberry DB 경로: find /Users/jongwoopark/.jenkins/workspace -path "*golden-elderberry/data*" -name "trades.db"
-- 주의: 2026-07 기준 elderberry는 polybot-cherry 워크스페이스에 있다 (이름 주의!)
ATTACH DATABASE '<elderberry trades.db 절대경로>' AS eld;

SELECT strategy_name,
       COUNT(*)                                            AS n,
       ROUND(AVG(realized_pnl > 0) * 100, 1)               AS win_rate_pct,
       ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct,
       ROUND(SUM(realized_pnl), 4)                         AS total_pnl_usdc
FROM (
  SELECT strategy_name, realized_pnl, sell_price, buy_price
  FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
  UNION ALL
  SELECT strategy_name, realized_pnl, sell_price, buy_price
  FROM eld.trades WHERE status = 'COMPLETED' AND mode = 'live'
)
GROUP BY strategy_name;
```

해석 (STRATEGY.md §2): lime만 수익 → "거래량 동반 급변은 지속" 가설 승. elderberry만 수익 → "급변은 회귀" 가설 승(lime은 구조적 고점 매수 → 폐기 검토). **둘 다 수익 → 거래량 필터가 유효한 구분자라는 강한 증거.** 같은 `condition_id`에 양쪽이 모두 진입한 사례가 있으면 개별 대조한다.

## 4. 반사실(what-if) 분석 레시피

수치 제안의 핵심. 가격 시계열은 §2.2 중앙 아카이브(nectarine)를 쓴다. 스크립트는 python(uv run)으로 작성해 scratchpad에서 실행하고, 결과 표만 회고에 남긴다.

### (a) 청산 스윕 — TP/SL/트레일링/death_window 격자

대상: 자기 DB의 `status IN ('COMPLETED', 'HOLDING', 'EXPIRED') AND mode = 'live'` 전 거래.

1. 거래별로 `condition_id`, `token_index`, `buy_price`, `buy_shares`, `buy_timestamp`, `sell_price`, `sell_timestamp`, `market_end_date`, `status`를 뽑는다.
2. 아카이브에서 `condition_id`의 스냅샷을 `buy_timestamp` 이후로 시간순 조회. `token_index = 1`(NO)이면 가격 = `1 - probability` (스프레드 무시 근사).
3. 각 격자 조합에 대해 5분 스텝으로 청산 체인을 현행 우선순위대로 시뮬레이션: SL → TP(목표가 `min(buy*(1+tp), 0.99)`) → 트레일링(런닝 최고가 갱신) → 모멘텀 사망(윈도우 시작가 대비 변화 <= 0, 커버리지 50% 미만이면 보류) → time_exit(endDate - exit_hours). 스냅샷이 끊기면(=해결) 최종가는 실제 `sell_price`, 없으면 0/1 redeem 가정 — 어느 가정인지 표에 명시.
4. counterfactual execution은 스냅샷 midpoint 가정으로 명시하고, actual 결과와 섞지 않는다.
   spread·unfilled·fee sensitivity를 별도 계산한다.

격자 (현행 설정 주변):

| 노브 | 격자 값 (굵게 = 현행) |
|---|---|
| take_profit | 0.06 / 0.09 / **0.12** / 0.15 / 0.18 |
| stop_loss | -0.05 / **-0.08** / -0.12 |
| trailing_stop.percent | 0.04 / **0.06** / 0.08 / off |
| death_window_hours | 2 / **3** / 4 / off |

출력: 격자별 총 P&L(USDC) + 승률 표. 전 조합(5x3x4x4=240)이 부담이면 노브별 1차원 스윕(나머지 현행 고정) 후 상위 조합만 2차원 교차. STRATEGY.md §7의 A-3(빠른 회전: TP 0.06 / death 2h / trailing 0.04) 조합은 반드시 포함해 프리셋 채택 여부를 판단.

### (b) 전략 고유 노브 스윕 — 진입 규칙 재생

핵심 노브 3개: **`jump_min`(진입 트리거 강도), `vol_mult_min`(거래량 게이트 — A/B 가설의 심장), `max_pullback` x `hold_window_minutes`(고점 유지 게이트)**.

아카이브로 진입 규칙을 재생한다 (아카이브에 `volume_24h`가 있어 거래량 게이트까지 재생 가능):

1. 아카이브에서 회고 기간의 전 시장 스냅샷을 로드. 시장별·5분 타임스탬프 t마다 양방향(YES 그대로, NO는 1-p) 평가:
   - 윈도우 = [t-6h, t] 스냅샷. 유효성: >= 5포인트 AND 시간 커버리지 >= 3h (아카이브는 5분 간격이라 대부분 통과 — 실봇의 cold-start와 다름, §c 참조)
   - base = 윈도우 최저가, jump = p(t) - base >= `jump_min`, base ∈ [0.15, 0.70], p(t) <= 0.85
   - 고점 유지: [t-`hold_window_minutes`, t] 구간 고점 대비 되돌림 <= `max_pullback`
   - 거래량: volume_24h(t) >= [t-24h, t] 구간 volume_24h 평균 x `vol_mult_min`
   - 시장 게이트: liquidity(t) >= 20000, volume_24h(t) >= 10000
   - 같은 condition_id 재진입은 24h 쿨다운으로 dedupe
2. 잡힌 가상 진입마다 (a)의 현행 청산 체인으로 성과 계산 → 노브 값별 {진입 수, 승률, 총 P&L} 표.
3. 실제 거래와 대조: "노브를 X로 했다면 걸러졌을 실제 진입"(손실 회피분)과 "잡혔을 추가 진입"(기회비용)을 분리 보고.

격자 (굵게 = 현행):

| 노브 | 격자 값 |
|---|---|
| jump_min | 0.07 / **0.10** / 0.12 / 0.15 |
| vol_mult_min | 1.5 / **2.0** / 2.5 / 3.0 |
| max_pullback | 0.01 / **0.02** / 0.03 |
| hold_window_minutes | **60** / 90 |

특히 **vol_mult_min 스윕은 elderberry 데이터와 교차**한다: elderberry가 진입한 급락 중 lime의 거래량 게이트를 통과했을 사례의 성과를 보면 "거래량이 회귀/지속의 구분자인가"를 거래 밖 표본으로도 검증할 수 있다.

### (c) 데이터 한계 (결과 보고에 반드시 명시)

- **end date coverage**: post-instrumentation `market_catalog.end_date`로 entry/time-exit을
  재현한다. legacy catalog gap만 Gamma 재조회로 보충하고 snapshot 존속 프록시는 look-ahead
  sensitivity로 별도 표시한다.
- **cold-start 비대칭**: 실봇은 스냅샷 축적 전(운영 초기·신규 시장 편입 직후)에는 진입 불가였지만, 아카이브 재생은 항상 데이터가 충분하다 → 재생 진입 수가 실제보다 부풀 수 있다. 실봇 운영 개시 + 24h 이후 구간만 비교하라.
- **post_scan_jump 재현 불가**: 스캔~주문 사이 추가 급등으로 skip된 사례(자기 DB `skipped_markets`)는 5분 해상도 재생으로 구분 못 한다.
- **NO 근사**: NO 가격 = 1-YES는 스프레드를 무시한다. NO 방향(jump_down) 결과는 별도 집계해 민감도를 표기.
- **Execution evidence**: legacy `trades`의 GTC 접수 상태 전환은 actual fill이 아니다. live
  실현 결과는 `CONFIRMED order_fills`의 partial size/price/fee로 계산하고 ledger gap을 분리한다.
  midpoint counterfactual은 execution sensitivity를 붙인 상대 비교로만 해석한다.
- **volume24hr 해상도**: gamma의 24h 누적 지표라 순간 폭증 감지가 지연된다. 아카이브도 같은 지표이므로 이 한계는 재생에 동일하게 반영된다 (충실 재현이자 공통 한계).
- **prices-history 백필 미반영**: 실봇의 백필 병합은 아카이브 재생에 없다. 점프 감지 시점이 실봇과 수 사이클 어긋날 수 있다.

## 5. 표본 주의사항

- **상관 클러스터**: 충격 뉴스 하나가 파생 시장 여러 개를 동시에 점프시킨다 (예: 후보 사퇴 → 관련 시장 5개 급변, STRATEGY.md §5 리스크 5 — 이벤트 단위 노출 상한이 없다). `market_tags` + `question` 키워드 + `buy_timestamp` 근접(±6h)으로 클러스터를 묶고, **이벤트 단위 P&L도 병기**한다. 클러스터 내 거래는 통계적으로 1건에 가깝다.
- **코호트 분리**: (1) 운영 초기 24~48h와 정상 신호, (2) sim/live, (3)
  `run_audits.config_hash`/Git commit 변경 전후를 분리한다. legacy 구간만 Jenkins 기록으로
  보충한다.
- **명목 n != 유효 n**: STRATEGY.md §6의 "30+ 거래" 판정 기준은 이벤트 클러스터 단위 유효 n으로 센다. 명목 30 / 유효 12라면 판정을 유보하고 기간을 연장한다.
- **방향 비대칭**: jump_up(YES 매수)과 jump_down(NO 매수, 1-p 근사 대상)은 성질이 다를 수 있다 — §3.4(5)로 분리 확인 후 합산 여부를 결정.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| 예: POLYBOT_JUMP_MIN | 0.10 | 0.12 | §4b: 0.12 격자에서 총 P&L +$X (진입 n=Y), momentum_death 비율 Z%→W% | 중간 |

- 근거 수치는 §3(실측)과 §4(반사실)의 구체 수치를 인용한다. 반사실만 근거인 제안은 신뢰도 "낮음"을 넘지 못한다.
- momentum_death 비율 > 50%였다면 "jump_min 상향" 또는 "전략 폐기" 중 하나를 표에 명시한다 (STRATEGY.md §6).
- elderberry A/B 판정 결과(§3.7)를 표 아래 한 줄로 요약한다.

**라운드 절차**: tunable knob는 Jenkins env로 반영하고 첫 성공 run의 새 `config_hash`/Git
commit을 확인한다. 병렬 A/B는 job/DB/account를 분리하며 preset은 STRATEGY.md §7을 따른다.

## 7. 기준 정보

- 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (main)
- 전략 문서: `golden-lime/STRATEGY.md` (논지·규칙·리스크·A/B 설계·변형 프리셋)
- 코드 기준: `golden-lime/src/polybot/strategy/signals.py`(진입/청산 판정 순수 함수 — 전략의 단일 소스), `strategy/trader.py`(청산 체인·exit_reason), `db/models.py`(스키마), `config.py`(env 이름)
- A/B 상대: `golden-elderberry/` (Panic Fade), 회고 가이드 `docs/retro/golden-elderberry.md`
- A/B 회고 절차 공통 플레이북: `docs/ab-retro-playbook.md`
