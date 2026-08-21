# Golden Tangerine 운영 절차

## 공통 Jenkins 안전 설정

- concurrent build 비활성화
- 첫 검증 전 timer 없음; 수동 성공 뒤 `H/5 * * * *`
- `Clean before checkout`, workspace wipe, DB 삭제 금지
- build discard는 14일 경과 기준으로 설정
- shell 시작은 `set +x`, `set -euo pipefail`
- 기존 job의 private key, funder address, signature type 값은 그대로 보존하고 출력하지 않음

## Arm A — polybot-orange

기존 credential export/binding 다음에 아래 공통값과 arm A만 설정한다.

```bash
export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_MIN_LIQUIDITY=10000
export POLYBOT_MIN_VOLUME_24H=0
export POLYBOT_MIN_CUMULATIVE_VOLUME=5000
export POLYBOT_MAX_POSITIONS=3
export POLYBOT_MAX_EVENT_POSITIONS=1
export POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE=1
export POLYBOT_YES_ONLY=false
export POLYBOT_ENTRY_PROB_MIN=0.94
export POLYBOT_ENTRY_PROB_MAX=0.95
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_ENTRY_HOURS_MAX=6
export POLYBOT_STOP_PRICE=0
export POLYBOT_EXPERIMENT_START_UTC=2026-08-20T14:08:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-19T14:08:00Z
export POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC=2026-10-19T14:08:00Z

cd ./golden-tangerine
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
"${UV}" run polybot config --live --job tangerine-live-a-94
"${UV}" run polybot run --live --job tangerine-live-a-94
"${UV}" run polybot status --live --job tangerine-live-a-94
```

## Arm B — polybot-fox

같은 shell에서 threshold와 runtime job만 바꾼다.

```bash
export POLYBOT_ENTRY_PROB_MIN=0.92
export POLYBOT_ENTRY_PROB_MAX=0.93

cd ./golden-tangerine
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
"${UV}" run polybot config --live --job tangerine-live-b-92
"${UV}" run polybot run --live --job tangerine-live-b-92
"${UV}" run polybot status --live --job tangerine-live-b-92
```

실제 Jenkins 구성에는 위 arm B 두 줄뿐 아니라 arm A 절의 모든 공통 frozen env가 들어가야 한다.

## Collector — polybot-black

`golden-black/OPERATIONS.md`가 권위다. custom workspace는
`/Volumes/t7/jenkins/polybot-black`이고 모든 Polymarket/CLOB credential을 `unset`한 뒤
`black-shadow-paired` simulation collector만 실행한다. credential binding과 live flag는 금지한다.

## 검증 순서

1. 세 job 모두 timer 없이 config 저장, SCM checkout 상태와 workspace 확인
2. Black 수동 build: external mount/free-space, terminal cursor, DB quick check 확인
3. Orange/Fox 수동 build: resolved arm, exact `$5.00` maker amount, venue-precision shares,
   FOK-only, open limit, DB path 확인
   - `DELAYED` FOK가 30분을 넘기면 exact order/trade/cancel conjunction으로
     `MATCHED/HOLDING` 또는 terminal zero-fill/`UNFILLED` 중 하나로 종결되는지 확인한다.
     일반 catalog 부재만으로 미체결 처리하면 실패다.
   - midpoint가 사라진 own holding은 Gamma final payout을 먼저 확인하고, 없으면 CLOB exact
     condition의 closed two-token unique one-hot `0/1` proof를 검사한다. selected token과
     confirmed BUY fill이 일치해야 하며 `resolution_observations`에 hash와 winner가 남아야 한다.
     이 경로는 SELL/redeem을 실행하지 않는다.
4. 최근 console에서 secret 노출·clean·old strategy 경로가 없는지 확인
5. `daily-rsync scan/sync-job/verify/locate`로 세 DB와 bot/console log 확인
6. 각 job에 `H/5` 활성화하고 두 번 이상 자연 build의 runtime/overlap/DB 증가 확인

첫 운영 collection-health 점검은 `2026-08-22T10:00:00Z` (`2026-08-22 19:00 KST`)에
수행한다. 이 점검에서는 수익성이나 threshold를 판단하지 않는다.

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-orange
uv run daily-rsync sync-job --job polybot-orange --strategy golden-tangerine --days 2
uv run daily-rsync verify --job polybot-orange --strategy golden-tangerine

uv run daily-rsync scan --job polybot-fox
uv run daily-rsync sync-job --job polybot-fox --strategy golden-tangerine --days 2
uv run daily-rsync verify --job polybot-fox --strategy golden-tangerine

uv run daily-rsync scan --job polybot-black
uv run daily-rsync sync-job --job polybot-black --strategy golden-black --days 2
uv run daily-rsync verify --job polybot-black --strategy golden-black
```

entry window 종료 뒤에도 기존 own trade의 resolution 대사를 위해 follow-up cutoff까지 job을
유지한다. 자동 entry clock gate가 신규 후보를 0으로 만들므로 중간 threshold를 바꾸지 않는다.
