# Golden Strawberry / Last Mile

Golden Strawberry is an accountless, research-only Polymarket collector for the frozen
`last-mile-v1` experiment. It observes sampled Gamma probability crossings, requests public CLOB
books only for new crossings and unresolved episodes, and archives hypothetical `$5` ask-walk to
fixed-share bid-walk evidence through a proven Gamma terminal payout.

It has no account, order SDK, wallet, execution component, position lifecycle, or realized-P&L
model. `--live` and any of the exact nine denied credential environment keys fail before database,
log, or HTTP construction. Simulation means public-source collection plus append-only evidence; it
is not a mocked data run.

## Setup and commands

Run from this directory with Python 3.11 or newer:

```bash
uv sync --frozen --extra dev
uv run polybot config --simulate --job strawberry-shadow-one
uv run polybot run --simulate --job strawberry-shadow-one
uv run polybot status --simulate --job strawberry-shadow-one
uv run polybot health --simulate --job strawberry-shadow-one
```

The only runtime job is `strawberry-shadow-one`. Its compatibility database path is
`data/strawberry-shadow-one/trades_sim.db`; this filename is research evidence, not a trading ledger.
Daily UTC logs are under `data/strawberry-shadow-one/logs/` and are retained for 45 days. A
non-blocking `.strawberry.lock`, 30 GiB free-space floor, and 90% filesystem-use stop protect the
single writer before source access.

The analyzer requires a verified absolute DB path and explicit UTC half-open range:

```bash
uv run polybot analyze --simulate --job strawberry-shadow-one \
  --db /absolute/verified/trades_sim.db \
  --start 2026-08-15T02:00:00Z \
  --end 2026-08-22T02:00:00Z \
  --output /absolute/review/golden-strawberry.json
```

The equivalent standalone entry is `uv run python scripts/analyze_experiment.py ...`. It opens
SQLite with `mode=ro&immutable=1`, runs `quick_check`, and emits JSON only.

## Frozen experiment

- Entry window: `[2026-08-15T02:00:00Z, 2026-08-22T02:00:00Z)`.
- Follow-up end: `2026-09-21T02:00:00Z`.
- Cadence: 10 minutes.
- Entry observations: `0.90`, `0.92`, `0.95`, `0.97`.
- Stop observations: `0.80`, `0.85`, `0.90`.
- Descriptive target observations: `0.98`, `0.99`; neither is resolution.
- Primary policy: entry `0.95`, stop `0.85`, otherwise proven terminal resolution.

The other entry/stop combinations and all target variants are sensitivity-only. Initial
above-threshold observations are left-censored. A crossing requires a same-token/same-condition prior
below the threshold, a positive gap no longer than 25 minutes, and a current observation inside the
entry window. Sampled jumps are interval-censored and never described as continuous passage through
an intermediate probability.

The source envelope has no liquidity, volume, category, sport, outcome-type, negRisk, or horizon gate.
Gamma keyset uses zero liquidity/volume minima and complete cursor traversal with tags. Only source
tradability is required. Binary and multi-outcome, sports and non-sports, and negRisk markets remain
available for later stratification.

## Capacity and storage

A dated no-filter design probe at `2026-08-15T00:xxZ` measured 32,132 markets, 322 pages, and 121.39
seconds. One representative 100-market page was 841,325 raw bytes and 54,532 gzip bytes, implying
about 16.7 MiB compressed raw per sweep and 16.5 GiB for 1,008 ten-minute sweeps before indexes and
other evidence. These are capacity estimates, not fixed market-count or byte contracts. `health` and
the analyzer report actual DB growth, free space, and days to the 30 GiB/90% stop instead.

Every complete cycle archives gzip raw pages and normalized full membership, while CLOB and resolution
raw bodies are also gzip/SHA-linked. Source/evidence tables are protected by no-update/no-delete
triggers. Only `latest_outcome_state` is mutable and is explicitly limited to crossing detection.
Partial, repeated-cursor, malformed, or over-budget Gamma traversal cannot publish a sweep.

## Economic evidence and interpretation

A hypothetical entry exists only if displayed asks cover exactly `$5`; every unresolved executable
episode cycle stores a bid walk for its fixed shares or an explicit censoring reason. Entry ask VWAP,
exit bid VWAP, full levels, spread, depth, and source fee-rate/tick/minimum-size metadata are preserved.
No midpoint or forward substitution is allowed.

Reports show gross displayed-book counterfactual values and separate 10.4 bps and 72.5 bps round-trip
cost stresses. The stresses are not exact-fee claims. Same-poll stop and resolution evidence is
ambiguous and conservatively ordered stop first.

A one-week report is `HEALTH_ONLY` when evidence health fails or `PILOT_UNDERPOWERED` until there are at
least 50 executable episodes, 30 resolved independent event clusters, 90% path coverage, and 90%
resolution coverage. Even then the maximum label is `PILOT_CANDIDATE`, requiring a separately frozen
30-day out-of-sample confirmation. This cohort cannot select and validate a parameter winner itself.

## External Jenkins workspace

The Jenkins job is `polybot-shadow-one`, the runtime job remains `strawberry-shadow-one`, and the exact
custom workspace is `/Volumes/t7/jenkins/polybot-shadow-one`. The T7 is already trusted by Raspberry’s
two identity anchors. Reuse them read-only; do not create a Strawberry replacement pin:

```bash
/usr/bin/python3 ./golden-strawberry/scripts/verify_external_workspace.py \
  --mount-root /Volumes/t7 \
  --workspace "$WORKSPACE" \
  --expected-workspace /Volumes/t7/jenkins/polybot-shadow-one \
  --job polybot-shadow-one \
  --sentinel /Volumes/t7/.golden-raspberry-volume \
  --host-uuid-pin /Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid \
  --write-daily-rsync-marker
```

The preflight validates external APFS identity, matching UUID in both existing anchors, off-volume pin
placement, canonical workspace/device, and then atomically writes the exact three-key
`.daily-rsync-workspace.json` for `polybot-shadow-one`. See [OPERATIONS.md](OPERATIONS.md) for the full
runbook and [STRATEGY.md](STRATEGY.md) for the evidence contract.
