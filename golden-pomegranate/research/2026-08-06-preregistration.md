# 2026-08-06 Golden Pomegranate collection preregistration

## 상태와 목적

이 문서는 첫 운영 수집 결과를 보기 전에 Golden Pomegranate의 collection denominator,
health/falsification gate와 cadence 변경 규칙을 고정한다. 목적은 Polymarket 공개 Gamma
full-universe census, deterministic public CLOB sample과 exact-window public Data API `/trades`
rolling tape를 장기 연구용 evidence로 만드는 것이다.

Golden Pomegranate는 accountless research collector다. trading hypothesis, expected return,
order, fill, `ExecutionLedger`, position과 P&L은 모두 범위 밖이며 **N/A**다. 수집 결과가 특정
전략을 지지해 보여도 이 문서의 성공 판정은 live/shadow/trading promotion이 아니다.

## 사전 고정 cohort

한 분석 cohort는 다음 identity가 모두 같은 run으로만 구성한다.

```text
config_hash × strategy_source_digest × mode × job_name × schema_profile
```

- mode는 항상 simulation/research이고 lifecycle은 `archive_only`뿐이다.
- schema profile은 append-only `research-full-v1`이다.
- Git commit은 provenance이며 cohort boundary가 아니다.
- parser, sampler, request envelope 또는 schema semantics가 달라지면 version과 source digest가
  바뀐 새 cohort다.
- credential presence, `--live`, `simulation_mode=false`는 첫 run 이전에 hard fail한다.

최초 health window는 immutable `research_run_events`의 가장 이른 `STARTED`가 속한 날의 **다음
UTC day 00:00Z**부터 연속 7개 complete UTC day인 half-open interval로 기계적으로 정한다.
partial 첫날은 smoke evidence로 보존하되 7-day denominator에 넣지 않는다. 결과를 본 뒤 시작일을
고르지 않으며, 산출한 `[start,end)`는 별도 review artifact manifest와 7-day report 첫머리에
고정한다. DB별 checksum manifest 하나가 여러 shard의 review range를 증명한다고 간주하지 않는다.

## Source와 observation denominator

### Gamma census

각 scheduled cycle은 `closed=false` source lifecycle envelope를 strategy filter 없이 keyset
terminal까지 순회한다. 별도 `active=true`, liquidity, volume, probability, date, category,
outcome count, orderbook-enabled 또는 tradability filter로 반환 row를 제외하지 않는다.

최초 census에서 관측한 condition만 independent resolution/redeemable watcher가 closed 이후까지
추적한다. 첫 collector run 전에 이미 closed인 history는 coverage 밖이며, 과거 closed 전수를
15분마다 다시 읽지 않는다.

분모는 다음 순서로 보고한다.

1. expected Jenkins slots
2. started runs
3. successful cursor-complete atomic sweeps
4. page count
5. returned market memberships
6. returned outcome/token memberships

retry exhaustion이나 malformed/repeated cursor run은 failed run이지 0-market sweep이 아니다.
incomplete sweep의 page/market/outcome/membership row가 하나라도 published되면 atomicity gate
실패다.

각 page는 request start, response receipt, input cursor, output cursor와 raw count를 갖는다. market
observation clock은 해당 page receipt UTC다. `volume`과 `volume24hr`는 독립 source field로
보존하며 missing/invalid 값을 다른 field로 채우지 않는다.

### CLOB rotating sample

sampling frame은 successful Gamma sweep의 market 전체다. market identity는 `condition_id`를
우선하고 없으면 stable source market key를 쓴다. 아래 함수를 사전 고정한다.

```text
market_bucket = int(SHA256(market_key), 16) mod bucket_count
sampler_slot = floor(cycle_now_epoch / (cadence_minutes * 60))
cycle_bucket = sampler_slot mod bucket_count
bucket_visit_index = floor(sampler_slot / bucket_count)
rank = int(SHA256(market_key + ":rank"), 16)
rotation_offset = (bucket_visit_index * max_markets_per_cycle) mod bucket_candidate_count
```

