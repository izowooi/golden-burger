# Golden Strawberry

Golden Strawberry is an accountless, simulation-only, research-only Polymarket **Last Mile**
collector with a frozen 10-minute cadence.
The closed `last-mile-clob-v1` epoch contains the frozen entry census; the active
`last-mile-clob-followup-v2a` epoch follows those episodes without placing an order or reading a
wallet.

During v1, the complete public CLOB `/sampling-markets` cursor population was sampled every 10
minutes to create interval-censored crossings and hypothetical `$5` ask entries. That entry window
is closed. v2a opens the pinned v1 DB with SQLite `mode=ro`, deterministically seeds its executable
episodes, and requests only books and Gamma resolution for unresolved imported episodes. It never
calls `/sampling-markets`, detects a crossing, or creates a new entry.

Jenkins build `#761` failed before creating a v2 DB. The attempted v2 identity remains untouched;
v2a uses a new preregistration, runtime job, data contract, directory, schema, source digest, and
config hash.

The frozen primary policy is entry `0.95`, stop `0.85`, otherwise resolution hold. Entries
`0.90/0.92/0.97`, stops `0.80/0.90`, and targets `0.98/0.99` are sensitivity dimensions, not tuned
defaults. See [STRATEGY.md](STRATEGY.md) and the active
[preregistration](research/frozen-2026-08-24-followup-v2a/PREREGISTRATION.md).

## Safety contract

- `--live` always fails before filesystem or network work.
- Any supported Polymarket/CLOB credential environment variable, even empty, always fails.
- Lifecycle is fixed to `archive_only`; active runtime job is fixed to
  `strawberry-shadow-one-followup-v2a`.
- The v1 DB is a read-only source. Any sidecar or anchor drift fails before v2a HTTP/publication.
- Jenkins runtime must be `/Volumes/t7/jenkins/polybot-shadow-one/golden-strawberry` and must have the
  verified daily-rsync workspace marker.
- Every `PINNED_FAST` cycle rechecks the exact v1 stat/schema/contract/latest successful cutoff,
  source anchor, and every imported seed count and hash before public HTTP.
- v2a stores one canonical gzip full-book blob per distinct token/cycle; thresholds sharing a token
  share that row and no `clob_levels` table exists.
- Cycle evidence, phase timings, successful storage metric, and terminal `SUCCEEDED` commit in one
  transaction; a post-publication failure rolls them all back and leaves durable `FAILED` evidence.
- A shared 450-second cooperative deadline bounds CLOB/Gamma batches, timeouts, retries, sleeps, and
  `Retry-After`. Every recurring `PINNED_FAST` cycle must finish below the recurring 480-second hard
  SLA. One-time `FULL_SEED` has a separate bounded 1,800-second maintenance budget.
- A failed deployment attempt remains `FULL_SEED` and revalidates the immutable imported seed; the
  runtime cannot transition to `PINNED_FAST` until one atomic `FULL_SEED` cycle is `SUCCEEDED`.
- A nonblocking single-writer lock, SQLite `BEGIN IMMEDIATE`, append-only triggers, 100GiB free-space
  floor, and 90% filesystem stop protect evidence.
- Partial or malformed cursor traversals never publish a sweep.

## Local verification

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-15-clob && shasum -a 256 -c MANIFEST.sha256)
(cd research/frozen-2026-08-24-followup-v2a && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

Active follow-up config and lightweight status:

```bash
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot-followup config --simulate \
  --job strawberry-shadow-one-followup-v2a
POLYBOT_LIFECYCLE_MODE=archive_only POLYBOT_SIMULATION_MODE=true \
  uv run polybot-followup status --simulate \
  --job strawberry-shadow-one-followup-v2a
```

v2a `status`와 `health`는 대형 DB `quick_check` 없이 latest run/cycle/SLA만 읽는다. Jenkins 10분
shell에는 넣지 않는다. Retired v1의 기존 `status`/`health`는 deep maintenance 명령이며 30GB
source에 매 run 실행하면 안 된다. Combined analyzer도 `--deep-v1`을 명시할 때만 v1
`quick_check`를 수행한다.

The frozen source DB is `data/strawberry-shadow-one/trades_sim.db`; the active DB is
`data/strawberry-shadow-one-followup-v2a/trades_sim.db`. UTC logs are adjacent to each runtime DB.
None is committed. `polybot run` for v1 is retired and intentionally fails.

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

Combined v1/v2a health (no profitability or parameter verdict):

The analysis cadence range begins at the first successful natural `PINNED_FAST` scheduled slot,
not at the one-time manual `FULL_SEED`. Set the start value below to that observed slot; the analyzer
also derives and reports the boundary from immutable run evidence.

```bash
uv run polybot-followup analyze --simulate \
  --job strawberry-shadow-one-followup-v2a \
  --v1-db /absolute/verified/v1/trades_sim.db \
  --v2a-db /absolute/verified/v2a/trades_sim.db \
  --start "$FIRST_NATURAL_PINNED_FAST_SLOT_UTC" \
  --end 2026-09-21T04:00:00Z \
  --output /absolute/output/strawberry-followup-health.json
```

Deployment and recovery details are in [OPERATIONS.md](OPERATIONS.md).
