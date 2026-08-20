# 월간 전략 회고 Evidence Contract

이 문서는 수익 가설을 검정하는 18개 `golden-*` 전략 회고의 공통 데이터 계약이다. 각 전략별 문서는
시그널과 파라미터를 설명하지만, **어떤 기록을 사실로 인정할지**는 이 문서가 우선한다.
계측이 배포되기 전의 legacy 구간과 배포 후 구간을 섞어 하나의 실측치처럼 보고하지 않는다.

## 1. 기간 경계가 먼저다

회고를 시작하기 전에 UTC 기준의 닫힌 날짜 범위를 고정한다.

```bash
export REVIEW_START=2026-06-12
export REVIEW_END=2026-07-11
export REVIEW_DAYS=30
export RETRO_OUTPUT="$HOME/polybot-retro/$REVIEW_END"
export BOT_DB=/absolute/path/to/golden-example/data/job/trades.db
```

- 보고서, SQL, backtest, 로그 집계는 모두 `REVIEW_START 00:00:00Z` 이상,
  `REVIEW_END + 1 day 00:00:00Z` 미만으로 제한한다.
- `REVIEW_DAYS`는 위 날짜 범위와 일치시킨다. `polybot-retro`가 만드는 기간은 사전
  readiness 검사이며, 최종 SQL에는 항상 `REVIEW_START`와 `REVIEW_END`를 직접 적용한다.
- 기간 밖의 backlog, 현재 `HOLDING`, 과거 설정으로 시작한 포지션을 참고할 수는 있지만
  별도 carry-in/carry-out cohort로 표시한다. 기간 내 거래처럼 합치지 않는다.
- 보고서 첫머리에 범위, timezone, 대상 DB의 절대 경로와 SHA-256을 남긴다.

## 2. 회고 시작 gate

먼저 DB를 read-only로 검사하고 secret이 제거된 JSON/Markdown bundle을 만든다.

```bash
uv run --project polybot-observability polybot-retro audit \
  --db "$BOT_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_END" \
  --output-dir "$RETRO_OUTPUT" \
  --strict
```

slot이 여러 개면 `--db /absolute/path/to/trades.db`를 반복한다. 기본 `--root` discovery는
live `trades.db`만 찾지만 중단된 job·legacy copy도 포함할 수 있으므로, live 회고 strict gate에는
선택한 DB 목록을 명시한다. simulation은 별도 실행에 `--include-sim`을 붙여
`simulation_assumption` cohort로 검사하며 live strict 결과에는 합치지 않는다.
`--strict`는 `CRITICAL` 또는 `HIGH` issue가 있으면 exit code 1을 낸다.

- `CRITICAL`이 하나라도 있으면 **파라미터 수치 교정, 승격, 증액을 중단**한다. 먼저 데이터
  복구·대사를 하거나, 그 cohort를 분석 대상에서 제외하고 gate를 다시 통과시킨다.
- `HIGH`도 자동 workflow를 중단한다. 해결할 수 없는 legacy gap이라면 범위를 계측 배포
  이후로 줄이고 재실행한다. 단순 주석으로 무시하지 않는다.
- `MEDIUM`은 보고서의 limitation과 민감도 분석에 남긴다.
- `retro-audit.json`과 `retro-audit.md`를 원자료 checksum 및 최종 보고서와 함께 보관한다.

과거 DB에는 아래 테이블이나 값이 없을 수 있다. 이는 도구 오류가 아니라
**pre-instrumentation evidence gap**일 수 있으며, 없는 fill/config/market metadata를 추정해
사실로 바꾸면 안 된다.

## 3. resolved config와 code provenance

배포 후 운영 설정의 source of truth는 각 봇 DB의 다음 두 테이블이다.

- `strategy_configs`: secret을 제거한 resolved trading config, `config_hash`, mode, 최초
  관측 Git commit
- `run_audits`: `run_id`, strategy/job/mode, `config_hash`, Git commit, 시작·종료 시각,
  `RUNNING`/`SUCCESS`/`FAILED`, cycle 통계와 DB 요약

