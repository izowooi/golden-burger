# golden-elderberry 회고(포스트모템) 가이드

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 전략: **Panic Fade** — 원래 favorite(≥70%)이던 토큰의 공황 투매 급락(≥12%p)을 바닥 안정화 확인 후 역매수, 반등(+10%) 시 청산.

## 0. 복붙용 회고 프롬프트

```text
/Users/izowooi/git/t1/docs/retro/golden-elderberry.md 를 읽고 §3~§5를 실행해서
§6 표 형식으로 파라미터 교정안을 제시해줘.

- 내 DB는 find로 직접 찾아라 (§2의 find 명령). live/sim 혼입 주의: mode='live'만 집계.
- 반사실 분석(§4)의 가격 시계열은 nectarine 중앙 아카이브를 사용해라 (§2 참조).
- 같은 이벤트에서 파생된 시장들(negRisk 다후보)은 이벤트 단위로 묶어서 유효 표본 수를
  따로 세라 (§5). 하루에 몰린 panic 진입도 같은 뉴스 클러스터일 수 있다.
- 초기 백로그 코호트(운영 첫 48h, cold-start 백필 의존 구간)와 정상 신호 코호트를
  분리해서 보고해라.
- Jenkins job 설정의 export 블록 (키 제외):
  [여기에 Jenkins 잡의 export POLYBOT_* / LOG_LEVEL 블록을 붙여넣기 —
   운영 env는 repo에 없으므로 이것 없이는 현행값을 확정할 수 없다]
```

## 1. 전략 요약

원래 favorite(ref ≥ 0.70)이던 토큰이 악재/루머에 의한 공황 투매로 12%p 이상 급락한 뒤, 최근 45분 바닥 안정화(std ≤ 0.02, 신저가 아님)가 확인되면 역매수한다. 노리는 편향은 loss aversion에 의한 오버슈팅이며, "10%+ 급변은 mean-revert"라는 리서치 근거를 정조준한다. 진짜 정보에 의한 붕괴를 배제하기 위해 진입 밴드(0.35~0.75)와 잔여 시간(해결까지 ≥ 48h) 필터를 둔다. favorite 판별은 ref 윈도우(최근 48h, 단 최근 3h 제외)의 YES 최고가 ≥ 0.5면 YES쪽, 아니면 NO쪽(1-p 환산)이다. 청산은 trailing 없이 우선순위 순: 손절 -10% → 익절 +10%(목표가 0.99 캡) → 보유 48h 초과(`max_holding`) → 해결 24h 전(`time_exit`). 재진입은 영구 밴 없이 HOLDING 여부 + 24h 쿨다운만 체크한다. 같은 이벤트 클래스의 반대 가설(급등 편승)은 자매 봇 **golden-lime**이 A/B 쌍으로 검증 중이다.

### 파라미터 표 (env 이름은 `src/polybot/config.py`, 기본값은 `config.yaml` 실측)

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 0.10 | 익절 +10% (목표가 0.99 캡) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.10 | 손절 -10% |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 마지막 청산/skip 후 재진입 쿨다운 |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | true | prices-history 백필 (cold start 완화) |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | [] (빈 값 = 비활성) | 제외 카테고리 (comma 구분) |
| strategy.ref_window_hours | `POLYBOT_REF_WINDOW_HOURS` | 48 | 기준가(ref) 산출 윈도우 |
| strategy.ref_exclude_recent_hours | `POLYBOT_REF_EXCLUDE_RECENT_HOURS` | 3 | ref에서 제외할 최근 구간 (급락 자체 배제) |
| strategy.ref_min | `POLYBOT_REF_MIN` | 0.70 | ref 최소값 (원래 favorite) |
| strategy.drop_min | `POLYBOT_DROP_MIN` | 0.12 | 최소 낙폭 (ref - 현재가) |
| strategy.current_min | `POLYBOT_CURRENT_MIN` | 0.35 | 진입 밴드 하한 (붕괴 배제) |
| strategy.current_max | `POLYBOT_CURRENT_MAX` | 0.75 | 진입 밴드 상한 |
| strategy.stab_window_minutes | `POLYBOT_STAB_WINDOW_MINUTES` | 45 | 바닥 안정화 확인 윈도우 (분) |
| strategy.stab_max_std | `POLYBOT_STAB_MAX_STD` | 0.02 | 안정화 최대 std |
| strategy.max_holding_hours | `POLYBOT_MAX_HOLDING_HOURS` | 48 | 최대 보유 시간 (반등 실패 청산) |
| time_based.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | 48 | 진입 최소 잔여 시간 |
| time_based.exit_hours | `POLYBOT_EXIT_HOURS` | 24 | 해결 이 시간 전 청산 |
| (로그) | `LOG_LEVEL` | INFO | 로그 레벨 (`--verbose`가 최우선) |

