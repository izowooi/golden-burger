# 데이터 구조와 identity

## Identity

Jenkins 표시 이름, 전략 폴더명, runtime `--job`, 계좌 이름은 서로 같은 값이 아니다.
`daily-rsync`는 다음 값을 독립적으로 보존한다.

| 필드 | 예시 | 의미 |
|---|---|---|
| source | `macmini-m5` | SSH/Jenkins 원본 |
| Jenkins job | `polybot-king` | Jenkins build와 console log 소유자 |
| strategy | `golden-queen` | 실행된 코드/전략 |
| runtime job | `queen-live-12h` | 봇 DB와 로그 namespace |
| workspace epoch | `external-v2` | 명시적으로 분리한 workspace 이동 evidence |
| deployment epoch | build 범위 | job의 전략/계좌 재사용 구간 |
| config cohort | `config_hash` | 실제 resolved parameter 집합 |

따라서 `default` 폴더를 Jenkins job 이름으로 바꾸지 않는다. 실제 Jenkins job이 이미
상위 namespace를 제공하므로 `polybot-yellow/golden-cherry/default`는 다른 job의
`default`와 충돌하지 않는다.

## Local layout

```text
data/
├── catalog.sqlite3
├── plans/
├── incoming/
├── sources/macmini-m5/jobs/
│   └── polybot-king/
│       ├── builds/000000/725.log.gz
│       ├── strategies/golden-queen/runtime/queen-live-12h/
│       │   ├── databases/latest/trades.db
│       │   ├── databases/latest/manifest.json
│       │   ├── databases/research/2026/08/05/
│       │   │   ├── trades_sim_20260805.db
│       │   │   └── manifest.json
│       │   ├── databases/pinned/<timestamp>/
│       │   └── logs/2026/07/20260729.log
│       └── workspace-epochs/external-v2/
│           └── strategies/golden-queen/runtime/queen-live-12h/...
└── bundles/<bundle-id>/
```

Jenkins build log는 strategy 아래로 복제하지 않는다. job/build가 canonical 위치이고,
catalog가 build와 strategy epoch를 연결한다. 봇 DB와 파일 로그는 원격 strategy/runtime
경로를 보존한다.

## Jenkins workspace allowlist

원격 workspace root는 `remote_workspace_roots` allowlist로 관리한다. 기본값은
`$JENKINS_HOME/workspace`이며 외장 volume은 예를 들어
`/Volumes/t7/jenkins/workspace`를 추가한다. Job workspace identity는 항상
`<allowlisted-root>/<jenkins-job>`이다. `config.xml`의 absolute `customWorkspace`도 이
정확한 형태일 때만 사용한다. root의 실경로와 Job workspace realpath를 비교하므로
symlink escape, mount 누락, allowlist 밖 경로와 공유 상위 directory 자체를 Job
workspace로 쓰는 구성을 거부한다.
저장한 plan 실행 시에도 현재 `customWorkspace`와 모든 allowlisted root를 다시 해석한다.
두 root에 같은 Job directory가 존재하면 정확히 한 후보의
`.daily-rsync-workspace.json`이 `schema_version=1`, 정확한 `job`, 정확한 absolute
`workspace` 세 key만 가져야 한다. marker 없음·복수·invalid는 모두 중단한다. 선택한
root/workspace의 `st_dev`와 marker digest는 plan identity에 들어가고 실행 직전에 다시
검사한다. console batch 직전, 각 artifact 직전, SQLite snapshot helper 안에서도 같은
identity를 다시 읽는다. root가 이동·교체됐거나 identity 없는 구버전 plan이면 실행하지 않는다. 서로
다른 원격 source path를 같은 로컬 destination으로 합치지 않는다.

workspace를 옮긴 뒤에도 같은 strategy/runtime/filename을 사용해야 하면
`config.local.toml`의 `[workspace_epochs]`에 exact current workspace를 명시해야 한다.
이 explicit mapping이 있을 때만 기존 충돌 evidence의 checksum을 다시 확인하고 conflict를
`RESOLVED` audit row로 남긴 뒤 새 workspace artifact를
`workspace-epochs/<label>/`에 저장한다. 기존 artifact는 파일을 보존한 채
`SOURCE_MISSING`으로 남는다. mapping이 없는 root 이동은 계속 fail closed한다.

