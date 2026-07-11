# golden-grape 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 대상 봇: **Cascade Rider** — 24h 소폭 드리프트(+4~10%p) + 버킷 일관성 + 거래량 가속 편승 전략.

## 0. 복붙용 회고 프롬프트

```text
/Users/izowooi/git/t1/docs/retro/golden-grape.md 를 읽고 §3(실적 SQL)~§5(표본 주의)를 실행한 뒤,
§6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.

- 봇 자기 DB는 §2의 find 명령으로 찾아라 (job명은 바뀔 수 있음).
- 반사실(what-if) 가격 시계열은 grape 자기 스냅샷(보존 ~7일)이 아니라
  nectarine DB의 market_snapshots(60일 공용 아카이브)를 써라 (§2).
- §4(a) 청산 스윕과 §4(b) 노브 스윕(드리프트 밴드 / consistency_min / vol_accel_min)을
  격자대로 돌리고, 격자별 총 P&L·거래수·승률 표를 제시해라.
- 같은 이벤트에서 파생된 시장들(negRisk 다후보 등)은 이벤트 단위로 묶어 집계하고,
  배포 초기 cold-start/백필 코호트는 정상 신호 코호트와 분리해서 봐라 (§5).
- NO 포지션의 아카이브 가격은 1-YES 근사임을 결과에 명시해라.
- Jenkins export 블록은 DB provenance와 대조할 current/legacy cross-check다. 실키를 제외한다:
  [여기에 Jenkins env export 블록 붙여넣기 — POLYMARKET_PRIVATE_KEY 등 실키는 제외]
```

## 1. 전략 요약

**논지**: 예측시장 참가자는 정보를 동시에 얻지 않는다. 정보에 가까운 소수가 먼저 베팅해 가격이 소폭 움직이면, 그 가격 변화가 신호가 되어 대중이 뒤따른다(information cascade / underreaction). 확산이 끝나기 전의 "소폭이지만 일관된 24h 드리프트 + 거래량 가속" 구간에 편승해 잔여 이동을 수확한다. 리서치 근거: 2~3% 일일 변화는 지속 확률이 가장 높고, 10%+ 급변은 mean-revert 영역이므로 배제한다.

**진입 (모두 AND, 매수 토큰 기준 가격 p)**: liquidity >= $20k, volume24hr >= $10k, 해결까지 >= 48h → 24h 윈도우 유효(포인트 >= 5, 커버리지 >= 12h; invalid면 CLOB `/prices-history` 백필, 그래도 invalid면 진입 금지) → YES 상승 드리프트면 YES 매수 / 하락이면 NO 매수 → p ∈ [0.40, 0.80] → 24h 드리프트 ∈ [+0.04, +0.10] → 4h 버킷 6개 중 >= 70% 비음 변화 → 현재 volume24hr >= 윈도우 평균의 1.2배 → HOLDING 없음 + 마지막 청산/skip 후 24h 경과. 매수 직전 CLOB midpoint 재검증(밴드 초과 급등 시 `rapid_jump` skip). `entry_reason`은 `cascade_up`(YES) / `cascade_down`(NO).

**청산 (우선순위 순, 매 사이클)**: ① 손절 P&L <= -8% (`stop_loss`) → ② 익절 현재가 >= min(진입가×1.15, 0.99) (`take_profit`) → ③ 드리프트 소멸: 최근 6h 매수 토큰 변화 <= 0 (`drift_death`) → ④ 트레일링 최고가 대비 -6% (`trailing_stop`) → ⑤ 해결까지 < 24h (`time_exit`). 해결된 시장은 midpoint 조회 실패 + end_date 24h 경과 시 EXPIRED (`resolved_unredeemed`, 수동 redeem 필요, realized_pnl NULL).

