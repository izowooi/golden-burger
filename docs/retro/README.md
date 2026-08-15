# 전략 회고(포스트모템) 마스터 플레이북

> **용도**: `golden-*` 전략을 고정된 기간과 검증된 execution evidence로 회고하고,
> 근거가 충분할 때만 파라미터를 교정한다.

폐쇄 완료된 6개 전략(lime·fig·mango·date·honeydew·nectarine)의 교차 비교와 다음 전략용 체크리스트는
**[폐쇄 전략 통합 포스트모템](closed-strategies-postmortem.md)** 에 있다.

모든 회고는 먼저 [Evidence Contract](EVIDENCE_CONTRACT.md)를 읽는다. 전략별 문서는
시그널·SQL·sweep grid를 제공하며 단독 문서가 아니다. Jenkins env를 붙여넣는 것만으로
resolved config, 체결, 시장 coverage가 증명되지 않는다.

## 1. 전략별 가이드

| 전략 | 문서 | 비고 |
|---|---|---|
| golden-apple | [golden-apple.md](golden-apple.md) | 운영 2계정 (1)/(2) |
| golden-banana | [golden-banana.md](golden-banana.md) | 운영 |
| golden-blueberry | [golden-blueberry.md](golden-blueberry.md) | **Closing Surge**, 72h 첫 급등 +2%p/+5%p A/B, $5 hard cap |
| golden-cherry | [golden-cherry.md](golden-cherry.md) · **1차 완료** → [2026-07 파라미터 회고](golden-cherry-2026-07-parameter-review.md) | 운영 + 변형 slot |
| golden-date | [golden-date.md](golden-date.md) · **⛔ 폐쇄 완료 (2026-07-29)** → [판정](golden-date-2026-07-verdict.md) | Conviction Ladder |
| golden-elderberry | [golden-elderberry.md](golden-elderberry.md) · **파라미터 조정 보류** → [2026-08 파라미터 리뷰](golden-elderberry-2026-08-parameter-review.md) | Panic Fade, `polybot-cherry` job에서 운영. `max_positions: 20` 적용 완료 |
| golden-fig | [golden-fig.md](golden-fig.md) · **⛔ 폐쇄 완료 (2026-07-28)** → [판정](golden-mango-fig-2026-07-verdict.md) | Hope Crusher |
| golden-grape | [golden-grape.md](golden-grape.md) | Cascade Rider |
| golden-honeydew | [golden-honeydew.md](golden-honeydew.md) · **⛔ 폐쇄 완료 (2026-07-30, 운영자 확인)** → [판정](golden-honeydew-2026-07-verdict.md) | Night Watch, archive 보존 |
| golden-kiwi | [golden-kiwi.md](golden-kiwi.md) · [과거 증거 정정](../../golden-kiwi/research/2026-07-30-cohort-correction.md) | **Micro-Cascade, research/simulation 전용, live 금지**, 4-arm 5분 독립 검정 |
| golden-lime | [golden-lime.md](golden-lime.md) · **⛔ 폐쇄 완료 (2026-07-28)** → [최종 판정](golden-lime-2026-07-backtest-verdict.md) · [진단](golden-lime-2026-07-close-recommendation.md) | Shock Follow |
| golden-mango | [golden-mango.md](golden-mango.md) · **⛔ 폐쇄 완료 (2026-07-28)** → [판정](golden-mango-fig-2026-07-verdict.md) | Patience Premium |
| golden-melon | [golden-melon.md](golden-melon.md) | **Resolution Sprint**, cherry 재설계, 거래량 gate가 A/B/C 처치축 |
| golden-nectarine | [golden-nectarine.md](golden-nectarine.md) · **⛔ 폐쇄 완료 (2026-07-30, 운영자 확인)** → [판정](golden-nectarine-2026-07-verdict.md) | Bottom Fisher, archive 보존 |
| golden-orange | [golden-orange.md](golden-orange.md) | Fear Spike Fade |
| golden-papaya | [golden-papaya.md](golden-papaya.md) | Final Five, 자체 저유동성 60일 archive |
| golden-pomegranate | [golden-pomegranate.md](golden-pomegranate.md) | **Market Observatory**, accountless `research-full-v1` collector; trading P&L은 N/A |
| golden-queen | [golden-queen.md](golden-queen.md) | Crown Momentum, 스포츠 포함, 12h/24h 사전 등록 A/B |
| golden-quince | [golden-quince.md](golden-quince.md) | **Spread Harvest**, BUY 실행 측면이 처치축, $5·24h·A/B/C 3팔 사전 등록 |
| golden-raspberry | [golden-raspberry.md](golden-raspberry.md) | **Queue Echo**, accountless `queue-echo-v1`; MI primary와 DO/RE sensitivity를 세 hash shard에서 동시 수집 |
| golden-strawberry | [golden-strawberry.md](golden-strawberry.md) | **Last Mile**, accountless `last-mile-clob-v1`; 10분 CLOB sampling census, 0.95/0.85/terminal primary, 1주 pilot health only |

