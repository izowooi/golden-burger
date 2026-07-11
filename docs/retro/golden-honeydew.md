# golden-honeydew 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 전략: **Night Watch** — 한산 시간대(UTC 06-13시/주말)에 뉴스 없이 발생한 가격 이탈을 24h median 복원 방향으로 매수.

## 0. 복붙용 회고 프롬프트

```
docs/retro/golden-honeydew.md 를 읽고 §3~§5를 실행해 §6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.

- 봇: golden-honeydew (Night Watch). DB는 §2의 find 명령으로 찾아라
  (2026-07 기준 Jenkins job=polybot-eco, 대시보드 계정 golden-eco — job명은 바뀔 수 있음).
- Jenkins env 블록 (job 설정의 export 블록에서 POLYMARKET_PRIVATE_KEY /
  POLYMARKET_FUNDER_ADDRESS 두 키만 제외하고 그대로 붙여넣기):
  [여기에 붙여넣기]
- 반사실 분석(§4)의 가격 시계열: 1차 = honeydew 자기 DB의 market_snapshots,
  결손 보충/교차검증 = nectarine DB의 market_snapshots (공용 아카이브).
- 반드시 지킬 것:
  * §5 상관 클러스터 — 같은 이벤트 파생 시장·같은 밤의 동시 진입은 이벤트(클러스터)
    단위로 묶어 재집계하고, 유효 n 기준으로 신뢰도를 매겨라.
  * §5 코호트 분리 — 운영 첫 주 cold-start volume evidence 부족으로 fail-closed된 구간과
    정상 신호를 분리하고, 주말 진입과 평일 quiet 진입도 분리 집계하라.
  * 존재하지 않는 컬럼/env를 지어내지 마라. 컬럼은 §2, env는 §1 표가 전부다.
```

## 1. 전략 요약

Polymarket 참여자 대다수가 자는 시간(미 동부 새벽 = UTC 06-13시, 그리고 주말)에는 orderbook이 얇아져 소액 주문만으로 가격이 밀린다. 이때 뉴스(거래량 급증) 없이 24h median에서 ±5% 이상 이탈한 가격은 정보가 아니라 노이즈일 확률이 높고, 아침에 주의(attention)가 돌아오면 복원된다는 가설이다. 하락 이탈(dev < 0)이면 그 시장의 **YES 매수**(반등), 상승 이탈(dev > 0)이면 **NO 매수**(페이드, 매수가는 실제 NO 토큰 가격 `outcomePrices[1]`). 승률 자체가 전략의 전부인 +6%/-6% 대칭 손익 구조이며, trailing stop은 의도적으로 없다.

- **진입 (모두 충족)**: 한산 시간대 + liquidity >= $15k + 해결까지 >= 24h +
  |현재 YES − 24h median| >= 0.05 + 거래량 급증 아님 + 매수 토큰 가격 ∈ [0.30, 0.90].
  가격 윈도우는 >=5포인트/50% coverage, volume baseline과 최근 3h 구간은 각각 >=2포인트와
  해당 구간 50% coverage가 필요하다. volume evidence가 부족하거나 baseline 평균이 0이면
  `volume_window_invalid`로 fail-closed한다.
- **청산 (우선순위 순, `signals.evaluate_exit`)**: ① P&L <= -6% → `stop_loss` ② 현재가 >= min(매수가×1.06, 0.99) → `take_profit` ③ 보유 >= 24h → `max_holding` ④ 해결까지 < 12h → `time_exit`. 별도로 midpoint 조회 실패(또는 0 반환) + endDate 24h 경과 시 status=EXPIRED, exit_reason=`resolved_unredeemed`(realized_pnl NULL, 수동 redeem 필요).

### 파라미터 표 (env는 `src/polybot/config.py`, 기본값은 `config.yaml` 실제 값)

