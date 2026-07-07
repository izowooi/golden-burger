# golden-nectarine 회고(포스트모템) 가이드

> 회고 실행: 운영 시작 4주 후 (운영 시작 2026-07-06 → 2026-08-03 전후). 이 문서 경로를 AI에게 주면 된다.
> 전략: **Bottom Fisher** — 장기(30일+) tail~중간 구간(YES 0.03~0.50) 시장에서 YES 가격이 20일 롤링 최저가(최근 24h 제외) 이하로 떨어지면 매수, 보유 120h(5일) 경과 시 손익 무관 무조건 청산 (calendar exit). QuantPedia 백테스트(X=20/Y=5) 복제.
> 자매 문서: `docs/nectarine-max-positions-retro.md` (max_positions 상한 전용 회고 — 같은 시점에 함께 실행할 것).

## 0. 복붙용 회고 프롬프트

```
docs/retro/golden-nectarine.md 를 읽고 §3~§5를 실행해 §6 표 형식으로 파라미터 교정안을 제시해줘.

- 봇: golden-nectarine (Bottom Fisher). DB는 §2의 find 명령으로 찾아라
  (2026-07 기준 Jenkins job=polybot-fox, 대시보드 계정 golden-fox — job명은 바뀔 수 있음).
- Jenkins env 블록 (job 설정의 export 블록에서 POLYMARKET_PRIVATE_KEY /
  POLYMARKET_FUNDER_ADDRESS 두 키만 제외하고 그대로 붙여넣기):
  [여기에 붙여넣기]
- 반사실 분석(§4)의 가격 시계열: nectarine 자기 DB의 market_snapshots
  (이 DB가 곧 전 봇 공용 중앙 아카이브다).
- max_positions 상한은 docs/nectarine-max-positions-retro.md 의 SQL A/B/C와
  판정 규칙을 그대로 실행해 §6 표의 POLYBOT_MAX_POSITIONS 행을 채워라.
- 반드시 지킬 것:
  * §5 상관 클러스터 — 같은 이벤트 파생 시장(예: "대선 출마 선언" 계열, 코인
    가격 사다리 계열)과 같은 주의 동시 진입은 이벤트(클러스터) 단위로 묶어
    재집계하고, 유효 n 기준으로 신뢰도를 매겨라.
  * §5 코호트 분리 — 운영 첫 이틀(2026-07-06~07)의 백필發 백로그 진입과 이후의
    신선한 신저가 진입을 분리 집계하라. 5일 보유 전략이라 회고 시점의 HOLDING
    미완결 물량도 §4 스윕에 반드시 포함하라.
  * 존재하지 않는 컬럼/env를 지어내지 마라. 컬럼은 §2, env는 §1 표가 전부다.
  * status는 대문자 enum name('COMPLETED' 등)으로 저장된다 — 소문자 필터는 0건이 나온다.
```

## 1. 전략 요약

손실 회피(loss aversion)發 투매가 얇은 장기 시장의 YES 가격을 펀더멘털 이하로 오버슈트시키고, 며칠 안에 평균 회귀한다는 가설. 감이 아니라 QuantPedia(2026-04) 공개 백테스트 — "20일 롤링 최저가 매수 → 5일 후 청산"이 거래비용 10bps 반영 후 CAR +18.9~22.1%로 생존 — 의 직접 복제이며, 소액 실전으로 그 재현성을 검증하는 것이 이 봇의 존재 이유다. **YES 매수 고정**(1-p 환산 없음), trailing 없음, 주 청산 경로는 가격이 아니라 **달력(보유 120h)** 이다.

- **진입 (모두 충족, `signals.evaluate_bottom_fisher`)**: liquidity >= $10k + 해결까지 >= 720h(30일 — theta 감쇠發 가짜 신저가 차단) + YES 가격 p ∈ [0.03, 0.50] + **p <= min(20일 룩백 윈도우 중 최근 24h 제외 구간의 최저가)** (동률 허용, EPSILON=1e-9 오차 흡수) + 윈도우 유효(포인트 >= 5 AND 커버리지 >= 룩백의 50% = 10일, 부족 시 prices-history 백필(fidelity=60) 후 재평가 — 그래도 invalid면 진입 금지) + 재진입 쿨다운 168h(7일) 통과. 매수 직전 CLOB midpoint 재검증: 상한(0.50) 초과면 `price_above_band`로 skipped_markets 기록(쿨다운 차단), 하한(0.03) 미만이면 기록 없이 이번 사이클만 보류.
- **청산 (우선순위 순, `signals.evaluate_exit` — calendar가 최우선인 것이 이 봇의 특징)**: ① 보유 >= 120h → `max_holding` (**손익 무관 무조건, 주 청산 경로**) ② P&L <= -30% → `stop_loss` (안전판) ③ 현재가 >= min(매수가×1.30, 0.99) → `take_profit` (안전판) ④ 해결까지 < 24h → `time_exit` (30일+ 진입이라 드문 경로). 별도로 midpoint 조회 실패(또는 0 반환 — zero-midpoint 가드) + endDate 24h 경과 시 status=EXPIRED, exit_reason=`resolved_unredeemed`(realized_pnl NULL, 수동 redeem 필요).