새 전략은 [새 전략 구현·승격 플레이북](../new-strategy-playbook.md)을 따른다. nectarine의
position cap은 [전용 회고](../nectarine-max-positions-retro.md)를 함께 참고하되, execution
및 기간 계약은 이 디렉터리의 Evidence Contract가 우선한다.

## 2. 데이터 계층

```text
strategy decision        trades / skipped_markets / cycle_stats
resolved provenance      strategy_configs / run_audits
actual execution         order_submissions / order_status_events / order_fills
counterfactual archive   market_snapshots / market_catalog
daily account evidence   daily_evidence.sqlite3
portfolio NAV            Supabase pb_* tables
```

- `trades`는 전략 상태 기록이지 자동으로 actual fill 원장이 아니다.
- GTC order의 접수, `live` 응답, order ID는 fill을 뜻하지 않는다. 실현 결과는
  `order_fills.status='CONFIRMED'`의 size/price/fee를 사용한다.
- pre-instrumentation legacy 구간은 fill/config/catalog가 없을 수 있다. 그 구간을 사후
  추정값으로 채우지 않고 별도 cohort로 표시한다.
- 중앙 가격 archive는 폐쇄된 nectarine(liquidity ≥ $10k)과 honeydew(≥ $15k)의
  보존 DB다. 폐쇄가 archive 삭제를 뜻하지 않는다. papaya, queen, quince, kiwi는
  각각의 진입/연구 universe와 lineage가 달라 자기 DB의 point-in-time
  `market_snapshots`/`market_catalog`를 주 source로 사용한다. Blueberry도 첫 교차와
  A/B 상승폭을 증명하기 위해 자기 archive/catalog를 주 source로 사용한다. 중앙 archive와 겹치는
  시장은 교집합 대조에만 쓴다.
  Gamma keyset cursor를 끝까지 순회한 당시 qualifying universe를 저장하므로 고정 시장 수나
  2,100 cap을 가정하지 않는다. 중앙 archive는 실제 5-minute coverage를 측정하고,
  papaya/queen/quince/kiwi/blueberry는 Jenkins schedule/run manifest에서 cohort 기대 cadence를
  산출해 actual bucket과 gap을 비교한다. Kiwi의 primary B는 5분 full-cadence 30일
  독립 기간이 필수이며 compact된 cold rollup을 원래 5분 data로 해석하지 않는다.
- Pomegranate는 중앙 archive를 대체하는 새 strategy runtime 의존성이 아니라, 향후 가설
  탐색용 별도 연구 원자료다. 거래 audit 대신 complete cursor, raw cadence, order-book
  selection coverage, resolution follow-up, disk/restore health를 검증하며 `trades_sim.db`와
  완결 UTC shard를 함께 읽는다. 관측치를 P&L이나 confirmed fill로 해석하지 않는다.
