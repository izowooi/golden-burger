# Golden Raspberry — Queue Echo

## 상태

`research-only / accountless / archive_only`. 이 프로젝트에는 주문, wallet, position,
fill, realized P&L 경로가 없다. `--live` 또는 credential 환경 변수가 존재하면 DB와
network를 열기 전에 실패한다. 이 단계의 출력은 수익이 아니라 **가설 검정용
counterfactual evidence**다.

## 가설

유동성이 충분한 표준 이진 Polymarket에서 YES·NO 두 CLOB에 동시에 나타나는
방향 일치형 displayed-depth imbalance가 5분 간격으로 세 번 지속되면, 세 번째 관측
시점에 해당 outcome을 $5만큼 ask에서 샀다가 60분 뒤 bid에 전량 팔 수 있다고 가정한
실행가능 수익률이 비용 stress 뒤에도 양수다.

Primary는 3회 지속 `MI`다. 1회 `DO`와 2회 `RE`는 instantaneous imbalance와 persistence
효과를 분리하는 sensitivity이며, 관측 결과가 좋아도 사후 primary로 바꾸지 않는다.

## 메커니즘과 경쟁 설명

가능한 메커니즘은 정보가 있는 주문 흐름 또는 유동성 공급자의 비대칭 철회가 여러
snapshot에 걸쳐 잔량 불균형으로 남고, 이후 가격에 천천히 반영되는 것이다.

하지만 public REST book은 queue identity와 취소 경로를 보여주지 않는다. 같은 잔량이
유지됐는지, 서로 다른 주문이 같은 모양을 만들었는지, spoofing인지 구분할 수 없다.
따라서 이 실험은 “queue pressure”나 “spoof resistance”를 증명하지 않고
**persistent displayed-depth snapshots의 예측력**만 검정한다. 경쟁 설명은 다음과 같다.

- 잔량은 취소 가능한 noise여서 이후 수익률과 무관하다.
- 한 token book의 모양은 보완 YES/NO book 구조가 만든 기계적 착시다.
- imbalance가 가격 추세를 새로 설명하지 않고 Kiwi식 직전 가격 변화의 대리변수다.
- 양의 midpoint 변화가 있어도 bid-ask spread와 $5 depth를 넘지 못한다.

동시 YES/NO pair, one-tick spread, neutral control, opposite-outcome control,
$5 ask-walk→bid-walk를 고정해 이 설명들을 구분한다.

## Source universe와 hash shard

매 cycle Gamma `/markets/keyset`을 terminal cursor까지 읽는다. server envelope은
`closed=false`, liquidity ≥$20,000, cumulative volume ≥$10,000이다. client에서 다음을
모두 요구한다.

- exact outcomes `Yes/No`, 서로 다른 token 2개
- `active=true`, `closed!=true`, `enableOrderBook=true`, `acceptingOrders=true`
- `negRisk=false`, event ID 존재
- Gamma `outcomePrices` 두 값 모두 `[0.20,0.80]`
- `volume24hr ≥ $2,000`
- 종료까지 `[6h, 2160h]`(6시간~90일)

book을 보기 전에 event별 `sha256(condition_id)`가 가장 작은 market 하나만 고른다.
그 condition hash를 3으로 나눠 shard 0/1/2에 배정한다. 세 Jenkins job은 experimental
arm이 아니라 source shard다.

| Jenkins job | runtime job | shard | cron |
|---|---|---:|---|
| `polybot-do` | `raspberry-do-shard-0` | 0 | `0-59/5 * * * *` |
| `polybot-re` | `raspberry-re-shard-1` | 1 | `1-59/5 * * * *` |
| `polybot-mi` | `raspberry-mi-shard-2` | 2 | `2-59/5 * * * *` |

각 shard의 동일 raw stream에서 DO·RE·MI를 전부 계산하므로 request time과 missingness가
arm 차이로 섞이지 않는다. 세 DB를 합칠 때 condition/event는 hash상 서로 겹치지 않아야 한다.
현재 외장 workspace confirmatory window는
`[2026-08-13T12:00:00Z, 2026-09-12T12:00:00Z)`다. 이전 내부 workspace 구간은
operational health 자료로만 보존하며 이 window의 결론에 합치지 않는다.

## Displayed-depth feature

두 token의 full REST book을 같은 `/books` HTTP request에서 받으며 DB의 `request_id`가
같아야 한다. source receipt skew는 최대 2초다. 각 token `x`에 대해 best price부터
3 tick의 share depth를 `1, 1/2, 1/4`로
가중한다.

```text
B_x = Σ(d=0..2) 0.5^d × bid_size(best_bid - d×tick)
A_x = Σ(d=0..2) 0.5^d × ask_size(best_ask + d×tick)
I_x = (B_x - A_x) / (B_x + A_x)
S   = (I_YES - I_NO) / 2
```

source가 구조적으로 유효한 full book에서 해당 tick level이 없으면 그 level size는 0이다.
book/token 자체가 missing/error/malformed이면 0으로 채우지 않고 censor한다.

