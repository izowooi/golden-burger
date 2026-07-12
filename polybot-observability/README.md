# Polybot Observability

12개 `golden-*` 전략이 공통으로 사용하는 로컬 관측성 패키지다. 주문 가격이나 전략 판단을
바꾸지 않고, 각 봇의 기존 SQLite DB에 실행 provenance와 실제 CLOB lifecycle evidence를
추가한다.

- `strategy_configs`: 비밀값을 제외한 resolved trading config를 content hash로 중복 제거
- `run_audits`: 실행별 Git commit, mode, 시작/종료 시각, 성공/실패, cycle 통계와 DB 요약
- `order_submissions`: 요청 price/size, order ID, 접수 응답과 대사 상태
- `order_status_events`: 관측한 order status, `original_size`, `size_matched`
- `order_fills`: trade ID별 status, 실제 size/price, liquidity role, fee와 chain 정보
- `market_sweeps` / `market_sweep_memberships`: Gamma keyset 완주와 qualified universe,
  snapshot 적재 여부를 count/digest로 증명하는 중앙 archive denominator

`BotConfig.api` 전체와 funder/private key는 직렬화하지 않는다. 로그에도 config 본문 대신
hash와 run ID만 기록한다. 향후 trading config 안에 secret-like key가 추가되더라도 값은
`<redacted>`로 치환한다.

persisted error와 operator resolution reason도 길이를 제한하고 Authorization/dict assignment,
credentialed DB DSN, GitHub/AWS/OpenAI/Slack/Supabase token, JWT, private-key block을 redaction한다.
token/secret 형태의 public identifier도 보수적으로 가려질 수 있으며, 회고 evidence에는 credential
원문보다 fail-closed 비노출을 우선한다.

관측성 기록은 **fail-closed**다. `RunAudit.start()`가 DB lock, 디스크 오류, schema 오류로
실패하면 trading cycle도 시작하지 않는다. 텔레메트리 없는 실거래를 허용하지 않고,
Jenkins 동시 실행/손상 DB를 조기에 드러내기 위한 안전 규칙이다.

GTC 주문의 `live`/`accepted` 응답은 fill이 아니다. `ExecutionLedger`는 미완료 주문을 다음
cycle에서 다시 조회하고, 연결된 모든 trade가 `CONFIRMED` 또는 `FAILED`가 될 때까지
`needs_reconciliation=1`을 유지한다. 취소 주문도 `size_matched=0`이 명시되지 않으면 미체결로
단정하지 않는다. CLOB v2의 fixed-6 size/amount를 실제 단위로 변환한 뒤 CONFIRMED fill 합계가
각 주문의 `latest_size_matched`와 ±0.000001 안에서 정확히 일치해야 대사가 끝난다. 초과 fill은
CRITICAL evidence corruption으로 분류하고 P&L에서 제외한다. 기존 `trades.realized_pnl`은 양쪽
주문의 실제 confirmed fill 수량이 서로 일치하기 전까지 order-assumption 값이다. legacy
`buy_shares`/`sell_shares`가 달라도 reconciled BUY/SELL 실제 수량이 같으면 실제 수량으로 P&L을
계산하고 legacy mismatch를 별도 표시한다.

단건 order 조회와 current/pre-migration exact order catalog에서 주문이 사라졌다면, bot은
현재 인증 계정의 해당 `token_id` trade catalog 전체 페이지를 한 cycle에 token별 한 번만
조회한다. 복구 대상은 `taker_order_id` 또는 `maker_orders[].order_id`가 pending order ID와
정확히 같은 trade ID뿐이며 price/size/time 유사 매칭은 하지 않는다. 발견한 ID도 exact trade
재조회와 order 상관관계를 다시 검증한 뒤 `order_fills`와 canonical association에 기록한다.
order status 없이 대사를 종결하려면 모든 exact-correlated bucket이 `CONFIRMED`이고 그 합이
submission 응답의 실제 token amount(BUY=`taking_amount`, SELL=`making_amount`)와
±0.000001 안에서 일치해야 한다. 이 경우 `reconciliation_proof`에
`AUTHENTICATED_TOKEN_TRADE_CATALOG_FULL_FILL`을 남긴다. amount 누락, partial/nonterminal fill,
exact match 부재는 모두 fail-closed 상태를 유지한다.

