# golden-banana 회고(포스트모템) 가이드

> 회고 실행: 운영 시작 4주 후. 이 문서 경로를 AI에게 주면 된다.
> 마스터 플레이북: `docs/retro/README.md` (3라운드 교정 프로세스, 공통 주의사항)

## 0. 복붙용 회고 프롬프트

```
docs/retro/golden-banana.md 를 읽고 §3(실적 분석 SQL)과 §4(반사실 분석)를 실행한 뒤,
§6 표 형식으로 파라미터 교정안을 제시해줘.

주의사항:
- status 값은 대문자 enum 이름('COMPLETED','HOLDING' 등)이다. 소문자로 쿼리하면 0건 나온다.
- entry_reason='short_momentum_positive'(cold-start fallback)와 'golden_cross' 코호트를
  반드시 분리해서 판단해줘. 운영 초기(스냅샷 축적 전)의 fallback 진입은 별도 코호트다.
- 상관 클러스터(같은 이벤트에서 파생된 시장들)는 이벤트 단위로 묶어서 세어줘.
- banana가 산 쪽이 outcome='No'인 거래는 아카이브 YES 가격을 1-YES로 뒤집어서 재생해줘.
- 표본이 부족한 결론에는 신뢰도 '낮음'을 명시해줘.

Jenkins env 블록 (PRIVATE_KEY 줄 제외하고 붙여넣음):
[여기에 붙여넣기]
```

운영 env는 repo에 없다 (config.yaml은 기본값일 뿐). **Jenkins job 설정의 export 블록(키 제외)을
복사해 위 프롬프트에 함께 붙여넣는 것이 필수다** — env > yaml > 코드 기본값 순으로 적용되므로
env 블록 없이는 실제 운용 파라미터를 알 수 없다.

## 1. 전략 요약

**논지**: Polymarket에서 확률 85~97% 구간의 고확률(favorite) outcome은 "정보 확산 지연" 때문에
100%로 서서히 수렴한다. 이때 아무 때나 사는 게 아니라(구세대 apple 방식), 자체 축적한 5분 간격
가격 스냅샷으로 단기(15분)·장기(6시간) momentum을 계산해 **골든크로스(단기-장기 ≥ +2%p/스냅샷)가
발생한 순간**에만 편승 진입한다. 청산은 4중: 확률 0.97 도달(threshold) / 진입가 대비 +7%(take_profit)
/ -10%(stop_loss) / 데드크로스(dead_cross).

**진입 규칙** (전부 AND, `scanner.py` → `trader.py`):
1. Gamma 스캔에서 liquidity ≥ min_liquidity, 스포츠 아님 (tags + question/slug 키워드 필터)
2. YES/NO 중 **높은 쪽** 확률이 `buy_threshold ≤ p ≤ sell_threshold` (상한 포함) — 높은 쪽 토큰을 산다 (NO일 수도 있음)
3. momentum 진입 시그널 (`momentum.py get_entry_signal`):
   - 골든크로스: `short_mom - long_mom ≥ golden_cross_threshold` (+ `require_positive_long_momentum`이면 long_mom > 0 필수) → `golden_cross`
   - **cold-start fallback**: 스냅샷 6개 미만이라 장기 momentum이 None이면 단기 momentum > 0만으로 진입 → `short_momentum_positive`
   - 단기 데이터(3개)도 없으면 진입 불가
4. 시장당 평생 1회 (`condition_id` UNIQUE). CLOB midpoint 재검증: > sell_threshold면 `rapid_jump`로 영구 skip
5. GTC limit BUY @ midpoint, 접수 즉시 HOLDING 기록 (체결 확인 없음)

**청산 규칙** (`trader.py execute_sell`, 우선순위 순): ① midpoint ≥ sell_threshold → `threshold`
② PnL ≤ stop_loss → `stop_loss` ③ PnL ≥ take_profit → `take_profit` ④ 데드크로스 → `dead_cross`.
매도도 GTC limit @ midpoint, 접수 즉시 COMPLETED.

**파라미터 표** (env > config.yaml > 코드 기본값 순 적용, env 이름은 `src/polybot/config.py` 실제 파싱 기준):

