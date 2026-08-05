# L3 AGENTS.md — golden-melon

`golden-melon`은 Polymarket **Resolution Sprint** 전략 봇이다.
`golden-cherry`(Resolution Momentum)의 재설계이며, cherry 671건 실측 진단이 설계 근거다.

**A/B/C 처치축은 `min_volume_24h` 하나다.** 진입 밴드·시계·배리어·금액·실행모드는
전 팔 동일하다. A=$20,000 / B=$50,000 / C=$150,000.

- 상위 규칙: L2 `/Users/izowooi/git/t1/AGENTS.md`, L1 `/Users/izowooi/git/AGENTS.md`
- 전략 계약: `STRATEGY.md`
- 실행·환경변수: `README.md`, `config.yaml`, `.env.example`
- 회고 계약: `../docs/retro/EVIDENCE_CONTRACT.md`, `../docs/retro/golden-melon.md`

## 실행과 검증

```bash
uv sync --frozen
uv run pytest
uv run python main.py config --job polybot-melon-mid
uv run python main.py run --simulate --job melon-sim
uv run python main.py run --live --job polybot-melon-mid
```

실주문은 안전 기본값인 simulation을 CLI `--live`로 명시적으로 해제할 때만 허용한다.
simulation/live와 A/B/C variant는 서로 다른 `job_name`과 DB를 사용한다.

> `main.py config`는 `--live`를 받지 않으므로 **항상 simulation 기준으로 출력**한다.
> 표시되는 DB 경로(`trades_sim.db`)와 `Simulation: True`는 실제 run의 모드가 아니다.
> 실제 모드는 `[RUN_AUDIT] ... mode=live`로 확인한다.

## Melon 고유 계약

- **처치축은 `min_volume_24h`다.** 30일 A/B/C 중 절대 바꾸지 않는다. 나머지 파라미터를
  팔별로 다르게 두면 처치가 둘이 되어 실험이 무효다.
- `buy_amount_usdc` 기본 **$5는 선호가 아니라 하한**이다. CLOB 최소 주문이 5.0 shares라
  $1은 진입가 전 구간에서 1.1주가 되어 전량 거절된다. `config.py`가
  `buy_amount_usdc / prob_max >= min_order_size + buffer`를 validation에서 강제한다.
  이 제약이 `prob_max`의 상한(0.98)도 정한다.
- **trailing stop과 time exit은 없다.** cherry에서 trailing 122건 −3.94%였고, 무엇보다
  `max_price`를 매수가로 초기화하는 trailing이 명목 손절을 도달 불가능하게 만들었다.
  다시 넣지 않는다.
- **절대가 손절 0.78은 파라미터로 꼬리를 막지 못한다.** cherry의 `stop_loss` 52건 중
  **31건(60%)이 매수 후 30분 이내**에 평균 −24.6%(최악 −99.3%)로 발생했다. 5분 폴링으로
  잡을 수 없는 가격 점프다. 꼬리 통제는 **금액($5)과 포지션 수(20)** 로 한다.
  손절값을 조여서 이 문제를 풀려고 하지 않는다.
- **낙폭 kill switch가 코드에 있다.** 경제손익(확정 `realized_pnl` + 해결
  `settlement_pnl_assumption`)이 `experiment_capital_usdc x max_drawdown_stop`
  (기본 $100 × 20% = **$20**) 이하가 되면 `Trader._drawdown_stop_triggered()`가
  신규 진입을 차단한다. 청산은 계속된다. 우회하거나 비활성화하지 않는다.
- `execution_mode`는 **melon의 처치가 아니다.** quince가 그 축을 A/B/C로 검정 중이므로
  `nearest`로 고정한다. 두 실험이 같은 축을 동시에 흔들지 않는다.
- `intent_autoresolve`는 env가 아니라 `TradingConfig` 필드다. `config_hash`에 담겨야
  동작이 다른 두 run이 같은 cohort로 묶이지 않는다.
