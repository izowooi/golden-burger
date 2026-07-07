# golden-fig 회고(포스트모템) 가이드

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 전략: **Hope Crusher** — 롱샷(YES 5~25%) 시장에서 항상 NO 토큰(75~95%)을 매수해 시간 가치 소멸(theta)을 수확.

## 0. 복붙용 회고 프롬프트

```text
/Users/izowooi/git/t1/docs/retro/golden-fig.md 를 읽고 §3~§5를 실행해 §6 표 형식으로 파라미터 교정안을 제시해줘.

- 봇 DB 찾기: find /Users/jongwoopark/.jenkins/workspace -path "*golden-fig/data*" -name "trades.db" 2>/dev/null
  (시뮬레이션 병행 운용 시 trades_sim.db 도 같은 방법으로 찾아 별도 분석. mode 컬럼 'live'/'sim' 확인)
- 중앙 가격 아카이브(반사실 분석용): find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
- Jenkins job 설정의 export 블록(키 제외)을 여기 붙여넣는다 — 운영 env는 repo에 없다:
  [붙여넣기]

주의사항 (반드시 지킬 것):
1. 같은 이벤트의 파생 시장(NO 여러 개 = 사실상 한 베팅)은 이벤트 단위로 묶어 집계해라 (§5).
2. 운영 첫 주 백로그 진입(첫 유효 스캔에서 한꺼번에 잡힌 것) 코호트와 정상 신호 코호트를 분리해라.
3. Hope Crusher는 손익비 약 1:1.7 불리한 꼬리 구조다 — stop_loss 1건이 take_profit 여러 건을 지운다.
   승률만 보지 말고 총 P&L, 이벤트 단위 유효 n, 그리고 "이번 달 표본에 꼬리 이벤트가 안 잡혔을 가능성"을 함께 판단해라.
4. EXPIRED(resolved_unredeemed)는 realized_pnl이 NULL이다. 수동 redeem 결과(1.00 또는 0.00)를 반영해 총손익을 보정해라.
```

## 1. 전략 요약

**논지**: favorite-longshot bias의 미러. 대중은 낮은 확률(YES 5~25%)에 복권 심리로 과지불하고, "D일까지 X가 일어날까" 시장은 시간이 소진될수록 YES 정당 가치가 기계적으로 하락(theta decay)하지만 YES 보유자는 앵커링·희망적 사고로 늦게 놓는다. 그 할인 구간에서 **항상 NO 토큰(`clobTokenIds[1]`, 가격 0.75~0.95)을 매수**해 만기 수렴을 수확한다. cherry(같은 편향의 favorite 쪽 75~92% 매수)와의 성과 비교가 이 편향의 어느 쪽이 더 두터운지에 대한 A/B다.

**진입 (모두 충족)**: liquidity >= $10k → 해결까지 24~240h → YES ∈ [0.05, 0.25] → 스냅샷 윈도우 유효(24h 윈도우 >= 5포인트, 커버리지 >= 12h; invalid면 백필, 그래도 invalid면 진입 금지) → YES 24h 변화 <= +0.02 AND 최근 6h YES 급등(저점 대비) < 0.05 → HOLDING 없음 + 마지막 청산/skip 후 24h 경과. trader 매수 직전 재검증에서 NO midpoint > 0.95면 `rapid_jump`로 skip 기록(쿨다운 24h 적용), < 0.75면 이번 사이클만 skip.

**청산 (우선순위 순, trailing 없음)**: ① P&L <= -10% → `stop_loss` ② 현재가 >= min(매수가×1.06, **0.99 캡**) → `take_profit` ③ 해결까지 < 2h → `time_exit`. midpoint 조회 불가 상태로 해결 후 24h 경과하면 `resolved_unredeemed`(status=EXPIRED, 수동 redeem 필요).

