# A/B 테스트 회고 플레이북 (Polymarket 전략 봇)

한 달 운영 후 포스트모템 때 SQLite DB만으로 "무엇 때문에 손해가 났는지, 트랜잭션을 얼마나 맺었는지"를 분석하는 표준 절차. 대상 DB는 각 봇의 `data/<job>/trades.db` (시뮬레이션은 `trades_sim.db`).

## 로깅 계약 (date~papaya 10개 봇 공통)

모든 신규 봇의 `trades` 테이블은 아래를 기록한다:

- **공통 식별**: `strategy_name`(봇명), `mode`(`live`/`sim`), `status`(**대문자** enum 이름: `HOLDING`/`COMPLETED`/`EXPIRED`)
- **거래 수치**: `buy_price/buy_amount/buy_shares/buy_timestamp`, `sell_*` 대응, `realized_pnl`
- **판정 사유**: `entry_reason`, `exit_reason` (공통 어휘: `take_profit`/`stop_loss`/`trailing_stop`/`time_exit`/`max_holding`/`momentum_death`/`drift_death`/`retrace_target`/`resolved_unredeemed`)
- **시장 컨텍스트**: `liquidity_at_buy`, `volume_24h_at_buy`, `market_end_date`, `hours_until_resolution_at_buy`, `market_tags`
- **전략 고유 시그널 (수치 컬럼)**:
  - date: `ladder_band_at_buy`, `momentum_at_buy`
  - elderberry: `ref_price_at_buy`, `drop_at_buy`, `stabilization_range_at_buy`
  - fig: `yes_price_at_buy`, `yes_price_at_exit`
  - grape: `drift_at_buy`, `consistency_at_buy`, `vol_accel_at_buy`, `drift_at_exit`
  - honeydew: `deviation_at_buy`, `median_at_buy`, `deviation_at_exit`
  - lime: `jump_size_at_buy`, `base_price_at_buy`, `vol_mult_at_buy`
  - mango: `carry_yield_at_buy`, `momentum_6h_at_buy`, `carry_yield_at_exit`
  - nectarine: `rolling_min_at_buy`, `lookback_days_at_buy`, `hold_hours_at_exit`
  - orange: `base_price_at_buy`, `spike_peak_at_buy`, `spike_age_minutes_at_buy`, `vol_mult_at_buy`, `yes_price_at_exit`
  - papaya: `prior_yes_price_at_entry`, `yes_price_at_buy`, `stop_price_at_entry`,
    `prior_snapshot_id_at_entry`, `entry_snapshot_id`, `best_bid_at_buy`,
    `best_ask_at_buy`, `yes_price_at_exit`,
    `best_bid_at_exit`, `resolution_value`, `resolution_status`

`EXPIRED`(= `resolved_unredeemed`)는 해결됐지만 청산 못 한 포지션이다. resolution payout은
YES=1/NO=0이며 드문 ambiguous market은 0.5일 수 있다. resolution 관측, redeemable 상태,
실제 redemption transaction을 분리하고, 상환 전에는 realized cash P&L로 집계하지 않는다.

## 구 3봇 (apple/banana/cherry) 주의사항

코드 무수정 원칙에 따라 로깅 계약이 다르다. 회고 시 보정 필요:

- `entry_reason`/`exit_reason` 없음(apple) 또는 어휘 다름 — 분포 비교에서 제외하거나 별도 축으로.
- `EXPIRED` 없음 → 해결된 시장이 **영구 `HOLDING` 좀비**로 남아 손실이 `realized_pnl`에 안 잡힌다. 아래 쿼리 6번으로 좀비를 반드시 집계할 것 (특히 총손익 비교 시 왜곡 주의).
- `strategy_name`/`mode`/`volume_24h_at_buy` 없음 — 교차 쿼리에서 리터럴로 부여.

## 표준 쿼리

`sqlite3 golden-<fruit>/data/<job>/trades.db` 에서 실행. status는 대문자 리터럴.