### 파라미터 표 (env는 `src/polybot/config.py`, 기본값은 `config.yaml` 실제 값)

우선순위: **환경변수 > config.yaml > 코드 기본값**. 운영 실값은 Jenkins export 블록이 진실이다 (§0 프롬프트에 붙여넣기). 참고로 2026-07-06 시작 설정은 `POLYMARKET_SIGNATURE_TYPE=3`, `POLYBOT_BUY_AMOUNT=10`, `POLYBOT_MAX_POSITIONS=150`이었다 (`docs/nectarine-max-positions-retro.md` §1 — 이후 바뀌었을 수 있으니 env 블록으로 확인).

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| strategy.lookback_days | `POLYBOT_LOOKBACK_DAYS` | `20` | 롤링 최저가 룩백 (일, 백테스트 X) |
| strategy.exclude_recent_hours | `POLYBOT_EXCLUDE_RECENT_HOURS` | `24` | 최저가 산출 시 제외할 최근 구간 (진행 중 하락을 기준선에서 배제) |
| strategy.hold_hours | `POLYBOT_HOLD_HOURS` | `120` | calendar exit 보유 시간 (백테스트 Y=5일, 주 청산 경로) |
| strategy.prob_min | `POLYBOT_PROB_MIN` | `0.03` | 진입 YES 가격 하한 (붕괴/해결 임박 노이즈 차단) |
| strategy.prob_max | `POLYBOT_PROB_MAX` | `0.50` | 진입 YES 가격 상한 (tail~중간 구간) |
| time_based.entry_hours_min | `POLYBOT_ENTRY_HOURS_MIN` | `720` | 해결까지 최소 잔여 시간 (30일 — 장기 시장만) |
| time_based.exit_hours | `POLYBOT_EXIT_HOURS` | `24` | 해결 N시간 전 청산 |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | `0.30` | 익절 안전판 +30% (목표가 0.99 캡) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | `-0.30` | 손절 안전판 -30% |
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | `5.0` | 1회 매수 USDC (운영 시작값 10) |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | `10000` | 최소 유동성 $ |
| min_volume_24h | `POLYBOT_MIN_VOLUME_24H` | `0` | 최소 24h 거래량 $ (0=비활성 — 얇은 시장이 대상) |
| max_positions | `POLYBOT_MAX_POSITIONS` | `-1` | 최대 동시 포지션 (-1=무제한, 운영 시작값 150) |
| reentry_cooldown_hours | `POLYBOT_REENTRY_COOLDOWN_HOURS` | `168` | 재진입 쿨다운 (7일 — 최저가 부근 연속 재진입 방지) |
| history_backfill | `POLYBOT_HISTORY_BACKFILL` | `true` | prices-history 백필 (**끄면 사실상 무전략**) |
| excluded_categories | `POLYBOT_EXCLUDED_CATEGORIES` | `[]` | 제외 카테고리 (comma 구분, 기본 비활성) |
| (로그) | `LOG_LEVEL` | yaml 키 없음, 기본 INFO | 로그 레벨 (`--verbose`가 최우선) |

env 없는 코드 상수 (`signals.py` / `scanner.py` / `trader.py` / `bot.py`): `EPSILON=1e-9`(경계 비교), `TAKE_PROFIT_PRICE_CAP=0.99`, `window_min_points=5`·`window_min_coverage=0.5`(윈도우 유효성 — 20일 룩백이면 커버 10일 필요), `BACKFILL_FIDELITY_MINUTES=60`(백필 캔들 간격), `MIN_ORDER_SIZE=5.0`(주), `RESOLVED_GRACE_HOURS=24.0`, 스냅샷 보존 = max(lookback_days×3, 7일) = **60일**. 이 값들에 env를 지어내지 말 것.

## 2. 데이터 위치와 스키마