- Supabase NAV는 account snapshot이다. effective deployment, complete snapshot marker,
  external cash flow migration이 실제 적용·backfill되기 전에는 과거 전략 귀속이나 TWR의 완전한
  source로 사용하지 않는다.

## 3. 실행 순서

### 3.1 범위 고정

UTC 날짜를 명시한다. 모든 SQL은 `[REVIEW_START 00:00Z, REVIEW_END + 1 day 00:00Z)`를
사용한다.

```bash
export REVIEW_START=2026-06-12
export REVIEW_END=2026-07-11
export REVIEW_DAYS=30
export RETRO_OUTPUT="$HOME/polybot-retro/$REVIEW_END"
export BOT_DB=/absolute/path/to/golden-example/data/job/trades.db
```

### 3.2 evidence gate

```bash
uv run --project polybot-observability polybot-retro audit \
  --db "$BOT_DB" \
  --days "$REVIEW_DAYS" \
  --as-of "$REVIEW_END" \
  --output-dir "$RETRO_OUTPUT" \
  --strict
```

`CRITICAL` issue가 있으면 parameter tuning·증액·promotion을 하지 않는다. `HIGH`도 strict
workflow를 중단하므로 복구하거나 계측 배포 이후로 범위를 다시 잡는다. audit bundle을
최종 보고서와 함께 보관한다. 여러 live slot은 `--db`를 반복하고 simulation DB는 별도
bundle에서 `--include-sim`으로 검사한다. simulation issue는 `simulation_assumption`으로 표시되며
live strict 판정에는 들어가지 않는다. 넓은 `--root`는 중단 job/legacy copy까지 발견하므로 live
gate에 섞지 않는다.

### 3.3 cohort와 execution 대사

1. `run_audits`와 `strategy_configs`로 `config_hash × git_commit × mode × job_name` cohort를
   만든다. 단, Kiwi와 Blueberry는 모노레포 commit 대신 각 L3 계약의
   `strategy_source_digest`를 사용한다. Jenkins export는 legacy/current cross-check로만 쓴다.
2. `trades`의 BUY/SELL order ID를 execution ledger와 연결한다. confirmed fill coverage,
   partial fills, fee/role completeness, pending reconciliation을 먼저 보고한다.
3. `UNFILLED`은 뒤늦게 발견한 일부 미체결 표식일 뿐이다. 그 외 trade가 체결됐다는 역증거로
   쓰지 않는다. ledger가 없는 legacy P&L은 `ORDER_ASSUMPTION`으로 분리한다.
4. 필요하면 주소를 출력·저장하지 않는 로컬 세션에서 지갑과 대사한다.

```bash
uv run tools/wind_down.py --funder 0x... status
```

이 명령은 `tools/wind_down.py`가 있는 해당 bot 디렉터리에서 실행한다.

### 3.4 archive와 account quality

- 거래 condition ID의 `market_snapshots`/`market_catalog` join coverage를 확인한다.
- 기간 양끝, archive별 기대 cadence 대비 actual bucket, run gap, event ID coverage를 수치화한다.
  중앙 nectarine/honeydew의 hot evidence는 실제 5-minute coverage를 재고,
  papaya/queen/quince/kiwi/blueberry는 Jenkins schedule/run manifest의 cohort cadence를 쓴다.
- daily local evidence는 해당 schema의 exact account set(v2=6, v3=9)이 모두 있는
  `COMPLETE` run만 사용한다.
- dashboard의 freshness, account별 date range/missing days, portfolio reconciliation을 확인한다.
- event 파생 시장은 `market_catalog.event_id` 중심으로 묶어 명목 n과 유효 n을 함께 보고한다.

### 3.5 분석과 결정

동일한 evidence set에서 현재값과 sweep 후보를 비교한다. look-ahead, survivorship,
same-event leakage를 차단하고 비용·spread·unfilled sensitivity를 별도 제시한다. 결과는
`KEEP`/`CHANGE`/`STOP`과 confidence, rollback 기준으로 끝낸다. 표본이나 계측이 부족하면
숫자 변경안 대신 다음 라운드의 instrumentation 요구사항을 제시한다.

