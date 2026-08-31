# Golden Plum

축구 경기 5~75분에 HOME/DRAW/AWAY 직접 YES·NO 여섯 호가를 1분마다 관측하고,
유일한 선두가 3회 상승 확인 뒤 0.75를 처음 통과할 때 exact `$5`로 검증하는
전략입니다.

## 구성

| Jenkins | runtime job | 역할 |
|---|---|---|
| `polybot-king` | `plum-live-king-90-1m-v1` | live A, 절대 TP 0.90 |
| `polybot-queen` | `plum-live-queen-95-1m-v1` | live B, 절대 TP 0.95 |
| `polybot-silver` | `plum-shadow-silver-1m-v1` | credential-free raw/simulation |

공통 entry는 `[0.75,0.78]` first crossing, stop은 confirmed entry -0.15,
time exit은 source minute 80입니다. live와 shadow 모두 direct six-book을 저장하며
합성 NO를 사용하지 않습니다.

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

Silver에는 credential을 주입하면 안 됩니다.

```bash
unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE

POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot config --simulate --job plum-shadow-silver-1m-v1
POLYBOT_TAKE_PROFIT_PRICE=0.95 \
  uv run polybot run --simulate --job plum-shadow-silver-1m-v1
```

## 과거/수집 자료 재생

```bash
uv run python scripts/replay_direct_six_book.py \
  --db /absolute/path/to/trades_sim.db
```

출력은 displayed full-depth 반사실이며 수수료가 제외된 탐색 자료입니다. actual fill이나
realized P&L로 해석하지 않습니다.

## 운영 전제

- cadence: 1분
- live DB: `data/<runtime-job>/trades.db`
- Silver DB: `data/plum-shadow-silver-1m-v1/trades_sim.db`
- Silver workspace는 external `/Volumes/...`에서만 실행
- 신규 진입 기간: `[2026-08-31T00:00:00Z, 2026-09-14T00:00:00Z)`
- follow-up 종료: `2026-09-21T00:00:00Z`

구체적인 가설·무효화·표본 기준은 `STRATEGY.md`와
`research/frozen-2026-08-31-midgame-confirmation-v1/PREREGISTRATION.md`를 확인하세요.
