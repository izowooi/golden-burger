# Golden Peach 운영 절차

## Jenkins 배치

| Job | custom workspace | runtime | mode | cron |
|---|---|---|---|---|
| `polybot-eco` | `/Volumes/t7/jenkins/polybot-eco` | `peach-live-eco-3pp-1m-v1` | live | `* * * * *` |
| `polybot-fruit` | `/Volumes/t7/jenkins/polybot-fruit` | `peach-live-fruit-5pp-1m-v1` | live | `* * * * *` |
| `polybot-grey` | `/Volumes/t7/jenkins/polybot-grey` | `peach-shadow-1m-v1` | simulation | `* * * * *` |

Concurrent build는 금지하고 build discard는 14일로 둔다. 첫 검증 build 전 timer를 끄고,
exact pushed commit으로 수동 build를 통과시킨 뒤 timer를 켠다. clean build는 사용하지 않는다.

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
/Users/jongwoopark/.local/bin/uv run polybot config --simulate --job peach-shadow-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job peach-shadow-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot status --simulate --job peach-shadow-1m-v1
```

## 배포 검증

Console에서 다음을 확인한다.

- resolved mode/job/TP와 `strategy_source_digest`가 기대값과 일치한다.
- 한 cycle이 다음 분과 겹치지 않고 `.cycle-run.lock` skip이 반복되지 않는다.
- Gamma sweep `cursor_complete=true`, source clock exclusion과 triad/book 누락이 집계된다.
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
