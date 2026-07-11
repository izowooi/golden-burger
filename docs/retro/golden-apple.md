# golden-apple 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽고
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정한다. `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` gap을 해결하기 전에는 parameter tuning을 제안하지 않는다. 실제 성과는
> `CONFIRMED` fill만 사용하고 legacy `ORDER_ASSUMPTION` cohort를 분리한다.

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 공통 절차·백업·3라운드 프로세스는 `docs/retro/README.md` 참조. 계정 간 A/B 비교는 `docs/ab-retro-playbook.md`.

## 0. 복붙용 회고 프롬프트

```
docs/retro/golden-apple.md 를 읽고 §3(실적 분석)과 §4(반사실 분석)를 실행한 뒤,
§6 표 형식으로 파라미터 교정안을 제시해줘.
REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>
먼저 docs/retro/EVIDENCE_CONTRACT.md의 strict audit gate를 통과시켜라. 통과하지 못하면
파라미터 교정 대신 evidence 복구 계획만 제시해라.
- golden-apple은 같은 코드로 Jenkins job 2개(GOLDEN-APPLE (1)/(2))를 돌린다.
  find로 두 job의 trades.db를 모두 찾아 인스턴스별로 따로 분석하고, 마지막에 둘을 비교해줘.
  같은 시장이 양쪽 DB에 다 있을 수 있으니 포트폴리오 합산 시 condition_id로 dedupe 해줘.
- 상관 클러스터(같은 이벤트 파생 시장)는 이벤트 단위로 묶어서 세고,
  초기 백로그 코호트(운용 개시 직후 일괄 매수분)와 정상 신호 코호트를 분리해서 판단해줘.
- 이 전략은 손절이 없어서 COMPLETED만 보면 승률이 정의상 ~100%다.
  HOLDING 포지션을 중앙 아카이브 최종가로 mark해서 잠긴 손익을 반드시 함께 평가해줘.
- 표본이 부족한 결론에는 신뢰도 '낮음'을 명시해줘.
Jenkins env export 블록 (PRIVATE_KEY 줄 제외):
(1): [붙여넣기]
(2): [붙여넣기]
```

`config.py`의 우선순위는 env > `config.yaml` > 코드 기본값이고 두 인스턴스는 Jenkins env로
차별화된다. 다만 post-instrumentation 운영값의 source of truth는 DB의 `strategy_configs`와
`run_audits`다. export 블록은 현재값·legacy 구간의 cross-check로만 사용하고 secret을 제외한다.

## 1. 전략 요약

