# golden-papaya 회고(포스트모템) 가이드

> **필수 선행 계약**: [Evidence Contract](EVIDENCE_CONTRACT.md)를 먼저 읽는다.
> `REVIEW_START`/`REVIEW_END`를 UTC 날짜로 고정하고 `polybot-retro audit --strict`의
> `CRITICAL`/`HIGH` issue를 해결하기 전에는 tuning·증액·promotion을 제안하지 않는다.
> 실제 주문 결과는 `order_fills.status='CONFIRMED'`만 사용하며 resolution과 redeem은
> 별도 evidence로 대사한다.

전략: **Final Five** — strict standard binary YES가 해결까지 0시간 초과 72시간 이하로
남았을 때 처음 0.95를 상향 돌파하고 현재가가 0.95–0.97일 때 $5 매수한다. 사전 해결
청산은 YES 절대가 0.90 이하 stop뿐이며, 그 외에는 resolution/redeem까지 보유한다.

## 0. 복붙용 프롬프트

```text
docs/retro/EVIDENCE_CONTRACT.md와 docs/retro/golden-papaya.md를 순서대로 읽어라.

REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>

1) papaya live DB와 자체 archive DB를 찾고 checksum을 보존한다.
2) polybot-retro audit --strict를 먼저 실행한다. CRITICAL/HIGH면 수치 변경 대신
   evidence 복구 계획만 제안한다.
3) config_hash × git_commit × mode × job_name cohort를 분리한다.
4) strict binary, previous_yes < .95 <= current_yes, (0h,72h], event cap lineage를 검증한다.
5) actual execution은 CONFIRMED fill size/price/fee만 사용한다. resolution payout
   (YES=1/NO=0/rare ambiguous=0.5), redeemable,
   redemption transaction은 SELL fill과 분리한다.
6) papaya 자체 archive의 cursor completion과 Jenkins schedule/run manifest에서 산출한
   expected cadence 대비 actual bucket/run gap, catalog/event coverage를 검증한다.
7) event-cluster 유효 표본으로 baseline과 §4 grid를 비교한다.
8) §6 형식으로 KEEP/CHANGE/STOP을 제안한다.

Jenkins export는 secret/address를 제거한 legacy cross-check로만 사용한다.
```

## 1. 사전 등록값과 실패 가설

| 항목 | env | baseline |
|---|---|---:|
| 주문 금액 | `POLYBOT_BUY_AMOUNT` | $5 |
| 최소 유동성 | `POLYBOT_MIN_LIQUIDITY` | $10,000 |
| 최소 volume24h | `POLYBOT_MIN_VOLUME_24H` | $2,000 |
| 최대 연속 snapshot 간격 | `POLYBOT_MAX_SNAPSHOT_GAP_MINUTES` | 30m |
| 첫 교차/진입 하한 | `POLYBOT_ENTRY_PROB_MIN` | 0.95 |
| 진입 상한 | `POLYBOT_ENTRY_PROB_MAX` | 0.97 |
| 절대 stop | `POLYBOT_STOP_PRICE` | 0.90 |
| 잔여시간 | `POLYBOT_ENTRY_HOURS_MIN/MAX` | 0h 초과–72h 이하 |
| 최대 포지션 | `POLYBOT_MAX_POSITIONS` | 20 |
| event cap | `POLYBOT_MAX_EVENT_POSITIONS` | 1 |
| archive envelope | `POLYBOT_ARCHIVE_PROB_MIN/HOURS_MAX` | 0.80 / 168h |
| 보존 | `POLYBOT_SNAPSHOT_RETENTION_DAYS` | 60d |

first observed crossing은 현재 sweep에서 commit된 양의 snapshot ID, 직전 persisted snapshot,
기본 `0 < gap <= 30분`이 모두 증명되고 직전 YES가 0.95 미만이며 현재 YES가 0.95 이상일
때만 성립한다. 보존된 더 이른 snapshot에 YES 0.95 이상이 하나라도 있으면 re-crossing이다.
최초 발견가가 이미 0.95 이상이거나 0.97을 gap-up한 시장은 반사실에서도 실제 진입으로
취급하지 않는다. sweep/run gap이 있으면 실제 연속시간 교차는 interval-censored이므로
“시장 최초 crossing”으로 단정하지 않는다.

one-shot은 첫 threshold snapshot이 commit될 때 소진한다. 그 crossing이 liquidity, volume,
window, portfolio/event cap, fresh ask 또는 minimum-order gate에서 주문으로 이어지지 않아도
이후 dip/re-cross를 다시 진입시키지 않는다. baseline과 모든 counterfactual grid에 같은
ordering을 적용한다.

