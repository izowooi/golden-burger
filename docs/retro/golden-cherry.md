# golden-cherry 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 대상: Resolution Momentum 전략. 2026-07-22부터 신규 진입은 비스포츠 `endDate` / 스포츠
> `gameStartTime` 기준 0~120h이고 시간 청산은 기본 비활성이다. 그 전 24~720h/12h 코호트는
> 대규모 손실 원인 분석을 위해 아래 SQL에서 별도 historical cohort로 유지한다.

> 계정/전략 식별: 분석할 자금 계좌는 **golden-banana**, 실행 코드·DB·run audit의
> strategy_name은 **golden-cherry**다. 이름이 같은 별도 전략 golden-banana와 혼합하지 않는다.

## 0. 복붙용 회고 프롬프트

```
docs/retro/golden-cherry.md 를 읽고 §3~§5를 실행한 뒤, §6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.

전제/주의:
- 과거에는 기본/변형 두 슬롯이 있었을 수 있으므로 DB가 여러 개면 config hash별로 분리한다.
  현재 확인 대상은 golden-banana 계좌의 golden-cherry `--yes-only` job이다. 이름만 보고
  다른 golden-banana 전략이나 과거 슬롯을 합치지 말고 resolved config로 식별한다.
- status 컬럼은 대문자 enum 이름('COMPLETED', 'HOLDING' 등)으로 저장돼 있다. 소문자로 조회하면 0건 나온다.
- HOLDING인데 market_end_date가 이미 지난 "좀비 포지션"을 먼저 집계하고(§3.5), 좀비의 추정 손익(0/1 redeem 가정)을
  포함한 보정 P&L과 실현 P&L을 반드시 나란히 제시해라. 실현 통계만 보면 생존편향으로 성과가 과대평가된다.
- 상관 클러스터: 같은 이벤트에서 파생된 시장들(예: 같은 선거의 후보별 시장)은 이벤트 단위로 묶어
  유효 n을 별도 계산해라 (§5).
- 코호트 분리: 운영 첫 며칠의 백로그 일괄 진입(30일 진입창 → 초기 burst)과 이후 정상 신호 진입을
  buy_timestamp 기준으로 분리해 따로 집계해라 (§5).
- 반사실 분석(§4)의 가격 시계열은 nectarine DB의 market_snapshots(중앙 아카이브)를 쓴다.
  cherry 자기 DB의 market_snapshots 테이블은 비어 있다(봇이 안 씀).
- NO 포지션(outcome='No')의 가격은 1 - probability 근사임을 결과에 명시해라.

Jenkins env 블록 (키 제외하고 현재 golden-banana job의 export 라인 복사):
[현재 운영값: 여기 붙여넣기]
```

## 1. 전략 요약

**논지**: "해결(resolution)이 가까워진 고확률 favorite은 1.0으로 수렴한다"에 베팅한다. Gamma API로 활성 시장을 전수 스캔해 확률 0.75~0.92 구간의 favorite 토큰을 시장당 1회 매수한다. 2026-07-22 이후 진입 기준은 비스포츠 `endDate`까지 0~120h, 스포츠는 `gameStartTime`까지 0~120h 또는 주문 가능한 인플레이 상태다. 수익 원천은 "0.75~0.92 매수 → 해결 직전 0.95+ 매도"의 수렴 구간 캡처(favorite-longshot bias + 시간가치 소멸). 한 번 거래(또는 skip)한 시장은 영구 재진입 금지.

**진입 규칙** (`src/polybot/strategy/scanner.py::scan_buy_candidates` → `trader.py::execute_buy`):
1. 유동성 >= `min_liquidity`
2. favorite 확률 p: `buy_threshold <= p <= sell_threshold` (양끝 포함). 기본 모드는 Yes/No 중 높은 쪽(→ **NO 토큰도 매수**), `--yes-only`면 index 0(Yes) 토큰만
3. 진입 시간: 비스포츠 `0 < endDate 잔여 h <= 120`; 스포츠는 경기 전 `0 < gameStartTime 잔여 h <= 120` 또는 인플레이
4. 매수 직전 CLOB midpoint 재검증: `> sell_threshold`면 "rapid_jump"로 **영구 skip**, `< buy_threshold`면 이번 사이클만 skip
5. 주문 직전 sports pregame/in-play 상태, open 원금/포지션/cycle burst, 주문/유동성 비율 재검증

