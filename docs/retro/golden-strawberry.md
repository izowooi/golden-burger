# Golden Strawberry — Last Mile 수집·파일럿 회고

> 성격: accountless research-only displayed-book counterfactual
> 거래 성과·실거래 원장: **N/A — source-level no-order contract**

먼저 [Evidence Contract](EVIDENCE_CONTRACT.md)를 읽는다. 다만 generic trading
`polybot-retro audit --strict`는 이 collector에 없는 거래 lifecycle을 전제로 하므로 secondary
provenance/schema 참고일 뿐이다. Primary gate는 Golden Strawberry의 immutable read-only analyzer,
research run/sweep/raw lineage, path/resolution coverage와 storage health다.

## 리뷰 범위 고정

분석 전에 아래 값을 실제 증거로 교체한다. 모든 범위는 UTC half-open
`[REVIEW_START, REVIEW_END)`다.

```text
REVIEW_START=<YYYY-MM-DDTHH:MM:SSZ>
REVIEW_END=<YYYY-MM-DDTHH:MM:SSZ>  # exclusive
TIMEZONE=UTC
JENKINS_JOB=polybot-shadow-one
RUNTIME_JOB=strawberry-shadow-one
EXPECTED_CADENCE_MINUTES=10
```

`daily-rsync scan --job polybot-shadow-one`으로 current strategy/runtime을 확인한 뒤 별도 plan,
sync, locate, verify를 수행한다. 보고서 첫머리에 canonical DB 절대 경로, `local_sha256`, remote
path, latest successful sync 시각, DB `synced_at`, source cutoff를 기록한다. verify 실패,
`SOURCE_MISSING` cutoff 초과, retention skip, runtime/strategy 불일치가 있으면 분석을 중단한다.

## Primary 분석

```bash
cd golden-strawberry
uv run python scripts/analyze_experiment.py \
  --db /absolute/verified/strawberry/trades_sim.db \
  --start "$REVIEW_START" \
  --end "$REVIEW_END" \
  --output /absolute/review/golden-strawberry-analysis.json
```

분석기는 DB를 `mode=ro&immutable=1`로 열고 `quick_check`를 실행한다. 다음을 먼저 판정한다.

- 10분 expected slot 대비 성공 coverage, duplicate/off-slot, terminal run state
- terminal CLOB sampling cursor, complete membership gzip, raw page/request/SHA linkage
- `config_hash × strategy_source_digest × mode × job_name` 단일 cohort
- end-to-end runtime p95/max와 실제 DB growth/day, 100 GiB/90% guard-stop forecast
- new crossing CLOB attempt, unresolved episode별 fixed-share bid-walk 또는 explicit censoring
- crossing-time Gamma metadata coverage/lag와 unknown event-cluster 분리
- left/gap/interval censoring과 continuous `.95` passage 미주장
- resolution lookup/payout 및 resolution jump와 price target의 구분
- sports/non-sports, binary/multi, negRisk, liquidity/volume strata

2026-08-15 predeployment CLOB probe의 약 12.5k market/25k token/13 page와 first DB 31.7MB,
subsequent growth 6.46MB는 dated estimate다. 이를 contract denominator로 쓰지 않고 review DB의
actual membership/runtime/growth를 사용한다.

## Frozen primary와 sensitivity

Primary는 오직 `entry=0.95`, `stop=0.85`, target 없음, 그 외 proven terminal Gamma resolution까지
보유다. `0.99`를 resolution으로 대체하지 않는다. entry `0.90/0.92/0.97`, stop `0.80/0.90`,
target `0.98/0.99`는 sensitivity grid일 뿐이며 같은 1주 자료에서 winner를 고르지 않는다.

경제 결과는 `$5` ask VWAP → fixed-share bid VWAP 또는 terminal payout의 displayed-book
counterfactual이다. gross와 10.4bps/72.5bps round-trip cost stress를 분리하고 source fee metadata가
없는 경우 exact fee를 주장하지 않는다. 같은 poll의 stop/resolution은 ambiguous로 표시하고
conservative stop-first를 적용한다.

## Verdict gate

- collection health 실패: `HEALTH_ONLY`
- health 통과지만 executable episode 50개, resolved known event cluster 30개, path coverage 90%,
  resolution coverage 90%, crossing-time metadata coverage 90% 중 하나라도 부족:
  `PILOT_UNDERPOWERED`
- 모두 충족: 최대 `PILOT_CANDIDATE`

`PILOT_CANDIDATE`도 profitability, parameter winner, live 승인에 해당하지 않는다. Primary 정책은
별도로 frozen한 healthy 30-day out-of-sample cohort에서 확인해야 한다. 1주 결과를 보고 parameter를
선택했다면 반드시 새 cohort로 시작하며 같은 자료에 재적용하지 않는다.

## Secondary strict audit의 한계

필요하면 generic `polybot-retro audit --strict` 결과를 별도 artifact로 보존할 수 있지만,
trading execution tables와 P&L 부재는 이 프로젝트에서 evidence gap이 아니다. 반대로 그 audit의
통과나 실패를 Strawberry health로 재해석하지 않는다. Primary JSON과 DB checksum, Daily Rsync
verify, source cutoff, `research/frozen-2026-08-15-clob` manifest가 권위다.

append-only trigger 위반, mutable latest-state cache를 source evidence로 사용, midpoint/forward
substitution, partial sweep publication, target-as-resolution, cohort 혼합이 하나라도 있으면 가설
판정이 아니라 **instrument evidence failure**로 기록하고 독립 기간을 다시 고정한다.