경쟁 설명은 다음과 같다.

- 0.95는 실제 tail risk를 정확히 반영하므로 비용 전 5.26% upside가 특별한 edge가 아니다.
- 얇은 호가/stale midpoint가 교차를 만들며 confirmed fill은 불리한 후보에 집중된다.
- 실패 시장의 stop 미체결과 unresolved/redeem 지연이 성공 시장보다 관측 누락되기 쉽다.
- 같은 event의 여러 파생 시장을 독립 승리로 세어 n을 부풀린다.

## 2. DB와 archive

```bash
find /Users/jongwoopark/.jenkins/workspace \
  -path "*golden-papaya/data*" -name "trades.db" 2>/dev/null
find /Users/jongwoopark/.jenkins/workspace \
  -path "*golden-papaya/data*" -name "trades_sim.db" 2>/dev/null
```

운영 entry universe는 $10,000/$2,000이지만 첫 crossing 이전 history와 낮은 gate의
counterfactual까지 보존해야 한다. 따라서 주 source는 papaya 자체 archive다. envelope는
YES ≥ 0.80, 잔여 ≤168h, 유동성 ≥$1,000, volume24h ≥$0이고 최소 60일을 보존한다.
counterfactual이나 운영 entry filter를 바꿔도 archive request baseline은 $1,000/$0으로
유지한다.

회고마다 다음을 수치화한다.

- `market_sweeps` cursor complete와 membership count/digest
- Jenkins schedule/run manifest에서 산출한 expected cadence 대비 actual bucket/sweep/run gap
- `market_snapshots`와 `market_catalog` condition/event join coverage
- 거래별 직전 snapshot lineage와 first observed crossing 재현률
- strict binary/negRisk exclusion 및 event cap 준수율

중앙 archive는 교집합 시장의 독립 대조용으로만 사용한다. papaya archive에 없던 저유동성
시장을 중앙 archive로 소급 생성하지 않는다.

## 3. Evidence 진단

```bash
export REVIEW_START=<YYYY-MM-DD>
export REVIEW_END=<YYYY-MM-DD>
export REVIEW_DAYS=30
export BOT_DB=/absolute/path/to/golden-papaya/data/<job>/trades.db
export RETRO_OUTPUT="$HOME/polybot-retro/papaya-$REVIEW_END"

uv run --project polybot-observability polybot-retro audit \
  --db "$BOT_DB" --days "$REVIEW_DAYS" --as-of "$REVIEW_END" \
  --output-dir "$RETRO_OUTPUT" --strict
```

아래 SQL은 상태 진단용이다. 기간은 항상
`[REVIEW_START 00:00Z, REVIEW_END + 1 day 00:00Z)` half-open으로 제한한다.

```sql
-- 전략 상태. 이것은 actual P&L이 아니다.
SELECT status, mode, exit_reason, COUNT(*) AS n
FROM trades
WHERE created_at >= :review_start
  AND created_at < :review_end_exclusive
GROUP BY status, mode, exit_reason;

-- entry contract drift 진단. 실제 컬럼명은 배포 schema를 audit bundle과 대조한다.
SELECT id, condition_id, event_id, entry_snapshot_id, buy_probability,
       prior_yes_price_at_entry, hours_until_resolution_at_buy,
       liquidity_at_buy, volume_24h_at_buy
FROM trades
WHERE mode = 'live'
  AND created_at >= :review_start
  AND created_at < :review_end_exclusive
  AND NOT (
    prior_yes_price_at_entry < 0.95
    AND buy_probability BETWEEN 0.95 AND 0.97
    AND hours_until_resolution_at_buy > 0
    AND hours_until_resolution_at_buy <= 72
  );
```

실제 성과 계산은 BUY/SELL order ID를 execution ledger에 연결해 CONFIRMED partial fill을
size-weighted price로 합치고 fee/role을 반영한다. DB trade의 midpoint, `live`/`accepted`,
order ID 존재, `realized_pnl`만으로 체결을 추정하지 않는다.

terminal 분류는 최소 다음처럼 나눈다.

| 상태 | 실제 성과 사용 |
|---|---|
| confirmed pre-resolution SELL | fill size/price/fee로 사용 |
| resolution 확인, 미상환 | payout 1/0/드문 0.5 outcome만 사용; cash P&L 미확정 |
| redeemable | 청구 가능 상태; 실현 cash 아님 |
| confirmed redemption | payout transaction/금액으로 사용 |
| pending/unknown intent | 제외하고 strict issue |

