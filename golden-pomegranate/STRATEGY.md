# Golden Pomegranate 수집 계약 — Accountless Market Observatory

문서 이름은 monorepo convention에 맞춘 `STRATEGY.md`지만 Golden Pomegranate에는 trading
strategy가 없다. 이 문서는 full-universe 관측과 deterministic public-book sampling을 나중에
재현할 수 있게 하는 data-collection contract다.

## 1. Research hypothesis

Gamma `closed=false` envelope가 반환한 전체 non-closed market/outcome의 point-in-time 상태를
selection filter 없이 보존하고,
공개 CLOB book을 selection probability가 복원 가능한 deterministic rotation으로 표본화하며,
public Data API `/trades`의 exact bounded window를 반복 수집하면,
기존 전략 DB로는 답할 수 없던 다음 질문을 survivorship/strategy-selection bias를 드러낸 채
검정할 수 있다.

- `volume`과 `volume24hr`의 변화가 market lifecycle에서 어떻게 달라지는가.
- price/calibration, spread/depth와 resolution 사이의 관계가 category/outcome 구조별로 다른가.
- Gamma resolution 관측과 redeemable 관측 사이에 어떤 시차와 disagreement가 있는가.
- 전수 metadata와 표본 CLOB execution state 사이의 coverage/selection gap이 얼마나 큰가.
- rolling trade activity와 census/book 관측의 시간 관계를 API cap/gap 한계와 함께 측정할 수 있는가.

이는 edge나 수익성 hypothesis가 아니다. 주문 가능한 가격을 관측해도 fill 또는 P&L을
주장하지 않는다.

## 2. Mechanism

과거 `golden-*` archive는 각 전략의 request envelope와 eligibility filter 때문에 저유동성,
closed/delisted, variable-outcome, 비표준 시장을 같은 확률로 포함하지 않는다. Golden
Pomegranate는 Gamma census의 분모 전체를 sweep membership으로 고정해 이 selection을 제거한다.

CLOB은 API 비용 때문에 전수 수집하지 않는다. 대신 selection 함수를 사전 고정하고 각 row에
sampling frame/rank/probability/version을 저장하면 book 분석의 표본 편향을 측정하거나 weighting할
수 있다. Gamma census와 CLOB sample을 같은 것으로 표현하지 않는 것이 핵심이다.

Data API trade는 WebSocket/tick 전수 capture가 아니라 15분 polling의 bounded rolling query다.
persisted complete watermark, 30분 overlap, 300초 safety lag와 cap-window 재귀 분할을 함께
저장해야만 어느 source interval을 완전히 조회했다고 주장하는지 복원할 수 있다.

가장 강한 competing explanation은 공개 endpoint의 metadata update clock과 order-book receipt
clock이 달라 join이 실제 market state를 대표하지 못하고, rotating sample의 missing/error가
비무작위여서 분석 가능한 표본이 다시 편향된다는 것이다. page receipt clock, request outcome,
selection denominator와 error row를 보존해 이 설명을 직접 측정한다.

## 3. Universe와 observation unit

### Gamma census

한 cycle의 Gamma universe는 strategy threshold가 아니라 `closed=false` keyset cursor가
terminal이 될 때까지 해당 request envelope가 반환한 **모든 non-closed market**이다.

- `closed=false`는 움직임을 관측하는 source lifecycle envelope다. 별도의 `active=true`,
  orderbook-enabled, accepting-orders filter는 쓰지 않는다.
- liquidity, cumulative volume, 24h volume, probability, category/tag, date로 제외하지 않는다.
- standard binary, `negRisk`, sports 여부로 제외하지 않는다.
- market의 outcome 수를 2개로 가정하지 않고 source ordering과 null을 보존한다.
- dedupe가 필요한 중복 identity도 원 source membership과 quality issue를 남긴다.