Primary quote gate는 두 token 모두 다음을 만족해야 한다.

- bid와 ask 존재, spread가 정확히 source tick 1개
- best bid·best ask 각각 표시 notional ≥$5
- best부터 ±2pp near-touch bid/ask notional 각각 ≥$50
- $5 entry ask walk complete
- token best ask `[0.20, 0.80]`

YES 방향은 `S ≥ +0.50`, `I_YES > 0`, `I_NO < 0`; NO 방향은 그 반대인
`S ≤ -0.50`, `I_NO > 0`, `I_YES < 0`다. neutral은 `|S| ≤ 0.10`이다.

## 시간·episode·control

- 인접 관측 receipt gap은 `[3,10]`분이다. backfill, forward-fill, 다른 cohort나 FAILED
  run snapshot을 연결하지 않는다.
- DO=현재 1회, RE=동일 방향 2회, MI=동일 방향 3회다. 각 arm의 entry clock은 그 arm이
  완성된 현재 receipt이며 MI를 첫 관측으로 backdate하지 않는다.
- event/arm cooldown은 6시간이다.
- same-slot neutral control은 `|S|≤0.10` market에서 condition hash로 YES/NO를 정하고,
  다른 event의 price 10pp bin, horizon bin, depth 2배 이내, 직전 15분 ask move bin
  (`DOWN <-1pp`, `FLAT ±1pp`, `UP >+1pp`, `MISSING`)이 같은 경우에만 point-in-time
  match한다.
- opposite control은 같은 condition의 반대 token이다. 둘 다 미래 return을 보고 고르지 않는다.

## Outcome

각 SIGNAL/CONTROL/OPPOSITE case는 entry ask를 걸어 정확히 $5를 쓰고 받은 shares를
고정한다. `signal+60m`부터 `+75m` 사이 시작한 첫 독립 CLOB request에서 같은 token의
bid를 걸어 그 shares 전량을 판다.

```text
counterfactual_return = exit_proceeds / entry_cost - 1
```

quote가 없거나 depth가 부족하면 0, 마지막 가격, resolution payout으로 대체하지 않는다.
REST 표시잔량은 실제 fill·queue position을 증명하지 않으므로 `trades`, fill, realized P&L
용어를 사용하지 않는다. base stress는 10.4bps, severe taker stress는 72.5bps다.

## Evidence contract

`queue-echo-v1` SQLite는 append-only로 다음을 보존한다.

- immutable experiment contract, preregistration/config/source digest와 run lifecycle
- 모든 Gamma request receipt, cursor-complete sweep, 압축 full membership와 gate funnel
- event panel/hash shard 선택과 모든 client-eligible market metadata
- 모든 requested token status, request/source clocks, CLOB raw gzip/hash, normalized levels
- exact YES/NO pair snapshot, score, DO/RE/MI decision과 rejection/cooldown/history lineage
- signal/opposite/neutral case와 모든 follow-up attempt/censoring reason
- cycle runtime, storage metric, data-quality issue

cohort는 `config_hash × strategy_source_digest × mode × job_name`이다. source digest는
lock/package, analyzer, CLI/orchestration, public clients, collector, repository와 frozen
계약을 모두 포함한다. code instrumentation을 고친 뒤에는 새 source digest cohort가 되며
30일 판정은 최종 단일 cohort 구간만 사용한다.

## Health와 falsification

첫 24시간은 수익성을 판단하지 않는다. 7 complete UTC day health gate는 다음과 같다.

- job별 expected slot SUCCESS coverage ≥95%, duplicate/off-slot 0
- SUCCESS Gamma sweep 100% terminal cursor, partial publish 0
- requested YES/NO token coverage ≥95%, same-request pair 100%, raw payload linkage 100%
- cycle runtime p95 <180초, max <240초
- lifecycle·lineage 위반과 CRITICAL/HIGH issue 0
- DB quick_check 정상, disk guard 정상

MI confirmatory gate는 새 단일 cohort 30일, quote-complete signal 50개, event 30개,
20 UTC day 이상을 요구한다. event-cluster bootstrap 20,000회(seed 20260813)의 familywise
98.33% lower bound가 raw·10.4bps·72.5bps stress 모두 0보다 커야 한다. outcome coverage
≥90%, neutral match coverage ≥80%, SIGNAL−neutral의 clustered 95% lower bound >0,
전반/후반 severe-stress 평균 >0도 모두 필요하다.
MI까지 도달한 episode에서는 event별 severe-stress `MI−DO`도 pair하고 clustered 95%
lower bound가 양수여야 persistence가 instantaneous imbalance보다 정보를 더했다고 말한다.

하나라도 실패하면 `STOP / UNRESEARCHABLE`이다. threshold·gap·touch window를 같은 data에서
완화하거나 DO/RE 사후 승자를 채택하지 않는다. 통과해도 `SHADOW_REVIEW_ONLY`이며 live
승인이 아니다. 실제 운용은 별도 live-capable 프로젝트, wallet risk budget, execution
evidence, 새 preregistration을 요구한다.
