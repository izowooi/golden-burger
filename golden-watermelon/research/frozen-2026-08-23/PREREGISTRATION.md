# Frozen preregistration — In-Play Match Winner

- Frozen at: `2026-08-22T15:30:00Z` (`2026-08-23 00:30 KST`).
- Data contract: `sports-inplay-match-winner-v1`.
- Mode: accountless displayed-book simulation only.
- Entry window: `[2026-08-22T15:30:00Z, 2026-09-05T15:30:00Z)`.
- Calibration/confirmation split: `2026-08-29T15:30:00Z`.
- Resolution follow-up end: `2026-09-19T15:30:00Z`.
- First operational health review: `2026-08-24T10:00:00Z`
  (`2026-08-24 19:00 KST`).

## Primary question

경기 시작 후 whole-match winner의 exact displayed $5 ask VWAP가 X 이상이 되었을 때,
resolution까지 보유하거나 Y 이하 best bid에서 실제 displayed depth로 stop하는 정책이
fee 후 양의 event-equal counterfactual ROI를 만드는가?

## Frozen universe

Gamma `/markets/keyset` cursor 전체를 다음 request envelope로 수집한다.

- `closed=false`
- `sports_market_types=moneyline`
- receipt time 기준 `end_date_min=-24h`, `end_date_max=+24h`
- page size 100, max 10 pages
- liquidity/volume filter 없음

한 page라도 실패하거나 terminal cursor에 도달하지 못하면 그 cycle 전체를 무효화한다.

client eligibility는 다음 conjunction이다.

1. exactly one event relation, exactly two event teams
2. `sportsMarketType=moneyline` exactly
3. active, not closed, accepting orders, order book enabled
4. `gameStartTime <= receipt time`
5. event `ended != true`, explicit `live != false`
6. exactly two labels/tokens/prices
7. non-negRisk: two labels each exactly match a different event team
8. negRisk: team-name `groupItemTitle` and `Yes` outcome only

`child_moneyline`, Draw/Tie, negRisk No, map/game/set winner, score, spread, total, goal,
foul/corner/player prop은 제외한다. Gamma endDate를 실제 경기 종료시각으로 사용하지 않는다.

## Frozen treatment

두 DB는 동일 모집단과 동일 grid를 수집하며 cadence만 다르다.

| job | arm | nominal cadence |
|---|---|---:|
| `watermelon-white-1m` | `FAST_1M` | 1 minute |
| `watermelon-grey-5m` | `CONTROL_5M` | 5 minutes |

paired unit은 `condition_id × token_id × entry_threshold`이다. 두 DB의 같은 unit을 두
독립 거래로 세지 않는다. cadence가 entry crossing 자체를 다르게 관측할 수 있으므로 entry
time/VWAP delta도 treatment outcome으로 보존한다.

## Frozen entry and exits

- Entry thresholds X: `0.95, 0.96, 0.97, 0.98, 0.99`.
- Notional: exact displayed ask depth `$5`.
- Entry provenance: `FIRST_FULL_DEPTH_ABOVE` 또는 `UPWARD_CROSS`.
- Primary exit: `HOLD_TO_RESOLUTION`.
- Stop Y: `STOP_0.95`, `STOP_0.93`, `STOP_0.90`, `STOP_0.85`, `STOP_0.80`,
  `STOP_0.70`.
- Early take-profit: none.

stop trigger는 best bid이고 exit는 original shares에 대한 full-depth bid VWAP다. gap,
partial fill, remaining shares, retry, fee를 별도 evidence로 저장한다. 각 X/Y는 같은
displayed liquidity를 실제로 경쟁하지 않는 counterfactual이다.

## Cost and resolution

entry와 stop 모두 source fee schedule을 사용한다. fee-enabled인데 rate가 없으면 보수적으로
`0.05`를 사용하고 그 fallback을 저장한다. maker rebate와 fill probability는 가정하지 않는다.

resolution은 public CLOB market이 closed이고 exactly two tokens 중 `winner=true`가 하나일
때만 인정한다. unresolved/ambiguous/delisted episode는 임의 0/1로 채우지 않는다.

## Health gate

첫 review에서는 ROI나 X/Y를 선택하지 않는다.

- SQLite quick check/data contract/config/source digest 정상
- cursor completeness 100%
- FAST cadence coverage ≥95%, CONTROL ≥90%
- eligible outcome CLOB attempt coverage ≥95%
- CRITICAL/HIGH DQ issue 0
- external free ≥50GiB, used ratio <90%
- FAST natural-build runtime p95 <45 seconds

health 실패 시 parameter를 바꾸지 않고 instrumentation/runtime을 고친다. universe를 줄이기
위해 volume/liquidity gate를 사후 추가하지 않는다.

## Outcome falsification gate

entry/follow-up 종료 후 다음을 모두 만족한 X/Y만 후속 confirmatory 후보다.

1. confirmation 구간에서 X별 resolved unique events ≥100
2. resolution coverage ≥90%
3. entry exact $5 depth evidence 100%
4. fee 후 event-cluster bootstrap 95% ROI lower bound >0
5. calibration과 confirmation 모두 point estimate >0
6. cadence-paired episode coverage ≥80%

FAST가 CONTROL보다 stop gap/tail loss를 줄이지 못하면 1분 cadence 우월 가설을 기각한다.
어떤 X/Y도 gate를 통과하지 못하면 수익 가설을 기각한다. 같은 cohort 결과를 본 뒤 threshold,
stop, universe를 추가해 성공으로 재정의하지 않는다. 표본 부족은 inconclusive이며 새 frozen
cohort가 필요하다.

## Evidence identity

cohort key는
`config_hash × strategy_source_digest × mode × job_name`이다. Git commit은 provenance다.
raw Gamma/CLOB payload, request receipt, complete sweep, classifier evidence, full books/levels,
decisions, episode paths, stop attempts, resolution, run/config/source, DB checks와 storage metrics는
append-only다. 모든 P&L은 actual fill이 아닌 displayed-book counterfactual로만 표현한다.