한 번 census에서 관측한 condition은 independent resolution/redeemable watcher가 closed 이후까지
추적한다. 최초 collector run 전에 이미 closed였던 market은 historical coverage 밖이다. 과거
closed 전수를 매 15분 재수집하지 않으며, 이 한계를 full historical Gamma archive라고 숨기지
않는다.

primary observation clock은 market이 포함된 **page receipt UTC**다. sweep end를 모든 row에
복사하지 않는다. source-provided update clock은 별도 field로 보존한다.

### Public CLOB sample

sampling frame은 successful Gamma sweep의 market 전체다. market identity는 `condition_id`를
우선하고 없으면 stable source market key를 쓴다. market status나 token 유무로 Gamma census
frame 자체를 바꾸지 않는다. market은 다음 함수로 하나의 bucket에 고정 배치된다.

```text
market_bucket = int(SHA256(market_key), 16) mod bucket_count
sampler_slot = floor(cycle_now_epoch / (cadence_minutes * 60))
cycle_bucket = sampler_slot mod bucket_count
bucket_visit_index = floor(sampler_slot / bucket_count)
rank = int(SHA256(market_key + ":rank"), 16)
rotation_offset = (bucket_visit_index * max_markets_per_cycle) mod bucket_candidate_count
```

현재 bucket의 market을 rank 오름차순으로 정렬하고 cyclic `rotation_offset`부터
`min(bucket_candidate_count, max_markets_per_cycle)`개를 선택한다. 끝을 넘으면 처음으로 wrap한다.
따라서 stable frame에서는 `ceil(bucket_candidate_count/max_markets_per_cycle)`번의 해당 bucket
방문 안에 모든 candidate가 포함된다. daily shard의 cycle 번호가 초기화되어도 wall-clock slot과
visit은 이어진다. 선택된 market은 source가 제공한 **모든 distinct public outcome token**을
요청한다. 선택/실패 row에는 Gamma market frame 수, bucket candidate 수, configured cap,
selected/truncated 수, rank, slot, bucket/count/visit, offset/wrap, sampler version과 deterministic
inclusion basis를 남긴다. 관측 결과를 본 뒤 market이나 token을 재표본하지 않는다. request가
empty/not-found/error이면 그 결과도 sample observation이다.

성공 response는 raw public response와 configured top levels를 함께 저장한다. normalized
best bid/ask만 남기거나 raw response만 남겨 해석 규칙을 잃는 것을 허용하지 않는다.

### Public Data API rolling trade tape

trade component는 credential 없이 public
`GET https://data-api.polymarket.com/trades?takerOnly=true`를 사용한다. 동일 economic fill의
maker/taker 양측 표현을 중복 보존하지 않고 taker-side 단일 row만 수집한다. 이는 명시적 source
envelope이며 maker-side activity coverage를 주장하지 않는다.
trade count와 notional은 이 taker-side economic-event envelope의 row만 분모로 삼는다.
`proxyWallet`을 양측 participant 모집단으로 해석하거나 maker counterparty 수, maker wallet
share, participant-side volume을 추정하는 분석은 금지한다.
한 cycle의 source 안정화 경계 `source_target_end_epoch`는 `cycle_now - 300초`이고, backlog
예산으로 잘린 실제 요청 경계는 `bounded_target_end_epoch`에 따로 남긴다. 첫 run은 source 안정화
경계 전 24시간만 bounded backfill한다.
다음 run부터 logical start는 persisted complete watermark에서 1,800초를 뺀 값이다.

과도한 backlog 때문에 Gamma census가 Jenkins timeout에 밀리지 않도록 trade worker는 한 cycle에
최대 3,600초의 새 구간만 전진한다. HTTP 시도 64회, 전체 logical/sub-window node 32개, 120초 runtime 중
하나라도 소진되면 `BUDGET_EXHAUSTED`와 `possible_gap=1`을 append하고 그 cycle의 watermark를
전진시키지 않는다. 초기 24시간 bootstrap도 이 예산 안에서 처리하며, 완결되지 않으면 다음
cycle이 같은 안전한 경계에서 다시 수집한다.

