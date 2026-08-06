# Golden Pomegranate — Accountless Market Observatory

Golden Pomegranate는 Polymarket 공개 시장자료를 장기간 보존하는 **계좌 없는 research-only
collector**다. 수익을 내는 전략 bot이 아니며 시장을 주문하거나 position, fill, P&L을 만들지
않는다. `ExecutionLedger`도 사용하지 않는다.

정상 운영은 항상 `--simulate`다. 이 옵션은 mock API가 아니라 실제 공개 Gamma/CLOB/Data API를
읽고 실제 SQLite에 쓰되 계좌·주문 경로가 없다는 뜻이다. `--live`, credential,
`simulation_mode=false`는 DB나 network client를 열기 전에 hard fail한다.

가장 먼저 읽을 문서는 별도 [운영 README](OPERATIONS.md)다. 7/14/30/60~90/120일 일정,
외장 APFS, Jenkins shell, disk guard, 용량 추정과 기존 DB 전환 절차를 한곳에 모았다.

## 현재 수집 범위

첫 global cycle의 139,310 markets/1.38GB 실측 뒤, 2026-08-07부터 다음 capacity-bounded
Gamma envelope를 기본값으로 사용한다.

- `closed=false`
- cumulative liquidity `>= $10,000`
- cumulative volume `>= $2,000`
- `endDate <= cycle start + 120 days`
- category, sports, probability, active, standard-binary 여부로는 제외하지 않음
- 반환된 bounded envelope는 `/markets/keyset` terminal cursor까지 전수 저장

공식 Gamma의 `volume`과 `volume24hr`는 다른 source field다. cumulative storage gate에
`volume`을 쓰며 `volume24hr`로 대체하거나 fallback하지 않는다. 이 범위는 특정 BUY 후보를
고르는 전략 filter가 아니라 먼 미래·저유동성 자료의 무제한 저장을 막는 capacity boundary다.
override는 더 엄격하게만 허용된다.

2026-08-07 실제 API 비교에서는 2,899 markets로 약 97.9% 줄었다. 이 count는 고정값이 아니며
매 cycle 실제 page/market/outcome 수와 receipt clock을 기록한다. 변경 근거는
[capacity amendment](research/2026-08-07-capacity-amendment.md)에 있다.

## Gamma complete bounded census

1. `/markets/keyset`의 첫 cursor에서 시작한다.
2. `next_cursor`를 terminal cursor까지 따라간다.
3. variable-length outcome/token 배열과 source null을 순서대로 보존한다.
4. 각 market에 page별 request/receipt UTC clock을 연결한다.
5. cursor 반복, malformed page, page limit 또는 request 실패 시 partial census를 publish하지 않는다.
6. page/market/outcome/full membership과 canonical membership digest를 원자적으로 append한다.

한 번 관측한 condition은 별도 watcher가 resolution과 redeemable을 서로 추론하지 않고 독립
관측한다. collector 시작 전에 이미 closed였던 history는 coverage 밖이며 full historical archive로
표현하지 않는다.

## CLOB book deterministic sample

CLOB book은 전 token을 매 cycle 수집하지 않는다. Gamma frame의 market identity를
SHA-256으로 `bucket_count`개에 고정 배치한다.

- `sampler_slot=floor(cycle_now_epoch/(cadence_minutes×60))`
- `sampler_slot mod bucket_count`로 현재 bucket 선택
- `bucket_visit_index`로 반복 방문 횟수 복원
- `rotation_offset=(bucket_visit_index × max_markets_per_cycle) mod candidate_count`
- wall-clock slot을 사용하므로 UTC daily shard가 바뀌어도 rotation이 초기화되지 않음

선택 market의 distinct public outcome token을 요청하고 selection denominator, rank, truncation,
empty/error와 top levels/raw response linkage를 저장한다. 이는 order-book tick 전수 WebSocket
capture가 아니라 polling 표본이다.

## Public Data API trade tape

`/trades?takerOnly=true`의 bounded rolling tape만 수집한다. 한 economic fill의 maker/taker
양측 표현을 중복 저장하지 않는 taker-side economic-event 범위이며 maker-side participant 또는
maker counterparty archive가 아니다. `proxyWallet`은 경제적 source identity로 보존하지만 display
profile field는 저장하지 않는다.

- 최초 immutable 24h bootstrap baseline
- persisted watermark 이후 300초 safety lag, 1,800초 overlap
- exact requested `[start,end]`, `offset=0`, `limit=10,000`
- cap이면 midpoint recursive split, 같은 economic row는 canonical ID로 dedupe
- 정상 빈 window는 `EMPTY`
- error, cap 또는 source bounds violation은 `possible_gap=true`
- gap이 있으면 complete watermark를 절대 전진시키지 않음
- source가 bounds를 무시한 global-head row를 반환하면 lineage는 보존하되
  `SOURCE_BOUNDS_VIOLATION`으로 표시

이 tape는 maker-side activity, WebSocket/price tick 전수 또는 complete historical tape가 아니다.
분석은 source/request/receipt clock을 혼합하지 않고 regression leakage를 검사한다.

## Atomicity와 evidence profile

Gamma census가 primary atomic bundle이다. 성공한 census는 CLOB/Data API/resolution 장애 때문에
버리지 않으며 secondary component는 `SUCCESS`, `EMPTY`, `PARTIAL`, `ERROR`, `POSSIBLE_GAP`을
독립적으로 남긴다. 누락을 0이나 이전 값으로 채우지 않는다.

SQLite `research-full-v1` profile은 append-only trigger, WAL, `synchronous=FULL`을 사용한다.
`compact-v1`, row prune, 자동 `VACUUM`은 허용하지 않는다. 저장은 UTC daily shard다.

```text
data/<job>/
├── trades_sim.db
├── trades_sim_20260806.db
├── trades_sim_20260807.db
└── logs/YYYYMMDD.log
```

닫힌 shard의 일반 패턴은 `trades_sim_YYYYMMDD.db`다.

cohort는 `config_hash × strategy_source_digest × mode × job_name × schema_profile`로 나눈다.
Git commit은 provenance일 뿐 cohort key가 아니다. parser/source가 바뀌어도 run별 source digest로
구분하며 cadence와 universe가 달라지는 profile 전환은 새 runtime job을 사용한다.

## CLI

| 명령 | network | DB mutation | 의미 |
|---|---|---|---|
| `polybot config --simulate` | 없음 | 없음 | resolved config와 DB 경로 확인 |
| `polybot health --simulate` | 없음 | 없음 | quick check, append guard, disk readiness |
| `polybot run --simulate` | 공개 API read | append-only | 한 collection cycle |
| `polybot status --simulate` | 없음 | 없음 | 최근 run/component/count/storage |
| `polybot export-manifest --simulate` | 없음 | 없음 | whole-shard checksum manifest |

운영 명령과 `/Users/jongwoopark/.local/bin/uv`, `UV_LINK_MODE=copy`, Jenkins Freestyle shell은
[OPERATIONS.md](OPERATIONS.md)를 그대로 사용한다. `--live`는 negative test에서 실패해야만 정상이다.

## 문서

- [OPERATIONS.md](OPERATIONS.md): 실제 운영의 단일 기준
- [STRATEGY.md](STRATEGY.md): research hypothesis와 falsification
- [2026-08-06 preregistration](research/2026-08-06-preregistration.md): 최초 설계 provenance
- [2026-08-07 capacity amendment](research/2026-08-07-capacity-amendment.md): 현재 envelope 근거
