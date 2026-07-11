# golden-date 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 전략: **Conviction Ladder** — 시간 사다리 확률 밴드 + 모멘텀 게이트 (cherry 고도화).

## 0. 복붙용 회고 프롬프트

```text
docs/retro/golden-date.md 를 읽고 §3~§5를 실행한 뒤, §6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.

- 봇 DB 위치: find /Users/jongwoopark/.jenkins/workspace -path "*golden-date/data*" -name "trades.db" 2>/dev/null
  (2026-07 기준 job=polybot-red. job명은 바뀔 수 있으니 find 결과를 믿어라. 시뮬레이션은 trades_sim.db 별도)
- 중앙 가격 아카이브(반사실 분석용): find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
- Jenkins job 설정의 export 블록 (키 제외, POLYBOT_* 전부): [여기 붙여넣기]

주의 지시:
1. 같은 이벤트에서 파생된 시장(예: 한 선거의 후보별 시장)은 이벤트 단위로 묶어 §5 방식으로 집계해라.
   개별 거래 n이 아니라 유효 n(이벤트 클러스터 수) 기준으로 신뢰도를 매겨라.
2. 운영 첫 며칠의 "백로그 코호트"(이미 밴드 안에 앉아 있던 시장 일괄 진입)와
   이후 정상 신호 코호트를 buy_timestamp로 분리해서 봐라.
3. §4(a) 청산 스윕과 §4(b) 사다리/모멘텀 노브 스윕을 모두 수행하고,
   격자별 총 P&L 표를 제시한 뒤 §6 표를 채워라.
4. exit_reason 분포에서 trailing_stop이 과반이면 whipsaw 가설을 우선 검증해라 (STRATEGY.md §6-3).
```

## 1. 전략 요약

**논지**: 예측시장의 favorite-longshot bias와 사전 해결 수렴(pre-resolution convergence)을 수확한다.
잔여 시간이 길수록 반전 리스크가 크므로, 잔여 시간 구간별로 다른 확률 밴드(3단 사다리)를 적용해
"해결이 가까울수록 비싸게, 멀수록 싸게만" favorite을 산다. 진입은 (1) 유동성 >= $15k AND 24h 거래량 >= $5k,
(2) YES/NO 중 확률 높은 쪽(favorite side) 자동 선택, (3) 시간 사다리 밴드 통과,
(4) 모멘텀 게이트(최근 6h favorite 변화 >= -0.01, 윈도우 invalid면 백필 시도 후 그래도 invalid면 진입 금지),
(5) 재진입 쿨다운 24h. 청산은 우선순위 순으로 손절 -8% → 익절 +12%(목표가 0.99 캡) →
트레일링 스탑(최고가 대비 -5%) → 해결 2h 전 시간 청산. 상세: `golden-date/STRATEGY.md`.

