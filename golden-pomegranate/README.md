# Golden Pomegranate — Accountless Market Observatory

Golden Pomegranate는 Polymarket 공개 시장 상태를 장기간 보존하는 **계좌 없는 research-only
collector**다. 수익을 내는 전략 bot이 아니며 시장을 고르거나 주문하지 않는다. 매 cycle마다
Gamma의 `closed=false` keyset cursor를 끝까지 순회해 반환된 모든 market과 모든 outcome을
저장하고, 공개 CLOB
order book은 사전 고정한 deterministic rotation으로 일부만 표본 수집한다. 공식 public Data
API `/trades`는 persisted watermark와 source-window 분할을 사용하는 bounded rolling tape로
수집한다. 이는 15분 cadence의 polling 관측이며, WebSocket으로 모든 price/order-book tick을
연속 캡처하는 시스템이 아니다.

Source envelope는 Polymarket 공식 문서의 [Gamma keyset market pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination),
[public Data API trades](https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets),
[public CLOB batch books](https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body)을
기준으로 버전·request parameter·receipt clock을 DB에 남긴다. 문서나 upstream 응답 계약이
바뀌면 기존 row를 고치지 않고 source digest가 다른 새 collection cohort로 시작한다.

이 프로젝트에서 다음 항목은 모두 **N/A**다.

- wallet/account와 자금
- BUY/SELL, order submission, fill, position
- `ExecutionLedger`
- realized/hypothetical P&L, win rate, strategy promotion

`--live` 또는 credential 환경변수가 하나라도 있으면 network client나 SQLite를 열기 전에
hard fail한다. 정상 운영도 명시적인 `--simulate`만 사용한다.

## 수집 계약

### Gamma는 전수, CLOB book은 표본

Gamma 수집은 strategy filter가 없는 **full non-closed universe census**다.

1. `/markets/keyset`의 첫 cursor에서 시작한다.
2. `next_cursor`를 terminal cursor까지 따라간다.
3. source lifecycle envelope는 `closed=false` 하나다. `active=true`, sports/category,
   liquidity/volume/probability, end date, orderbook-enabled/accepting-orders와 standard binary
   여부로 market을 제외하지 않는다.
4. variable-length outcome과 token 배열을 순서와 source null을 보존해 전부 저장한다.
5. 한 page의 market에는 sweep 종료시각이 아니라 그 page를 실제 받은 UTC receipt clock을
   연결한다.
6. 같은 cursor가 반복되거나 page 하나라도 실패하면 그 sweep의 page, market, outcome,
   membership을 전부 rollback한다. incomplete sweep은 published evidence가 아니다.

이미 census에서 한 번 관측한 condition은 독립 resolution/redeemable watcher가 이후 closed
상태까지 추적한다. collector 시작 전에 이미 closed였던 market의 historical path와 resolution은
coverage 밖이다. 이를 사후 complete history로 표현하지 않는다. 과거 closed 전체를 15분마다
반복 fetch하지 않는 것은 명시적인 storage/source lifecycle 경계이지 strategy filter가 아니다.

Gamma의 `volume`과 `volume24hr`는 별도 source field다. 전자는 cumulative volume, 후자는
rolling 24-hour volume이며 서로 대체하거나 fallback하지 않는다. field가 없거나 파싱되지
않으면 `NULL`과 원인을 남기고 다른 값을 복사하지 않는다.

모든 successful sweep은 다음 분모를 복원할 수 있어야 한다.

- page별 request/receipt UTC clock, input/output cursor, raw row count
- sweep의 page/market/outcome count와 complete cursor
- 반환된 모든 market의 full membership
- market별 모든 outcome/token membership
- canonical serialization으로 계산한 membership digest
- parser/schema/source version과 collecting `run_id`

