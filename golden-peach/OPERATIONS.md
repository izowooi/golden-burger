# Golden Peach 운영 절차

## Jenkins 배치

| Job | custom workspace | runtime | mode | cron |
|---|---|---|---|---|
| `polybot-eco` | `/Volumes/t7/jenkins/polybot-eco` | `peach-live-eco-3pp-1m-v1` | live | `* * * * *` |
| `polybot-fruit` | `/Volumes/t7/jenkins/polybot-fruit` | `peach-live-fruit-5pp-1m-v1` | live | `* * * * *` |
| `polybot-grey` | `/Volumes/t7/jenkins/polybot-grey` | soccer/MLB/NBA/NFL/NHL shadow 5개 | simulation | `* * * * *` |

Concurrent build는 금지하고 build discard는 14일로 둔다. 첫 검증 build 전 timer를 끄고,
exact pushed commit으로 수동 build를 통과시킨 뒤 timer를 켠다. clean build는 사용하지 않는다.
각 shell은 `uv sync`보다 먼저 `WORKSPACE`의 device가 `/`와 다른지 검사하고, sync 뒤에는
`scripts/verify_external_workspace.py --workspace "${WORKSPACE}" --min-free-gib 50`도 통과해야 한다.
T7이 분리된 채 같은 경로가 내부 디스크에 생긴 경우 주문 전에 실패한다.

## 공통 shell

Live 두 job의 기존 credential/address/signature binding은 값과 종류를 바꾸지 않는다. 아래
placeholder에 inline secret을 넣지 않는다.

### Eco A

```bash
#!/bin/bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_TAKE_PROFIT_DELTA=0.03

cd ./golden-peach
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --live --job peach-live-eco-3pp-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot run --live --job peach-live-eco-3pp-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot status --live --job peach-live-eco-3pp-1m-v1
```

### Fruit B

```bash
#!/bin/bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_TAKE_PROFIT_DELTA=0.05

cd ./golden-peach
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --live --job peach-live-fruit-5pp-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot run --live --job peach-live-fruit-5pp-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot status --live --job peach-live-fruit-5pp-1m-v1
```

### Grey shadow

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_FUNDER_ADDRESS
unset POLYMARKET_SIGNATURE_TYPE

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_TAKE_PROFIT_DELTA=0.05

cd ./golden-peach
/Users/jongwoopark/.local/bin/uv sync --frozen

jobs=(
  peach-shadow-1m-v1
  peach-shadow-mlb-1m-v2
  peach-shadow-nba-1m-v2
  peach-shadow-nfl-1m-v2
  peach-shadow-nhl-1m-v2
)
pids=()
for job in "${jobs[@]}"; do
  /Users/jongwoopark/.local/bin/uv run polybot run --simulate --job "${job}" &
  pids+=("$!")
done
failed=0
for pid in "${pids[@]}"; do
  if ! wait "${pid}"; then
    failed=1
  fi
done
exit "${failed}"
```

다섯 runtime은 서로 다른 DB를 사용하므로 병렬로 실행한다. 같은 runtime의 중복 실행은 각
DB의 cycle lock이 막는다. 어느 한 sport가 실패해도 이미 시작한 다른 네 수집기는 끝까지
실행하며, 마지막 exit code는 실패를 숨기지 않는다.

## 배포 검증

Console에서 다음을 확인한다.

- resolved mode/job/TP와 `strategy_source_digest`가 기대값과 일치한다.
- 한 cycle이 다음 분과 겹치지 않고 `.cycle-run.lock` skip이 반복되지 않는다.
- Gamma sweep `cursor_complete=true`, source clock exclusion과 event book 누락이 집계된다.
- Grey의 각 sport DB에 `execution_capacity_json`, sport/league/tag가 기록되고 전체 병렬
  실행이 60초 미만이다.
- live에는 BUY/SELL accepted와 confirmed fill을 구분한 로그가 남는다.
- 실패한 SELL은 degraded/event-local이며 다른 event entry를 전체 차단하지 않는다.

## daily-rsync

```bash
cd daily-rsync
uv run daily-rsync scan --job polybot-eco
uv run daily-rsync scan --job polybot-fruit
uv run daily-rsync scan --job polybot-grey
# scan 결과로 각각 별도 plan/sync 후
uv run daily-rsync verify --job polybot-eco --strategy golden-peach
uv run daily-rsync verify --job polybot-fruit --strategy golden-peach
uv run daily-rsync verify --job polybot-grey --strategy golden-peach
```

동기화 후 catalog가 새 external workspace epoch와 runtime을 가리키는지, DB SHA-256,
`quick_check`, latest successful run, direct six-book/source-clock coverage, PENDING/QUARANTINED 상태,
DB·로그 증가량을 확인한다. 기존 Quince/Melon/Watermelon epoch와 병합하지 않는다.