### 자기 DB 찾기

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
```

- job명은 바뀔 수 있으니 반드시 find로 찾는다 (2026-07 기준 job=polybot-fox, 대시보드 계정 golden-fox, `--job` 미지정 실행이라 경로는 `.../golden-nectarine/data/default/trades.db`).
- 시뮬레이션은 같은 폴더의 `trades_sim.db` 별도. 완료 거래 월별 CSV(`trades_YYYY-MM.csv`, 청산 시 append — `hold_hours_at_exit`/`rolling_min_at_buy` 시그널 컬럼 포함)와 일자별 로그(`logs/YYYYMMDD.log`)도 같은 `data/<job>/` 아래에 있다.
- Jenkins 콘솔 로그/일자별 로그에는 사이클마다 `제외 사유 요약 - reason: count` 한 줄이 남는다 (스캔 병목 파악용). reason 키: `excluded_category`, `low_liquidity`, `low_volume`, `too_close_to_resolution`, `no_price_data`, `price_out_of_band`, 그리고 시그널 탈락(수치 접미사 제거) `window_invalid`, `no_ref_data`, `above_rolling_min`.

### 테이블 (`src/polybot/db/models.py` 기준 — 이 컬럼만 사용)

**trades** — 핵심 컬럼:

- 식별: `id`, `condition_id`(unique 아님 — 재진입 쿨다운 방식), `market_slug`, `question`, `outcome`(**항상 "Yes"** — YES 매수 고정), `token_id`, `market_tags`
- 매수: `buy_price`, `buy_amount`, `buy_shares`, `buy_order_id`, `buy_timestamp`, `buy_probability`(= buy_price와 동일 값)
- 매도: `sell_price`, `sell_shares`, `sell_order_id`, `sell_timestamp`, `sell_probability`, `realized_pnl`
- 전략: `entry_reason`(예: `bottom_fisher_min0.180_p0.175`), `exit_reason`(`max_holding`(주 경로) / `stop_loss` / `take_profit` / `time_exit` / `resolved_unredeemed`), `rolling_min_at_buy`(진입 시점 20일·최근 24h 제외 롤링 최저가), `lookback_days_at_buy`(룩백 윈도우 실제 커버 일수 — 백필 실효성 지표), `hold_hours_at_exit`(청산 시점 보유 시간), `max_price`(진입 후 최고가, 분석용 — trailing 미사용), `market_end_date`, `hours_until_resolution_at_buy`, `liquidity_at_buy`
- 회고 공통 계약: `strategy_name`(상수 "nectarine"), `mode`("live"/"sim"), `volume_24h_at_buy`
- `status`: **SQLAlchemy Enum은 enum name 대문자로 저장된다** — `'PENDING_BUY'`, `'HOLDING'`, `'PENDING_SELL'`, `'COMPLETED'`, `'SKIPPED'`, `'EXPIRED'`. SQL에서 소문자 value(`completed` 등)를 쓰면 0건이 나온다. (주의: `docs/nectarine-max-positions-retro.md` §3 SQL C의 `status = 'completed'`는 이 이유로 대문자로 고쳐 실행할 것.)
- `EXPIRED`는 `realized_pnl` NULL — P&L 집계에서 자동으로 빠지므로 §3.3에서 반드시 별도 확인 (수동 redeem 필요 물량 — tail 진입이라 조기 NO 해결 시 전액 손실 가능, §5 참고).

**market_snapshots** — `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격). **이 테이블이 전 봇 공용 중앙 가격 아카이브다** (아래 참조).

**skipped_markets** — `condition_id`, `reason`(`price_above_band` 등), `skipped_at`. 재진입 쿨다운 판정용 (skip 후 168h 차단).

**cycle_stats** — 사이클마다 1행: `ts`, `markets_scanned`, `buy_candidates`, `holdings_before/after`, `max_positions`·`buy_amount_usdc`(**당시 설정값** — env 변경 이력 자동 추적), `bought`, `cap_skips`(상한 때문에 못 산 수), `cooldown_skips`, `failed_buys`. → max_positions 튜닝 회고 전용 가이드: **`docs/nectarine-max-positions-retro.md`** (SQL A/B/C + 판정 규칙 — 이 문서와 함께 실행).

**capped_candidates** — `ts`, `condition_id`, `question`, `yes_price`(스킵 시점 가격 = 가상 매수가), `rolling_min`, `hours_left`. 상한이 걸러낸 진입의 반사실 P&L 계산용 (시장당 24h dedup).

### 중앙 가격 아카이브 (반사실 분석용 시계열)

모든 봇이 같은 gamma sweep(가장 오래된 활성 시장 ~2100개)을 스냅샷하며, **nectarine 자기 DB의 `market_snapshots`가 곧 공용 아카이브다** — 유니버스(liq >= $10k)가 자기 진입 조건과 정확히 일치하고, 보존 60일(=룩백 20일×3), 5분 간격, `liquidity`/`volume_24h` 포함. 별도 DB를 ATTACH할 필요가 없다.

