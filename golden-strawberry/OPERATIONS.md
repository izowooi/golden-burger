# Golden Strawberry operations

## Jenkins contract

- Jenkins job: `polybot-shadow-one`.
- Runtime job: `strawberry-shadow-one`.
- Custom workspace: `/Volumes/t7/jenkins/polybot-shadow-one`.
- Timer: `7-59/10 * * * *`.
- Concurrent builds: disabled; SCM clean/wipe extensions: absent.
- Jenkins build retention: 14 days.

Ten minutes is frozen because complete end-to-end cycles measured 5.5–6.3 seconds and the source
requires only 13 cursor pages. Five minutes would double load without a demonstrated scientific
benefit. Minute offset 7 avoids the existing 0–3 minute research shard offsets and is part of health
slot accounting.

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
(cd research/frozen-2026-08-15-clob && shasum -a 256 -c MANIFEST.sha256)
"${UV}" run polybot config --simulate --job strawberry-shadow-one
"${UV}" run polybot run --simulate --job strawberry-shadow-one
"${UV}" run polybot status --simulate --job strawberry-shadow-one
"${UV}" run polybot health --simulate --job strawberry-shadow-one
```

Do not add `clean`, workspace wipe, DB deletion, credentials, `--live`, or a different runtime job.
The verifier reuses the existing trusted T7 sentinel and off-volume UUID pin; it never creates trust
anchors: `/Volumes/t7/.golden-raspberry-volume` and
`/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid`. The collector rechecks the exact Jenkins
workspace and marker before opening a log or DB.

## First deployment

1. Keep the periodic trigger absent while changing config.
2. Verify T7 is mounted and has at least 100GiB free.
3. Save the shell and run one manual build.
4. Require a successful 13-page terminal sweep, SQLite `quick_check=ok`, no HIGH/CRITICAL issue, and
   healthy status.
5. Add `7-59/10 * * * *`; wait for at least one natural build and inspect its console.
6. Run daily-rsync scan/sync/verify and inspect the verified DB, not the Jenkins workspace DB.

The first full optimized DB measured about 31.7MB because it includes the 25k-token latest cache and
initial left-censoring. A normal subsequent cycle measured about 6.46MB. The 100GiB floor includes
the roughly 35GB through-follow-up plan plus DB/journal/backup margin. `storage_metrics` and analyzer
forecast override these dated estimates.

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

The remote marker must identify `polybot-shadow-one`; runtime identity is discovered as
`strawberry-shadow-one`. Never infer success from a directory name or plan file alone.

## Review prompts

After 24 hours:

> `polybot-shadow-one`을 daily-rsync로 동기화하고 Golden Strawberry Last Mile의 첫 24시간
> collection health를 검증해줘. 수익성이나 파라미터는 판단하지 말고 cadence, cursor/membership,
> crossing book, Gamma metadata, path/resolution, DB 무결성, 저장공간 증가량을 확인해줘.

After the entry window and sufficient follow-up:

> `polybot-shadow-one`을 다시 동기화하고 `[2026-08-15T04:00:00Z,
> 2026-08-22T04:00:00Z)` Golden Strawberry cohort를 strict review해줘. 0.95/0.85 primary와
> sports/non-sports, liquidity/volume strata를 보여주되, evidence gate를 통과하지 못하면 수익성
> 판단이나 파라미터 추천을 중단해줘.

The equivalent immutable helper is `uv run python scripts/analyze_experiment.py --help`; always pass
the absolute DB returned by daily-rsync `locate` and verified by `verify`.

## Failure handling

- Cursor/page/count/token duplication failure: no sweep should publish; keep the failed run receipts.
- Book or Gamma enrichment failure: retain explicit censoring; do not synthesize a price or metadata.
- Missing episode token in terminal payout: resolution is `MALFORMED` and remains unresolved.
- Workspace/mount/marker mismatch or <100GiB free: source access must not begin.
- Digest/manifest/config mismatch: use a new preregistered cohort; never edit the active DB contract.
