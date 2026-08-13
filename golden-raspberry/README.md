# Golden Raspberry

Golden Raspberry / Queue Echo는 전략을 먼저 실거래하지 않고, public Gamma와 CLOB에서
가설 검정용 DB·로그를 수집하는 accountless research-only 프로젝트다. 세 Jenkins job은
DO/RE/MI arm이 아니라 deterministic market hash shard이며, 각 shard 내부에서 1·2·3회
지속 신호를 같은 raw stream으로 계산한다.

## 안전 경계

- `simulation_mode=true`, `lifecycle_mode=archive_only`, `queue-echo-v1`
- `--live`는 항상 실패한다.
- private key, funder, signature type, CLOB API credential 중 하나라도 환경에 존재하면
  DB·log·HTTP session 생성 전에 실패한다.
- 주문 SDK, Trader, ExecutionLedger, account, position, fill, realized P&L은 없다.
- 결과는 `$5 displayed-book counterfactual`이며 실제 체결 증거가 아니다.

## 설치와 검증

```bash
cd golden-raspberry
uv sync --frozen --extra dev
uv run pytest
uv build
(cd research/frozen-2026-08-13-external-v2 && shasum -a 256 -c MANIFEST.sha256)
```

config 확인은 read-only이며 DB를 만들지 않는다.

```bash
uv run polybot config --simulate --job raspberry-do-shard-0
uv run polybot config --simulate --job raspberry-re-shard-1
uv run polybot config --simulate --job raspberry-mi-shard-2
```

한 번의 public collection cycle은 다음과 같다.

```bash
uv run polybot run --simulate --job raspberry-re-shard-1
uv run polybot status --simulate --job raspberry-re-shard-1
uv run polybot health --simulate --job raspberry-re-shard-1
```

`run`은 mock이 아니며 public network를 호출하고
`data/<runtime-job>/trades_sim.db`와 `data/<runtime-job>/logs/YYYYMMDD.log`에 기록한다.
routine 검증에는 test를 사용하고 명시적인 smoke/deployment에서만 `run`을 호출한다.

## Jenkins shell

각 job은 아래에서 `RUNTIME_JOB`, `SHARD_INDEX`, `OFFSET`만 표대로 바꾼다. clean/wipe는
사용하지 않는다.

```bash
#!/bin/bash
set +x
set -euo pipefail

# Job별로 아래 세 값만 표와 일치하게 바꾼다.
RUNTIME_JOB=raspberry-do-shard-0
SHARD_INDEX=0
OFFSET=0
MOUNT_ROOT=/Volumes/t7
EXPECTED_WORKSPACE="${MOUNT_ROOT}/jenkins/${JOB_NAME}"
VOLUME_SENTINEL="${MOUNT_ROOT}/.golden-raspberry-volume"
HOST_UUID_PIN=/Users/jongwoopark/.jenkins/golden-raspberry-volume.uuid

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
export POLYBOT_SHARD_COUNT=3
export POLYBOT_SHARD_INDEX="${SHARD_INDEX}"
export POLYBOT_CADENCE_OFFSET_MINUTE="${OFFSET}"
export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T12:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T12:00:00Z

/usr/bin/python3 ./golden-raspberry/scripts/verify_external_workspace.py \
  --mount-root "${MOUNT_ROOT}" \
  --workspace "${WORKSPACE}" \
  --expected-workspace "${EXPECTED_WORKSPACE}" \
  --job "${JOB_NAME}" \
  --sentinel "${VOLUME_SENTINEL}" \
  --host-uuid-pin "${HOST_UUID_PIN}" \
  --write-daily-rsync-marker
cd ./golden-raspberry
UV=/Users/jongwoopark/.local/bin/uv
"${UV}" sync --frozen
(cd research/frozen-2026-08-13-external-v2 && shasum -a 256 -c MANIFEST.sha256)
"${UV}" run polybot config --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot run --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot status --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot health --simulate --job "${RUNTIME_JOB}"
```

| Jenkins | `RUNTIME_JOB` | `SHARD_INDEX` | `OFFSET` | Build periodically |
|---|---|---:|---:|---|
| `polybot-do` | `raspberry-do-shard-0` | 0 | 0 | `0-59/5 * * * *` |
| `polybot-re` | `raspberry-re-shard-1` | 1 | 1 | `1-59/5 * * * *` |
| `polybot-mi` | `raspberry-mi-shard-2` | 2 | 2 | `2-59/5 * * * *` |

Freestyle concurrent build는 false, Jenkins build log retention은 14일로 둔다. 첫 build를
timer 없이 순차 성공시킨 뒤 timer를 활성화한다. `polybot-re`가 copied-job hold-off로
`buildable=false`이면 Configure 화면의 실제 Save 또는 같은 효과의 config save 후
`buildable=true`를 확인한다.

custom workspace는 각각 `/Volumes/t7/jenkins/polybot-do`,
`/Volumes/t7/jenkins/polybot-re`, `/Volumes/t7/jenkins/polybot-mi`다. 세 job이 하나의
workspace를 공유하거나 symlink로 우회하지 않는다. preflight는 `/Volumes/t7`이 실제 external
APFS mount인지, UUID pin과 sentinel이 일치하는지, `WORKSPACE`가 정확한 job 경로인지 확인한
뒤 job-bound `.daily-rsync-workspace.json`을 원자적으로 기록한다.

## 분석

daily-rsync로 받은 검증된 세 DB를 read-only analyzer에 명시한다.

```bash
uv run python scripts/analyze_experiment.py \
  --start 2026-08-13T12:00:00Z \
  --end 2026-08-14T12:00:00Z \
  --db DO=/absolute/path/to/do/trades_sim.db \
  --db RE=/absolute/path/to/re/trades_sim.db \
  --db MI=/absolute/path/to/mi/trades_sim.db \
  --output /absolute/path/to/queue-echo-health.json
```

24시간은 `HEALTH_ONLY_NOT_ENOUGH_DURATION`, 7일은 collection health만 판단한다. 수익
가설의 confirmatory 판정은 새 단일 cohort 30일 전에는 하지 않는다.

## Data layout과 보존

```text
data/<runtime-job>/
├── trades_sim.db
├── .raspberry.lock
└── logs/YYYYMMDD.log
```

bot log는 45일 보존한다. DB는 experiment evidence이므로 자동 thinning/UPDATE/DELETE하지
않는다. SQLite는 single-writer cadence에서 rollback `DELETE` journal을 사용해 process가
끝난 뒤 `daily-rsync`가 source를 read-only online backup할 수 있게 한다. transaction 중
생기는 `-journal`은 일시 파일이다. 매 cycle full Gamma membership은 normalized gzip으로,
CLOB batch raw body는 gzip과 SHA-256으로 보존한다. disk free 30GiB 미만 또는 사용률 90%
이상이면 network 전에 중단한다.

운영·daily-rsync·복구 점검은 [OPERATIONS.md](OPERATIONS.md), 고정 가설과 판정 기준은
[STRATEGY.md](STRATEGY.md), frozen 계약은
[현재 PREREGISTRATION.md](research/frozen-2026-08-13-external-v2/PREREGISTRATION.md)를
따르며, 최초 내부 workspace frozen 자료는 이력으로만 보존한다.
