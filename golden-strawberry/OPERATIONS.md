# Golden Strawberry operations

## Jenkins contract

- Jenkins job: `polybot-shadow-one`.
- Active runtime job: `strawberry-shadow-one-followup-v2a`.
- Active data contract: `last-mile-clob-followup-v2a`.
- Active preregistration:
  `research/frozen-2026-08-24-followup-v2a/PREREGISTRATION.md`.
- Frozen source runtime: `strawberry-shadow-one` (read-only; `polybot run` retired).
- Custom workspace: `/Volumes/t7/jenkins/polybot-shadow-one`.
- Timer: **OFF**. The frozen schedule, if separately authorized after acceptance, is
  `7-59/10 * * * *`.
- Concurrent builds: disabled; SCM clean/wipe extensions: absent.
- Jenkins build retention: 14 days.

Ten minutes remains frozen by the experiment contract. The v1 full census/row-per-level path grew to
about 30GB and is retired after the entry window. v2a retains minute offset 7 but follows only imported
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
(cd research/frozen-2026-08-24-followup-v2a && shasum -a 256 -c MANIFEST.sha256)
"${UV}" run polybot-followup config --simulate \
  --job strawberry-shadow-one-followup-v2a
"${UV}" run polybot-followup run --simulate \
  --job strawberry-shadow-one-followup-v2a
```

Do not append `status` or `health` to the periodic shell. v2a versions are lightweight, but the run
already records terminal event, storage guard, phase timings and atomic-publication result. Retired
v1 `status`/`health` remain deep diagnostics; at 12GiB they added about 19 minutes per build, and they
must never be run automatically against the roughly 30GB source. Use the combined analyzer on
daily-rsync verified immutable copies; invoke `--deep-v1` only in an explicit maintenance window.
The first v2a cycle performs one `FULL_SEED` deterministic seed scan under a bounded 1,800-second
maintenance budget. Later cycles require `validation_mode=PINNED_FAST`: exact file stat, source
anchor, schema/contract/latest successful cutoff, and every imported canonical seed row/count/hash
are revalidated before public HTTP. One shared 450-second deadline bounds CLOB/Gamma batches,
timeouts, retries, sleeps, and `Retry-After`; recurring cycles must terminate below 480 seconds.

Do not add `clean`, workspace wipe, DB deletion, credentials, `--live`, or a different runtime job.
Do not restore `polybot run`, `/sampling-markets`, crossing detection, candidate metadata calls, or
row-per-level storage. The canonical v1 DB must remain at
`data/strawberry-shadow-one/trades_sim.db`, with no SQLite sidecars, and is opened only with
`mode=ro`. Any source or imported-seed drift records `FAILED` and stops v2a before public HTTP.
The verifier reuses the existing trusted T7 sentinel and off-volume UUID pin; it never creates trust
anchors: `/Volumes/t7/.golden-raspberry-volume` and
`/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid`. The collector rechecks the exact Jenkins
workspace and marker before opening a log or DB.

## Follow-up v2a transition

1. Keep the periodic trigger absent. Build `#761` failed and left no v2 database. Preserve the old
   v2 config/prereg/runtime identity as attempted provenance; do not retry, migrate, or overwrite it.
   Confirm `#760` is aborted and `#759` is the final successful v1 sweep; do not retry v1.
2. Verify T7 is mounted, has at least 100GiB free, the canonical v1 DB has no `-wal`/`-shm`/`-journal`,
   and no process holds it writable. Preserve its byte content and permissions.
3. Install the shell above and run one manual build. It must create only
   `data/strawberry-shadow-one-followup-v2a/trades_sim.db`; it must not create or mutate either the
   v1 DB or the attempted v2 runtime directory.
4. Accept the first `FULL_SEED` only when all of these checks are visible in its committed evidence:
   exactly one source anchor; exact v1 path/stat/schema/contract/job/clocks/latest successful cutoff;
   no v1 sidecars; imported episode/condition/terminal/threshold counts equal the source anchor;
   every imported row hash and all three aggregate hashes match; `validation_mode=FULL_SEED` and
   total runtime are below 1,800 seconds; the network phase is bounded by 450 seconds; one compact
   book attempt per unresolved token and one resolution row per unresolved condition; no
   sampling/candidate-metadata requests; all required phase timings; no HIGH/CRITICAL issue; and one
   terminal `SUCCEEDED` in the same transaction as cycle evidence. Any failure must leave no cycle,
   path, resolution, threshold, phase, successful-storage, or `SUCCEEDED` row for that run.
   A failed attempt may retain the atomically imported seed, but the next manual attempt remains
   `FULL_SEED` and recaptures/revalidates it; do not enable the timer or treat it as `PINNED_FAST`
   until this acceptance transaction succeeds.
5. On an immutable v2a copy, run `quick_check` and the combined analyzer. Do not run v1 quick check
   unless separately scheduling `--deep-v1` maintenance. A manual `FULL_SEED` is maintenance, so
   recurring cadence health is intentionally not established yet.