**파라미터 표** (우선순위: env > config.yaml > 코드 기본값. env 이름은 `src/polybot/config.py`, 기본값은 `config.yaml` 실측):

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC (NO 0.95 매수 시 최소 5주 요건 때문에 약 $4.75 이상 필요) |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 10000 | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0 = 비활성) |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 0.06 | 익절 % (목표가 0.99 캡) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.10 | 손절 % (NO 하락 = YES 급등 = 사건 발생 신호) |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입 쿨다운 (시간) |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | true | CLOB /prices-history 백필 |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | [] | 제외 카테고리 (comma 구분, 빈 값 = 비활성) |
| strategy.yes_min | `POLYBOT_YES_MIN` | 0.05 | YES 롱샷 밴드 하한 |
| strategy.yes_max | `POLYBOT_YES_MAX` | 0.25 | YES 롱샷 밴드 상한 (NO 매수가 [1-yes_max, 1-yes_min]) |
| strategy.yes_rise_block_24h | `POLYBOT_YES_RISE_BLOCK_24H` | 0.02 | YES 24h 변화가 이 값 초과면 skip |
| strategy.yes_spike_block_6h | `POLYBOT_YES_SPIKE_BLOCK_6H` | 0.05 | 최근 6h YES 급등이 이 값 이상이면 skip |
| strategy.rise_lookback_hours | `POLYBOT_RISE_LOOKBACK_HOURS` | 24 | 24h 게이트 lookback |
| strategy.spike_lookback_hours | `POLYBOT_SPIKE_LOOKBACK_HOURS` | 6 | 6h 게이트 lookback |
| time_based.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | 24 | 진입 최소 잔여 시간 |
| time_based.entry_hours_max | `POLYBOT_ENTRY_HOURS_MAX` | 240 | 진입 최대 잔여 시간 (10일) |
| time_based.exit_hours | `POLYBOT_EXIT_HOURS` | 2 | 해결 N시간 전 청산 |

API 인증 env(`POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE`)는 회고 대상이 아니다 — 값을 출력·복사하지 않는다.

## 2. 데이터 위치와 스키마