- 보조/교차검증: honeydew DB의 `market_snapshots` (job=polybot-eco, liq >= $15k, 60일 보존). 단 유니버스가 더 좁아서($15k) nectarine 전용 저유동($10k~15k) 시장은 거기 없다 — 고유동 시장의 교차검증 용도로만.

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-honeydew/data*" -name "trades.db" 2>/dev/null
```

- **NO 토큰 가격은 1-YES 근사**지만, nectarine은 YES 매수 고정이라 이 근사가 필요 없다 (다른 봇 대비 재생 오차 요인이 하나 적다).
- **시장이 해결되면 스냅샷이 끊긴다** → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1 (redeem 근사).
- **아카이브에는 백필 포인트가 없다**: 봇이 본 20일 윈도우는 스냅샷 + prices-history 백필 병합이지만, 아카이브에는 스냅샷만 쌓인다 → §4(c) 참고.

## 3. 실적 분석 SQL (그대로 실행 가능)

`sqlite3 <trades.db 경로>` 로 실행. 먼저 `.headers on` / `.mode column` 권장. 모든 쿼리는 `mode='live'` 필터 포함 (sim DB는 파일이 다르지만 안전장치).

```sql
-- 3.0 상태 분포 (status는 enum name 대문자 저장)
SELECT status, COUNT(*) AS n FROM trades GROUP BY status;

-- 3.1 완결 거래 총괄: 건수 / 승률 / 총·평균·중앙 수익률
--    판단 기준(STRATEGY.md §6): 승률보다 평균손익 우선 (tail 매수는 승률이 낮고 payoff 비대칭)
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

-- 3.2 exit_reason별 분해 — 이 전략의 본질 지표
--    판단 기준(STRATEGY.md §6): max_holding 거래의 평균 P&L이 양수인가 (핵심).
--    stop_loss 비중 > 40%면 가설 기각. take_profit/time_exit는 드물어야 정상.
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
--    tail YES 진입이라 NO 확정 해결이면 주당 0 = 전액 손실: 총 P&L 판단 시 반드시 수동 가산)
SELECT id, condition_id, outcome, buy_price, buy_shares, buy_amount,
       market_end_date, substr(question, 1, 60) AS question
FROM trades
WHERE status = 'EXPIRED';

-- 3.4 진입가 밴드 버킷 (현행 밴드 0.03~0.50) — prob_min/max 교정 근거
--    STRATEGY.md §7 A-3(0.05~0.25 좁은 tail 집중) 가설 검증
SELECT
  CASE
    WHEN buy_price < 0.10 THEN '0.03-0.10'
    WHEN buy_price < 0.20 THEN '0.10-0.20'
    WHEN buy_price < 0.35 THEN '0.20-0.35'
    ELSE '0.35-0.50'
  END                                                       AS price_bucket,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY price_bucket
ORDER BY price_bucket;

-- 3.5 신저가 갱신 깊이 버킷 — exclude_recent_hours / 진입 트리거 민감도 교정 근거
--    깊이 = (rolling_min_at_buy - buy_price) / rolling_min_at_buy
--    (진입 조건이 p <= rolling_min 동률 허용이라 깊이 >= 0 부근에 몰린다.
--     '동률 진입'과 '깊은 붕괴 진입'의 성과가 다른지 확인)
SELECT
  CASE
    WHEN (rolling_min_at_buy - buy_price) / rolling_min_at_buy < 0.01 THEN 'a_tie(<1%)'
    WHEN (rolling_min_at_buy - buy_price) / rolling_min_at_buy < 0.05 THEN 'b_1-5%'
    ELSE 'c_5%+'
  END                                                       AS depth_bucket,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
  AND rolling_min_at_buy IS NOT NULL AND rolling_min_at_buy > 0
GROUP BY depth_bucket
ORDER BY depth_bucket;

-- 3.6 룩백 커버리지 분포 — 백필 실효성 검증 (STRATEGY.md §6: lookback_days_at_buy >= 10 비율)
--    커버 짧은 진입(백필 실패/신생 시장)의 성과가 나쁘면 window_min_coverage 상향 근거
SELECT
  CASE
    WHEN lookback_days_at_buy < 10 THEN 'a_<10d'
    WHEN lookback_days_at_buy < 15 THEN 'b_10-15d'
    ELSE 'c_15d+'
  END                                                       AS coverage_bucket,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND lookback_days_at_buy IS NOT NULL
GROUP BY coverage_bucket
ORDER BY coverage_bucket;

-- 3.7 거래량 버킷 — min_volume_24h=0(비활성)의 정당성 검증
--    저거래량 진입의 성과가 유의하게 나쁘면 POLYBOT_MIN_VOLUME_24H 활성화 근거
SELECT
  CASE
    WHEN volume_24h_at_buy < 100 THEN 'a_<$100'
    WHEN volume_24h_at_buy < 1000 THEN 'b_$100-1k'
    WHEN volume_24h_at_buy < 10000 THEN 'c_$1k-10k'
    ELSE 'd_$10k+'
  END                                                       AS vol_bucket,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND volume_24h_at_buy IS NOT NULL
GROUP BY vol_bucket
ORDER BY vol_bucket;