### 파라미터 표 (env 이름은 `src/polybot/config.py`, 기본값은 `config.yaml` 실제 값)

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| prob_min | `POLYBOT_PROB_MIN` | 0.40 | 매수 토큰 가격 하한 |
| prob_max | `POLYBOT_PROB_MAX` | 0.80 | 매수 토큰 가격 상한 (러닝룸) |
| drift_lookback_hours | `POLYBOT_DRIFT_LOOKBACK_HOURS` | 24 | 드리프트 판정 윈도우 (h) |
| drift_min | `POLYBOT_DRIFT_MIN` | 0.04 | 드리프트 하한 (+4%p) |
| drift_max | `POLYBOT_DRIFT_MAX` | 0.10 | 드리프트 상한 (mean-revert 배제) |
| bucket_hours | `POLYBOT_BUCKET_HOURS` | 4 | 일관성 버킷 크기 (h) |
| consistency_min | `POLYBOT_CONSISTENCY_MIN` | 0.70 | 비음(>=0) 버킷 비율 하한 |
| vol_accel_min | `POLYBOT_VOL_ACCEL_MIN` | 1.2 | 거래량 가속 배수 하한 |
| death_window_hours | `POLYBOT_DEATH_WINDOW_HOURS` | 6 | 드리프트 소멸 판정 윈도우 (h) |
| death_window_min_points | `POLYBOT_DEATH_WINDOW_MIN_POINTS` | 3 | 소멸 판정 최소 포인트 |
| death_window_min_coverage | `POLYBOT_DEATH_WINDOW_MIN_COVERAGE` | 0.5 | 소멸 윈도우 최소 시간 coverage |
| entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | 48 | 해결까지 최소 잔여시간 (h) |
| exit_hours | `POLYBOT_EXIT_HOURS` | 24 | 시간 청산 기준 (h) |
| trailing_stop.enabled | `POLYBOT_TRAILING_STOP_ENABLED` | true | 트레일링 스탑 on/off |
| trailing_stop.percent | `POLYBOT_TRAILING_STOP_PERCENT` | 0.06 | 최고가 대비 하락률 |
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 USDC |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 20000 | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | 10000 | 최소 24h 거래량 $ |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 0.15 | 익절 % (목표가 0.99 캡) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.08 | 손절 % |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 최대 동시 포지션 (-1 무제한) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | 24 | 재진입/skip 쿨다운 (h) |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | true | CLOB `/prices-history` 백필 |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | [] (비활성) | 제외 카테고리 (comma 구분) |
| (로그) | `LOG_LEVEL` | INFO | 로그 레벨 |

코드 상수(env 아님): 윈도우 유효성 `min_points=5`, `min_coverage=0.5` (`signals.py`), 최소 주문 5주 (`trader.py MIN_ORDER_SIZE`), 익절 캡 0.99, EXPIRED 판정 유예 24h (`RESOLVED_GRACE_HOURS`).

post-instrumentation 실제 운용값은 `strategy_configs`와 `run_audits`의 config hash/Git cohort가
source of truth다. Jenkins export는 secret을 제거한 current/legacy cross-check로만 사용한다.

## 2. 데이터 위치와 스키마