### 2.1 fig 자기 DB

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-fig/data*" -name "trades.db" 2>/dev/null
# 시뮬레이션은 같은 디렉토리의 trades_sim.db (별도 파일)
```

job명은 바뀔 수 있으니 반드시 find로 찾는다. 완료 거래 월별 CSV(`trades_YYYY-MM.csv`)와 로그(`logs/YYYYMMDD.log`)도 같은 `data/<job>/` 아래에 있다.

**`trades`** (재진입 허용 — `condition_id`는 unique가 아니다):

- 식별: `id`, `condition_id`, `market_slug`, `question`, `outcome`(항상 "No"), `token_id`
- 매수: `buy_price`(**NO 가격**, 0.75~0.95), `buy_amount`, `buy_shares`, `buy_order_id`, `buy_timestamp`(UTC naive), `buy_probability`
- 매도: `sell_price`, `sell_shares`, `sell_order_id`, `sell_timestamp`, `sell_probability`
- 손익/판정: `realized_pnl`, `status`, `entry_reason`(`hope_crusher_yes0.18_120.0h` 형식), `exit_reason`
- 컨텍스트: `max_price`(진입 후 최고가, 분석용 — trailing 아님), `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`, `volume_24h_at_buy`, `market_tags`
- 회고 계약 컬럼: `yes_price_at_buy`(매수 시점 YES 가격), `yes_price_at_exit`(청산 시점 1 - NO 매도가), `strategy_name`(항상 "fig"), `mode`("live"/"sim")
- 메타: `created_at`, `updated_at`

`status`는 **대문자 enum 이름**으로 저장: `PENDING_BUY` / `HOLDING` / `PENDING_SELL` / `COMPLETED` / `SKIPPED` / `EXPIRED`. `exit_reason` 값: `take_profit` / `stop_loss` / `time_exit` / `resolved_unredeemed`(EXPIRED와 짝, `realized_pnl` NULL — 수동 redeem 후 별도 보정).

**`market_snapshots`** (`condition_id`, `probability`=**항상 YES 가격**, `liquidity`, `volume_24h`, `timestamp`): 스캔한 전 시장을 매 사이클 저장하지만 **보존 기간이 7일**(`bot.py` `SNAPSHOT_RETENTION_DAYS = 7`)이다. → **월간 회고의 가격 시계열로는 못 쓴다. 반사실 분석은 반드시 아래 중앙 아카이브를 쓴다.**

**`skipped_markets`** (`condition_id`, `reason`, `skipped_at`): trader가 기록하는 skip. 현재 기록되는 reason은 `rapid_jump`(NO midpoint > 밴드 상단). 쿨다운 판정은 가장 최근 `skipped_at` 기준.

### 2.2 중앙 가격 아카이브 (반사실 분석용)

모든 봇이 같은 gamma sweep(가장 오래된 활성 시장 ~2100개)을 스냅샷하므로, **nectarine DB의 `market_snapshots`를 공용 아카이브**로 쓴다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 바뀔 수 있으니 find로 찾는다.
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존** — fig의 `min_liquidity` 기본값(10000)과 동일 컷이라 커버리지가 잘 맞는다
- 보조 아카이브: honeydew DB (liq >= $15k, 60일, job=polybot-eco) — nectarine에 구멍이 있으면 교차 확인
- **NO 토큰 가격은 `1 - probability` 근사** (스프레드 무시 근사임을 결과에 명시할 것)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결 보유분의 최종가는 `trades.sell_price` 또는 redeem 값 0/1
- 아카이브에는 `endDate`가 없다 → 잔여시간 계산은 fig `trades.market_end_date`(진입한 시장) 또는 Gamma API `condition_ids` 재조회(미진입 시장)로 보충

### 2.3 Jenkins 콘솔 로그

매 사이클 "제외 사유 요약 - reason: count" 한 줄이 남는다(스캔 병목 파악용). 로그는 `data/<job>/logs/YYYYMMDD.log`에도 남는다. fig의 정규화된 사유 키: `excluded_category`, `low_liquidity`, `no_price_data`, `yes_out_of_band`, `low_volume`, `no_end_date`, `already_resolved`, `too_late`, `too_early`, `window_invalid`, `yes_rising`(24h 게이트), `yes_spike`(6h 게이트). `window_invalid`가 지속적으로 크면 백필 실패/스냅샷 축적 문제, `yes_rising`/`yes_spike`가 크면 게이트가 진입을 얼마나 막았는지의 지표다.

## 3. 실적 분석 SQL (그대로 실행 가능)

`sqlite3 <fig trades.db 경로>` 에서 실행. status는 대문자 리터럴.

```sql
-- 3.1 완결 거래 총괄: 건수 / 승률 / 평균·중앙 수익률
SELECT COUNT(*)                                                        AS closed_trades,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(SUM(realized_pnl), 4)                                     AS total_pnl,
       ROUND(AVG(realized_pnl), 4)                                     AS avg_pnl,
       ROUND(AVG(100.0 * realized_pnl / NULLIF(buy_amount, 0)), 2)     AS avg_return_pct,
       (SELECT ROUND(100.0 * realized_pnl / NULLIF(buy_amount, 0), 2)
        FROM trades WHERE status = 'COMPLETED'
        ORDER BY realized_pnl / NULLIF(buy_amount, 0)
        LIMIT 1 OFFSET (SELECT COUNT(*) FROM trades WHERE status = 'COMPLETED') / 2) AS median_return_pct
FROM trades
WHERE status = 'COMPLETED';
```

```sql
-- 3.2 exit_reason별 분해 (STRATEGY.md §6 판단 기준: stop_loss 비중 25% 초과 시 게이트 재조정/중단)
SELECT COALESCE(exit_reason, '(none)') AS exit_reason,
       COUNT(*) AS n,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM trades WHERE status = 'COMPLETED'), 1) AS share_pct,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl,
       ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades
