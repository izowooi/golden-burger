# Golden Coconut 운영 절차

## Canonical Jenkins 경계

| item | exact value |
|---|---|
| Jenkins job | `polybot-gold` |
| workspace | `/Volumes/t7/jenkins/polybot-gold` |
| runtime | `coconut-major-sports-lifecycle-5m-v7` |
| schedule | `H/5 * * * *` |
| active DB | `data/coconut-major-sports-lifecycle-5m-v7/trades_sim.db` |

concurrent build와 workspace clean을 끈다. collector는 외장 APFS volume, exact mount/device UUID,
shared Raspberry sentinel과 off-volume UUID pin을 검증한다. 내부 disk fallback, symlink workspace,
credential binding은 허용하지 않는다.

checkout/clean 이후 `scripts/verify_external_workspace.py`가 다음 marker를 atomic replace와 directory
`fsync`로 기록해야 한다. payload에는 정확히 세 key만 있어야 한다.

```json
{"job":"polybot-gold","schema_version":1,"workspace":"/Volumes/t7/jenkins/polybot-gold"}
```

exact path는 `/Volumes/t7/jenkins/polybot-gold/.daily-rsync-workspace.json`이다. marker가 없거나 key,
value, path가 다르면 collector를 실행하지 않는다. [Jenkinsfile](Jenkinsfile)은 marker를 다시 읽어
exact object equality를 검사한다.

## 배포 전 검증

```bash
cd golden-coconut
uv lock
uv sync --frozen --extra dev
uv run pytest
uv build
```

Jenkins 실행에서는 dependency 변경이 없으므로 `uv sync --frozen`만 사용한다. shell은 `set +x`와
`set -euo pipefail`을 유지한다. environment key 이름을 먼저 검사해 모든 `POLYMARKET_*`,
`CLOB_*`, legacy credential alias, unknown `POLYBOT_*`가 하나라도 있으면 값이 비어 있어도 uv와
network 전에 실패한다. credential을 `unset`해 숨기지 않는다.

## Cycle 순서

1. exact external APFS/sentinel/UUID/workspace preflight
2. exact daily-rsync marker 작성·재검증
3. `polybot config --simulate`
4. storage preflight: free 150 GiB, warn 70%, stop 80%
5. atomic UTC 5분 slot claim
6. 서로 격리된 worker 다섯 개에서 soccer의 frozen 8개 대회 tag fan-out과
   MLB/NBA/NFL/NHL 단일 tag를 동시에 시작한다. `closed=false`, 실제 경기 시작 시각
   `start_time_min/max=slot-24h..slot+48h`로 읽고, 모든 physical cursor completion과
   client-side schedule을 재검증한 뒤 frozen family order로 정규화
7. discovery에서 빠진 tracked game의 Gamma event-by-ID lifecycle follow-up
8. public sports clock, same-cycle Gamma fallback, full books, optional public fee, resolution observation
9. atomic evidence publication과 `SUCCEEDED` 또는 evidence-backed `FAILED`
10. `status`, `health`

cooperative budget은 225초, 새 request stop margin은 30초, hard cycle은 240초, max receipt skew는
90초다. HTTP socket read는 5초, response 전체 attempt wall-clock은 15초, retry는 최대 2회다.
deadline/cursor/skew/attempt exhaustion에서는 episode를 승인하지 않는다.

## Daily-rsync와 analyzer

parent가 inventory/Jenkins routing을 통합한 뒤 `polybot-gold × golden-coconut ×
coconut-major-sports-lifecycle-5m-v7` 경계로 scan/plan/sync/verify한다. daily-rsync가 검증한 exact absolute
`trades_sim.db`와 필요한 `trades_sim_YYYYMMDD.db`만 analyzer에 넘긴다.

```bash
uv run polybot analyze --simulate --job coconut-major-sports-lifecycle-5m-v7 \
  --db /absolute/verified/trades_sim_20260827.db \
  --db /absolute/verified/trades_sim.db \
  --output /tmp/golden-coconut-health.json
```

같은 inode를 active/archive 두 번 전달하거나 cohort가 섞이면 analyzer가 실패한다. unique
`SUCCEEDED`이면서 five-family cursor-complete인 cycle만 estimand에 들어간다. PRESEASON과
REGULAR/POSTSEASON은 별도 strata로만 읽는다. missing sport가 있으면 macro null을 유지하며
health-only 결과로 profitability를 주장하지 않는다.

## 장애 대응

- family cursor incomplete: partial family census를 사용하지 않고 next cycle에서 source envelope 확인
- family worker failure: 다른 worker 결과를 partial census로 publish하지 않고 attempt receipt와
  FAILED run만 보존한 뒤 다음 slot에서 재시도
- attempt wall timeout: partial response receipt를 확인하고 bounded retry exhaustion이면 해당 cycle을
  제외한다. socket read가 이어져도 15초 전체 경계를 우회할 수 없다.
- lifecycle follow-up failure: 해당 cycle을 실패시키고 event ID/slug raw receipt를 보존한 뒤 다음
  cycle에서 재시도
- schedule envelope drift: raw page는 보존하되 신규 event를 거절하고 `start_time_*` 요청과
  canonical scheduled start의 half-open 판정을 대조
- identity drift: compressed Gamma raw와 frozen `SPORTS_REGISTRY.json` hash 대조
- book malformed/partial: full로 보간하지 않고 상태 보존
- fee unavailable: fallback 금지
- marker/mount failure: 내부 workspace로 우회하지 않음
- storage STOP: timer 중지, whole closed shard의 backup/verify/retention을 별도 승인 절차로 수행
- schema 변경: active DB에 migration하지 않고 새 frozen epoch/runtime 설계
