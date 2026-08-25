# Golden Watermelon Live 운영 절차

## Jenkins 공통 안전 설정

- `polybot-cat`, `polybot-dog`의 기존 private key, funder address, signature type은 그대로 보존
- secret 참조 전 `set +x`; credential 값을 console·문서·Git에 출력하지 않음
- concurrent build 비활성화
- 수동 build 검증 뒤 `* * * * *`
- build discard는 14일 경과 기준 유지
- `Clean before checkout`, workspace wipe, 기존 DB 삭제 금지
- 새 runtime job 이름을 사용하므로 과거 Papaya DB와 자동 분리
- 기존 Cat/Dog wallet의 API credential은 `derive_api_key()`로만 읽는다. live cycle에서
  API key 신규 생성이나 교체를 시도하지 않으며 derive 실패 시 fail closed한다.

## Cat — 0.98 arm

기존 credential export/binding 다음에 아래 shell을 사용한다.

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
export POLYBOT_ENTRY_PROB_MIN=0.98
export POLYBOT_ENTRY_PROB_MAX=0.999
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_ENTRY_HOURS_MAX=4
export POLYBOT_STOP_PRICE=0.70
export POLYBOT_EXPERIMENT_START_UTC=2026-08-24T13:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-08-31T13:00:00Z
export POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC=2026-09-07T13:00:00Z

cd ./golden-watermelon-live
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
"${UV}" run polybot config --live --job watermelon-live-cat-98-1m-v2d
"${UV}" run polybot run --live --job watermelon-live-cat-98-1m-v2d
"${UV}" run polybot status --live --job watermelon-live-cat-98-1m-v2d
```

## Dog — 0.99 arm

Cat과 공통 env를 모두 동일하게 두고 아래 두 값만 바꾼다.

```bash
export POLYBOT_ENTRY_PROB_MIN=0.99

cd ./golden-watermelon-live
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
"${UV}" run polybot config --live --job watermelon-live-dog-99-1m-v2d
"${UV}" run polybot run --live --job watermelon-live-dog-99-1m-v2d
"${UV}" run polybot status --live --job watermelon-live-dog-99-1m-v2d
```

## 배포 검증 순서

1. timer 없이 두 config를 저장하고 config SHA, SCM, workspace, no-clean을 확인한다.
2. Cat/Dog를 각각 수동 build한다. resolved threshold, `$5`, league hash, DB path,
   FOK-only, lifecycle `active`를 확인한다.
3. console에 secret, Papaya 경로, clean option, concurrent overlap이 없는지 확인한다.
4. 같은 job을 한 번 더 수동 실행해 기존 DB가 이어지고 sweep/run audit가 증가하는지 확인한다.
5. `* * * * *`를 활성화하고 최소 두 번의 자연 build 성공과 실행시간 `<45s`를 확인한다.
6. `daily-rsync`로 새 strategy/runtime epoch를 scan·sync·verify한다.

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

v2d 24시간 health checkpoint는 각 arm 첫 성공 run의 UTC 시작시각부터 정확히 24시간 뒤이며,
v2a/v2b/v2c와 합치지 않는다. 첫 run의 정확한 시각은 배포 후 `strategy_runs.started_at`에서
고정한다. 이 checkpoint에서는 수익성이나 0.98/0.99 우열을 판정하지 않는다.
entry 종료는 `2026-08-31T13:00:00Z`, resolution/stop follow-up cutoff는
`2026-09-07T13:00:00Z`다.
entry 종료 뒤에는 `close_only`로 전환해 신규 BUY를 막고 own open trade 대사만 지속한다.
긴급 중단은 공통 [wind-down 절차](../docs/strategy-wind-down-playbook.md)를 따른다.
