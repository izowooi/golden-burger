# Golden Strawberry operations

## Jenkins contract

- Jenkins job: `polybot-shadow-one`.
- Active runtime job: `strawberry-shadow-one-followup-v2`.
- Frozen source runtime: `strawberry-shadow-one` (read-only; `polybot run` retired).
- Custom workspace: `/Volumes/t7/jenkins/polybot-shadow-one`.
- Timer: `7-59/10 * * * *`.
- Concurrent builds: disabled; SCM clean/wipe extensions: absent.
- Jenkins build retention: 14 days.

Ten minutes remains frozen by the experiment contract. The v1 full census/row-per-level path grew to
about 30GB and is retired after the entry window. v2 retains minute offset 7 but follows only imported
unresolved episodes with one compact book row per distinct token. Five minutes would change the
preregistered cadence; offset 7 remains part of health slot accounting.

## Shell

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE
unset POLYMARKET_API_KEY
unset POLYMARKET_API_SECRET
unset POLYMARKET_API_PASSPHRASE
unset CLOB_API_KEY
unset CLOB_SECRET
unset CLOB_PASSPHRASE

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=archive_only
export POLYBOT_SIMULATION_MODE=true

/usr/bin/python3 ./golden-strawberry/scripts/verify_external_workspace.py \
  --workspace "${WORKSPACE}" \
  --write-daily-rsync-marker

cd ./golden-strawberry
UV=/Users/jongwoopark/.local/bin/uv

"${UV}" sync --frozen
(cd research/frozen-2026-08-23-followup-v2 && shasum -a 256 -c MANIFEST.sha256)
"${UV}" run polybot-followup config --simulate \
  --job strawberry-shadow-one-followup-v2
"${UV}" run polybot-followup run --simulate \
  --job strawberry-shadow-one-followup-v2
```

Do not append `status` or `health` to the periodic shell. v2 versions are lightweight, but the run
already records terminal event, storage guard, phase timings and atomic-publication result. Retired
v1 `status`/`health` remain deep diagnostics; at 12GiB they added about 19 minutes per build, and they
must never be run automatically against the roughly 30GB source. Use the combined analyzer on
daily-rsync verified immutable copies; invoke `--deep-v1` only in an explicit maintenance window.
The first v2 cycle performs one full deterministic seed scan. Later cycles require
`validation_mode=PINNED_FAST`: exact file stat fingerprint, schema, contract and latest successful
sweep are revalidated without rescanning the multi-million-row v1 path evidence.

Do not add `clean`, workspace wipe, DB deletion, credentials, `--live`, or a different runtime job.
Do not restore `polybot run`, `/sampling-markets`, crossing detection, candidate metadata calls, or
row-per-level storage. The canonical v1 DB must remain at
`data/strawberry-shadow-one/trades_sim.db`, with no SQLite sidecars, and is opened only with
`mode=ro`. Any anchor drift stops v2 before public HTTP.
The verifier reuses the existing trusted T7 sentinel and off-volume UUID pin; it never creates trust
anchors: `/Volumes/t7/.golden-raspberry-volume` and
`/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid`. The collector rechecks the exact Jenkins
workspace and marker before opening a log or DB.

## Follow-up v2 transition

1. Keep the periodic trigger absent. Confirm build `#760` is aborted and `#759` is the final
   successful v1 sweep; do not retry v1.
2. Verify T7 is mounted, has at least 100GiB free, the canonical v1 DB has no `-wal`/`-shm`/`-journal`,
   and no process holds it writable. Preserve its byte content and permissions.
3. Install the shell above and run one manual build. It must create only
   `data/strawberry-shadow-one-followup-v2/trades_sim.db` and must leave v1 byte/schema/sidecars
   unchanged.
4. Inspect the console and v2 lightweight health. Require one source anchor, deterministic imported
   counts/hashes, one compact book per token, full episode path/resolution request coverage, no
   sampling/candidate-metadata receipts, complete phase timings, and no HIGH/CRITICAL issue.
5. On an immutable copy, run v2 `quick_check` and the combined analyzer. Do not run v1 quick check
   unless separately scheduling `--deep-v1` maintenance.
6. Restore `7-59/10 * * * *`; wait for one natural build and inspect the console/runtime SLA.
7. Run daily-rsync scan/plan/sync/locate/verify and confirm it discovers both the frozen v1 epoch and
   new v2 runtime rather than replacing or merging them.

Historically, the first full v1 DB measured about 31.7MB because it included the 25k-token cache and
initial left-censoring. A normal subsequent cycle measured about 6.46MB. The 100GiB floor includes
the v1 source, v2 DB/journal and backup margin. v2 `storage_metrics` and analyzer growth override
these dated v1 estimates.

## Daily-rsync

From the monorepo:

```bash
cd daily-rsync
uv run daily-rsync scan --job polybot-shadow-one
uv run daily-rsync plan --job polybot-shadow-one
# execute the generated explicit plan according to daily-rsync OPERATIONS.md
uv run daily-rsync locate --job polybot-shadow-one
uv run daily-rsync verify --job polybot-shadow-one --strategy golden-strawberry
```

The remote marker must identify `polybot-shadow-one`. Discovery must retain the frozen
`strawberry-shadow-one` source epoch and identify the active
`strawberry-shadow-one-followup-v2` epoch separately. Never infer success from a directory name or
plan file alone, and do not merge the DBs.

## Review prompts

After 24 hours:

> `polybot-shadow-one`을 daily-rsync로 동기화하고 Golden Strawberry Last Mile의 첫 24시간
> collection health를 검증해줘. 수익성이나 파라미터는 판단하지 말고 cadence, cursor/membership,
> crossing book, Gamma metadata, path/resolution, DB 무결성, 저장공간 증가량을 확인해줘.

During compact follow-up:

> `polybot-shadow-one`의 frozen v1과 `strawberry-shadow-one-followup-v2` DB를 각각
> daily-rsync로 동기화·검증하고 combined health를 확인해줘. v1 anchor, v2 cadence, compact
> book/path/resolution coverage, resolved-condition exclusion, DB/storage/SLA만 판정하고 수익성,
> threshold 선택, live 승격은 판단하지 마.

The immutable helper is:

```bash
uv run polybot-followup analyze --simulate \
  --job strawberry-shadow-one-followup-v2 \
  --v1-db /absolute/verified/v1/trades_sim.db \
  --v2-db /absolute/verified/v2/trades_sim.db \
  --start 2026-08-22T04:00:00Z \
  --end 2026-09-21T04:00:00Z \
  --output /absolute/output/strawberry-followup-health.json
```

Always pass absolute DBs returned by `daily-rsync locate` and accepted by `verify`.
The retired v1-only `scripts/analyze_experiment.py` / `polybot analyze` helper remains read-only for
the original entry collection report; it must never be used to resume v1 collection.

## Failure handling

- v1 sidecar/schema/contract/job/cutoff/sweep/config/source/count/hash drift: fail before v2 HTTP; do
  not repair, alter, vacuum or reseed either DB in place.
- Book or Gamma failure: keep durable API receipts and explicit censoring; no partial cycle or
  synthesized price/resolution.
- Missing episode token in terminal payout: resolution is `MALFORMED` and remains unresolved.
- Workspace/mount/marker mismatch or <100GiB free: source access must not begin.
- SLA miss: treat as instrument health failure and inspect phase timings; it is not strategy evidence.
- Digest/manifest/config mismatch: stop; never edit the frozen v1 or active v2 DB contract in place.