현재 cycle bucket의 market을 rank 오름차순으로 정렬한 뒤 cyclic `rotation_offset`에서 cap만큼
선택하며 끝을 넘으면 처음으로 wrap한다. stable frame에서는
`ceil(bucket_candidate_count/max_markets_per_cycle)`번의 bucket 방문 안에 모든 candidate가
포함된다. wall-clock slot을 쓰므로 UTC daily shard의 cycle 번호 초기화가 rotation을 되감지
않는다. 각 선택 market의 모든 distinct public outcome token을 요청하고, 관측된 book 상태를
보고 재표본하지 않는다. 선택/성공/empty/error 모두 Gamma frame market 수, bucket candidate 수,
configured cap, selected/truncated 수, rank, slot, bucket/count/visit, cyclic offset/wrap, sampler
version, deterministic inclusion basis와 request/receipt clock을 갖는다. success는 configured top
bid/ask levels와 exact raw public batch response가 모두 있어야 한다.

Gamma는 census, CLOB은 sample이다. CLOB result를 full universe라고 표현하지 않고 selection/error
coverage와 함께 보고한다.

### Public Data API rolling trade component

Data API `/trades?takerOnly=true`는 authless GET만 사용한다. 한 economic fill의 taker-side
단일 row만 수집하며 maker/taker 양측 participant representation archive가 아니다. 15분 polling
관측을 WebSocket/price·book tick 전수 capture 또는 complete historical tape라고 부르지 않는다.
분석 단위는 이 taker-side economic-event row다. row count와 `size × price`는 해당 envelope
내에서만 집계하며, `proxyWallet`로 maker counterparty, maker-side wallet share, 양측
participant 활동량 또는 participant-row volume을 추정하지 않는다.
다음 window state machine을 사전 고정한다.

1. `source_target_end_epoch = cycle_now - 300초`; backlog 예산으로 잘린 실제 요청 경계는
   `bounded_target_end_epoch`에 별도로 기록한다.
2. 첫 run에서 immutable bootstrap baseline을 `source_target_end_epoch - 24h`로 고정한다. 이는 24시간을
   한 cycle에 전부 가져온다는 뜻이 아니다.
3. 이후 logical start는 persisted complete watermark `-1,800초`; overlap duplicate는 canonical
   trade ID로 dedupe한다.
4. 한 cycle이 새로 전진하는 구간은 최대 3,600초이며 HTTP 시도 64회, 전체
   logical/sub-window node 32개,
   runtime 120초 중 하나라도 소진되면 `BUDGET_EXHAUSTED`, `possible_gap=1`로 남기고 watermark를
   전진시키지 않는다. 초기 24시간 bootstrap도 같은 budget을 지키며, 성공한 cycle마다 최대
   1시간씩 같은 baseline부터 순서대로 catch up한다. 실패한 경계는 다음 cycle에서 다시
   시도하고 source의 최신 stabilized end와 이번 bounded target end를 별도로 기록한다.
5. Data API의 inclusive bound에 맞춰 각 `[start,end]` window를 `offset=0`, 최대 10,000-row로
   요청한다. midpoint 경계의 반복 row는 canonical trade ID로 dedupe한다.
6. cap에 닿은 window는 epoch midpoint로 재귀 분할한다.
7. 하나의 epoch timestamp만 남은 window도 cap이면 `possible_gap=1`; 해당 logical cycle은
   watermark를 전진시키지 않는다.
8. network/malformed error도 window `ERROR`로 commit하고 watermark를 전진시키지 않는다.
9. HTTP success empty array는 receipt clock을 가진 complete `EMPTY` window이고, complete empty
   cycle도 watermark를 전진시킨다.
10. 필수 economic field 누락, non-finite/out-of-range economics, integer가 아닌 epoch
    timestamp는 window `ERROR`; watermark는 전진하지 않는다. HTTP success가 requested
    bounds 밖 timestamp를 반환하면 row와 HTTP/sanitized raw lineage는
    `SOURCE_BOUNDS_VIOLATION`으로 보존하되 component는 `POSSIBLE_GAP`이며 watermark는
    전진하지 않는다. bounds를 무시한 응답을 재귀 split하지 않는다.
11. `source_target_end_epoch`가 persisted watermark보다 과거인 **clock regression**은 source request
    전에 `ERROR`로 기록하고 watermark를 유지한다.

각 logical/sub-window는 requested start/end, parent window, split depth, page offsets, request/receipt
UTC, returned/unique/duplicate count, canonical membership digest, status와 `possible_gap`을 가진다.
canonical global trade와 sweep membership을 별도 보존한다. byte-identical economic row의
true multiplicity는 `economic_hash + occurrence_index`로 유지하며, overlap에서 같은 multiplicity
set을 다시 읽을 때만 같은 `trade_id`로 dedupe한다.