### 자기 DB

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-grape/data*" -name "trades.db" 2>/dev/null
# 시뮬레이션은 같은 디렉터리의 trades_sim.db (별도 파일)
# 같은 디렉터리에 trades_YYYY-MM.csv(월별 청산 append 로그), logs/YYYYMMDD.log(사이클 로그)도 있다
```

job명은 바뀔 수 있으니 반드시 find로 찾는다. Jenkins 콘솔/파일 로그에는 사이클마다 `제외 사유 요약 - reason: count` 한 줄이 남는다 — 어느 게이트(`window_invalid`, `drift_too_small_*`, `inconsistent_drift_*`, `vol_accel_too_low_*` 등)가 스캔 병목인지 파악할 때 사용.

테이블 (`src/polybot/db/models.py` 기준, SQL은 이 컬럼만 사용):

- **trades**: `condition_id`, `market_slug`, `question`, `outcome`("Yes"/"No"), `token_index`(0=YES, 1=NO), `token_id`, `buy_price`, `buy_amount`, `buy_shares`, `buy_timestamp`, `buy_probability`, `sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability`, `realized_pnl`(USD 절대액), `status`, `entry_reason`("cascade_up"/"cascade_down"), `exit_reason`, `max_price`, `market_end_date`, `hours_until_resolution_at_buy`, **`drift_at_buy`**, **`consistency_at_buy`**, **`vol_accel_at_buy`**, `strategy_name`("grape"), `mode`("live"/"sim"), **`volume_24h_at_buy`**, **`drift_at_exit`**(drift_death 판정 값), `liquidity_at_buy`, `market_tags`, `created_at`, `updated_at`
  - `status`는 **enum 이름 문자열**로 저장: `PENDING_BUY` / `HOLDING` / `PENDING_SELL` / `COMPLETED` / `SKIPPED` / `EXPIRED`
  - `exit_reason` 값 (trader.py): `stop_loss`, `take_profit`, `drift_death`, `trailing_stop`, `time_exit`, `resolved_unredeemed`
  - `buy_price`는 **매수 토큰 기준** 가격 (NO 포지션이면 NO 가격). `buy_probability`도 동일 값.
  - 재진입 허용 전략이라 `condition_id`는 unique가 아니다.
- **market_snapshots**: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(naive UTC)
- **skipped_markets**: `condition_id`, `reason`("rapid_jump" 등), `skipped_at` — timestamp 기반 쿨다운용

모든 DateTime은 **naive UTC**.

### 중앙 가격 아카이브 (반사실 분석용) — 필수

grape는 자체 market_snapshots를 갖지만(liq >= $20k 유니버스), **보존이 약 7일뿐이다** (`bot.py`: `max(7, ceil(lookback*3/24))` = 7일 cleanup). 한 달 회고의 what-if 가격 시계열로는 부족하므로 **nectarine DB의 market_snapshots를 공용 아카이브로 쓴다**:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브: honeydew DB (liq >= $15k, 60일, job=polybot-eco)
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존**. Gamma keyset cursor를 끝까지 순회한 당시
  qualifying universe에서 `liquidity >= 20000 AND volume_24h >= 10000`을 적용해 grape
  후보를 재현한다. 고정 시장 수를 가정하지 않는다.
- **NO 토큰 가격은 1-YES 근사** (스프레드 무시 근사임을 결과에 명시).
- 시장이 해결되면 스냅샷이 끊긴다 → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1 (redeem 가정).
- grape의 최근 7일 자체 스냅샷은 `drift_at_buy` 재검산·아카이브 대조(sanity check)에만 쓴다.

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

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

-- 3.2 exit_reason별 분해 (STRATEGY.md §6: drift_death+trailing_stop 비중 70%+ 면 진입이 늦다는 신호)
SELECT exit_reason,
       COUNT(*)                                                       AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(realized_pnl), 4)                                    AS avg_pnl_usd,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY exit_reason ORDER BY n DESC;

-- 3.3 진입가 버킷별 성과 (prob band [0.40, 0.80] 교정 근거)
SELECT CASE WHEN buy_price < 0.50 THEN '0.40-0.50'
            WHEN buy_price < 0.60 THEN '0.50-0.60'
            WHEN buy_price < 0.70 THEN '0.60-0.70'
            ELSE '0.70-0.80' END                                      AS price_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY price_bucket ORDER BY price_bucket;

-- 3.4 진입 시그널 특성 버킷 (드리프트 밴드 [0.04, 0.10] 교정 근거)
SELECT CASE WHEN drift_at_buy < 0.06 THEN '0.04-0.06'
            WHEN drift_at_buy < 0.08 THEN '0.06-0.08'
            ELSE '0.08-0.10' END                                      AS drift_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
  AND drift_at_buy IS NOT NULL
GROUP BY drift_bucket ORDER BY drift_bucket;

-- 3.5 거래량 가속 버킷 (vol_accel_min 1.2 교정 근거)
SELECT CASE WHEN vol_accel_at_buy < 1.5 THEN '1.2-1.5'
            WHEN vol_accel_at_buy < 2.0 THEN '1.5-2.0'
            ELSE '2.0+' END                                           AS accel_bucket,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
  AND vol_accel_at_buy IS NOT NULL
GROUP BY accel_bucket ORDER BY accel_bucket;

-- 3.6 일관성 버킷 (consistency_min 0.70 교정 근거 — 6버킷 기준 값은 이산적: ~0.83, 1.0 등)
SELECT ROUND(consistency_at_buy, 2)                                   AS consistency,
       COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
  AND consistency_at_buy IS NOT NULL
GROUP BY ROUND(consistency_at_buy, 2) ORDER BY consistency;

-- 3.7 방향별 (YES 편승 vs NO 편승) — 1-YES 근사·상승/하락 비대칭 확인
SELECT entry_reason, COUNT(*) AS n,
       ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 1.0
             / COUNT(*), 3)                                           AS win_rate,
       ROUND(AVG(sell_price / buy_price - 1), 4)                      AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl_usd
FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY entry_reason;

-- 3.8 보유시간 분석 (exit_reason별 평균/최소/최대 보유 h)
SELECT exit_reason, COUNT(*) AS n,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h,
       ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_hold_h,
       ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_h
FROM trades
WHERE status = 'COMPLETED' AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason ORDER BY n DESC;

-- 3.9 미해결 자산: HOLDING 현황 + EXPIRED(수동 redeem 필요, realized_pnl NULL → 총계에서 빠짐!)
SELECT id, condition_id, outcome, buy_price, buy_timestamp, status, exit_reason,
       hours_until_resolution_at_buy, market_end_date
FROM trades WHERE status IN ('HOLDING', 'EXPIRED') ORDER BY buy_timestamp;

-- 3.10 주간 거래 빈도 (신호 빈도 체크 — 주 3건 미만이면 게이트 완화 검토, STRATEGY.md §6)
SELECT strftime('%Y-%W', buy_timestamp) AS week, COUNT(*) AS entries
FROM trades WHERE buy_timestamp IS NOT NULL
GROUP BY week ORDER BY week;
```