- **배리어로 edge를 만들려 하지 않는다.** 밴드 중앙 0.89에서 손익분기 승률 58.0% =
  martingale 도달확률 57.9%다. 배리어 재배치는 quince §5-b에서 보정 p=0.27로 기각됐다.

## 변경 불가 전략 계약

- universe는 outcomes가 정확히 `[Yes, No]`이고 price/token이 각각 2개이며 token ID가
  고유한 표준 이진 시장이다. `negRisk is False`가 명시돼야 한다. archive는 누락된
  `event_id`도 보존하지만 신규 진입에는 `event_id`가 필수이며 event당 동시에 한 시장만
  보유한다.
- 방향은 YES-only다. `POLYBOT_YES_ONLY=false`나 NO 진입은 validation에서 거부한다.
- 유효한 직전 persisted YES가 **0.85 미만**이고 현재 YES가 **`[0.85, 0.93]`** 인 Melon
  자체 archive의 **first observed upward crossing**만 인정한다. 현재 snapshot은 이번
  sweep에서 commit된 양의 ID여야 하며 직전 snapshot과 `0 < gap <= 15분`이어야 한다.
- 60일 보존 이력에 0.85 이상 관측이 하나라도 있으면 dip/re-cross를 새 후보로 만들지
  않는다. 최초 crossing은 유동성·거래량·시간·event·fresh book gate에서 거부돼도
  one-shot을 소진한다.
- 비스포츠와 스포츠 경기 전 진입 시계는 **`(0h, 72h]`** 다. `hours_min=0`은 추가 하한이
  없다는 뜻이다. 스포츠 경기 중에는 이 창을 적용하지 않고 kickoff 후 360분까지 허용한다.
- 주문 직전 fresh midpoint가 entry band에 있어야 하고, fresh best ask `<= 0.93`,
  spread `<= 0.02`, ask depth 가격창 `best ask + 0.01` 이내, depth safety 배수 1.2,
  최소 5.1 shares를 모두 만족해야 한다.
- 미해결 청산은 fresh YES signal `>= 0.97`이며 실행 가능한 bid도 `>= 0.97`인 익절을
  우선하고, signal `<= 0.78`이면 절대가격 손절을 시도한다.
- resolution 결과, redeemable 상태, 실제 redeem transaction, SELL fill은 서로 다른
  lifecycle evidence다. 현재 Melon은 resolution 결과까지만 적재하고 실제 redeem
  transaction은 수집하지 않는다. 해결됐다는 이유로 1.00 synthetic SELL이나 실현 P&L을
  만들지 않는다.

## 스포츠 기본 포함 계약

- 기본 `excluded_categories=[]`이므로 스포츠를 keyword heuristic으로 제외하지 않는다.
  cherry 실측에서 스포츠(−2.96%)와 비스포츠(−3.27%)는 차이가 없었다.
- `POLYBOT_EXCLUDED_CATEGORIES`는 Gamma tag slug/label의 대소문자 무시 exact match다.
  `sports` 하나가 `nba`, `soccer`를 포괄한다고 가정하지 말고 각각 명시한다.
- 경기 전 스포츠는 `gameStartTime`, 비스포츠는 `endDate`를 시계로 쓴다.
  `gameStartTime`이 없으면 `endDate`로 fallback한다.

## 규모와 archive 계약

- A/B/C 기본 주문액은 **$5**, 동시 포지션 20, event당 1, cycle당 신규 1, 재진입 cooldown
  168시간이다. open-notional cap은 주문액의 10배($50)이며 주문액 hard cap은 $1,000이다.
- 주문액별 실효 유동성은 `max($20,000, 주문액 / 0.001)`, 24시간 거래량은
  `max(min_volume_24h, 주문액 / 0.02)`다. 실제 same-snapshot CLOB ask depth도 요청
  수량의 1.2배를 충족해야 한다. **증액할 때 이 자동 파생 gate를 우회하지 않는다** —
  cherry는 주문/유동성이 21.7%까지 갔다.