저장 allowlist는 `proxyWallet`, `side`, `asset`, `conditionId`, `size`, `price`, `timestamp`,
`outcome`, `outcomeIndex`, `transactionHash`다. `name`, `pseudonym`, `bio`, image field와
`title`/`slug`/`icon`/`eventSlug`를 포함한 display/profile data 및 이를 포함하는 raw response는
보존하지 않는다.

### Component status

Gamma census만 page/market/outcome/membership/raw 단일 atomic bundle이다. Gamma failure/repeated
cursor면 census 전체를 rollback한다. CLOB/Data API는 같은 Gamma sweep/run에 연결하되
success/empty/error/cap observation을 독립 commit한다. secondary source failure는 complete Gamma
census를 rollback하지 않는다. run report는 세 component의 status와 coverage를 합치지 않는다.

## 독립 상태 계약

다음 쌍은 결합하거나 서로 추론하지 않는다.

- cumulative `volume` / rolling `volume24hr`
- source update time / page receipt time / sweep end time
- resolution / redeemable / 실제 redeem transaction
- public book / executable order / confirmed fill
- bounded Data API window / WebSocket complete trade tape

account가 없으므로 마지막 두 항목의 transaction/fill channel은 존재하지 않는다. resolution
관측을 synthetic SELL 또는 payout으로 변환하지 않는다.

## Storage와 rotation

- active UTC shard: `trades_sim.db`
- closed UTC shard: `trades_sim_YYYYMMDD.db`
- profile: `research-full-v1`
- mutation: append-only; compact/rollup/delete/replace/update 금지
- rotation: process lock → `quick_check` → 완결된 `wal_checkpoint(TRUNCATE)` → idle reader를
  검출하는 WAL→DELETE ownership barrier → 다음 날 임시 DB
  생성·검증·`fsync` → 기존 active의 same-volume hard-link archive → 새 active atomic replace →
  directory `fsync`; same-inode 중단 상태는 재개
- cross-midnight sweep: 시작일 shard에 전체 commit, 다음 cycle 시작 전에 rotation
- collision/corruption: overwrite/repair 없이 failed run과 hard stop

disk gate는 collection/network보다 먼저 평가한다. Jenkins가 exact external APFS/sentinel/off-volume UUID pin과 canonical workspace device를
검증하고 collector `run`이 disk/lock/shard gate를 적용한다. CLI `health`는 DB/path readiness를
읽기 전용으로 보고할 뿐 Jenkins mount identity preflight를 대신하지 않는다.

| 상태 | 판정 |
|---|---|
| usage `<70%`와 free `>=150 GiB` | collection 허용 |
| usage `>=70%` and `<80%` | warning, 10분 cadence 금지 |
| usage `>=80%` | hard stop |
| free `<150 GiB` | hard stop |
| external APFS/sentinel/off-volume UUID pin/workspace device/lock/quick-check 실패 | hard stop |

120일 whole-shard 보존을 capacity forecast horizon으로 사용한다. 120일이 자동 deletion age는
아니다. verified durable backup, checksum, restore `quick_check`와 manifest의 shard UTC date,
source cutoff, schema/table count가 모두 일치하기 전에는 shard를 제거하지 않는다. 여러
shard의 review range coverage는 manifest 하나로 주장하지 않고 catalog와 UTC-bounded query로
별도 대사한다. row-level retention은 허용하지 않는다.

## Primary 7-day collection-health gate

15분 cadence의 7 complete UTC day는 expected slot `7 × 24 × 4 = 672`개다. 아래 수치는 window가
끝난 뒤 처음 계산하며 중간 결과로 threshold를 바꾸지 않는다.

| Metric | Pass |
|---|---:|
| started/expected slot coverage | report only |
| successful cursor-complete sweep / expected slot | `>=95%` |
| complete sweep의 page clock coverage | `100%` |
| complete sweep의 full market/outcome membership+digest coverage | `100%` |
| repeated cursor accepted | `0` |
| partial published sweep | `0` |
| `volume`/`volume24hr` conflation or fallback | `0` |
| selected/failed book selection metadata coverage | `100%` |
| requested book success/explicit empty-or-error coverage | `100%` |
| successful book raw response+top-level linkage | `100%` |
| Data API requested window status+membership accounting | `100%` |
| Data API complete watermark crossing ERROR/gap | `0` |
| unresolved 1-second cap `possible_gap` | `0` |
| persisted Data API display/profile fields | `0` |
| resolution inferred from redeemable or reverse | `0` |
| dated shard rotation and `quick_check` coverage | `100%` |
| mixed cohort/profile | `0` |