**청산 규칙** (`trader.py::execute_sell`, 우선순위 순 — exit_reason 값 그대로):
| 순위 | 조건 | exit_reason |
|------|------|-------------|
| 1 | P&L <= `stop_loss_percent` (-8%) | `stop_loss` |
| 2 | P&L >= `take_profit_percent` (+10%) | `take_profit` |
| 3 | 현재가 < max_price × (1 - 트레일링 5%) | `trailing_stop` |
| 4 | `0 < endDate 잔여시간 <= exit_hours` (기본 0=비활성) | `time_exit` |

**구조적 특성 (회고 시 반드시 고려)**: `max_price`가 매수가로 초기화되므로 실효 손절선은 트레일링 -5%다. -8% 손절은 사이클 간 갭 하락에서만 발동하는 백업. 또 매수가 0.91 이상이면 TP +10%는 수학적으로 도달 불가(상한 1.0) → 그 구간은 trailing/time_exit로만 청산된다.

**파라미터 표** (env > config.yaml > 코드 기본값. env 이름은 `src/polybot/config.py` 실제 파싱 코드 기준):

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---------|---------|-------------------|------|
| 매수 하한 확률 | `POLYBOT_BUY_THRESHOLD` | 0.75 | 이 확률 이상만 매수 |
| 매수 상한 확률 | `POLYBOT_SELL_THRESHOLD` | 0.92 | 초과 시 rapid_jump 영구 skip |
| 건당 매수 금액 | `POLYBOT_BUY_AMOUNT` | 5.0 | USDC 달러 단위 |
| 건당 하드캡 | `POLYBOT_MAX_BUY_AMOUNT_USDC` | 100 | scale-up 이중 확인 |
| 최소 유동성 | `POLYBOT_MIN_LIQUIDITY` | 50000 | $ |
| 주문/유동성 비율 | `POLYBOT_MAX_ORDER_LIQUIDITY_RATIO` | 0.002 | 최대 0.2% |
| 최대 동시 포지션 | `POLYBOT_MAX_POSITIONS` | 100 | 무제한 금지 |
| 최대 open 원금 | `POLYBOT_MAX_OPEN_NOTIONAL_USDC` | 5000 | 요청 BUY 원금 합계 |
| cycle 신규 포지션 | `POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE` | 5 | burst 제한 |
| 익절 | `POLYBOT_TAKE_PROFIT` | 0.10 | 진입가 대비 (코드 기본은 0.15) |
| 손절 | `POLYBOT_STOP_LOSS` | -0.08 | 진입가 대비 |
| 트레일링 on/off | `POLYBOT_TRAILING_STOP_ENABLED` | true | |
| 트레일링 % | `POLYBOT_TRAILING_STOP_PERCENT` | 0.05 | 최고점 대비 하락률 |
| 시간필터 on/off | `POLYBOT_TIME_BASED_ENABLED` | true | false면 진입·시간청산 조건 비활성 |
| 진입 최대 잔여시간 | `POLYBOT_ENTRY_HOURS_MAX` | 120 | 진입 기준시각까지 시간 |
| 진입 최소 잔여시간 | `POLYBOT_ENTRY_HOURS_MIN` | 0 | 기준시각 전 모든 양수 시간 |
| 시간 청산 기준 | `POLYBOT_EXIT_HOURS` | 0 | endDate 시간 청산 비활성 |
| 경기시각 필터 | `POLYBOT_GAME_START_FILTER_ENABLED` | true | 스포츠 경기 전 기준을 gameStartTime으로 교체 |
| 인플레이 진입 | `POLYBOT_ALLOW_IN_PLAY` | true | 주문 가능한 경기 중 시장도 신규 진입 허용 |
| 경기시각 누락 차단 | `POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START` | true | fail closed |
| YES-Only 모드 | `POLYBOT_YES_ONLY` (CLI `--yes-only`가 우선) | (yaml에 없음, 기본 false) | index 0 토큰만 매수 |
| 제외 카테고리 | (env 없음, yaml 전용) | `[]` | 빈 배열 = 스포츠 필터 완전 비활성화 (현재 상태) |

> 우선순위는 env > yaml > code default다. post-instrumentation 운영값은 `strategy_configs`와
> `run_audits`의 config hash/Git cohort로 확정한다. Jenkins export와 과거 로그는 secret을
> 제거한 legacy/current cross-check이며, 현재 값을 과거 전체에 소급하지 않는다.

## 2. 데이터 위치와 스키마

### 2.1 자기 DB 찾기

