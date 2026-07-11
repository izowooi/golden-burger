# 월간 전략 회고 Evidence Contract

이 문서는 12개 `golden-*` 전략 회고의 공통 데이터 계약이다. 각 전략별 문서는
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
**현재 설정의 legacy cross-check**일 뿐 과거 실행의 단일 진실이 아니다. 회고 기간을
`config_hash × git_commit × mode × job_name` cohort로 나누고, 각 거래·스냅샷은 가능한 경우
`run_id`로 연결한다. `git_commit='unknown'`, stale `RUNNING`, 실패 run, 실행 간 큰 공백은
결론의 신뢰도를 낮추며 strict audit issue로 처리한다.

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
4. 실제 fill price와 size로 gross P&L을 계산하고, `fee_amount_usdc`가 있으면 차감한다.
   fee amount가 없을 때 임의 수수료를 0으로 채우지 않는다. `fee_rate_bps`와
   `liquidity_role`을 함께 보고 fee completeness를 표시한다.
5. `needs_reconciliation = 1`, 오래된 `last_reconciled_at`, reconciliation error,
   terminal이 아닌 order/trade는 미완결 evidence다. 결과를 확정하지 않는다.
6. `trades.buy_order_id`/`sell_order_id`로 ledger와 연결한다. 두 주문의 confirmed fill
   coverage가 완전하지 않은 `COMPLETED` trade의 `realized_pnl`은 **order-assumption P&L**로만
   표시하고 실현 P&L 합계에서 제외한다.

기존 order ID는 제한된 기간에 best-effort bootstrap될 수 있지만, API에서 더 이상 확인할 수
없는 과거 주문이나 누락된 ID를 복원하지 못한다. legacy 행은 별도 표본으로 두고 “체결 사실
미확인”이라고 명시한다. 지갑 대사가 필요하면 해당 봇 도구가 있는 프로젝트에서 전역 옵션을
subcommand 앞에 둔다.

```bash
uv run tools/wind_down.py --funder 0x... status
```

주소는 보고서·로그·commit에 넣지 않는다.

## 5. 시장 universe와 가격 archive

Gamma 시장 수집은 `/markets/keyset`의 `next_cursor`를 끝까지 따라간다. 고정 offset 2,100개
cap이나 “가장 오래된 시장” 표본을 더 이상 전제로 하지 않는다. 한 sweep의 대상은 cursor가
완주된 응답 중 active, not closed, orderbook enabled, accepting orders이고 전략별 최소
liquidity를 통과한 **그 시점의 qualifying universe**다. 따라서 절대 시장 수를 문서에
고정하지 않고 run별 `markets_scanned`와 catalog coverage를 보고한다.

중앙 archive는 `golden-nectarine`(liquidity ≥ $10k)과 보조 `golden-honeydew`
(liquidity ≥ $15k)의 SQLite다.

- `market_snapshots`: YES probability, liquidity, volume, best bid/ask, spread,
  source update 시각, `run_id`, 수집 시각
- `market_catalog`: condition/market/event ID와 slug, question, end date, outcomes,
  token IDs, tags, fee metadata, first/last seen

`market_catalog.event_id`를 우선 사용해 correlated markets를 묶고, 없을 때만 event slug,
질문·시간 기반 휴리스틱을 사용한다. NO 가격 `1 - YES`는 spread와 fee를 무시한 근사이며
실제 fill을 대체하지 않는다.

“5분 archive”라는 이름만 믿지 않는다. `market_sweeps`와
`market_sweep_memberships`의 cursor-complete qualifying 분모를 기준으로 선택 기간 양 끝 coverage,
expected 5-minute sweep, 시장별 eligible/snapshot 비율, run gap, 거래 condition ID의
snapshot/catalog join coverage를 수치로 보고한다. digest/count/run 연결이 깨진 sweep은 분모에서도
제외하고 CRITICAL evidence issue로 처리한다. coverage가 부족한 구간은 counterfactual 대상에서
제외한다.

## 6. local daily evidence와 내구성

`daily-report/data/daily_evidence.sqlite3`는 이미 API에서 가져온 6계정 결과를 한 transaction으로
저장한다.

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
