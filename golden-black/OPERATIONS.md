# Golden Black 운영 절차

## Jenkins 최초 설정

권장 Jenkins job은 `polybot-black`, exact custom workspace는
`/Volumes/t7/jenkins/polybot-black`이다. concurrent build를 비활성화하고 처음에는 timer 없이
수동 1회만 실행한다. Clean before checkout, workspace wipe, credential binding은 사용하지 않는다.

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

cd ./golden-black
UV=/Users/jongwoopark/.local/bin/uv

"${UV}" sync --frozen
"${UV}" run python scripts/verify_external_workspace.py \
  --workspace "${WORKSPACE}" \
  --min-free-gib 50
"${UV}" run polybot config --simulate --job black-shadow-paired
"${UV}" run polybot run --simulate --job black-shadow-paired
```

첫 build의 runtime, `cursor_complete=true`, page 수, `quick_check=ok`, free-space metric을 확인한 뒤
`H/5 * * * *`를 활성화한다. `status`와 `health`는 timed shell에 넣지 않는다. 큰 DB에서 매 cycle
deep check가 cadence를 잠식할 수 있다.

첫 manual build 뒤 `polybot status`에서 episode가 생기면 각 episode당
`HOLD_TO_RESOLUTION/STOP_0.80/STOP_0.70/STOP_0.60` 네 policy가 생기는지 확인한다. 이후 log의
`stop_attempts`와 `stop_exits`는 실제 주문 수가 아니라 displayed-book counterfactual 수다.

## Cadence 판정

5분으로 시작한다. server-side filter가 4페이지 상한을 넘거나 자연 build p95가 240초를 넘으면
수집 범위를 줄이지 말고 timer를 일시 중지해 원인을 고친다. 증거 없이 10분으로 바꾸면 새
cadence cohort가 되므로 중간 변경하지 않는다.

## Daily-rsync

24시간 후 다음처럼 current DB와 최근 log를 pull한다.

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-black
uv run daily-rsync sync-job --job polybot-black --strategy golden-black --days 2
uv run daily-rsync verify --job polybot-black --strategy golden-black
uv run daily-rsync locate --job polybot-black --strategy golden-black
```

`verify`가 반환한 절대 DB 경로만 analyzer에 넘긴다.

```bash
cd ../golden-black
uv run polybot analyze --simulate --job black-shadow-paired \
  --db /absolute/verified/trades_sim.db \
  --output /tmp/golden-black-analysis.json
```

## 장애 대응

- credential rejection: Jenkins binding/inline export를 제거한다.
- cursor incomplete: timer를 끄고 API response와 page count를 확인한다. partial sweep은 사용하지 않는다.
- storage gate: 외장 volume mount와 free space를 복구한다. DB를 삭제하지 않는다.
- overlap lock: concurrent build를 끄고 이전 process가 끝났는지 확인한다.
- stop gap: 기준가와 exit VWAP 차이는 오류가 아니라 관측값이다. trigger 가격으로 덮어쓰지 않는다.
- partial stop: 다음 cycle의 remaining-share retry가 이어지는지 확인하고 DB를 clean하지 않는다.
- DB 오류: job을 멈추고 online backup/verified local copy로 조사한다. clean build 금지.