우선순위: **env > config.yaml > 코드 기본값**. 운영 실측값은 Jenkins job의 export 블록에만 있다 (repo에 없음) — §0 프롬프트에 반드시 붙여넣을 것.

코드에만 있는 고정 상수 (env 없음, `signals.py`/`trader.py`): 윈도우 유효성 `min_points=5`·`min_coverage=0.5`(48h 윈도우면 span ≥ 24h), 안정화 `stab_min_points=3`, TP 목표가 캡 `0.99`, 최소 주문 `MIN_ORDER_SIZE=5.0`주, 해결 leak 유예 `RESOLVED_GRACE_HOURS=24`.

## 2. 데이터 위치와 스키마

### 자기 DB 찾기

elderberry는 **polybot-cherry** 워크스페이스 슬롯에서 돈다 (2026-07-07 기준 — 이름 주의! cherry 봇과 무관). job명은 바뀔 수 있으니 find로 찾는다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-elderberry/data*" -name "trades.db" 2>/dev/null
# 시뮬레이션 이력은 별도 파일
find /Users/jongwoopark/.jenkins/workspace -path "*golden-elderberry/data*" -name "trades_sim.db" 2>/dev/null
```

### 테이블 (`src/polybot/db/models.py` 실측)

**`trades`** — 거래 레코드. condition_id는 unique 아님(재진입 허용). 핵심 컬럼:

- 식별/시장: `id`, `condition_id`, `market_slug`, `question`, `outcome`("Yes"/"No"), `token_id`, `market_tags`
- 매수: `buy_price`, `buy_amount`, `buy_shares`, `buy_timestamp`, `buy_probability`
- 매도: `sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability`, `realized_pnl`
- 상태: `status` — **SQLAlchemy Enum은 이름으로 저장**되므로 SQL에서는 `'PENDING_BUY' / 'HOLDING' / 'PENDING_SELL' / 'COMPLETED' / 'SKIPPED' / 'EXPIRED'` 를 쓴다
- 사유: `entry_reason`(예: `"panic_fade_ref0.82_drop0.15"`), `exit_reason`(`take_profit` / `stop_loss` / `max_holding` / `time_exit` / `resolved_unredeemed`)
- 전략 데이터: `ref_price_at_buy`, `drop_at_buy`, `max_price`(진입 후 최고가, 분석용), `stabilization_range_at_buy`
- 회고 계약: `strategy_name`("elderberry"), `mode`("live"/"sim")
- 시간: `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`, `volume_24h_at_buy`, `created_at`, `updated_at`

**`market_snapshots`** — 자체 스냅샷 (`condition_id`, `probability`=**항상 YES 가격**, `liquidity`, `volume_24h`, `timestamp`). 단 **보존 7일**(`cleanup_old_snapshots(days=7)`) — 월간 회고의 반사실 분석에는 부족하므로 아래 중앙 아카이브를 쓴다.

**`skipped_markets`** — skip 기록 (`condition_id`, `reason` 예: `"rebound_before_entry"`, `skipped_at`). 재진입 쿨다운의 시작점.

### 중앙 가격 아카이브 (반사실 분석용)

모든 봇이 같은 gamma sweep(가장 오래된 활성 시장 ~2100개)을 스냅샷하므로, what-if 가격 시계열은 **nectarine DB의 market_snapshots**를 공용 아카이브로 쓴다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브(honeydew, liq >= $15k):
find /Users/jongwoopark/.jenkins/workspace -path "*golden-honeydew/data*" -name "trades.db" 2>/dev/null  # job=polybot-eco
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 ≥ $10k, **60일 보존** — elderberry의 필터(liq ≥ $20k)보다 넓으므로 elderberry 유니버스를 포함한다
- NO 토큰 가격은 **1-YES 근사** (스프레드 무시 근사임을 결과에 명시)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결된 보유분의 최종가는 `trades.sell_price` 또는 0/1(redeem) 가정으로 대체
- SQLite `ATTACH`로 자기 DB와 아카이브를 한 세션에서 조인 가능:

```sql
ATTACH DATABASE '/path/to/nectarine/trades.db' AS archive;
-- archive.market_snapshots 로 접근
```

### Jenkins 콘솔 로그

봇은 사이클마다 `제외 사유 요약 - reason: count` 한 줄을 남긴다 (스캔 병목 파악용). elderberry의 제외 사유 키: `window_invalid`, `no_ref_data`, `ref_below_min`, `drop_too_small`, `price_out_of_band`, `stab_insufficient_data`, `still_falling`, `not_stabilized_std` (수치 접미사는 집계 시 정규화됨). 로그 파일은 워크스페이스 `data/<job>/logs/YYYYMMDD.log` 에도 남는다 — 어느 게이트에서 후보가 죽는지 보려면 이 요약 라인을 grep:

```bash
grep "제외 사유 요약" <workspace>/golden-elderberry/data/<job>/logs/*.log | tail -50
```

## 3. 실적 분석 SQL (그대로 실행 가능)

모든 쿼리는 자기 DB(`trades.db`) 기준, live만 집계 (`mode='live'`). `sqlite3 <db경로>` 로 실행.

### 3.1 완결 거래 전체 요약

```sql
SELECT
  COUNT(*)                                                        AS n,
  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)               AS wins,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        / COUNT(*), 1)                                            AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl_usdc,
  ROUND(AVG(realized_pnl), 4)                                     AS avg_pnl_usdc,
  ROUND(100.0 * AVG((sell_price - buy_price) / buy_price), 2)     AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live';
```

수익률 중앙값 (SQLite에 median 없음):

```sql
SELECT ROUND(100.0 * (sell_price - buy_price) / buy_price, 2) AS median_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
ORDER BY (sell_price - buy_price) / buy_price
LIMIT 1
OFFSET (SELECT (COUNT(*) - 1) / 2 FROM trades
        WHERE status = 'COMPLETED' AND mode = 'live');
```

### 3.2 exit_reason별 분해 (전략 건강 진단의 핵심)

```sql
SELECT
  exit_reason,
  COUNT(*)                                                        AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        / COUNT(*), 1)                                            AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl,
  ROUND(AVG(realized_pnl), 4)                                     AS avg_pnl,
  ROUND(100.0 * AVG((sell_price - buy_price) / buy_price), 2)     AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY exit_reason
ORDER BY n DESC;
```

판단 기준 (STRATEGY.md §6): 승률 ≥ 55%(TP:SL 1:1이므로 손익분기+마진), `max_holding` 비율 < 40%. `max_holding`이 많으면 반등 가설 자체가 약하거나 48h가 짧은 것.

### 3.3 미완결/이상 상태 점검

```sql
SELECT status, mode, COUNT(*) AS n FROM trades GROUP BY status, mode;

-- EXPIRED = 수동 redeem 필요 (발생 시 원인 분석 우선, STRATEGY.md §6)
SELECT id, condition_id, question, buy_timestamp, buy_price, buy_amount
FROM trades WHERE status = 'EXPIRED';

-- skip 사유 분포 (rebound_before_entry가 많으면 안정화 대기가 너무 길다는 신호)
SELECT reason, COUNT(*) AS n FROM skipped_markets GROUP BY reason ORDER BY n DESC;
```

### 3.4 진입 특성 버킷 — 밴드/낙폭 교정 근거

진입가(buy_price) 0.05 버킷별 성과 (현행 밴드 0.35~0.75의 어느 구간이 버는가):

```sql
SELECT
  ROUND(CAST(buy_price * 20 AS INTEGER) / 20.0, 2)                AS price_bucket,
  COUNT(*)                                                        AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        / COUNT(*), 1)                                            AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl,
  ROUND(100.0 * AVG((sell_price - buy_price) / buy_price), 2)     AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY price_bucket
ORDER BY price_bucket;
```

낙폭(drop_at_buy) 버킷별 성과 (`POLYBOT_DROP_MIN` 교정 근거 — 깊은 공황일수록 되돌림이 큰가?):

```sql
SELECT
  CASE
    WHEN drop_at_buy < 0.15 THEN '0.12-0.15'
    WHEN drop_at_buy < 0.18 THEN '0.15-0.18'
    WHEN drop_at_buy < 0.25 THEN '0.18-0.25'
    ELSE '0.25+'
  END                                                             AS drop_bucket,
  COUNT(*)                                                        AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        / COUNT(*), 1)                                            AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl,
  ROUND(100.0 * AVG((sell_price - buy_price) / buy_price), 2)     AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND drop_at_buy IS NOT NULL
GROUP BY drop_bucket ORDER BY drop_bucket;
```

ref 높이 / 안정화 폭 / 방향(Yes·No) 별:

```sql
-- ref_price_at_buy 버킷 (POLYBOT_REF_MIN 교정 근거)
SELECT
  CASE WHEN ref_price_at_buy < 0.75 THEN '0.70-0.75'
       WHEN ref_price_at_buy < 0.85 THEN '0.75-0.85'
       ELSE '0.85+' END                                           AS ref_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND ref_price_at_buy IS NOT NULL
GROUP BY ref_bucket ORDER BY ref_bucket;

-- 안정화 고저폭 버킷 (POLYBOT_STAB_MAX_STD 교정 근거의 프록시)
SELECT
  CASE WHEN stabilization_range_at_buy < 0.02 THEN '<0.02'
       WHEN stabilization_range_at_buy < 0.04 THEN '0.02-0.04'
       ELSE '0.04+' END                                           AS stab_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND stabilization_range_at_buy IS NOT NULL
GROUP BY stab_bucket ORDER BY stab_bucket;

-- Yes/No 방향별 (NO쪽 1-p 환산 평가의 유효성 — STRATEGY.md 리스크 6 whipsaw 검증)
SELECT outcome, COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY outcome;
```

### 3.5 보유시간 분석

```sql
SELECT
  exit_reason,
  COUNT(*)                                                        AS n,
  ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h,
  ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_hold_h,
  ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_h
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason;

-- 보유시간 구간별 성과 (max_holding_hours=48 교정 근거)
SELECT
  CASE
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 6  THEN '<6h'
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 24 THEN '6-24h'
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 48 THEN '24-48h'
    ELSE '48h+'
  END                                                             AS hold_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY hold_bucket ORDER BY hold_bucket;
```

### 3.6 max_price 활용 — TP 상향/트레일링 재검토 근거

`max_price`(진입 후 최고가)는 분석용으로만 기록된다. "TP를 넘겨서 더 갔는가"를 공짜로 볼 수 있다:

```sql
SELECT
  exit_reason,
  COUNT(*) AS n,
  ROUND(100.0 * AVG((max_price - buy_price) / buy_price), 2)      AS avg_peak_ret_pct,
  SUM(CASE WHEN (max_price - buy_price) / buy_price >= 0.10 THEN 1 ELSE 0 END) AS reached_tp10,
  SUM(CASE WHEN (max_price - buy_price) / buy_price >= 0.06 THEN 1 ELSE 0 END) AS reached_tp6
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND max_price IS NOT NULL
GROUP BY exit_reason;
```

`stop_loss`/`max_holding` 거래 중 `reached_tp6`이 많으면 → TP 0.06으로 낮추는 A-1 변형의 직접 근거.

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### (a) 청산 스윕: TP/SL/보유시간 격자

대상: `status IN ('COMPLETED','HOLDING','EXPIRED')` AND `mode='live'` 인 모든 거래.

방법:

1. 자기 DB에서 거래별 `condition_id`, `outcome`, `buy_price`, `buy_timestamp`, `market_end_date`, `sell_price`, `status`를 뽑는다.
2. 중앙 아카이브(nectarine)에서 `condition_id`별 `buy_timestamp` 이후의 스냅샷 시계열을 붙인다. `outcome='No'`면 토큰 가격 = `1 - probability` (근사).
3. 시계열을 시간순으로 걸으며 **현행과 같은 우선순위**로 청산을 시뮬레이션: SL 도달 → TP 도달(목표가 `min(buy_price*(1+tp), 0.99)` 캡 유지) → 보유 `max_holding_hours` 초과 → `market_end_date - 24h` 도달 시 time_exit.
4. 스냅샷이 청산 전에 끊기면(시장 해결): 실거래 `sell_price`가 있으면 그 값, 없으면(HOLDING/EXPIRED) 0/1 redeem 가정 — 어느 쪽으로 해결됐는지는 마지막 스냅샷의 방향(YES가 0.95+면 YES 승 등)으로 추정하고 추정임을 명시.
5. 격자별 총 P&L, 승률, 평균 보유시간을 표로.

격자 (현행 TP +0.10 / SL -0.10 / 보유 48h 주변, STRATEGY.md §7 베리에이션 반영):

- `take_profit`: **0.06, 0.08, 0.10(현행), 0.12, 0.15**
- `stop_loss`: **-0.06, -0.08, -0.10(현행), -0.12, -0.15**
- `max_holding_hours`: **24, 48(현행), 72**

trailing은 의도적으로 제거된 전략이므로 (STRATEGY.md §2 — whipsaw 회피) 격자에 넣지 않는다. 단 §3.6의 `max_price` 결과가 "peak 후 반납"이 뚜렷하면 트레일링 재도입을 별도 논점으로만 제기한다.

### (b) 전략 고유 노브 스윕: 진입 규칙 재생

핵심 노브 3개와 스윕 범위:

| 노브 | env | 현행 | 스윕 |
|---|---|---|---|
| 최소 낙폭 | `POLYBOT_DROP_MIN` | 0.12 | **0.08, 0.10, 0.12, 0.15, 0.18** |
| 진입 밴드 하한 | `POLYBOT_CURRENT_MIN` | 0.35 | **0.30, 0.35, 0.40, 0.45** |
| 안정화 (윈도우/std) | `POLYBOT_STAB_WINDOW_MINUTES` / `POLYBOT_STAB_MAX_STD` | 45 / 0.02 | **(30,0.02), (45,0.02), (45,0.015), (90,0.015), (45,0.03)** |

방법 (아카이브로 진입 규칙 재생):

1. 아카이브에서 회고 기간의 `condition_id`별 5분 간격 YES 시계열을 로드한다. `liquidity >= 20000 AND volume_24h >= 10000` 필터를 스냅샷 시점 값으로 적용한다 (아카이브에 두 컬럼 모두 있음).
2. 각 시점 t에서 signals.py의 `evaluate_panic_fade` 로직을 재현한다: ref = [t-48h, t-3h] 구간 최고가 (YES max ≥ 0.5면 YES쪽, 아니면 1-p로 NO쪽), ref ≥ ref_min, drop = ref - p ≥ drop_min, current_min ≤ p ≤ current_max, 최근 stab_window분(5분 간격이므로 45분 = 최대 9포인트, ≥3포인트)의 std ≤ stab_max_std + 직전 포인트 min보다 신저가 아님. 윈도우 유효성(≥5포인트, span ≥ 24h)도 동일 적용.
3. 진입 후에는 24h 쿨다운을 시뮬레이션해 같은 시장 중복 진입을 막고, 현행 청산 규칙((a)의 3단계)으로 가상 청산한다.
4. 노브 조합별로 "실거래에서 잡힌 진입 / 걸러졌을 진입 / 새로 잡혔을 진입"을 나눠 가상 P&L을 집계한다. 실거래와 겹치는 진입은 실거래 P&L을 우선 사용해 근사 오차를 줄인다.
5. 보조 검증: 실거래 `entry_reason` 문자열(`panic_fade_ref0.82_drop0.15`)과 `drop_at_buy`로 재생 로직이 실제 진입을 재현하는지 몇 건 대조한다.

### (c) 데이터 한계 (결과에 반드시 명시)

- **NO 토큰 = 1-YES 근사**: 스프레드 무시. 특히 급락 직후는 스프레드가 벌어지는 구간이라 (STRATEGY.md §2) 진입가 근사 오차가 가장 클 때다.
- **체결 가정**: 봇 자체가 GTC limit at midpoint + orderID 수신 = 체결로 간주한다. 기록 P&L도, 가상 P&L도 midpoint 기준이라 실현치보다 과대평가될 수 있다.
- **5분 간격 vs 봇 사이클**: 아카이브는 5분 간격이라 45분 안정화 판정(봇은 3-5분 사이클 스냅샷)과 미세하게 다를 수 있다. SL/TP 사이 갭 통과(계단식 붕괴)는 5분 봉 안에서 일어나면 재현 불가.
- **`market_end_date` 부재**: 아카이브에는 endDate가 없다. `entry_hours_min >= 48h` / `time_exit` 재현은 자기 trades의 `market_end_date`를 조인하거나, 미거래 시장은 gamma API 조회로 보충한다 (불가하면 그 필터 생략을 명시).
- **해결 시장 스냅샷 단절**: 해결 보유분의 최종가는 sell_price 또는 0/1 추정 — (a)-4의 추정 규칙을 결과에 명시.
- **아카이브 유니버스 편향**: "가장 오래된 활성 시장 ~2100개" sweep이므로 최신 상장 시장의 panic은 아카이브에 없을 수 있다. 실거래에 있는데 아카이브에 시계열이 없는 condition_id 수를 먼저 세라.
- **60일 보존**: 회고가 늦어지면 초기 거래의 시계열이 이미 삭제됐을 수 있다 — 기간 커버리지를 먼저 확인.
- **excluded_categories/tags**: 아카이브에 카테고리 정보가 없어 카테고리 필터 반사실은 `trades.market_tags`가 있는 실거래분만 가능.

## 5. 표본 주의사항

- **상관 클러스터**: negRisk 다후보 이벤트의 여러 시장에 동시 진입할 수 있고 상관 제어가 없다 (STRATEGY.md §8). 같은 이벤트 파생 시장은 `market_slug` 접두사나 question 유사도로 묶어 **이벤트 단위**로도 집계하라. 또한 panic은 뉴스에 클러스터링된다 — 같은 날 진입 다발은 사실상 1개 베팅일 수 있다 (`DATE(buy_timestamp)`별 진입 수 확인).
- **코호트 분리**: (1) 운영 첫 48h — cold-start 구간은 prices-history 백필이 성공한 시장만 진입 가능해 유니버스가 편향된다. (2) Jenkins 중단 직후 — 윈도우 invalid로 진입이 멈췄다 재개되는 구간. 코호트별로 나눠 보고 합쳐서 결론 내지 마라.
- **명목 n ≠ 유효 n**: 30건이어도 이벤트 클러스터로 묶으면 10건일 수 있다. §6 신뢰도는 유효 n 기준으로 매긴다.
- **sim/live 혼입**: 같은 DB 파일이 아니어도(trades_sim.db 분리) UNION 분석 시 반드시 `mode`로 거른다. 교차 봇 비교는 `strategy_name`으로 구분한다.
- **EXPIRED 제외 편향**: `resolved_unredeemed`는 `realized_pnl`이 NULL이라 §3 집계에서 자동 누락된다. 건수와 원금(`buy_amount` 합)을 별도 보고하라 — 이게 크면 승률이 좋아 보여도 실제 계좌는 다르다.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| 예: `POLYBOT_DROP_MIN` | 0.12 | 0.15 | drop 0.15+ 버킷 승률 68% (n=14, 유효 n=9) vs 0.12-0.15 버킷 41% | 중간 |

- 신뢰도 기준: 유효 n ≥ 15 & 방향 일관 = 높음 / 유효 n 5~14 = 중간 / 그 외 = 낮음.
- **라운드 절차**: 제안값은 env만 바꾸면 적용된다 (코드 수정 불필요, Jenkins job export 블록 수정). 제안값으로 **2차 테스트 4주 → 3차 교정** 순으로 반복한다. cherry처럼 **기본/변형 슬롯 병행 운용**도 옵션이다 — STRATEGY.md §7의 A-1(얕은 낙폭·빠른 회전: `POLYBOT_DROP_MIN=0.08` `POLYBOT_TAKE_PROFIT=0.06` `POLYBOT_MAX_HOLDING_HOURS=24`) / A-2(깊은 낙폭만: `POLYBOT_DROP_MIN=0.18` `POLYBOT_CURRENT_MIN=0.40`) / A-3(엄격 안정화: `POLYBOT_STAB_WINDOW_MINUTES=90` `POLYBOT_STAB_MAX_STD=0.015`)이 사전 정의된 변형 후보다.
- 자매 봇 **golden-lime**(급등 편승, 의도적 A/B 쌍)의 같은 기간 성과와 교차 대조하면 "급변 이벤트가 회귀했는가/지속했는가"의 시장 국면 판단에 도움이 된다.

## 7. 기준 정보

- 이 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (2026-07-06)
- 전략 문서: `/Users/izowooi/git/t1/golden-elderberry/STRATEGY.md` (진입/청산 정밀 명세 §3, 실패 모드 §5, A/B 기준 §6, 베리에이션 §7, 구현 한계 §8)
- 운영 지침: `/Users/izowooi/git/t1/golden-elderberry/AGENTS.md`, 실행법: 같은 폴더 `README.md`
- 코드 기준: 시그널 `src/polybot/strategy/signals.py` (순수 함수), 청산 실행 `src/polybot/strategy/trader.py`, env 파싱 `src/polybot/config.py`, 스키마 `src/polybot/db/models.py`
- 알려진 슬롯 매핑(2026-07-07, 바뀔 수 있음 — find로 재확인): elderberry=polybot-cherry 워크스페이스(이름 주의), nectarine=polybot-fox, honeydew=polybot-eco, date=polybot-red. 운영 4계정 = apple x2, banana, cherry.
