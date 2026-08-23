# Golden Raspberry

Golden Raspberry / Queue Echo는 public Gamma와 CLOB에서 가설 검정용 DB·raw payload·
lineage만 수집하는 accountless research-only 프로젝트다. `polybot-do/re/mi`는 DO/RE/MI
arm이 아니라 condition hash 3-shard이고, 각 shard의 동일한 raw stream에서 1·2·3회
지속 신호를 모두 계산한다.

현재 공식 cohort는 `queue-echo-v3`다. 동결 구간은 정확히
`[2026-08-23T20:00:00Z, 2026-09-22T20:00:00Z)`이며 5분 cadence와 0/1/2분 offset을
사용한다. `frozen-2026-08-13-external-v2`의 legacy data contract `queue-echo-v1`과 기존
runtime DB는 운영상 무효인 evidence로 보존하되 v3로 migration, UPDATE, DELETE하거나 v3
분석에 합치지 않는다.

## 안전 경계

- `simulation_mode=true`, `lifecycle_mode=archive_only`, `data_contract=queue-echo-v3`
- `--live`는 항상 실패한다.
- private key, funder, signature type, CLOB API credential 중 하나라도 환경에 있으면
  DB·log·HTTP session 생성 전에 실패한다.
- 주문 SDK, Trader, account, position, fill, realized P&L 경로는 없다.
- 결과는 `$5 displayed-book counterfactual`이며 actual fill 또는 realized P&L 증거가 아니다.
- cycle은 225초 cooperative budget을 사용하고, 남은 budget이 30초 margin에 도달하면
  새 HTTP를 중단한다. 모든 terminal duration은 240초 미만이어야 한다.

## 설치와 검증

```bash
cd golden-raspberry
uv sync --frozen --extra dev
(cd research/frozen-2026-08-23-v3 && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
```

config 확인은 read-only이며 DB를 만들지 않는다.

```bash
uv run polybot config --simulate --job raspberry-do-v3-shard-0
uv run polybot config --simulate --job raspberry-re-v3-shard-1
uv run polybot config --simulate --job raspberry-mi-v3-shard-2
```

`run`은 mock이 아니며 public network를 호출하고 v3 전용
`data/<v3-runtime-job>/trades_sim.db`와 log를 만든다. routine 검증에는 test/config를
사용하고, timer를 끈 명시적 deployment smoke에서만 아래 명령을 실행한다.

```bash
uv run polybot run --simulate --job raspberry-re-v3-shard-1
uv run polybot status --simulate --job raspberry-re-v3-shard-1
uv run polybot health --simulate --job raspberry-re-v3-shard-1
```

## v3 runtime과 timer

| Jenkins | v3 `RUNTIME_JOB` | shard | offset | Build periodically |
|---|---|---:|---:|---|
| `polybot-do` | `raspberry-do-v3-shard-0` | 0 | 0 | `0-59/5 * * * *` |
| `polybot-re` | `raspberry-re-v3-shard-1` | 1 | 1 | `1-59/5 * * * *` |
| `polybot-mi` | `raspberry-mi-v3-shard-2` | 2 | 2 | `2-59/5 * * * *` |

Freestyle concurrent build는 false, Jenkins build log retention은 14일로 둔다. 현재 멈춰
있는 timer를 유지한 채 DO→RE→MI를 한 번씩 순차 성공시키고, DB path와 runtime metadata가
v3인지 확인한 뒤에만 위 0/1/2 timer를 활성화한다. official start가 이미 지났더라도
backfill하지 않는다. 60초보다 늦은 invocation은 명시적 `LATE` skip이며 HTTP를 호출하지
않는다.

custom workspace는 각각 `/Volumes/t7/jenkins/polybot-do`,
`/Volumes/t7/jenkins/polybot-re`, `/Volumes/t7/jenkins/polybot-mi`다. 세 job이 workspace를
공유하거나 symlink로 우회하지 않는다. preflight는 `/Volumes/t7` external APFS mount,
UUID pin, sentinel, exact job workspace를 확인하고 job-bound daily-rsync marker를 원자적으로
기록한다.

## Jenkins v3 shell

각 job은 아래 `RUNTIME_JOB`, `SHARD_INDEX`, `OFFSET`을 표와 일치시킨다. clean/wipe를
사용하지 않으며, v2 runtime name이나 v2 frozen manifest를 배포 명령에 넣지 않는다.

