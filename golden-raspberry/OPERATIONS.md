# Golden Raspberry Operations — Queue Echo v3

이 문서는 현재 공식 `queue-echo-v3` 배포와 health review의 운영 계약이다. 공식 UTC
half-open window는 `[2026-08-23T20:00:00Z, 2026-09-22T20:00:00Z)`다. Jenkins job과
external workspace는 유지하지만 runtime name과 DB path는 v3 전용으로 바꾼다.

## v2와 v3 경계

기존 external-v2 runtime DB는 다음 경로에 그대로 보존한다.

```text
data/raspberry-do-shard-0/trades_sim.db
data/raspberry-re-shard-1/trades_sim.db
data/raspberry-mi-shard-2/trades_sim.db
```

v2는 atomic slot claim, durable first-follow-up claim, FAILED duration gate와 source-role
coverage가 없어서 v3 confirmatory evidence로 무효다. 삭제·migration·UPDATE·schema repair,
v3 DB와의 merge를 하지 않는다. v3 runtime은 다음 새 경로만 생성한다.

```text
data/raspberry-do-v3-shard-0/trades_sim.db
data/raspberry-re-v3-shard-1/trades_sim.db
data/raspberry-mi-v3-shard-2/trades_sim.db
```

## 고정 runtime과 timer

| Jenkins job | v3 runtime | shard | offset | Build periodically |
|---|---|---:|---:|---|
| `polybot-do` | `raspberry-do-v3-shard-0` | 0 | 0 | `0-59/5 * * * *` |
| `polybot-re` | `raspberry-re-v3-shard-1` | 1 | 1 | `1-59/5 * * * *` |
| `polybot-mi` | `raspberry-mi-v3-shard-2` | 2 | 2 | `2-59/5 * * * *` |

5분 cadence, 0/1/2 offset, shard hashing과 DO/RE/MI 정의는 v2 경제 가설에서 바꾸지
않는다. Freestyle concurrent build는 false, log retention은 14일로 유지한다.

## 배포 전 검증

프로젝트 root에서 실행한다.

```bash
uv sync --frozen --extra dev
(cd research/frozen-2026-08-23-v3 && shasum -a 256 -c MANIFEST.sha256)
uv run pytest
uv build
uv run polybot config --simulate --job raspberry-do-v3-shard-0
uv run polybot config --simulate --job raspberry-re-v3-shard-1
uv run polybot config --simulate --job raspberry-mi-v3-shard-2
```

config 출력에서 `data_contract=queue-echo-v3`, schema/source/prereg digest, 정확한 window,
runtime별 shard/offset과 서로 다른 v3 DB path를 확인한다. credential-like 환경 변수가 하나라도
있거나 `--live`이면 DB·log·HTTP 전에 거부되어야 한다.

## Jenkins shell 변경

README의 “Jenkins v3 shell”을 각 Freestyle job에 적용한다. job별 값은 아래와 같고,
나머지 threshold·HTTP·storage 설정은 `config.yaml`에서 고정한다.

```bash
# polybot-do
RUNTIME_JOB=raspberry-do-v3-shard-0
SHARD_INDEX=0
OFFSET=0

# polybot-re
RUNTIME_JOB=raspberry-re-v3-shard-1
SHARD_INDEX=1
OFFSET=1

# polybot-mi
RUNTIME_JOB=raspberry-mi-v3-shard-2
SHARD_INDEX=2
OFFSET=2

export POLYBOT_EXPERIMENT_START_UTC=2026-08-23T20:00:00Z
export POLYBOT_EXPERIMENT_END_UTC=2026-09-22T20:00:00Z
```

각 shell은 DB/public HTTP보다 먼저 `scripts/verify_external_workspace.py`를 실행하고,
다음 v3 manifest와 config를 검증한 뒤 run/status/health를 실행한다.

```bash
(cd research/frozen-2026-08-23-v3 && shasum -a 256 -c MANIFEST.sha256)
uv run polybot config --simulate --job "${RUNTIME_JOB}"
uv run polybot run --simulate --job "${RUNTIME_JOB}"
uv run polybot status --simulate --job "${RUNTIME_JOB}"
uv run polybot health --simulate --job "${RUNTIME_JOB}"
```

## 기동 순서

현재 parent가 멈춘 timer는 그대로 유지하고 아래 순서로 진행한다.