주의: `realized_pnl`은 USD 절대액(= (sell_price - buy_price) × buy_shares), 수익률은 `sell_price/buy_price - 1`로 계산한다. EXPIRED 거래는 realized_pnl이 NULL이라 3.1~3.8 집계에서 빠진다 — 3.9로 반드시 별도 확인하고, redeem 결과(0 또는 1)를 수동 반영해 총 P&L을 보정할 것.

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

공통 준비: grape `trades.db`의 각 거래(COMPLETED + HOLDING + EXPIRED)에 대해, nectarine 아카이브에서 `condition_id`로 스냅샷 시계열을 조회해 붙인다.

```sql
-- 아카이브 DB에서 (Python sqlite3로 실행, ? 바인딩)
SELECT timestamp, probability, liquidity, volume_24h
FROM market_snapshots
WHERE condition_id = ? AND timestamp >= ?   -- buy_timestamp 이후
ORDER BY timestamp;
```

- 매수 토큰 가격: `token_index = 0`(Yes)이면 `probability` 그대로, `1`(No)이면 `1 - probability` (**1-YES 근사**).
- 시계열이 끊긴 시장 = 해결됨. 최종가: 실제 청산됐으면 `trades.sell_price`, 아니면 마지막 스냅샷이 0.95+/0.05- 쪽으로 수렴했는지로 승패 판정 후 1/0 (redeem 가정) — 판정 애매하면 gamma API로 결과 확인.

### (a) 청산 스윕 — TP/SL/트레일링/drift_death 윈도우 격자

각 거래의 매수 토큰 가격 시계열(5분 간격)을 buy_timestamp부터 시간순으로 걷고, **trader.py와 동일한 우선순위**로 첫 트리거에서 청산한다:

1. `stop_loss`: (p / buy_price - 1) <= SL
2. `take_profit`: p >= min(buy_price × (1 + TP), 0.99)
3. `drift_death`: 최근 W시간 매수 토큰 가격 변화 <= 0. 현행은 최소 3포인트와 W의 50%
   time coverage를 모두 충족하지 않으면 판단 보류한다.
4. `trailing_stop`: p < (진입 후 최고가) × (1 - TRAIL)
5. `time_exit`: 해결까지 < 24h — post-instrumentation `market_catalog.end_date`를 사용하고,
   legacy catalog gap만 `trades.market_end_date`로 보충
6. 시계열 종료(해결)까지 미청산이면 redeem 가정 최종가 적용

격자 (현행: TP 0.15 / SL -0.08 / TRAIL 0.06 / W 6h):

| 노브 | 격자 |
|---|---|
| TP (`POLYBOT_TAKE_PROFIT`) | 0.10, **0.15**, 0.20, 0.25 |
| SL (`POLYBOT_STOP_LOSS`) | -0.05, **-0.08**, -0.12 |
| TRAIL (`POLYBOT_TRAILING_STOP_PERCENT`) | 0.04, **0.06**, 0.08, off |
| drift_death W (`POLYBOT_DEATH_WINDOW_HOURS`) | 3, **6**, 12, off |

풀 격자는 4×3×4×4 = 192조합. 계산이 부담되면 현행 기준 **one-at-a-time** 스윕(4+3+4+4 = 15조합) 먼저, 상위 조합만 교차 확인. 출력: 격자별 `총 P&L / 거래수 / 승률 / 평균 보유 h / exit_reason 분포` 표. drift_death off가 P&L을 크게 올리면 "드리프트 소멸 청산이 조기 청산"이라는 신호, SL이 지배적이면 진입 타이밍 문제.

