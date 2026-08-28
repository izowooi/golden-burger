# Golden Watermelon Live v2h 운영 절차

## Jenkins 안전 설정

- `polybot-cat`, `polybot-dog` 기존 private key/funder/signature type을 그대로 보존한다.
- secret 참조 전 `set +x`; console·문서·Git에 값을 출력하지 않는다.
- concurrent build와 `Clean before checkout`을 끄고 build discard 14일을 유지한다.
- config/test/manual build가 끝나기 전 timer를 켜지 않는다.
- workspace wipe, 기존 DB 삭제·migration/import 금지.

## Cat — 0.96

기존 credential export/binding 뒤 아래 shell을 사용한다.

```bash
#!/bin/bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_MIN_LIQUIDITY=0
export POLYBOT_MIN_VOLUME_24H=0
export POLYBOT_MIN_CUMULATIVE_VOLUME=0
export POLYBOT_MAX_POSITIONS=20
export POLYBOT_MAX_EVENT_POSITIONS=1
export POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE=20
export POLYBOT_YES_ONLY=true
export POLYBOT_ENTRY_PROB_MIN=0.96
export POLYBOT_ENTRY_PROB_MAX=0.999
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_ENTRY_HOURS_MAX=4
export POLYBOT_STOP_PRICE=0.70
export POLYBOT_EXPERIMENT_START_UTC=2026-08-26T18:30:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-02T18:30:00Z
export POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC=2026-09-09T18:30:00Z

cd ./golden-watermelon-live
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
"${UV}" run polybot config --live --job watermelon-live-cat-96-1m-v2h
"${UV}" run polybot run --live --job watermelon-live-cat-96-1m-v2h
"${UV}" run polybot status --live --job watermelon-live-cat-96-1m-v2h
```

Dog는 `POLYBOT_ENTRY_PROB_MIN=0.99`와 runtime
`watermelon-live-dog-99-1m-v2h`만 다르고 나머지는 exact 동일하다.

## 배포 검증

1. timer 없이 config SHA/SCM/no-clean/secret redaction을 확인한다.
2. Cat/Dog를 수동 1회씩 실행해 `$5`, threshold, v2h DB path, league hash, FOK-only,
   lifecycle `active`를 확인한다.
3. UCL/UEL가 live이면 exact identity와 regular-time HOME/DRAW/AWAY만 candidate인지 확인한다.
4. stop 후보는 SELL 직전에도 event `live=true`, `ended=false`와 market order-taking이 명시적으로
   확인돼야 한다. 종료 후 0.001 cleanup bid에서는 SELL이 없어야 한다.
5. open/pending/quarantined/orphan/fill-fee guards가 0이거나 증거 기반으로 설명되는지 확인한다.
6. 같은 DB를 이어 쓰는 수동 2회째를 검증한다.
7. 둘 다 runtime <45초, CRITICAL/HIGH 0이면 `* * * * *`를 활성화한다.
8. 자연 build 각 2회 뒤 daily-rsync로 새 epoch를 scan/sync/verify한다.

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-cat
uv run daily-rsync sync-job --job polybot-cat --strategy golden-watermelon-live --days 2
uv run daily-rsync verify --job polybot-cat --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-cat --strategy golden-watermelon-live

uv run daily-rsync scan --job polybot-dog
uv run daily-rsync sync-job --job polybot-dog --strategy golden-watermelon-live --days 2
uv run daily-rsync verify --job polybot-dog --strategy golden-watermelon-live
uv run daily-rsync locate --job polybot-dog --strategy golden-watermelon-live
```

첫 24시간 checkpoint는 각 v2h 첫 successful `run_audits.started_at`부터 exact half-open range로
고정한다. 이때 수익성, 0.96/0.99 우열, late-entry minute 또는 scale-up을 판정하지 않는다.

entry 종료 후 `close_only`로 바꿔 신규 BUY를 막고 bot-owned open trade만 관리한다. 모두
종결되면 `archive_only`로 전환한다. 긴급 중단은
[strategy-wind-down-playbook.md](../docs/strategy-wind-down-playbook.md)를 따른다.

과거 `watermelon-live-cat-98-1m-v2f`, `watermelon-live-dog-99-1m-v2f`와 v2g DB는 분석용
immutable archive이며 Jenkins에서 재사용하지 않는다.
