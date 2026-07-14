# 새 Polymarket 전략 구현·승격 플레이북

이 문서는 AI가 `golden-*` 전략을 새로 설계할 때도 연구 아이디어에서 운영 회고까지 같은
evidence contract를 갖추게 하는 Definition of Done이다. 과일 코드네임 폴더를 복제하는 것은
출발점일 뿐이며, 아래 gate가 빠진 bot은 simulation이나 live slot으로 승격하지 않는다.

## 1. Research brief를 먼저 고정한다

코드 전에 `STRATEGY.md`에 다음을 쓴다.

- **Hypothesis**: 어떤 participant behavior 또는 market microstructure가 반복 가능한 edge를
  만드는지 한 문장으로 설명한다.
- **Mechanism**: 가격 변화가 단순 correlation이 아니라 왜 entry 이후 payoff로 이어지는지
  설명한다.
- **Falsification**: 폐기 조건을 사전 등록한다. 예: event-cluster 기준 최소 n, fee 포함 기대값,
  maximum drawdown, signal decay, execution coverage, baseline 대비 하회 폭.
- **Universe**: active/closed/orderbook/accepting-orders, liquidity/volume, end-date,
  category/tag, YES/NO/negRisk 처리와 제외 이유를 명시한다.
- **Decision rules**: entry, exit priority, sizing, max exposure, cooldown, resolution/redeem,
  API failure 시 fail-closed 동작을 순수한 규칙으로 적는다.
- **Competing explanation**: 같은 데이터를 설명하는 가장 강한 대안 가설과 이를 가르는
  측정치를 적는다.

서로 반대인 전략을 A/B로 만들 때도 두 bot의 주문을 독립 표본으로 세지 않는다. 동일
`event_id`와 시간창을 paired experiment 단위로 사전 정의한다.

## 2. 독립 backtest artifact

unit test와 backtest는 목적이 다르다. 새 전략에는 재실행 가능한 독립 artifact가 있어야 한다.

- raw source, 수집 시각, query/version, checksum을 manifest에 남긴다.
- train/calibration/test를 시간순으로 나누고 test 구간을 parameter 선택에 사용하지 않는다.
- market creation/end/resolution 시점과 당시 공개 가능했던 field만 사용한다. 미래 `closed`, 최종
  outcome, 사후 수정 metadata, 미래 liquidity로 과거 universe를 거르지 않는다.
- rolling feature는 현재 시점 이전 값만 읽고 embargo/warm-up을 둔다. 같은 event의 파생 시장이
  train과 test 양쪽에 들어가는 leakage를 막는다.
- delisted/resolved/실패 시장도 포함해 survivorship bias를 측정한다.
- bid/ask 또는 보수적 executable price, partial/unfilled scenario, latency, category별 fee와
  maker/taker role을 반영한다. midpoint 100% fill은 optimistic sensitivity일 뿐 baseline이 아니다.
- 결과에는 trade-level output, event-cluster summary, parameter grid, out-of-sample result,
  drawdown, turnover/capital lock, fee/slippage sensitivity를 machine-readable 파일로 남긴다.
- research artifact를 live bot SQLite에 섞지 않는다. seed·기간·명령을 README에 고정한다.

승격 기준은 단일 best grid가 아니라 주변 parameter에서도 edge가 유지되는지, falsification
threshold를 out-of-sample에서 통과하는지다.

## 3. 프로젝트 skeleton과 config contract

새 폴더는 기존 `golden-*` 구조와 uv 표준을 따른다.

```text
golden-<fruit>/
├── AGENTS.md
├── README.md
├── STRATEGY.md
├── config.yaml
├── pyproject.toml
├── src/polybot/
│   ├── api/
│   ├── db/
│   └── strategy/
└── tests/
```

- `pyproject.toml`에 실제 install 가능한 build system/package와 `polybot` entry point를 둔다.
- shared observability를 uv path dependency로 선언한다.

