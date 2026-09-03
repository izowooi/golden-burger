# Golden Plum 프로젝트 지침

이 문서는 `golden-plum/`에만 적용한다. 상위 저장소 규칙과 충돌하면
`/Users/izowooi/git/t1/AGENTS.md`를 우선한다.

## 프로젝트 목적

경기 전체에서 직접 결과 호가의 유일한 선두가 세 번의 1분 관측으로 상승하며 0.75를
처음 통과할 때 추가 상승하는지 종목별로 검증한다. 축구는 3시장/6token, MLB·NBA·
NFL·NHL은 1시장/2token의 서로 다른 versioned profile을 사용한다. King/Queen은 축구와
별도 MLB exact `$5` live A/B, Silver는 축구, Gold는 MLB/NBA/NFL/NHL credential-free
simulation/raw 수집기다.

## Runtime 계약

| Jenkins | runtime job | mode | 유일한 처치 |
|---|---|---|---|
| `polybot-king` | `plum-live-king-90-1m-v1` | live | 절대 TP `0.90` |
| `polybot-queen` | `plum-live-queen-95-1m-v1` | live | 절대 TP `0.95` |
| `polybot-king` | `plum-live-king-mlb-90-1m-v1` | MLB live | 절대 TP `0.90` |
| `polybot-queen` | `plum-live-queen-mlb-95-1m-v1` | MLB live | 절대 TP `0.95` |
| `polybot-silver` | `plum-shadow-silver-1m-v1` | simulation | raw six-book + 반사실 grid |
| `polybot-gold` | `plum-shadow-gold-mlb-1m-v1` | simulation | MLB direct two-book + 반사실 grid |
| `polybot-gold` | `plum-shadow-gold-{nba,nfl,nhl}-1m-v1` | simulation | direct two-book + 반사실 grid |

- 네 job은 1분 cadence를 사용한다.
- live 금액은 정확히 5 USDC이며 event당 filled/불확실 BUY는 한 번뿐이다.
- 수동 wallet position은 봇 DB에 편입하거나 청산하지 않는다.
- Silver/Gold에는 private key, funder address, signature type을 주입하지 않는다.
- runtime spec은 Jenkins job, sport family, mode, lifecycle, target, protocol,
  cadence, deadline과 exact workspace를 한 레코드로 고정한다.
- cohort는 `config_hash × strategy_source_digest × mode × job_name`으로 분리한다.

## 핵심 전략 계약

- 축구 live는 8개 대회와 regular-time 승/무/패 세 명제만 사용한다.
- source `live=true`, `ended=false`와 명시적 경기 시계를 요구하며 0분부터 종료까지
  관측한다. source minute 및 wall-clock age 상한은 없다.
- 같은 token의 최근 3개 snapshot 간격은 각각 90초 이하여야 한다.
- 누적 상승은 0.02 이상, 인접 pullback은 0.01 이하여야 한다.
- 직전 exact `$5` ask VWAP은 0.75 미만, 현재 값은 `[0.75, 0.78]`이어야 한다.
- 현재 direct six-book이 모두 있고 선두 margin이 0.005 이상이어야 한다.
- 공통 SL은 confirmed entry VWAP `-0.15`이며 시간 강제 청산은 없다. TP는 목표가 이상
  bid의 최대 안전 수량을 FOK로 부분 익절할 수 있고, SL은 잔여 전량 FOK만 허용한다.
  TP·SL이 없으면 검증된 resolution까지 유지한다.
- MLB Gold는 exact MLB identity의 whole-game two-team moneyline만 사용하며 inning·prop·
  spread·total·minor league를 제외한다. 이닝을 가짜 축구 minute로 바꾸지 않고 NULL과
  명시적 누락 사유를 저장한다.
- Gamma 선필터는 누적 거래량/유동성 각각 5,000이고 direct sport는 최대 2페이지다.
- Silver/Gold만 `$5/$10/$25/$50/$100/$250/$500` displayed-depth 증액 자료를 저장한다.