-- 3.8 보유시간 분석 (hold_hours_at_exit — 청산 시 봇이 직접 기록) — hold_hours 교정 근거
--    max_holding은 120h 부근에 몰려야 정상 (사이클 간격만큼의 초과는 정상)
SELECT
  exit_reason,
  COUNT(*)                                    AS n,
  ROUND(AVG(hold_hours_at_exit), 1)           AS avg_hold_h,
  ROUND(MIN(hold_hours_at_exit), 1)           AS min_hold_h,
  ROUND(MAX(hold_hours_at_exit), 1)           AS max_hold_h
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live' AND hold_hours_at_exit IS NOT NULL
GROUP BY exit_reason;

-- 3.9 코호트: 운영 첫 이틀 백필發 백로그 vs 이후 신선한 신저가 (§5 참고)
--    (첫날 20일 백필이 과거 신저가를 한꺼번에 잡아 ~128건 백로그 진입 — 레짐이 다르다)
SELECT
  CASE WHEN date(buy_timestamp) <= '2026-07-07'
       THEN 'backlog_w1' ELSE 'fresh' END                   AS cohort,
  COUNT(*)                                                  AS n,
  ROUND(AVG(realized_pnl > 0), 3)                           AS win_rate,
  ROUND(SUM(realized_pnl), 2)                               AS total_pnl,
  ROUND(AVG((sell_price - buy_price) / buy_price) * 100, 2) AS avg_ret_pct
FROM trades
WHERE status = 'COMPLETED' AND mode = 'live'
GROUP BY cohort;

-- 3.10 현재 HOLDING (5일 보유 전략이라 회고 시점 미완결 물량이 많다 — §4 스윕에 반드시 포함)
SELECT id, condition_id, buy_price, rolling_min_at_buy,
       ROUND((julianday('now') - julianday(buy_timestamp)) * 24, 1) AS held_h,
       substr(question, 1, 50) AS question
FROM trades
WHERE status = 'HOLDING'
ORDER BY held_h DESC;

-- 3.11 skip 사유 분포 (price_above_band = 스캔~주문 사이 반등으로 신저가 프리미엄 소멸)
SELECT reason, COUNT(*) AS n
FROM skipped_markets
GROUP BY reason
ORDER BY n DESC;

-- 3.12 max_positions 상한 히트 요약 (전용 회고는 docs/nectarine-max-positions-retro.md 실행)
SELECT date(ts) AS day, MAX(holdings_after) AS peak, MAX(max_positions) AS cap,
       SUM(bought) AS bought, SUM(cap_skips) AS cap_skips,
       SUM(cooldown_skips) AS cooldown_skips, SUM(failed_buys) AS failed_buys