| 파라미터 | env 이름 | config.yaml 기본값 | 의미 |
|---|---|---|---|
| buy_threshold | `POLYBOT_BUY_THRESHOLD` | 0.85 | 매수 최소 확률 |
| sell_threshold | `POLYBOT_SELL_THRESHOLD` | 0.97 | 매수 상한이자 threshold 청산 트리거 |
| buy_amount_usdc | `POLYBOT_BUY_AMOUNT` | 5.0 | 1회 매수 금액 USDC (코드 기본값은 10.0 — yaml이 5.0으로 덮음) |
| min_liquidity | `POLYBOT_MIN_LIQUIDITY` | 50000 | 최소 유동성 필터 ($) |
| take_profit_percent | `POLYBOT_TAKE_PROFIT` | 0.07 | 진입가 대비 이익실현 (+7%) |
| stop_loss_percent | `POLYBOT_STOP_LOSS` | -0.10 | 진입가 대비 손절 (-10%) |
| max_positions | `POLYBOT_MAX_POSITIONS` | -1 | 동시 포지션 상한 (-1 = 무제한) |
| momentum.enabled | `POLYBOT_MOMENTUM_ENABLED` | true | momentum 게이트 on/off |
| momentum.short_window | `POLYBOT_MOMENTUM_SHORT_WINDOW` | 3 | 단기 윈도우 (3 스냅샷 ≈ 15분) |
| momentum.long_window | `POLYBOT_MOMENTUM_LONG_WINDOW` | 72 | 장기 윈도우 (72 스냅샷 ≈ 6시간) |
| momentum.golden_cross_threshold | `POLYBOT_GOLDEN_CROSS_THRESHOLD` | 0.02 | 진입: 단기-장기 ≥ 이 값 (**스냅샷당 기울기** 단위) |
| momentum.dead_cross_threshold | `POLYBOT_DEAD_CROSS_THRESHOLD` | -0.02 | 청산: 단기-장기 ≤ 이 값 |
| momentum.require_positive_long_momentum | `POLYBOT_REQUIRE_POSITIVE_LONG_MOMENTUM` | true | 골든크로스여도 장기 momentum ≤ 0이면 진입 거부 |
| excluded_categories | (env 없음 — yaml만) | Sports 계열 10종 | 카테고리+키워드 제외 |
| simulation_mode | (env 없음 — CLI `--simulate`) | false | sim이면 trades_sim.db 사용 |

주의: momentum = `(최신 확률 - 가장 오래된 확률) / 스냅샷 수`. MA cross가 아니라 양 끝점 기울기 비교다.
threshold 0.02는 "15분에 약 6%p 급등" 수준이라 **실운영에서 golden_cross 경로는 거의 발화하지 않고,
실제 매수 대부분이 cold-start fallback(`short_momentum_positive`)이었다** (2026-02 운영 로그 실측,
`golden-banana/STRATEGY_ANALYSIS.md` §7.1 참조). 회고의 최우선 질문: **entry_reason 분포가 지금도 그런가.**

## 2. 데이터 위치와 스키마