우선순위는 **환경변수 > config.yaml > 코드 기본값**이다. post-instrumentation 운영값은
`strategy_configs`와 `run_audits`의 config hash/Git cohort가 source of truth다. Jenkins export는
secret을 제거한 current/legacy cross-check로만 사용한다.

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| quiet.hours_utc | `POLYBOT_QUIET_HOURS_UTC` | `"6-13"` | 진입 허용 UTC 시간대 `[start, end)`. 자정 넘는 `"22-4"` 지원 |
| quiet.weekends | `POLYBOT_QUIET_WEEKENDS` | `true` | 주말(토/일 UTC)은 시각 무관 진입 허용 |
| signal.median_lookback_hours | `POLYBOT_MEDIAN_LOOKBACK_HOURS` | `24` | median 계산 윈도우 (시간) |
| signal.dev_min | `POLYBOT_DEV_MIN` | `0.05` | 최소 편차 \|현재 YES 가격 − median\| |
| signal.vol_spike_block | `POLYBOT_VOL_SPIKE_BLOCK` | `1.5` | 거래량 급증 차단 배수 (뉴스 배제) |
| signal.entry_prob_min | `POLYBOT_ENTRY_PROB_MIN` | `0.30` | 매수 토큰 가격 하한 |
| signal.entry_prob_max | `POLYBOT_ENTRY_PROB_MAX` | `0.90` | 매수 토큰 가격 상한 |
| time_based.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | `24` | 해결까지 최소 잔여 시간 |
| time_based.exit_hours | `POLYBOT_EXIT_HOURS` | `12` | 해결 N시간 전 청산 |
| time_based.max_holding_hours | `POLYBOT_MAX_HOLDING_HOURS` | `24` | 최대 보유 시간 (복원 실패 시 회전) |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | `0.06` | 익절 +6% (목표가 0.99 캡) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | `-0.06` | 손절 -6% |
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | `5.0` | 1회 매수 USDC |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | `15000` | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | `0` | 최소 24h 거래량 $ (0=비활성) |
| max_positions | `POLYBOT_MAX_POSITIONS` | `-1` | 최대 동시 포지션 (-1=무제한) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | `24` | 재진입 쿨다운 (시간) |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | `true` | prices-history 백필 (cold start 보완) |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | `[]` | 제외 카테고리 (comma 구분, 기본 비활성) |
| (로그) | `LOG_LEVEL` | yaml 키 없음, 기본 INFO | 로그 레벨 (`--verbose`가 최우선) |

env 없는 코드 상수 (`signals.py` / `trader.py`): `vol_recent_hours=3.0`, 가격 window
`min_points=5`·`min_coverage=0.5`, volume baseline/recent 각각
`vol_window_min_points=2`·`vol_window_min_coverage=0.5`, `MIN_ORDER_SIZE=5.0`,
`RESOLVED_GRACE_HOURS=24.0`.

## 2. 데이터 위치와 스키마