1. 세 Jenkins config 원본 SHA와 rollback copy를 보관한다.
2. `/Volumes/t7` APFS/external identity, UUID pin, sentinel과 job별 exact workspace를 확인한다.
3. shell에서 v3 runtime/date/manifest가 설정되고 v2 runtime 명령이 제거됐는지 확인한다.
4. timer 없이 `polybot-do → polybot-re → polybot-mi`를 한 번씩 수동 실행한다.
5. 각 build가 새 v3 DB를 열었고 v2 DB mtime/hash가 그대로인지 확인한다.
6. preflight, slot claim, terminal Gamma cursor, UNIVERSE pair/raw evidence, quick_check,
   `STARTED → SUCCEEDED|FAILED` terminal evidence와 cycle duration을 확인한다.
7. 세 build가 성공하면 timer를 각각 0/1/2분 expression으로 활성화한다.
8. 최소 두 번의 natural build 뒤 duplicate/late/overlap과 runtime/DB routing을 재확인한다.

clean workspace/build는 DB를 지우므로 사용하지 않는다. 세 custom workspace는
`/Volumes/t7/jenkins/polybot-do`, `/Volumes/t7/jenkins/polybot-re`,
`/Volumes/t7/jenkins/polybot-mi`이며 공유하거나 symlink로 우회하지 않는다.

공식 시작 시각이 이미 지났어도 missed slot을 backfill하거나 window를 이동하지 않는다.
configured offset의 가장 최근 slot보다 60초 초과 늦은 invocation은 explicit `LATE` skip을
기록하고 public HTTP를 하지 않는다. 따라서 실제 배포 전 누락 slot은 health report에 그대로
남으며, 95% coverage 또는 단일 30일 confirmatory gate를 훼손하면 결과는
`STOP / UNRESEARCHABLE`이다. 같은 data를 살리기 위해 window를 재고정하지 않는다.

## cycle budget incident gate

- cooperative budget은 225초, hard health limit은 terminal duration `<240초`다.
- 새 network는 30초 margin에서 중단한다. remaining budget이 connect/read timeout,
  retry sleep과 `Retry-After`보다 작으면 sleep/retry/request를 보내지 않는다.
- duplicate 또는 late slot은 HTTP 0건이어야 한다.
- 모든 `STARTED` run은 `SUCCEEDED` 또는 `FAILED` terminal evidence와 elapsed/deadline detail을
  가져야 한다. analyzer는 FAILED duration도 p95/max/deadline gate에 포함한다.
- 한 run이라도 240초 이상이면 timer를 중단하고 원인을 고친 뒤 새 자연 slot로 검증한다.
  해당 row를 삭제하거나 SUCCESS만 골라 health gate를 계산하지 않는다.

## follow-up recovery gate

case당 durable claim을 commit한 다음 +60~75분 window의 첫 FOLLOWUP_ONLY logical request를
시작한다. request-start 전에 죽은 stale lease만 120초 뒤 새 lease generation으로 회수한다.
durable request-start 이후 terminal publish 전에 죽었으면 `STALE_REQUEST_UNKNOWN`으로 censor하고
재요청하지 않는다. `EMPTY_BOOK`, source missing, malformed, invalid/depth 부족도 첫 attempt의
terminal disposition이며 이후 성공 quote로 교체하지 않는다.

## source-role health gate

UNIVERSE와 FOLLOWUP_ONLY를 같은 coverage 분모에 합치지 않는다. UNIVERSE는 다음을 따로
보고해야 한다.

- requested YES/NO pair와 same-logical-request atomicity
- normalized token/pair availability
- quote-eligible pair availability
- `EMPTY_BOOK`, missing, malformed, error 상태
- raw payload linkage

FOLLOWUP_ONLY는 claim, lease recovery, request-start와 terminal censor reason을 별도로 보고한다.
same-request atomicity가 100%여도 normalized/quote-eligible coverage를 대신하지 않는다.

7 complete UTC day health gate는 accepted expected-slot SUCCESS ≥95%, duplicate/late HTTP 0,
STARTED-owned terminal completeness 100%, terminal Gamma sweep 100%, UNIVERSE normalized token
coverage ≥95%, same-request pair atomicity 100%, raw linkage 100%, all-terminal runtime p95 <180초,
max <240초, cooperative deadline breach 0, 단일 cohort, quick_check 정상, CRITICAL/HIGH 0을
모두 요구한다.

## Daily Rsync

각 source는 `Jenkins job × golden-raspberry × v3 runtime job`으로 분리한다. local-only
`daily-rsync/config.local.toml`은 기존 internal/external workspace root allowlist를 유지한다.

```toml
remote_workspace_roots = [
  "/Users/jongwoopark/.jenkins/workspace",
  "/Volumes/t7/jenkins",
]
```