환경변수 우선순위는 여전히 `env > config.yaml > code default`지만, Jenkins export 블록은
**현재 설정의 legacy cross-check**일 뿐 과거 실행의 단일 진실이 아니다. 기본 회고 기간은
`config_hash × git_commit × mode × job_name` cohort로 나누고, 각 거래·스냅샷은 가능한 경우
`run_id`로 연결한다. 단, 전략 코드와 shared runtime의 별도 SHA-256 source digest를
사전 등록해 DB에 저장하는 전략(Kiwi·Blueberry)은 해당 L3 계약에 따라 `git_commit` 대신
`strategy_source_digest`를 cohort 축으로 쓴다. 이 경우에도 Git commit은 provenance로
보존한다. `git_commit='unknown'`, stale `RUNNING`, 실패 run, 실행 간 큰 공백은 결론의
신뢰도를 낮추며 strict audit issue로 처리한다.

계측 배포 전 구간에는 resolved config가 자동 저장되지 않았다. 그 구간만 Jenkins 설정,
로그, 문서의 `운용 이력`을 보조 증거로 사용하고 출처와 불확실성을 적는다. 현재 env를 과거
전체에 소급하지 않는다.

## 4. 주문 접수와 체결을 분리한다

`trades`는 전략 의사결정과 기존 상태 머신의 기록이다. 특히 legacy 구현은 GTC limit 주문이
접수되어 `live`/`accepted` 응답이나 `orderID`를 받으면 즉시 `HOLDING` 또는 `COMPLETED`로
기록할 수 있다. 이것은 fill 증거가 아니다. `UNFILLED`도 뒤늦은 잔고 대사에서 발견한 일부
유령 포지션 표식일 뿐, 그 밖의 행이 모두 전량 체결됐다는 증거가 아니다.

배포 후 execution truth는 다음 append-only ledger에서 가져온다.

| 테이블 | 의미 |
|---|---|
| `order_submissions` | 요청 side/price/size, order ID, 접수 응답, simulation 여부, 최신 대사 상태 |
| `order_status_events` | 주문 상태와 `original_size`/`size_matched`의 관측 이력 |
| `order_fills` | trade ID별 상태, 실제 size/price, `MAKER`/`TAKER` role, fee, match/chain 정보 |

실현 결과 집계 규칙은 다음과 같다.

1. `simulation = 0`인 주문만 live 결과에 포함한다.
2. `order_fills.status = 'CONFIRMED'`인 fill만 실제 체결로 인정한다. `MATCHED` 주문 상태나
   `MINED` 전 trade, 접수 응답만으로 P&L을 만들지 않는다.
3. partial fill은 confirmed fill의 size를 합산하고, 요청 수량이나 legacy `buy_shares`로
   전량 체결을 가정하지 않는다. BUY와 SELL 양쪽의 confirmed coverage를 각각 검증한다.
   단, exact CONFIRMED fill 합계가 `latest_size_matched`와 일치하고 대사가 끝났으며 주문
   상태가 terminal `MATCHED`이면, 거래소가 소수점 아래를 quantize하여 원시 요청 수량보다
   조금 작더라도 해당 **거래소 주문의 전량 체결**로 인정한다. `MATCHED` 상태만 있거나
   confirmed fill 합계가 matched 수량과 다르면 이 예외를 적용하지 않는다.
   terminal `CANCELED` 계열의 부분 체결은 전량 체결로 부르지 않지만, confirmed fill 합계가
   `latest_size_matched`와 일치하고 대사가 끝났다면 그 실제 체결 수량만 포지션에 반영한다.
   미체결 잔여 수량을 요청 수량으로 채우거나 PENDING 상태에 영구 고정하지 않는다.
   `MATCHED` 문자열만으로 전량 체결을 단정하지 않는다. submission token amount 또는
   order status event의 `original_size`와 confirmed fill 합계를 비교해 잔여 수량을 보존한다.