- 자체 archive는 표준 이진 YES `>= 0.75`, scheduled/pregame `<= 168h`를 60일 보존한다.
  archive는 진입 gate보다 반드시 넓어야 최초 교차를 증명할 수 있다.
- **거절된 후보도 자산이다.** `skipped_markets`의 거절 사유와 당시 volume/liquidity/
  잔여시간이 남으므로, 30일 뒤 실행하지 않은 threshold의 반사실을 오프라인으로 만든다.

## lifecycle과 evidence

- 매 cycle은 broad own archive를 먼저 원자적으로 저장한다. 불완전한 sweep/archive,
  잘못된 reconciliation 통계, RunAudit 실패는 전체 cycle을 fail closed한다.
- `active`: archive + reconcile + exit + entry / `close_only`: 신규 BUY 없음 /
  `archive_only`: 읽기·대사 후 archive만.
- GTC `accepted`/`live`는 체결이 아니다. 실제 성과와 포지션은 live cohort에서
  `order_fills.status='CONFIRMED'`의 정확한 size/price/fee만으로 확정한다.

## A/B/C 격리 계약

- A=`polybot-melon-low`($20,000), B=`polybot-melon-mid`($50,000),
  C=`polybot-melon-high`($150,000)다. Jenkins job과 `--job`을 동일 이름으로 고정하고
  각 `data/<job>/trades.db`를 분리한다.
- 세 팔은 서로 다른 wallet/account/funder/credential을 쓰고 cadence는 모두
  `H/5 * * * *`다. 같은 wallet, job 또는 DB를 공유하면 live A/B/C 격리가 아니다.
- **세 팔을 같은 시각에 기동한다.** 팔마다 자기 archive에서 최초 교차를 판정하므로
  시차를 두면 후보 집합이 갈린다.
- 세 팔 모두 $5, `POLYBOT_EXPERIMENT_CAPITAL_USDC=100`, 진입 `[0.85, 0.93]`, 72h,
  0.97/0.78, archive 168h, risk, sports/in-play, lifecycle, Git commit을 동일하게
  유지한다. 다른 것은 `POLYBOT_MIN_VOLUME_24H`뿐이다.
- 비교 단위는 독립 market 수가 아니라 `event_id × crossing time window` pair/cluster다.
  `config_hash × git_commit × mode × job_name` cohort와 CONFIRMED fill coverage로
  평가하며 simulation과 live를 섞지 않는다.
- 30일 1차 종점은 **건당 수익률과 승률**을 손익분기 **58.0%** 와 비교한 것이다.
  팔당 CONFIRMED BUY가 30건 미만이면 판정 불가다.

## 변경 시 동기화와 금지사항

- 전략 수치를 바꾸면 `config.yaml`, `src/polybot/config.py`, signal/timing tests,
  `STRATEGY.md`, `README.md`, `../docs/retro/golden-melon.md`를 함께 갱신한다.
- midpoint나 주문 접수 응답을 fill로 간주하거나 missing snapshot/event/book/depth/API
  evidence를 추정으로 채우지 않는다. `trades.realized_pnl`을 성과로 쓰지 않는다.
- A/B/C에서 여러 노브를 동시에 바꾸거나 동일 job/SQLite/wallet을 재사용하지 않는다.
- trailing stop이나 time exit을 되살리지 않는다.
- private key, funder 실값, API/access token, `.env`, `data/`, DB, 로그를 커밋하지 않는다.
  Jenkins는 Credentials Binding을 쓰고 secret 참조 전부터 `set +x`를 적용한다.
- 새 DB는 자동 `compact-v1`이며 sweep 상세는 24시간 checkpoint, telemetry와 bot 일일 로그는 60일 보존한다. clean build는 새 cohort 시작 시 한 번만 허용한다.
- 다른 `golden-*` 폴더는 read-only로 취급한다.

`CLAUDE.md`는 `@AGENTS.md` 한 줄만 유지한다.