## 4. 복붙용 상위 프롬프트

```text
docs/retro/EVIDENCE_CONTRACT.md와 docs/retro/golden-<전략>.md를 순서대로 읽어라.

REVIEW_START=<YYYY-MM-DD UTC>
REVIEW_END=<YYYY-MM-DD UTC>

1) polybot-retro --strict audit를 먼저 실행하고 audit bundle을 보존한다.
2) CRITICAL/HIGH gap을 해결하거나 분석 범위를 계측 이후로 다시 고정한다.
3) config_hash × git_commit × mode × job cohort를 분리한다. Kiwi와 Blueberry는 각 L3의
   strategy_source_digest 기반 cohort 계약을 따른다.
4) actual result는 CONFIRMED order_fills만 사용하고 partial fill·fee·liquidity role·
   reconciliation coverage를 보고한다. legacy ORDER_ASSUMPTION P&L은 섞지 않는다.
5) market_catalog event cluster와 archive별 실제 cadence coverage를 검증한다. 중앙 archive는
  실제 5-minute hot coverage를 재고, papaya/queen/quince/kiwi/blueberry는 Jenkins schedule/run
  manifest에서 산출한 cohort cadence를 사용한다.
6) 기간 내 결과와 동일 evidence의 counterfactual을 비교해 전략 문서의 제안 표를 채운다.
7) evidence gate를 못 통과하면 파라미터 변경 대신 복구/계측 계획만 제안한다.

Jenkins export 블록은 secret과 wallet address를 제거한 legacy cross-check로만 사용한다.
```

여러 전략을 동시에 회고해도 DB, config/code cohort, account slot을 먼저 분리한다. 공통 이벤트에
동시에 노출된 전략 간 결과는 독립 표본으로 세지 않는다.

## 5. 3라운드 교정

1. baseline 또는 사전 등록값으로 충분한 기간 운용
2. evidence gate를 통과한 회고에서 한 번에 최소 knob만 변경
3. 새 `config_hash` cohort로 동일 기간 이상 재검증하고 rollback/채택/폐기 결정

변경 시 `run_audits`가 resolved config를 자동 기록한다. 문서의 `운용 이력`은 의사결정 이유와
승인 기록을 위한 보조 기록이며 유일한 config 원장이 아니다. 병렬 A/B는 account·DB·Jenkins
job을 분리하고 같은 이벤트 노출을 paired analysis로 처리한다.

## 6. 보존과 복구

Jenkins workspace는 durable storage가 아니다. 실행 중 SQLite를 단순 복사하지 말고 online
backup과 checksum manifest를 만든다.

```bash
uv run --project polybot-observability polybot-retro backup \
  --root "$JENKINS_HOME/workspace" \
  --output-dir "$HOME/polybot-db-backup"
```

backup은 workspace 밖과 별도 durable storage에 보관하고 정기적으로 SHA-256,
`PRAGMA quick_check`, row count와 기간을 복구 테스트한다. secret 파일, wallet address,
credential은 audit/report/artifact/commit에 포함하지 않는다.

## 7. 관련 문서

- [A/B 회고 절차](../ab-retro-playbook.md)
- [전략 portfolio](../prediction-market-strategy-portfolio.md)
- 각 `golden-*/STRATEGY.md`
- `slack-data-collector/sql/pb_portfolio_history_v2.sql` — atomic daily writer의 필수
  additive migration; production 적용 여부는 별도로 검증
- `slack-data-collector/sql/pb_portfolio_history_v3.sql` — 당시 account-set을 원자적으로
  적재하도록 도입한 migration. 현재 계정 수를 9로 하드코딩한 운영 계약으로 해석하지 말고,
  v2 적용 여부와 각 run의 expected/observed account set을 별도로 검증