Jenkins job 이름은 바뀔 수 있으니 find로 찾는다. **주의: `polybot-cherry`라는 이름의 Jenkins job은 elderberry의 워크스페이스다 (2026-07-07 기준). job 이름이 아니라 경로 안의 `golden-cherry`로 찾아라.**

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-cherry/data*" -name "trades.db" 2>/dev/null
```

- 과거 슬롯 또는 다른 job 때문에 결과가 2개 이상 나올 수 있다. 현재 golden-banana job의
  workspace/job/config hash를 먼저 확정하고 각 DB를 따로 분석한다.
- 시뮬레이션 기록은 같은 폴더의 `trades_sim.db`로 분리돼 있다 — 실거래 분석에 섞지 말 것.
- 완결 거래는 `data/<job>/trades_YYYY-MM.csv`에도 append된다 (교차 검증용).
- Jenkins 콘솔 로그와 `data/<job>/logs/YYYYMMDD.log`에 사이클마다 "제외 사유 요약 - reason: count" 한 줄이 남는다. 주요 reason 키: `excluded_category`, `low_liquidity`, `no_price_data`, `prob_out_of_range`, `too_early`, `too_late`, `already_resolved`, `no_end_date`, `sports_missing_game_start`, `invalid_game_start_time`, `game_in_play_disabled`. 스캔 병목 파악에 사용한다.

### 2.2 테이블 (`src/polybot/db/models.py` 기준)

**`trades`** — 핵심 컬럼:

| 컬럼 | 의미 |
|------|------|
| `condition_id` | 시장 식별자 (unique — 시장당 1거래) |
| `market_slug`, `question`, `outcome` | outcome은 "Yes"/"No" — NO 포지션 구분에 필수 |
| `buy_price`, `buy_amount`, `buy_shares`, `buy_timestamp`, `buy_probability` | 매수 정보. buy_price = buy_probability = 매수 시 midpoint |
| `sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability` | 매도 정보 |
| `realized_pnl` | (매도가-매수가)×주수. 부분 매도 HOLDING에서는 누적 실현값일 수 있음 |
| `status` | **대문자 enum 이름으로 저장**: `PENDING_BUY` / `HOLDING` / `PENDING_SELL` / `COMPLETED` / `SKIPPED` / `UNFILLED` / `QUARANTINED` |
| `entry_reason` | 비스포츠 `time_based_<잔여h>h`, 스포츠 경기 전 `game_start_<잔여h>h`, 인플레이 `game_in_play_<경과분>m`, 또는 `probability_only` |
| `exit_reason` | `take_profit` / `stop_loss` / `trailing_stop` / `time_exit` |
| `max_price` | 진입 후 최고가 (트레일링 추적용, 매수가로 초기화됨) |
| `market_end_date` | 시장 해결 예정 시각 (Gamma endDate, 실제 해결 시각과 다를 수 있음) |
| `hours_until_resolution_at_buy` | endDate까지 잔여시간 (스포츠 진입 기준과 다를 수 있음) |
| `market_game_start_time`, `minutes_until_game_start_at_buy` | 스포츠 실제 시작시각과 매수 직전 잔여분 |
| `entry_time_reference`, `hours_until_entry_deadline_at_buy` | `end_date`/`game_start_time` 및 실제 진입창 잔여시간 |
| `sports_market_type` | Gamma sportsMarketType evidence |
| `sports_phase_at_buy` | `pregame` / `in_play` 등 주문 직전 스포츠 단계 |
| `liquidity_at_buy`, `market_tags` | 매수 시 유동성, Gamma 태그 문자열 |

모든 timestamp는 `datetime.utcnow()` — **UTC naive**. 중앙 아카이브의 timestamp와 같은 기준이라 그대로 join 가능하다.

**`skipped_markets`**: `condition_id`, `reason`("rapid_jump" 등), `skipped_at`. rapid_jump 영구 밴의 기회비용 분석에 사용.

**`market_snapshots`**: 스키마에는 존재하지만 **cherry 봇은 이 테이블에 쓰지 않는다(빈 테이블)**. 가격 시계열은 아래 중앙 아카이브만 사용한다.

### 2.3 중앙 가격 아카이브 (nectarine DB)

Gamma keyset cursor를 끝까지 순회한 당시 qualifying universe를 수집하므로, 반사실 분석의
가격 시계열은 nectarine DB의 `market_snapshots`를 공용 아카이브로 쓴다. 고정 시장 수 대신
run별 cursor completion과 `market_catalog` join coverage를 확인한다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`, `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존** — 현재 cherry 문턱($50k)보다 넓지만
  run/cursor gap은 별도 확인해야 한다. 보조 아카이브: honeydew DB (liq >= $15k, 60일,
  job=polybot-eco)
- **NO 포지션은 1 - probability 근사** (스프레드 무시 근사임을 결과에 명시)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결 보유분의 최종가는 `trades.sell_price` 또는 0/1 (redeem 가정)
- 60일 보존이므로 회고는 반드시 보존 기간 안에 실행

**커버리지 확인 (분석 첫 단계로 실행)**:

```sql
-- sqlite3 <cherry의 trades.db> 접속 후
ATTACH '<nectarine의 trades.db 경로>' AS arch;

