# Golden Blueberry 전략 계약 — Closing Surge

## 1. Hypothesis

해결 또는 경기 종료가 가까운 표준 이진 시장에서 YES가 처음으로 높은 확률 band에 급등하면,
새 정보의 분산 반영과 우세 결과로의 수렴이 짧게 더 이어질 수 있다. 다만 barrier 배치 자체는
edge를 만들지 않으므로 **최초 교차의 순간 상승폭**이 비용 이후 선별력을 갖는지를 A/B한다.

이 문서는 수익성 주장이 아니라 사전 등록이다. 가장 강한 competing explanation은 “0.85~0.93은
이미 잘 calibrated되어 있고, 급등은 새 정보를 즉시 반영했으므로 이후 기대수익은 fee/spread만큼
음수”라는 것이다. A/B 두 arm 모두 confirmed-fill 기대값이 0 이하이면 가설을 폐기한다.

## 2. 과거 자료가 말하는 것과 말하지 못하는 것

- `golden-cherry/data/default/trades.db` 사본은 671건이지만 COMPLETED 247,
  HOLDING 246, UNFILLED 102, QUARANTINED 76이고 legacy `trades.realized_pnl`은 실제
  체결 성과로 인정할 수 없다.
- 주문액은 3월 $10에서 4~5월 $1,000, 6~7월 최대 $8,000으로 급격히 커졌다. 대형 주문은
  유동성 대비 비중과 confirmed-fill coverage를 악화시켰다.
- 이 사본만으로 “스포츠가 더 수익이었다” 또는 “소액 구간 월 10%”를 재현하지 못했다.
  따라서 스포츠를 제외하지는 않되 category 승자 가설로도 쓰지 않는다.
- Queen archive 56,354 snapshot의 5일 구간에서는 first crossing 자체는 관측됐지만
  2%p/5%p 급등과 book/metadata gate를 모두 만족한 표본이 희소했다. 1주는 운영 health,
  30일도 최소 표본을 못 채우면 연장해야 한다.

원본 checksum과 재현 수치는 `research/2026-08-04-origin-and-preregistration.md`에 고정한다.

## 3. Universe

모든 조건을 만족해야 archive/entry evidence로 인정한다.

1. Gamma의 active, not closed, orderbook-enabled, accepting-orders market.
2. outcomes가 정확히 `['Yes','No']`, outcome price와 token ID가 각각 2개이고 token ID가
   서로 다르다.
3. `negRisk is False`가 명시돼야 한다. missing/unknown은 허용하지 않는다.
4. 기본 `excluded_categories=[]`로 스포츠를 포함한다. category 제외는 Gamma tag의 exact
   match이며 question keyword 추정은 쓰지 않는다.
5. archive는 YES `>=0.75`, scheduled/pregame 잔여 `<=168h`, metadata liquidity `>=1k`인
   더 넓은 우주를 60일 보존한다. 이 archive는 거래 gate가 아니다.

## 4. Entry

조건 `c`의 현재 run persisted snapshot을 `S_t`, 바로 이전 persisted snapshot을 `S_{t-1}`라 한다.

```text
entry(c) = strict_standard_binary_yes(c)
         AND no_historical_snapshot_at_or_above_0.85_before(S_t)
         AND S_{t-1}.yes < 0.85
         AND 0.85 <= S_t.yes <= 0.93
         AND S_t.yes - S_{t-1}.yes >= min_surge
         AND 0m < S_t.time - S_{t-1}.time <= 15m
         AND eligible_clock(c)
         AND liquidity >= effective_min_liquidity
         AND volume24h >= effective_min_volume24h
         AND event_id exists
```

`min_surge`만 A=`0.02`, B=`0.05`다. 최초 0.85 crossing이 상승폭·시간·유동성·거래량·event
gate에서 거절돼도 one-shot을 소비한다. 나중 dip/re-cross를 “첫 교차”로 재분류하지 않는다.
거절은 `entry_signal_decisions`에 기록하며 cooldown trade를 만들지는 않는다.

### Clock

- 비스포츠: `endDate`까지 `(0h,72h]`.
- 스포츠 경기 전: `gameStartTime`까지 `(0h,72h]`.
- 스포츠 경기 중: upstream이 여전히 tradable이고 kickoff 후 `<=360분`; 72h 창은 적용하지 않는다.
- 스포츠인데 `gameStartTime`이 없으면 기본 설정에서는 `endDate`로 fallback하고 그 사실을
  `entry_time_reference`에 남긴다.
- 이미 지난 scheduled deadline, missing/invalid clock은 fail closed다.

### Fresh execution gate

scanner 후보를 곧바로 주문하지 않는다. 주문 직전에 다시 clock, midpoint, crossing을 검사하고
동일 CLOB depth snapshot에서 아래를 모두 확인한다.

- best ask `<=0.93`
- spread `<=0.02`
- depth limit은 best ask부터 최대 `+0.01` 이내
- 해당 limit까지 ask depth `>= 요청 shares × 1.20`
- order shares `>=5.0+0.1`
- event당 open 1건, 전체 10건, open notional `$50`, cycle 신규 1건

BUY limit price는 depth limit을 쓰고 양 arm의 tick rounding은 `nearest`로 고정한다.

## 5. Exit와 resolution

미해결 position의 우선순위는 다음과 같다.

