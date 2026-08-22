# Golden Watermelon 운영 절차

## Jenkins 안전 경계

두 신규 잡이 기존 `polybot-black` workspace를 공유하면 안 된다.

| Jenkins | exact custom workspace | runtime job | schedule |
|---|---|---|---|
| `polybot-white` | `/Volumes/t7/jenkins/polybot-white` | `watermelon-white-1m` | `* * * * *` |
| `polybot-grey` | `/Volumes/t7/jenkins/polybot-grey` | `watermelon-grey-5m` | `H/5 * * * *` |

concurrent build는 비활성화하고 Jenkins build discard는 14일로 둔다. Clean before checkout,
workspace wipe, credential binding은 사용하지 않는다. DB는 workspace의
`golden-watermelon/data/<runtime-job>/trades_sim.db`에 append-only로 남는다.

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
"${UV}" run polybot config --simulate --job watermelon-white-1m
"${UV}" run polybot run --simulate --job watermelon-white-1m
```

Grey는 마지막 두 command의 job만 `watermelon-grey-5m`로 바꾼다. 실험 parameter는 Jenkins
env로 override하지 않는다.

## 최초 배포

1. timer 없이 각 job을 수동 1회 실행한다.
2. console에서 `cursor_complete=true`, page 수 ≤4, `cadence_arm`,
   `quick_check=ok` 또는 scheduled lightweight/full check, external free space를 확인한다.
3. `polybot status`로 DB contract, episode/policy count를 확인한다.
4. White runtime이 45초 미만, Grey runtime이 240초 미만일 때만 timer를 켠다.
5. 최소 White 2회, Grey 2회의 자연 build를 확인한다.

실패하면 DB를 clean하지 않는다. timer를 끄고 schema/API/runtime 원인을 고친 뒤 같은 DB에서
재개한다. source/config identity를 바꾸는 수정이면 새 epoch를 만든다.

## Cadence 장애 기준

- White p95 ≥45초 또는 queued build가 생기면 1분 stop 관측이 성립하지 않는다.
- Grey p95 ≥240초면 5분 control이 성립하지 않는다.
- cursor cap에 도달하면 partial universe를 저장하지 말고 cycle을 실패시킨다.
- moneyline filter를 유지한 채 API payload/DB write 병목을 먼저 고친다.
- volume/liquidity 하한을 사후 추가해 runtime을 맞추면 universe가 바뀌므로 금지한다.

## 다음 health review

현재 실행 환경에서 사용자가 말한 “다음 날 19:00 KST”는
`2026-08-24 19:00 KST`(`2026-08-24T10:00:00Z`)이다. 그때까지는 수익성과 X/Y 선택을
판단하지 않고 collection health만 확인한다.

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

`verify`가 반환한 두 절대 DB 경로만 analyzer에 넘긴다.

```bash
cd ../golden-watermelon
uv run polybot analyze --simulate --job watermelon-white-1m \
  --db /absolute/white/trades_sim.db \
  --db /absolute/grey/trades_sim.db \
  --output /tmp/golden-watermelon-health.json
```

24시간 review 항목은 cadence, cursor completeness, strict moneyline classification,
book/full-depth coverage, path/stop retry, resolution attempt, cohort, DB integrity와 storage
growth다. ROI·best threshold·best stop은 보고하지 않는다.

## 장애 대응

- credential rejection: Jenkins binding/inline export를 제거한다.
- `EVENT_RELATION_NOT_UNIQUE` 또는 team metadata 부족: raw Gamma evidence를 확인하고
  fail-closed classifier를 완화하지 않는다.
- `EVENT_LIVE_FALSE`만 비정상적으로 많음: source 의미를 raw payload/공식 schema로 확인한 뒤
  새 preregistration 없이 eligibility 의미를 바꾸지 않는다.
- stop gap: trigger와 actual displayed VWAP 차이는 관측값이다.
- partial stop: remaining shares retry가 다음 cycle에 이어지는지 확인한다.
- storage gate: 외장 mount를 복구하며 DB를 삭제하거나 내부 disk로 fallback하지 않는다.
- DB 오류: timer를 중지하고 verified local copy로 조사한다. clean build 금지.