WHERE status = 'COMPLETED'
GROUP BY exit_reason
ORDER BY total_pnl ASC;
```

```sql
-- 3.3 진입 특성 버킷 ①: YES 밴드 구간별 성과 (yes_min/yes_max 교정 근거)
--     yes_price_at_buy가 NULL인 행은 1 - buy_price 근사로 폴백
SELECT CASE
         WHEN COALESCE(yes_price_at_buy, 1.0 - buy_price) < 0.10 THEN 'YES 0.05-0.10 (NO 0.90-0.95)'
         WHEN COALESCE(yes_price_at_buy, 1.0 - buy_price) < 0.15 THEN 'YES 0.10-0.15 (NO 0.85-0.90)'
         WHEN COALESCE(yes_price_at_buy, 1.0 - buy_price) < 0.20 THEN 'YES 0.15-0.20 (NO 0.80-0.85)'
         ELSE 'YES 0.20-0.25 (NO 0.75-0.80)'
       END AS yes_band,
       COUNT(*) AS n,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       SUM(CASE WHEN exit_reason = 'stop_loss' THEN 1 ELSE 0 END) AS stop_loss_n,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl,
       ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades
WHERE status = 'COMPLETED'
GROUP BY yes_band
ORDER BY yes_band;
```

```sql
-- 3.4 진입 특성 버킷 ②: 잔여시간 구간별 성과 (entry_hours_min/max 교정 근거)
SELECT CASE
         WHEN hours_until_resolution_at_buy < 72  THEN '24-72h'
         WHEN hours_until_resolution_at_buy < 144 THEN '72-144h'
         ELSE '144-240h'
       END AS ttr_band,
       COUNT(*) AS n,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       SUM(CASE WHEN exit_reason = 'stop_loss' THEN 1 ELSE 0 END) AS stop_loss_n,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl,
       ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND hours_until_resolution_at_buy IS NOT NULL
GROUP BY ttr_band
ORDER BY ttr_band;
```

```sql
-- 3.5 보유시간 분석 (exit_reason별): time_exit까지 며칠씩 묶였는지 = 자본 회전율 근거
SELECT COALESCE(exit_reason, '(none)') AS exit_reason,
       COUNT(*) AS n,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_hours,
       ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_hold_hours,
       ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_hours,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl
FROM trades
WHERE status = 'COMPLETED' AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason
ORDER BY avg_hold_hours DESC;
```

```sql
-- 3.6 최대 손실 top 10 + 진입 컨텍스트 (꼬리 이벤트 검시)
SELECT id, substr(question, 1, 60) AS q, entry_reason, exit_reason,
       ROUND(buy_price, 3) AS no_buy, ROUND(sell_price, 3) AS no_sell,
       ROUND(COALESCE(yes_price_at_buy, 1.0 - buy_price), 3) AS yes_at_buy,
       ROUND(yes_price_at_exit, 3) AS yes_at_exit,
       ROUND(realized_pnl, 4) AS pnl,
       ROUND(hours_until_resolution_at_buy, 1) AS hrs_to_res,
       ROUND(liquidity_at_buy, 0) AS liq, market_tags
FROM trades
WHERE status = 'COMPLETED' AND realized_pnl < 0
ORDER BY realized_pnl ASC
LIMIT 10;
```

```sql
-- 3.7 숨은 손실 감사: HOLDING 잔여 + EXPIRED(수동 redeem 필요, realized_pnl NULL)
SELECT status, COUNT(*) AS n,
       ROUND(SUM(buy_amount), 2) AS capital_locked_usdc,
       SUM(CASE WHEN exit_reason = 'resolved_unredeemed' THEN 1 ELSE 0 END) AS unredeemed_n
FROM trades
WHERE status IN ('HOLDING', 'EXPIRED')
GROUP BY status;

-- EXPIRED 목록 (redeem 결과 0/1을 수동 확인해 총손익 보정에 반영할 것)
SELECT id, substr(question, 1, 60) AS q, ROUND(buy_price, 3) AS no_buy,
       ROUND(buy_amount, 2) AS usdc, market_end_date
