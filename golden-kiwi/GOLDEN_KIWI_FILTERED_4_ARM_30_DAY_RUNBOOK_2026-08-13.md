# Golden Kiwi filtered-universe — 4개 Arm 30일 실행 가이드

- 실험 구간: `[2026-08-13T00:00:00Z, 2026-09-12T00:00:00Z)`
- 상태: research/simulation-only, live source hard block 유지
- 사전등록: `research/frozen-2026-08-13/PREREGISTRATION.md`
- 기존 `2026-08-06` cadence-invalid DB와 결과를 합치지 않음

## 1. 이번 변경의 결론

Jenkins shell에 새 filter 환경변수를 추가할 필요가 없다. 코드와 `config.yaml`이 다음
request envelope를 고정하며, 다른 값으로 override하면 시작을 거부한다.

```text
Gamma liquidity_num_min = 20,000
Gamma volume_num_min    = 10,000 (cumulative volume)
최대 complete sweep     = 53 pages / 5,330 raw markets / 120 seconds
entry volume gate       = volume24hr 10,000 (별도 재검증)
```

실측 기준 기존 267 page·26,654 raw market은 22 page·2,182 raw market으로 줄었다.
strict entry 후보는 17개에서 17개로 유지됐다. 이 값은 point-in-time 결과이므로 운영 중
상한을 넘으면 partial data를 성공으로 처리하지 않고 build가 실패하는 것이 정상이다.

## 2. 최초 시작 순서

1. 네 job의 periodic trigger가 꺼져 있고 concurrent build도 꺼져 있는지 확인한다.
2. 그 전에 확인할 때는 shell의 `polybot config`까지만 수동 실행한다. `polybot run`을
   실행해 pre-window snapshot을 만들지 않는다.
3. 각 job에서 기존 workspace/DB를 지우는 **clean build를 정확히 한 번만** 설정하고,
   아래 periodic trigger를 2026-08-13 00:00Z(한국시간 09:00) 직전에 켠다.
4. 첫 clean build도 scheduler가 A/B/C/D 각각 UTC minute 0/1/2/3에 시작하게 한다. window
   안의 임의 시각에 수동 build하면 off-schedule SUCCESS가 되어 실험 전체가 무효다.
5. 각 첫 SUCCESS 직후, 다음 5분 trigger 전에 clean 옵션을 끈다. 이후 clean build는
   30일 lineage와 immutable DB를 없애므로 사용하지 않는다.
6. console/DB의 실제 `started_at` minute가 각 offset과 일치하는지 확인한다. queue 때문에
   다른 minute에 시작했다면 timer를 끄고 해당 새 DB를 다시 clean한 뒤 다음 정상 slot에서
   처음부터 시작한다.

| Jenkins job | Arm/runtime job | Trigger |
|---|---|---|
| `polybot-kiwi-a` | A / `kiwi-sim-a-3x1` | `0-59/5 * * * *` |
| `polybot-kiwi-b` | B / `kiwi-sim-b-3x2` | `1-59/5 * * * *` |
| `polybot-kiwi-c` | C / `kiwi-sim-c-5x1` | `2-59/5 * * * *` |
| `polybot-kiwi-d` | D / `kiwi-sim-d-5x2` | `3-59/5 * * * *` |

## 3. Jenkins shell

### A — `polybot-kiwi-a`

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

export POLYBOT_CONFIRMATION_STEPS=3
export POLYBOT_MIN_CUMULATIVE_MOVE=0.01
export POLYBOT_CADENCE_OFFSET_MINUTE=0

export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z

cd ./golden-kiwi

/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-a-3x1
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-a-3x1
```

### B — `polybot-kiwi-b` (primary)

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

export POLYBOT_CONFIRMATION_STEPS=3
export POLYBOT_MIN_CUMULATIVE_MOVE=0.02
export POLYBOT_CADENCE_OFFSET_MINUTE=1

export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z

cd ./golden-kiwi

/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-b-3x2
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-b-3x2
```

### C — `polybot-kiwi-c`

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

export POLYBOT_CONFIRMATION_STEPS=5
export POLYBOT_MIN_CUMULATIVE_MOVE=0.01
export POLYBOT_CADENCE_OFFSET_MINUTE=2

export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z

cd ./golden-kiwi

/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-c-5x1
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-c-5x1
```

### D — `polybot-kiwi-d`

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

export POLYBOT_CONFIRMATION_STEPS=5
export POLYBOT_MIN_CUMULATIVE_MOVE=0.02
export POLYBOT_CADENCE_OFFSET_MINUTE=3

export POLYBOT_EXPERIMENT_START_UTC=2026-08-13T00:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-12T00:00:00Z

cd ./golden-kiwi

/Users/jongwoopark/.local/bin/uv sync --frozen
/Users/jongwoopark/.local/bin/uv run polybot config --job kiwi-sim-d-5x2
/Users/jongwoopark/.local/bin/uv run polybot run --simulate --job kiwi-sim-d-5x2
```

## 4. 첫 build에서 확인할 값

`polybot config`에는 다음이 보여야 한다.

```text
Simulation only: True
Live execution: HARD DISABLED
Gamma request: liquidity >= $20,000, cumulative volume >= $10,000; budgets=53 pages / 5,330 markets / 120s
Promotion collection: ENABLED [2026-08-13..., 2026-09-12...)
Experiment evidence: schema=2, analyzer=3
```

각 runtime job과 arm/offset도 표와 정확히 일치해야 한다. 첫 `run` 로그에서는 complete
sweep의 page, market, elapsed가 각각 상한 이하이고 RunAudit가 SUCCESS인지 확인한다.
직접 condition follow-up은 main filter와 독립이므로 종료·저유동성 시장도 +60~75분
quote 관측 대상에서 빠지지 않는다.

DB에서 최근 sweep을 확인할 때는 해당 job workspace의 DB에 대해 다음을 사용한다.

```sql
SELECT schema_version, pages, raw_market_count,
       min_liquidity, min_volume,
       max_pages, max_markets, max_elapsed_seconds, elapsed_seconds,
       cursor_complete
FROM market_sweeps
ORDER BY completed_at DESC
LIMIT 5;
```

## 5. 24시간 운영 판정

- 네 job 모두 build가 겹치지 않아야 한다.
- cycle runtime p95가 5분 미만이어야 한다.
- sweep은 매번 cursor complete이고 53 page·5,330 market·120초 이하여야 한다.
- SUCCESS 시작 minute은 A/B/C/D 각각 0/1/2/3 modulo 5여야 한다.
- snapshot gap은 `[3,10]`분이어야 한다.
- 네 팔의 strategy source digest가 같아야 한다.

상한 초과로 build가 실패하면 page budget, gap 또는 cron을 완화하지 않는다. 네 timer를
다시 멈추고 Gamma 분포를 재측정한 뒤 새 코드·새 preregistration 여부를 검토한다.

## 6. 30일 뒤 판정

analyzer v3는 각 SUCCESS run의 sweep schema/filter/budget/elapsed까지 검증한다. Primary B의
quote-complete 50 signals, 30 events, 98.75% lower CI, 10.4bps stress, early/late,
quote/cadence 90%, strict audit CRITICAL/HIGH 0을 모두 통과해야 shadow review만 열 수 있다.
이 실험은 실제 수익 또는 live 승인을 의미하지 않는다.