FROM cycle_stats GROUP BY day ORDER BY day;
```

보조: 진입 빈도 자체가 적으면 로그의 `제외 사유 요약` 라인을 집계해 병목 필터를 찾는다 (예: `above_rolling_min`이 압도적이면 정상 — 신저가 대기 상태. `window_invalid`가 크면 백필 문제부터 의심).

```bash
grep -h "제외 사유 요약" <data/<job>/logs/*.log 경로> | tail -200
```

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### (a) 청산 스윕: hold_hours / TP / SL 격자

완결(COMPLETED) + 보유(HOLDING) 거래 각각에 대해, 자기 DB 스냅샷 시계열을 붙여 "다른 청산 파라미터였다면"의 P&L을 계산한다. **이 봇의 핵심 스윕 축은 TP/SL이 아니라 hold_hours다** (calendar exit가 주 경로).

**격자 (현행 hold 120h / TP +0.30 / SL -0.30 / exit_hours 24 주변):**

- `POLYBOT_HOLD_HOURS` ∈ {72, 96, **120**, 168, 240} — A-2(회전 가속, 72h) 가설 포함
- `POLYBOT_TAKE_PROFIT` ∈ {0.15, **0.30**, 0.50, 9.9(비활성)} — 9.9는 A-1(순수 calendar exit) 가설
- `POLYBOT_STOP_LOSS` ∈ {-0.15, **-0.30**, -0.50, -0.99(비활성)} — -0.99는 A-1 가설
- trailing 격자는 두지 않는다 (백테스트 복제가 목적 — trailing 도입은 파라미터 교정이 아니라 전략 변경)

시계열 추출 (자기 DB에서 바로 — 스냅샷 유니버스가 자기 진입 유니버스와 일치):

```sql
-- 거래별 진입 후 264h 시계열 (스윕 최대 보유 240h + 여유 24h)
SELECT t.id            AS trade_id,
       t.buy_price,
       t.buy_amount,
       t.buy_timestamp,
       t.market_end_date,
       s.timestamp,
       s.probability   AS yes_price
FROM trades t
JOIN market_snapshots s
  ON s.condition_id = t.condition_id
 AND s.timestamp BETWEEN t.buy_timestamp AND datetime(t.buy_timestamp, '+264 hours')
WHERE t.status IN ('COMPLETED', 'HOLDING') AND t.mode = 'live'
ORDER BY t.id, s.timestamp;
```

**시뮬레이션 규칙 (봇의 실제 우선순위를 그대로 재현 — calendar가 최우선인 것에 주의):**

1. 토큰 가격 = `yes_price` 그대로 (YES 매수 고정 — NO 근사 불필요).
2. 시계열을 시간순으로 걸으며 첫 도달 조건으로 청산: **보유시간 >= hold_hours 먼저 검사** (도달 시 그 시점 가격으로 `max_holding` — 가격이 SL/TP에 걸려 있어도 calendar 우선, `signals.evaluate_exit`와 동일) → SL (P&L <= stop_loss_percent) → TP (가격 >= min(buy×(1+TP), 0.99)) → `market_end_date - 24h` 도달 시 그 시점 가격으로 time_exit. 5분 간격이라 같은 간격 안의 동시 터치는 봇과 동일 우선순위(calendar → SL → TP)로 처리.
3. 스냅샷이 조기 종료(시장 해결)된 거래: 실거래가 청산됐으면 `trades.sell_price`를 최종가로, 아니면 redeem 근사 0/1 (tail YES는 NO 해결 시 0 = 전액 손실 — 이 시나리오가 스윕 결과를 지배할 수 있으니 해당 거래 수를 병기).
4. 격자별로 총 P&L($, `buy_amount`는 실거래 값 사용), 승률, 평균 보유시간을 표로 출력. hold_hours 값별로 TP×SL 표를 반복:

| TP \ SL | -0.15 | -0.30 | -0.50 | -0.99(비활성) |
|---|---|---|---|---|
| 0.15 | | | | |
| 0.30 | | (현행 기준선, hold=120h) | | |
| 0.50 | | | | |
| 9.9(비활성) | | | | (A-1: 순수 calendar) |

(현행 셀 값이 실제 실적 §3.1과 크게 어긋나면 재현 로직부터 의심할 것 — 좋은 sanity check다. 특히 A-1 셀(TP/SL 비활성)과 현행 셀의 차이가 "안전판이 실제로 번 돈/버린 돈"의 직접 측정값이다.)

### (b) 전략 고유 노브 스윕 — 진입 규칙 재생

진입 판정이 순수 함수(`signals.evaluate_bottom_fisher` — 스냅샷 리스트+숫자 입력)라서 **그대로 import해서 재생에 쓸 수 있다** (`PricePoint`, `BottomFisherParams`). 핵심 노브 3개:

**노브 1 — `POLYBOT_LOOKBACK_DAYS`: {10, 15, **20**, 30}** (A-2 가설: 짧은 룩백 → 빈도 상승·신호 품질 하락? 백테스트 X=20이 그리드 서치 승자였는지 우리 데이터로 재확인)

**노브 2 — `POLYBOT_EXCLUDE_RECENT_HOURS`: {12, **24**, 48, 72}** (기준선에서 제외하는 최근 구간이 길수록 "더 오래된 저가" 대비 신저가만 잡는다 — §3.5 깊이 버킷과 교차 확인)

**노브 3 — `POLYBOT_PROB_MIN`/`POLYBOT_PROB_MAX` 밴드: {[0.03,0.50](현행), [0.05,0.25](A-3 좁은 tail), [0.03,0.20], [0.10,0.50]}** (§3.4 진입가 버킷과 교차 확인 — 오버슈트가 가장 큰 구간만 남기는 가설)

**재생 절차:**

1. 자기 DB 아카이브에서 회고 기간의 스냅샷을 condition_id별 시계열로 로드 (`liquidity >= 10000` 필터 — 자기 유니버스와 일치).
2. 각 시각 t(5분 격자)에 대해: 룩백 윈도우 구성(`get_window` + `is_window_valid`: 포인트 >= 5, 커버 >= 50%) → 밴드 체크 → 기준 최저가 = 최근 exclude_recent_hours 제외 구간의 min → `p_now <= rolling_min`(동률 허용, EPSILON) → **condition_id당 168h 쿨다운** 적용(직전 가상 진입/청산 후 차단).
3. 가상 진입마다 §(a)의 청산 시뮬레이터(현행 hold 120h / TP 0.30 / SL -0.30)로 청산 → 노브 값별 "잡혔을/걸러졌을 진입" 목록과 가상 성과 집계:

| 노브 값 | 가상 진입 수 | 승률 | 총 P&L | 실거래와 겹치는 진입 수 |
|---|---|---|---|---|

4. sanity check: 현행 노브 값의 재생 결과에 실제 진입(trades)이 대부분 포함되는지 확인. 괴리가 크면 원인(아카이브에 백필 부재, hours_left 필터 미재현 — 아래 (c))을 §6 신뢰도에 반영.

### (c) 데이터 한계 (수치 해석 전 반드시 명시)

- **아카이브에는 백필 포인트가 없다**: 봇의 20일 윈도우 = 자기 스냅샷 + CLOB `/prices-history` 백필 병합. 아카이브에는 스냅샷만 있으므로, **운영 시작 후 20일이 지나기 전 구간과 유니버스에 새로 들어온 시장의 재생은 봇이 본 것과 다르다** (윈도우가 invalid로 나와 가상 진입이 누락되거나 rolling_min이 다르게 계산됨). 특히 첫 이틀 백로그 코호트는 재생 불가에 가깝다 → §5 코호트 분리로 흡수. 재생 충실도가 필요한 가상 진입은 `/prices-history`를 직접 다시 조회해 병합하는 것도 방법이다 (`api/history_client.py` 재사용, fidelity=60).
- **`entry_hours_min >= 720h` 필터는 아카이브만으로 재현 불가** (스냅샷에 endDate 없음). 미필터 재생은 마감 임박 시장의 theta 감쇠發 가짜 신저가를 포함해 **성과를 체계적으로 나쁘게** 왜곡한다. 보정: 진입된 시장은 `trades.market_end_date`, 상한 스킵분은 `capped_candidates.hours_left`로 확인하고, 그 외 가상 진입은 Gamma API로 endDate를 사후 조회하거나 "스냅샷 시계열이 가상 진입 후 30일 내 끊긴 시장"을 의심 표기하라.
- **체결 가정**: 봇은 GTC limit @ midpoint 접수 즉시 HOLDING/COMPLETED로 기록한다 (STRATEGY.md §8). tail 시장 스프레드는 fat-tail이라 **시장가 환산 시 알파가 소멸**한다는 것이 원 백테스트의 경고 — 스윕 P&L은 상대 비교용이지 절대 수익 예측이 아니다. 회고 시 실계좌 잔고(대시보드 golden-fox)와 DB 누적 P&L의 괴리를 먼저 확인하라.
- **해결 시 스냅샷 중단**: 최종가는 `trades.sell_price` 또는 0/1 redeem 근사. tail YES는 0 근사가 지배적 시나리오다.
- **60일 보존**: 4주 시점 회고면 커버되지만, 미루면 초기 구간 유실. 회고가 늦어질 것 같으면 DB 파일을 먼저 복사해둘 것. 노브 2의 30일 룩백 재생은 회고 시점 기준 최소 30일치 아카이브가 필요하다.
- **5분 간격**: 봇 사이클(5분)과 같아 격자 재생 정밀도는 좋은 편이나, 매수 직전 CLOB midpoint 재검증(스냅샷 가격과 다를 수 있음)은 재현 불가.
- (교차 봇 비교 시에만) NO 토큰 1-YES 근사 이슈가 있으나 nectarine 자체는 YES 고정이라 해당 없음.

## 5. 표본 주의사항

- **상관 클러스터**: 장기 tail 시장은 같은 이벤트의 파생(예: "대선 출마 선언" 계열, 코인 가격 사다리(ladder) 계열)이 많고, 시장 전반 하락 국면(크립토 급락 등)에는 여러 시장이 **동시에** 신저가를 만들어 진입이 한 방향으로 몰린다. `market_slug`/`question` 접두어 + `market_tags` + 진입 주(`strftime('%Y-%W', buy_timestamp)`)로 클러스터를 묶고, **이벤트(클러스터) 단위 승률/P&L을 별도 집계**하라. 클러스터 내 상관이 높으면 명목 n이 커도 독립 표본이 아니다.
- **코호트 분리**: ① 운영 첫 이틀(2026-07-06~07) 백로그 — 첫 실행의 20일 백필이 과거 신저가를 한꺼번에 잡아 ~128건이 몰려 들어간 코호트로, "신선한 신저가 돌파"와 레짐이 다르다 (§3.9). ② 운영 중 env를 바꿨다면 변경 시점 전후 분리 — `cycle_stats.max_positions`/`buy_amount_usdc`에 당시 설정값이 남아 있어 변경 시점을 DB에서 역추적할 수 있다. ③ EXPIRED(수동 redeem 대기)와 HOLDING(미완결)은 3.1 집계에 없다 — 총 P&L 판단 전 반드시 가산.
- **명목 n ≠ 유효 n**: STRATEGY.md §6의 "30+ 거래" 기준은 명목 건수다. 5일 보유 + 168h 쿨다운 구조라 4주 회고는 비중첩 보유 사이클이 5~6개뿐이고, 같은 주 진입들은 시장 레짐을 공유한다. 클러스터 집계 후 유효 n(독립 이벤트 수)이 15 미만이면 §6 교정안의 신뢰도를 한 단계 낮춰라. 승률 55%와 50%는 n=30에서 통계적으로 구분되지 않는다 — 방향성 제안 + 2차 테스트로 검증하는 구조를 유지할 것.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| `POLYBOT_HOLD_HOURS` | 120 | | §3.8 보유시간 + §4(a) hold 축 스윕 | |
| `POLYBOT_TAKE_PROFIT` | 0.30 | | §4(a) 격자 (A-1 비활성 셀 대비) | |
| `POLYBOT_STOP_LOSS` | -0.30 | | §3.2 stop_loss 비중 + §4(a) 격자 | |
| `POLYBOT_LOOKBACK_DAYS` | 20 | | §4(b) 노브1 스윕 | |
| `POLYBOT_EXCLUDE_RECENT_HOURS` | 24 | | §3.5 깊이 버킷 + §4(b) 노브2 | |
| `POLYBOT_PROB_MIN`/`MAX` | 0.03 / 0.50 | | §3.4 진입가 버킷 + §4(b) 노브3 | |
| `POLYBOT_MIN_VOLUME_24H` | 0 (비활성) | | §3.7 거래량 버킷 | |
| `POLYBOT_REENTRY_COOLDOWN_HOURS` | 168 | | §3.11 skip 분포 + 재진입 건 성과 | |
| `POLYBOT_MAX_POSITIONS` | (Jenkins env 값, 시작 150) | | `docs/nectarine-max-positions-retro.md` SQL A/B/C + 판정 규칙 | |
| `POLYBOT_BUY_AMOUNT` | (Jenkins env 값, 시작 10) | | §3.1 총괄 + STRATEGY.md §6 증액/중단 기준 | |

- 근거 수치는 "버킷 X 평균수익 a% (n=b, 유효 n=c) vs 버킷 Y 평균수익 d%"처럼 표본 수를 병기한다.
- 판단 프레임(STRATEGY.md §6): **`max_holding` 거래의 평균 P&L 양수 여부가 본질 지표** (승률보다 평균손익 우선 — tail 매수는 payoff 비대칭). 30거래 후 총 P&L < -15% 또는 `stop_loss` 비중 > 40% → 가설 기각·중단 검토. 안전판(TP/SL) 교정은 A-1 비활성 셀과의 차이가 유의할 때만.

**라운드 절차**: 모든 제안은 **env만 바꾸면 된다** (코드 변경 불필요, Jenkins job 설정의 export 블록 수정. `cycle_stats`가 당시 설정값을 기록하므로 조정 이력은 자동 추적).

1. **1차 회고 (이 문서 + max-positions 문서)** → 교정안 확정 → Jenkins env 반영.
2. **2차 테스트 (4주+ — 5일 보유 전략이라 회전이 느리다)**: 단일 교체 대신 cherry처럼 **기본/변형 병행**도 가능 — 별도 Jenkins job에 `--job <변형명>`으로 DB를 분리하고 변형 env(STRATEGY.md §7의 A-1 순수 calendar / A-2 짧은 룩백·보유 / A-3 좁은 tail)를 얹어 A/B. 변형은 시뮬레이션(`--simulate`, trades_sim.db) 또는 소액 실전 중 리스크에 맞게 선택.
3. **3차 교정**: 2차 결과로 이 문서 §3~§5를 재실행해 수렴 여부 판단. 교정이 수렴하지 않고 `max_holding` 평균 P&L이 0 부근을 맴돌면 파라미터 문제가 아니라 백테스트 재현성 자체(§1 — 이 봇의 검증 대상 가설) 문제로 판정하고 중단을 검토.

## 7. 기준 정보

- 이 문서 생성 기준: commit `7bcf83f` (2026-07-06 시점 main), 작성일 2026-07-07.
- 전략 문서: `/Users/izowooi/git/t1/golden-nectarine/STRATEGY.md` (논지·백테스트 출처 §2·규칙 명세 §3·리스크 §5·A/B 기준 §6·베리에이션 §7·구현 한계 §8).
- 자매 회고: `/Users/izowooi/git/t1/docs/nectarine-max-positions-retro.md` (max_positions 상한 전용 — cycle_stats/capped_candidates 기반, 계측 도입 커밋 `26a2d2f`).
- 코드 기준: env 파싱 `golden-nectarine/src/polybot/config.py`, 스키마 `src/polybot/db/models.py`, 진입/청산 판정 `src/polybot/strategy/signals.py`(순수 함수 — 재생 구현 시 그대로 import 가능), 주문 실행·EXPIRED 처리 `src/polybot/strategy/trader.py`, 스캔·백필 `src/polybot/strategy/scanner.py`.
- 슬롯 매핑 (2026-07-07 기준, 바뀔 수 있음 — find로 재확인): nectarine=polybot-fox(계정 golden-fox), honeydew=polybot-eco(계정 golden-eco), date=polybot-red, elderberry=polybot-cherry 워크스페이스(이름 주의). 운영 4계정 = apple x2, banana, cherry.