공개 CLOB book은 전체 token을 매번 조회하지 않는다. Gamma sweep의 market identity
(`condition_id`, 없으면 stable market fallback key)를 SHA-256으로 `bucket_count`개 bucket에
고정 배치한다. `sampler_slot=floor(cycle_now_epoch/(cadence_minutes×60))`이며
`sampler_slot mod bucket_count` bucket을 선택한다. 해당 bucket의 market은
`SHA-256(market_key + ":rank")` 순으로 고정 정렬한다. candidate가 cap보다 많으면
`rotation_offset=(bucket_visit_index × max_markets_per_cycle) mod bucket_candidate_count`의 cyclic
offset에서 cap만큼 선택해, frame이 안정적일
때 반복 방문마다 다음 window로 이동한다. 선택 market의 **모든 distinct public outcome token**을
요청한다. UTC daily shard에서 cycle 번호가 초기화되어도 wall-clock slot은 초기화되지 않는다.
각 요청에는 다음 selection-bias metadata를 같이 저장한다.

- Gamma market frame size, bucket candidate 수, configured market cap, 실제 selected/truncated 수와 rank
- sampler version, wall-clock slot, bucket/count/visit, cyclic offset/wrap과 hash/selection reason
- deterministic inclusion basis와 선택 market의 전체 token 수
- market/outcome/token identity와 request/receipt clock
- success/error/empty-book 상태

성공한 book observation은 configured top bid/ask levels와 exact raw public batch response를 함께
보존한다. raw response가 없거나 normalized top level과 연결되지 않으면 그 관측은 book 분석에
사용하지 않는다. 실패 token을 성공 표본에서 조용히 제외하지 않는다.

### Public Data API `/trades`는 bounded rolling tape

Data API 수집은 API key 없이 `https://data-api.polymarket.com/trades?takerOnly=true`만 읽는다.
한 economic fill의 maker/taker 양측 표현을 중복 수집하지 않고 taker-side 단일 row를 쓰는
명시적 source envelope다. 따라서 maker-side participant activity archive는 아니다. 이는 WebSocket
연속 capture도, price/order-book tick 전수 archive도, 무한 과거를 보장하는 complete trade
tape도 아니다. 각 cycle은 정확한 source window와 10,000-row budget 한계를 기록하는
**polling rolling observation**이다.

따라서 trade row 수와 `size × price` 합계는 이 taker-side economic-event tape 안에서만
해석한다. `proxyWallet`은 수집된 taker-side row의 지갑일 뿐이며, maker counterparty 수,
maker-side wallet 점유율, 양측 participant 활동량이나 participant-row volume을 이 데이터로
계산하지 않는다. 그런 분석에는 이 collector가 보존하지 않는 maker-side representation이
필요하다.

- 첫 run의 logical/sub-window 수집 baseline은 `source_target_end_epoch - 24h`로 고정한다.
- 그 24시간 bootstrap과 이후 backlog는 한 cycle에 최대 3,600초씩만 새로 전진한다. HTTP
  시도 64회, 전체 logical/sub-window node 32개, Data API worker runtime 120초 중 하나라도 소진되면
  `BUDGET_EXHAUSTED`, `possible_gap=1`을 append하고 complete watermark를 전진시키지 않는다.
  다음 cycle은 같은 안전한 경계에서 다시 시작한다.
- 이후 start는 마지막 persisted complete watermark에서 30분(1,800초)을 뺀 시각이다. 이 overlap은
  늦게 나타난 row를 다시 읽기 위한 것이며 canonical dedupe로 중복을 제거한다.
- `source_target_end_epoch = cycle_now - 300초`를 source의 최신 안정화 경계로 기록하고,
  backlog 예산으로 잘린 실제 요청 경계는 `bounded_target_end_epoch`에 별도로 기록한다. 따라서 가장
  최근 source 지연 구간을 완결됐다고 주장하지 않는다.
- Data API의 upper bound가 inclusive이므로 각 logical/sub-window는 `[start,end]`와 고정
  `offset=0`, request/receipt UTC, returned count와 canonical membership digest를 보존한다.
  midpoint split 경계에서 같은 row를 다시 받을 수 있으며 canonical trade ID로 dedupe한다.