Data API의 upper bound가 inclusive이므로 logical source window를 `[start,end]`로 추적하고
`offset=0`으로 요청한다. midpoint 경계나 overlap에서 다시 보이는 row는 canonical dedupe한다.
window가 10,000-row cap에 닿으면 midpoint로 재귀 분할한다. 하나의 epoch timestamp만 남은 window가
여전히 cap에 닿으면 더 나눠 complete라고 꾸미지 않고 `possible_gap=1`을 기록하며 watermark를
전진시키지 않는다. network/parser error도 같은 fail-visible 규칙을 쓴다. HTTP success의 빈
array는 명시적인 complete `EMPTY` window이며, cycle 전체가 비었어도 complete watermark는
전진한다. 필수 economic field 누락, non-finite/out-of-range economics, integer가 아닌 epoch
timestamp는 window `ERROR`다. HTTP success가 requested window 밖 timestamp를 반환하면 source
bounds 위반이다. economic row와 HTTP/sanitized raw lineage는
`SOURCE_BOUNDS_VIOLATION`으로 보존하되 component는 `POSSIBLE_GAP`이고 watermark는 전진시키지
않는다. bounds를 무시한 응답을 재귀 split해 complete tape로 오인시키지 않는다.
`source_target_end_epoch`가 persisted watermark보다 과거인
**clock regression**은 request 전에 `ERROR`로 멈춘다.

canonical economic hash는 다음 allowed trade fields의 typed serialization에서 만든다.

```text
proxyWallet | side | asset | conditionId | size | price | timestamp |
outcome | outcomeIndex | transactionHash
```

같은 window에 byte-identical economic row가 `n`개면 true fill multiplicity를 버리지 않고
canonical order의 `occurrence_index=0..n-1`을 준다. 최종 `trade_id`는
`SHA-256(economic_hash | occurrence_index)`이고 overlap window에 같은 multiplicity set이 다시
나오면 같은 ID로 dedupe된다. global canonical trade row와 각 window의 membership을 모두
append한다. API response의
`name`, `pseudonym`, `bio`, `profileImage`, `profileImageOptimized`, `title`, `slug`, `icon`,
`eventSlug`는 display/profile data이므로 저장하지 않고, 전체 raw Data API response도 보존하지
않는다. 이는 endpoint가 반환한 모든 presentation field의 archive가 아니라 trade field만의
bounded rolling tape다.

## 4. Source-field semantics

다음 사실은 독립 column/observation이며 서로 fallback하지 않는다.

| 사실 | 의미 | 금지된 대체 |
|---|---|---|
| `volume` | source cumulative volume | `volume24hr` 복사 |
| `volume24hr` | source rolling 24h volume | cumulative volume 복사 |
| source update time | upstream metadata clock | page/sweep receipt clock 복사 |
| page receipt time | collector가 page body를 받은 UTC | sweep end 일괄 복사 |
| resolution | outcome이 해결됐다는 source evidence | redeemable 또는 book 부재 추론 |
| redeemable | public source의 redeem 가능 상태 | resolution로 자동 true 처리 |
| CLOB book | 요청 순간의 public liquidity | executable/filled order 주장 |
| Data API window | bounded rolling trade query coverage | WebSocket/complete historical tape 주장 |

missing, invalid, contradictory source value는 추정으로 고치지 않는다. raw value와 typed null,
quality reason을 함께 남긴다.

## 5. Gamma atomicity, source component와 append-only evidence

Gamma page, market, outcome, page receipt, raw payload와 full membership이 primary census
bundle이다. terminal cursor와 count/digest를 모두 검증한 뒤 하나의 SQLite transaction으로
publish한다.

- `next_cursor`가 과거 cursor와 같거나 이미 본 cursor로 돌아오면 failure다.
- transient `ChunkedEncodingError`/timeout/5xx는 bounded retry할 수 있지만 같은 page의 성공
  receipt만 published bundle에 들어간다. retry history는 append-only request evidence다.