FROM trades
WHERE status = 'EXPIRED'
ORDER BY market_end_date;
```

```sql
-- 3.8 skip 이력: rapid_jump 빈도 (매수 직전 재검증에서 밴드 상단 초과가 얼마나 잦았나)
SELECT reason, COUNT(*) AS n,
       MIN(skipped_at) AS first_at, MAX(skipped_at) AS last_at
FROM skipped_markets
GROUP BY reason
ORDER BY n DESC;
```

```sql
-- 3.9 재진입 성과: 같은 condition_id 2회 이상 진입분의 회차별 성과 (쿨다운 24h 검증)
SELECT entry_seq, COUNT(*) AS n,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM (
  SELECT realized_pnl,
         ROW_NUMBER() OVER (PARTITION BY condition_id ORDER BY buy_timestamp) AS entry_seq
  FROM trades
  WHERE status = 'COMPLETED'
)
GROUP BY entry_seq
ORDER BY entry_seq;
```

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

fig 자체 스냅샷은 7일이면 지워지므로, 가격 시계열은 전부 **중앙 아카이브(§2.2)** 를 붙여 쓴다. NO 가격은 `1 - probability` 근사.

### (a) 청산 스윕: TP / SL / exit_hours 격자

현행 설정(TP +0.06 · 0.99 캡, SL -0.10, exit_hours 2) 주변 격자:

- TP: **{0.03, 0.04, 0.06, 0.08, 0.10, 만기 보유(redeem 1.00/0.00)}** — 전부 0.99 캡 적용
- SL: **{-0.05, -0.08, -0.10, -0.15, -0.20, SL 없음(만기까지)}**
- exit_hours: **{1, 2, 6, 12}**

단일 격자점 시뮬레이션 SQL (fig DB에서 실행, 아카이브 ATTACH — 두 DB 모두 timestamp가 UTC naive라 그대로 비교 가능):

```sql
ATTACH DATABASE '<아카이브 trades.db 절대경로>' AS arch;

WITH par AS (SELECT 0.06 AS tp, -0.10 AS sl, 0.99 AS cap),
px AS (  -- 매수 이후 NO 가격 시계열 (1 - YES 근사, 5분 간격)
  SELECT t.id AS trade_id, t.buy_price, t.buy_shares,
         s.timestamp AS ts, 1.0 - s.probability AS no_price
  FROM trades t
  JOIN arch.market_snapshots s
    ON s.condition_id = t.condition_id
   AND s.timestamp >= t.buy_timestamp
  WHERE t.buy_timestamp IS NOT NULL
    AND t.status IN ('COMPLETED', 'HOLDING', 'EXPIRED')
),
hits AS (  -- TP/SL 최초 도달 시각
  SELECT px.trade_id, px.buy_price, px.buy_shares,
         MIN(CASE WHEN px.no_price >= MIN(px.buy_price * (1 + par.tp), par.cap)
                  THEN px.ts END) AS tp_ts,
         MIN(CASE WHEN (px.no_price - px.buy_price) / px.buy_price <= par.sl
                  THEN px.ts END) AS sl_ts
  FROM px, par
  GROUP BY px.trade_id
)
SELECT CASE
         WHEN sl_ts IS NOT NULL AND (tp_ts IS NULL OR sl_ts <= tp_ts) THEN 'sim_stop_loss'
         WHEN tp_ts IS NOT NULL THEN 'sim_take_profit'
         ELSE 'sim_no_hit'   -- 만기/시간청산까지 무접촉: 아래 최종가 규칙으로 별도 정산
       END AS sim_exit,
       COUNT(*) AS n,
       ROUND(SUM(CASE
         WHEN sl_ts IS NOT NULL AND (tp_ts IS NULL OR sl_ts <= tp_ts)
           THEN buy_shares * buy_price * (SELECT sl FROM par)
         WHEN tp_ts IS NOT NULL
           THEN buy_shares * (MIN(buy_price * (1 + (SELECT tp FROM par)), (SELECT cap FROM par)) - buy_price)
         ELSE 0 END), 4) AS sim_pnl_usdc