```sql
-- 1. 승률 / 평균 손익
SELECT COUNT(*) AS closed_trades,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl,
       ROUND(SUM(realized_pnl), 4) AS total_pnl,
       ROUND(AVG(realized_pnl / NULLIF(buy_amount, 0)) * 100, 2) AS avg_return_pct
FROM trades WHERE status = 'COMPLETED';

-- 2. exit_reason 분포와 사유별 손익 (어떤 청산 규칙이 돈을 벌고 잃는가)
SELECT COALESCE(exit_reason, '(none)') AS exit_reason, COUNT(*) AS n,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl, ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY exit_reason ORDER BY total_pnl ASC;

-- 3. 손실 귀인: 진입가 밴드 × entry_reason
SELECT COALESCE(entry_reason, '(none)') AS entry_reason,
       CASE WHEN buy_price < 0.30 THEN '<30%' WHEN buy_price < 0.50 THEN '30-50%'
            WHEN buy_price < 0.80 THEN '50-80%' WHEN buy_price < 0.90 THEN '80-90%'
            ELSE '>=90%' END AS entry_band,
       COUNT(*) AS n, ROUND(SUM(realized_pnl), 4) AS total_pnl, ROUND(AVG(realized_pnl), 4) AS avg_pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY entry_reason, entry_band ORDER BY total_pnl ASC;

-- 4. 최대 손실 top10 + 진입 컨텍스트
SELECT id, substr(question, 1, 60) AS q, outcome, entry_reason, exit_reason,
       ROUND(buy_price, 3) AS buy, ROUND(sell_price, 3) AS sell, ROUND(realized_pnl, 4) AS pnl,
       ROUND(liquidity_at_buy, 0) AS liq, ROUND(hours_until_resolution_at_buy, 1) AS hrs_to_res, market_tags
FROM trades WHERE status = 'COMPLETED' AND realized_pnl < 0
ORDER BY realized_pnl ASC LIMIT 10;

-- 5. 보유시간 분석 (exit_reason별)
SELECT COALESCE(exit_reason, '(none)') AS exit_reason, COUNT(*) AS n,
       ROUND(AVG((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS avg_hold_hours,
       ROUND(MAX((julianday(sell_timestamp) - julianday(buy_timestamp)) * 24), 1) AS max_hold_hours,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl
FROM trades WHERE status = 'COMPLETED' AND buy_timestamp IS NOT NULL AND sell_timestamp IS NOT NULL
GROUP BY exit_reason ORDER BY avg_hold_hours DESC;

-- 6. 숨은 손실 감사: 미청산/만료 포지션에 잠긴 자본
--    (구 3봇은 EXPIRED가 없어 해결된 시장도 HOLDING에 섞여 있다 — already_resolved 열이 그 좀비 수)
SELECT status, COUNT(*) AS n, ROUND(SUM(buy_amount), 2) AS capital_locked_usdc,
       SUM(CASE WHEN market_end_date IS NOT NULL AND market_end_date < datetime('now') THEN 1 ELSE 0 END) AS already_resolved
FROM trades WHERE status IN ('HOLDING', 'PENDING_BUY', 'PENDING_SELL', 'EXPIRED')
GROUP BY status;

-- 7. 교차 봇 A/B 비교 (ATTACH로 병합; 경로·봇명은 상황에 맞게)
ATTACH DATABASE 'golden-cherry/data/default/trades.db' AS cherry;
ATTACH DATABASE 'golden-date/data/default/trades.db' AS date_bot;
SELECT bot, n, ROUND(total_pnl, 4) AS total_pnl, ROUND(100.0 * wins / n, 1) AS win_rate_pct
FROM (
  SELECT 'cherry' AS bot, COUNT(*) AS n, SUM(realized_pnl) AS total_pnl,
         SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
  FROM cherry.trades WHERE status = 'COMPLETED'
  UNION ALL
  SELECT 'date', COUNT(*), SUM(realized_pnl), SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
  FROM date_bot.trades WHERE status = 'COMPLETED'
);

-- 8. 카테고리(태그)별 손익 귀인
SELECT COALESCE(NULLIF(market_tags, ''), '(untagged)') AS tags, COUNT(*) AS n,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(SUM(realized_pnl), 4) AS total_pnl
FROM trades WHERE status = 'COMPLETED'
GROUP BY tags ORDER BY total_pnl ASC LIMIT 15;

-- 9. 거래 활동량: 주별 진입/청산/만료 건수 (트랜잭션 볼륨)
SELECT strftime('%Y-%W', buy_timestamp) AS week, COUNT(*) AS entries,
       SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS closed,
       SUM(CASE WHEN status = 'EXPIRED' THEN 1 ELSE 0 END) AS expired,
       ROUND(SUM(CASE WHEN status = 'COMPLETED' THEN realized_pnl ELSE 0 END), 4) AS week_pnl
FROM trades WHERE buy_timestamp IS NOT NULL
GROUP BY week ORDER BY week;

-- 10. 시그널 수치 vs 결과 (예: grape — 다른 봇은 해당 *_at_buy 컬럼으로 치환)
SELECT CASE WHEN drift_at_buy < 0.06 THEN 'drift 4-6%p' ELSE 'drift >=6%p' END AS drift_bucket,
       CASE WHEN vol_accel_at_buy < 3 THEN 'vol x1.2-3' ELSE 'vol >=x3' END AS vol_bucket,
       COUNT(*) AS n,
       ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
       ROUND(AVG(realized_pnl), 4) AS avg_pnl
FROM trades WHERE status = 'COMPLETED' AND drift_at_buy IS NOT NULL
GROUP BY drift_bucket, vol_bucket;
```

## 회고 판단 기준 (제안)

- 표본: 전략당 최소 4주 + 30건 이상 청산 완료.
- 기각 신호: `stop_loss` 비중 40%+ (진입 조건 부실), `EXPIRED`/좀비 다수 (청산 로직 부실), 특정 태그에서만 손실 집중 (카테고리 필터 필요).
- 채택 신호: 승률과 avg_return이 시뮬레이션→소액 실전에서 유지, exit_reason 분포가 설계 의도와 일치 (예: mango는 take_profit 위주, nectarine은 max_holding 위주가 정상).
- 채택 후 베리에이션(A-1/A-2)은 env만 바꿔 별도 `--job`으로 병행 — 쿼리 7의 ATTACH 패턴으로 비교.
- 실제 성과는 `order_fills.status='CONFIRMED'`의 partial size/price와 fee/liquidity role로
  재계산한다. `realized_pnl`, GTC 접수, midpoint는 actual fill 증거가 아니다. 월 1회
  Polymarket 실계정 잔고(daily-report) 및 resolution/redeem evidence와 대사한다.