### 자기 DB 찾기

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-honeydew/data*" -name "trades.db" 2>/dev/null
```

- job명은 바뀔 수 있으니 반드시 find로 찾는다 (2026-07 기준 job=polybot-eco, 대시보드 계정 golden-eco).
- 시뮬레이션은 같은 폴더의 `trades_sim.db` 별도. 완료 거래 월별 CSV(`trades_YYYY-MM.csv`)와 일자별 로그(`logs/YYYYMMDD.log`)도 같은 `data/<job>/` 아래에 있다.
- Jenkins 콘솔 로그/일자별 로그에는 사이클마다 `제외 사유 요약 - reason: count` 한 줄이 남는다 (스캔 병목 파악용). reason 키: `excluded_category`, `low_liquidity`, `low_volume`, `too_close_to_resolution`, `no_price_data`, 그리고 시그널 탈락(숫자 접미사 제거) `window_invalid`, `dev_below_min`, `volume_spike_news`, `price_out_of_band`.

### 테이블 (`src/polybot/db/models.py` 기준 — 이 컬럼만 사용)

**trades** — 핵심 컬럼:

- 식별: `id`, `condition_id`(unique 아님 — 재진입 쿨다운 방식), `market_slug`, `question`, `outcome`("Yes"/"No"), `token_id`, `market_tags`
- 매수: `buy_price`, `buy_amount`, `buy_shares`, `buy_order_id`, `buy_timestamp`, `buy_probability`(= buy_price와 동일 값)
- 매도: `sell_price`, `sell_shares`, `sell_order_id`, `sell_timestamp`, `sell_probability`, `realized_pnl`
- 전략: `entry_reason`(예: `night_dislocation_dev+0.070` — 부호 있는 YES 기준 편차), `exit_reason`(`take_profit` / `stop_loss` / `max_holding` / `time_exit` / `resolved_unredeemed`), `max_price`(분석용 진입 후 최고가), `deviation_at_buy`(YES 기준, 부호 있음), `median_at_buy`, `deviation_at_exit`(청산 시점 편차, 계산 불가 시 NULL), `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`
- 회고 공통 계약: `strategy_name`(상수 "honeydew"), `mode`("live"/"sim"), `volume_24h_at_buy`
- `status`: **SQLAlchemy Enum은 enum name 대문자로 저장된다** — `'PENDING_BUY'`, `'HOLDING'`, `'PENDING_SELL'`, `'COMPLETED'`, `'SKIPPED'`, `'EXPIRED'`. SQL에서 소문자 value(`completed` 등)를 쓰면 0건이 나온다.
- `EXPIRED`는 `realized_pnl` NULL — P&L 집계에서 자동으로 빠지므로 §3.3에서 반드시 별도 확인 (수동 redeem 필요 물량).

**market_snapshots** — `condition_id`, `probability`(**항상 YES 가격**), `liquidity`,
`volume_24h`, `best_bid`, `best_ask`, `spread`, `source_updated_at`, `run_id`, `timestamp`.
추가 컬럼은 post-instrumentation 행부터 채워지며 legacy `NULL`을 추정해 채우지 않는다.

**market_catalog** — `condition_id`, market/event ID·slug, question, `end_date`, outcomes/token IDs,
tags, fee metadata, first/last seen. event-cluster와 universe 재현은 이 테이블을 사용한다.

**skipped_markets** — `condition_id`, `reason`(`rapid_jump` 등), `skipped_at`. 재진입 쿨다운 판정용.

### 중앙 가격 아카이브 (반사실 분석용 시계열)

Gamma keyset cursor를 끝까지 순회한 당시 qualifying universe를 수집하므로, what-if 가격
시계열은 공용 아카이브를 쓴다. 고정 시장 수 대신 run별 cursor completion과
catalog/snapshot coverage를 확인한다:

- **1차: honeydew 자기 DB의 `market_snapshots`** — 유니버스(liq >= $15k)가 자기 진입 조건과 정확히 일치하고, volume_24h까지 있어 뉴스 필터 재현이 가능하다. 보존 60일, 5분 간격. (honeydew DB는 레포 공통 문서에서 "보조 아카이브"로 지정된 그 DB다 — job=polybot-eco.)
- **보조/교차검증: nectarine DB의 `market_snapshots`** (공용 아카이브, liq >= $10k, 60일 보존):

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox
```

- snapshot/catalog 계약은 위와 동일하다. 목표 cadence 5분을 가정하지 말고 실제 bucket ratio와
  catalog join coverage를 측정한다.
- **NO 토큰 가격은 1-YES 근사** (스프레드 무시 근사 — honeydew는 실전 진입 시 실제 NO 가격 `outcomePrices[1]`을 쓰므로, 얇은 호가에서 YES+NO 합이 1이 아닌 만큼 재생 오차가 있다).
- **시장이 해결되면 스냅샷이 끊긴다** → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1 (redeem 근사).

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

`sqlite3 <trades.db 경로>` 로 실행. 먼저 `.headers on` / `.mode column` 권장. trades 쿼리는 모두 `mode='live'` 필터 포함 (sim DB는 파일이 다르지만 안전장치. `skipped_markets`에는 mode 컬럼이 없다).