FROM hits
GROUP BY sim_exit;
```

- `sim_no_hit` 행은 스냅샷이 끊긴 지점(해결) 이후를 모른다 → 최종가는 해당 거래의 실제 `sell_price`(COMPLETED), redeem 값 1.00/0.00(EXPIRED·만기 보유 가정)으로 수동 정산해 합산한다.
- exit_hours 스윕은 `hits`의 TP/SL 도달 시각을 `market_end_date - exit_hours`와 비교해, 그보다 늦으면 "그 시각의 NO 가격으로 time_exit"로 바꿔 계산한다.
- 격자 전체는 `par` 값을 바꿔가며 반복 실행(bash 루프 or Python)해 **격자별 총 P&L 표**로 정리한다. 손절 체결은 정확히 SL 가격에 됐다고 가정하는 낙관 가정임을 표에 명시(실제는 갭 하락 가능).

### (b) 전략 고유 노브 스윕

핵심 노브 3개와 스윕 범위:

1. **YES 밴드** (`POLYBOT_YES_MIN` / `POLYBOT_YES_MAX`): yes_max ∈ {0.15, 0.20, **0.25**, 0.30}, yes_min ∈ {0.03, **0.05**, 0.10}. §3.3 버킷 성과가 1차 근거이고, "넓혔다면 잡혔을 진입"은 아카이브로 재생한다.
2. **진입 시간창** (`POLYBOT_ENTRY_HOURS_MIN` / `POLYBOT_ENTRY_HOURS_MAX`): entry_hours_max ∈ {72, 120, **240**, 360}, entry_hours_min ∈ {12, **24**, 48}. STRATEGY.md §7의 fig-2(단기 집중, 72h)가 대표 변형.
3. **사건 진행 게이트** (`POLYBOT_YES_RISE_BLOCK_24H` / `POLYBOT_YES_SPIKE_BLOCK_6H`): rise ∈ {0.00, 0.01, **0.02**, 0.04}, spike ∈ {0.03, **0.05**, 0.08}. §3.2에서 stop_loss 비중이 25%를 넘으면 조이는 방향(fig-3), stop_loss가 드물고 진입이 너무 적으면 푸는 방향.

(보조 노브: `POLYBOT_MIN_VOLUME_24H` 0 → {2000, 5000, 10000} — 아카이브 `volume_24h` 컬럼으로 그대로 필터 재생 가능.)

**진입 규칙 재생 방법** (아카이브 스냅샷은 5분 간격이므로 fig의 3~5분 사이클과 근사 일치):

1. 아카이브에서 후보 시점 추출: `probability ∈ [yes_min', yes_max']` AND `liquidity >= 10000` 인 (condition_id, timestamp) 행.
2. 각 후보 시점에 대해 같은 condition_id의 직전 24h 스냅샷으로 게이트 재현: `change_24h = probability - (윈도우 最古 probability)` <= rise', `spike_6h = probability - (직전 6h 최저 probability)` < spike'. 윈도우 유효성(>= 5포인트, 커버리지 >= 12h)도 동일 기준으로 확인.
3. 잔여시간 필터: `endDate`는 아카이브에 없다 → fig가 진입한 시장은 `trades.market_end_date` 재사용, 미진입 시장은 Gamma API(`https://gamma-api.polymarket.com/markets?condition_ids=...`, 해결된 시장도 조회됨)로 받아 `entry_hours_min' <= (endDate - ts) <= entry_hours_max'` 판정.
4. 재진입 쿨다운 재현: 같은 condition_id에서 가상 진입 후 24h 이내 후보 시점은 버린다 (첫 통과 시점만 진입).
5. 각 가상 진입에 (a)의 청산 시뮬레이션을 그대로 적용해 가상 P&L 계산 → "노브를 X로 했다면 추가로 잡혔을 / 걸러졌을 진입"의 성과를 현행 대비 증분으로 표기.
6. 반대로 조이는 노브(fig-3 등)는 실제 진입 거래 중 "새 기준이면 걸러졌을 것"을 표시하고 그 거래들의 실제 P&L 합을 "회피됐을 손익"으로 계산한다 — 이쪽은 재생이 아니라 실측이므로 신뢰도가 높다.