```bash
#!/bin/bash
set +x
set -euo pipefail

RUNTIME_JOB=raspberry-do-v3-shard-0
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
export POLYBOT_EXPERIMENT_START_UTC=2026-08-23T20:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-22T20:00:00Z

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
(cd research/frozen-2026-08-23-v3 && shasum -a 256 -c MANIFEST.sha256)
"${UV}" run polybot config --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot run --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot status --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot health --simulate --job "${RUNTIME_JOB}"
```

## v3 evidence와 health gate

각 accepted invocation은 public HTTP 전에 `slot_id`, `slot_at`, `claimed_at`, lateness를
`cycle_slot_claims`에 원자적으로 claim한다. duplicate/late invocation은 explicit skip
evidence만 남기고 HTTP를 하지 않는다. `STARTED`가 review range ownership을 정하며 모든
STARTED run은 `SUCCEEDED` 또는 `FAILED` terminal duration/deadline evidence를 가져야 한다.

follow-up은 case claim을 먼저 durable commit하고 +60~75분의 첫 logical request start를
한 번만 기록한다. request 전 stale lease만 회수할 수 있다. request-start 이후 crash는
`STALE_REQUEST_UNKNOWN`으로 censor하고 재요청하지 않는다.

UNIVERSE와 FOLLOWUP_ONLY는 별도 source role이다. health는 같은 request에 YES/NO가 있었는지,
normalized quote가 존재했는지, quote-eligible인지, `EMPTY_BOOK`인지와 raw linkage를 각각
보고한다. FOLLOWUP_ONLY censoring은 UNIVERSE pair coverage 분모에 넣지 않는다.

7 complete UTC day gate는 다음을 모두 요구한다.

- expected accepted-slot SUCCESS coverage ≥95%, duplicate/late HTTP count 0
- STARTED-owned lifecycle terminal completeness 100%, cooperative deadline breach 0
- SUCCESS Gamma terminal-cursor sweep 100%, partial publish 0
- UNIVERSE normalized token coverage ≥95%, same-request pair atomicity 100%
- UNIVERSE raw payload linkage 100%; EMPTY_BOOK/missing/malformed/error 별도 보고
- SUCCEEDED와 FAILED를 합친 terminal runtime p95 <180초, max <240초
- 단일 v3 cohort, quick_check 정상, CRITICAL/HIGH issue 0

첫 24시간과 7일에는 collection health만 판정한다. 수익 가설의 confirmatory 판정은 동결된
30일 window와 MI gate가 완결되기 전에는 하지 않는다.

## 분석

daily-rsync가 검증한 v3 DB 절대 경로 세 개만 read-only analyzer에 명시한다.

```bash
uv run python scripts/analyze_experiment.py \
  --start 2026-08-23T20:00:00Z \
  --end 2026-09-22T20:00:00Z \
  --db DO=/absolute/path/to/raspberry-do-v3-shard-0/trades_sim.db \
  --db RE=/absolute/path/to/raspberry-re-v3-shard-1/trades_sim.db \
  --db MI=/absolute/path/to/raspberry-mi-v3-shard-2/trades_sim.db \
  --output /absolute/path/to/queue-echo-v3-health.json
```

## Data layout과 보존

```text
data/raspberry-do-v3-shard-0/trades_sim.db
data/raspberry-re-v3-shard-1/trades_sim.db
data/raspberry-mi-v3-shard-2/trades_sim.db
```

각 runtime directory에는 `.raspberry.lock`과 `logs/YYYYMMDD.log`도 있다. bot log는 45일
보존한다. DB는 append-only experiment evidence이므로 thinning/UPDATE/DELETE하지 않는다.
SQLite rollback `DELETE` journal을 유지하며, disk free 30GiB 미만 또는 사용률 90% 이상이면
network 전에 중단한다.

legacy `data/raspberry-do-shard-0`, `data/raspberry-re-shard-1`,
`data/raspberry-mi-shard-2` 및 external-v2 DB는 read-only 보존 대상이다. v3 process가 그
경로를 선택하거나 schema migration하는 것은 오류다.

운영·daily-rsync·복구는 [OPERATIONS.md](OPERATIONS.md), 고정 가설은
[STRATEGY.md](STRATEGY.md), 공식 동결 계약은
[v3 PREREGISTRATION.md](research/frozen-2026-08-23-v3/PREREGISTRATION.md)와
[DATA_CONTRACT.md](research/frozen-2026-08-23-v3/DATA_CONTRACT.md)를 따른다.
