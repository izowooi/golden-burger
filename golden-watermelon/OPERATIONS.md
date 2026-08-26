# Golden Watermelon v3c 운영 절차

## Jenkins 경계

| Jenkins | exact workspace | runtime | schedule |
|---|---|---|---|
| `polybot-white` | `/Volumes/t7/jenkins/polybot-white` | `watermelon-white-1m-v3c` | `* * * * *` |
| `polybot-grey` | `/Volumes/t7/jenkins/polybot-grey` | `watermelon-grey-5m-v3c` | `H/5 * * * *` |

concurrent build는 끄고 build discard는 14일로 둔다. `Clean before checkout`, workspace wipe,
credential binding, 기존 DB migration/import를 사용하지 않는다. v3b와 이전 DB는 그대로 두고
`data/watermelon-*-v3c/trades_sim.db`를 새로 만든다.

## Execute shell

White:

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

cd ./golden-watermelon
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
"${UV}" run python scripts/verify_external_workspace.py \
  --workspace "${WORKSPACE}" --min-free-gib 50
"${UV}" run polybot config --simulate --job watermelon-white-1m-v3c
"${UV}" run polybot run --simulate --job watermelon-white-1m-v3c
"${UV}" run polybot status --simulate --job watermelon-white-1m-v3c
```

Grey는 runtime만 `watermelon-grey-5m-v3c`로 바꾼다. 실험 parameter를 Jenkins env로
override하지 않는다.

## 배포 순서

1. 두 timer를 끈 채 config SHA, SCM, external workspace, no-clean을 확인한다.
2. White/Grey를 수동 1회씩 실행한다.
3. exact `tag_id=100350`, `related_tags=false`, cursor complete, classifier/mapping hash,
   application/user version, external storage gate를 확인한다.
4. UCL/UEL가 존재하면 exact identity로 ACCEPTED되고 advancement/extra time/penalty 시장은
   제외되는지 확인한다.
5. `sports_clock_websocket` request와 `SPORTS_CLOCK_UPDATE` raw evidence, target/matched coverage,
   `75/80/85` minute grid, `$5..$500` ladder가 config/decision에 기록되는지 확인한다.
6. White runtime <45초, Grey runtime <240초이고 CRITICAL/HIGH 원인이 없을 때만 timer를 켠다.
7. White/Grey 각각 자연 실행 2회 이상을 확인하고 daily-rsync로 새 epoch를 동기화한다.

Sports WebSocket에 target이 있는데 update가 하나도 없거나 연결이 실패하면 timer를 복원하지
않는다. kickoff wall time으로 elapsed를 만들어내지 말고 source behavior를 먼저 수정한다.

## Daily-rsync 및 analyzer

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-white
uv run daily-rsync scan --job polybot-grey
uv run daily-rsync sync-job --job polybot-white --strategy golden-watermelon --days 3
uv run daily-rsync sync-job --job polybot-grey --strategy golden-watermelon --days 3
uv run daily-rsync verify --job polybot-white --strategy golden-watermelon
uv run daily-rsync verify --job polybot-grey --strategy golden-watermelon
uv run daily-rsync locate --job polybot-white --strategy golden-watermelon
uv run daily-rsync locate --job polybot-grey --strategy golden-watermelon
```

`verify`가 반환한 exact absolute DB만 analyzer에 넘긴다.

```bash
cd ../golden-watermelon
uv run polybot analyze --simulate --job watermelon-white-1m-v3c \
  --db /absolute/white/trades_sim.db \
  --db /absolute/grey/trades_sim.db \
  --output /tmp/golden-watermelon-v3c-health.json
```

첫 health review는 `2026-08-27T18:30:00Z` 이후다. cadence, cursor completeness, domestic/UEFA
identity, strict regular-time moneyline, book/full-depth, Sports clock, path/resolution, cohort,
DB integrity, notional depth와 storage growth만 본다. ROI·best threshold/stop/minute/notional은
판정하지 않는다.

## 장애 대응

- `LEAGUE_IDENTITY_DRIFT`: exact numeric tag/series/team/source field를 raw payload와 대조한다.
- `SPORTS_CLOCK_COVERAGE_GAP`: WSS request receipt와 matched slug를 보고 source 연결을 복구한다.
- incomplete cursor: partial universe를 사용하지 않는다.
- database epoch mismatch: v3b archive를 쓰지 말고 v3c path를 고친다.
- White p95 ≥45초 또는 queue 발생: timer를 끄고 병목을 고친다.
- storage gate: external mount를 복구하며 내부 disk fallback이나 DB 삭제를 하지 않는다.
- schema/identity 변경: 기존 DB에 `ALTER TABLE`하지 않고 새 prereg/runtime epoch를 만든다.