이 재생은 SQL만으로는 무겁다. 아카이브를 pandas로 읽어 condition_id별 groupby로 도는 단발 Python 스크립트를 권장 (scratchpad에 작성, repo 커밋 불필요).

### (c) 데이터 한계 (결과 보고서에 반드시 명시)

- **NO = 1 - YES 근사**: 실제 봇은 NO 토큰의 CLOB midpoint로 체결한다. 스프레드·양쪽 호가 불일치가 무시된 근사다. 특히 YES 0.05 근처(NO 0.95)는 스프레드가 수익률의 상당분을 잠식할 수 있다 (STRATEGY.md §5-3).
- **게이트 재현 한계**: fig의 실전 게이트는 자체 스냅샷 + CLOB /prices-history 백필 병합본으로 판정했다. 아카이브(5분 간격, 60일)와 커버리지가 달라 window_invalid 판정이 실전과 다를 수 있다 — 재생 결과의 진입 집합은 실전과 완전히 일치하지 않는다.
- **체결 가정**: 실전도 시뮬레이션도 GTC limit 접수 = 체결 가정(STRATEGY.md §8). SL 시뮬레이션은 갭 하락(한 사이클에 -10%를 건너뛰는 폭락)을 SL 가격 체결로 낙관 처리한다.
- **해결 시장 스냅샷 단절**: 아카이브는 시장 해결 시점에 끊긴다. 만기 보유 격자점의 redeem 값(1.00/0.00)은 실제 해결 결과를 확인해 수동 부여한다.
- **아카이브 유니버스 컷**: liq >= $10k 기준이라 fig가 `POLYBOT_MIN_LIQUIDITY`를 낮춰 운영했다면 아카이브에 없는 진입이 존재할 수 있다 (env 블록으로 확인).

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트의 파생 시장 여러 개에 NO를 들면 사실상 한 베팅이다 (fig는 이벤트 단위 노출 제한이 없다 — STRATEGY.md §5-5). `market_tags`, `market_slug` 접두어, `question` 앞 40자 유사도로 클러스터를 묶고, 승률·P&L을 **이벤트 단위로도** 집계하라. 명목 n(거래 수) != 유효 n(독립 이벤트 수).
- **코호트 분리**: 운영 시작 직후 첫 유효 스캔은 "조건을 이미 충족한 채 쌓여 있던 시장"을 한꺼번에 잡는다(초기 백로그). `buy_timestamp` 첫 주 코호트와 이후 정상 신호 코호트를 분리해 성과가 코호트 간 일관적인지 확인하라. cold start 기간(스냅샷 축적 전 window_invalid로 진입이 거의 없던 며칠)도 표본에서 구분한다.
- **꼬리 미표집 편향**: Hope Crusher의 손익 구조상(최대 이익 ~ +0.14/주, 최대 손실 ~ -0.85/주) 한 달 표본에 "사건이 실제로 일어난" 꼬리 이벤트가 0건이면 승률·평균 수익이 과대평가된다. stop_loss 0건 = 전략이 안전하다는 뜻이 아니라 아직 안 맞았다는 뜻일 수 있다. `resolved_unredeemed`와 stop_loss를 합쳐 꼬리 노출을 별도 보고하라.
- **STRATEGY.md §6 판단 기준 재확인**: 4주 + 완결 30건 이상, 승률 >= 75%(TP+time_exit 비중), 평균 손익 > 0, stop_loss 비중 25% 이하. 미달이면 교정이 아니라 중단도 옵션이다.
- cherry(같은 편향의 favorite 쪽)와 동일 기간 수익률 비교를 곁들이면 편향의 어느 쪽이 더 수확 가능한지 판단할 수 있다. 단, `strategy_name`/`mode` 컬럼은 fig 등 신봇에만 있고 **cherry(구봇 스키마)에는 없다** — 교차 봇 UNION 시 cherry 쪽은 `'cherry' AS strategy_name, 'live' AS mode` 식 리터럴로 부여한다 (`docs/ab-retro-playbook.md` 참조).

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| `POLYBOT_YES_MAX` | 0.25 | | §3.3 밴드별 P&L + §4(b) 재생 결과 | |
| `POLYBOT_YES_MIN` | 0.05 | | §3.3 (NO 0.90-0.95 버킷 스프레드 잠식 여부) | |
| `POLYBOT_ENTRY_HOURS_MAX` | 240 | | §3.4 + §4(b) | |
| `POLYBOT_ENTRY_HOURS_MIN` | 24 | | §3.4 + §4(b) | |
| `POLYBOT_YES_RISE_BLOCK_24H` | 0.02 | | §3.2 stop_loss 비중 + §4(b) 회피됐을 손익 | |
| `POLYBOT_YES_SPIKE_BLOCK_6H` | 0.05 | | 상동 | |
| `POLYBOT_TAKE_PROFIT` | 0.06 | | §4(a) 격자 총 P&L | |
| `POLYBOT_STOP_LOSS` | -0.10 | | §4(a) 격자 총 P&L (갭 가정 명시) | |
| `POLYBOT_EXIT_HOURS` | 2 | | §4(a) + §3.5 보유시간 | |
| `POLYBOT_MIN_VOLUME_24H` | 0 | | §4(b) 보조 노브 필터 재생 | |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | | §3.9 회차별 성과 | |