```toml
dependencies = [
  "polybot-observability",
]

[tool.uv.sources]
polybot-observability = { path = "../polybot-observability", editable = true }
```

- `config.yaml`은 보수적인 simulation 기본값과 비밀이 아닌 default만 둔다. 실제 credential,
  wallet address, webhook, database password는 저장소에 넣지 않는다.
- config loader는 `env > config.yaml > code default`를 적용한 뒤 **resolved config 전체를 한 번에
  validate**한다. 숫자 유한성, 0~1 probability, positive amount/시간, min≤max,
  buy/sell/TP/SL 관계, max positions, signature type enum, simulation/live 제약을 검사한다.
- `get_trading_config_mapping()`과 `validate_yaml_config_shape()`을 사용해 root/trading/nested
  typo를 거부한다. YAML boolean을 숫자로, fractional 값을 integer로 암묵 변환하지 않는다.
- 잘못된 key, type, `NaN`/infinity, 모순된 cross-field는 주문 client 생성 전에 실패시킨다.
- config parsing/validation은 경계값과 invalid table-driven test를 둔다. default 변경은 migration
  note와 재현 test 없이 하지 않는다.

## 4. safe simulation과 live safety

- `--simulate`는 명시적으로 `simulation_mode=True`를 override하고 `trades_sim.db`처럼 live DB와
  다른 경로를 사용한다. YAML이 live여도 simulation script가 실주문 client/DB를 열지 않는
  regression test를 둔다.
- simulation order에는 실제처럼 보이는 live order ID나 confirmed fill을 만들지 않는다.
- API/history/volume coverage가 부족하면 signal을 생성하지 않는 fail-closed가 기본이다.
- 주문 전에 band, balance, minimum order size, max exposure를 다시 검사한다.
- GTC가 접수되었다는 이유로 actual fill을 주장하지 않는다. cancel/reconcile/restart 동작과
  partial fill 정책을 설계한다.
- live promotion 전 별도 저액 account와 isolated DB/job에서 shadow 또는 simulation 기간을 거친다.
  production slot 재사용은 effective deployment record 없이는 금지한다.

## 5. 필수 observability

### 5.1 run audit

`Bot.run()`을 `RunAudit`로 감싼다. `RunAudit.start()` 실패 시 trading cycle을 시작하지 않는
fail-closed 동작을 유지한다.

```python
from polybot_observability import RunAudit

audit = RunAudit.start(config, strategy_name="golden-<fruit>")
try:
    gamma.sweep_attestations.clear()
    reconciliation = clob.reconcile_order_ledger()
    if reconciliation["errors"]:
        raise RuntimeError("unresolved execution evidence")
    stats = run_cycle()
    stats["order_reconciliation"] = reconciliation
    stats["market_sweeps"] = gamma.get_sweep_summaries()
except Exception as error:
    audit.fail(error)
    raise
else:
    audit.succeed(stats)
```

`strategy_configs`에는 secret을 제거한 resolved trading config가, `run_audits`에는 run ID,
mode/job, config hash, Git commit, 상태와 cycle/DB summary가 남아야 한다. 새 config field가
secret-like인지 redaction test를 추가한다.

### 5.2 execution ledger

CLOB wrapper에 DB path와 strategy name을 전달해 `ExecutionLedger`를 사용한다.

- BUY/SELL submission intent와 응답을 `order_submissions`에 남긴다.
- 매 cycle/restart에 pending order를 조회해 `order_status_events`와 `order_fills`를 갱신한다.
- actual result는 `CONFIRMED` fill의 actual size/price만 사용하고 partial fill을 합산한다.
- maker/taker role, fee rate/amount, transaction status와 reconciliation error를 보존한다.
- ledger가 terminal이 될 때까지 `needs_reconciliation`을 해제하지 않는다.
- credential/API response 전체를 저장하거나 exception에 노출하지 않는다.

기존 `trades` 상태 머신이 주문 접수 시 HOLDING/COMPLETED를 기록한다면 문서와 회고에서 이를
decision/order-assumption record로 명시한다. 확인되지 않은 `realized_pnl`을 actual 결과로 쓰지
않는다.