- 한 source window가 10,000-row cap에 닿으면 midpoint로 재귀 분할하고 양쪽 window를 각각
  다시 수집한다.
- 하나의 epoch timestamp만 남은 window에서도 10,000행에 닿으면 `possible_gap=1`을 append하고 complete watermark를
  진행시키지 않는다.
- request 실패나 malformed response도 error component observation으로 남기며 watermark를
  진행시키지 않는다. 필수 economic field가 없거나 timestamp가 integer epoch가 아니거나
  requested window 밖인 row도 malformed로 보고 전체 window를 `ERROR` 처리한다.
- 정상 empty window와 trade가 하나도 없는 complete cycle은 명시적인 `EMPTY`이며 complete
  watermark는 전진한다. `source_target_end_epoch`가 이미 저장된 watermark보다 과거이면
  **clock regression** `ERROR`로 기록하고 source request와 watermark 전진을 모두 막는다.

trade identity는 허용된 trade field의 canonical economic hash를 먼저 만든다. 같은 window에
byte-identical economic row가 여러 개면 true multiplicity일 수 있으므로 안정적인
`occurrence_index=0..n-1`을 부여하고 `trade_id = hash(economic_hash | occurrence_index)`로
구분한다. 30분 overlap에서 같은 multiplicity set을 다시 보면 같은 trade ID가 만들어져
window/cycle 사이에서 dedupe된다. 전역 trade row와 각 source-window sweep membership을 모두
보존해 “새 row”와 “이번 요청에서 다시 본 row”를 구분한다. 저장 allowlist는 `proxyWallet`, `side`, `asset`, `conditionId`, `size`, `price`,
`timestamp`, `outcome`, `outcomeIndex`, `transactionHash`다. `name`, `pseudonym`, `bio`,
`profileImage`, `profileImageOptimized`와 `title`/`slug`/`icon`/`eventSlug` 같은 display profile/
presentation field는 raw JSON에도 보존하지 않는다.

### Component atomicity와 fail-visible missingness

원자성의 primary boundary는 Gamma census bundle이다. page/market/outcome/full membership/raw
evidence는 terminal cursor와 count/digest 검증 뒤 하나의 transaction으로 publish된다. Gamma
page failure나 repeated cursor면 이 census bundle만 전부 rollback한다.

CLOB sample과 Data API rolling tape는 같은 Gamma sweep/run에 연결된 **독립 source-component
observation**이다. success뿐 아니라 empty, retry exhaustion, malformed response, cap과
`possible_gap`도 append-only로 commit한다. secondary source 실패 때문에 이미 완결된 Gamma
census를 버리지 않는다. run summary는 `gamma_status`, `clob_status`, `trade_tape_status`와 각
coverage를 분리하며, 7-day health gate에서 secondary failure/missingness를 판정한다.

### Resolution과 redeemable은 독립 사실

`resolved`, winning outcome과 resolution source clock은 resolution observation이다.
`redeemable`과 그 source clock은 별도 observation이다. 둘 중 하나로 다른 하나를 추론하지 않고,
resolution을 synthetic SELL/fill/redeem transaction으로 변환하지 않는다. 계좌가 없으므로 실제
redeem transaction 수집은 범위 밖이다.

## Evidence profile과 SQLite shard

모든 DB는 append-only `research-full-v1` profile이다. `compact-v1`, cold rollup, sampling 후
Gamma row 삭제, retention `DELETE`, 기존 evidence `UPDATE`/`REPLACE`는 금지한다. parser나
schema 의미가 바뀌면 기존 row를 고치지 않고 version을 올린 새 cohort에서 수집한다.

기본 runtime layout은 다음과 같다.

```text
data/<job>/
├── trades_sim.db                 # 현재 UTC day의 active shard
├── trades_sim_20260806.db        # 닫힌 UTC day의 immutable shard
├── trades_sim_20260807.db
└── .pomegranate.lock             # 한 writer만 허용하는 process lock
```

닫힌 shard의 일반 이름은 `trades_sim_YYYYMMDD.db`다.