### (b) 전략 고유 노브 스윕 — 진입 규칙 재생

핵심 노브 3개: **드리프트 밴드**(`POLYBOT_DRIFT_MIN`/`POLYBOT_DRIFT_MAX`), **일관성**(`POLYBOT_CONSISTENCY_MIN`), **거래량 가속**(`POLYBOT_VOL_ACCEL_MIN`).

재생 방법 — 아카이브 전체(60일)에서 grape 유니버스를 근사해 가상 진입을 생성한다:

1. 아카이브에서 `liquidity >= 20000 AND volume_24h >= 10000`인 (condition_id, timestamp) 지점만 후보로.
2. 평가 시점은 1시간 간격으로 다운샘플 (5분 전부 돌리면 과도).
3. 각 평가 시점에서 `signals.py`의 `evaluate_entry`와 동일 로직 적용 (grape repo를 import해서 쓰면 로직 복제 오류가 없다 — `SnapshotPoint(timestamp, probability, volume_24h)`로 변환해 넘기면 됨):
   - 24h 윈도우 (포인트 >=5, 커버리지 >=12h). 목표 5-minute cadence를 이유로 자동 통과
     처리하지 말고 실제 bucket/run coverage를 검증한다.
   - `yes_drift = p_now - window[0]` 방향 결정 → 매수 토큰 기준 drift ∈ [drift_min, drift_max], p ∈ [0.40, 0.80]
   - 4h 버킷 일관성 >= consistency_min, vol_accel = 현재 volume_24h / 윈도우 평균 >= vol_accel_min
4. 시장당 재진입 쿨다운 24h + "보유 중 중복 진입 금지"를 적용해 가상 포지션 시퀀스 생성.
5. `entry_hours_min=48h`는 post-instrumentation `market_catalog.end_date`로 재현한다. legacy
   catalog gap만 Gamma condition ID 재조회로 보충하며, 마지막 snapshot 프록시는 look-ahead
   sensitivity로 별도 표시한다.
6. 각 가상 진입을 (a)의 현행 청산 규칙으로 청산해 가상 P&L 산출.

격자 (현행 굵게):

| 노브 | 격자 |
|---|---|
| drift_min | 0.02, 0.03, **0.04**, 0.05 |
| drift_max | 0.08, **0.10**, 0.12, 0.15 |
| consistency_min | 0.50, 0.60, **0.70**, 0.85 |
| vol_accel_min | 1.0(사실상 off), 1.1, **1.2**, 1.5 |

출력: 노브 값별 `가상 진입 수 / 승률 / 총·평균 P&L`. 특히 (i) 현행 설정의 가상 진입이 실제 진입과 대략 일치하는지 먼저 검증(재생 로직 sanity check — grape 자체 스냅샷 7일치와 drift_at_buy 컬럼으로 대조), (ii) "완화했다면 추가로 잡혔을 진입"의 성과가 기존 진입보다 나쁜지/좋은지, (iii) "강화했다면 걸러졌을 진입"이 실제로 손실 거래였는지를 본다. STRATEGY.md §7의 변형(grape-1 엄격 / grape-2 민감)이 이 격자의 특정 조합에 해당한다.

### (c) 데이터 한계 (결과 보고서에 반드시 명시)

- **NO 토큰 = 1-YES 근사**: 스프레드 무시. NO 포지션 P&L은 실제보다 낙관될 수 있다.
- **Execution evidence**: legacy `trades`의 midpoint GTC 상태 전환은 actual fill이 아니다. live
  실현 결과는 `CONFIRMED order_fills`의 partial size/price/fee로 계산하고 ledger gap을 분리한다.
  simulation/counterfactual은 spread·unfilled sensitivity를 붙인 상대 비교로만 사용한다.
- **유니버스 차이**: 아카이브는 liq >= $10k 수집이지만 필터로 grape 유니버스(>= $20k)를
  근사하므로 경계 시장에서 실제 run과 다를 수 있다. keyset에는 고정 cap이 없지만
  run/cursor/cadence gap과 legacy 기간은 별도 coverage로 측정한다.
