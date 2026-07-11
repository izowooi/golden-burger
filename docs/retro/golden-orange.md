# golden-orange 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 전략: **Fear Spike Fade** — 평시 YES ≤ 15% tail 시장이 공포 헤드라인에 +10%p 이상 급등한 뒤, 스파이크 스톨(90분 경과 + 45분 신고가 없음 + 거래량 2배)이 확인되면 **NO 토큰을 매수**해 공포 프리미엄의 감쇠(YES 되돌림)를 수확.

## 0. 복붙용 회고 프롬프트

```text
/Users/izowooi/git/t1/docs/retro/golden-orange.md 를 읽고 §3~§5를 실행해서
§6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.

- 내 DB는 find로 직접 찾아라 (§2의 find 명령). live/sim 혼입 주의: mode='live'만 집계.
- 반사실 분석(§4)의 가격 시계열은 nectarine 중앙 아카이브를 사용해라 (§2 참조).
  orange는 NO 토큰을 보유하므로 NO 가격 = 1 - YES 근사임을 결과에 명시하라.
  단 retrace_target 판정은 YES 단위라 아카이브 YES를 그대로 쓸 수 있다.
- 공포 헤드라인은 시장 여러 개를 동시에 스파이크시킨다 (같은 지정학 이벤트의
  파생 시장들). 같은 이벤트/같은 날 진입은 이벤트 단위로 묶어 유효 표본 수를
  따로 세라 (§5). 상관 클러스터 무시하고 명목 n으로 신뢰도 매기지 마라.
- 초기 코호트(운영 초반, 거래량 게이트가 자체 스냅샷 축적 전까지 진입을 막는 구간)와
  정상 신호 코호트를 분리해서 보고해라.
- Jenkins job 설정의 export 블록 (키 제외):
  [여기에 Jenkins 잡의 export POLYBOT_* / LOG_LEVEL 블록을 붙여넣기 —
   DB provenance와 대조할 current/legacy cross-check로만 사용]
```

## 1. 전략 요약

평시 확률(base = 최근 7일 중 최근 6h를 제외한 구간의 YES 중앙값) ≤ 0.15인 tail 시장이 공포 헤드라인에 +0.10 이상 급등(단 YES ≤ 0.30 유지)하면, 대중은 확률이 아니라 결과의 끔찍함에 반응해 YES를 '보험/복권'으로 과매수한 상태다(probability neglect + availability cascade). 봇은 스파이크 시작(스냅샷에서 base+jump_min 첫 돌파) 후 90분 경과 + 최근 45분 YES 신고가 없음(스톨) + volume24h ≥ 윈도우 평균 x2를 모두 확인한 뒤 **NO 토큰**을 매수한다 (매수 직전 NO midpoint가 [0.70, 0.95] 밴드인지 재검증 — NO > 0.95면 스파이크 붕괴 완료로 쿨다운 skip, NO < 0.70이면 재점화로 이번 사이클만 skip). 청산은 trailing 없이 우선순위 순: 손절 -10%(`stop_loss`, YES 지속 상승 = 진짜 정보의 유일한 방어선) → **retrace 익절(주 청산, `retrace_target`)**: 스냅샷 최신 YES ≤ base + 0.5x(peak - base) → 보조 익절 NO +8%(0.99 캡, `take_profit`) → 보유 72h 초과(`max_holding`) → 해결 24h 전(`time_exit`). 재진입은 영구 밴 없이 HOLDING 여부 + 24h 쿨다운만 체크한다. 같은 급등 이벤트의 반대 가설(고점 유지 급등 편승)은 자매 봇 **golden-lime**이 A/B 쌍으로 검증 중이다 — 스파이크가 유지되면 lime이 벌고, 되돌아오면 orange가 번다.