SELECT t.id, substr(t.question,1,40) AS q, t.status,
       COUNT(s.id) AS snap_cnt,
       MIN(s.timestamp) AS first_snap, MAX(s.timestamp) AS last_snap
FROM trades t
LEFT JOIN arch.market_snapshots s ON s.condition_id = t.condition_id
GROUP BY t.id
ORDER BY snap_cnt ASC;
-- snap_cnt = 0 인 거래는 반사실 분석에서 제외하고 제외 건수를 보고서에 명시
```

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

`sqlite3 <cherry의 trades.db>` 접속 후 실행. 슬롯별 DB 각각에 대해 반복한다.

### 3.1 완결 거래 총괄

```sql
SELECT COUNT(*)                                                    AS n,
       SUM(realized_pnl > 0)                                       AS wins,
       ROUND(AVG(realized_pnl > 0), 3)                             AS win_rate,
       ROUND(SUM(realized_pnl), 4)                                 AS total_pnl,
       ROUND(AVG(realized_pnl / buy_amount), 4)                    AS avg_ret,
       ROUND(MIN(realized_pnl / buy_amount), 4)                    AS worst_ret,
       ROUND(MAX(realized_pnl / buy_amount), 4)                    AS best_ret
FROM trades
WHERE status = 'COMPLETED' AND buy_amount > 0;
```

```sql
-- 중앙값 수익률
WITH r AS (
  SELECT realized_pnl / buy_amount AS ret,
         ROW_NUMBER() OVER (ORDER BY realized_pnl / buy_amount) AS rn,
         COUNT(*) OVER () AS n
  FROM trades WHERE status = 'COMPLETED' AND buy_amount > 0
)
SELECT ROUND(AVG(ret), 4) AS median_ret FROM r WHERE rn IN ((n+1)/2, (n+2)/2);
```

### 3.2 exit_reason별 분해 — 청산 규칙 교정의 1차 근거

```sql
SELECT exit_reason,
       COUNT(*)                                                       AS n,
       ROUND(AVG(realized_pnl > 0), 3)                                AS win_rate,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl,
       ROUND(AVG(realized_pnl / buy_amount), 4)                       AS avg_ret,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h
FROM trades
WHERE status = 'COMPLETED' AND buy_amount > 0
GROUP BY exit_reason
ORDER BY total_pnl DESC;
```

해석 가이드: `time_exit`이 수익 본류인지(전략 논지대로 0.95+ 수렴 매도), `trailing_stop`이 whipsaw로 손실을 양산하는지(실효 손절 -5% 문제), `stop_loss`가 거의 안 나오는지(트레일링에 선점) 확인.

### 3.3 진입가 버킷별 성과 — 확률 밴드 교정 근거

```sql
SELECT CASE
         WHEN buy_price < 0.78 THEN '0.75-0.78'
         WHEN buy_price < 0.82 THEN '0.78-0.82'
         WHEN buy_price < 0.86 THEN '0.82-0.86'
         WHEN buy_price < 0.90 THEN '0.86-0.90'
         ELSE '0.90-0.92+' END                                        AS entry_bucket,
       COUNT(*)                                                       AS n,
       ROUND(AVG(realized_pnl > 0), 3)                                AS win_rate,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl,
       ROUND(AVG(realized_pnl / buy_amount), 4)                       AS avg_ret
