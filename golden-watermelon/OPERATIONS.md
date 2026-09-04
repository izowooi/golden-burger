# Golden Watermelon v4b 운영 절차

## Jenkins 경계

| Jenkins | exact workspace | runtime | schedule | 시작 offset |
|---|---|---|---|---:|
| `polybot-white` | `/Volumes/t7/jenkins/polybot-white` | `watermelon-white-1m-v4b` | `* * * * *` | 30초 |

concurrent build는 끄고 build discard는 14일로 둔다. `Clean before checkout`, workspace wipe,
credential binding, 기존 DB migration/import를 사용하지 않는다. v4a 이하 DB는 그대로 두고 새
`data/watermelon-white-1m-v4b/trades_sim.db`를 만든다. `watermelon-grey-5m-v4b`는 향후
주기 대조용 예약 runtime이다. 현재 `polybot-grey`는 Golden Peach 자료 수집기이므로 바꾸지 않는다.

## Execute shell

White:

```bash
#!/bin/bash
set +x
set -euo pipefail

unset POLYMARKET_PRIVATE_KEY POLYMARKET_FUNDER_ADDRESS POLYMARKET_SIGNATURE_TYPE
unset POLYMARKET_API_KEY POLYMARKET_API_SECRET POLYMARKET_API_PASSPHRASE
unset CLOB_API_KEY CLOB_SECRET CLOB_PASSPHRASE

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export POLYBOT_LIFECYCLE_MODE=archive_only
export POLYBOT_SIMULATION_MODE=true

cd ./golden-watermelon
UV=/Users/jongwoopark/.local/bin/uv
START_OFFSET_SECONDS=30
LOCK_SHA="$(/usr/bin/shasum -a 256 uv.lock | /usr/bin/awk '{print $1}')"
STAMP=.venv/.uv-lock-sha256
if [[ ! -x ./.venv/bin/polybot || ! -f "${STAMP}" || "$(<"${STAMP}")" != "${LOCK_SHA}" ]]; then
  "${UV}" sync --frozen
  /usr/bin/printf '%s\n' "${LOCK_SHA}" > "${STAMP}"
fi
/bin/sleep "${START_OFFSET_SECONDS}"
./.venv/bin/python scripts/verify_external_workspace.py \
  --workspace "${WORKSPACE}" --min-free-gib 50
./.venv/bin/polybot run --simulate --job watermelon-white-1m-v4b
```

experiment parameter나 family를 Jenkins env로 override하지 않는다. 다섯 family Gamma cursor는
별도 session/worker에서 동시에 실행하고 frozen family order로 결합한다. 42초 network cutoff 뒤에는
새 요청을 시작하지 않으며 50초 cycle boundary까지 incomplete/FAILED evidence를 기록한다. process
alarm, hard kill, partial-success publication은 사용하지 않는다. release build에서만 Git SCM으로 exact commit을
checkout하고 검증 뒤 정기 build는 같은 external workspace를 `NullSCM`으로 고정한다.

## 배포 순서

1. White timer를 끈 채 config SHA, SCM, external workspace, no-clean/concurrent-off를 확인한다.
2. test/build를 통과한 exact commit을 push하고 White를 수동 1회 실행한다.
3. family별 numeric tag `100350/100381/745/450/899`, independent cursor complete, classifier/mapping hash,
   application ID `GWM4`, user version `401`, external storage gate를 확인한다.
4. Soccer/UCL/UEL, MLB/World Series, NBA/Finals, NFL/Super Bowl, NHL/Stanley Cup의 exact
   identity와 minor/college/e-sports/child/period/prop 제외를 확인한다.
5. accepted Soccer event는 HOME/DRAW/AWAY 3개, MLB/NBA/NFL/NHL은 one-condition HOME/AWAY 2개가
   완전하며 `$5..$1000` ladder가 기록되는지 확인한다.
6. Soccer source minute `75/80/85`만 replay되고 다른 네 종목 clock이 이 strata에 섞이지 않는지 본다.
7. White runtime <50초이고 network request가 42초 안에 끝나며 CRITICAL/HIGH 원인이 없을 때 timer를 켠다.
8. 자연 실행 2회 이상을 확인하고 daily-rsync로 새 epoch를 동기화한다.

## Daily-rsync 및 analyzer

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-white
uv run daily-rsync sync-job --job polybot-white --strategy golden-watermelon --days 3
uv run daily-rsync verify --job polybot-white --strategy golden-watermelon
uv run daily-rsync locate --job polybot-white --strategy golden-watermelon
```

`verify`가 반환한 exact absolute DB만 analyzer에 넘긴다.

```bash
cd ../golden-watermelon
uv run polybot analyze --simulate --job watermelon-white-1m-v4b \
  --db /absolute/white/trades_sim.db \
  --output /tmp/golden-watermelon-v4b-health.json
```

첫 health review는 첫 정상 배포 24시간 뒤다. family cursor/identity, strict whole-game
winner, full-depth, source clock, path/resolution, cohort, DB integrity, notional depth와 storage growth만
본다. ROI·best family/threshold/stop/minute/notional은 판정하지 않는다.

## 장애 대응

- identity drift: raw Gamma numeric tag/sport/root/season/team tuple를 대조한다.
- incomplete family cursor: partial universe를 사용하지 않는다.
- result identity gap: Soccer 3-token 또는 MLB/NBA/NFL/NHL 2-token completeness를 raw event와 대조한다.
- source clock gap: raw public source를 확인하며 kickoff 추정으로 대체하지 않는다.
- database epoch mismatch: v4a archive를 쓰지 말고 v4b path를 고친다.
- White p95 ≥50초, 42초 network cutoff 또는 queue 발생: timer를 끄고 source family별 병목을 고친다.
- storage gate: external mount를 복구하며 내부 disk fallback이나 DB 삭제를 하지 않는다.
- schema/identity 변경: 기존 DB에 `ALTER TABLE`하지 않고 새 prereg/runtime epoch를 만든다.