파라미터 표 (env 이름은 `src/polybot/config.py` 파싱 코드, 기본값은 `config.yaml` 실제 값):

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| 1회 매수 금액 | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| 최소 유동성 | `POLYBOT_MIN_LIQUIDITY` | 15000 | 진입 최소 liquidity ($) |
| 최소 거래량 | `POLYBOT_MIN_VOLUME_24H` | 5000 | 진입 최소 gamma volume24hr ($) |
| 익절 | `POLYBOT_TAKE_PROFIT` | 0.12 | 진입가 대비 +12% (목표가 0.99 캡) |
| 손절 | `POLYBOT_STOP_LOSS` | -0.08 | 진입가 대비 -8% |
| 트레일링 on/off | `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 스탑 사용 여부 |
| 트레일링 폭 | `POLYBOT_TRAILING_STOP_PERCENT` | 0.05 | 최고가 대비 하락률 |
| 시간 청산 | `POLYBOT_EXIT_HOURS` | 2 | 해결까지 이 시간 미만이면 청산 |
| 진입 최소 잔여 | `POLYBOT_ENTRY_HOURS_MIN` | 6 | 잔여 시간이 이 값 이하이면 진입 금지 |
| 사다리 구간 상한 | `POLYBOT_LADDER_H1` / `H2` / `H3` | 24 / 72 / 168 | 밴드1/2/3 상한 시간 (h) |
| 밴드1 (6~24h) | `POLYBOT_BAND1_MIN` / `POLYBOT_BAND1_MAX` | 0.80 / 0.95 | 확률 구간 (양끝 포함) |
| 밴드2 (24~72h) | `POLYBOT_BAND2_MIN` / `POLYBOT_BAND2_MAX` | 0.75 / 0.92 | 확률 구간 |
| 밴드3 (72~168h) | `POLYBOT_BAND3_MIN` / `POLYBOT_BAND3_MAX` | 0.70 / 0.88 | 확률 구간 |
| 모멘텀 윈도우 | `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | favorite 변화 관측 구간 (h) |
| 모멘텀 하한 | `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.01 | favorite 변화가 이 값 미만이면 진입 배제 |
| 재진입 쿨다운 | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 마지막 매도/skip 이후 재진입 대기 |
| 히스토리 백필 | `POLYBOT_HISTORY_BACKFILL` | true | CLOB prices-history로 cold start 완화 |
| 최대 포지션 | `POLYBOT_MAX_POSITIONS` | -1 | -1 = 무제한 |
| 제외 카테고리 | `POLYBOT_EXCLUDED_CATEGORIES` | [] (빈 배열) | 빈 값 = 필터 비활성 |
| YES-only 모드 | `POLYBOT_YES_ONLY` | false | index 0(Yes)만 매수 (CLI `--yes-only`가 우선) |
| 로그 레벨 | `LOG_LEVEL` | INFO | `--verbose`가 우선 |

인증(필수, 값은 Jenkins credential): `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`,
`POLYMARKET_SIGNATURE_TYPE`(기본 1).

우선순위는 env > config.yaml > code default다. post-instrumentation 실제 운영값은
`strategy_configs`와 `run_audits`의 config hash/Git cohort가 source of truth다. Jenkins export는
secret을 제거한 current/legacy cross-check로만 붙이고 현재 값을 과거 전체에 소급하지 않는다.

## 2. 데이터 위치와 스키마

### 2.1 자기 DB

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-date/data*" -name "trades.db" 2>/dev/null
```

2026-07 기준 job=`polybot-red`이지만 job명은 바뀔 수 있으니 find 결과를 믿는다.
시뮬레이션은 같은 폴더의 `trades_sim.db` 별도 파일. 월별 완결 거래 CSV(`trades_YYYY-MM.csv`)와
일자별 로그(`logs/YYYYMMDD.log`)도 같은 `data/<job>/` 아래에 있다.

**trades 테이블** (`src/polybot/db/models.py` 기준, 핵심 컬럼):

- 식별: `id`, `condition_id`(재진입 허용이라 unique 아님), `market_slug`, `question`, `outcome`("Yes"/"No"), `token_id`
- 매수: `buy_price`, `buy_amount`(USDC), `buy_shares`, `buy_timestamp`, `buy_probability`
- 매도: `sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability`, `realized_pnl`
- 상태: `status` — enum 이름 문자열로 저장: `'PENDING_BUY'`, `'HOLDING'`, `'PENDING_SELL'`, `'COMPLETED'`, `'SKIPPED'`, `'EXPIRED'`
  (SQLAlchemy Enum은 **대문자 이름**을 저장한다. 쿼리가 0행이면 `SELECT DISTINCT status FROM trades;`로 먼저 확인)
- 사유: `entry_reason`(예: `"ladder1_12.3h_mom+0.005"`), `exit_reason` —
  trader.py/signals.py 기준 값: `'stop_loss'`, `'take_profit'`, `'trailing_stop'`, `'time_exit'`, `'resolved_unredeemed'`(EXPIRED 처리, P&L 미확정)
- 시간/맥락: `max_price`(트레일링 추적 최고가), `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`, `market_tags`
- **회고 전용 컬럼** (entry_reason 파싱 불필요): `strategy_name`(="date"), `mode`("live"/"sim"),
  `volume_24h_at_buy`, `ladder_band_at_buy`(1/2/3), `momentum_at_buy`

**market_snapshots 테이블**: `condition_id`, `probability`(YES 가격), `liquidity`, `volume_24h`, `timestamp`.
단, **보존이 짧다** — bot.py가 매 사이클 `cleanup_old_snapshots(days=max(3×lookback, 7일))`을 돌려
기본 설정(lookback 6h)에서는 **약 7일치만 남는다**. 월간 회고의 가격 시계열로는 못 쓴다 → 중앙 아카이브 사용(§2.2).