4. 실제 fill price와 size로 gross P&L을 계산하고, `fee_amount_usdc`가 있으면 차감한다.
   fee amount가 없을 때 임의 수수료를 0으로 채우지 않는다. 단, 같은 CONFIRMED fill의
   `fee_rate_bps`가 유효한 숫자 `0`으로 명시된 경우에는 이를 **증명된 0 fee**로 인정한다.
   Golden Cherry처럼 builder-fee 주문 경로가 없다고 source contract가 명시한 봇은 exact
   `liquidity_role='MAKER'` CONFIRMED fill에서 거래소가 rate와 amount를 모두 생략한 경우도
   platform fee 0으로 인정할 수 있다. 이 예외를 `TAKER`, role 불명, builder-fee 가능 주문,
   또는 0이 아닌 rate의 amount 누락에 적용하지 않는다. 그 밖의 누락은 fee evidence gap이며
   `liquidity_role`과 함께 fee completeness를 표시한다.
5. `needs_reconciliation = 1`, 오래된 `last_reconciled_at`, reconciliation error,
   terminal이 아닌 order/trade는 미완결 evidence다. 결과를 확정하지 않는다.
6. `trades.buy_order_id`/`sell_order_id`로 ledger와 연결한다. 두 주문의 confirmed fill
   coverage가 완전하지 않은 `COMPLETED` trade의 `realized_pnl`은 **order-assumption P&L**로만
   표시하고 실현 P&L 합계에서 제외한다.

Sports delay의 `DELAYED` FOK가 order-detail catalog에서 사라지는 경우에는 일반적인
catalog-missing gap과 구분한 좁은 zero-fill 계약을 사용할 수 있다. 해당 주문이 source-level
FOK-only 경로였고, 제출 후 사전 등록된 TTL 이상이 지났으며, current/pre-migration order
catalog에 exact ID가 없고, 전체 authenticated token-trade catalog에도 exact order를 참조하는
trade가 없고, cancellation API가 그 exact ID를 `canceled` 또는 exact
`not found/already canceled`로 반환해야 한다. FOK는 all-or-none이므로 이 conjunction은
`DELAYED_FOK_*_ZERO_FILL` proof로 종결할 수 있다. 연결된 trade/fill, 부분 체결, 일반 GTC/FAK,
단순 HTTP 200이나 빈 catalog 하나만으로는 이 예외를 적용하지 않는다.

기존 order ID는 제한된 기간에 best-effort bootstrap될 수 있지만, API에서 더 이상 확인할 수
없는 과거 주문이나 누락된 ID를 복원하지 못한다. legacy 행은 별도 표본으로 두고 “체결 사실
미확인”이라고 명시한다. 지갑 대사가 필요하면 해당 봇 도구가 있는 프로젝트에서 전역 옵션을
subcommand 앞에 둔다.

```bash
uv run tools/wind_down.py --funder 0x... status
```

주소는 보고서·로그·commit에 넣지 않는다.

## 5. 시장 universe와 가격 archive

Gamma 시장 수집은 `/markets/keyset`에 전략별 `liquidity_num_min`(해당되는 경우 누적
`volume_num_min`)을 요청하고 `next_cursor`를 끝까지 따라간다. 고정 offset 2,100개 cap이나
“가장 오래된 시장” 표본을 더 이상 전제로 하지 않는다. 한 sweep의 대상은 서버 필터가 만든
request envelope를 cursor 완주한 응답 중 active, not closed, orderbook enabled, accepting
orders이고 동일 최소 liquidity/누적 volume을 client에서도 다시 검증한 **그 시점의 qualifying
universe**다. `raw_market_count`와 exclusion count는 전체 Gamma 시장이 아니라 이 request
envelope 기준이다. `volume24hr` 하한은 `volume_num_min`과 의미가 다르므로 서버 필터로
대체하지 않고 전략 scanner에서 계속 검사한다. 따라서 절대 시장 수를 문서에 고정하지 않고
run별 filter, `markets_scanned`, membership digest와 catalog coverage를 보고한다.

