# Golden Watermelon Live v3c 운영 절차

## Jenkins job matrix

기존 private key/funder/signature type은 각 job에 그대로 둔다. 아래 값만 family/arm별로 다르다.

| Jenkins | family | lower entry | runtime | 시작 offset |
|---|---|---:|---|---:|
| `polybot-cat` | `soccer` | `0.96` | `watermelon-live-cat-96-1m-v2h` | 0초 |
| `polybot-dog` | `soccer` | `0.99` | `watermelon-live-dog-99-1m-v2h` | 0초 |
| `polybot-bear` | `mlb` | `0.96` | `watermelon-live-bear-mlb-96-1m-v3a` | 10초 |
| `polybot-tiger` | `mlb` | `0.99` | `watermelon-live-tiger-mlb-99-1m-v3a` | 10초 |
| `polybot-lion` | `nhl` | `0.96` | `watermelon-live-lion-nhl-96-1m-v3a` | 20초 |
| `polybot-wolf` | `nhl` | `0.99` | `watermelon-live-wolf-nhl-99-1m-v3a` | 20초 |

모두 concurrent build와 `Clean before checkout`을 끄고 build discard 14일을 유지한다. 배포 검증이
끝날 때까지 timer를 끄며 workspace wipe나 기존 DB 삭제·migration/import를 하지 않는다.

## 공통 환경

Credentials Binding 또는 기존 secret export 다음에 아래를 둔다.

```bash
set +x
set -euo pipefail

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=active
export POLYBOT_BUY_AMOUNT=5
export POLYBOT_MIN_LIQUIDITY=5000
export POLYBOT_MIN_VOLUME_24H=0
export POLYBOT_MIN_CUMULATIVE_VOLUME=5000
export POLYBOT_MAX_POSITIONS=20
export POLYBOT_MAX_EVENT_POSITIONS=1
export POLYBOT_MAX_NEW_POSITIONS_PER_CYCLE=5
export POLYBOT_MAX_EMERGENCY_SELLS_PER_CYCLE=1
export POLYBOT_FOK_RECONCILIATION_TIMEOUT_MINUTES=2
export POLYBOT_YES_ONLY=true
export POLYBOT_ENTRY_PROB_MAX=0.999
export POLYBOT_ENTRY_HOURS_MIN=0
export POLYBOT_STOP_PRICE=0.70
export POLYBOT_EXPERIMENT_START_UTC=2026-08-29T04:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-05T04:00:00Z
export POLYBOT_EXPERIMENT_FOLLOWUP_END_UTC=2026-09-12T04:00:00Z
```

Soccer는 `POLYBOT_ENTRY_HOURS_MAX=4`, MLB는 `8`, NHL은 `5`다. matrix의 family, threshold와
runtime을 각 job에 넣는다.

## 배포와 빠른 정기 shell

timer를 끈 release build에서만 Git SCM으로 reviewed commit을 checkout한다. 성공 뒤 SCM은
`NullSCM`으로 바꿔 매분 전체 monorepo fetch가 여섯 번 겹치지 않게 하고, 같은 workspace의 배포본을
고정한다. 다음 release도 timer off → Git SCM checkout → 수동 검증 → NullSCM 순서다.

dependency hash가 바뀐 release에만 `uv sync`가 실행되고 평소 cycle은 이미 설치된 console script를
직접 호출한다. 종목군별 `START_OFFSET_SECONDS`는 같은 family의 A/B를 같은 초에 유지하면서 서로
다른 family의 API 폭주만 분산한다. process alarm이나 elapsed-time request suppression은 넣지 않는다.

```bash
cd ./golden-watermelon-live
UV=/Users/jongwoopark/.local/bin/uv
START_OFFSET_SECONDS=<0-or-10-or-20>
STAMP=.venv/.golden-watermelon-lock.sha256
LOCK_SHA=$(/usr/bin/shasum -a 256 uv.lock pyproject.toml | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')
CURRENT_SHA=$(/bin/cat "${STAMP}" 2>/dev/null || true)

if [[ ! -x .venv/bin/polybot || "${CURRENT_SHA}" != "${LOCK_SHA}" ]]; then
  "${UV}" sync --frozen
  /bin/mkdir -p .venv
  /usr/bin/printf '%s\n' "${LOCK_SHA}" > "${STAMP}"
fi

/bin/sleep "${START_OFFSET_SECONDS}"
./.venv/bin/polybot run --live --job <runtime>
```

DB 내부 `.cycle-run.lock`을 먼저 획득하므로 이전 build가 남아 있으면 새 build가 주문·DB 작업 전
정상 종료한다. Jenkins 자체 concurrent build도 계속 꺼서 workspace checkout 충돌을 막는다.

## 배포 검증

1. 여섯 timer가 모두 꺼졌고 SCM/no-clean/secret redaction/concurrent-off인지 확인한다.
2. `uv sync --frozen --extra dev`, 전체 test와 build를 통과시킨 exact commit을 push한다.
3. timer 없는 수동 build를 job별 1회 실행한다. console에서 family, `$5`, threshold, runtime,
   server gate `$5k/$5k`, FOK-only, lifecycle `active`, limits `20/1/5`, source digest를 확인한다.
4. Cat/Dog는 기존 v2h DB와 HOLDING position을 보존했는지 확인한다. Bear/Tiger/Lion/Wolf는 새
   v3a DB가 생성됐는지 확인한다.
5. `PENDING_BUY/PENDING_SELL/QUARANTINED`, orphan, fill-fee gap과 CRITICAL/HIGH가 없거나 기존
   evidence로 설명되는지 확인한다. `PENDING_SELL=0` 뒤 bot-owned open token의 DB 잔량과 인증
   지갑 잔고를 대사하되 수동 보유는 편입하지 않는다.
6. timer 없이 각 job을 한 번 더 실행해 dependency sync가 반복되지 않고 overlap lock, cursor와
   lifecycle이 정상이며 `cycle resources closed` 뒤 즉시 `Finished: SUCCESS`인지 확인한다.
7. SCM을 `NullSCM`으로 고정한다. offset을 포함한 Jenkins duration이 1분 아래이고 실패가 없으면
   `* * * * *`를 활성화한다.
8. 자연 build 각 2회 뒤 daily-rsync scan/sync/verify와 DB quick check를 수행한다.

```bash
cd ../daily-rsync
uv run daily-rsync scan --job <jenkins-job>
uv run daily-rsync sync-job --job <jenkins-job> --strategy golden-watermelon-live --days 2
uv run daily-rsync verify --job <jenkins-job> --strategy golden-watermelon-live
uv run daily-rsync locate --job <jenkins-job> --strategy golden-watermelon-live
```

첫 24시간 checkpoint는 job별 첫 successful 새 source digest `run_audits.started_at`부터 exact
half-open range로 고정한다. 이때 수익성, arm/sport 우열과 scale-up을 판정하지 않는다.

entry 종료 후 `close_only`로 바꿔 신규 BUY를 막고 bot-owned open trade만 관리한다. 모두
종결되면 `archive_only`로 전환한다. 긴급 중단은
[strategy-wind-down-playbook.md](../docs/strategy-wind-down-playbook.md)를 따른다.