```sql
-- 3.0 상태 분포 (status는 enum name 대문자 저장)
SELECT status, COUNT(*) AS n FROM trades WHERE mode = 'live' GROUP BY status;

-- 3.1 완결 거래 총괄: 건수 / 승률 / 총·평균·중앙 수익률
SELECT
  COUNT(*)                                                  AS n,
  SUM(realized_pnl > 0)                                     AS wins,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl_usdc,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct,
  (SELECT ROUND((sell_price - buy_price) / buy_price * 100, 2)
     FROM trades
     WHERE status = 'COMPLETED' AND mode = 'live'
     ORDER BY (sell_price - buy_price) / buy_price
     LIMIT 1 OFFSET (SELECT (COUNT(*) - 1) / 2 FROM trades
                     WHERE status = 'COMPLETED' AND mode = 'live')
  )                                                         AS median_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live';

-- 3.2 exit_reason별 분해
--    판단 기준(STRATEGY.md §6): max_holding 비중 > 50% → 복원 가설 자체가 약한 것
SELECT
  exit_reason,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY exit_reason
ORDER BY n DESC;

-- 3.3 EXPIRED (수동 redeem 필요 — realized_pnl NULL이라 3.1/3.2에서 빠져 있음.
--    redeem 가치는 해결 결과에 따라 주당 0 또는 1: 총 P&L 판단 시 반드시 수동 가산)
SELECT id, condition_id, outcome, buy_price, buy_shares, buy_amount,
       market_end_date, substr(question, 1, 60) AS question
FROM trades
WHERE status = 'EXPIRED' AND mode = 'live';

-- 3.4 진입가 밴드 버킷 (현행 밴드 0.30~0.90) — entry_prob_min/max 교정 근거
SELECT
  CASE
    WHEN buy_price < 0.45 THEN '0.30-0.45'
    WHEN buy_price < 0.60 THEN '0.45-0.60'
    WHEN buy_price < 0.75 THEN '0.60-0.75'
    ELSE '0.75-0.90'
  END                                                       AS price_bucket,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY price_bucket
ORDER BY price_bucket;

-- 3.5 편차 버킷 (deviation_at_buy는 YES 기준 부호 있음 → ABS) — dev_min 교정 근거
SELECT
  CASE
    WHEN ABS(deviation_at_buy) < 0.07 THEN '0.05-0.07'
    WHEN ABS(deviation_at_buy) < 0.10 THEN '0.07-0.10'
    ELSE '0.10+'
  END                                                       AS dev_bucket,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND deviation_at_buy IS NOT NULL
GROUP BY dev_bucket
ORDER BY dev_bucket;

-- 3.6 방향별: YES 매수(반등) vs NO 매수(페이드)
SELECT
  outcome,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY outcome;

-- 3.7 코호트: 주말 진입 vs 평일 quiet 진입 (strftime %w: 0=일, 6=토, UTC)
--    weekends=true 가정 검증 (주말은 스포츠 해결 피크와 겹칠 수 있음)
SELECT
  CASE WHEN strftime('%w', buy_timestamp) IN ('0', '6')
       THEN 'weekend' ELSE 'weekday_quiet' END              AS cohort,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY cohort;

-- 3.8 진입 UTC 시각 분포 — quiet_hours_utc 창 교정 근거 (A-2 "7-11" 검증)
SELECT
  strftime('%H', buy_timestamp)                             AS utc_hour,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY utc_hour
ORDER BY utc_hour;

-- 3.9 보유시간 분석 (buy_timestamp ~ sell_timestamp) — max_holding_hours 교정 근거
SELECT
  exit_reason,
  COUNT(*)                                                            AS n,
  ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h,
  ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_hold_h,
  ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_h
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason;

-- 3.10 복원 완료 여부: 청산 시점 median 대비 편차 (|dev|<0.02 = 복원 완료로 간주)
--     take_profit인데 미복원(추세 편승)인지, max_holding인데 거의 복원(24h가 아깝게 짧은지) 구분
SELECT
  exit_reason,
  COUNT(*)                                                  AS n_with_dev,
  ROUND(AVG(ABS(deviation_at_exit)), 4)                     AS avg_abs_dev_exit,
  SUM(ABS(deviation_at_exit) < 0.02)                        AS restored_n
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND deviation_at_exit IS NOT NULL
GROUP BY exit_reason;

-- 3.11 현재 HOLDING (회고 시점 미완결 물량 — §4 스윕에는 포함시킬 것)
SELECT id, condition_id, outcome, buy_price, deviation_at_buy,
       ROUND((julianday('now') - julianday(buy_timestamp)) * 24, 1) AS held_h
FROM trades
WHERE status = 'HOLDING' AND mode = 'live';

-- 3.12 skip 사유 분포 (rapid_jump = 스캔~주문 사이 급등으로 진입 무산)
SELECT reason, COUNT(*) AS n
FROM skipped_markets
GROUP BY reason
ORDER BY n DESC;
```

보조: 진입 빈도 자체가 적으면 로그의 `제외 사유 요약` 라인을 집계해 병목 필터를 찾는다 (예: `dev_below_min`이 압도적이면 dev_min이 아니라 시장 자체가 조용했던 것).

```bash
grep -h "제외 사유 요약" <data/<job>/logs/*.log 경로> | tail -200
```

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### (a) 청산 스윕: TP / SL / max_holding 격자

