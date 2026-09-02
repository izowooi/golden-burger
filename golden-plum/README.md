# Golden Plum

경기 시작부터 종료까지 직접 결과 호가를 1분마다 관측하고, 유일한 선두가 3회 상승
확인 뒤 0.75를 처음 통과할 때 추가 상승하는지 검증하는 종목별 전략입니다. 축구는
HOME/DRAW/AWAY의 직접 YES·NO 6개 호가를 사용하고, MLB·NBA·NFL·NHL은 두 팀이 직접
표시된 moneyline 2개 호가를 사용합니다.

현재 실거래는 축구 King/Queen만 허용합니다. Silver는 축구를 수집하고, Gold는 MLB·
NFL·NBA를 서로 다른 DB로 수집합니다. NHL은 코드 구조만 준비되어 Jenkins와 실거래에서는
꺼져 있습니다.

## 구성

| Jenkins | runtime job | 역할 |
|---|---|---|
| `polybot-king` | `plum-live-king-90-1m-v1` | live A, 절대 TP 0.90 |
| `polybot-queen` | `plum-live-queen-95-1m-v1` | live B, 절대 TP 0.95 |
| `polybot-silver` | `plum-shadow-silver-1m-v1` | credential-free raw/simulation |
| `polybot-gold` | `plum-shadow-gold-mlb-1m-v1` | credential-free MLB raw/simulation |
| `polybot-gold` | `plum-shadow-gold-nfl-1m-v1` | credential-free NFL raw/simulation |
| `polybot-gold` | `plum-shadow-gold-nba-1m-v1` | credential-free NBA raw/simulation |

공통 entry는 baseline `$5` 기준 `[0.75,0.78]` first crossing, stop은 confirmed entry
-0.15입니다. King/Queen의 현재 live 목표는 `$5`라 기존 A/B 처치는 바뀌지 않습니다. 나중에
목표 금액을 올리면 같은 fresh book에서 전량 체결 가능한 가장 큰 사다리 금액으로 자동 축소한
FOK 한 건만 제출합니다.
시간 강제 청산은 없고 익절·손절·검증된 resolution로만 종료합니다. live와 shadow 모두
direct six-book을 저장하며 합성 NO를 사용하지 않습니다. Silver와 Gold는 추가로
`$5/$10/$15/$20/$25/$30/$40/$50/$75/$100/$150/$200/$250/$500/$750/$1000` displayed-depth 증액
자료를 저장합니다.
이 증액 계산은 같은 CLOB 응답을 재사용하므로 추가 API 호출을 만들지 않습니다.

catalog/snapshot/trade에는 종목·리그·원본 tag를 저장합니다. live trade에는 목표 금액, 실제
선택 금액, 가격 상한 안에서 표시 호가로 가능한 최대 금액과 축소 사유도 남깁니다. 익절은
목표가 이상 bid에서 가능한 최대 안전 수량을 FOK로 나누어 처리할 수 있고, 확인된 잔여는
`HOLDING`으로 이어갑니다. 손절은 확인된 잔여 전량만 FOK로 처리합니다. fresh exit book과
선택·잔여·최대 실행 가능 수량/금액은 `exit_execution_observations`에 append-only로 남깁니다.

## 설치·테스트

```bash
uv sync --frozen --extra dev
uv run pytest
```

## 실행

실거래는 private key와 funder address를 Jenkins Credentials Binding으로 제공한 뒤
명시적인 `--live`를 사용합니다.

```bash
uv run polybot config --live --job plum-live-king-90-1m-v1
uv run polybot run --live --job plum-live-king-90-1m-v1

POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot config --live --job plum-live-queen-95-1m-v1
POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot run --live --job plum-live-queen-95-1m-v1
```

Silver와 Gold에는 credential을 주입하면 안 됩니다. runtime 이름이 종목·mode·protocol·
target·workspace를 원자적으로 고정하므로 각 Gold runtime은 별도 target 환경변수 없이
자기 종목 profile을 선택합니다.

```bash
unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE

POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot config --simulate --job plum-shadow-silver-1m-v1
POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot run --simulate --job plum-shadow-silver-1m-v1

uv run polybot config --simulate --job plum-shadow-gold-mlb-1m-v1
uv run polybot run --simulate --job plum-shadow-gold-mlb-1m-v1

uv run polybot config --simulate --job plum-shadow-gold-nfl-1m-v1
uv run polybot run --simulate --job plum-shadow-gold-nfl-1m-v1

uv run polybot config --simulate --job plum-shadow-gold-nba-1m-v1
uv run polybot run --simulate --job plum-shadow-gold-nba-1m-v1
```

## 과거/수집 자료 재생

```bash
uv run python scripts/replay_direct_six_book.py \
  --sport-family mlb \
  --db /absolute/path/to/trades_sim.db
```

출력은 displayed full-depth 반사실이며 수수료가 제외된 탐색 자료입니다. actual fill이나
realized P&L로 해석하지 않습니다.

## 운영 전제

- cadence: 1분
- live DB: `data/<runtime-job>/trades.db`
- Silver DB: `data/plum-shadow-silver-1m-v1/trades_sim.db`
- Gold DB: `data/plum-shadow-gold-{mlb,nfl,nba}-1m-v1/trades_sim.db`
- Silver/Gold workspace는 각각 exact external T7 경로에서만 실행
- 신규 진입 기간: `[2026-08-31T00:00:00Z, 2026-09-14T00:00:00Z)`
- follow-up 종료: `2026-09-21T00:00:00Z`
- Gold MLB 수집 기간: `[2026-09-01T00:00:00Z, 2026-10-01T00:00:00Z)`;
  follow-up은 `2026-10-08T00:00:00Z`까지
- Gold NFL·NBA 수집 기간: `[2026-09-02T10:30:00Z, 2026-12-01T10:30:00Z)`;
  follow-up은 `2026-12-08T10:30:00Z`까지

구체적인 가설·무효화·표본 기준은 `STRATEGY.md`와
축구는 `research/frozen-2026-08-31-full-match-no-time-exit-v2/PREREGISTRATION.md`,
MLB Gold는 `research/frozen-2026-09-01-multisport-mlb-shadow-v3/PREREGISTRATION.md`를
확인하세요. NFL·NBA Gold는
`research/frozen-2026-09-02-nba-nfl-shadow-v4/PREREGISTRATION.md`가 권위입니다.
과거 v1과 Golden Coconut 자료는 원래 경로에 보존하고 섞지 않습니다.