submission의 요청 price/size, order detail의 original/matched size, CONFIRMED fill의 positive
size·`0 < price < 1`·nonnegative integer bucket, fee rate/amount도 finite domain을 통과해야 한다.
MAKER fill은 해당 `maker_orders.order_id` entry, TAKER fill은 `taker_order_id` 또는 명시적 TAKER
증거와 연결되어야 한다. 다른 maker의 top-level size/price를 현재 주문에 귀속하지 않는다.
MAKER/TAKER coverage가 빠지면 strict 회고가 실패한다.

POST timeout/5xx, `success=true`인데 `orderID`가 없는 응답, `success=false`와 `orderID`가 함께 온
모순 응답은 `SUBMIT_OUTCOME_UNKNOWN` evidence로 남기고 현재 cycle을 중단한다. 다음 process에서는
불확실 intent 하나가 전략 전체의 청산·스캔을 영구 중단하지 않는다. `pending_submissions()`는
정상적인 exact-ID 대사를 계속하고, 새 주문 직전에는 같은 `token_id × side` 조합만 국소 격리한다.
따라서 불확실 BUY의 중복 매수와 불확실 SELL의 중복 매도는 차단하면서 다른 시장과 반대 방향의
정상 주문은 계속된다. `assert_execution_ready()`와 strict 회고는 operator 해결 전까지 계속
실패하며 evidence issue를 숨기지 않는다. 자동 해제하지 않으며 operator가 venue 증거를 검토한 뒤
먼저 secret-safe 진단을 조회한다.

```bash
uv run polybot-retro unresolved-intents \
  --db data/default/trades.db \
  --strategy golden-cherry
```

주문 ID가 없으면 같은 Jenkins credential 환경에서 제출 시각 전후의 authenticated user order와
trade history를 read-only로 대조한다. 이 명령은 `derive_api_key`와 GET 요청만 사용하고 DB outcome을
변경하지 않는다. `unique_candidate_order_id`와 `resolution_evidence`가 함께 나올 때만 아래
`ORDER_ID_LINKED` 복구를 진행한다. 빈 후보나 일부 query 실패는 `NO_ORDER_CREATED` 증거가 아니다.

```bash
uv run polybot-retro probe-intent \
  --db data/default/trades.db \
  --strategy golden-cherry \
  --submission-id '<submission-id>' \
  --window-seconds 600
```

Venue history에서 주문이 생성되지 않았음이 확인된 경우에만 workspace 밖 backup과 강한 확인
문구를 사용해 해제한다. 주문 ID를 찾았다면 `ORDER_ID_LINKED`와 exact ID를 지정해 일반 대사 큐로
보낸다.

```bash
uv run polybot-retro resolve-intent \
  --db data/default/trades.db \
  --strategy golden-cherry \
  --submission-id '<submission-id>' \
  --resolution NO_ORDER_CREATED \
  --confirm 'RESOLVE_<submission-id>_AS_NO_ORDER_CREATED' \
  --reason 'authenticated venue history proves no order' \
  --backup-dir ~/.polybot/operator-backups

uv run polybot-retro resolve-intent \
  --db data/default/trades.db \
  --strategy golden-cherry \
  --submission-id '<submission-id>' \
  --resolution ORDER_ID_LINKED \
  --order-id '<order-id>' \
  --confirm 'LINK_<submission-id>_TO_<order-id>' \
  --reason 'exact order found in authenticated venue history' \
  --backup-dir ~/.polybot/operator-backups
```

동일 기능은 아래 Python API로도 사용할 수 있다.

```python
unresolved = ledger.unresolved_submission_outcomes()
ledger.resolve_uncertain_submission(
    unresolved[0]["submission_id"],
    resolution="NO_ORDER_CREATED",  # 또는 ORDER_ID_LINKED + order_id="..."
    reason="venue history와 support case로 미접수 확인",
)
```

단건 주문, exact user-order catalog, pre-migration catalog에서 모두 사라진 accepted/live 주문은
자동으로 미체결 처리하지 않는다. 현재 credential과 workspace ledger의 provenance가 달라졌고,
연결된 trade ID·fill·order status도 전혀 없는 경우에만 아래 operator quarantine 대상이 된다.
먼저 exact 대상 목록을 확인한 뒤 예상 건수와 확인 문구를 함께 지정한다.