- Gamma retry exhaustion, malformed page, digest/count mismatch 또는 DB error가 있으면 Gamma
  census bundle 전체를 rollback한다.
- 실패를 성공 0-market sweep으로 기록하지 않는다.
- crash 뒤 부분 row를 repair해 complete로 승격하지 않는다.

CLOB과 Data API는 Gamma sweep/run에 연결된 secondary source component다. 이들의 success,
empty, error, retry exhaustion, cap과 `possible_gap`은 각각 독립적으로 commit한다. secondary
failure 때문에 complete Gamma census를 rollback하지 않으며, 반대로 Gamma success가 secondary
coverage를 성공으로 바꾸지도 않는다. run summary는 component status/count/window를 분리한다.

DB profile은 `research-full-v1`이며 insert-only다. `compact-v1`, cold rollup, aggregation으로
원 row 대체, retention `DELETE`, existing evidence `UPDATE`/`REPLACE`를 허용하지 않는다.
오류 정정은 새 quality row/version/cohort를 append한다.

## 6. Daily shard와 storage safety

active path는 `trades_sim.db`이고 UTC day가 바뀌면 이전 shard를
`trades_sim_YYYYMMDD.db`로 보존하고 새 active shard를 설치한다. cycle을 shard 사이에 나누지
않는다. rotation은 single-writer lock, `PRAGMA quick_check=ok`, 완결된
`wal_checkpoint(TRUNCATE)`, idle reader까지 차단하는 WAL→DELETE ownership barrier,
destination nonexistence를 요구한다. 다음 날 DB를 임시 파일에 먼저
만들어 검증·`fsync`한 뒤 같은 APFS volume에서 기존 active를 dated 이름으로 hard-link하고 새
active를 atomic replace한다. hard-link 뒤 중단된 same-inode 상태는 다음 실행에서 재개한다.

collection 시작 전 다음이면 hard stop한다. exact external APFS/sentinel/off-volume UUID pin과 canonical workspace device는 Jenkins
preflight가 담당하고, collector `run`은 disk/lock/shard integrity를 담당한다. CLI `health`는
DB/path readiness의 read-only view이며 Jenkins mount identity 검사를 대체하지 않는다.

- filesystem usage `>=80%`
- free space `<150 GiB`
- external APFS/sentinel/off-volume UUID pin/canonical workspace device mismatch
- writer lock 획득 실패
- active/dated shard collision 또는 failed `quick_check`

usage `>=70%`는 warning이며 10분 cadence 승격과 새 장기 cohort를 금지한다. space pressure가
evidence 삭제 권한을 만들지 않는다. 120일 whole-shard 보존을 기본 planning horizon으로 쓰고,
verified durable backup 전에는 오래된 shard도 제거하지 않는다.

## 7. Run provenance

collection cycle은 `RunAudit`와 동등한 fail-closed audit로 감싼다. audit start가 실패하면 source
fetch를 시작하지 않는다. primary run success는 cursor-complete Gamma bundle이 commit된 뒤에만
기록한다. Gamma exception, disk/DB failure는 `FAILED`다. CLOB/Data API 오류는 committed
component `ERROR`와 run summary에 남기며 Gamma success를 지우지 않는다.

cohort identity는 다음과 같다.

```text
config_hash × strategy_source_digest × mode × job_name × schema_profile
```

Git commit은 provenance로만 보존한다. run/sweep/page/book row는 `run_id`로 연결하며 version은
최소 schema profile/version, collector, Gamma parser, book sampler, source request envelope를
포함한다.

credential-like config는 redaction 대상 이전에 **configuration error**다. 이 collector에는
secret-bearing resolved config가 존재할 이유가 없다.

## 8. Safety boundary

소스와 CLI에서 다음을 모두 hard block한다.