**skipped_markets 테이블**: `condition_id`, `reason`(예: "rapid_jump"), `skipped_at`.
영구 밴이 아니라 skipped_at 기준 24h 쿨다운 후 재평가된다.

**Jenkins 콘솔/파일 로그**: 매 사이클 `제외 사유 요약 - reason: count` 한 줄이 남는다
(수치 접미사 정규화 키: `too_early`, `too_late`, `price_out_of_band1/2/3`(밴드 번호는 유지됨), `momentum_down`, `window_invalid` 등).
스캔 병목(어느 게이트에서 후보가 죽는지) 파악에 사용. `data/<job>/logs/YYYYMMDD.log`에도 동일하게 남는다.

### 2.2 중앙 가격 아카이브 (반사실 분석용)

Gamma keyset cursor를 끝까지 순회한 당시 qualifying universe를 수집하므로,
what-if 가격 시계열은 **nectarine DB의 market_snapshots**를 공용 아카이브로 쓴다. 고정
시장 수 대신 run별 cursor completion과 catalog/snapshot coverage를 확인한다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브: honeydew DB (liq >= $15k, 60일, job=polybot-eco)
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존**. date의 진입 필터(liq >= $15k)가 더 엄격하므로 date 후보는 대부분 포함된다.
- date는 favorite side를 자동 선택하므로 **NO 포지션이 존재한다**: NO 토큰 가격은 `1 - probability` 근사
  (스프레드 무시 근사임을 결과에 명시할 것)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1(redeem 결과)로 보정

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

`sqlite3 <trades.db 경로>` 에서 실행. 모두 live 거래 기준(`mode='live'`) —
시뮬레이션 분석은 `trades_sim.db`에서 같은 쿼리를 돌리면 된다.

### 3.1 완결 거래 요약

```sql
SELECT
  COUNT(*)                                                          AS n_trades,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3)   AS win_rate,
  ROUND(SUM(realized_pnl), 4)                                       AS total_pnl_usdc,
  ROUND(AVG(realized_pnl), 4)                                       AS avg_pnl_usdc,
  ROUND(AVG((sell_price - buy_price) / buy_price), 4)               AS avg_return,
  ROUND(MIN((sell_price - buy_price) / buy_price), 4)               AS worst_return,
  ROUND(MAX((sell_price - buy_price) / buy_price), 4)               AS best_return
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live';
```

중앙 수익률 (SQLite에 median 없음 → OFFSET 방식):

```sql
SELECT ROUND(ret, 4) AS median_return FROM (
  SELECT (sell_price - buy_price) / buy_price AS ret
  FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
  ORDER BY ret
)
LIMIT 1
OFFSET (SELECT (COUNT(*) - 1) / 2 FROM trades WHERE status = 'COMPLETED' AND mode = 'live');
```

### 3.2 exit_reason별 분해 (whipsaw 진단의 핵심)

```sql
SELECT
  exit_reason,
  COUNT(*)                                                          AS n,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3)   AS win_rate,
  ROUND(SUM(realized_pnl), 4)                                       AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price), 4)               AS avg_return
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY exit_reason
ORDER BY n DESC;
```

판정 기준(STRATEGY.md §6): `trailing_stop`이 과반이면 whipsaw 의심 → §4(a)에서 트레일링 지연/완화를 우선 스윕.

### 3.3 사다리 단별 성과 (밴드 교정의 1차 근거)

```sql
SELECT
  ladder_band_at_buy                                                AS band,
  COUNT(*)                                                          AS n,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3)   AS win_rate,
  ROUND(SUM(realized_pnl), 4)                                       AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price), 4)               AS avg_return,
  ROUND(AVG(buy_price), 3)                                          AS avg_buy_price,
  ROUND(AVG(hours_until_resolution_at_buy), 1)                      AS avg_hours_at_buy
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY ladder_band_at_buy
ORDER BY band;
```

판정 기준: 밴드3(72~168h)이 밴드1(6~24h)보다 유의미하게 나쁘면 `POLYBOT_LADDER_H3` 축소 (§4(b)).

### 3.4 진입가 버킷별 성과 (밴드 상·하한 교정 근거)