FROM trades
WHERE status = 'COMPLETED' AND buy_amount > 0
GROUP BY entry_bucket ORDER BY entry_bucket;
```

> 과거 0.85~0.95 config cohort가 실제로 있으면 버킷 경계를
> 0.85/0.88/0.91/0.93/0.95로 바꿔 실행한다. 0.90+ 버킷은 TP 도달 불가 구간이므로
> exit_reason 분포를 함께 본다 (`GROUP BY entry_bucket, exit_reason` 변형).

### 3.4 잔여시간 버킷별 성과 — 과거 진입 시간창(24~720h) 교정 근거

```sql
WITH timed AS (
  SELECT *,
         COALESCE(
           hours_until_entry_deadline_at_buy,
           hours_until_resolution_at_buy
         ) AS entry_hours
  FROM trades
)
SELECT CASE
         WHEN entry_hours < 12  THEN 'a. 0-12h'
         WHEN entry_hours < 24  THEN 'b. 12-24h'
         WHEN entry_hours < 72  THEN 'c. 24-72h'
         WHEN entry_hours < 120 THEN 'd. 72-120h'
         WHEN entry_hours < 240 THEN 'e. 120-240h'
         WHEN entry_hours < 480 THEN 'f. 240-480h'
         ELSE 'g. 480h+' END                                          AS ttr_bucket,
       COUNT(*)                                                       AS n,
       ROUND(AVG(realized_pnl > 0), 3)                                AS win_rate,
       ROUND(SUM(realized_pnl), 4)                                    AS total_pnl,
       ROUND(AVG(realized_pnl / buy_amount), 4)                       AS avg_ret,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h
FROM timed
WHERE status = 'COMPLETED' AND buy_amount > 0
  AND entry_hours IS NOT NULL
GROUP BY ttr_bucket ORDER BY ttr_bucket;
```

해석 가이드: 문서상 원래 전략(4~24h 단기 수렴), 과거 운영(24~720h),
2026-07-22 이후 운영(기준시각 0~120h)을 서로 다른 config/Git cohort로 분리한다.
과거 장기 버킷(e~g)은 720h 확대가 손실과 자본 회전에 미친 영향을 평가하는 자료다.
새 컬럼이 없는 과거 행만 `hours_until_resolution_at_buy`로 대체한다. 현재 스포츠 행은 반드시
`hours_until_entry_deadline_at_buy`(gameStartTime 기준)를 사용하며 endDate 시간과 섞지 않는다.

### 3.5 좀비 포지션 + 보정 P&L (실현 통계의 생존편향 교정 — 필수)

봇은 해결된 시장을 처리하지 못한다: 보유 중 시장이 해결되면 영구 HOLDING으로 남는다. **패배 포지션일수록 좀비로 남기 쉬우므로, COMPLETED만 집계하면 성과가 과대평가된다.**

```sql
-- 좀비 후보: HOLDING인데 해결 예정 시각이 지난 포지션
SELECT id, substr(question, 1, 50) AS q, outcome, buy_price,
       ROUND(buy_amount, 2) AS cost,
       buy_timestamp, market_end_date,
       ROUND((julianday('now') - julianday(market_end_date)) * 24, 1) AS h_past_end
FROM trades
WHERE status = 'HOLDING' AND market_end_date < datetime('now')
ORDER BY market_end_date;
```

```sql
-- 정상 보유 포지션 (아직 해결 전)
SELECT id, substr(question, 1, 50) AS q, outcome, buy_price, max_price,
       market_end_date,
       ROUND((julianday('now') - julianday(buy_timestamp)) * 24, 1) AS held_h
FROM trades
WHERE status = 'HOLDING' AND (market_end_date IS NULL OR market_end_date >= datetime('now'))
ORDER BY market_end_date;
```

좀비 각각에 대해 Polymarket에서 해결 결과를 확인하고(승리 → +$ (1-buy_price)×buy_shares 상당의 redeem 가치, 패배 → -buy_amount 전액 손실), 보정 P&L = 실현 P&L + 좀비 추정 P&L을 §6에 나란히 제시한다.

### 3.6 side(Yes/No)·태그별 성과, rapid_jump 통계

```sql
-- 과거 non-yes-only cohort가 있으면 side별 성과를 분리해 판단 근거로 사용
SELECT outcome, COUNT(*) AS n, ROUND(AVG(realized_pnl > 0), 3) AS win_rate,
       ROUND(SUM(realized_pnl), 4) AS total_pnl,
       ROUND(AVG(realized_pnl / buy_amount), 4) AS avg_ret
FROM trades WHERE status = 'COMPLETED' AND buy_amount > 0
GROUP BY outcome;
```

```sql
-- 태그(카테고리)별 — 스포츠 필터가 꺼져 있으므로 스포츠 성과를 분리 확인
SELECT market_tags, COUNT(*) AS n, ROUND(SUM(realized_pnl), 4) AS total_pnl,
       ROUND(AVG(realized_pnl / buy_amount), 4) AS avg_ret