1. `current_yes >=0.97`이고 **fresh best bid도 `>=0.97`**이면 `take_profit` SELL.
2. 그렇지 않고 `current_yes <=0.78`이면 `absolute_stop` SELL.
3. 그 외는 보유.

trailing stop과 pre-resolution time exit은 없다. poll 사이 jump 때문에 손절 fill은 0.78보다
나쁠 수 있으며, 이 tail은 stop 숫자가 아니라 $5 size와 exposure cap으로 제한한다.

Gamma resolution 결과, redeemable 상태, 실제 redeem transaction, CLOB SELL fill은 서로 다른
증거다. proven payout과 exact confirmed BUY가 있으면 `settlement_pnl_assumption`을 기록하지만
synthetic 1.00 SELL이나 `realized_pnl`을 만들지 않는다. 실제 redeem ingestion은 현재 범위 밖이다.

## 6. A/B preregistration

| 계약 | Arm A | Arm B |
|---|---:|---:|
| `min_surge` | `0.02` | `0.05` |
| runtime job 예시 | `blueberry-live-a-2pp` | `blueberry-live-b-5pp` |
| account capital | $150 | $150 |
| order | $5 | $5 |
| 나머지 config/source/cadence | 동일 | 동일 |

두 arm은 서로 다른 wallet/account, Jenkins job, runtime job, SQLite를 사용한다. 시작일과 5분
cadence를 맞춘다. 동일 event와 crossing time window를 paired/cluster 단위로 보며 market row를
독립 n으로 세지 않는다.

모노레포 Git commit은 provenance이지 cohort 기준이 아니다. 비교 cohort는:

```text
config_hash × strategy_source_digest × mode × job_name
```

`strategy_source_digest`는 Blueberry runtime/config/lock/backtest/analyzer와 shared observability
bytes의 SHA-256이다. unrelated monorepo commit은 cohort를 쪼개지 않지만, 전략 동작을 바꾸는
source change는 새 cohort가 된다.

## 7. Falsification과 review gates

### 1주 health checkpoint

수익 비교와 tuning을 금지한다. 두 arm 모두 다음을 확인한다.

- expected 5분 cadence와 성공 run coverage
- complete Gamma cursor sweep과 archive/snapshot lineage
- `entry_signal_decisions`의 crossing/rejection 기록
- order submission → status → exact fill reconciliation
- DB online backup, SHA-256, restore/integrity check
- account/signature type/DB namespace 격리와 kill-switch 상태

### 30일 primary review

- arm당 exact confirmed BUY+SELL round trip이 20건 미만이면 `INCONCLUSIVE`로 연장한다.
- 모든 closed trade의 fee coverage가 완전하지 않으면 순성과 비교를 중단한다.
- primary endpoint: event-clustered confirmed net P&L/position과 win rate.
- secondary: signal→submission, confirmed BUY, round-trip coverage; slippage; hold time;
  스포츠/비스포츠는 탐색적 slice일 뿐 승자 선택에 쓰지 않는다.
- rejected first crossings는 threshold 반사실과 selection rate를 측정한다.
- 한 arm이 우연히 양수라는 이유로 자동 승자를 고르지 않는다. cluster uncertainty, 양쪽
  시간 half의 부호, worst loss, unresolved exposure를 함께 검토한다.

둘 다 fee 이후 음수거나 drawdown kill switch가 발동하면 `STOP/REDESIGN`한다. 표본이 부족하면
threshold를 완화하지 않고 기간을 연장한다. A/B 중간 결과를 보고 arm을 바꾸거나 다른 knob를
수정하면 새 실험으로 다시 사전 등록한다.

## 8. Risk와 scaling

- 초기 hard cap `$5`; `$1`은 high-probability band에서 CLOB 5-share 최소를 충족하지 못한다.
- arm당 capital `$150`, max positions 10, event당 1, open notional `$50`, 신규 1/cycle.
- 경제손익=`confirmed realized channel + resolution settlement assumption channel`이 `-$30`이면
  신규 BUY를 코드가 차단하고 exit/reconciliation은 계속한다. 성과 보고에서는 두 채널을 합치지
  않는다.
- liquidity와 volume24h gate는 각각 `max($10k, order / 0.0005)`다. hard cap을 회고 후
  코드로 올리면 시장 gate도 자동 증가한다.
- scale은 `$5 → $10 → $20`처럼 최대 2배씩 별도 runtime job/DB cohort에서 진행한다. 이전
  cohort의 손익으로 새 kill switch를 상쇄하지 않는다. confirmed fill/fee,
  slippage, worst loss, drawdown, position utilization을 다시 통과하기 전 다음 단계로 가지 않는다.

## 9. Evidence contract

실제 성과는 `order_fills.status='CONFIRMED'`의 exact order size, VWAP, fee만 인정한다.
`accepted`, `live`, order ID, requested price×size, midpoint, simulation result는 actual fill이 아니다.
partial fill은 exact 합계로 계산하고 BUY/SELL size mismatch는 terminal P&L로 만들지 않는다.

필수 provenance:

- `strategy_configs`, `run_audits`
- `market_sweeps`, `market_sweep_memberships`, `market_catalog`, `market_snapshots`
- `entry_signal_decisions`
- `order_submissions`, `order_status_events`, `order_fills`
- live DB online backup SHA-256와 sanitized Jenkins log

관측성 기록, incomplete sweep, malformed book, missing lineage, unresolved submission evidence는
fail closed한다. DB/log는 git에 넣지 않고 workspace 밖 durable storage에 보관한다.