cycle 시작 시 active shard의 UTC date가 현재 cycle date와 다르면 다음 순서로 회전한다.

1. process lock을 얻는다.
2. 진행 중인 transaction이 없음을 확인하고 connection을 닫는다.
3. `PRAGMA quick_check`가 `ok`인지 확인한다.
4. `PRAGMA wal_checkpoint(TRUNCATE)`가 정확히 `(busy=0, log=0, checkpointed=0)`으로
   끝났는지 확인한다. reader가 WAL frame을 붙들고 있으면 회전하지 않는다.
5. fresh connection에서 `journal_mode=DELETE` ownership barrier를 통과해 idle reader도 과거
   WAL namespace를 더 이상 소유하지 않음을 증명한다. 잠겨 있으면 회전하지 않는다.
6. 다음 UTC day의 완전한 `research-full-v1` DB를 임시 파일에 먼저 만들고
   `quick_check`, WAL checkpoint와 file `fsync`를 끝낸다.
7. 같은 APFS volume에서 기존 active DB를 dated shard 이름으로 hard-link하고 directory를
   `fsync`한 뒤, 준비된 새 DB를 `trades_sim.db`에 atomic replace하고 directory를 다시
   `fsync`한다.
8. hard-link 뒤 process가 중단되어 active와 dated shard가 같은 inode인 상태는 다음 실행이
   인식해 새 active 설치부터 안전하게 재개한다.

23:59Z에 시작해 자정을 넘긴 sweep은 둘로 쪼개지 않는다. 그 sweep 전체를 시작일 shard에
commit하고 다음 cycle 시작 전에 회전한다. dated shard가 이미 존재하거나 `quick_check`가
실패하거나 WAL checkpoint/ownership barrier가 완결되지 않으면 덮어쓰지 않고 fail closed한다.

각 shard와 row에는 최소한 다음 version/provenance가 있어야 한다.

- `schema_profile=research-full-v1`과 schema version
- collector/parser/book sampler version
- Data API window/dedupe parser version과 complete watermark
- secret-free resolved config hash
- `strategy_source_digest`, Git commit, mode와 stable `job_name`
- `run_id`, source endpoint/request envelope version

Git commit은 provenance이며 cohort 경계는
`config_hash × strategy_source_digest × mode × job_name × schema_profile`이다.

## 설치와 로컬 실행

credential은 필요하지 않다. `.env`나 실제 key를 만들지 않는다.

```bash
cd golden-pomegranate
uv sync --frozen --extra dev
uv run pytest
uv build

uv run polybot config --simulate --job pomegranate-local
uv run polybot health --simulate --job pomegranate-local
uv run polybot run --simulate --job pomegranate-local
uv run polybot status --simulate --job pomegranate-local
```

`run`은 daemon이 아니라 정확히 한 cursor-complete cycle을 수행한다. `config`와 `status`도
mode를 생략하지 않는다. lifecycle은 오직 `archive_only`만 허용하며 `active`, `close_only`와
unknown value는 config 단계에서 거부한다. 다음 명령은 DB나 network를 열기 전에 **실패해야
정상**이다.

```bash
uv run polybot run --live --job pomegranate-local
POLYMARKET_PRIVATE_KEY=forbidden uv run polybot config --simulate --job pomegranate-local
```

실제 credential 값을 예제로 넣거나 console에 출력하지 않는다. 안전 block은 대표 key 하나만
검사하지 않고 private key, funder, signature type, CLOB API key/secret/passphrase 등 지원하는
credential alias 전체에 적용된다.

### CLI 의미

| 명령 | network | DB mutation | 용도 |
|---|---|---|---|
| `polybot config` | 없음 | 없음 | resolved config, cohort, DB path와 safety gate 확인 |
| `polybot health` | 없음 | 없음 | 기존 shard `quick_check`/profile/append-only guard 또는 새 DB path readiness 확인 |
| `polybot run` | 공개 Gamma/CLOB/Data API read | append-only | Gamma atomic census + secondary component observations |
| `polybot status` | 없음 | 없음 | 최근 run/sweep, shard, runtime/storage summary |
| `polybot export-manifest` | 없음 | 없음 | whole-shard checksum/기간/count manifest 출력 |