과거 중앙 archive는 `golden-nectarine`(liquidity ≥ $10k)과 보조 `golden-honeydew`
(liquidity ≥ $15k)의 SQLite다. 두 전략은 2026-07-30 폐쇄됐으므로 이 DB는 폐쇄 시점까지의
historical evidence source일 뿐, 이후 universe를 계속 수집하는 live archive로 간주하지 않는다.

`golden-papaya`·`golden-queen`·`golden-quince`의 기본 request envelope는 liquidity ≥ $1k,
누적 volume ≥ $1k이며, liquidity의 실제 하한은 `min(configured entry liquidity, $1k)`이다.
`golden-melon`은 liquidity ≥ $1k, 누적 volume ≥ $10k를 세 팔에 공통 적용한다. 누적 volume
서버 필터는 각 전략의 최근 24h volume entry gate와 별개이며 client에서 둘 다 다시 검증한다.
따라서 중앙 archive가 이 universe를 완전히 덮는다고 간주하지 않고 각 counterfactual은 자체
archive를 주 source로 사용한다. first-observed 주장은 각 request envelope와 보존기간 안으로
제한한다. Papaya·Queen·Quince는 같은 호스트에서 동일 filter의 검증된 sweep만 공유하고,
Melon 세 팔은 별도의 $10k sweep만 공유한다. 모든 자체 archive는 cursor-complete
sweep/membership digest/catalog/event coverage 계약을 지킨다.

`golden-kiwi`는 live 성과가 아니라 Micro-Cascade 가설을 검증하는 **simulation-only**
archive다. 모든 5분 raw snapshot을 60일 보존하고, A/B/C/D arm은
`config_hash × strategy_source_digest × mode × job_name` cohort로 완전히 분리한다. Git
commit은 provenance일 뿐 cohort 경계가 아니다. 진입의
best ask와 60~75분 뒤 최초 관측 best bid로 계산한 값은 hypothetical proxy return이며,
confirmed fill 또는 live realized P&L로 표현하지 않는다. 75분 안에 exit quote가 없으면
임의 가격을 채우지 않고 censored observation으로 남긴다. Kiwi compact metadata가
깨졌거나 cadence coverage가 90% 미만이면 strict audit은 fail closed한다.

2026-08-13 재실험에서 Kiwi의 request envelope는 Gamma
`liquidity_num_min=20000`, 누적 `volume_num_min=10000`으로 고정한다. 전략 entry의
`volume24hr>=10000`은 별도로 재검증한다. 각 SUCCESS run은 정확히 한 schema v2
cursor-complete sweep을 가져야 하고, 53 page·5,330 raw market·120초를 하나라도 넘은
partial/over-budget run은 evidence로 인정하지 않는다. analyzer v3는 DB의 attested filter,
budget과 elapsed를 모든 canonical SUCCESS run에서 다시 확인한다.

- `market_snapshots`: YES probability, liquidity, volume, best bid/ask, spread,
  source update 시각, `run_id`, 수집 시각
- `market_catalog`: condition/market/event ID와 slug, question, end date, outcomes,
  token IDs, tags, fee metadata, first/last seen

`market_catalog.event_id`를 우선 사용해 correlated markets를 묶고, 없을 때만 event slug,
질문·시간 기반 휴리스틱을 사용한다. NO 가격 `1 - YES`는 spread와 fee를 무시한 근사이며
실제 fill을 대체하지 않는다.

`compact-v1` 활성 전에는 모든 sweep의 상세 membership과 5분 snapshot을 전제로 했다.
활성 후에는 cursor-complete `market_sweeps`의 count/digest/run summary는 매 sweep 유지하지만,
시장별 `market_sweep_memberships` 상세는 기본 24시간 checkpoint 표본이다.
`membership_detail_stored=0`인 sweep의 digest는 사후 개별 row로 재계산할 수 없으므로 count와
digest shape/run provenance만 검증하며, per-condition exclusion과 eligible/snapshot 비율은
`membership_detail_stored=1` 표본에 한해 보고한다. 두 evidence 수준을 동등하다고 표현하지 않는다.