### 5.3 market archive와 catalog

Gamma universe는 `/markets/keyset`의 `next_cursor`를 끝까지 따라가고 cursor 반복은 실패시킨다.
active, not closed, orderbook enabled, accepting orders와 전략별 liquidity를 적용한다. 고정 2,100
offset cap을 다시 넣지 않는다.

rolling signal이나 counterfactual에 가격 history가 필요하면 다음을 저장한다.

- `market_snapshots`: YES probability, liquidity, volume, bid/ask/spread, source timestamp,
  collecting `run_id`, timestamp
- `market_catalog`: condition/market/event ID와 slug, question, end date, outcomes/token IDs,
  tags, fee metadata, first/last seen
- `market_sweeps`: cursor completion, page/raw/qualified/excluded count, filters, qualified-set digest,
  collecting `run_id`
- `market_sweep_memberships`: sweep별 qualifying condition과 snapshot eligible/saved 결과

한 sweep의 snapshot/catalog/qualified membership은 한 transaction으로 저장하고
`(condition_id, timestamp)`와 run index를 둔다. 보존
기간은 lookback + holding + 월간 회고 지연을 덮어야 하며 최소 60일 중앙 archive를 전제로 한다.
5-minute cadence와 cursor completion은 매번 측정할 수 있어야 한다.

## 6. test matrix

최소 test는 다음을 포함한다.

- signal pure functions: 모든 경계, insufficient history/coverage, YES/NO 변환, exit priority
- config: default load, env override, unknown key, invalid YAML numeric type/range/cross-field/signature
- simulation isolation: live YAML에서도 sim DB와 sim order만 사용
- Gamma contract: multi-page cursor, dedupe, repeated cursor, active/tradable/liquidity filter,
  catalog parsing
- CLOB contract: accepted-but-unfilled, partial fill, `MATCHED`→`CONFIRMED`, cancel/fail,
  maker/taker와 fee, retry/restart idempotency
- DB migration: 기존 SQLite에 additive schema 적용, snapshot+catalog batch atomicity
- run audit: success/failure/stale/secret redaction/Git/config cohort
- lifecycle: 미설정 `active`의 기존 진입 경로, `close_only`의 청산 유지+신규 스캔/BUY 차단,
  `archive_only`의 주문 전면 차단+기존 아카이브 Phase 유지
- end-to-end dry cycle: fake Gamma/CLOB에서 signal→submission→reconciliation→run summary

검증 명령은 프로젝트 README에 고정한다.

```bash
uv sync --frozen --extra dev
uv run pytest
uv run polybot config
```

live promotion 직전에는 `polybot-observability` 자체 test와 월간 strict audit를 함께 실행한다.

## 7. Jenkins 운영 계약

- 모든 전략은 `POLYBOT_LIFECYCLE_MODE`를 `active`(기본), `close_only`, `archive_only`로
  지원한다. 환경변수를 설정하지 않은 live job은 반드시 기존 `active` 경로와 같아야 한다.
  퇴역 절차와 GTC BUY 취소·잔여 포지션 게이트는
  [전략 퇴역 플레이북](strategy-wind-down-playbook.md)을 따른다.
- 같은 SQLite를 쓰는 build는 `disableConcurrentBuilds()` 또는 동등한 single-writer 보장이
  있어야 한다. job 이름은 stable하고 DB namespace와 일치시킨다.
- workspace wipe, SCM cleanup, ephemeral agent가 DB 원본을 없애지 않도록 data directory를
  분리한다.
- build 종료 후 SQLite online backup을 만들고 checksum manifest와 sanitized log/audit를
  archive한다. artifact만 의존하지 말고 workspace 밖 durable storage에도 복제한다.