**라운드 절차**: 제안값은 코드 수정 없이 **Jenkins env만 바꾸면 적용**된다 (우선순위 env > yaml). ① 교정안 확정 → ② Jenkins job env 수정 후 4주 2차 테스트 → ③ 같은 문서로 3차 교정. 공격적인 변경은 cherry처럼 **기본/변형 병행**이 안전하다: 기존 job은 그대로 두고 `--job fig-1` 식으로 job을 분리하면 DB(`data/fig-1/trades.db`)가 분리되어 동시 A/B가 된다 (STRATEGY.md §7의 fig-1 보수 / fig-2 단기 집중 / fig-3 게이트 강화 변형 참조). 변경은 한 라운드에 노브 1~2개까지만 — 여러 개를 동시에 바꾸면 귀인이 불가능하다.

## 7. 기준 정보

- 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (main)
- 전략 문서: `/Users/izowooi/git/t1/golden-fig/STRATEGY.md` (논지·리스크·A/B 기준), `/Users/izowooi/git/t1/golden-fig/AGENTS.md`, `/Users/izowooi/git/t1/golden-fig/README.md`
- 그라운딩 소스: `src/polybot/config.py`(env 이름), `config.yaml`(기본값), `src/polybot/db/models.py`(스키마), `src/polybot/strategy/trader.py`·`signals.py`(청산 로직·exit_reason), `src/polybot/strategy/scanner.py`(제외 사유 키), `src/polybot/bot.py`(`SNAPSHOT_RETENTION_DAYS = 7`)
- 교차 봇 공통 절차: `/Users/izowooi/git/t1/docs/ab-retro-playbook.md`
- 알려진 슬롯 매핑(2026-07-07 기준, 바뀔 수 있음 — find로 재확인): nectarine=polybot-fox(계정 golden-fox), honeydew=polybot-eco(계정 golden-eco), date=polybot-red, elderberry=polybot-cherry 워크스페이스(이름 주의). 운영 4계정 = apple x2, banana, cherry. cherry는 변형 슬롯(BUY 0.85/SELL 0.95/liq 30k/--yes-only) 병행 운용 중.