```sql
SELECT
  CASE
    WHEN buy_price < 0.75 THEN '0.70-0.75'
    WHEN buy_price < 0.80 THEN '0.75-0.80'
    WHEN buy_price < 0.85 THEN '0.80-0.85'
    WHEN buy_price < 0.90 THEN '0.85-0.90'
    WHEN buy_price < 0.95 THEN '0.90-0.95'
    ELSE '0.95+'
  END                                                               AS price_bucket,
  COUNT(*)                                                          AS n,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3)   AS win_rate,
  ROUND(SUM(realized_pnl), 4)                                       AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price), 4)               AS avg_return
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY price_bucket
ORDER BY price_bucket;
```

같은 방식으로 밴드×가격 교차 (`GROUP BY ladder_band_at_buy, price_bucket`)도 확인하면
"밴드2의 상단(0.90~0.92)만 나쁘다" 같은 국소 교정이 가능하다.

### 3.5 모멘텀 버킷별 성과 (게이트 임계 교정 근거)

```sql
SELECT
  CASE
    WHEN momentum_at_buy < 0     THEN 'a_neg(-0.01~0)'
    WHEN momentum_at_buy < 0.01  THEN 'b_0~+0.01'
    WHEN momentum_at_buy < 0.03  THEN 'c_+0.01~+0.03'
    ELSE                              'd_+0.03+'
  END                                                               AS mom_bucket,
  COUNT(*)                                                          AS n,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3)   AS win_rate,
  ROUND(SUM(realized_pnl), 4)                                       AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND momentum_at_buy IS NOT NULL
GROUP BY mom_bucket
ORDER BY mom_bucket;
```

`a_neg` 버킷(현행 게이트가 통과시킨 약한 하락)이 뚜렷이 나쁘면 `POLYBOT_MOMENTUM_MIN_CHANGE`를 0.0 이상으로 상향 검토.

### 3.6 보유시간 분석

```sql
SELECT
  exit_reason,
  COUNT(*)                                                                        AS n,
  ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1)      AS avg_hold_h,
  ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1)      AS min_hold_h,
  ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1)      AS max_hold_h
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason
ORDER BY n DESC;
```

trailing_stop/stop_loss의 보유시간이 수 시간 이내로 몰려 있으면 진입 직후 노이즈에 털리는 것(whipsaw).

### 3.7 재고 확인: HOLDING / EXPIRED / SKIPPED

```sql
-- 상태별 총괄 (EXPIRED는 realized_pnl NULL이라 §3.1~3.6에 안 잡힌다!)
SELECT status, COUNT(*) AS n, ROUND(SUM(buy_amount), 2) AS usdc_in
FROM trades GROUP BY status;

-- 수동 redeem 대상 목록: Polymarket 웹에서 해결 결과(0 or 1) 확인 후 회고 P&L에 수동 반영
SELECT id, condition_id, outcome, buy_price, buy_amount, market_end_date, question
FROM trades WHERE status = 'EXPIRED';

-- skip 사유 분포 (rapid_jump 빈발 = 밴드 상한이 실시간 가격을 못 따라간다는 신호)
SELECT reason, COUNT(*) AS n FROM skipped_markets GROUP BY reason ORDER BY n DESC;
```

**주의**: EXPIRED 포지션은 P&L 미확정이므로 반드시 redeem 가치(favorite 적중=1주당 $1, 실패=$0)를 확인해
총 P&L에 더한 뒤 결론을 내려라. 수렴 전략에서 EXPIRED는 대개 만기까지 간 favorite(승리 가능성 높음)이라
이를 빼면 성과가 체계적으로 과소평가된다.

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### 4.0 준비: 거래에 아카이브 시계열 붙이기

```sql
-- date 봇 DB를 연 상태에서 nectarine 아카이브를 ATTACH
ATTACH DATABASE '/Users/jongwoopark/.jenkins/workspace/<polybot-fox...>/golden-nectarine/data/<job>/trades.db' AS arch;

-- 거래별 보유 구간 favorite 가격 시계열 (NO는 1-YES 근사)
SELECT
  t.id, t.outcome, t.buy_price, t.buy_timestamp, t.sell_timestamp, t.exit_reason,
  s.timestamp,
  CASE WHEN t.outcome = 'Yes' THEN s.probability ELSE 1.0 - s.probability END AS fav_price
FROM trades t
JOIN arch.market_snapshots s ON s.condition_id = t.condition_id
WHERE t.mode = 'live' AND t.buy_timestamp IS NOT NULL
  AND s.timestamp >= t.buy_timestamp
ORDER BY t.id, s.timestamp;

-- 커버리지 검증: 스냅샷이 거의 없는 거래는 스윕에서 제외하고 그 수를 보고
SELECT t.id, t.condition_id, COUNT(s.id) AS n_snaps,
       MIN(s.timestamp) AS first_snap, MAX(s.timestamp) AS last_snap
FROM trades t
LEFT JOIN arch.market_snapshots s
  ON s.condition_id = t.condition_id
 AND s.timestamp BETWEEN t.buy_timestamp AND COALESCE(t.sell_timestamp, '9999-01-01')
WHERE t.mode = 'live'
GROUP BY t.id ORDER BY n_snaps ASC;
```