## 4. Counterfactual grid

동일 archive와 당시 알려진 값만 사용해 다음을 한 축씩 비교한다.

| 축 | grid |
|---|---|
| crossing/entry lower | 0.94 / **0.95** / 0.96 |
| entry upper | 0.96 / **0.97** / 0.98 |
| absolute stop | 0.85 / **0.90** / 0.93 |
| min liquidity | 1k / 5k / **10k** / 20k |
| min volume24h | 0 / 500 / **2k** / 5k |
| hours max | 24 / 48 / **72** |

동일한 immutable export를 아래처럼 실행하면 여섯 축의 1,296개 조합과 월별 UTC entry
cohort가 SHA-256 manifest와 함께 생성된다.

```bash
uv run --project golden-papaya python golden-papaya/scripts/backtest.py \
  /absolute/path/papaya-research.csv \
  --output-dir "$RETRO_OUTPUT/offline-grid" \
  --review-start "$REVIEW_START" \
  --review-end "$REVIEW_END"
```

CSV는 `outcomes`, `token_ids`, `yes_token_id`, `neg_risk`를 포함해 strict standard binary
YES universe를 행마다 증명해야 한다. 스크립트의 midpoint와 observed bid/ask 결과는
research-only 가상 체결이다. `confirmed_fill_pnl_per_share`는 기본적으로 `null`이며 exact
order ID의 `CONFIRMED` fill/fee를 별도 join하기 전에는 채우거나 추정하지 않는다.

의사코드:

```text
for each cursor-complete snapshot in chronological order:
  require a positive current snapshot id persisted by this sweep
  require 0 < current_at - previous_at <= max_snapshot_gap_minutes
  if any earlier snapshot YES >= entry_lower: classify as re-crossing and reject
  if previous YES < entry_lower <= current YES: consume the one-shot now
  require strict standard binary and event exposure == 0
  require liquidity >= min_liquidity and volume24h >= min_volume24h
  require previous valid YES < entry_lower <= current YES <= entry_upper
  require 0h < hours_left <= hours_max
  refresh best ask; reject missing ask or executable ask > entry_upper
  never restore a consumed crossing after any downstream rejection
  model BUY at fresh ask, then confirmed-fill sensitivity
  before resolution, trigger stop when YES signal/midpoint <= stop_price
  evaluate executable SELL at fresh best bid, then confirmed fill
  if stop not fillable, retain exposure and mark execution shortfall
  at resolution, use payout 1/0/rare 0.5 then separate redemption evidence
cluster all results by event_id before uncertainty calculation
```

midpoint 체결, best bid/ask 체결, 실제 confirmed fill의 세 결과를 분리한다. stop trigger와
confirmed SELL 사이의 시간, fill ratio, worst/median slippage를 반드시 보고한다.

## 5. Falsification 기준

판단은 4주와 terminal event 유효 n 30을 모두 충족한 뒤 한다. 다음 중 하나면 `STOP` 또는
새 사전등록이 우선이다.

- confirmed execution, fee, resolution/redeem을 반영한 event-cluster 비용 후 EV ≤ 0
- 0.90 stop trigger가 얇은 호가에서 반복적으로 미체결되거나 손실한도를 재현하지 못함
- strict binary/first-observed-crossing lineage coverage가 100%가 아님
- leave-one-event/category-out에서 edge가 사라짐
- terminal loss 한 건이 정상 승리 평균 약 19건 이상을 지속적으로 상쇄

표본 부족 또는 evidence gap은 `KEEP` 근거가 아니다. 다음 달까지 baseline 계측을 계속하거나
계측을 복구한다.

## 6. 최종 보고 형식

| 구분 | 결과 |
|---|---|
| 기간/DB checksum/audit bundle | |
| config hash × commit × mode × job | |
| 명목 시장 n / event 유효 n / terminal n | |
| archive sweep/bucket/catalog/lineage coverage | |
| confirmed BUY/SELL fill 및 fee/role coverage | |
| resolution / redeemable / redeemed / unresolved | |
| gross / fee / spread-adjusted net EV | |
| stop trigger→fill률·지연·슬리피지 | |
| 경쟁 설명·leave-one-event-out | |
| 결론 | `KEEP` / `CHANGE` / `STOP` |
| confidence / rollback 조건 | |

`CHANGE`는 한 번에 하나의 knob만 새 config hash cohort에 적용한다. strict gate 실패 시에는
수치 변경 대신 owner, 복구 절차, 재검증 시점을 기록한다.