- Freestyle `Execute shell`의 `sh -x`/`sh -xe`에서 private key를 inline `export`하지 않는다.
  Jenkins **Credentials Binding**(Secret text/file)으로 주입하고, secret을 참조하는 shell 구간은
  첫 줄부터 `set +x`를 적용한다. console dump도 credential로 간주해 저장·공유하지 않는다.
- backup restore를 정기적으로 실행해 SHA-256, `PRAGMA quick_check`, 기간/table/count를 확인한다.
- log에는 `run_id`, config hash prefix, Git commit, universe count, skip/fill/reconciliation summary를
  남기되 credential, wallet address, Authorization header를 남기지 않는다.
- failed telemetry, DB lock, incomplete config validation은 live cycle을 실패시킨다. 정상 0건/0잔고
  report로 위장하지 않는다.

## 8. account·daily-report·deployment 계약

새 live 또는 test slot을 추가하기 전에 다음을 한 변경으로 맞춘다.

- `daily-report`의 configured account slot과 display name
- `slack-data-collector`의 account catalog 및 Slack schema contract
- Supabase `pb_algorithm_accounts`의 account ID/display/strategy mapping
- Jenkins credential binding과 job 이름
- dashboard label/data-quality 기대치
- root index와 전략 portfolio/retro 문서

daily report는 expected account 집합 전체가 성공한 `COMPLETE` run만 정상 snapshot으로 보낸다.
한 계정 오류를 0잔고로 저장하거나 정상 Slack 메시지로 보내지 않는다. 이미 fetch한 position은
wallet address/secret 없이 `daily_evidence.sqlite3`에 보존한다.

slot의 strategy가 바뀌면 mutable account label만 덮어쓰지 않는다.
`pb_strategy_deployments` migration이 production에 적용되고 effective range, Git commit,
config hash가 기록된 뒤 배치한다. `pb_snapshot_runs`/`pb_external_cash_flows`도 SQL 파일 존재와
실제 migration·backfill 완료를 구분한다.

## 9. retrospective와 dashboard 준비

새 전략과 동시에 `docs/retro/golden-<fruit>.md`를 만든다. 프롬프트에는 반드시 다음이 있다.

- `docs/retro/EVIDENCE_CONTRACT.md` 선행
- explicit `REVIEW_START`/`REVIEW_END`
- `polybot-retro audit --strict` gate
- config/Git/mode/job cohort
- confirmed fill/partial/fee/reconciliation coverage
- market catalog/event cluster/5-minute coverage
- 전략별 실적 SQL, falsification metric, counterfactual grid
- legacy와 post-instrumentation 분리, 표본 부족 시 tuning 금지

dashboard에는 account별 실제 date range, latest timestamp/freshness, missing days,
account sum↔portfolio total reconciliation, selected-account KPI semantics를 표시한다. 시간축 gap을
선으로 이어 실제 데이터처럼 보이게 하지 않는다. cash flow가 없으면 단순 NAV 변화와 TWR을
구분한다.

## 10. promotion gates

| 단계 | 통과 조건 | 실패 시 |
|---|---|---|
| Research | 사전 등록 hypothesis/falsification, 독립 OOS artifact, leakage audit | 연구 수정/폐기 |
| Code | config validation, fail-closed signal, full test matrix | simulation 금지 |
| Simulation | live isolation, 충분한 universe/cadence, execution model sensitivity | 계측/모델 보완 |
| Shadow/test slot | run/config provenance 100%, pending reconciliation 없음, daily contract complete | 기간 연장/복구 |
| Small live | confirmed BUY/SELL fill과 fee coverage, risk limits, durable backup/restore | 즉시 중단/축소 |
| Scale | strict retro gate, event-effective n, fee 포함 OOS/live edge, drawdown 기준 통과 | 유지/rollback/폐기 |

승격은 “수익이 났다”가 아니라 **사전 등록한 가설이 재현 가능한 evidence로 살아남았다**는
결론이다. 계측이 비어 있거나 legacy assumption에 의존하면 더 오래 운용하는 것이 아니라 먼저
관측 계약을 고친다.