완결(COMPLETED) + 보유(HOLDING) 거래 각각에 대해, 아카이브 스냅샷 시계열을 붙여 "다른 청산 파라미터였다면"의 P&L을 계산한다.

**격자 (현행 TP +0.06 / SL -0.06 / max_holding 24h / exit_hours 12 주변):**

- `POLYBOT_TAKE_PROFIT` ∈ {0.03, 0.04, **0.06**, 0.08, 0.10} — A-3(회전 가속) 가설 포함
- `POLYBOT_STOP_LOSS` ∈ {-0.04, **-0.06**, -0.08, -0.12}
- `POLYBOT_MAX_HOLDING_HOURS` ∈ {12, **24**, 36, 48}
- (선택) trailing {없음(현행), 3%, 5%} — "trailing 제거" 설계 결정의 사후 검증. cherry/banana whipsaw 문제 재발 여부 확인용

시계열 추출 (자기 DB에서 바로 — 스냅샷이 자기 진입 유니버스와 일치):

```sql
-- 거래별 진입 후 48h 시계열 (스윕 최대 보유시간에 맞춰 조정)
SELECT t.id            AS trade_id,
       t.outcome,
       t.buy_price,
       t.buy_timestamp,
       t.market_end_date,
       s.timestamp,
       s.probability   AS yes_price
FROM trades t
JOIN market_snapshots s
  ON s.condition_id = t.condition_id
 AND s.timestamp BETWEEN t.buy_timestamp AND datetime(t.buy_timestamp, '+48 hours')
WHERE t.status IN ('COMPLETED', 'HOLDING') AND t.mode = 'live'
ORDER BY t.id, s.timestamp;
```

결손 시(자기 DB 60일 보존 초과분 등) nectarine 아카이브로 보충:

```sql
ATTACH DATABASE '<nectarine trades.db 경로>' AS arch;
-- 위 쿼리에서 market_snapshots → arch.market_snapshots 로 교체
```

**시뮬레이션 규칙 (봇의 실제 우선순위를 그대로 재현):**

1. 토큰 가격 = `outcome='Yes'`면 `yes_price`, `'No'`면 `1 - yes_price` (근사 — §c 참고).
2. 시계열을 시간순으로 걸으며 첫 도달 조건으로 청산: **SL 먼저 검사** (가격 <= buy×(1+SL)) → TP (가격 >= min(buy×(1+TP), 0.99)) → 보유시간 >= max_holding이면 그 시점 가격으로 청산 → `market_end_date - 12h` 도달 시 그 시점 가격으로 time_exit. 5분 간격이라 같은 간격 안에서 TP/SL 동시 터치 판별이 불가하므로 봇과 동일하게 SL 우선(보수적).
3. 스냅샷이 조기 종료(시장 해결)된 거래: 실거래가 청산됐으면 `trades.sell_price`를 최종가로, 아니면 redeem 근사 0/1.
4. 격자별로 총 P&L($, buy_amount는 실거래 `buy_amount` 사용), 승률, 평균 보유시간을 표로 출력:

| TP \ SL | -0.04 | -0.06 | -0.08 | -0.12 |
|---|---|---|---|---|
| 0.03 | | | | |
| 0.04 | | | | |
| 0.06 | | (현행 기준선) | | |
| 0.08 | | | | |
| 0.10 | | | | |

(max_holding 값별로 위 표를 반복. 현행 셀 값이 실제 실적 §3.1과 크게 어긋나면 재현 로직부터 의심할 것 — 좋은 sanity check다.)

### (b) 전략 고유 노브 스윕 — 진입 규칙 재생

honeydew 진입은 post-instrumentation snapshot/catalog coverage가 충분한 구간에서 재현한다.
가격 5-point/50% gate뿐 아니라 volume baseline/recent 각각의 2-point/50% gate가 필요하며,
백필에는 volume이 없으므로 cold-start 구간은 제외될 수 있다. 핵심 노브 3개:

**노브 1 — `POLYBOT_DEV_MIN`: {0.04, **0.05**, 0.06, 0.08, 0.10}** (A-1 가설: 더 큰 이탈 → 승률 상승·빈도 하락)

**노브 2 — `POLYBOT_QUIET_HOURS_UTC` × `POLYBOT_QUIET_WEEKENDS`: {"4-13", "6-13"(현행), "7-11"} × {true(현행), false}** (A-2 가설: 코어 심야만 남기면 순수해지는가; §3.7/3.8 실적 코호트와 교차 확인)