시장이 해결돼 스냅샷이 끊긴 구간은 `trades.sell_price`(청산가) 또는 redeem 가치 0/1로 시계열 끝을 보정한다.

### 4.1 (a) 청산 스윕: TP/SL/트레일링/시간 격자

완결+보유+EXPIRED 거래 각각에 대해, 매수 시점부터 아카이브 5분 시계열을 걸으며
signals.py `evaluate_exit`와 같은 우선순위(손절 → 익절 → 트레일링 → 시간)로 격자 시뮬레이션을 돌린다.
체결은 해당 스냅샷의 fav_price 그대로(midpoint 가정), max_price는 매수가로 초기화 후 갱신 — 실 구현과 동일.

현행 설정(TP 0.12 / SL -0.08 / trailing 0.05 / exit 2h) 주변 격자:

| 노브 | 격자 값 (굵게 = 현행) |
|---|---|
| take_profit | 0.08, 0.10, **0.12**, 0.15, 없음(0.99 캡 도달만) |
| stop_loss | -0.05, **-0.08**, -0.12, 없음 |
| trailing_percent | off, 0.03, **0.05**, 0.08 (STRATEGY.md 변형 A-3 = off) |
| exit_hours | **2**, 6, 12 (12 = cherry 원형과 비교) |

출력: 격자 셀별 `총 P&L / 승률 / 거래당 평균 / exit_reason 분포` 표.
트레일링은 max_price가 매수가로 초기화되므로 **실효 초기 손절선이 -trailing_percent라는 점**
(현행 -5%가 -8% 손절보다 먼저 발동)을 격자 해석에 반영할 것. 시뮬레이션 골격:

```python
def simulate_exit(series, buy_price, tp, sl, trail, exit_hours, end_date):
    """series: (timestamp, fav_price) 오름차순. 반환: (exit_price, exit_reason)."""
    cap = 0.99
    tp_target = min(buy_price * (1 + tp), cap) if tp else cap
    max_price = buy_price
    for ts, p in series:
        max_price = max(max_price, p)
        if sl and (p - buy_price) / buy_price <= sl:      return p, "stop_loss"
        if p >= tp_target:                                 return p, "take_profit"
        if trail and p < max_price * (1 - trail):          return p, "trailing_stop"
        if end_date and (end_date - ts).total_seconds() / 3600 < exit_hours:
            return p, "time_exit"
    return None, "series_end"   # 해결 도달 → redeem 0/1로 보정
```

### 4.2 (b) 전략 고유 노브 스윕: 사다리 / 모멘텀 게이트 / 시간창

핵심 노브 3개와 스윕 범위:

1. **사다리 깊이** `POLYBOT_LADDER_H3` ∈ {24, 72, **168**} — 24는 변형 A-1(밴드1만), 72는 밴드3 제거.
   보조로 밴드3 구간 `POLYBOT_BAND3_MIN/MAX` [0.70, 0.88]의 상하한 ±0.02~0.04 이동.
2. **모멘텀 게이트** `POLYBOT_MOMENTUM_MIN_CHANGE` ∈ {-0.03, **-0.01**, 0.0, +0.01},
   `POLYBOT_MOMENTUM_LOOKBACK_HOURS` ∈ {3, **6**, 12}.
3. **진입 시간창 하한** `POLYBOT_ENTRY_HOURS_MIN` ∈ {**6**, 12, 24}.

**조이기(tightening) 방향은 SQL만으로 가능** — 회고 컬럼이 진입 시점 수치를 이미 기록하므로,
"노브를 X로 조였다면 걸러졌을 거래"의 실현 성과를 빼보면 된다:

