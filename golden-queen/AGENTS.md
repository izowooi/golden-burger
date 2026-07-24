# L3 AGENTS.md — golden-queen

`golden-queen`은 Polymarket **Crown Momentum** 전략 봇이다. 표준 이진 시장의 YES가
보존된 연속 snapshot에서 처음 0.90을 상향 돌파할 때 0.90–0.94에서 진입하고, 미해결
상태에서 0.98 익절 또는 0.85 손절을 시도한다. 스포츠는 기본 포함하며 경기 전과 경기
중 cohort를 분리한다.

- 상위 규칙: L2 `/Users/izowooi/git/t1/AGENTS.md`, L1 `/Users/izowooi/git/AGENTS.md`
- 전략 계약: `STRATEGY.md`
- 실행·환경변수: `README.md`, `config.yaml`, `.env.example`
- 회고 계약: `../docs/retro/EVIDENCE_CONTRACT.md`, `../docs/retro/golden-queen.md`

## 실행과 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv run polybot config --job queen-a
uv run polybot run --simulate --job queen-sim
uv run python scripts/backtest.py <immutable.csv> --output-dir <artifact-dir> --train-fraction 0.7
```

시뮬레이션도 시장 조회를 위한 CLOB 인증이 필요하다. 실주문은 안전 기본값인 simulation을
CLI `--live`로 명시적으로 해제할 때만 허용한다. simulation/live와 A/B variant는 서로
다른 `job_name`과 DB를 사용한다.

## 변경 불가 전략 계약

- universe는 outcomes가 정확히 `[Yes, No]`이고 price/token이 각각 2개이며 token ID가
  고유한 표준 이진 시장이다. `negRisk is False`가 명시돼야 한다. archive는 누락된
  `event_id`도 보존하지만 신규 진입에는 `event_id`가 필수이며 event당 동시에 한 시장만
  보유한다.
- 방향은 YES-only다. `POLYBOT_YES_ONLY=false`나 NO 진입은 validation에서 거부한다.
- 유효한 직전 persisted YES가 0.90 미만이고 현재 YES가 `[0.90, 0.94]`인 Queen 자체
  archive envelope 안의 **first observed upward crossing**만 인정한다. 현재 snapshot은
  이번 sweep에서 commit된 양의 ID여야 하며 직전 snapshot과
  `0 < gap <= 15분`이어야 한다.
- 60일 보존 이력에 0.90 이상 관측이 하나라도 있으면 dip/re-cross를 새 후보로 만들지
  않는다. 최초 crossing은 유동성·거래량·시간·event·fresh book gate에서 거부돼도
  one-shot을 소진한다.
- 비스포츠와 스포츠 경기 전 진입 시계는 `(0h, 24h]`다. `hours_min=0`은 2시간 같은
  추가 하한이 없다는 뜻이다. 스포츠 경기 중에는 이 양의 잔여시간 창을 적용하지 않고,
  upstream에서 여전히 거래 가능할 때 kickoff 후 360분까지만 허용한다.
- 주문 직전 fresh midpoint가 entry band에 있어야 하고, fresh best ask `<= 0.94`,
  spread `<= 0.02`, ask depth 가격창 `best ask + 0.01` 이내, depth safety 배수 1.2,
  최소 5.1 shares를 모두 만족해야 한다.
- 미해결 청산은 fresh YES signal `>= 0.98`이며 실행 가능한 bid도 `>= 0.98`인 익절을
  우선하고, signal `<= 0.85`이면 절대가격 손절을 시도한다. trailing stop과 time exit은
  없다.
- resolution 결과, redeemable 상태, 실제 redeem transaction, SELL fill은 서로 다른
  lifecycle evidence다. 현재 Queen은 resolution 결과까지만 적재하고 실제 redeem
  transaction은 아직 수집하지 않는다. 해결됐다는 이유로 1.00 synthetic SELL 또는 실현
  P&L을 만들지 않는다.

## 스포츠 기본 포함 계약

- 기본 `excluded_categories=[]`이므로 스포츠를 keyword heuristic으로 제외하지 않는다.
  `POLYBOT_EXCLUDED_CATEGORIES`는 Gamma tag slug/label의 대소문자를 무시한 exact match다.
  따라서 `sports` 하나가 `nba`, `soccer` 같은 별도 태그까지 포괄한다고 가정하지 말고,
  제외하려는 태그를 comma로 각각 명시한다.
- 경기 전 스포츠는 `gameStartTime`까지의 시간을 쓰고, 비스포츠는 `endDate`를 쓴다.
- 스포츠인데 `gameStartTime`이 없으면 기본적으로 `endDate`를 fallback 시계로 사용한다.
  `POLYBOT_REJECT_SPORTS_WITHOUT_GAME_START=true`는 명시적인 strict override다.
- `gameStartTime`은 경기 종료 시각이 아니다. 경기 종료 몇 분 전으로 해석하지 않으며,
  `pregame`과 `in_play` 결과를 별도 cohort로 평가한다.

## 규모와 archive 계약

- 기본 주문액은 $100, 동시 포지션 20, event당 1, cycle당 신규 1, 재진입 cooldown
  168시간이다. open-notional cap은 주문액의 10배이며 주문액 hard cap은 $1,000이다.
- 주문액별 실효 유동성은 `max($10,000, 주문액 / 0.001)`, 24시간 거래량은
  `max($2,000, 주문액 / 0.02)`다. 실제 same-snapshot CLOB ask depth도 요청 수량의
  1.2배를 충족해야 한다. 증액할 때 이 자동 파생 gate를 우회하지 않는다.
- 자체 archive는 표준 이진 YES `>= 0.80`, scheduled/pregame `<= 72h`, Gamma 요청
  envelope 유동성 `>= min(POLYBOT_MIN_LIQUIDITY, $1,000)`, volume `>= $0`를 60일
  보존한다. 기본 envelope는 $1,000이며, archive baseline과 실제 entry gate를 혼동하거나
  entry와 함께 좁히지 않는다.

## lifecycle과 evidence

- 매 cycle은 broad own archive를 먼저 원자적으로 저장한다. 불완전하거나 잘못된 원자적
  sweep/archive, 잘못된 reconciliation 통계, 전파된 reconciliation 예외, RunAudit 실패는
  전체 cycle을 fail closed한다. 개별 시장의 lineage/event/book/depth 누락은 그 시장만
  거부하고, 개별 미완료 주문의 대사 오류는 해당 `token_id × side` 신규 주문만 격리한 채
  cycle을 계속한다.
- `active`: archive + reconcile + exit + entry.
- `close_only`: archive + reconcile + exit, 신규 BUY 없음.
- `archive_only`: cycle 시작 시 execution ledger의 읽기·대사는 수행한 뒤 archive를
  저장한다. 그 뒤 신규 주문이나 trade-position lifecycle mutation은 수행하지 않는다.
- GTC `accepted`/`live`는 체결이 아니다. 실제 성과와 포지션은
  live cohort에서 `order_fills.status='CONFIRMED'`의 정확한 size/price/fee만으로 확정하고,
  simulation 결과는 별도 hypothetical cohort로 유지한다.

## A/B 격리 계약

- A는 기본 `POLYBOT_ENTRY_HOURS_MAX=24`, B는 `12`만 바꾼다. archive horizon은 둘 다
  72시간으로 고정하고 그 밖의 commit, cadence, 금액, threshold, risk, sports/in-play,
  lifecycle을 동일하게 유지한다.
- 서로 다른 stable Jenkins job, `--job`, DB, wallet/account/funder/credential을 쓴다.
  같은 wallet이면 두 봇이 상대 노출을 알 수 없으므로 live A/B 격리가 아니다.
- 비교 단위는 독립 market 수가 아니라 `event_id × crossing time window` pair/cluster다.
  `config_hash × git_commit × mode × job_name` cohort와 CONFIRMED fill coverage로
  평가하며 simulation과 live를 섞지 않는다.

## 변경 시 동기화와 금지사항

- 전략 수치를 바꾸면 `config.yaml`, `src/polybot/config.py`, 순수 signal/timing tests,
  `STRATEGY.md`, `README.md`, retro grid, offline replay grid를 함께 갱신한다.
- midpoint나 주문 접수 응답을 fill로 간주하거나 missing snapshot/event/book/depth/API
  evidence를 추정으로 채우지 않는다.
- A/B에서 여러 노브를 동시에 바꾸거나 동일 job/SQLite/wallet을 재사용하지 않는다.
- private key, funder 실값, API/access secret token, `.env`, `data/`, DB, 로그를
  커밋하지 않는다. 공개 market `token_id`는 이 금지 대상의 secret token이 아니다.
  Jenkins는 Credentials Binding을 쓰고 secret 참조 전부터 `set +x`를 적용한다.
- 다른 `golden-*` 폴더는 read-only로 취급한다.

`CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.