manifest는 DB를 수정하지 않으며 backup 검증 입력이다. raw market, question, token, book JSON을
stdout에 dump하지 않는다.

`status`, `health`, `export-manifest`는 evidence row나 WAL을 쓰지 않는 논리적 read-only
명령이다. 다만 SQLite가 active WAL DB를 여는 동안 transient `-shm` coordination file을 만들거나
mtime을 바꿀 수 있으므로, `-shm`은 durable evidence와 Daily Rsync fingerprint에서 제외한다.

## Disk guard와 보존

검증 책임은 계층으로 나뉜다. Jenkins preflight는 external mount/APFS/sentinel/volume UUID와
workspace UUID를 검사한다. `polybot run`은 network 요청과 write transaction 전에 disk 사용량,
single-writer lock, active shard rotation/`quick_check`를 검사한다. `polybot health`는 DB/path
readiness를 읽기 전용으로 보여 주지만 Jenkins mount identity 검사를 대신하지 않는다.

- filesystem 사용률 `>=70%`: warning, 10분 cadence 승격 금지
- filesystem 사용률 `>=80%`: collection hard stop
- free space `<150 GiB`: collection hard stop
- Jenkins mount/sentinel/UUID/APFS 검증 실패: checkout/collection hard stop
- `run`의 lock/active shard rotation/`quick_check` 실패: collection hard stop

warning은 데이터를 지울 권한이 아니다. collector에는 compact, rollup, prune, vacuum 기반
evidence 축약 명령이 없다.

dated shard는 **120일 whole-shard retention**을 권장한다. 이는 자동 삭제 설정이 아니다.
120일을 넘은 shard도 아래를 모두 만족한 뒤 운영자가 shard 파일 전체 단위로만 별도 보관
절차를 수행한다.

1. SQLite online backup 또는 closed shard copy 완료
2. source와 backup SHA-256 일치
3. backup `PRAGMA quick_check=ok`
4. manifest의 shard UTC date, source cutoff, schema/table count와 backup 실측 일치
5. workspace 밖 durable storage에 backup이 실제 존재

검증 전 삭제, row 단위 retention, 가장 큰 table만 삭제, `VACUUM`으로 증거를 다시 쓰는 작업은
금지한다. free-space hard stop에 닿으면 먼저 cadence를 30분으로 낮추고 backup/storage를
확장한다.

## Cadence 변경 gate

초기 Jenkins trigger는 `H/15 * * * *`다. 첫 7개의 완결된 UTC day 동안 15분 cadence를
유지하고 중간 결과를 보고 바꾸지 않는다.

10분 cadence는 7일 health report가 아래를 모두 만족하고 명시적 운영 검토를 거친 뒤에만
새 cohort로 허용한다.

- cycle runtime p95 `<8분`
- scheduled slot coverage `>=95%`
- successful sweep의 cursor/page-clock/full-membership/atomicity coverage `100%`
- repeated cursor와 partial published sweep `0건`
- dated shard rotation과 `quick_check` 성공률 `100%`
- CLOB selection metadata coverage `100%`, requested book observation coverage `>=95%`
- Data API logical source-window accounting `100%`, unresolved `possible_gap=0`, watermark monotonicity
- `1.2 × p95(daily shard bytes) × 120` forecast 후에도 사용률 `<70%`이고 free space
  `>=150 GiB`

어느 gate라도 실패하면 10분으로 올리지 않는다. p95가 13분 timeout에 접근하거나 slot
coverage가 계속 낮고 API/storage pressure가 원인이면 `H/30 * * * *`로 fallback한다. 동시
build, partial cursor, filter 추가, row 삭제로 cadence를 맞추지 않는다.

## Jenkins와 외장 APFS volume

저장소 [Jenkinsfile](Jenkinsfile)은 다음 계약을 고정한다.