## Research archive identity

| 원격 파일 | kind | mode | canonical | 로컬 위치 |
|---|---|---|---|---|
| `trades.db` | `database_live` | live | true | `databases/latest/` |
| `trades_sim.db` | `database_sim` | sim | true | `databases/latest/` |
| `shadow.db` | `database_sim` | sim | true | `databases/latest/` |
| `trades_sim_YYYYMMDD.db` | `database_research_archive` | sim | false | `databases/research/YYYY/MM/DD/` |
| 그 밖의 `trades*.db` | `database_safety` | DB identity 유지 | false | `databases/safety/` |

Research archive의 날짜는 filename의 UTC day다. source mtime·size, online backup
SHA-256, `quick_check`, sync cutoff와 manifest provenance는 canonical DB와 동일하게
보존한다. `locate`는 `current_databases`와 `research_archives`를 분리해 반환하므로 daily
shard를 현재 canonical state로 해석하지 않는다.
dated archive에는 정확히 하나의 `collection_contracts` row가 있어야 하며 contract는
`research-full-v1`, `database_utc_date`는 filename의 `YYYYMMDD`와 같아야 한다. 이
identity를 읽지 못하거나 날짜가 다르면 scan/plan/snapshot/local verify가 모두 fail closed한다.
날짜 범위를 지정하면 `research-full-v1`의 mutable `trades_sim.db`는 DB 내부
`collection_contracts.database_utc_date`가 현재 UTC day이고 범위에 들 때만 포함한다. 이 구분은 catalog의
`data_contract`와 `archive_date` metadata로 판정하며 일반 누적 simulation DB를 과거
범위에서 임의로 제외하지 않는다.

날짜 범위 검증은 요청한 UTC day 각각의 immutable archive가 **한 runtime 안에서** 모두
존재해야 완결된다. 현재 active shard는 `partial_active_dates`로만 보고하며 그 날짜의
완결 증거가 아니다. 일반
canonical/safety DB와 log는 coverage가 아니다. `locate`와
`verify`는 `archive_coverage`에 covered, partial active, missing, unavailable, conflicted 날짜를 분리해
반환한다. `SOURCE_MISSING` archive는 local file과 checksum이 남아 있고 source cutoff가
해당 날짜 다음 날 00:00Z 이상일 때만 coverage로 인정한다.

DB source fingerprint는 main file과 durable `-wal`의 존재 여부, size, mtime, inode를
합성한다. catalog의 `remote_size_bytes`도 두 파일의 합계다. `-shm`은 read-only open만으로도
생성·mtime 변경되는 volatile coordination 파일이라 fingerprint와 전송량에서 제외한다. 동일 remote path의 immutable
shard fingerprint가 한 번 동기화된 뒤 달라지면 `artifact_conflicts`에
`IMMUTABLE_REMOTE_CHANGED`를 기록하고 원래 local shard를 보존한다. workspace 이동이
같은 local destination과 충돌하면 `SOURCE_PATH_COLLISION`으로 기록한다.
prior artifact row 없이 먼저 발견된 open conflict도 이후 plan과 verify를 차단한다.

## Catalog

`catalog.sqlite3`는 source, job, artifact, build log, sync run, account epoch, pin과
open artifact conflict를
transaction으로 기록한다. 실제 DB를 합치거나 거래 row를 재작성하지 않는다. 서로 다른
job의 성과 비교는 catalog가 가리키는 독립 DB를 대상으로 수행한다.
artifact key는 `source × Jenkins job × kind × remote path`의 source-aware v2 digest다.
따라서 동일 경로를 가진 두 SSH host가 서로 덮어쓰지 않으며, schema v4 migration은 기존
artifact key와 연결된 pin 및 conflict 양쪽 참조를 함께 갱신한다.