**논지**: Polymarket 바이너리 시장에서 확률(가격)이 80% 이상 90% 미만인 쪽(YES/NO 중 높은 쪽)을
매수하고, 90% 도달 시 전량 매도해 거래당 ~10%를 노리는 단순 threshold 전략.
근거는 favorite-longshot bias(고확률 쪽 과소평가)와 정보 확산 관성("80%를 돌파한 컨센서스는
90%로 밀려 올라간다"), 해결 수렴. **손절·시간 청산·만기(redeem) 처리 없음** — 90% 미도달이면
영원히 보유한다. 한 번 거래(또는 rapid_jump로 skip)한 시장은 영구 재거래 금지. 스포츠 카테고리
+ 키워드 제외. Jenkins 5분 주기 one-shot 실행, job별 SQLite 분리. 상세 분석은
`golden-apple/STRATEGY_ANALYSIS.md`.

**진입**: Gamma 스캔에서 필터(카테고리/유동성/거래량) 통과 + `buy_threshold <= p < sell_threshold`
→ 매수 직전 CLOB midpoint 재검증 → limit BUY GTC → 즉시 `HOLDING` 기록 (체결 확인 없음).
재검증에서 `p >= sell_threshold`면 `skipped_markets`에 `rapid_jump`로 영구 제외.

**청산**: 매 사이클 HOLDING 전건에 대해 midpoint 조회 → `p >= sell_threshold`면 전량 limit SELL
GTC → `COMPLETED`. **청산 경로는 이 한 가지뿐이다.** apple의 trades 테이블에는 `exit_reason`
컬럼이 없다 (cherry와 다름) — exit_reason별 분해는 불가능하고 필요도 없다.

**파라미터 표** (env > config.yaml > 코드 기본값. 아래 "config.yaml 기본값"은 repo의 실제 값):

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| buy_threshold | `POLYBOT_BUY_THRESHOLD` | 0.80 | 진입 하한 확률 |
| sell_threshold | `POLYBOT_SELL_THRESHOLD` | 0.90 | 매도 트리거 = 진입 상한(미만) |
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 금액 (USDC) |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 50000 | Gamma `liquidity` 최소값 ($) |
| min_volume | `POLYBOT_MIN_VOLUME` | 100000 | Gamma 누적 `volume` 최소값 (0=비활성) |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 동시 HOLDING 상한 (-1=무제한) |
| excluded_categories | (env 없음, yaml 전용) | Sports/NFL/NBA 등 10종 | 태그+키워드 기반 제외 |
| simulation_mode | (CLI `--simulate`가 override) | false | true면 주문 미전송, `trades_sim.db` 사용 |

주의: 코드 기본값은 yaml과 다르다 (buy_amount 10.0 / min_liquidity 100000 / min_volume 0).
"현행"은 `run_audits`의 기간 내 config hash와 `strategy_configs.config_json`으로 채운다.
Jenkins env는 current/legacy cross-check다.

## 2. 데이터 위치와 스키마

### 자기 DB (인스턴스별 2개)

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-apple/data*" -name "trades.db" 2>/dev/null
```

job명은 바뀔 수 있으니 반드시 find로 찾는다. **결과가 2개 나와야 정상** (GOLDEN-APPLE (1)/(2)).
시뮬레이션 기록은 같은 폴더의 `trades_sim.db`에 별도 저장된다.
로그는 각 job의 `data/<job>/logs/YYYYMMDD.log` (Jenkins 콘솔에도 동일 출력).
사이클마다 `제외 사유 요약 - reason: count` 한 줄이 남는다 — apple의 reason 키:
`excluded_category`, `low_liquidity`, `low_volume`, `no_price_data`, `prob_out_of_range`
(스캔 병목 파악용 보조 데이터).

### 테이블 (`src/polybot/db/models.py` 기준)

**trades** — 핵심 컬럼:
`condition_id`, `market_slug`, `question`, `outcome`("Yes"/"No"), `token_id`,
`buy_price`, `buy_amount`, `buy_shares`, `buy_timestamp`, `buy_probability`,
`sell_price`, `sell_shares`, `sell_timestamp`, `sell_probability`,
`realized_pnl`, `status`, `liquidity_at_buy`, `market_tags`, `created_at`, `updated_at`.

- `status`는 SQLAlchemy Enum이라 **대문자 enum name**으로 저장된다:
  `PENDING_BUY` / `HOLDING` / `PENDING_SELL` / `COMPLETED` / `SKIPPED`.
  실전에서는 `HOLDING`과 `COMPLETED`만 쓰인다 (PENDING_*은 코드에서 미사용).
  회고 시작 시 `SELECT DISTINCT status FROM trades;`로 실제 값을 한 번 확인할 것.
  (README.md의 소문자 `'completed'` 예시 SQL은 틀렸다 — 이 문서 SQL을 쓸 것.)
- `buy_probability`는 매수 직전 재조회 가격이라 `buy_price`와 사실상 동일하다.

**skipped_markets**: `condition_id`, `reason`, `skipped_at`. 실전 reason 값은 `rapid_jump` 하나
(매수 직전 재검증에서 `p >= sell_threshold`였던 시장, 영구 제외).

**market_snapshots**: 테이블 정의는 있으나 **run flow에서 `save_snapshot`이 호출되지 않아
비어 있다 (0행)**. apple 자체 가격 시계열은 없다 — 반사실 분석은 아래 중앙 아카이브만 쓴다.

### 중앙 가격 아카이브 (반사실 분석의 원료)

Gamma keyset cursor를 끝까지 순회해 당시 조건을 통과한 시장을 수집하므로, 가격 시계열은
**nectarine DB의 `market_snapshots`**를 공용 아카이브로 쓴다. 고정 시장 수를 가정하지 말고
`run_audits`의 sweep과 `market_catalog` join coverage를 확인한다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브: honeydew DB (job=polybot-eco, liq >= $15k)
```

- 컬럼: `condition_id`, `probability`(**항상 YES 가격**), `liquidity`, `volume_24h`,
  `timestamp`(UTC naive, 5분 간격)
- 유니버스: 유동성 >= $10k, **60일 보존** → 회고는 이 창 안에서 실행할 것
- **NO 토큰 가격은 1 − YES 근사** (스프레드 무시 근사임을 결과에 명시)
- 시장이 해결(resolve)되면 스냅샷이 끊긴다 → 해결된 보유분의 최종가는
  `trades.sell_price` 또는 0/1 (redeem 가치)로 처리

## 3. decision/status 진단 SQL (기간 filter 추가 필수)

> 아래 `trades` SQL은 decision/status 진단용이다. 모든 query에 `REVIEW_START`/`REVIEW_END`
> half-open UTC filter를 추가한다. 실제 P&L·승률은 order ID로 ledger를 join해 `CONFIRMED` fill의
> partial size/price/fee로 다시 계산하며, coverage 없는 legacy 행을 합계에 넣지 않는다.

**해석 전제**: apple 구조상 `COMPLETED`는 `sell_price >= 0.90 > buy_price`라서 정의상 거의 전부
이익이다. 완결 승률은 무의미하고, 진짜 지표는 (1) **진입 대비 완결률**, (2) **HOLDING에 잠긴
미실현 손익**(§4에서 아카이브로 mark), (3) 완결까지 걸린 시간 대비 수익률이다.

각 인스턴스 DB에 대해 `sqlite3 <trades.db 경로>`로 실행:

```sql
-- 3-1. 상태 깔때기: 진입 대비 완결률
SELECT status, COUNT(*) AS n FROM trades GROUP BY status;
SELECT reason, COUNT(*) AS n FROM skipped_markets GROUP BY reason;

-- 3-2. 완결 거래 요약 (건수/승률/총·평균 P&L/평균·중앙 수익률/평균 보유일)
SELECT
  COUNT(*)                                                        AS n_completed,
  SUM(realized_pnl > 0)                                           AS wins,
  ROUND(1.0 * SUM(realized_pnl > 0) / COUNT(*), 3)                AS win_rate,
  ROUND(SUM(realized_pnl), 4)                                     AS total_pnl,
  ROUND(AVG(realized_pnl), 4)                                     AS avg_pnl,
  ROUND(AVG(sell_price / buy_price - 1), 4)                       AS avg_ret,
  ROUND(AVG(julianday(sell_timestamp) - julianday(buy_timestamp)), 2) AS avg_hold_days
FROM trades
WHERE status = 'COMPLETED';

-- 3-3. 중앙(median) 수익률
SELECT ROUND(AVG(ret), 4) AS median_ret FROM (
  SELECT sell_price / buy_price - 1 AS ret
  FROM trades WHERE status = 'COMPLETED'
  ORDER BY ret
  LIMIT 2 - (SELECT COUNT(*) FROM trades WHERE status = 'COMPLETED') % 2
  OFFSET (SELECT (COUNT(*) - 1) / 2 FROM trades WHERE status = 'COMPLETED')
);

-- 3-4. 진입가 버킷별 성과 (2%p 구간) — 진입 밴드 교정의 핵심 근거
-- 상단 버킷(0.88-0.90)은 이익 상한 ~2%인데 리스크 -100%: 완결률과 잔존 HOLDING을 같이 봐야 함
SELECT
  printf('%.2f-%.2f', CAST(buy_price * 50 AS INT) / 50.0,
                      CAST(buy_price * 50 AS INT) / 50.0 + 0.02)  AS entry_bucket,
  COUNT(*)                                                        AS n_total,
  SUM(status = 'COMPLETED')                                       AS n_completed,
  SUM(status = 'HOLDING')                                         AS n_holding,
  ROUND(1.0 * SUM(status = 'COMPLETED') / COUNT(*), 3)            AS completion_rate,
  ROUND(SUM(CASE WHEN status = 'COMPLETED' THEN realized_pnl END), 4)          AS pnl_completed,
  ROUND(AVG(CASE WHEN status = 'COMPLETED' THEN sell_price / buy_price - 1 END), 4) AS avg_ret_completed,
  ROUND(AVG(CASE WHEN status = 'COMPLETED'
        THEN julianday(sell_timestamp) - julianday(buy_timestamp) END), 2)     AS avg_hold_days
FROM trades
WHERE buy_price IS NOT NULL
GROUP BY entry_bucket
ORDER BY entry_bucket;

-- 3-5. 보유시간 분포 (완결 거래)
SELECT
  CASE
    WHEN julianday(sell_timestamp) - julianday(buy_timestamp) < 1  THEN 'a) <1d'
    WHEN julianday(sell_timestamp) - julianday(buy_timestamp) < 3  THEN 'b) 1-3d'
    WHEN julianday(sell_timestamp) - julianday(buy_timestamp) < 7  THEN 'c) 3-7d'
    WHEN julianday(sell_timestamp) - julianday(buy_timestamp) < 14 THEN 'd) 7-14d'
    ELSE 'e) >=14d'
  END                                       AS hold_bucket,
  COUNT(*)                                  AS n,
  ROUND(SUM(realized_pnl), 4)               AS pnl,
  ROUND(AVG(sell_price / buy_price - 1), 4) AS avg_ret
FROM trades
WHERE status = 'COMPLETED'
GROUP BY hold_bucket ORDER BY hold_bucket;

-- 3-6. 미청산 포지션 나이 (오래된 것 = 해결됐거나 뒤집힌 후보)
SELECT id, substr(question, 1, 60) AS q, outcome, buy_price,
       ROUND(julianday('now') - julianday(buy_timestamp), 1) AS days_held
FROM trades
WHERE status = 'HOLDING'
ORDER BY days_held DESC;

-- 3-7. 카테고리(market_tags)별 분해
SELECT market_tags, COUNT(*) AS n,
       SUM(status = 'COMPLETED') AS completed, SUM(status = 'HOLDING') AS holding,
       ROUND(SUM(CASE WHEN status = 'COMPLETED' THEN realized_pnl END), 4) AS pnl
FROM trades
GROUP BY market_tags ORDER BY n DESC LIMIT 30;

-- 3-8. 일자별 매수 건수 — 초기 백로그 코호트 경계 판별용 (§5)
SELECT date(buy_timestamp) AS d, COUNT(*) AS buys
FROM trades WHERE buy_timestamp IS NOT NULL
GROUP BY d ORDER BY d;
```

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

### 준비: 아카이브 붙이기 + 커버리지 확인

```sql
-- sqlite3 <apple trades.db 경로> 에서:
ATTACH DATABASE '<nectarine trades.db 경로>' AS archive;

-- 커버리지: apple 거래 중 아카이브에 시계열이 있는 비율 (누락분은 반사실 대상에서 제외하고 명시)
SELECT COUNT(*) AS total,
       SUM(EXISTS (SELECT 1 FROM archive.market_snapshots s
                   WHERE s.condition_id = t.condition_id)) AS covered
FROM trades t
WHERE t.status IN ('HOLDING', 'COMPLETED');

-- 포지션 가격 시계열 추출 (NO 보유분은 1-YES 근사). CSV로 뽑아 Python 시뮬레이션 입력으로 사용
-- .mode csv / .output apple_series.csv 후 실행
SELECT t.id AS trade_id, t.condition_id, t.outcome, t.buy_price, t.buy_shares,
       t.buy_timestamp, t.status, t.sell_price, t.sell_timestamp,
       s.timestamp AS snap_ts,
       CASE WHEN t.outcome = 'No' THEN 1.0 - s.probability ELSE s.probability END AS pos_price
FROM trades t
JOIN archive.market_snapshots s ON s.condition_id = t.condition_id
WHERE t.status IN ('HOLDING', 'COMPLETED')
  AND s.timestamp >= t.buy_timestamp
ORDER BY t.id, s.timestamp;

-- HOLDING 포지션 아카이브 최종가 mark (잠긴 손익 + 스냅샷 끊긴 시점)
-- last_seen이 오래 전이면 해결된 시장 → 최종가치 0/1을 수동 판정해 반영
SELECT t.id, t.outcome, t.buy_price, t.buy_shares,
       (SELECT CASE WHEN t.outcome = 'No' THEN 1.0 - s.probability ELSE s.probability END
        FROM archive.market_snapshots s
        WHERE s.condition_id = t.condition_id ORDER BY s.timestamp DESC LIMIT 1) AS last_price,
       (SELECT MAX(s.timestamp) FROM archive.market_snapshots s
        WHERE s.condition_id = t.condition_id) AS last_seen
FROM trades t
WHERE t.status = 'HOLDING';
```

### (a) 청산 스윕 — 현행: sell 0.90, 손절 없음, 시간 청산 없음

거래별 `pos_price` 시계열에 대해 **first-touch** 규칙으로 격자 시뮬레이션 (Python 권장):
각 조합에 대해 시계열을 순회하며 ①`pos_price >= TP` ②`pos_price/buy_price - 1 <= SL`
③`snap_ts - buy_timestamp >= max_hold` 중 먼저 닿는 것으로 청산가 결정, 아무것도 안 닿으면
마지막 스냅샷 가격(또는 해결 시 0/1)으로 mark.

격자 (현행 설정 주변):

- **TP (sell_threshold)**: 0.86, 0.88, **0.90(현행)**, 0.92, 0.94, "해결까지 보유(1.0)"
- **SL (진입가 대비 손절)**: **없음(현행)**, -10%, -20%, -30%
- **max_hold**: **없음(현행)**, 7d, 14d, 30d

출력 표: 격자 조합별 | 총 P&L | 완결률 | 평균 수익률 | 평균 보유일 | 미청산 잔존 수 |.
HOLDING 잔존분의 mark 값 포함/제외 두 버전을 모두 제시할 것 (손절 없는 전략은 잔존분이 결론을
뒤집을 수 있다). 체결은 스냅샷 가격 즉시 체결 가정(낙관적) — 결과에 명시.

### (b) 전략 고유 노브 스윕 — 진입 밴드 + 유니버스 필터

**노브 1: 진입 밴드 [L, U)** — 현행 [0.80, 0.90).
- L ∈ {0.75, 0.78, **0.80**, 0.83, 0.85}, U ∈ {0.88, **0.90**, 0.92}
- 재생 방법: 아카이브의 각 `condition_id`에 대해 YES 가격이 **아래에서 밴드로 처음 진입한
  스냅샷**을 가상 매수 시점으로 잡는다 (첫 관측치가 이미 >= U면 rapid_jump 에뮬레이션 = 영구
  제외 — 봇 동작과 동일). NO 쪽 후보는 1-YES로 같은 규칙 적용. 이후 현행 청산 규칙(>= U 매도,
  아니면 보유 mark)을 적용해 격자별 가상 성과를 계산한다.
- 실제 진입분과 가상 진입분을 구분해 표기: "L을 0.75로 내렸다면 추가로 잡혔을 진입 N건, 그
  가상 P&L $X" / "L을 0.85로 올렸다면 걸러졌을 실거래 N건, 그 실제 P&L $Y".

**노브 2: min_liquidity** — 현행 yaml 50000 (env 확인).
- {10k, 30k, **50k**, 100k}. 가상 진입 시점의 아카이브 `liquidity` 컬럼으로 필터 재현.
- 주의: 아카이브 유니버스 하한이 $10k라 그 밑으로는 스윕 불가.

**노브 3: min_volume** — 현행 yaml 100000 (env 확인).
- 봇은 Gamma **누적** `volume`을 쓰는데 아카이브에는 `volume_24h`만 있다 → 누적 volume 필터는
  재현 불가. `volume_24h` 기반 근사 스윕({0, 5k, 20k})만 하고 근사임을 명시.

**가상 진입의 카테고리 필터**: post-instrumentation 구간은 `market_catalog.question`과
`tags_json`으로 스포츠 제외 필터를 재현한다. legacy catalog gap은 Gamma metadata 재조회 또는
“필터 미적용 universe”로 분리하고 coverage를 명시한다.

### (c) 데이터 한계 (결과 보고서에 반드시 명시)

- archive midpoint는 actual execution을 재현하지 않는다. legacy `trades`의 GTC 접수 상태는
  fill이 아니며, 실현 결과는 `CONFIRMED order_fills`의 partial size/price/fee로 계산한다.
  ledger gap은 `ORDER_ASSUMPTION`으로 분리하고 account evidence/NAV와 대사한다.
- NO 가격은 1 − YES 근사 (스프레드 무시).
- 아카이브 유니버스(liq >= $10k)와 apple의 실행별 필터·수집 시점이 완전히 일치하지 않을 수
  있다. 고정 cap 문제가 아니라 run/cursor/cadence gap이므로 위 coverage SQL과 catalog를
  사용해 누락률을 보고한다.
- 60일 보존 — 회고가 늦어지면 초기 거래의 시계열이 유실된다.
- 해결된 시장은 스냅샷이 끊긴다 — 최종가는 `trades.sell_price` 또는 0/1 수동 판정.

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트에서 파생된 시장들(예: 같은 선거의 후보별/주별 시장)은 한 방향
  으로 같이 움직인다. `market_slug` 접두어·`question` 키워드로 이벤트 단위 묶음을 만들어
  **이벤트 수 기준(유효 n)** 으로 다시 세라. 명목 20건 = 유효 1건일 수 있다.
- **코호트 분리**: max_positions 무제한 + 재거래 금지 구조상, 운용 개시 직후 이미 80~90% 구간에
  있던 시장을 첫 며칠간 일괄 매수한다(초기 백로그). §3-8로 매수 폭주 구간을 찾아
  **백로그 코호트 vs 정상 신호 코호트**(운용 중 새로 밴드에 진입한 시장)를 분리 집계하라.
  전략 논지("80% 돌파 후 90% 수렴")를 검증하는 것은 후자다.
- **생존 편향**: COMPLETED만 집계하면 손절 없는 전략의 손실이 전부 빠진다(패배는 전부 HOLDING에
  잠겨 있음). §4의 HOLDING mark 포함 수치를 항상 병기하라.
- **인스턴스 중복**: (1)/(2)가 같은 시장을 양쪽에서 샀을 수 있다. 인스턴스별 분석 후 합산 시
  `condition_id`로 dedupe. env가 같다면 두 DB는 사실상 같은 전략의 시차 복제본이므로 독립
  표본으로 취급하지 말 것.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

인스턴스별로 하나씩 (env가 동일하면 통합 1개 + 명시):

| 파라미터 | 현행 (resolved config cohort) | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| POLYBOT_BUY_THRESHOLD | | | | |
| POLYBOT_SELL_THRESHOLD | | | | |
| POLYBOT_MIN_LIQUIDITY | | | | |
| POLYBOT_MIN_VOLUME | | | | |
| POLYBOT_MAX_POSITIONS | | | | |
| POLYBOT_BUY_AMOUNT | | | | |
| (구조 제안: SL/max_hold 등 코드 변경 필요 항목) | 없음 | | | |

- 손절(SL)·시간 청산(max_hold)은 apple 코드에 노브가 없다 — §4(a)에서 유의미하면 "env가 아닌
  코드 변경 필요" 항목으로 분리해 제안한다.
- **라운드 절차**: 제안값으로 2차 테스트 → 4주 후 재회고 → 3차 교정 (전체 절차는
  `docs/retro/README.md` §3). 파라미터 변경은 Jenkins env 교체만으로 끝난다.
  apple은 이미 2개 슬롯이므로 cherry식 병행이 자연스럽다: **(1)은 현행 유지(대조군),
  (2)에 제안 env 적용(실험군)** — 계정 간 비교 절차는 `docs/ab-retro-playbook.md`.
- 변경 후 새 `config_hash`/Git cohort가 `run_audits`에 나타나는지 확인한다. 아래 `운용 이력`에는
  날짜, 의사결정 이유, rollback 기준과 secret을 제거한 env diff를 보조 기록한다.

## 7. 기준 정보

- 이 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f`
- 전략 문서: `golden-apple/STRATEGY_ANALYSIS.md` (별도 STRATEGY.md 없음 — 이 파일이 전략
  논지·코드 분석·약점 목록을 겸한다. 특히 §7 약점: 체결 미확인, 해결 시장 출구 없음,
  스포츠 키워드 오폭, 페이지네이션 상한)
- 코드 근거: `golden-apple/src/polybot/config.py`(env 이름), `db/models.py`(스키마),
  `strategy/trader.py`(청산 로직), `strategy/scanner.py`(제외 사유 키)

## 운용 이력

(라운드 시작/변경 시 여기에 날짜 + env 블록(키 제외)을 추가)