### 자기 DB

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-banana/data*" -name "trades.db" 2>/dev/null
```

job명은 바뀔 수 있으니 반드시 find로 찾는다. 시뮬레이션 기록은 같은 폴더의 `trades_sim.db` 별도.
Jenkins 콘솔 로그와 동일 내용이 `data/<job>/logs/YYYYMMDD.log`에도 남는다 — 사이클마다
`제외 사유 요약 - reason: count` 한 줄이 있어 스캔 병목(prob_out_of_range / no_signal /
insufficient_short_data 등) 파악에 쓴다.

테이블 (`src/polybot/db/models.py` 기준 — 이 컬럼만 존재한다):

- **trades**: `id, condition_id(UNIQUE), market_slug, question, outcome('Yes'/'No'), token_id,
  buy_price, buy_amount, buy_shares, buy_order_id, buy_timestamp, buy_probability,
  sell_price, sell_shares, sell_order_id, sell_timestamp, sell_probability, realized_pnl,
  status, entry_reason, exit_reason, short_momentum_at_buy, long_momentum_at_buy,
  short_momentum_at_sell, long_momentum_at_sell, liquidity_at_buy, market_tags, created_at, updated_at`
  - `status`는 **대문자 enum 이름**으로 저장: `PENDING_BUY / HOLDING / PENDING_SELL / COMPLETED / SKIPPED`
    (golden-banana/README.md의 소문자 예시 SQL은 틀렸다 — 실측 확인됨)
  - `entry_reason` 값: `golden_cross`, `short_momentum_positive`, `momentum_disabled` (+ 방어적 `unknown`)
  - `exit_reason` 값 (trader.py/momentum.py 기준): `threshold`, `take_profit`, `stop_loss`, `dead_cross`
  - `buy_price` = `buy_probability` (둘 다 매수 시점 midpoint), timestamp는 전부 **UTC naive**
- **market_snapshots** (banana 자체 보유): `id, condition_id, probability, liquidity, volume_24h, timestamp`
  - **주의 1 — 7일 보존**: Phase 4가 매 사이클 `cleanup_old_snapshots(days=7)`로 지운다.
    한 달 회고 시점엔 최근 7일치만 남아 있으므로 **월간 반사실 분석에는 쓸 수 없다.**
  - **주의 2 — 가격 의미가 아카이브와 다르다**: banana 스냅샷의 probability는 YES/NO 중
    **높은 쪽** 가격이다 (`filters.get_high_probability_outcome`). 중앙 아카이브는 항상 YES 가격.
  - 용도: 최근 7일 안의 진입에 한해 "봇이 실제로 본" momentum 재현 검증용 ground truth.
- **skipped_markets**: `id, condition_id(UNIQUE), reason('rapid_jump' 등), skipped_at`
  - 여기 오르면 영구 재거래 금지. rapid_jump가 몇 건인지, 그 시장들이 이후 어떻게 됐는지도 회고 대상.

### 중앙 가격 아카이브 (월간 반사실 분석의 원료)

모든 봇이 같은 gamma sweep(가장 오래된 활성 시장 ~2100개)을 스냅샷하므로, 가격 시계열은
**nectarine DB의 market_snapshots**를 공용 아카이브로 쓴다:

```bash
find /Users/jongwoopark/.jenkins/workspace -path "*golden-nectarine/data*" -name "trades.db" 2>/dev/null
# 2026-07 기준 job=polybot-fox. 보조 아카이브: honeydew DB (job=polybot-eco, liq >= $15k)
```

- 컬럼: `condition_id, probability(**항상 YES 가격**), liquidity, volume_24h, timestamp(UTC naive, 5분 간격)`
- 유니버스: 유동성 ≥ $10k, **60일 보존** — banana의 min_liquidity 50k 유니버스를 포함한다
  (아카이브 쪽 `liquidity` 컬럼으로 50k 필터를 재적용해서 쓴다)
- NO 토큰 가격은 `1 - YES` 근사 (스프레드 무시 근사임을 결과에 명시)
- 시장이 해결되면 스냅샷이 끊긴다 → 해결된 보유분의 최종가는 trades.sell_price 또는 0/1(redeem)로 처리

## 3. 실적 분석 SQL (banana trades.db에 sqlite3로 그대로 실행)

```bash
sqlite3 "$(find /Users/jongwoopark/.jenkins/workspace -path '*golden-banana/data*' -name 'trades.db' 2>/dev/null | head -1)"
```

### 3.1 완결 거래 전체 요약 (건수 / 승률 / 평균·중앙 수익률)

```sql
WITH c AS (
  SELECT (sell_price - buy_price) / buy_price AS ret, realized_pnl
  FROM trades
  WHERE status = 'COMPLETED' AND buy_price > 0
)
SELECT COUNT(*)                                        AS n,
       ROUND(AVG(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END), 3) AS win_rate,
       ROUND(AVG(ret), 4)                              AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                     AS total_pnl,
       ROUND(MIN(ret), 4)                              AS worst,
       ROUND(MAX(ret), 4)                              AS best