상세 계약과 판정 시점은 `STRATEGY.md` 및
축구는 `research/frozen-2026-08-31-full-match-no-time-exit-v2/PREREGISTRATION.md`,
MLB Gold는 `research/frozen-2026-09-01-multisport-mlb-shadow-v3/PREREGISTRATION.md`를
따른다. 공통 주문 실행 보정은
`research/frozen-2026-09-02-partial-profit-exit-v6/PREREGISTRATION.md`를 따른다.
과거 v1과 Golden Coconut epoch는 수정·병합하지 않는다.

## 주요 파일

- `src/polybot/config.py`: job별 동결값과 live/simulation fail-closed 검증
- `src/polybot/strategy/scanner.py`: direct six-book 저장과 token-aligned trend 판정
- `src/polybot/strategy/trader.py`: exact FOK 주문, TP/SL/resolution, event-local 실패 격리
- `src/polybot/db/models.py`, `db/repository.py`: snapshot, trend lineage, order/fill 및
  append-only exit capacity evidence
- `scripts/replay_direct_six_book.py`: 직접 호가 DB의 paired 반사실 grid 재생
- `scripts/verify_external_workspace.py`: Silver/Gold exact external T7 preflight

## 실행

```bash
uv sync --frozen
uv run polybot config --live --job plum-live-king-90-1m-v1
uv run polybot run --live --job plum-live-king-90-1m-v1

POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot run --live --job plum-live-queen-95-1m-v1

unset POLYMARKET_PRIVATE_KEY POLYMARKET_FUNDER_ADDRESS POLYMARKET_SIGNATURE_TYPE
POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot run --simulate --job plum-shadow-silver-1m-v1

uv run polybot run --simulate --job plum-shadow-gold-mlb-1m-v1
```

`--live` 없는 run은 simulation이다. live command는 Jenkins Credentials Binding을
전제로 하며 secret 값을 source나 문서에 넣지 않는다.

## 테스트와 검증

```bash
uv sync --frozen --extra dev
uv run pytest
uv run python -m compileall -q src scripts
```

네트워크 unit test는 Gamma/CLOB mock을 사용한다. 실제 주문 검증은 코드 push 뒤
중지된 Jenkins job의 1회 수동 build로만 수행하고, config·console·DB/order-fill ledger를
모두 확인한 뒤 timer를 켠다. 한 job의 주문 실패가 다른 event를 막는 회귀,
`max_positions` 유령 점유, 부분 체결·fee 공백, 1분 runtime 초과를 반드시 검사한다.

## 분석 규칙

- 과거 Golden Watermelon의 합성 NO와 Golden Peach의 direct-book 자료는 탐색용이다.
- displayed book 재생을 actual fill이나 realized P&L로 표현하지 않는다.
- live 성과는 `order_fills.status='CONFIRMED'`와 완전한 fee evidence만 사용한다.
- 첫 24시간에는 collection/execution health만 판정한다.
- arm당 closed 50, common event 30, evidence gap 0 전에는 금액을 늘리지 않는다.
- Silver/Gold grid를 독립 거래로 세지 말고 `event_id`를 paired unit으로 유지한다.
- Gold 첫 24시간에는 1분 cadence, 50초 deadline, exact 2-token event set, terminal
  follow-up, NULL source minute, capacity JSON과 DB 무결성만 판정한다.
- MLB 해결 경기 100개 전에는 최적 파라미터·실거래 승격·증액을 말하지 않는다.

## 자주 깨지는 부분

- source clock과 벽시계 혼용
- condition별 YES/NO가 섞인 trend history
- current snapshot이 아닌 stale row로 first crossing을 만드는 오류
- fresh six-book leader가 바뀐 뒤 이전 token을 주문하는 오류
- SELL 실패가 전역 entry guard 또는 전체 cycle을 막는 오류
- accepted order를 confirmed fill로 오인하는 오류
- Silver/Gold workspace가 internal disk로 돌아가거나 old Coconut DB와 섞이는 구성