- external APFS root `/Volumes/t7`
- job별 고유 workspace `/Volumes/t7/jenkins/workspace/${JOB_NAME}`
- 외장 workspace를 Daily Rsync가 유일하게 식별하는
  `.daily-rsync-workspace.json` exact marker
- `disableConcurrentBuilds()`
- `H/15 * * * *`, pipeline timeout 13분
- summary console log 120일 보존
- `UV_LINK_MODE=copy`
- explicit `config → health → run → status → health`
- credential binding/inline secret 없음

`/Volumes/t7`은 기본 예시다. 승인한 외장 volume 이름이 다르면 Declarative pipeline에서는
[Jenkinsfile](Jenkinsfile)의 `POMEGRANATE_MOUNT_ROOT` 값 하나를 바꾸고, 아래 sentinel 생성 경로도
같은 절대 경로로 맞춘다. job마다 임의로 다른 mount를 쓰지 않는다.

volume root에는 운영자가 한 번 만드는 sentinel이 필요하다. sentinel의 UUID는 현재 mount의
`Volume UUID`와 같아야 한다.

```bash
MOUNT_ROOT=/Volumes/t7
VOLUME_UUID="$(diskutil info "$MOUNT_ROOT" | awk -F: '/Volume UUID/ {gsub(/^[[:space:]]+/, "", $2); print $2; exit}')"
printf 'profile=golden-pomegranate-apfs-v1\nvolume_uuid=%s\n' "$VOLUME_UUID" \
  > "$MOUNT_ROOT/.golden-pomegranate-volume"
```

Jenkins는 APFS personality, sentinel profile, expected/current UUID가 모두 맞기 전에는 checkout이나
collector를 실행하지 않는다. mount point에 다른 disk 또는 빈 directory가 나타나도 local disk에
조용히 DB를 만들지 않는다.

### Jenkins Freestyle job

Freestyle을 쓰면 shell보다 먼저 Jenkins UI의 **Use custom workspace**를 켜고 exact path를
`/Volumes/t7/jenkins/workspace/${JOB_NAME}`로 설정한다. 다른 승인 volume을 쓰는 경우 이 UI path와
아래 `MOUNT_ROOT` 첫 줄을 함께 바꾼다. Build Environment의 Credentials Binding은 추가하지 않고,
Build Discarder는 summary console log를 120일 보존하도록 설정한다. Git SCM이 monorepo root를
workspace에 checkout한다는 전제에서 다음 shell을 그대로 사용한다.