```bash
cd ../daily-rsync
uv run daily-rsync scan --job polybot-do
uv run daily-rsync scan --job polybot-re
uv run daily-rsync scan --job polybot-mi
# scan에서 v3 runtime/DB를 발견한 job별 별도 plan을 만든 뒤 sync한다.
uv run daily-rsync verify --job polybot-do --strategy golden-raspberry
uv run daily-rsync verify --job polybot-re --strategy golden-raspberry
uv run daily-rsync verify --job polybot-mi --strategy golden-raspberry
```

분석에는 catalog가 가리키는 verified v3 DB 절대 경로와 SHA-256만 사용한다. v2 DB가 scan에
함께 보이더라도 v3 label로 넘기지 않는다. canonical DB와 snapshot manifest의 journal mode는
`delete`여야 한다.

## Analyzer 명령

24시간 health는 공식 start부터 정확한 half-open range로 실행한다.

```bash
uv run python scripts/analyze_experiment.py \
  --start 2026-08-23T20:00:00Z \
  --end 2026-08-24T20:00:00Z \
  --db DO=/verified/raspberry-do-v3-shard-0/trades_sim.db \
  --db RE=/verified/raspberry-re-v3-shard-1/trades_sim.db \
  --db MI=/verified/raspberry-mi-v3-shard-2/trades_sim.db \
  --output /absolute/path/to/queue-echo-v3-24h-health.json
```

7일 health의 exclusive end는 `2026-08-30T20:00:00Z`, 30일 final exclusive end는
`2026-09-22T20:00:00Z`다. STARTED timestamp가 range ownership을 정하며 range 안에서
시작하고 밖에서 terminal된 run도 포함한다. 첫 24시간과 7일은 수익성·parameter 선택·live
승격을 판단하지 않는다.

## 24시간 뒤 요청 문장

> polybot-do/re/mi를 daily-rsync로 동기화하고 Queue Echo v3의
> `[2026-08-23T20:00:00Z, 2026-08-24T20:00:00Z)` collection health를 검증해줘.
> external-v2 DB는 제외하고 세 v3 runtime DB만 사용해. 수익성·튜닝은 판단하지 말고,
> accepted slot/duplicate/late HTTP, STARTED-owned SUCCEEDED·FAILED terminal duration과 225/240초
> deadline, terminal Gamma cursor, UNIVERSE same-request atomicity·normalized·quote-eligible·
> EMPTY_BOOK/raw coverage, FOLLOWUP_ONLY claim/start/recovery/censoring, cohort/source digest,
> DO·RE·MI lineage, +60~75분 outcome/control, quick_check와 storage를 각각 검증해줘.

## 7일 뒤 요청 문장

> Queue Echo v3 세 shard를 daily-rsync로 동기화하고
> `[2026-08-23T20:00:00Z, 2026-08-30T20:00:00Z)` 7-day health gate를 실행해줘.
> external-v2를 합치지 말고, SUCCESS만이 아니라 FAILED terminal duration도 runtime/deadline
> gate에 포함해. UNIVERSE와 FOLLOWUP_ONLY coverage를 분리하고 same-request atomicity,
> normalized quote availability와 EMPTY_BOOK를 각각 보고해. 수익성은 preliminary
> diagnostic으로만 표시하고 MI promotion이나 threshold 변경은 하지 마.

## 30일 뒤 요청 문장

> Queue Echo v3의 frozen range
> `[2026-08-23T20:00:00Z, 2026-09-22T20:00:00Z)`를 세 verified v3 DB로 분석해줘.
> external-v2와 다른 source/config cohort는 제외하고 health gate를 먼저 판정해. HIGH/CRITICAL,
> missed-slot coverage, lifecycle/deadline 또는 outcome/control evidence gap이 있으면 경제 가설
> 결론과 parameter tuning을 중단해. 모두 통과할 때만 frozen MI confirmatory gate와 paired
> MI−DO diagnostic을 실행하고 결과는 최대 `SHADOW_REVIEW_ONLY`로 제한해.

## 장애 원칙

- Gamma repeated cursor/page limit/partial page는 FAILED run이며 partial sweep을 publish하지 않는다.
- credentials 또는 `--live` 거부 실패는 즉시 모든 timer를 중단하는 CRITICAL incident다.
- disk free 30GiB 미만/90% 사용은 HTTP 전에 STOP한다. evidence 삭제로 우회하지 않는다.
- v3 rollback은 code/config 복구 후 새 v3 slot에서 검증한다. v2 runtime을 다시 공식 timer에
  연결하거나 v2 DB를 v3 schema로 고치지 않는다.
