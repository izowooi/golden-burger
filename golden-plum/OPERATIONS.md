# Golden Plum 운영 절차

## Jenkins 배치

| Jenkins job | workspace | runtime job | mode | 처치 | cron |
|---|---|---|---|---|---|
| `polybot-king` | 기존 내부 workspace 유지 | `plum-live-king-90-1m-v1` | live | 절대 익절 `0.90` | `* * * * *` |
| `polybot-queen` | 기존 내부 workspace 유지 | `plum-live-queen-95-1m-v1` | live | 절대 익절 `0.95` | `* * * * *` |
| `polybot-silver` | `/Volumes/t7/jenkins/polybot-silver` | `plum-shadow-silver-1m-v1` | simulation | 전체 탐색 격자 | `* * * * *` |

세 job 모두 동시 빌드를 금지하고 Jenkins build discard는 14일로 둔다. 첫 배포에서는
timer를 끈 채 정확한 pushed commit의 수동 build를 통과시킨 후 timer를 켠다. `clean`
build는 사용하지 않는다. King과 Queen은 서로 다른 기존 지갑을 그대로 쓰되, Golden Plum이
새 runtime DB에 만든 주문만 관리한다. 지갑의 수동 포지션은 편입하거나 청산하지 않는다.

Silver는 주문과 계정이 없는 원자료 수집기다. shell은 `uv sync` 전에 `WORKSPACE`가 실제
외장 디스크인지 확인하고, sync 뒤에도 `scripts/verify_external_workspace.py`를 실행한다.
T7이 분리된 상태에서 같은 이름의 내부 폴더가 생기면 네트워크와 DB를 열기 전에 실패해야 한다.

## 공통 실험 구간

```bash
export POLYBOT_EXPERIMENT_START_UTC=2026-08-31T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-14T00:00:00Z
```

최초 24시간은 수집·실행 건강 상태만 확인한다. 공통 경기 20개 전에는 A/B 우열을 판단하지
않고, Silver의 경기 100개 전에는 새 파라미터를 선택하지 않는다.

v1의 실제 first successful run은 Silver `12:26:10.205072Z`, King
`12:28:39.856965Z`, Queen `12:29:20.473917Z`다. 전체경기 v2는 배포 후 각 job의 첫 성공
run부터 별도 코호트로 시작한다. 첫 24시간 건전성은 해당 first run부터 정확히 24시간인
half-open range로 검사하고, 배포 전 공백을 cadence 누락으로 세지 않는다.

## King A shell

기존 Jenkins Credentials Binding의 private key, funder address, signature type은 값과 종류를
바꾸지 않는다. 아래 예시에 비밀값을 직접 적지 않는다.

```bash
#!/bin/bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_TAKE_PROFIT_PRICE=0.90
export POLYBOT_EXPERIMENT_START_UTC=2026-08-31T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-14T00:00:00Z

cd ./golden-plum
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --live --job plum-live-king-90-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot run --live --job plum-live-king-90-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot status --live --job plum-live-king-90-1m-v1
```

## Queen B shell

King과 동일하며 아래 두 값만 다르다.

```bash
export POLYBOT_TAKE_PROFIT_PRICE=0.95

cd ./golden-plum
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --live --job plum-live-queen-95-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot run --live --job plum-live-queen-95-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot status --live --job plum-live-queen-95-1m-v1
```

## Silver shadow shell

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
export POLYBOT_TAKE_PROFIT_PRICE=0.95
export POLYBOT_EXPERIMENT_START_UTC=2026-08-31T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-14T00:00:00Z

cd ./golden-plum
/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run python scripts/verify_external_workspace.py \
  --workspace "${WORKSPACE}" --min-free-gib 50
/Users/jongwoopark/.local/bin/uv run polybot config --simulate --job plum-shadow-silver-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job plum-shadow-silver-1m-v1
/Users/jongwoopark/.local/bin/uv run polybot status --simulate --job plum-shadow-silver-1m-v1
```

## 배포 검증

Console과 동기화된 DB에서 다음을 확인한다.

- resolved mode, runtime job, 익절값, `strategy_source_digest`가 사전 등록과 일치한다.
- 한 cycle이 다음 분과 겹치지 않고 `.cycle-run.lock` skip이 반복되지 않는다.
- kickoff부터 Gamma `ended=true` 전까지 HOME/DRAW/AWAY의 직접 YES/NO 여섯 호가가
  저장되고 source minute 또는 누락 사유가 함께 남는다.
- 같은 token의 최근 3회, 누적 상승 2%p, 회차당 하락 1%p 이하, 최초 0.75 교차가 영속 기록된다.
- source minute 상한과 80분 강제 청산이 없고, 종료는 TP·SL·검증된 resolution뿐이다.
- Silver의 모든 snapshot에 `$5/$10/$25/$50/$100/$250/$500` 증액 호가 깊이가
  `execution_capacity_json`으로 남고 live arm에는 이 추가 계산이 없다.
- live의 주문 응답과 확정 체결을 구분하며, 한 event의 실패가 다른 event 처리를 막지 않는다.
- `PENDING_BUY`/`PENDING_SELL`은 다음 cycle에도 대사되고, 180분 이후에는 거짓 완료가 아닌
  `QUARANTINED`로 격리된다.
- Silver DB는 계정·주문·실현 손익 자료로 해석하지 않는다.

## daily-rsync

```bash
cd daily-rsync
uv run daily-rsync scan --job polybot-king
uv run daily-rsync scan --job polybot-queen
uv run daily-rsync scan --job polybot-silver
# scan 결과별로 별도 plan/sync 후
uv run daily-rsync verify --job polybot-king --strategy golden-plum
uv run daily-rsync verify --job polybot-queen --strategy golden-plum
uv run daily-rsync verify --job polybot-silver --strategy golden-plum
```

검증된 catalog DB 절대 경로만 분석에 사용한다. DB SHA-256, `quick_check`, 최신 성공 run,
직접 여섯 호가와 full-match source-clock coverage, capacity JSON, cohort,
pending/quarantine 상태, 저장공간 증가량을 함께
기록하고 과거 Queen/Quince/Watermelon epoch와 병합하지 않는다.
