# Golden Strawberry operations

## Deployment invariants

- Jenkins job: `polybot-shadow-one`.
- Runtime job: `strawberry-shadow-one` only.
- Exact custom workspace: `/Volumes/t7/jenkins/polybot-shadow-one`.
- Suggested timer: `0-59/10 * * * *`; concurrent builds disabled.
- Database: `golden-strawberry/data/strawberry-shadow-one/trades_sim.db` inside that workspace.
- Lifecycle/mode: `archive_only` and `--simulate` only.
- Never clean/wipe the persistent workspace as deployment or recovery.

The design probe took 121.39 seconds for 322 pages, leaving measured margin inside a ten-minute slot,
but runtime is not assumed constant. Health reports p95/max and treats p95 at 80% of cadence or max at
cadence as unhealthy. The observed market count and 16.7 MiB/sweep estimate are dated capacity inputs,
not hardcoded contracts.

## Trusted T7 preflight

The T7 already has two trusted Raspberry identity anchors:

- `/Volumes/t7/.golden-raspberry-volume`
- `/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid`

Strawberry reuses those files read-only because they prove the same volume. Do not auto-create, copy,
rename, replace, or loosen either anchor. Strawberry identity remains distinct through Jenkins job
`polybot-shadow-one`, exact workspace `/Volumes/t7/jenkins/polybot-shadow-one`, runtime job
`strawberry-shadow-one`, and the marker inside that workspace.

Run preflight before `uv sync`, database access, logging, or collection:

```bash
set -eu
set +x
test "$WORKSPACE" = /Volumes/t7/jenkins/polybot-shadow-one

/usr/bin/python3 ./golden-strawberry/scripts/verify_external_workspace.py \
  --mount-root /Volumes/t7 \
  --workspace "$WORKSPACE" \
  --expected-workspace /Volumes/t7/jenkins/polybot-shadow-one \
  --job polybot-shadow-one \
  --sentinel /Volumes/t7/.golden-raspberry-volume \
  --host-uuid-pin /Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid \
  --write-daily-rsync-marker

cd ./golden-strawberry
uv sync --frozen
uv run polybot run --simulate --job strawberry-shadow-one
```

The verifier requires an external APFS mount, exact UUID agreement in both pre-existing anchors, an
off-volume host pin, canonical same-device workspace, and the exact Jenkins job. Only after all checks
does it atomically write `.daily-rsync-workspace.json`. Daily Rsync requires exactly
`schema_version`, `job`, and absolute `workspace`; extra keys are intentionally absent.

## Safety preflight

The exact denied environment keys are:

```text
POLYMARKET_PRIVATE_KEY
POLYMARKET_FUNDER_ADDRESS
POLYMARKET_SIGNATURE_TYPE
POLYMARKET_API_KEY
POLYMARKET_API_SECRET
POLYMARKET_API_PASSPHRASE
CLOB_API_KEY
CLOB_SECRET
CLOB_PASSPHRASE
```

Presence, even with an empty value, stops before lock/database/log/HTTP creation. Unknown
`POLYBOT_*` keys also fail. Never add credentials to Jenkins or `.env`; this collector needs only
public Gamma and CLOB REST.

Before timer enablement:

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-15 && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
uv run polybot config --simulate --job strawberry-shadow-one
```

Run one manual cycle, inspect `status` and `health`, then observe at least two natural timer cycles.

## Storage and evidence

The collector stops before source access below 30 GiB free or at 90% filesystem usage. A warning begins
at 80%. Evidence is never thinned, updated, or deleted to evade a storage guard. Daily logs alone have
a 45-day age policy. SQLite uses rollback `DELETE` journal and `synchronous=FULL`; the short-lived
`-journal` is not an independent evidence artifact.

Every published sweep includes compressed full membership and raw Gamma pages. The dated week-one raw
estimate is about 16.5 GiB before indexes and non-Gamma tables. Review actual `storage_metrics`, DB
growth/day, free-space headroom, and forecast days to the earliest 30 GiB/90% stop. Expand trusted
capacity or start a newly preregistered profile; do not silently reduce source coverage.

Back up SQLite with an online backup method, then checksum the resulting copy. Do not copy a running
database byte-for-byte. Daily Rsync is read-only transport and its cataloged `local_sha256`, source
cutoff, and successful verify result are required for analysis.

## Daily Rsync and review

Use `daily-rsync scan --job polybot-shadow-one`, then a dedicated plan, sync, locate, and verify for
strategy `golden-strawberry`. Do not infer runtime identity from directory names. The expected runtime
is `strawberry-shadow-one`; a mismatch is a routing failure.

Analyze only the canonical verified DB path:

```bash
cd golden-strawberry
uv run python scripts/analyze_experiment.py \
  --db /absolute/daily-rsync/verified/trades_sim.db \
  --start "$REVIEW_START" \
  --end "$REVIEW_END" \
  --output /absolute/review/golden-strawberry.json
```

`REVIEW_END` is exclusive. The analyzer is primary for this research collector. A trading-oriented
strict audit can only be secondary schema/provenance context because this DB intentionally has no
trading lifecycle evidence.

## Incident handling

- Partial/repeated/over-budget Gamma: run is failed; verify no `gamma_sweeps` row for it.
- CLOB missing/malformed/depth shortage: preserve explicit censoring; never substitute a value.
- Resolution missing/error: retain the observation and retry on a later poll until follow-up cutoff.
- Lock contention: do not remove a lock file while a process is alive; disable overlap and inspect it.
- `quick_check` failure: stop timer, preserve files, recover from a verified online backup.
- HIGH/CRITICAL issue, multiple cohort, or trust-anchor mismatch: stop interpretation and repair the
  instrument. Start a new frozen cohort when evidence-affecting source/config changes.