**노브 3 — `POLYBOT_VOL_SPIKE_BLOCK`: {1.3, **1.5**, 2.0, 비활성(∞)}** (A-1 가설: 뉴스 필터 민감도; 비활성 격자는 "필터가 실제로 손실을 막았는가"의 직접 측정)

**재생 절차:**

1. 아카이브에서 회고 기간의 스냅샷을 condition_id별 시계열로 로드 (`liquidity >= 15000` 필터 — 자기 DB는 이미 이 유니버스).
2. 각 시각 t에 가격 window와 volume baseline/recent coverage를 각각 검증한다. volume 어느
   구간이든 2포인트/50% span이 없거나 baseline 평균이 0이면 후보를 제외한다. 유효할 때만
   median/dev/news gate, 방향, 가격 band, 24h cooldown을 순서대로 적용한다.
3. 가상 진입마다 §(a)의 청산 시뮬레이터(현행 TP/SL/24h/12h)로 청산 → 노브 값별 "잡혔을/걸러졌을 진입" 목록과 가상 성과 집계:

| 노브 값 | 가상 진입 수 | 승률 | 총 P&L | 실거래와 겹치는 진입 수 |
|---|---|---|---|---|

4. sanity check: 현행 노브 값의 재생 결과에 실제 진입(trades)이 대부분 포함되는지 확인. 괴리가 크면 원인(백필 median 차이, cold start, NO 근사)을 §6 신뢰도에 반영.

### (c) 데이터 한계 (수치 해석 전 반드시 명시)

- **NO 가격 1-YES 근사**: 실전 진입은 실제 NO 가격(`outcomePrices[1]`)을 쓴다. 얇은 호가에서 YES+NO 합 ≠ 1이며, honeydew는 *일부러 얇은 시간대만* 노리는 전략이라 이 근사 오차가 다른 봇보다 크다. NO 페이드 쪽 재생 성과는 ±1~2%p 오차 밴드로 읽을 것.
- **Execution evidence**: legacy `trades`는 GTC 접수 즉시 HOLDING/COMPLETED를 기록하지만 이는
  fill이 아니다. live 실현 결과는 `CONFIRMED order_fills`의 partial size/price/fee로 계산하고
  ledger gap을 분리한다. account evidence/NAV와도 대사하며 midpoint grid는 상대 비교만 한다.
- **봇이 본 median ≠ 재생 median**: 봇은 자기 DB 스냅샷 + prices-history 백필 병합으로 median을 계산했다. 백필 포인트는 아카이브에 없으므로, 특히 운영 첫 주(cold start)의 진입은 재생과 어긋날 수 있다 → §5 코호트 분리.
- **market metadata coverage**: post-instrumentation `market_catalog.end_date`와 `tags_json`으로
  잔여시간·category를 재현한다. legacy catalog gap은 `trades.market_end_date` 또는 Gamma
  재조회로 보충하고, metadata 없는 후보는 필터를 생략하지 말고 제외한다.
- **해결 시 스냅샷 중단**: 최종가는 `trades.sell_price` 또는 0/1 redeem 근사.
- **60일 보존**: 회고를 4주 시점에 하면 커버되지만, 미루면 초기 구간 유실. 회고가 늦어질 것 같으면 DB 파일을 먼저 복사해둘 것.
- **5분 간격**: 봇 사이클(3-5분)과 유사하지만 진입/청산 시점의 정확한 midpoint는 재현 불가. 간격 내 TP/SL 동시 터치는 SL 우선으로 보수 처리.

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트의 파생 시장들(동일 선거의 후보별 시장, 동일 대회 매치들)은 한 밤의 유동성 요인을 공유하며 함께 이탈하고 함께 복원(또는 함께 손절)된다. 특히 이 전략은 "같은 quiet 밤"에 다중 진입이 몰리는 구조다. `market_slug`/`question` 접두어 + 진입 UTC 날짜(`date(buy_timestamp)`)로 클러스터를 묶고, **이벤트(클러스터) 단위 승률/P&L을 별도 집계**하라. 클러스터 내 상관이 높으면 명목 n이 커도 독립 표본이 아니다.
- **코호트 분리**: ① 운영 첫 주 cold-start 진입과 정상 신호, ② 주말과 평일 quiet,
  ③ `run_audits.config_hash`/Git commit 변경 전후를 분리한다. legacy 구간만 Jenkins 기록으로
  보충한다.
