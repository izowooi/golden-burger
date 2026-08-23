# Queue Echo preregistration — v3 frozen 2026-08-23

이 문서는 Queue Echo의 경제 가설을 바꾸지 않고 수집·claim·deadline evidence를 보강한
새 confirmatory cohort를 고정한다. `frozen-2026-08-13-external-v2`와 그 세 SQLite DB는
삭제·migration·UPDATE하지 않고 운영 이력으로 보존한다. v2는 atomic slot claim, durable
first-follow-up claim, FAILED duration gate와 source-role별 coverage 분리가 없으므로 v3
confirmatory 분석에는 유효하지 않으며 v3 DB와 합치지 않는다.

## Confirmatory statement

표준 이진 non-negRisk 시장의 동시 YES/NO 3-tick weighted displayed-depth score가 같은
방향으로 3개의 5분 snapshot에 걸쳐 유지된 MI signal은 세 번째 receipt의 $5 ask-walk
entry에서 60~75분 뒤 첫 bid-walk exit까지 72.5bps stress 후 양의 event-cluster return을 낸다.

## Frozen identities

- experiment window: `[2026-08-23T20:00:00Z, 2026-09-22T20:00:00Z)`
- data contract: `queue-echo-v3`; SQLite schema version: `3`
- source shards: 3, full `sha256(condition_id)` integer modulo 3
- runtime jobs: `raspberry-do-v3-shard-0`, `raspberry-re-v3-shard-1`,
  `raspberry-mi-v3-shard-2`
- Jenkins jobs/workspaces remain `polybot-do/re/mi` under their existing isolated external
  workspaces; only the runtime name and therefore DB path change
- schedules: `0-59/5`, `1-59/5`, `2-59/5`
- primary: MI / 3 observations
- sensitivity only: DO / 1, RE / 2
- analyzer: `queue-echo-analyzer-v3`
- bootstrap: 20,000 event-cluster draws, seed `20260813`

## Frozen source and quote gates

- Gamma keyset terminal cursor; `closed=false`; liquidity ≥20k; cumulative volume ≥10k
- exact Yes/No, two distinct tokens, active/orderbook/accepting, `negRisk=false`, event ID
- Gamma outcomePrices 두 값 모두 0.20~0.80; volume24hr ≥2k; end horizon 6h~2160h
- one hash-selected condition/event before book values; hash shard before book values
- both UNIVERSE token books in one public CLOB `/books` logical request with identical recorded
  request ID; receipt skew ≤2 seconds
- spread exactly one source tick on both books
- each token best bid/ask displayed notional ≥$5
- each token ±2pp near-touch bid/ask notional ≥$50
- selected token ask in 0.20~0.80; exact $5 ask walk complete

## Frozen feature

Three ticks, weights `1, 0.5, 0.25`. For token x,
`I_x=(weighted_bid-weighted_ask)/(weighted_bid+weighted_ask)` and
`S=(I_YES-I_NO)/2`.

- YES: `S≥0.50`, `I_YES>0`, `I_NO<0`
- NO: `S≤-0.50`, `I_NO>0`, `I_YES<0`
- neutral: `|S|≤0.10`
- history receipt gap: 3~10 minutes
- event/arm cooldown: 6 hours

## Frozen outcomes and controls

- Entry is current-arm receipt; MI is never backdated.
- Entry asks spend exactly $5; exit bids sell exact acquired shares.
- The first logical FOLLOWUP_ONLY request started in `[entry+60m, entry+75m]` is terminal.
- Missing/error/EMPTY_BOOK/invalid/insufficient depth is censored, never zero or forward-filled.
- A durable case claim is committed before that first request. A crash before request start may
  recover after the frozen stale lease; a crash after durable request-start evidence is censored
  without issuing another first request.
- Base stress 10.4bps; severe stress 72.5bps.
- Same-condition opposite token and same-slot neutral hash direction are controls.
- Neutral match uses another event, same 10pp price bin, same horizon bin, depth within 2x,
  and the same prior-15-minute ask move bin (`DOWN <-1pp`, `FLAT ±1pp`, `UP >+1pp`,
  `MISSING`).

## Frozen cadence and deadline contract

- A runtime deterministically maps an invocation to its most recent configured 5-minute offset.
- `slot_id`, `slot_at`, `claimed_at` and lateness are atomically committed with uniqueness on
  runtime × slot before public HTTP.
- Lateness greater than 60 seconds and duplicate invocation are explicit terminal skips and make
  no public HTTP request.
- Cooperative cycle budget is 225 seconds; hard health limit is strictly below 240 seconds.
- New HTTP is stopped with a 30-second cooperative margin. Remaining budget bounds connect/read
  timeout, retry count, exponential sleep and `Retry-After`; an unaffordable retry is not slept or
  sent.
- SQLite busy timeout is 10 seconds; cycle publish, post-publish metric and terminal audit each
  reserve their bounded portion of the 30-second non-network margin.
- Every STARTED run has one SUCCEEDED or FAILED terminal event with duration and deadline details.

## Coverage contract

UNIVERSE and FOLLOWUP_ONLY are independent source roles. UNIVERSE health reports, separately:

1. requested pair evidence and same-logical-request atomicity,
2. normalized token/pair availability,
3. quote-eligible pair availability,
4. `EMPTY_BOOK`, missing, malformed and error counts,
5. raw payload linkage.

FOLLOWUP_ONLY reports claim disposition and terminal censoring by reason. Follow-up missingness does
not reduce UNIVERSE pair coverage, and same-request atomicity is not treated as proof that two
normalized, non-empty, quote-eligible books were available.

## Health gate

Seven complete UTC days: successful expected-slot coverage ≥95%; duplicate/late HTTP count 0;
all STARTED-owned lifecycles terminal and well formed; all SUCCESS Gamma sweeps cursor complete;
UNIVERSE normalized token coverage ≥95%; same-request pair atomicity 100%; raw payload linkage
100%; all terminal run durations p95 <180s/max <240s and cooperative deadline breach 0; one valid
cohort; hidden failed-run sweep 0; quick_check okay; CRITICAL/HIGH issue 0.

## Diagnostic contrast

On episodes that reach MI, report paired severe-stress `MI − DO` by event. Its clustered 95%
lower bound must be positive before claiming persistence adds information beyond instantaneous
imbalance. This diagnostic cannot replace any MI confirmatory gate or make DO a primary arm.

## MI falsification gate

Exactly one final healthy source cohort over 30 days; ≥50 quote-complete MI SIGNAL cases;
≥30 event clusters; ≥20 UTC days; outcome coverage ≥90%; neutral coverage ≥80%.
Event-cluster 98.33% lower bounds for raw, 10.4bps, and 72.5bps return must all be positive.
Signal-minus-neutral clustered 95% lower bound and early/late severe-stress means must be positive.
The paired severe-stress `MI − DO` clustered 95% lower bound must also be positive.

Failure is `STOP / UNRESEARCHABLE`. DO/RE cannot replace MI. Parameters, timing, feature direction,
control, stress, or winner are not changed after outcome inspection. Passing is only
`SHADOW_REVIEW_ONLY`; it does not authorize trading.