`MATCHED` 주문의 `requested_size`가 약 100인데 matched/fill size가 약 `0.0001`처럼 정확히 10⁶
축소된 경우는 quarantine 대상이 아니다. SDK가 이미 사람 단위로 반환한 quantity를 fixed-6으로
다시 나눈 과거 계측 오류다. terminal trade ID와 CONFIRMED fill 합계가 모두 일치하는 건만 아래
명령으로 진단·복구한다.

```bash
uv run polybot-retro quantity-scale-repairs \
  --db data/default/trades.db \
  --strategy golden-date

# 후보가 []이면 mutation 없이 제외 사유와 fill 합계를 출력
uv run polybot-retro quantity-scale-diagnostics \
  --db data/default/trades.db \
  --strategy golden-date

uv run polybot-retro repair-quantity-scale \
  --db data/default/trades.db \
  --strategy golden-date \
  --expected-count 19 \
  --confirm REPAIR_19_CLOB_QUANTITIES_X1000000 \
  --reason "runtime order/fill quantities were double-scaled" \
  --backup-dir ~/.polybot/operator-backups
```

repair는 기존 quantity, 수정 후 quantity, multiplier, reason을 `quantity_scale_repairs`에 남기고
order/fill 대사를 다시 수행한다. 신규 응답은 `original_size × requested_size` 관계로 raw fixed-6과
SDK-normalized quantity를 판별하고 같은 scale을 order와 fill 전체에 적용한다.
과거 빈 fee 응답이 `fee_rate_invalid`로 기록됐더라도 저장된 `fee_rate_bps`가 NULL인 경우만
missing fee evidence로 재분류한다. NULL은 유지되므로 fee coverage가 있는 것으로 간주되지 않는다.
후보별 `repair_mode`는 order와 fill이 함께 축소된 `ORDER_AND_FILL_X1000000`, fill은 이미 정상이고
order만 축소된 `ORDER_ONLY_X1000000` 중 terminal fill 합계로 증명되는 한 가지를 표시한다.

```bash
uv run polybot-retro catalog-gaps \
  --db data/default/trades.db \
  --strategy golden-date

uv run polybot-retro resolve-catalog-gaps \
  --db data/default/trades.db \
  --strategy golden-date \
  --expected-count 19 \
  --confirm ACKNOWLEDGE_19_CLOB_EVIDENCE_GAPS \
  --reason "authenticated current/pre-migration catalogs reviewed" \
  --backup-dir ~/.polybot/operator-backups
```

resolve는 mutation 전에 workspace 밖에 SQLite online backup, `quick_check`, SHA-256 manifest를
만든다. 격리된 행은 `OPERATOR_EVIDENCE_GAP`으로 남으며 이는 미체결 증명이 아니다. live cycle
gate에서만 제외되고 `polybot-retro audit --strict`에는 계속 HIGH evidence issue로 보고된다.

기본 조회가 `[]`이지만 operator가 이전 전략/수동 주문의 연결된 status·trade ID·fill까지 보존한
채 live gate에서 제외하기로 명시했다면 아래 별도 override를 사용한다. 강한 확인 문구 없이는
실행되지 않으며 기존 evidence 행은 삭제하지 않는다.

`--include-evidence-linked` 조회 결과에는 `fill_statuses`, `confirmed_fill_size`,
`expected_fill_size`, `fill_domain_errors`, `completion_ready`, `completion_blockers`가 함께 나온다.
`MATCHED`/`MINED`/`RETRYING` fill은 Polymarket finality 전 상태이므로 격리하거나 성공 처리하지 않고
`CONFIRMED` 또는 `FAILED`가 될 때까지 재조회한다.

POST 응답의 `makingAmount`/`takingAmount`는 로컬 order intent의 token 수량과 대조해 fixed-6 또는
human-unit 표현을 판별한다. 과거 human-unit을 한 번 더 10⁶으로 나눈 ledger 값도 대사 시 양쪽
amount와 주문 가격이 일치하는 경우에만 복구해 사용한다. Exact associated trade가 전부 `FAILED`면
미체결로 종결하고, 낙관적으로 기록했던 BUY `HOLDING`은 `UNFILLED`로, SELL `COMPLETED`는
`HOLDING`으로 원자 복구한다. `CONFIRMED`와 `FAILED`가 섞이면 자동 판단하지 않고 fail closed한다.