```bash
#!/bin/bash
set +x
set -euo pipefail

MOUNT_ROOT=/Volumes/t7
EXPECTED_WORKSPACE="${MOUNT_ROOT}/jenkins/workspace/${JOB_NAME}"
SENTINEL="${MOUNT_ROOT}/.golden-pomegranate-volume"

if [ "${WORKSPACE}" != "${EXPECTED_WORKSPACE}" ] || [ ! -d "${MOUNT_ROOT}" ] || \
   [ -L "${MOUNT_ROOT}" ] || [ ! -f "${SENTINEL}" ] || [ -L "${SENTINEL}" ]; then
  echo 'Golden Pomegranate workspace or external mount is unsafe' >&2
  exit 2
fi

VOLUME_INFO="$(/usr/sbin/diskutil info "${MOUNT_ROOT}")"
FILESYSTEM="$(printf '%s\n' "${VOLUME_INFO}" | awk -F: '/File System Personality/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}')"
CURRENT_UUID="$(printf '%s\n' "${VOLUME_INFO}" | awk -F: '/Volume UUID/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}')"
EXPECTED_PROFILE="$(sed -n 's/^profile=//p' "${SENTINEL}")"
EXPECTED_UUID="$(sed -n 's/^volume_uuid=//p' "${SENTINEL}")"
WORKSPACE_UUID="$(/usr/sbin/diskutil info "${WORKSPACE}" | awk -F: '/Volume UUID/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}')"

if [ "${FILESYSTEM}" != APFS ] || \
   [ "${EXPECTED_PROFILE}" != golden-pomegranate-apfs-v1 ] || \
   [ -z "${CURRENT_UUID}" ] || [ "${CURRENT_UUID}" != "${EXPECTED_UUID}" ] || \
   [ "${WORKSPACE_UUID}" != "${EXPECTED_UUID}" ]; then
  echo 'Golden Pomegranate APFS sentinel or volume UUID verification failed' >&2
  exit 2
fi

# default Jenkins workspace와 external workspace가 둘 다 남아 있어도 Daily Rsync가
# 추측하지 않도록, UUID 검증을 통과한 이 workspace에만 exact marker를 원자적으로 쓴다.
/usr/bin/python3 - "${WORKSPACE}" "${JOB_NAME}" <<'PY'
import json
import os
from pathlib import Path
import sys

workspace = Path(sys.argv[1])
job = sys.argv[2]
payload = {
    "schema_version": 1,
    "job": job,
    "workspace": str(workspace),
}
target = workspace / ".daily-rsync-workspace.json"
temporary = workspace / f".daily-rsync-workspace.json.tmp.{os.getpid()}"
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(temporary, flags, 0o600)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    directory = os.open(workspace, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY

if [ "${POLYMARKET_PRIVATE_KEY+x}" = x ] || \
   [ "${POLYMARKET_FUNDER_ADDRESS+x}" = x ] || \
   [ "${POLYMARKET_SIGNATURE_TYPE+x}" = x ] || \
   [ "${POLYMARKET_API_KEY+x}" = x ] || \
   [ "${POLYMARKET_API_SECRET+x}" = x ] || \
   [ "${POLYMARKET_API_PASSPHRASE+x}" = x ] || \
   [ "${CLOB_API_KEY+x}" = x ] || [ "${CLOB_SECRET+x}" = x ] || \
   [ "${CLOB_PASSPHRASE+x}" = x ]; then
  echo 'Golden Pomegranate refuses credential-bearing Jenkins jobs' >&2
  exit 2
fi

export UV_LINK_MODE=copy
export LOG_LEVEL=INFO
export PYTHONUNBUFFERED=1
export POLYBOT_LIFECYCLE_MODE=archive_only
RUNTIME_JOB="${JOB_NAME//\//__}"
UV=/Users/jongwoopark/.local/bin/uv

cd "${WORKSPACE}/golden-pomegranate"
"${UV}" sync --frozen
"${UV}" run polybot config --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot health --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot run --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot status --simulate --job "${RUNTIME_JOB}"
"${UV}" run polybot health --simulate --job "${RUNTIME_JOB}"
```

## 로그와 운영 확인

console/log에는 summary만 남긴다.

- `run_id`, config/source digest prefix, schema profile
- cycle/page/market/outcome/membership/book count와 Data API source-window/trade count
- cursor-complete 여부, duration, active shard와 disk percentage/free GiB
- component별 success/empty/error, Data API watermark/`possible_gap`, resolution/redeemable count

question, slug 전체 목록, token별 raw response, Authorization header, environment dump와
credential-like 값은 출력하지 않는다. raw public response는 SQLite evidence이며 console
artifact가 아니다.

운영 확인은 다음 순서로 한다.

1. Jenkins preflight에서 APFS/sentinel/current·expected UUID, exact workspace와
   `.daily-rsync-workspace.json` marker 확인
2. `polybot config`에서 `research-full-v1`, simulation, stable job과 외장 volume DB path 확인
3. `polybot health`에서 DB/path readiness와 기존 shard `quick_check`/profile/append-only guard 확인
4. `polybot run` summary에서 disk/lock/rotation gate, cursor complete와 committed counts 확인
5. `polybot status`에서 최근 complete sweep, table별 row count와 trade watermark 확인
6. 다음 UTC day에 dated shard, checksum manifest와 새 active shard 확인

연구 목적과 사전 고정 gate는 [STRATEGY.md](STRATEGY.md), 최초 collection preregistration은
[2026-08-06 문서](research/2026-08-06-preregistration.md)를 따른다.