FROM c;

-- 중앙 수익률 (window function, SQLite 3.25+)
WITH c AS (
  SELECT (sell_price - buy_price) / buy_price AS ret,
         ROW_NUMBER() OVER (ORDER BY (sell_price - buy_price) / buy_price) AS rn,
         COUNT(*) OVER () AS n
  FROM trades WHERE status = 'COMPLETED' AND buy_price > 0
)
SELECT ROUND(AVG(ret), 4) AS median_ret FROM c WHERE rn IN ((n + 1) / 2, (n + 2) / 2);
```

### 3.2 exit_reason별 분해 — 4중 청산 조건 중 무엇이 실제로 일하나

```sql
SELECT exit_reason,
       COUNT(*)                                             AS n,
       ROUND(AVG(CASE WHEN sell_price > buy_price THEN 1.0 ELSE 0 END), 3) AS win_rate,
       ROUND(AVG((sell_price - buy_price) / buy_price), 4)  AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                          AS total_pnl
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY exit_reason
ORDER BY total_pnl DESC;
```

### 3.3 entry_reason별 분해 — **이 봇 회고의 핵심 축**

```sql
-- golden_cross vs short_momentum_positive 코호트 성과 (§5 코호트 분리 참조)
SELECT entry_reason,
       COUNT(*)                                             AS n,
       ROUND(AVG(CASE WHEN sell_price > buy_price THEN 1.0 ELSE 0 END), 3) AS win_rate,
       ROUND(AVG((sell_price - buy_price) / buy_price), 4)  AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                          AS total_pnl,
       ROUND(AVG(short_momentum_at_buy), 6)                 AS avg_short_mom,
       ROUND(AVG(long_momentum_at_buy), 6)                  AS avg_long_mom
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY entry_reason;

-- 미완결 포함 진입 경로 분포 (fallback 의존도)
SELECT entry_reason, status, COUNT(*) FROM trades GROUP BY entry_reason, status;
```

### 3.4 진입가 버킷별 성과 — 밴드(0.85~0.97) 교정 근거

buy_price ≥ 0.9346이면 take_profit 목표가(×1.07)가 1.0을 넘어 **수학적으로 도달 불가**다.
0.94+ 버킷이 threshold/dead_cross에만 의존하는지 반드시 본다.

```sql
SELECT CASE
         WHEN buy_price < 0.88 THEN '0.85-0.88'
         WHEN buy_price < 0.91 THEN '0.88-0.91'
         WHEN buy_price < 0.94 THEN '0.91-0.94'
         ELSE                       '0.94-0.97'
       END                                                  AS band,
       COUNT(*)                                             AS n,
       ROUND(AVG(CASE WHEN sell_price > buy_price THEN 1.0 ELSE 0 END), 3) AS win_rate,
       ROUND(AVG((sell_price - buy_price) / buy_price), 4)  AS avg_ret,
       ROUND(SUM(realized_pnl), 4)                          AS total_pnl,
       SUM(CASE WHEN exit_reason = 'take_profit' THEN 1 ELSE 0 END) AS n_tp,
       SUM(CASE WHEN exit_reason = 'stop_loss'   THEN 1 ELSE 0 END) AS n_sl,
       SUM(CASE WHEN exit_reason = 'dead_cross'  THEN 1 ELSE 0 END) AS n_dc,
       SUM(CASE WHEN exit_reason = 'threshold'   THEN 1 ELSE 0 END) AS n_th
