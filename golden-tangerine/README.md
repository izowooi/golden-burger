# Golden Tangerine

Golden Black의 prospective sports-resolution 가설을 실제 최소 금액으로 검증하는 독립 live
A/B 프로젝트다. `polybot-orange`는 arm A의 exact `$5` ask VWAP `[0.94,0.95]`,
`polybot-fox`는 arm B의 `[0.92,0.93]`만 사용한다. 둘의 나머지 universe, 주문 금액,
cadence, 보유 규칙과 안전 한도는 같다.

`golden-black`은 계속 accountless detailed collector로 남는다. 이 프로젝트만 기존 두
Jenkins wallet의 credential로 FOK BUY를 제출한다.

v0.2.0은 새 cohort용 safety/evidence hardening release다. arm band는 그대로이며 기본 주문액은
`$5`다. 주문액은 config/env로 바꿀 수 있지만 open-notional과 최소 주문 수량 검사를 동시에
통과해야 한다. 기존 live DB를 직접 고치지 않고 additive migration과 다음 자연 cycle의
권위 있는 Gamma/CLOB 대사로 과거 holding/pending을 복구한다.

성과 cohort는 모노레포 commit이 아니라
`config_hash × strategy_source_digest × mode × job_name`으로 나눈다. Git commit은 배포
provenance로만 남으므로 다른 하위 프로젝트 변경이 동일 Tangerine 실험을 분할하지 않는다.

- `polybot-orange` → `tangerine-live-a-94`
- `polybot-fox` → `tangerine-live-b-92`

## 핵심 계약

- Gamma `/events/keyset`: sports, `endDate` `(0h,6h]`, liquidity `>=10,000`, cumulative
  volume `>=5,000`, terminal cursor 필수
- 정확히 두 개의 label/price/token이 정렬된 market의 양 outcome을 검사. 팀명 moneyline
  (`negRisk=false`)과 Yes/No proposition(`negRisk=true`)을 별도 stratum으로 보존
- 정확히 `$5`를 소진하는 displayed ask walk의 VWAP가 arm band에
  **처음** 들어온 token만 후보
- 주문 직전 같은 exact `$5` full-depth walk를 다시 계산하고 two-decimal maker amount와
  venue-precision shares를 갖는 venue-tick FOK BUY 제출
- sports delay의 `DELAYED` FOK는 exact fill을 계속 대사한다. 30분 뒤에도 order detail이
  사라졌다면 current/pre-migration order catalog, 전체 authenticated token-trade catalog,
  exact cancellation 결과가 모두 zero-fill을 입증할 때만 `UNFILLED`로 종결
- 계정당 최대 3개, event당 1개, cycle당 신규 1개, 총 요청 원금 최대 `$15`
- `QUARANTINED`, unknown POST, orphan BUY submission도 position/notional capacity를 사용한다.
  모든 first-band candidate는 proven-no-POST queue evidence로 먼저 기록한다.
- terminal `MATCHED` order와 exact-order `CONFIRMED` fills, identity 및 fee proof가 모두 완전할
  때만 signed maker envelope 초과분을 최대 `$0.01`까지 허용한다.
- 조기 TP, stop, trailing, time exit, account-wide wind-down 없음
- midpoint가 사라지고 Gamma final payout이 아직 없으면 CLOB exact condition market의
  `closed=true`, 정확히 두 token, unique `winner=true`, aligned exact `0/1`을 모두 검증한다.
  selected token/outcome과 exact confirmed BUY fill이 일치할 때만 `RESOLVED`로 바꾸고,
  normalized public proof/hash를 append-only `resolution_observations`에 남긴다.
- 봇 DB가 직접 만든 trade만 resolution까지 추적; 지갑의 수동 포지션은 조회·편입·청산하지 않음
- Gamma current lookup이 resolved row를 찾지 못하면 `closed=true` exact-condition fallback을
  사용한다. exact `[0.5,0.5]`는 append-only proof를 가진 `VOID/RESOLVED`이며 SELL이 아니다.
- exact fee-proven resolved P&L이 configurable cumulative net-loss guard(기본 `$15`)에 도달하거나
  proof가 불완전하면 신규 entry를 fail closed한다.
- runtime DB별 nonblocking lock과 append-only cycle telemetry를 사용한다. POST를 가로지르는 hard
  process kill은 없다.
- live DB는 `data/<job>/trades.db`, simulation DB는 `trades_sim.db`로 분리

실험 entry window는 `[2026-08-20T14:08:00Z, 2026-09-19T14:08:00Z)`, resolution
follow-up cutoff는 `2026-10-19T14:08:00Z`다. 자세한 가설과 판정 기준은
[STRATEGY.md](STRATEGY.md), Jenkins 절차는 [OPERATIONS.md](OPERATIONS.md)를 따른다.

## 로컬 검증

```bash
cd golden-tangerine
uv sync --frozen --extra dev
uv run pytest

# 실제 값은 untracked .env 또는 Jenkins 기존 credential에서만 제공
uv run polybot config --simulate --job tangerine-local
uv run polybot run --simulate --job tangerine-local
uv run polybot status --simulate --job tangerine-local

uv run polybot analyze \
  --db A=/absolute/orange/trades.db \
  --db B=/absolute/fox/trades.db \
  --start 2026-08-20T14:08:00Z \
  --end-exclusive 2026-09-19T14:08:00Z
```

Analyzer는 exact half-open range에서 confirmed fills, fee completeness, proven one-hot/VOID
resolution, event clustering, arm 간 async cadence, episode funnel, unresolved/orphan exposure와
DB/WAL/SHM before/after checksum을 JSON으로 출력한다. evidence gap은 exit 3이다.

실주문은 명시적인 `--live`가 있어야 한다. `config.yaml`의 기본값은 simulation이다.
`Clean before checkout`, workspace wipe, 기존 DB 삭제를 사용하지 않는다.