- **해상도 차이**: 아카이브 5분 간격 vs 봇 사이클 주기. 청산 스윕의 트리거 시점이 실제와 수 분 어긋난다.
- **백필 미반영**: grape 실봇은 cold-start 시 CLOB `/prices-history` 백필을 병합해 판정하지만 아카이브에는 그 포인트가 없다 — 배포 초기 진입은 재생이 정확히 일치하지 않는다 (§5 코호트 분리).
- **end_date 부재**: entry_hours_min/time_exit 재현은 프록시(스냅샷 지속 기간 또는 gamma 조인) 기반.
- **volume24hr는 방향 무관 총량**: 반대 방향 매물 폭증도 "가속"으로 잡힌다 — vol_accel 스윕 해석 시 유의.
- **아카이브 보존 60일**: 회고 시점에서 60일보다 오래된 거래는 시계열이 없다. 4주 회고면 문제없지만 회고가 늦어지면 먼저 아카이브를 백업해둘 것.

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트에서 파생된 시장들(negRisk 다후보, 동일 뉴스에 반응하는 시리즈물)은 드리프트가 동시에 발생해 grape가 한꺼번에 진입할 수 있다. `market_slug` 접두어·`question` 유사도·`market_tags`로 이벤트 단위로 묶고, 이벤트 단위 집계를 병기하라. 승률 계산의 **명목 n != 유효 n** — 같은 이벤트 5개 시장의 5승은 1승에 가깝다.
- **코호트 분리**: ① 배포 직후 cold-start 구간(백필 의존, 24h 윈도우가 백필로 채워진 진입 — buy_timestamp가 배포 후 첫 1~2일)과 ② 정상 스냅샷 축적 후 진입을 분리해서 봐라. 백필 데이터 품질이 다르면 초기 코호트의 시그널 값(drift_at_buy 등)이 왜곡됐을 수 있다.
- **모멘텀 전략 특성상 시기 편중**: 드리프트 신호는 큰 뉴스 주간에 몰린다. 주간 진입 분포(§3.10)를 확인하고, 특정 1~2주에 표본이 몰렸다면 "그 주의 시황"이 성과를 지배한 것일 수 있다 — 파라미터 일반화에 주의.
- **EXPIRED 누락 편향**: realized_pnl NULL인 EXPIRED 거래를 빼고 집계하면 생존 편향이 생긴다. redeem 결과를 반영해 보정하라 (§3.9).
- **거래수 자체가 적을 가능성**: 8개 AND 게이트라 4주에 30건 미만일 수 있다. n < 30이면 §6 제안의 신뢰도를 "낮음"으로 낮추고, 파라미터 대수술 대신 시뮬 연장 또는 게이트 1개 완화의 소규모 변경만 제안하라.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| 예: `POLYBOT_DRIFT_MIN` | 0.04 | 0.03 | §4(b): 0.03 완화 시 진입 +12건, 추가분 승률 61%, 총 P&L +$1.8 | 중간 |
| ... | | | | |

라운드 절차:

1. **1차(이번 회고)**: 위 표 확정 → Jenkins env 반영 → 첫 성공 run의 새
   `config_hash`/Git commit을 확인한다.
2. **2차 테스트(4주)**: 제안값으로 운용. 변경 노브는 최대 2~3개로 제한 — 전부 바꾸면 귀속 불가.
3. 확신이 낮은 제안은 cherry처럼 **기본/변형 병행 슬롯**으로 A/B: 같은 코드, 다른 env의 Jenkins job 2개 (예: 현행 vs STRATEGY.md §7 grape-1 엄격 조합). DB는 job명으로 자동 분리(`data/<job>/trades.db`)되어 비교가 깨끗하다.
4. **3차 교정**: 2차 종료 후 이 문서로 재회고 → 표 갱신 → 수렴하면 `POLYBOT_BUY_AMOUNT` 증액 검토 (STRATEGY.md §6 판단 기준: 승률 >= 55% AND 평균 손익 > 0).

## 7. 기준 정보

- 이 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (t1 monorepo main)
- 전략 문서: `/Users/izowooi/git/t1/golden-grape/STRATEGY.md` (논지·파라미터 근거·실패 모드·변형 아이디어)
- 코드 기준: `golden-grape/src/polybot/config.py`(env 파싱), `db/models.py`(스키마), `strategy/signals.py`(진입/소멸 판정 순수 함수), `strategy/trader.py`(청산 우선순위)
- 운영 절차 문서: `/Users/izowooi/git/t1/docs/ab-retro-playbook.md`
