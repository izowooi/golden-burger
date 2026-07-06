# nectarine(golden-fox) max_positions 튜닝 회고 가이드

> **실행 시점: 2026년 8월 초** (운영 시작 2026-07-06 기준 약 한 달 뒤)
> 이 문서 하나만 AI에게 주고 "이 문서대로 회고를 실행하고 조정안을 내줘"라고 요청하면 된다.

## 1. 배경 — 무엇을 결정하려는가

- 계정 **golden-fox** (funder `0x393d3927b1095D5B734794def9b3BB877fd28fAe`, 초기 자본 $3,000)에서
  **golden-nectarine** (Bottom Fisher: YES 3~50%, 20일 롤링 최저가 이하 매수, 120h 달력 청산) 전략을 테스트 중이다.
- Jenkins job: `polybot-fox`, 5분 주기, 시작 설정:
  ```bash
  export POLYMARKET_SIGNATURE_TYPE=3   # 2026+ 신규 계정(POLY_1271) 필수
  export POLYBOT_BUY_AMOUNT=10         # 포지션당 $10
  export POLYBOT_MAX_POSITIONS=150     # = 현금 $3,000 × 50% ÷ $10
  ```
- `max_positions=150`은 "평상시엔 안 걸리고(첫날 백로그 128개 < 150) 시장 전반 급락일에만
  작동하는 크래시 브레이크"로 산정했다. **이 수치가 적절했는지를 한 달 데이터로 판정하는 것**이 이 회고의 목적이다.

## 2. 어떤 데이터가 쌓여 있나

DB 위치 (맥미니): `/Users/jongwoopark/.jenkins/workspace/polybot-fox/golden-nectarine/data/default/trades.db` (SQLite)

| 테이블 | 내용 | 용도 |
|---|---|---|
| `cycle_stats` | 사이클(5분)마다 1행: `buy_candidates`, `holdings_before/after`, `bought`, `cap_skips`(상한 때문에 못 산 수), `cooldown_skips`, `failed_buys`, **당시 `max_positions`·`buy_amount_usdc` 설정값** | 상한이 언제/얼마나 걸렸는지, 보유 궤적 |
| `capped_candidates` | 상한이 걸러낸 후보의 **스킵 시점 가격**(`yes_price` = 가상 매수가), 시장당 24h 1회 dedup | 반사실(counterfactual) 수익률 계산 |
| `trades` | 실제 체결 내역 (`buy_price`, `sell_price`, `status`, `exit_reason` 등) | 실제 수익률 |
| `market_snapshots` | 전 시장 YES 가격 5분 스냅샷, **60일 보존** | 반사실의 "+120h 뒤 가격" 소스 |

계측 도입 커밋: `26a2d2f`. (참고: 백필 범위 수정 `43512b6`, signature_type env `9351906`/`e72ae73`)

## 3. 회고 실행 — SQL 3개

```bash
sqlite3 /Users/jongwoopark/.jenkins/workspace/polybot-fox/golden-nectarine/data/default/trades.db
```

**A. 일별 보유 궤적과 상한 히트 빈도**
```sql
SELECT date(ts) AS day, MAX(holdings_after) AS peak, MAX(max_positions) AS cap,
       SUM(bought) AS bought, SUM(cap_skips) AS cap_skips
FROM cycle_stats GROUP BY day ORDER BY day;
```

**B. 상한이 걸러낸 진입의 반사실 수익률** (+120h 달력 청산 가정)
```sql
SELECT c.question, c.ts, c.yes_price,
       (SELECT s.probability FROM market_snapshots s
         WHERE s.condition_id = c.condition_id
           AND s.timestamp >= datetime(c.ts, '+120 hours')
         ORDER BY s.timestamp LIMIT 1) AS price_after_120h
FROM capped_candidates c;
```
→ 반사실 수익률 = `price_after_120h / yes_price - 1` 의 평균/분포.

**C. 실제 체결분의 실현 수익률** (비교 기준)
```sql
SELECT COUNT(*) AS n,
       AVG((sell_price - buy_price) / buy_price) AS avg_return,
       SUM(CASE WHEN sell_price > buy_price THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate
FROM trades WHERE status = 'completed' AND buy_price > 0;
```

## 4. 판정 규칙

| 관찰 | 해석 | 조치 |
|---|---|---|
| 한 달 내내 `cap_skips = 0` | 상한이 한 번도 안 걸림 | 그대로 두거나 낮춰도 무방. 조정 불요 |
| 상한이 자주 걸림 + **반사실 수익률 ≥ 실제 수익률** | 상한이 알파를 버리고 있음 | `MAX_POSITIONS` 상향 또는 자본 증액 (산식: 현금×배치비율÷BUY_AMOUNT) |
| 급락일에만 걸림 + **반사실 수익률이 실제보다 나쁨** | 상한이 떨어지는 칼날을 정확히 차단 | 유지. 오히려 하향 검토 가능 |

추가 체크:
- **유효 표본**: 첫날(7/6~7) 백로그 코호트는 이후의 "신선한 신저가" 코호트와 분리해서 볼 것
  (`trades.buy_timestamp` 기준). 상관 클러스터("대선 출마 선언" 계열, 코인 가격 사다리 계열)는
  이벤트 단위로 묶어 세야 한다 — 명목 n ≠ 유효 n.
- `cycle_stats.failed_buys`가 지속적으로 크면 주문 인프라 문제(잔고/최소수량/서명) — 상한과 무관하므로 먼저 해결.

## 5. 조정 방법

코드 수정 불필요. Jenkins `polybot-fox` job의 env만 바꾸면 되고,
`cycle_stats`에 당시 설정값이 함께 기록되므로 조정 이력은 자동 추적된다.

## 6. AI에게 시킬 때 예시 프롬프트

> docs/nectarine-max-positions-retro.md 를 읽고, golden-nectarine의 trades.db에서
> 3장의 SQL A/B/C를 실행한 뒤 4장의 판정 규칙대로 max_positions 조정안을 근거와 함께 제시해줘.
> 첫날 백로그 코호트와 이후 코호트를 분리하고, 상관 클러스터를 이벤트 단위로 묶어서 판단해줘.