API error는 허용되는 현실이지만 조용한 누락은 허용하지 않는다. requested book의 network success
rate는 별도 metric이고, 모든 요청이 success/empty/error 중 하나로 accounting되는 coverage가
primary integrity metric이다.

## Cadence decision

initial Jenkins trigger는 `H/15 * * * *`, per-run timeout은 13분이다.

7-day health gate를 전부 통과한 뒤 아래까지 만족할 때만 10분 cadence를 별도 change review에
올린다.

1. cycle runtime p95 `<8분`
2. `1.2 × p95(daily closed-shard bytes) × 120`을 더한 forecast usage `<70%`
3. 같은 forecast 후 free space `>=150 GiB`
4. mount/backup/restore drill 성공

pass는 자동 변경이 아니다. review가 승인되면 trigger/config와 collection contract를 새 cohort로
고정한다. 어느 하나라도 실패하면 15분을 유지한다. 15분에서도 runtime/API/storage pressure로
slot coverage가 `95%` 아래이고 recovery되지 않거나 p95가 timeout에 접근하면 30분 fallback을
검토한다. 30분 전환도 새 cohort이며 결과가 좋아 보이는 시간대만 고르지 않는다.

금지된 대응:

- concurrent build로 overlap시키기
- terminal 전 cursor에서 commit하기
- active/closed/liquidity/category filter를 추가해 “full” runtime을 줄이기
- Gamma row나 full membership을 compact/rollup/delete하기
- CLOB error token을 sampling denominator에서 제거하기
- Data API cap/error window를 누락하고 watermark를 전진시키기
- rolling `/trades`를 WebSocket/complete historical tape라고 보고하기

## Falsification rules

아래 중 하나면 현재 cohort의 affected research claim을 `NOT_EVALUABLE_FAIL_CLOSED`로 판정한다.

- cursor-complete/full membership/page clock/atomicity gate 실패
- volume 두 field 또는 resolution/redeemable semantics가 합쳐짐
- sampler input/version/denominator/rank/error evidence로 selection probability를 복원할 수 없음
- Data API source-window tree/membership/watermark로 exact covered interval을 복원할 수 없거나
  1초 cap `possible_gap`이 unresolved임
- source digest/config/profile가 섞였으나 run별 분리할 수 없음
- shard corruption/rotation collision을 row repair나 overwrite로 숨김
- backup checksum/restore integrity를 재현할 수 없음

15분 cadence에서도 13분 안에 stable census가 불가능하거나 120일 safe capacity를 확보하지
못하면 “현재 endpoint/storage 설계로 장기 full observatory를 운영할 수 있다”는 가설을
기각한다. 허용되는 다음 단계는 30분 fallback, transaction/retry 개선, durable capacity 확장 또는
연구 질문 축소다.

## 사전 등록된 report

7-day report는 다음 순서를 고정한다.

1. exact UTC `[start,end)`, timezone, job, mode, profile
2. active/dated DB 절대 경로, SHA-256, source digest/config hash
3. expected→started→successful complete sweep funnel
4. page clocks, repeated cursor/retry/failure와 atomic rollback audit
5. full market/outcome membership counts와 digest coverage
6. `volume`/`volume24hr` parse/null/disagreement summary
7. CLOB frame→selected→success/empty/error funnel과 selection metadata coverage
8. Data API logical window→recursive split/page→canonical membership funnel, exact complete watermark,
   overlap dedupe와 `possible_gap`
9. resolution/redeemable independent-state matrix
10. cycle runtime distribution, daily shard growth와 120-day forecast
11. cadence `KEEP_15M`, `ELIGIBLE_FOR_10M_REVIEW`, `FALLBACK_30M`, 또는
    `NOT_EVALUABLE_FAIL_CLOSED`

market profitability, simulated fills, P&L과 winner는 report에 만들지 않는다. 이 archive로 새
strategy를 고안하면 별도 train/test time split, event leakage audit와 preregistration을 만든 뒤
미사용 미래 구간에서 검정한다.