snapshot cadence도 hot/cold로 나눈다. `polybot_db_maintenance.last_report_json`의 profile,
schema version, strategy, selector와 양의 finite policy가 모두 유효할 때만 hot 구간은 5분,
cold 구간은 선언된 rollup bucket cadence로 감사한다. 깨진/임의 maintenance metadata는 legacy
5분 계약으로 fail closed한다. Nectarine cold row는 이동창 양끝의 최저 변화점,
Papaya/Queen은 최저·최고 변화점이므로 한 bucket에 여러 row가 있을 수 있다. 두 전략의 거래별
`prior_snapshot_id_at_entry`/`entry_snapshot_id`는 rollup과 retention에서 제외한다.

“5분 archive”라는 이름만 믿지 않는다. `market_sweeps`의 cursor-complete qualifying 분모를
기준으로 선택 기간 양 끝 coverage, adaptive snapshot bucket, run gap, 거래 condition ID의
snapshot/catalog join coverage를 수치로 보고한다. digest/count/run 연결이 깨진 sweep은
분모에서도 제외하고 CRITICAL evidence issue로 처리한다. coverage가 부족한 구간은
counterfactual 대상에서 제외한다. 구체적인 migration/rollback은
`docs/sqlite-storage-maintenance.md`를 따른다.

### 5.1 Golden Pomegranate accountless collector

`golden-pomegranate`는 trading strategy가 아니라 미래 가설 탐색용 market observatory다.
따라서 `trades`, confirmed BUY/SELL fill, fee coverage와 P&L이 없는 것은 evidence gap이 아니라
**source-level no-order 계약**이다. 반대로 다음 항목이 primary evidence gate다.

- append-only `research_run_events`, `research_config_versions`의 resolved config/source digest와
  UTC collection contract
- cursor-complete `market_sweeps`, 매 sweep 전체 membership, `closed=false` non-closed universe 전수 observation
- sweep 완료 시각이 아니라 Gamma page별 local receipt time
- variable outcome label/index/token identity와 누적 `volume`/`volume24hr` 분리
- deterministic CLOB book rotation의 bucket·selection reason·coverage·source receipt time
- Data API trade window의 start/end, complete watermark, overlap/dedupe, cap 분할, 경제 field만
  남긴 sanitized source payload와
  `possible_gap`; public polling coverage를 WebSocket full-tick evidence로 대체하지 않음
- active sweep에서 사라진 condition의 독립 resolution observation과 redeemable 분리
- UTC 일별 `trades_sim_YYYYMMDD.db` shard의 SHA-256/`quick_check`/source cutoff
- disk watermark, DB/WAL bytes, overlap/gap과 `forecast_days_to_stop`

Pomegranate는 `compact-v1` 대상이 아니며 observation/member row를 rollup하거나 삭제하지
않는다. 분석 기간에 걸친 active `trades_sim.db`와 완결 shard를 모두 명시해 검증한다.
현재 trading 중심 `polybot-retro audit --strict` 결과를 collector health 판정으로 재해석하지
말고, `golden-pomegranate`의 `health`와 manifest를 primary gate로 사용한다. Pomegranate에서
만든 derived dataset을 trading strategy backtest에 쓸 때는 dataset version/checksum과
point-in-time cutoff를 고정한 뒤 그 전략의 기존 execution contract를 별도로 적용한다.

## 6. local daily evidence와 내구성

`daily-report/data/daily_evidence.sqlite3`는 `ACCOUNT_<n>_NAME`/`ACCOUNT_<n>_ADDRESS`에서
동적으로 발견한 현재 configured account 집합을 Supabase stable catalog와 대사한 뒤 한
transaction으로 저장한다. 코드에 고정 상한은 없으며 2026-08-06 현재 Jenkinsfile과
`.env.example`에는 13개 slot이 선언돼 있다. 따라서 회고에서 9나 13 같은 수를 영구 계약으로
하드코딩하지 않고, 각 run의 expected/observed account 집합과 schema version을 기준으로 과거
v2 exact 6-account 구간 및 이전 v3 account-set cohort를 분리한다.