```bash
uv run polybot-retro catalog-gaps \
  --db data/default/trades.db \
  --strategy golden-date \
  --include-evidence-linked

uv run polybot-retro resolve-catalog-gaps \
  --db data/default/trades.db \
  --strategy golden-date \
  --expected-count 19 \
  --include-evidence-linked \
  --confirm ACKNOWLEDGE_19_CLOB_EVIDENCE_GAPS_WITH_LINKED_EVIDENCE \
  --reason "previous/manual order evidence reviewed and quarantined" \
  --backup-dir ~/.polybot/operator-backups
```

Jenkins가 `sh -x`/`sh -xe`로 실행되면 inline `export`의 private key가 콘솔에 노출된다. secret은
Credentials Binding으로 주입하고 shell의 첫 명령부터 `set +x`를 적용한다.

ledger schema upgrade는 `BEGIN IMMEDIATE` 아래 개별 SQLite DDL로 copy/drop/rename을 한 transaction에
묶는다. 이전 비원자적 migration에서 남은 `order_fills_v2`도 복구하며, 중간 실패는 원본 table과
row를 그대로 rollback한다.

## 봇 연동

각 전략 `pyproject.toml`에서 이 폴더를 uv path dependency로 선언하고, 봇의 `run()`을
다음처럼 감싼다.

```python
from polybot_observability import RunAudit

audit = RunAudit.start(self.config, strategy_name="golden-example")
try:
    stats = self.run_cycle()
except Exception as error:
    audit.fail(error)
    raise
else:
    audit.succeed(stats)
```

`ClobClientWrapper`는 같은 DB 경로로 `ExecutionLedger`를 만들고, 모든 limit submission을
기록한 뒤 cycle 시작 시 `get_order`와 trade 조회로 대사한다. 계측 도입 전에 저장된 최근
order ID는 best-effort로 등록하지만, API에서 사라진 과거 체결을 추정해 만들지는 않는다.

## 회고 readiness와 backup

```bash
uv run polybot-retro audit \
  --root /absolute/jenkins/workspace \
  --days 30 \
  --as-of 2026-07-11 \
  --output-dir /durable/retro/2026-07-11 \
  --strict

uv run polybot-retro backup \
  --root /absolute/jenkins/workspace \
  --output-dir /durable/polybot-db-backup
```

기본 `--root` 탐색은 live `trades.db`만 포함한다. simulation까지 보고 싶을 때만
`--include-sim`을 추가한다. `trades_sim.db`는 `simulation_assumption` cohort로 분리되며 실제
fill/P&L readiness를 주장하지 않고 `--strict`의 live 실패 수에도 합산하지 않는다.
단, filename과 `run_audits.mode`/`order_submissions.simulation` evidence가 충돌하면
`cohort_mode_mismatch` CRITICAL이며, sim filename 안의 live evidence도 live strict에 포함한다.

`audit`는 SQLite integrity와 hash, 기간 제한 trade/run/config cohort, confirmed fill 수량·가격·
fee coverage, 미완료 주문, archive 기간·5분 bucket, Gamma sweep membership count/digest,
qualified→snapshot-eligible 비율, 전략별 최소 history depth, snapshot probability/order-book
value domain, event/endDate/outcomes/token/tags/fee catalog completeness를 JSON과 Markdown으로
내보낸다. `--strict`는 `CRITICAL`/`HIGH` issue에서 exit 1을 반환하므로 해당 구간을 근거로 한
파라미터 조정을 중단해야 한다. `backup`은 SQLite online backup과 SHA-256 manifest를 만든다.
각 copy에 `PRAGMA quick_check`도 다시 실행한다. 생성 디렉터리는 Jenkins workspace 밖의 내구성
있는 저장소로 복제해야 한다.

## 검증

```bash
uv sync --frozen --extra dev
uv run pytest
```

월간 Jenkins 회고 bundle 명령은 `docs/retro/README.md`를 따른다.
사실 인정 기준은 `docs/retro/EVIDENCE_CONTRACT.md`가 우선한다.