FROM trades
WHERE status = 'COMPLETED' AND buy_price > 0
GROUP BY band ORDER BY band;
```

### 3.5 보유시간 분석

```sql
SELECT exit_reason,
       COUNT(*)                                                            AS n,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_h,
       ROUND(MIN((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS min_h,
       ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_h
FROM trades
WHERE status = 'COMPLETED' AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason;

-- 진입 직후(1시간 내) 청산되는 churn 감지 (0.97 근접 진입 → 즉시 threshold 매도)
SELECT COUNT(*) AS churn_1h
FROM trades
WHERE status = 'COMPLETED'
  AND (julianday(sell_timestamp) - julianday(buy_timestamp)) * 24 < 1.0;
```

### 3.6 미청산 보유 + 해결(resolved) 잔류 포지션 감지

시장이 해결되면 orderbook이 사라져 `get_midpoint`가 실패하고 포지션이 HOLDING으로 영구 잔류한다
(realized_pnl에 미반영 → 통계 왜곡). 아카이브 스냅샷이 끊겼는지로 감지한다:

```sql
-- banana DB에 접속한 상태에서 (경로는 find 결과로 치환):
ATTACH '/Users/jongwoopark/.jenkins/workspace/<polybot-fox-workspace>/golden-nectarine/data/<job>/trades.db' AS arc;

SELECT t.id, substr(t.question, 1, 50) AS q, t.outcome, t.buy_price,
       t.buy_timestamp, MAX(s.timestamp) AS last_snapshot
FROM trades t
LEFT JOIN arc.market_snapshots s ON s.condition_id = t.condition_id
WHERE t.status = 'HOLDING'
GROUP BY t.id
ORDER BY last_snapshot;
-- last_snapshot이 현재(UTC)보다 1시간 이상 과거면 해결/유니버스 이탈 의심.
-- 해결분은 Polymarket UI에서 결과 확인 후 최종가 0/1로 수동 정산해 §3.1 통계에 보정 반영.
```

### 3.7 skip·카테고리 보조 집계

```sql
SELECT reason, COUNT(*) FROM skipped_markets GROUP BY reason;

SELECT market_tags, COUNT(*) AS n, ROUND(SUM(realized_pnl), 4) AS pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY market_tags ORDER BY pnl DESC;
```

## 4. 반사실(what-if) 분석 레시피 — 수치 제안의 핵심

공통 준비: banana trades.db에 아카이브를 `ATTACH ... AS arc` 하고, 각 거래의 condition_id로
아카이브 시계열을 붙인다. **banana의 포지션 가격 = outcome이 'Yes'면 arc.probability,
'No'면 1 - arc.probability** (스프레드 무시 근사). Python(uv run)으로 pandas 없이도
sqlite3 + 반복문이면 충분하다.

### (a) 청산 스윕 — TP/SL/보유시간 격자

완결 + 보유 거래 전체에 대해, `buy_timestamp` 이후의 아카이브 시계열을 5분 단위로 재생하며
격자별 "먼저 닿는 조건"으로 가상 청산한다. 현행 exit 우선순위(threshold → SL → TP)를 그대로 적용.

- **TP 격자**: {+3%, +5%, **+7%(현행)**, +10%, TP 없음}
- **SL 격자**: {-5%, -8%, **-10%(현행)**, -15%, SL 없음}
- **sell_threshold 격자**: {0.95, 0.96, **0.97(현행)**, 0.99}
- **최대 보유시간 격자** (현행엔 없는 노브 — 추가 검토용): {24h, 72h, 7d, 무제한}

산출물: 격자 조합별 `총 P&L / 승률 / 평균 보유시간 / 강제청산 비율` 표. 시계열이 끊긴(해결된)
시장은 마지막 스냅샷 이후 실제 결과(trades.sell_price 또는 0/1)로 정산하고, 그런 거래 수를 명시.
dead_cross 청산의 반사실 재생은 momentum 재계산이 필요하므로 (b)의 재생 코드와 공유한다.

### (b) 전략 고유 노브 스윕 — 진입 게이트 3종

아카이브에서 5분 시계열로 favorite 가격 `f(t) = max(p_yes, 1 - p_yes)`를 만들고
(banana는 높은 쪽을 사므로 — 자체 스냅샷과 같은 의미로 변환), banana의 진입 규칙을 재생한다:
`f(t)`가 [buy_threshold, sell_threshold] 밴드 안 + momentum 게이트 통과 시점 = 가상 진입.
가상 진입 후 성과는 (a)의 현행 exit 규칙으로 평가. liquidity ≥ 50000 필터를 arc.liquidity로 재적용.

1. **golden_cross_threshold 스윕**: {0.001, 0.0025, 0.005, 0.01, **0.02(현행)**}
   - momentum 정의를 코드와 동일하게: `(마지막 - 처음) / 개수`, 단기 = 최근 3개, 장기 = 최근 72개
     (72개 미만이면 6개 이상일 때 전량 사용 — `momentum.py get_long_momentum`의 fallback 그대로)
   - 산출물: threshold별 "발화한 golden_cross 진입 수 / 그 진입들의 가상 P&L". 현행 0.02에서
     발화 0건이면 "게이트가 전략을 비활성화시킨다"는 §1의 가설이 확정된다.
2. **cold-start fallback 게이트 스윕**: 최소 스냅샷 수 {**6(현행 사실상)**, 12, 36, 72}
   - 실거래 중 `entry_reason='short_momentum_positive'`인 건들을 대상으로, "아카이브 기준 그 시점에
     스냅샷이 N개 이상 있어야 진입"이라는 가상 게이트를 적용했을 때 걸러졌을 진입과 그 P&L 계산.
   - 걸러진 진입의 실제 성과가 나빴다면 fallback 강화(또는 제거) 근거가 된다.
3. **진입 밴드 스윕**: buy_threshold {0.80, **0.85(현행)**, 0.90} × 진입 상한 {0.94, **0.97(현행)**}
   - 상한 0.94 분리안은 §3.4의 "TP 도달 불가 + churn" 버킷 성과가 근거.
   - 산출물: 밴드 조합별 가상 진입 수 / 총 P&L / 승률.

`dead_cross_threshold`({-0.01, **-0.02(현행)**, -0.04})와 `require_positive_long_momentum`(on/off)은
위 재생 코드에서 부수적으로 함께 스윕할 수 있다.

### (c) 데이터 한계 (결과 보고 시 반드시 명시)

- **momentum 재현은 근사다**: 봇이 실제로 본 것은 자체 스냅샷(높은 쪽 가격, 7일 보존)이고,
  재생은 아카이브(YES 가격, 60일)를 favorite 가격으로 변환한 것이다. 두 sweep의 실행 시각이
  달라 스냅샷 개수·타이밍이 어긋날 수 있다. 최근 7일 거래는 자체 market_snapshots로 교차 검증 가능.
- **NO 근사**: 1-YES는 스프레드를 무시한다. favorite이 NO인 시장에서 오차가 몇 tick 생긴다.
- **체결 가정**: 실봇도 반사실도 "midpoint GTC limit = 즉시 전량 체결" 가정. 실제 미체결
  (특히 급락장의 stop_loss 미체결)은 양쪽 다 반영 안 됨 — 지갑 잔고 대사로 보정 (`docs/retro/README.md` §5).
- **유니버스/필터 재현 한계**: 아카이브엔 tags가 없어 스포츠 키워드 필터를 완전 재현할 수 없다.
  (b)의 "신규 가상 진입"에는 비스포츠 확인을 question 텍스트 수동 샘플링으로 보완.
  또한 스윕은 "가장 오래된 활성 ~2100개"만 봐서 신규 단기 시장이 빠진다 — "진입이 없었다"는
  결론은 전략 탓이 아닐 수 있다.
- **7일 보존의 함정**: banana 자체 스냅샷으로 한 달치를 분석하려 하지 마라. 이미 지워졌다.

## 5. 표본 주의사항

- **상관 클러스터**: 같은 이벤트에서 파생된 시장들(예: 특정 선거의 후보별/주별 시장)은 사실상
  베팅 1개다. `market_slug`/`question` 접두어로 묶어 이벤트 단위 n을 다시 세고, 클러스터 내
  동반 손실은 1건으로 취급해 판단한다. 명목 n ≠ 유효 n.
- **코호트 분리 (banana 특유)**:
  - **cold-start 백로그 코호트**: 운영 시작 직후(및 DB 초기화·7일 정리 직후)에는 모든 시장이
    스냅샷 6개 미만이라 fallback 경로로 대량 진입한다. 이 초기 물량은 "85~97% + 방금 안 떨어짐"
    전략의 표본이지 momentum 전략의 표본이 아니다. `buy_timestamp` 첫 며칠을 분리 집계.
  - **entry_reason 코호트**: `golden_cross` vs `short_momentum_positive`는 사실상 다른 전략이다.
    §3.3으로 항상 분리. fallback 진입 중에서도 "유동성 경계를 갓 넘어 유니버스에 새로 편입된 시장"
    (스냅샷이 적은 또 다른 이유)이 섞여 있음에 유의.
- **threshold 청산 편향**: 0.97 근접 진입은 며칠 안에 threshold로 소액 익절되며 승률을 부풀린다.
  승률과 함께 반드시 평균 수익률·보유시간을 같이 본다 (§3.4·§3.5).
- **HOLDING 잔류 생존 편향**: COMPLETED만 집계하면 물려 있는 포지션의 미실현 손실이 빠진다.
  §3.6의 보유분 평가를 총 P&L에 반드시 합산한다.

## 6. 교정안 출력 형식 (AI가 반드시 채울 표)

| 파라미터 | 현행 | 제안 | 근거 수치 | 신뢰도(높음/중간/낮음) |
|---|---|---|---|---|
| POLYBOT_GOLDEN_CROSS_THRESHOLD | 0.02 | | §4(b)1 발화 수/P&L | |
| (fallback 최소 스냅샷 — 신규 노브 검토) | 사실상 6 | | §4(b)2 | |
| POLYBOT_BUY_THRESHOLD | 0.85 | | §3.4 + §4(b)3 | |
| (진입 상한 분리 — 신규 노브 검토) | =sell_threshold | | §3.4 TP 불가 버킷 | |
| POLYBOT_SELL_THRESHOLD | 0.97 | | §4(a) | |
| POLYBOT_TAKE_PROFIT | 0.07 | | §4(a) 격자 | |
| POLYBOT_STOP_LOSS | -0.10 | | §4(a) 격자 | |
| POLYBOT_DEAD_CROSS_THRESHOLD | -0.02 | | §3.2 dead_cross 성과 | |
| POLYBOT_MIN_LIQUIDITY | 50000 | | §3.7 | |
| POLYBOT_MAX_POSITIONS | -1 | | §3.6 동시 보유 피크 | |

**라운드 절차** (상세: `docs/retro/README.md` §3):
1. 1차 4주 운용 → 이 문서로 회고 → 위 표 산출
2. 제안값은 **Jenkins env만 교체**해서 2차 4주 재테스트 (코드 수정 불필요).
   cherry처럼 "기본 슬롯 + 제안값 슬롯" 병행 A/B가 이상적 — banana는 `--job` 분리로 DB가
   자동 격리되므로 두 job을 띄우면 된다 (A/B 비교 절차: `docs/ab-retro-playbook.md`).
   단, "신규 노브"(fallback 게이트, 진입 상한 분리)는 env가 없어 코드 수정이 필요하다.
3. 라운드 시작 시 이 문서 맨 아래 `## 운용 이력`에 날짜 + env 블록(키 제외)을 기록한다.
4. 2차 결과로 재회고 → 수렴하면 채택, 악화면 롤백/폐기.

## 7. 기준 정보

- 문서 생성: 2026-07-07, 기준 커밋 `7bcf83f` (t1 monorepo)
- 전략 문서: `golden-banana/STRATEGY.md`는 **없다**. 대신 `golden-banana/STRATEGY_ANALYSIS.md`
  (2026-07-03 코드 분석 — 파라미터 명세·약점 14개·개선안 10개)가 이 문서의 §1을 뒷받침한다.
- 코드 근거: `golden-banana/src/polybot/config.py`(env 파싱), `db/models.py`(스키마),
  `strategy/trader.py`·`strategy/momentum.py`(진입/청산), `db/repository.py`(7일 스냅샷 정리)
- 검증된 사실: status는 대문자 enum 이름으로 저장 (프로젝트 models로 실측, 2026-07-07)

## 운용 이력

| 날짜 | 라운드 | env 블록 (키 제외) |
|---|---|---|
| (기록 시작) | | |