- `evidence_report_runs`: expected/observed account 집합과 `COMPLETE`/`FAILED`
- `evidence_account_snapshots`: account별 total/position/cash value
- `evidence_positions`: condition/asset/outcome, size/price/value/P&L, redeemable/end date
- `evidence_delivery_status`: Supabase/Slack별 `PENDING`/`SUCCESS`/`FAILED`/`SKIPPED`와
  최종 delivery 상태

wallet address, private key, API token은 저장하지 않는다. `FAILED` run을 정상 0잔고로 해석하지
않고, portfolio 비교에는 expected account가 모두 있는 collection `COMPLETE` run만 사용한다.
collection 완료와 downstream delivery 완료는 별도 사실이므로 운영 보고에는 delivery
`COMPLETE`도 확인한다. Jenkins artifact는 편리한 사본이지 유일한 backup이 아니다.

Jenkins Freestyle의 `Execute shell`은 `-x`/`-xe`로 실행될 수 있다. private key를 inline
`export`하면 Python이 시작되기 전에 console에 노출되므로, Secret text/file Credentials
Binding을 사용하고 secret 참조 전부터 `set +x`를 적용한다. console dump는 일반 로그가 아니라
credential-bearing artifact로 취급하며, 노출이 의심되면 로그 삭제만으로 끝내지 않고 키를 회전한다.

SQLite를 실행 중에 `cp`하지 말고 online backup과 checksum manifest를 만든 뒤 Jenkins
workspace 밖의 내구성 있는 저장소로 복제한다.

```bash
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"
```

복구 훈련에서는 manifest SHA-256, `PRAGMA quick_check`, 테이블/기간/count를 검증한다.

## 7. Supabase NAV의 용도와 한계

현재 `pb_algorithm_accounts`, `pb_daily_algorithm_balances`,
`pb_daily_portfolio_totals`는 daily NAV 비교용이다. 다음을 구분한다.

- mutable `algorithm_code`만으로 과거 slot의 전략을 귀속하지 않는다. additive migration의
  `pb_strategy_deployments`가 실제로 적용·채워진 기간에만 effective-dated attribution을 한다.
- 현재 writer는 migration의 단일 `pb_write_complete_portfolio_snapshot_v2` RPC transaction과
  `pb_snapshot_runs.COMPLETE`만 사용하며 migration이 없으면 preflight에서 실패한다. migration
  이전 legacy 두 단계 write 구간은 부분 snapshot 가능성을 별도 검사한다.
- 입출금이 있으면 단순 `(end/start)-1`은 전략 수익률이 아니다. `pb_external_cash_flows`가
  실제로 수집된 구간만 flow-adjusted/TWR을 계산한다. 미수집 구간의 TWR을 꾸며내지 않는다.
- Polymarket `TRADE`, `SPLIT`, `MERGE`, `REDEEM`, reward, rebate는 user-controlled external
  cash flow와 구분한다.
- 대시보드의 freshness, account date range, missing dates, account 합계와 portfolio total
  reconciliation을 회고 bundle에 함께 남긴다.

위 세 테이블은 `slack-data-collector/sql/pb_portfolio_history_v2.sql`의 **additive design**이며,
SQL 파일이 존재한다는 사실은 production migration·backfill 완료를 뜻하지 않는다.

## 8. 최소 보고서 구조

모든 회고는 다음 순서를 지킨다.

1. `REVIEW_START`/`REVIEW_END`, DB checksum, audit status와 issue
2. config hash × Git commit × mode × job cohort 및 legacy 구간
3. confirmed execution coverage, partial fills, fee/role completeness, 미대사 주문
4. archive window/5-minute/catalog/event coverage
5. 기간 내 실현 결과와 carry-in/out, account/NAV 대사
6. event-cluster 기준 유효 표본 수와 uncertainty
7. 동일 evidence 위의 counterfactual grid 및 leakage/assumption
8. `KEEP`/`CHANGE`/`STOP` 제안, 근거, confidence, rollback 기준

Evidence gate를 통과하지 못하면 8번은 숫자 변경안 대신 **계측·복구 작업**만 제안한다.