### 파라미터 표 (env 이름은 `src/polybot/config.py`, 기본값은 `config.yaml` 실측)

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 15000 | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | 0 | 최소 24h 거래량 $ (0=비활성) |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 0.08 | 보조 익절 +8% (목표가 0.99 캡, 주 청산은 retrace_target) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.10 | 손절 -10% (YES 계속 상승 = 진짜 정보) |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 마지막 청산/skip 후 재진입 쿨다운 |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | true | CLOB /prices-history 백필 (cold start 완화) |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | [] (빈 값 = 비활성) | 제외 카테고리 (comma 구분) |
| strategy.base_window_days | `POLYBOT_BASE_WINDOW_DAYS` | 7 | base(평시 확률) 계산 윈도우 (일) |
| strategy.base_exclude_recent_hours | `POLYBOT_BASE_EXCLUDE_RECENT_HOURS` | 6 | base 계산에서 제외할 최근 시간 (스파이크 오염 방지) |
| strategy.base_max | `POLYBOT_BASE_MAX` | 0.15 | base 상한 (평시 tail 시장만) |
| strategy.jump_min | `POLYBOT_JUMP_MIN` | 0.10 | 스파이크 최소 상승폭 (yes_now - base) |
| strategy.yes_max | `POLYBOT_YES_MAX` | 0.30 | 스파이크 후 YES 상한 (NO 매수 밴드 하한 = 1 - 이 값) |
| strategy.spike_wait_minutes | `POLYBOT_SPIKE_WAIT_MINUTES` | 90 | 스파이크 시작 후 대기 (분) |
| strategy.stall_window_minutes | `POLYBOT_STALL_WINDOW_MINUTES` | 45 | 신고가 부재(스톨) 확인 윈도우 (분) |
| strategy.vol_mult_min | `POLYBOT_VOL_MULT_MIN` | 2.0 | volume24h ≥ 윈도우 평균 x 배수 |
| strategy.retrace_ratio | `POLYBOT_RETRACE_RATIO` | 0.5 | retrace 익절: YES ≤ base + ratio x (peak - base) |
| strategy.max_holding_hours | `POLYBOT_MAX_HOLDING_HOURS` | 72 | 최대 보유 시간 (되돌림 실패 청산) |
| time_based.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | 72 | 진입 최소 잔여 시간 (마감 임박 스파이크 = 진짜 정보 배제) |
| time_based.exit_hours | `POLYBOT_EXIT_HOURS` | 24 | 해결 이 시간 전 청산 |
| (로그) | `LOG_LEVEL` | INFO | 로그 레벨 (`--verbose`가 최우선) |

우선순위는 **env > config.yaml > 코드 기본값**이다. post-instrumentation 운영값은
`strategy_configs`/`run_audits`의 config hash와 Git cohort로 확정한다. §0 export 블록은
secret을 제거한 current/legacy cross-check다.

코드에만 있는 고정 상수 (env 없음, `signals.py`/`trader.py`/`bot.py`): 윈도우 유효성 `WINDOW_MIN_POINTS=5`·`WINDOW_MIN_COVERAGE=0.5`(7d 윈도우면 span ≥ 84h), TP 목표가 캡 `TP_PRICE_CAP=0.99`, NO 매수가 상한 `NO_PRICE_MAX=0.95`(매수 밴드 [1-yes_max, 0.95] = 기본 [0.70, 0.95]), 최소 주문 `MIN_ORDER_SIZE=5.0`주, 해결 leak 유예 `RESOLVED_GRACE_HOURS=24`, 자체 스냅샷 보존 `SNAPSHOT_RETENTION_DAYS=21`.

## 2. 데이터 위치와 스키마

### 자기 DB 찾기