- **명목 n ≠ 유효 n**: STRATEGY.md §6의 "30건 이상" 기준은 명목 건수다. 클러스터 집계 후 유효 n(독립 이벤트 수)이 15 미만이면 §6 교정안의 신뢰도를 한 단계 낮춰라. 승률 55%와 50%는 n=30에서 통계적으로 구분되지 않는다 — 방향성 제안 + 2차 테스트로 검증하는 구조를 유지할 것.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| `POLYBOT_DEV_MIN` | 0.05 | | §3.5 버킷 + §4(b) 노브1 스윕 | |
| `POLYBOT_QUIET_HOURS_UTC` | "6-13" | | §3.8 시각 분포 + §4(b) 노브2 | |
| `POLYBOT_QUIET_WEEKENDS` | true | | §3.7 코호트 + §4(b) 노브2 | |
| `POLYBOT_VOL_SPIKE_BLOCK` | 1.5 | | §4(b) 노브3 (비활성 대비 차단 성과) | |
| `POLYBOT_TAKE_PROFIT` | 0.06 | | §4(a) 격자 | |
| `POLYBOT_STOP_LOSS` | -0.06 | | §4(a) 격자 | |
| `POLYBOT_MAX_HOLDING_HOURS` | 24 | | §3.9 보유시간 + §3.10 복원도 + §4(a) | |
| `POLYBOT_ENTRY_PROB_MIN`/`MAX` | 0.30 / 0.90 | | §3.4 진입가 버킷 | |
| `POLYBOT_BUY_AMOUNT` | (Jenkins env 값) | | §3.1 총괄 + STRATEGY.md §6 증액/중단 기준 | |

- 근거 수치는 "버킷 X 승률 a% (n=b, 유효 n=c) vs 버킷 Y 승률 d%"처럼 표본 수를 병기한다.
- 판단 프레임(STRATEGY.md §6): 승률 >= 55% AND 평균 손익 > 0 → 증액 / 50-55% → 파라미터 조정 후 재검증 / < 50% 또는 총손익 < -10% → 중단. `max_holding` 비중 > 50%면 파라미터 교정이 아니라 가설 재검토.

**라운드 절차**: tunable knob는 Jenkins env로 반영하되 첫 성공 run의 새 `config_hash`와
Git commit을 확인해 이전 cohort와 분리한다.

1. **1차 회고 (이 문서)** → 교정안 확정 → Jenkins env 반영.
2. **2차 테스트 (4주)**: 단일 교체 대신 cherry처럼 **기본/변형 병행**도 가능 — 별도 Jenkins job에 `--job <변형명>`으로 DB를 분리하고 변형 env(예: STRATEGY.md §7의 A-1 엄격 / A-2 심야 집중 / A-3 회전 가속)를 얹어 A/B. 변형은 시뮬레이션(`--simulate`, trades_sim.db) 또는 소액 실전 중 리스크에 맞게 선택.
3. **3차 교정**: 2차 결과로 이 문서 §3~§5를 재실행해 수렴 여부 판단. 교정이 수렴하지 않고 승률이 50% 부근을 맴돌면 파라미터 문제가 아니라 가설(§1) 문제로 판정하고 중단을 검토.

## 7. 기준 정보

- 이 문서 생성 기준: commit `7bcf83f` (2026-07-06 시점 main), 작성일 2026-07-07.
- 전략 문서: `/Users/izowooi/git/t1/golden-honeydew/STRATEGY.md` (논지·파라미터 근거·리스크 §5·A/B 기준 §6·베리에이션 §7·구현 한계 §8).
- 코드 기준: env 파싱 `golden-honeydew/src/polybot/config.py`, 스키마 `src/polybot/db/models.py`, 진입 판정 `src/polybot/strategy/signals.py`(순수 함수 — 재생 구현 시 그대로 import 가능), 청산 `src/polybot/strategy/trader.py`.
- 슬롯 매핑 (2026-07-07 기준, 바뀔 수 있음 — find로 재확인): honeydew=polybot-eco(계정 golden-eco), nectarine=polybot-fox(계정 golden-fox), date=polybot-red, elderberry=polybot-cherry 워크스페이스(이름 주의). 운영 4계정 = apple x2, banana, cherry.
