# Queue Echo preregistration — external restart v2 frozen 2026-08-13

이 문서는 첫 외장 workspace outcome을 보기 전에 다시 고정한 `queue-echo-v1` 계약이다.
기존 가설, threshold, feature, control, stress와 primary arm은 바꾸지 않는다. 최초 내부
workspace 구간 `[2026-08-13T01:00:00Z, 2026-08-13T12:00:00Z)`은 배포·수집 건강성
검증 자료로만 보존하고, Jenkins executor 대기로 생긴 MI off-slot 1건과 storage path 변경
때문에 confirmatory 결론에서 제외한다.

## Confirmatory statement

표준 이진 non-negRisk 시장의 동시 YES/NO 3-tick weighted displayed-depth score가 같은
방향으로 3개의 5분 snapshot에 걸쳐 유지된 MI signal은 세 번째 receipt의 $5 ask-walk
entry에서 60~75분 뒤 첫 bid-walk exit까지 72.5bps stress 후 양의 event-cluster return을 낸다.

## Frozen identities

- experiment window: `[2026-08-13T12:00:00Z, 2026-09-12T12:00:00Z)`
- source shards: 3, full `sha256(condition_id)` integer modulo 3
- shard jobs: `raspberry-do-shard-0`, `raspberry-re-shard-1`, `raspberry-mi-shard-2`
- Jenkins workspaces: `/Volumes/t7/jenkins/polybot-do`, `/Volumes/t7/jenkins/polybot-re`,
  `/Volumes/t7/jenkins/polybot-mi`
- schedules: `0-59/5`, `1-59/5`, `2-59/5`
- primary: MI / 3 observations
- sensitivity only: DO / 1, RE / 2
- analyzer: `queue-echo-analyzer-v1`
- bootstrap: 20,000 event-cluster draws, seed `20260813`

## Frozen source and quote gates

- Gamma keyset terminal cursor; `closed=false`; liquidity ≥20k; cumulative volume ≥10k
- exact Yes/No, two distinct tokens, active/orderbook/accepting, `negRisk=false`, event ID
- Gamma outcomePrices 두 값 모두 0.20~0.80; volume24hr ≥2k; end horizon 6h~2160h
- one hash-selected condition/event before book values; hash shard before book values
- both token books in one public CLOB `/books` HTTP request with identical recorded request ID;
  receipt skew ≤2 seconds
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
- First request started in `[entry+60m, entry+75m]` only.
- Missing/error/insufficient depth is censored, never zero/forward-filled.
- Base stress 10.4bps; severe stress 72.5bps.
- Same-condition opposite token and same-slot neutral hash direction are controls.
- Neutral match uses another event, same 10pp price bin, same horizon bin, depth within 2x,
  and the same prior-15-minute ask move bin (`DOWN <-1pp`, `FLAT ±1pp`, `UP >+1pp`,
  `MISSING`).

## Health gate

Seven complete UTC days: slot SUCCESS coverage ≥95%; duplicate/off-slot 0; all SUCCESS Gamma
cursor complete; pair-token coverage ≥95%; same-request pair coverage 100%; raw payload linkage
100%; runtime p95 <180s/max <240s; single valid lineage per confirmatory range; hidden failed-run
sweep 0; quick_check okay; CRITICAL/HIGH issue 0.

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