orange의 Jenkins job명은 슬롯 배정에 따라 바뀔 수 있다 (2026-07-07 기준 매핑 미확정) — find로 찾는다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-orange/data*" -name "trades.db" 2>/dev/null
# 시뮬레이션 이력은 별도 파일
find /Users/jongwoopark/.jenkins/workspace -path "*golden-orange/data*" -name "trades_sim.db" 2>/dev/null
```

### 테이블 (`src/polybot/db/models.py` 실측)

**`trades`** — 거래 레코드. condition_id는 unique 아님(재진입 허용). 핵심 컬럼:

- 식별/시장: `id`, `condition_id`, `market_slug`, `question`, `outcome`(항상 "No"), `token_id`, `market_tags`
- 매수: `buy_price`(NO 가격), `buy_amount`, `buy_shares`, `buy_timestamp`, `buy_probability`
- 매도: `sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability`, `realized_pnl`
- 상태: `status` — **SQLAlchemy Enum은 이름으로 저장**되므로 SQL에서는 `'PENDING_BUY' / 'HOLDING' / 'PENDING_SELL' / 'COMPLETED' / 'SKIPPED' / 'EXPIRED'` 를 쓴다
- 사유: `entry_reason`(예: `"fear_spike_fade_base0.08_yes0.22_143m"`), `exit_reason`(`stop_loss` / `retrace_target` / `take_profit` / `max_holding` / `time_exit` / `resolved_unredeemed`)
- 진입 시그널 수치 (`*_at_buy` — 반사실 분석의 기준값): `yes_price_at_buy`(매수 시점 YES), `base_price_at_buy`(7d 중앙값), `spike_peak_at_buy`(진입 시점까지의 스파이크 고점), `spike_age_minutes_at_buy`(스파이크 시작 후 경과 분), `vol_mult_at_buy`(volume24h / 윈도우 평균)
- 청산 시그널: `yes_price_at_exit`(청산 판정에 쓴 스냅샷 최신 YES), `max_price`(진입 후 NO 최고가, 분석용 — trailing 없음)
- 회고 계약: `strategy_name`("orange"), `mode`("live"/"sim")
- 시간/기타: `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`, `volume_24h_at_buy`, `created_at`, `updated_at`

**`market_snapshots`** — 자체 스냅샷 (`condition_id`, `probability`=**항상 YES 가격**, `liquidity`, `volume_24h`, `timestamp`). 보존 **21일**(`SNAPSHOT_RETENTION_DAYS=21`) — 4주 회고 기간을 다 덮지 못하므로 반사실 분석은 아래 중앙 아카이브(60일)를 주로 쓰고, 자체 스냅샷은 retrace 판정 재현(청산 사이클이 실제로 본 YES 값) 대조용으로만 쓴다.

**`skipped_markets`** — skip 기록 (`condition_id`, `reason`, `skipped_at`). orange가 기록하는 reason은 `"spike_collapsed"`(매수 직전 NO > 0.95 = 페이드 기회 소멸) 하나다. 재진입 쿨다운의 시작점.

### 중앙 가격 아카이브 (반사실 분석용)

Gamma keyset cursor를 끝까지 순회한 당시 qualifying universe를 수집하므로, what-if 가격
시계열은 **nectarine DB의 market_snapshots**를 공용 아카이브로 쓴다. 고정 시장 수 대신
run별 cursor completion과 catalog/snapshot coverage를 확인한다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브(honeydew, liq >= $15k, job=polybot-eco):
find /Users/jongwoopark/.jenkins/workspace -path "*golden-honeydew/data*" -name "trades.db" 2>/dev/null
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 ≥ $10k, **60일 보존** — orange의 필터(liq ≥ $15k)보다 넓으므로 orange 유니버스를 포함한다
- orange 시그널(base/스파이크/스톨/retrace)은 전부 **YES 단위**라 아카이브 probability를 그대로 쓸 수 있다. 단 보유 손익(NO 토큰)의 가격은 **1-YES 근사** (스프레드 무시 근사임을 결과에 명시)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결된 보유분의 최종가는 `trades.sell_price` 또는 0/1(redeem) 가정으로 대체
- SQLite `ATTACH`로 자기 DB와 아카이브를 한 세션에서 조인 가능:

```sql
ATTACH DATABASE '/path/to/nectarine/trades.db' AS archive;
-- archive.market_snapshots 로 접근
```

### Jenkins 콘솔 로그

봇은 사이클마다 `제외 사유 요약 - reason: count` 한 줄을 남긴다 (스캔 병목 파악용). orange의 제외 사유 키(수치 접미사는 집계 시 정규화됨): 스캐너 레벨 `excluded_category`, `low_liquidity`, `no_price_data`, `spike_impossible`(YES > yes_max 또는 YES < jump_min — 시그널 계산 없이 skip) / 시그널 레벨 `low_volume`, `no_end_date`, `already_resolved`, `too_close_to_resolution`, `window_invalid`, `base_undefined`, `base_too_high`, `no_spike`, `yes_too_high`, `spike_too_fresh`, `spike_still_running`, `volume_unconfirmed`. 로그 파일은 워크스페이스 `data/<job>/logs/YYYYMMDD.log` 에도 남는다 — 어느 게이트에서 후보가 죽는지 보려면:

```bash
grep "제외 사유 요약" <workspace>/golden-orange/data/<job>/logs/*.log | tail -50
```

`spike_still_running`(스톨 게이트)과 `spike_too_fresh`(대기 게이트)가 지배적이면 진입이 게이트에 막히는 것이고, `volume_unconfirmed`가 지배적이면 거래량 게이트/스냅샷 축적 문제다 — §6 교정의 방향 판단에 쓴다.

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

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

판단 기준 (STRATEGY.md §6): 승률 ≥ 60% AND 평균 손익 > 0. **`retrace_target`이 주 수익원이어야 정상.** `stop_loss` 비중 > 40%면 게이트(대기/스톨/거래량)가 진짜 정보를 못 거르는 것 → 전략 재검토. `max_holding` 비중이 높으면 되돌림 가설 자체가 약한 것 (또는 72h가 짧은 것 — §4(a)로 판별).

### 3.3 미완결/이상 상태 점검

```sql
SELECT status, mode, COUNT(*) AS n FROM trades GROUP BY status, mode;

-- EXPIRED = 수동 redeem 필요 (발생 시 원인 분석 우선. NO 보유 중 시장 해결이면
-- YES 승 = 원금 전손 가능성 — 손절이 왜 안 걸렸는지 확인)
SELECT id, condition_id, question, buy_timestamp, buy_price, buy_amount
FROM trades WHERE status = 'EXPIRED';

-- skip 사유 분포 (spike_collapsed가 많으면 90분 대기 동안 되돌림이 이미
-- 끝나버린다는 신호 → POLYBOT_SPIKE_WAIT_MINUTES 단축 검토의 직접 근거)
SELECT reason, COUNT(*) AS n FROM skipped_markets GROUP BY reason ORDER BY n DESC;
```

### 3.4 진입 특성 버킷 — 밴드/게이트 교정 근거

진입 시점 YES 버킷 (`POLYBOT_YES_MAX` 교정 — 스파이크가 높이 올라간 시장일수록 페이드가 잘 되는가?):

```sql
SELECT
  CASE WHEN yes_price_at_buy < 0.15 THEN '0.10-0.15'
       WHEN yes_price_at_buy < 0.20 THEN '0.15-0.20'
       WHEN yes_price_at_buy < 0.25 THEN '0.20-0.25'
       ELSE '0.25-0.30' END                                       AS yes_bucket,
  COUNT(*)                                                        AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        / COUNT(*), 1)                                            AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl,
  ROUND(100.0 * AVG((sell_price - buy_price) / buy_price), 2)     AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND yes_price_at_buy IS NOT NULL
GROUP BY yes_bucket ORDER BY yes_bucket;
```

스파이크 크기(jump = yes - base) 버킷 (`POLYBOT_JUMP_MIN` 교정 — 큰 공포일수록 되돌림이 큰가, 아니면 진짜 정보인가?):

```sql
SELECT
  CASE
    WHEN yes_price_at_buy - base_price_at_buy < 0.12 THEN '0.10-0.12'
    WHEN yes_price_at_buy - base_price_at_buy < 0.15 THEN '0.12-0.15'
    WHEN yes_price_at_buy - base_price_at_buy < 0.20 THEN '0.15-0.20'
    ELSE '0.20+'
  END                                                             AS jump_bucket,
  COUNT(*)                                                        AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        / COUNT(*), 1)                                            AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND yes_price_at_buy IS NOT NULL AND base_price_at_buy IS NOT NULL
GROUP BY jump_bucket ORDER BY jump_bucket;
```

base 높이 / 스파이크 경과 / 거래량 배수 버킷 (`POLYBOT_BASE_MAX` / `POLYBOT_SPIKE_WAIT_MINUTES` / `POLYBOT_VOL_MULT_MIN` 교정 근거):

```sql
-- base_price_at_buy 버킷: 진짜 tail(<=0.05)과 경계(0.10-0.15)의 성과 차이
SELECT
  CASE WHEN base_price_at_buy < 0.05 THEN '<0.05'
       WHEN base_price_at_buy < 0.10 THEN '0.05-0.10'
       ELSE '0.10-0.15' END                                       AS base_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND base_price_at_buy IS NOT NULL
GROUP BY base_bucket ORDER BY base_bucket;

-- 스파이크 경과 분 버킷: 늦게 들어갈수록(피크 확정) 이기는가, 이미 되돌림을 놓치는가
SELECT
  CASE WHEN spike_age_minutes_at_buy < 120 THEN '90-120m'
       WHEN spike_age_minutes_at_buy < 180 THEN '120-180m'
       WHEN spike_age_minutes_at_buy < 360 THEN '180-360m'
       ELSE '360m+' END                                           AS age_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND spike_age_minutes_at_buy IS NOT NULL
GROUP BY age_bucket ORDER BY age_bucket;

-- 거래량 배수 버킷: 공포 거래량이 클수록 되돌림이 확실한가
SELECT
  CASE WHEN vol_mult_at_buy < 3.0 THEN '2-3x'
       WHEN vol_mult_at_buy < 5.0 THEN '3-5x'
       ELSE '5x+' END                                             AS vol_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live' AND vol_mult_at_buy IS NOT NULL
GROUP BY vol_bucket ORDER BY vol_bucket;

-- 진입 시점 피크 대비 이미 빠진 정도: (peak - yes_at_buy). 이미 되돌림이 시작된
-- 시장에 들어가는 비중이 크면 wait/stall이 너무 길다는 신호
SELECT
  CASE WHEN spike_peak_at_buy - yes_price_at_buy < 0.02 THEN '<0.02 (고원 진입)'
       WHEN spike_peak_at_buy - yes_price_at_buy < 0.05 THEN '0.02-0.05'
       ELSE '0.05+ (되돌림 진행 중)' END                          AS decay_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED' AND mode = 'live'
  AND spike_peak_at_buy IS NOT NULL AND yes_price_at_buy IS NOT NULL
GROUP BY decay_bucket ORDER BY decay_bucket;
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

-- 보유시간 구간별 성과 (max_holding_hours=72 교정 근거 — 되돌림은 몇 시간 안에 오는가.
-- 문헌 관찰은 "되돌림의 60%가 90~120분": <6h 버킷이 수익 대부분이어야 정상)
SELECT
  CASE
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 6  THEN '<6h'
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 24 THEN '6-24h'
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 48 THEN '24-48h'
    WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 72 THEN '48-72h'
    ELSE '72h+'
  END                                                             AS hold_bucket,
  COUNT(*) AS n,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
  ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY hold_bucket ORDER BY hold_bucket;
```

### 3.6 retrace 실현도와 max_price — retrace_ratio/TP 교정 근거

`yes_price_at_exit`(청산 판정에 쓴 YES)와 `base/peak`로 "되돌림이 실제로 얼마나 왔는지"를 잰다:

```sql
-- 청산 시점 되돌림 비율 = (peak - yes_at_exit) / (peak - base). 1.0이면 base까지 완전 복귀.
-- retrace_target 청산이 0.5 부근에 몰려 있고 그 후로도 더 내려갔다면(§4(a)로 확인)
-- POLYBOT_RETRACE_RATIO를 0.3~0.4로 낮추는 근거.
SELECT
  exit_reason,
  COUNT(*) AS n,
  ROUND(AVG((spike_peak_at_buy - yes_price_at_exit)
        / (spike_peak_at_buy - base_price_at_buy)), 2)            AS avg_retrace_realized
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND yes_price_at_exit IS NOT NULL AND spike_peak_at_buy IS NOT NULL
  AND base_price_at_buy IS NOT NULL
  AND spike_peak_at_buy > base_price_at_buy
GROUP BY exit_reason;

-- max_price(진입 후 NO 최고가): TP를 넘겨 더 갔는가 (보조 TP 0.08 교정 근거)
SELECT
  exit_reason,
  COUNT(*) AS n,
  ROUND(100.0 * AVG((max_price - buy_price) / buy_price), 2)      AS avg_peak_ret_pct,
  SUM(CASE WHEN (max_price - buy_price) / buy_price >= 0.08 THEN 1 ELSE 0 END) AS reached_tp8,
  SUM(CASE WHEN (max_price - buy_price) / buy_price >= 0.05 THEN 1 ELSE 0 END) AS reached_tp5
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND max_price IS NOT NULL
GROUP BY exit_reason;
```

`stop_loss`/`max_holding` 거래 중 `reached_tp5`가 많으면 → TP 0.05 또는 retrace_ratio 상향(0.6~0.7, 얕은 되돌림 실현)의 직접 근거.

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### (a) 청산 스윕: retrace_ratio / TP / SL / 보유시간 격자

대상: `status IN ('COMPLETED','HOLDING','EXPIRED')` AND `mode='live'` 인 모든 거래.

방법:

1. 자기 DB에서 거래별 `condition_id`, `buy_price`(NO), `buy_timestamp`, `base_price_at_buy`, `spike_peak_at_buy`, `market_end_date`, `sell_price`, `status`를 뽑는다.
2. 중앙 아카이브(nectarine)에서 `condition_id`별 `buy_timestamp` 이후의 YES 시계열을 붙인다. NO 가격 = `1 - probability` (근사).
3. 시계열을 시간순으로 걸으며 **현행과 같은 우선순위**로 청산을 시뮬레이션: ① SL — NO P&L ≤ sl → ② retrace — YES ≤ `base_price_at_buy + ratio x (spike_peak_at_buy - base_price_at_buy)` (**YES 단위 그대로 비교 — 근사 불필요**) → ③ TP — NO ≥ `min(buy_price x (1+tp), 0.99)` → ④ 보유 `max_holding_hours` 초과 → ⑤ `market_end_date - 24h` 도달 시 time_exit.
4. 스냅샷이 청산 전에 끊기면(시장 해결): 실거래 `sell_price`가 있으면 그 값, 없으면(HOLDING/EXPIRED) 0/1 redeem 가정 — 마지막 스냅샷 방향(YES 0.95+면 YES 승 = NO 전손)으로 추정하고 추정임을 명시. **NO 보유라 YES 승 해결은 -100%다** — 이 케이스 수를 별도 보고.
5. 격자별 총 P&L, 승률, 평균 보유시간을 표로.

격자 (현행 retrace 0.5 / TP +0.08 / SL -0.10 / 보유 72h 주변, STRATEGY.md §7 베리에이션 반영):

- `retrace_ratio`: **0.3, 0.4, 0.5(현행), 0.6, 0.7** ← 주 청산이므로 이 축이 제일 중요
- `take_profit`: **0.05, 0.08(현행), 0.12**
- `stop_loss`: **-0.06, -0.08, -0.10(현행), -0.15**
- `max_holding_hours`: **48, 72(현행), 96**

trailing은 의도적으로 없는 전략이므로 격자에 넣지 않는다. 단 §3.6의 `max_price` 결과가 "NO 고점 후 반납"이 뚜렷하면 트레일링 재도입을 별도 논점으로만 제기한다.

### (b) 전략 고유 노브 스윕: 진입 규칙 재생

핵심 노브 3개와 스윕 범위 (STRATEGY.md §7 A-1/A-2 반영):

| 노브 | env | 현행 | 스윕 |
|---|---|---|---|
| 스파이크 최소 상승폭 | `POLYBOT_JUMP_MIN` | 0.10 | **0.07, 0.10, 0.12, 0.15** |
| 대기/스톨 (분) | `POLYBOT_SPIKE_WAIT_MINUTES` / `POLYBOT_STALL_WINDOW_MINUTES` | 90 / 45 | **(60,30), (90,45), (120,45), (120,60)** |
| base 상한 | `POLYBOT_BASE_MAX` | 0.15 | **0.10, 0.15, 0.20** |

보조 노브: `POLYBOT_VOL_MULT_MIN` **1.5, 2.0(현행), 3.0**.

방법 (아카이브로 진입 규칙 재생 — orange 시그널은 전부 YES 단위라 아카이브만으로 재생 가능):

1. 아카이브에서 회고 기간의 `condition_id`별 5분 간격 YES 시계열을 로드한다. `liquidity >= 15000` 필터를 스냅샷 시점 값으로 적용한다.
2. 각 시점 t에서 `signals.py`의 `evaluate_entry`를 재현한다: base = [t-7d, t-6h] 구간 중앙값 ≤ base_max / jump = p_t - base ≥ jump_min AND p_t ≤ yes_max(0.30) / 스파이크 시작 = 윈도우에서 base+jump_min 첫 돌파 시각, 경과 ≥ wait분 / 최근 stall분(5분 간격이므로 45분 = 최대 9포인트)에 신고가 없음(그 이전 구간 고점 ≤ 비교) / `volume_24h(t) ≥ vol_mult_min x 윈도우 평균 volume_24h` (아카이브에 volume_24h가 5분 간격으로 있어 재생 가능). 윈도우 유효성(≥5포인트, span ≥ 84h)도 동일 적용.
3. 진입가는 NO = `1 - p_t` 근사, NO 매수 밴드 [1-yes_max, 0.95] 재검증도 동일 적용. 진입 후에는 24h 쿨다운 + HOLDING 중복 금지를 시뮬레이션하고, 현행 청산 규칙((a)의 ①~⑤)으로 가상 청산한다.
4. 노브 조합별로 "실거래에서 잡힌 진입 / 걸러졌을 진입 / 새로 잡혔을 진입"을 나눠 가상 P&L을 집계한다. 실거래와 겹치는 진입은 실거래 P&L을 우선 사용해 근사 오차를 줄인다.
5. 보조 검증: 실거래 `entry_reason` 문자열(`fear_spike_fade_base0.08_yes0.22_143m`)과 `base_price_at_buy`/`spike_age_minutes_at_buy`/`vol_mult_at_buy`로 재생 로직이 실제 진입을 재현하는지 몇 건 대조한다. 청산 쪽은 자체 `market_snapshots`(21일 보존)의 최신 YES와 `yes_price_at_exit`로 대조한다.

특히 **wait/stall 축은 §3.3의 `spike_collapsed` skip 수와 함께 해석하라**: (60,30)에서 가상 진입이 크게 늘고 승률이 유지되면 90분 대기가 기회를 흘리고 있는 것이고, 반대로 (120,60)에서 승률이 뚜렷이 오르면 초기 과열을 덜 걸러내고 있는 것이다.

### (c) 데이터 한계 (결과에 반드시 명시)

- **NO 토큰 = 1-YES 근사**: 스프레드 무시. 공포 스파이크 직후는 스프레드가 가장 벌어지는 구간이라 (STRATEGY.md §5-5) 진입가 근사 오차가 가장 클 때다. retrace 판정 자체는 YES 단위라 근사 오차가 없지만, P&L 환산은 NO 근사다.
- **Execution evidence**: legacy `trades`는 GTC 접수/order ID를 상태 전환 근거로 썼지만 이는
  fill이 아니다. 실현 결과는 `CONFIRMED order_fills`의 partial size/price/fee로 계산하고
  ledger gap을 분리한다. midpoint grid는 상대 비교만 한다.
- **5분 간격 vs 봇 사이클**: 아카이브는 5분 간격이라 45분 스톨 판정(최대 9포인트)이 봇의 3~5분 사이클 스냅샷과 미세하게 다르다. 스파이크 시작 시각도 5분 격자에 스냅되어 `spike_age`가 ±5분 오차를 갖는다. SL 통과 급등(계단식)이 5분 봉 안에서 일어나면 재현 불가.
- **백필 vs 아카이브**: 봇의 실제 판정은 자체 스냅샷 + `/prices-history` 백필 병합본(백필 포인트는 volume 없음)이고, 재생은 아카이브 단독이다. 특히 거래량 게이트는 봇에서는 자체 스냅샷만으로 계산되므로 재생과 차이가 날 수 있다 — (b)-5 대조로 편차 크기를 먼저 확인.
- **end date coverage**: post-instrumentation은 `market_catalog.end_date`로
  `entry_hours_min`/`time_exit`를 재현한다. legacy catalog gap은 `trades.market_end_date` 또는
  Gamma 재조회로 보충하고, metadata 없는 후보는 필터를 생략하지 말고 제외한다.
- **해결 시장 스냅샷 단절**: 해결 보유분의 최종가는 sell_price 또는 0/1 추정 — (a)-4의 추정 규칙을 결과에 명시. NO 보유 특성상 YES 승 해결 = -100%라 추정 오류의 비대칭이 크다.
- **아카이브 유니버스 편향**: keyset에는 고정 offset cap이 없지만 legacy 구간이나
  run/cursor/cadence gap이면 최신 공포 스파이크가 없을 수 있다. 실거래 condition ID의
  snapshot/catalog join coverage를 먼저 센다.
- **60일 보존**: 회고가 늦어지면 초기 거래의 시계열이 이미 삭제됐을 수 있다 — 기간 커버리지를 먼저 확인. 자체 스냅샷은 21일 보존이라 더 짧다.
- **excluded_categories/tags**: post-instrumentation `market_catalog.tags_json`으로 재현한다.
  legacy catalog gap은 별도 cohort/coverage로 표시한다.

## 5. 표본 주의사항

- **상관 클러스터**: 공포 헤드라인은 같은 지정학 이벤트의 파생 시장 여러 개를 동시에 스파이크시킨다 (핵/전쟁/휴전 시장군). 봇에는 상관 제어가 없어 한 헤드라인에 여러 포지션이 잡힐 수 있다 — `market_slug` 접두사·question 유사도·`DATE(buy_timestamp)`로 묶어 **이벤트 단위**로도 집계하라. 같은 날 진입 다발은 사실상 1개 베팅이다. STRATEGY.md §5-3의 연쇄 스파이크(손절 연타)도 이벤트 단위로 봐야 실제 리스크가 보인다.
- **코호트 분리**: (1) 운영 초기 — 거래량 게이트(`volume_unconfirmed`)는 백필 포인트에 volume이 없어 자체 스냅샷이 축적돼야 통과된다 (의도된 보수성). 초기 구간은 진입 유니버스가 편향되므로 분리. (2) Jenkins 중단 직후 — 윈도우 invalid/스파이크 시작 시각 왜곡으로 진입이 멈췄다 재개되는 구간. 코호트별로 나눠 보고 합쳐서 결론 내지 마라.
- **명목 n ≠ 유효 n**: 조건(base ≤ 0.15, +10%p, 72h+)을 모두 만족하는 이벤트는 드물다 (STRATEGY.md §5-6). 30건이어도 이벤트 클러스터로 묶으면 10건일 수 있다. §6 신뢰도는 유효 n 기준으로 매긴다. 표본 미달이면 결론을 미루고 8주 연장(STRATEGY.md §6)을 권고하라.
- **sim/live 혼입**: trades_sim.db가 분리돼 있어도 UNION 분석 시 반드시 `mode`로 거른다. 교차 봇 비교는 `strategy_name`('orange')으로 구분한다.
- **EXPIRED 제외 편향**: `resolved_unredeemed`는 `realized_pnl`이 NULL이라 §3 집계에서 자동 누락된다. 건수와 원금(`buy_amount` 합)을 별도 보고하라 — NO 보유라 YES 승 해결이면 원금 전손이므로, 이게 크면 승률이 좋아 보여도 실제 계좌는 다르다.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| 예: `POLYBOT_SPIKE_WAIT_MINUTES` | 90 | 60 | spike_collapsed skip 21건 vs 진입 9건, (60,30) 재생 시 가상 진입 +12건·승률 유지 64% (유효 n=8) | 중간 |

- 신뢰도 기준: 유효 n ≥ 15 & 방향 일관 = 높음 / 유효 n 5~14 = 중간 / 그 외 = 낮음.
- **라운드 절차**: tunable knob는 Jenkins env로 반영하고 첫 성공 run의 새
  `config_hash`/Git commit을 확인해 2차 4주 cohort를 분리한다. 기본/변형 slot 병행은
  job/DB/account를 분리하고 preset은 STRATEGY.md §7을 따른다.
- 자매 봇 **golden-lime**(거래량 동반 급등 편승, 의도적 A/B 쌍)의 같은 기간 성과와 교차 대조하라 — 같은 급등 이벤트군에서 lime이 벌고 orange가 잃었다면 "스파이크가 고점을 유지하는 국면"(진짜 정보 다수)이고, 반대면 감정 스파이크 국면이다. 게이트 교정 방향의 시장 국면 판단에 쓴다.

## 7. 기준 정보

- 이 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (2026-07-06)
- 전략 문서: `/Users/izowooi/git/t1/golden-orange/STRATEGY.md` (진입/청산 정밀 명세 §3, 실패 모드 §5, A/B 기준 §6, 베리에이션 §7, 구현 한계 §8)
- 운영 지침: `/Users/izowooi/git/t1/golden-orange/AGENTS.md`, 실행법: 같은 폴더 `README.md`
- 코드 기준: 시그널 `src/polybot/strategy/signals.py` (순수 함수), 청산 실행 `src/polybot/strategy/trader.py`, env 파싱 `src/polybot/config.py`, 스키마 `src/polybot/db/models.py`
- 알려진 슬롯 매핑(2026-07-07, 바뀔 수 있음 — find로 재확인): date=polybot-red, elderberry=polybot-cherry 워크스페이스(이름 주의), nectarine=polybot-fox, honeydew=polybot-eco. **orange 슬롯은 미확정 — §2의 find로 찾을 것.** 운영 4계정 = apple x2, banana, cherry (cherry는 변형 슬롯 병행 운용 중).