6. Do not enable the timer as part of the first-seed deployment. If timer restoration is separately
   authorized after the manual acceptance checks, restore `7-59/10 * * * *`, wait for the first
   successful natural `PINNED_FAST` slot, and use that slot as rollout-health/analysis start. Confirm
   its complete source/seed validation occurs before HTTP and total runtime is strictly below 480s.
7. Run daily-rsync scan/plan/sync/locate/verify and confirm it discovers both the frozen v1 epoch and
   new v2a runtime rather than replacing or merging them.

Historically, the first full v1 DB measured about 31.7MB because it included the 25k-token cache and
initial left-censoring. A normal subsequent cycle measured about 6.46MB. The 100GiB floor includes
the v1 source, v2a DB/journal and backup margin. v2a `storage_metrics` and analyzer growth override
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
`strawberry-shadow-one-followup-v2a` epoch separately. Never infer success from a directory name or
plan file alone, and do not merge the DBs.

## Review prompts

After 24 hours:

> `polybot-shadow-one`을 daily-rsync로 동기화하고 Golden Strawberry Last Mile의 첫 24시간
> collection health를 검증해줘. 수익성이나 파라미터는 판단하지 말고 cadence, cursor/membership,
> crossing book, Gamma metadata, path/resolution, DB 무결성, 저장공간 증가량을 확인해줘.

During compact follow-up:

> `polybot-shadow-one`의 frozen v1과 `strawberry-shadow-one-followup-v2a` DB를 각각
> daily-rsync로 동기화·검증하고 combined health를 확인해줘. v1 anchor, v2a cadence, compact
> book/path/resolution coverage, resolved-condition exclusion, DB/storage/SLA만 판정하고 수익성,
> threshold 선택, live 승격은 판단하지 마.

The immutable helper is:

```bash
uv run polybot-followup analyze --simulate \
  --job strawberry-shadow-one-followup-v2a \
  --v1-db /absolute/verified/v1/trades_sim.db \
  --v2a-db /absolute/verified/v2a/trades_sim.db \
  --start "$FIRST_NATURAL_PINNED_FAST_SLOT_UTC" \
  --end 2026-09-21T04:00:00Z \
  --output /absolute/output/strawberry-followup-health.json
```

Always pass absolute DBs returned by `daily-rsync locate` and accepted by `verify`.
The retired v1-only `scripts/analyze_experiment.py` / `polybot analyze` helper remains read-only for
the original entry collection report; it must never be used to resume v1 collection.

## Failure handling

### Verified device-only reattachment (2026-09-05 amendment)

An APFS `st_dev` number can change across reboot/remount while the trusted volume and source file
remain the same. Never rewrite `source_anchors`, touch the source mtime, or disable the fingerprint
check. Stop the timer and compare the whole source with an independently verified frozen backup.
SQLite online backups may differ only in documented header bookkeeping; preserve both raw hashes
and the full-body equivalence evidence rather than overwriting either file.

If and only if path/inode/size/mtime are unchanged and the previous device reproduces the exact old
fingerprint, the following **one-time maintenance** command can approve the device change. The expected
checksum must be the original source-file checksum established from that independent comparison,
not a newly observed checksum accepted without comparison.

```bash
uv run python scripts/attest_source_reattachment.py \
  --old-device <previous-device-id> \
  --expected-content-sha256 <independently-verified-source-sha256>
# After reviewing the dry-run, repeat with --apply in a timer-OFF maintenance build.
```

The apply command hashes the entire source and checks the existing T7 UUID pins before writing a
private immutable receipt under `/Users/jongwoopark/.jenkins/golden-strawberry-source-reattachments/`.
It does not change a source, anchor, seed, experiment clock, or previous follow-up row. Regular runs
read this approval, pin inode/size/mtime and the original stable UUID, revalidate all other anchor/seed
fields, and include the receipt and its checksum in phase evidence. Any inode/content-size/mtime/path,
UUID, sidecar or other anchor drift remains a hard failure. Do not put this full-hash maintenance
command in the recurring shell. Later OS device renumbering reuses this same content-verified stable
identity; it does not require reauthorizing an unchanged trusted volume on every reboot.
Restore the same 10-minute timer only after a successful verified run.
The original v2a preregistration is retained; the operational amendment is
`research/amendment-2026-09-05-device-reattachment/OPERATIONS_AMENDMENT.md`.

- v1 sidecar/schema/contract/job/cutoff/sweep/config/source/count/hash drift, or any imported seed
  row/count/hash drift: record `FAILED` and fail before v2a HTTP; do
  not repair, alter, vacuum or reseed either DB in place.
- Book, Gamma, deadline, publication, timing, or success-finalization failure: keep durable API
  receipts and `FAILED`, but roll back all cycle/path/resolution/threshold/phase/success-storage state;
  never synthesize a price or resolution.
- Missing episode token in terminal payout: resolution is `MALFORMED` and remains unresolved.
- Workspace/mount/marker mismatch or <100GiB free: source access must not begin.
- SLA miss: terminate cleanly before 480 seconds, record `FAILED`, and treat it as instrument health
  failure; the one-time bounded `FULL_SEED` duration is reported separately from recurring cadence.
- Digest/manifest/config mismatch: stop; never edit frozen v1, attempted v2, or active v2a in place.