```sql
-- 예: 게이트를 0.0으로 올렸다면 걸러졌을 거래 (이 그룹의 P&L이 음수면 상향 근거)
SELECT COUNT(*) AS n_filtered, ROUND(SUM(realized_pnl), 4) AS pnl_removed
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND momentum_at_buy < 0.0;

-- 예: H3=72로 줄였다면 걸러졌을 거래 (= 밴드3 진입 전부)
SELECT COUNT(*) AS n_filtered, ROUND(SUM(realized_pnl), 4) AS pnl_removed
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND ladder_band_at_buy = 3;

-- 예: ENTRY_HOURS_MIN=12로 올렸다면 걸러졌을 거래
SELECT COUNT(*) AS n_filtered, ROUND(SUM(realized_pnl), 4) AS pnl_removed
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND hours_until_resolution_at_buy <= 12;
```

**풀기(loosening) 방향은 아카이브 재생 필요** — "노브를 X로 풀었다면 잡혔을 진입"의 가상 성과:

1. 아카이브 `market_snapshots`에서 기간 내 condition_id 목록을 뽑고,
   Gamma API(`https://gamma-api.polymarket.com/markets?condition_ids=...`)로 각 시장의 `endDate`를 조회한다
   post-instrumentation은 `market_catalog.end_date`를 사용하고, legacy catalog gap만 Gamma에서
   condition ID로 재조회한다.
2. 각 시장의 5분 시계열을 걸으며 매 포인트에서 `favorite = max(p, 1-p)` 가격,
   `hours_left = endDate - timestamp`, 최근 6h 윈도우 favorite 변화를 계산하고
   가상 사다리+게이트로 진입 판정(재진입 쿨다운 24h 적용, 시장당 순차).
   date 실운영과 동일하게 `liquidity >= 15000 AND volume_24h >= 5000` 스냅샷 필터를 함께 적용한다.
3. 가상 진입 각각에 §4.1의 현행 청산 규칙을 적용해 가상 P&L 산출 →
   "현행 규칙이 잡은 집합"과 "완화 규칙이 추가로 잡는 집합"의 성과를 비교한다.
4. 교차 검증: 같은 재생기를 현행 노브로 돌려 실제 trades와 진입 목록이 대체로 일치하는지 먼저 확인
   (일치하지 않으면 재생기 버그 — 스윕 결과를 신뢰하지 말 것).

### 4.3 (c) 데이터 한계 (결과 보고서에 반드시 명시)

- **모멘텀 재현 한계**: 실봇은 자체 스냅샷(사이클 주기) + prices-history 백필로 게이트를 판정했지만,
  재생은 아카이브 5분 격자로 근사한다. `window_invalid`로 실제 진입이 막혔던 순간은 재현 불가.
- **NO 근사**: NO favorite 가격 = 1 - YES는 스프레드/뎁스를 무시한 근사다.
- **Execution evidence**: legacy `trades`의 GTC 접수 기록은 actual fill이 아니다. 실현 결과는
  `CONFIRMED order_fills`의 partial size/price/fee로 계산하고 ledger gap을 분리한다. midpoint
  sweep은 execution sensitivity를 붙인 상대 비교로만 사용한다.
- **endDate 신뢰성**: Gamma endDate는 예정일이지 실제 해결 시각이 아니다. 조기 해결 시장은
  time_exit 재현이 어긋난다 (스냅샷 중단 시점을 실질 해결 시각의 근사로 병용).
- **아카이브 유니버스**: keyset에는 고정 offset cap이 없지만 legacy 배포 전 구간이나
  run/cursor/cadence gap으로 date 거래 시장이 빠질 수 있다. §4.0 coverage와 catalog join으로
  제외 건수를 먼저 보고한다.
- **자체 스냅샷 7일 보존**: date 자체 market_snapshots는 월간 회고용 시계열이 아니다 (§2.1).

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트의 파생 시장(한 선거의 후보별 시장, 같은 경기의 파생 라인 등)은
  결과가 동시에 결정된다. `market_slug` 접두사·`question` 유사도·`market_tags`로 이벤트 단위 클러스터를 만들고,
  클러스터당 1표로 승률을 재계산해라. 거래 40건이 이벤트 12개면 **유효 n은 12**다.
- **코호트 분리**: 운영 첫 며칠은 "이미 밴드 안에 앉아 있던" 시장들이 일괄 진입하는 백로그 코호트다.
  `buy_timestamp`로 첫 3~5일 코호트와 이후 정상 신호 코호트를 분리 집계해라. 백로그 코호트는
  모멘텀 게이트가 백필 기반으로 판정돼 정상 코호트와 신호 품질이 다를 수 있다.
