# Golden Strawberry

Golden Strawberry is an accountless, simulation-only, research-only Polymarket **Last Mile** collector for the
`last-mile-clob-v1` experiment. It tests first sampled high-probability crossings without placing an
order or reading a wallet.

The complete public CLOB `/sampling-markets` cursor population is sampled every 10 minutes. Its token
price is used only to detect interval-censored crossings. A crossing requests the full public CLOB
book and records a hypothetical `$5` ask walk; unresolved episodes then record fixed-share bid walks.
Gamma is used only for crossing-time liquidity/volume/event metadata and proven terminal payout.

The frozen primary policy is entry `0.95`, stop `0.85`, otherwise resolution hold. Entries
`0.90/0.92/0.97`, stops `0.80/0.90`, and targets `0.98/0.99` are sensitivity dimensions, not tuned
defaults. See [STRATEGY.md](STRATEGY.md) and the active
[preregistration](research/frozen-2026-08-15-clob/PREREGISTRATION.md).

## Safety contract

- `--live` always fails before filesystem or network work.
- Any supported Polymarket/CLOB credential environment variable, even empty, always fails.
- Lifecycle is fixed to `archive_only`; job name is fixed to `strawberry-shadow-one`.
- Jenkins runtime must be `/Volumes/t7/jenkins/polybot-shadow-one/golden-strawberry` and must have the
  verified daily-rsync workspace marker.
- A nonblocking single-writer lock, SQLite `BEGIN IMMEDIATE`, append-only triggers, 100GiB free-space
  floor, and 90% filesystem stop protect evidence.
- Partial or malformed cursor traversals never publish a sweep.

## Local verification

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-15-clob && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

Config and read-only status:

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot config --simulate --job strawberry-shadow-one
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot status --simulate --job strawberry-shadow-one
```

`status`와 `health`는 대형 append-only DB에 `PRAGMA quick_check`와 exact table counts를 수행하는
maintenance 명령이다. Jenkins 10분 수집 shell에는 넣지 말고 job을 멈춘 maintenance window에서
별도 실행한다. 정기 검토는 daily-rsync로 동기화·검증한 immutable copy에 SQLite
`quick_check`와 analyzer를 적용한다.

The runtime DB is `data/strawberry-shadow-one/trades_sim.db`; UTC bot logs are under the adjacent
`logs/` directory. Neither is committed.

## Frozen clocks and interpretation

- Entry window: `[2026-08-15T04:00:00Z, 2026-08-22T04:00:00Z)`.
- Follow-up end: `2026-09-21T04:00:00Z`.
- Jenkins cadence: `7-59/10 * * * *`.

One week is a collection-health and pilot-size review. It cannot establish a profitable parameter or
authorize live trading; any selected hypothesis requires a new frozen 30-day out-of-sample cohort.

```bash
uv run polybot analyze --simulate --job strawberry-shadow-one \
  --db /absolute/verified/trades_sim.db \
  --start 2026-08-15T04:00:00Z \
  --end 2026-08-22T04:00:00Z \
  --output /absolute/output/strawberry-week-one.json
```

Deployment and recovery details are in [OPERATIONS.md](OPERATIONS.md).