FROM trades WHERE status = 'COMPLETED'
GROUP BY market_tags ORDER BY total_pnl ASC LIMIT 20;
```

```sql
-- rapid_jump 영구 밴 규모 (기회비용 분석 대상 목록)
SELECT reason, COUNT(*) AS n FROM skipped_markets GROUP BY reason;
```

### 3.7 보유시간 분포

```sql
SELECT CASE
         WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 24  THEN 'a. <24h'
         WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 72  THEN 'b. 24-72h'
         WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 168 THEN 'c. 72-168h'
         WHEN (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 336 THEN 'd. 168-336h'
         ELSE 'e. >336h' END                                          AS hold_bucket,
       COUNT(*) AS n,
       ROUND(AVG(realized_pnl / buy_amount), 4) AS avg_ret,
       ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND buy_amount > 0
  AND sell_timestamp IS NOT NULL AND buy_timestamp IS NOT NULL
GROUP BY hold_bucket ORDER BY hold_bucket;
```

보유시간이 길수록 수익률이 안 오르면 자본 회전 관점에서 진입창 축소(또는 최대 보유시간 도입) 근거가 된다.

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### (a) 청산 스윕: TP / SL / 트레일링 / exit_hours 격자

**방법**: 거래별(COMPLETED + HOLDING 전부)로 아카이브 스냅샷 시계열을 붙여, 매수 시점부터 5분 간격으로 청산 규칙을 우선순위 순서(SL → TP → 트레일링 → 시간)대로 재생한다.

1. `trades`에서 `condition_id, outcome, buy_price, buy_shares, buy_timestamp, market_end_date` 로드
2. 아카이브에서 `SELECT probability, timestamp FROM arch.market_snapshots WHERE condition_id = ? AND timestamp >= ? ORDER BY timestamp` (? = condition_id, buy_timestamp)
3. `outcome = 'No'`면 가격 = 1 - probability 로 변환
4. 각 스텝에서: P&L = (가격-매수가)/매수가, max_price 갱신(매수가로 초기화), 잔여시간 = market_end_date - timestamp → 격자의 청산 규칙 적용, 최초 발동 시점의 가격으로 청산
5. 시계열이 끝날 때까지 미발동이면: COMPLETED 거래는 실제 sell_price, 해결된 HOLDING은 0/1(redeem), 미해결 HOLDING은 마지막 스냅샷 가격으로 평가
6. 격자 셀별로 총 P&L / 승률 / 평균 보유시간 집계

**격자 (현재 설정 주변)**:

| 노브 | env | 현행 | 스윕 값 |
|------|-----|------|---------|
| 익절 | `POLYBOT_TAKE_PROFIT` | 0.10 | 0.06, 0.08, **0.10**, 0.12, 0.15, 없음(수렴 보유) |
| 손절 | `POLYBOT_STOP_LOSS` | -0.08 | -0.05, **-0.08**, -0.12, 없음 |
| 트레일링 | `POLYBOT_TRAILING_STOP_PERCENT` | 0.05 | off, 0.03, **0.05**, 0.08 |
| 시간 청산 | `POLYBOT_EXIT_HOURS` | 0 | **0**, 4, 12, 24 |

전체 조합(6×4×4×4=384셀)이 부담이면 노브별 1차원 스윕(다른 노브는 현행 고정) 후 상위 조합만 2차원 교차. **추가로 "armed trailing" 변형 1개를 반드시 포함**: 트레일링을 `가격 >= 매수가×1.03` 도달 후에만 활성화 — 실효 손절 -5% 문제(§1 구조적 특성)가 실제 손실원인지 판정하는 실험이다.

### (b) 전략 고유 노브 스윕: 진입 시간창 / 확률 밴드 / yes_only

**노브 1 — 진입 시간창** (`POLYBOT_ENTRY_HOURS_MIN` / `POLYBOT_ENTRY_HOURS_MAX`, 현재 0/120):

아카이브로 진입 규칙을 재생한다: 아카이브의 각 시장에 대해 "확률이 밴드(0.75~0.92) 안이고 잔여시간이 창 안인 최초 스냅샷"을 가상 진입 시점으로 잡고, (a)의 청산 재생을 현행 청산 설정으로 돌려 가상 성과를 계산한다.

| 창 (min~max) | 의미 |
|--------------|------|
| **0~120h** | **2026-07-22 이후 현재값; 스포츠 gameStartTime / 비스포츠 endDate** |
| 4~24h | 코드 기본값 = 문서상 원래 "Resolution Momentum" |
| 24~72h | 단기 수렴 |
| 24~168h | 1주 이내 |
| 72~240h | 중기 |
| 24~720h | 과거 운영값 |
| 168~720h | 장기 전용 (단기 제외의 기여 분리) |

잔여시간은 post-instrumentation `market_catalog.end_date`를 우선 사용한다. legacy catalog gap은
(1) 거래 시장의 `trades.market_end_date`, (2) Gamma condition ID 재조회 순으로 보충한다.
끝내 없는 시장은 제외하고 수를 명시한다.

**노브 2 — 확률 밴드** (`POLYBOT_BUY_THRESHOLD` / `POLYBOT_SELL_THRESHOLD`):

과거 두 슬롯(0.75/0.92 vs 0.85/0.95)의 config hash가 실제로 모두 존재하면 자연 A/B로
분리하고, 없으면 병행 운용이었다고 가정하지 않는다. 아카이브 재생으로 중간 격자를 보간한다:
buy {0.70, **0.75**, 0.80, **0.85**} × sell {0.90, **0.92**, **0.95**}
(buy < sell 조합만). 재생 방법은 노브 1과 동일하되 시간창은 현재값으로 고정한다.

**노브 3 — yes_only on/off**:

과거 non-yes-only config cohort가 있으면 `outcome` 컬럼으로 Yes/No를 나눠(§3.6)
재생 없이 1차 판정한다. 현재 Golden Banana job은 `--yes-only`이므로 NO side 실측이 없으면
아카이브에서 NO-favorite 시장의 가상 성과로만 기회비용을 추정하고 한계를 명시한다.

### (c) 데이터 한계 (결과 보고서에 반드시 명시)

- **Execution evidence**: legacy `trades`의 midpoint 상태 전환은 actual fill이 아니다. 실현 결과는
  `CONFIRMED order_fills`의 partial size/price/fee로 계산하고 미대사 구간을 분리한다. archive
  midpoint grid는 spread·depth를 반영하지 않으므로 상대 비교로만 사용한다.
- **NO 가격 = 1 - YES 근사**: 스프레드 무시 근사.
- **5분 간격**: 봇의 3~5분 사이클과 유사하나, 사이클 사이 급변(rapid_jump 경로, 갭 손절)은 정확 재현 불가.
- **endDate != 실제 해결 시각**: 조기 해결 시장은 time_exit 재생이 실제와 어긋난다. 스냅샷이 endDate보다 훨씬 먼저 끊긴 시장은 조기 해결로 간주하고 별도 표기.
- **아카이브 유니버스 갭**: keyset에는 고정 offset cap이 없지만, 배포 전 legacy 구간,
  run/cursor 실패, 5-minute cadence gap 때문에 cherry 거래 시장이 없을 수 있다. §2.3 coverage
  SQL과 catalog join으로 먼저 확인한다.
- **60일 보존**: 그 이전 거래는 반사실 불가. 회고를 미루지 말 것.
- **rapid_jump 영구 밴 재현 한계**: 스캔 시점과 매수 검증 시점 사이의 CLOB midpoint는 기록에 없다. skipped_markets 목록의 사후 성과(아카이브 재생)로 "밴에 TTL을 뒀다면"의 기회비용만 근사 가능.

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트 파생 시장들(같은 선거의 후보별 시장, 같은 대회의 경기들)은 한 방향으로 같이 움직인다. 과거 로그에서 같은 선거의 "A 당선 Yes"와 "B 당선 No"를 동시 매수한 사례가 확인됐다 — 동일 리스크 2배. `market_slug` 접두사·`question` 유사도·`market_tags`로 이벤트를 묶어 **이벤트 단위 집계를 병행**하고, 유효 n(이벤트 수)을 명목 n(거래 수)과 나란히 보고한다. 승률·평균수익률의 신뢰구간은 유효 n 기준으로 판단.
- **코호트 분리**: 30일 진입창 특성상 운영 첫 며칠에 기존 백로그(이미 조건을 충족하며 대기 중이던 시장 전체)를 일괄 매수하는 burst가 발생한다. `buy_timestamp` 기준으로 "초기 백로그 코호트"(운영 개시 ~3일)와 "정상 신호 코호트"를 분리해 따로 집계한다. 백로그 코호트는 잔여시간 분포가 편향돼 있어(장기 잔여 시장 과다) 시간창 교정 근거로 쓰면 안 된다.
- **좀비로 인한 생존편향**: §3.5 — COMPLETED만 보면 패배가 과소 집계된다. 보정 P&L 없이는 어떤 교정 결론도 내리지 말 것.
- **스포츠 필터 비활성**: 현행 `excluded_categories: []`이므로 스포츠 시장이 섞여 있다. 태그별 분해(§3.6)로 스포츠/비스포츠 성과를 분리해, 카테고리 효과를 파라미터 효과로 오독하지 않도록 한다.
- **명목 n != 유효 n**: 거래 수가 수백이어도 이벤트·기간·카테고리가 몰려 있으면 통계적 유효 표본은 훨씬 작다. n < 10인 버킷의 결론은 신뢰도 "낮음"으로만 제시.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

동시에 존재한 두 config cohort가 실제로 확인될 때만 A/B 비교표를 먼저 제시한다. 그렇지 않으면
현재 Golden Banana / Golden Cherry cohort 하나에 대해 아래 표를 채운다:

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---------|------|------|----------|----------------------|
| POLYBOT_BUY_THRESHOLD | 0.75 | | §3.3 버킷 + §4(b) 노브 2 격자 | |
| POLYBOT_SELL_THRESHOLD | 0.92 | | 〃 | |
| POLYBOT_ENTRY_HOURS_MIN | 0 | | §3.4 버킷 + §4(b) 노브 1 격자 | |
| POLYBOT_ENTRY_HOURS_MAX | 120 | | 〃 | |
| POLYBOT_EXIT_HOURS | 0 | | §4(a) 격자 | |
| POLYBOT_TAKE_PROFIT | 0.10 | | 〃 | |
| POLYBOT_STOP_LOSS | -0.08 | | 〃 (armed trailing 변형 포함) | |
| POLYBOT_TRAILING_STOP_PERCENT | 0.05 | | 〃 | |
| POLYBOT_YES_ONLY | 현재 Golden Banana true (`--yes-only`) | | §3.6 side 분해 + §4(b) 노브 3 | |
| POLYBOT_MIN_LIQUIDITY | 50000 | | config cohort + liquidity_at_buy 분해 | |

**라운드 절차**: tunable knob는 Jenkins env로 반영하되, 첫 성공 run에서 새 `config_hash`와
Git commit을 확인해 이전 cohort와 분리한다.

1. 1차(이번 회고): 위 표의 제안값 확정. 신뢰도 "높음"만 현재 job에 반영한다.
   별도 계좌·DB·Jenkins job이 실제로 준비된 경우에만 "중간" 값을 실험군으로 병행한다.
2. 2차(4주 후): 같은 문서로 재실행, 1차 제안값의 실측 검증. 이때 1차 교정 전/후 코호트를 `buy_timestamp`로 분리할 것.
3. 3차: 수렴하고 별도 실험군이 있으면 다음 단일 노브 실험에 재할당한다.

교정과 별개로, 좀비 포지션(§3.5)이 다수 확인되면 파라미터 교정보다 **해결/redeem 처리 자동화**(개선 아이디어는 `golden-cherry/STRATEGY_ANALYSIS.md` §7-2)를 우선 과제로 보고한다 — 회계가 틀리면 다음 회고의 근거 수치도 틀린다.

## 7. 기준 정보

- 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (branch main)
- 전략 문서: `golden-cherry/STRATEGY_ANALYSIS.md` (정밀 명세·알려진 약점 §6.2·개선 아이디어 §7 — 이 회고의 보조 근거), `golden-cherry/docs/polymarket-strategy-last.md` (Resolution Momentum 원 논지)
- 그라운딩 코드: `golden-cherry/config.yaml`, `src/polybot/config.py`, `src/polybot/db/models.py`, `src/polybot/strategy/trader.py`, `src/polybot/strategy/scanner.py`
- cherry에는 별도 `STRATEGY.md`/`AGENTS.md`가 없다 (신규 봇들과 달리 원조 3봇 구조). 전략 근거는 위 STRATEGY_ANALYSIS.md가 대신한다.
- 과거 운영 슬롯 정보(2026-07-07)는 config hash별 historical cohort로만 사용한다. 현재 분석
  대상은 golden-banana 계좌의 golden-cherry `--yes-only` job이며 Jenkins 이름이 아니라
  workspace/DB/run audit를 함께 확인한다. **`polybot-cherry` job은 elderberry 워크스페이스이니
  이름으로 판단 금지.**