- `--live`
- `simulation_mode=false`
- `lifecycle_mode`가 `archive_only`가 아닌 값
- private key, funder, signature type, CLOB API key/secret/passphrase 등 credential presence
- authenticated client construction
- order/position/fill/ExecutionLedger/P&L table 또는 code path

public CLOB read는 trading client의 read method 재사용으로 구현하지 않는다. test는 credential과
`--live`가 network/session/DB construction보다 먼저 실패하는지를 검사한다.

## 9. Preregistered collection-health gates

최초 cadence는 15분이다. 첫 7개 complete UTC day는 총 672 expected slot을 denominator로 한다.

### 필수 validity gate

- partial published sweep: `0`
- successful sweep cursor-complete/full-membership/page-clock coverage: `100%`
- repeat cursor accepted: `0`
- Gamma market/outcome count↔membership digest mismatch: `0`
- `volume`↔`volume24hr` fallback/conflation: `0`
- resolution↔redeemable inference: `0`
- successful book의 raw response/top-level/version linkage: `100%`
- selected/failed book의 selection-bias metadata coverage: `100%`
- Data API requested source-window status/membership coverage: `100%`
- unresolved 1-second cap `possible_gap`: `0`
- complete watermark가 error/gap window를 넘어 전진한 횟수: `0`
- persisted Data API display/profile field: `0`
- shard rotation/lock/`quick_check` failure hidden as success: `0`
- mixed `config_hash × source_digest × mode × job × profile` cohort: `0`

하나라도 실패한 범위는 해당 분석에 `NOT_EVALUABLE_FAIL_CLOSED`다. row를 고쳐 같은 cohort를
살리지 않는다.

### 10분 cadence 검토 gate

다음을 모두 만족해야 10분 cadence를 별도 변경으로 검토할 수 있다.

- 7-day scheduled-slot success coverage `>=95%`
- successful requested-book observation coverage `>=95%`
- cycle runtime p95 `<8분`
- validity gate 전부 통과
- `1.2 × p95(daily shard bytes) × 120`을 반영한 예상 usage `<70%`
- 같은 forecast 후 free space `>=150 GiB`

통과는 자동 cadence 변경이 아니다. 운영자가 7-day report와 Jenkins timeout/headroom을 검토한
뒤 새 config/source cohort로 시작한다. 실패하면 15분을 유지하고, runtime/API/storage pressure로
slot coverage가 회복되지 않으면 30분 fallback을 사용한다. filter, compaction, concurrent build로
수치를 맞추지 않는다.

## 10. Falsification과 판정

다음이면 “현재 설계가 unbiased reusable observatory evidence를 만든다”는 운영 가설을 기각하거나
범위를 제한한다.

- 15분 cadence에서도 cursor-complete census를 13분 timeout 안에 안정적으로 끝내지 못함
- source가 complete cursor 또는 stable market/outcome identity를 제공하지 않음
- full membership/page clocks가 storage transaction에 원자적으로 보존되지 않음
- CLOB selection/error metadata가 missingness와 selection probability를 복원하지 못함
- Data API 10k recursive window를 1초까지 나눠도 cap이 지속되거나 exact complete watermark를
  복원하지 못함
- 120일 storage forecast가 warn threshold를 넘고 durable capacity를 확보하지 못함
- daily shard를 backup/verify/restore할 수 없음

실패 시 허용되는 제안은 cadence 30분 fallback, fetch/retry/transaction 계측 수정, storage/backup
확장 또는 해당 연구 질문의 `NOT_EVALUABLE` 판정이다. 시장 filter 추가, 과거 row 삭제,
결과가 좋아 보이는 표본만 선택하는 방식으로 full-observatory 주장을 유지하지 않는다.

성공하더라도 trading/shadow/live promotion으로 이어지지 않는다. 별도 전략 hypothesis와
독립 preregistration 없이는 이 archive로 parameter를 고르고 같은 구간에서 성과를 주장할 수 없다.