- **명목 n ≠ 유효 n**: 30+ 거래(STRATEGY.md §6 기준)를 채워도 클러스터·코호트 보정 후 유효 n이 15 미만이면
  §6 신뢰도를 "낮음"으로 매기고 파라미터 변경 폭을 보수적으로 잡아라.
- **EXPIRED 제외 편향**: §3.7 참조 — EXPIRED를 빼고 계산한 승률/P&L은 편향돼 있다. redeem 가치 반영 전후를 모두 보고.
- **꼬리 리스크 표본 부재**: 수렴 전략의 손익 구조는 "다수 소형 이익 + 소수 대형 손실"이다.
  4주 표본에 0.9대 favorite 붕괴(0.9 → 0.2 갭)가 없었다고 해서 그 리스크가 없는 게 아니다 —
  손절 완화(-12%)나 트레일링 off 제안 시 이 점을 근거 수치와 별도로 명시해라.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| `POLYBOT_TAKE_PROFIT` | 0.12 | | §4.1 격자 셀 P&L | |
| `POLYBOT_STOP_LOSS` | -0.08 | | §4.1 격자 셀 P&L | |
| `POLYBOT_TRAILING_STOP_ENABLED` / `_PERCENT` | true / 0.05 | | §3.2 exit 분포 + §4.1 | |
| `POLYBOT_EXIT_HOURS` | 2 | | §4.1 격자 셀 P&L | |
| `POLYBOT_LADDER_H3` (+ 밴드3 상하한) | 168 (0.70/0.88) | | §3.3 밴드별 성과 + §4.2 | |
| `POLYBOT_MOMENTUM_MIN_CHANGE` | -0.01 | | §3.5 버킷 + §4.2 | |
| `POLYBOT_MOMENTUM_LOOKBACK_HOURS` | 6 | | §4.2 | |
| `POLYBOT_ENTRY_HOURS_MIN` | 6 | | §4.2 조이기 SQL | |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | | 재진입 표본 있으면 | |

- "현행"은 기간 내 `strategy_configs`/`run_audits`의 resolved config cohort로 채운다. Jenkins
  export는 current/legacy cross-check다.
- 근거 수치는 "격자 (TP 0.10, SL -0.08, trail off)에서 총 P&L +$X vs 현행 +$Y (n=Z, 유효 n=W)" 형식으로.

**라운드 절차 (2차 테스트 → 3차 교정)**:

1. tunable knob는 Jenkins env로 반영하고 첫 성공 run의 새 `config_hash`/Git commit을 확인한다.
2. cherry처럼 **기본/변형 병행 운용** 옵션: 같은 코드로 별도 Jenkins job(`--job <이름>`으로 DB 분리)을 하나 더 만들어
   기본 슬롯은 현행 유지, 변형 슬롯에 제안 env를 적용해 A/B 비교한다.
   STRATEGY.md §7의 사전 정의 변형: A-1(`POLYBOT_LADDER_H3=24`), A-2(`POLYBOT_YES_ONLY=true`),
   A-3(`POLYBOT_TRAILING_STOP_ENABLED=false`). 단, **같은 지갑으로 두 job을 돌리면 같은 시장 이중 매수 가능**
   (중복 방지가 job DB 단위 — STRATEGY.md §8) — 지갑 분리 또는 리스크 인지 후 진행.
3. 2차 운용 4주 후 이 문서로 다시 회고를 돌려 3차 교정. 신뢰도 "낮음" 항목은 2차에서 변경하지 말고 표본만 더 모은다.
4. 회고 절차 일반론은 `docs/ab-retro-playbook.md` 참조.

## 7. 기준 정보

- 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (main)
- 전략 문서: `/Users/izowooi/git/t1/golden-date/STRATEGY.md` (논지·허점 수정 이력·변형 안)
- 코드 기준: `golden-date/src/polybot/strategy/signals.py`(진입/청산 순수 함수),
  `strategy/trader.py`(주문·exit_reason 기록), `db/models.py`(스키마), `config.py`(env 파싱)
- 운영 슬롯 (2026-07-07 기준, 변동 가능): date=polybot-red. 아카이브: nectarine=polybot-fox, honeydew=polybot-eco.
  elderberry는 polybot-cherry 워크스페이스를 쓰므로 find 결과 해석 시 이름에 주의.
