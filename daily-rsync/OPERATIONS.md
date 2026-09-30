# 운영과 복구

## DB별 snapshot manifest

새 동기화는 같은 폴더의 `trades_sim.db`, `shadow.db` 등이 검증 기록을 덮어쓰지 않도록
각각 `trades_sim.db.manifest.json`, `shadow.db.manifest.json`을 기록한다. 새 manifest가
있으면 이것이 해당 DB snapshot의 권위다. 과거 `manifest.json`은 파일별 manifest가 없을
때만 읽으며, checksum·원격 경로·로컬 경로·source fingerprint가 그 DB에 맞아야 한다.
다른 DB의 기록이거나 이미 덮어써졌다면 추정 복원하지 않고 원본 재동기화가 필요한 gap이다.

고정 pin과 source-key별 review generation은 이미 폴더가 독립적이므로 기존
`manifest.json` 이름을 유지한다. 이전 원본의 manifest는 review generation으로 보존되고,
전환 실패 시 DB·closure·manifest와 catalog를 원래 상태로 복구한다. 공용 저장소 전환의
원본/변환본 대사는 파일별 snapshot과 이 lineage를 사용한다.

## 일상 실행

가장 단순한 시작·종료 방법은 저장소의 toggle script다.

```bash
cd /Users/izowooi/git/t1/daily-rsync
./daily-rsync-toggle.sh       # 꺼져 있으면 시작, 켜져 있으면 종료
./daily-rsync-toggle.sh status
```

Finder에서는 `Daily Rsync 켜고 끄기.command`를 더블클릭한다. 자동화나 장애 대응에서는
`start`, `stop`, `restart`, `open` action을 명시한다. 종료는 PID 파일과 process command가
모두 Daily Rsync임을 확인한 뒤 `SIGTERM`만 보내며, 10초 안에 끝나지 않아도 강제
종료하지 않는다.

`~/Applications/Daily Rsync.app`을 Finder에서 더블클릭한다. 앱은 이미 실행 중인
로컬 서버가 있으면 새 프로세스를 만들지 않고 브라우저만 다시 연다. 시작 로그와 PID는
각각 `data/ui-server.log`, `data/ui-server.pid`에 기록되며 둘 다 Git에서 제외된다.

웹 UI와 CLI는 같은 `data/catalog.sqlite3`와 artifact 경로를 사용한다. 따라서 UI에서
동기화한 뒤 CLI나 AI가 즉시 `verify`, `bundle`, 분석 명령을 이어서 사용할 수 있다.

웹 UI 시작 시 `/api/jobs`가 원격 Job inventory를 자동 scan한다. 사용자가 새로고침
버튼을 누를 때도 기존 브라우저 객체를 재사용하지 않고 scan 결과로 Job 목록과 현재
선택, 전략 dropdown, 요약 panel을 다시 만든다. 원격에서 선택 Job이 사라지면 선택을
해제하며, 성공한 scan 다음에는 local status metric도 갱신한다.

전략 identity는 raw 설정을 노출하지 않고 다음 세 신호로 교차 확인한다.

1. `config.xml` shell command의 안전한 `cd golden-*` 추출값
2. 최신 완료 Build가 남긴 구조화된 전략값
3. 최신 canonical DB의 `run_audits` 전략값

서버가 반환하는 `strategy_evidence.current_source`가 현재 판정에 사용한 신호이고,
`state`와 `conflict`가 신호 합의 여부를 나타낸다. UI에 conflict 경고가 보이면 과거 DB나
다른 전략 Build를 현재 전략 evidence로 간주하지 않는다. 설정 원문이나 environment,
credential은 전략 판정을 위해 출력하거나 browser response에 포함하지 않는다.

## 정상 실행

1. `doctor`
2. `scan --job <job>`
3. `plan --job <job> --strategy <strategy>`
4. plan의 파일 수·logical bytes·전송 상한과 양쪽 free space 확인
5. `sync --plan <id>`
6. `verify --job <job>`

`doctor`의 `workspace_roots`에서 configured path·realpath·`available`을 확인한다. 외장
volume root 하나라도 mount되어 있지 않으면 scan/snapshot을 실행하지 않는다. 외장
workspace를 쓰는 Job은 Jenkins `customWorkspace`가
`/Volumes/t7/jenkins/workspace/<job>`처럼 allowlisted root의 정확한 직속 Job 경로인지
확인한다. 공유 상위 directory(`/Volumes/t7/jenkins`)나 symlink workspace는 허용하지
않는다.

Pipeline이 기본 workspace와 외장 `ws(...)`를 모두 만들면 실제 외장 Job workspace
하나에만 `.daily-rsync-workspace.json`을 둔다. payload는
`{"schema_version":1,"job":"<JOB_NAME>","workspace":"<absolute workspace>"}` 세
key만 사용한다. `doctor`가 marker contract를 출력하며 scan inventory의
`workspace_identity`에서 선택 root의 `st_dev`와 marker digest를 확인한다. marker를
고치거나 volume을 교체했다면 기존 plan을 버리고 새로 scan/plan한다.

기존에 동기화한 Job을 새 workspace root에서 빈 DB로 다시 시작했다면 first sync 전에
`config.local.toml`의 `[workspace_epochs]`에 exact workspace와 epoch label을 등록한다.
`scan/plan`은 기존 충돌 row가 있더라도 보존 파일 checksum·DB `quick_check`와 새 destination
분리를 모두 확인한 경우에만 이를 `RESOLVED`로 바꾼다. 기존 evidence 삭제나 overwrite는
하지 않는다. `doctor` 출력의 `workspace_epochs`, plan JSON의 `workspace_epoch`, `locate`의
DB `workspace_epoch`가 같은지 확인한다.

처음에는 하나의 job으로 검증한 뒤 저장 profile을 늘린다. 선택하지 않은 job은 상세
build log를 조사하거나 전송하지 않는다.

### Scan과 Sync의 경계

- **Scan**: 원격 Job·Build·DB의 metadata와 identity를 읽고 inventory/catalog의 Job
  상태를 갱신한다. DB와 로그 본문은 가져오지 않는다. 웹 UI의 시작 및 Job 새로고침은
  이 단계까지만 자동 실행한다.
- **Plan**: 선택한 Job·전략·기간에 대해 전송 대상, 변경 없음, 예상 byte를 계산한다.
- **Sync**: 확인한 plan의 SQLite online snapshot과 로그를 실제로 local data root에
  전송하고 checksum·catalog 상태를 갱신한다.

따라서 자동 scan 후 화면에 새 전략이 표시되는 것과 해당 전략의 DB/log가 local에
존재하는 것은 별개다. 회고나 compact 검증에는 반드시 sync 완료, `locate`의 최신
attempt/success, `verify` 결과를 추가로 확인한다.

Research simulation은 active `trades_sim.db`와 UTC daily
`trades_sim_YYYYMMDD.db` shard를 함께 발견한다. Daily shard는 기본 plan 대상이고
`--days` 또는 `--from-date/--to-date`가 있으면 해당 UTC day 범위만 남는다.
dated shard의 filename day와 `collection_contracts.database_utc_date`가 다르거나 contract
row가 없으면 scan/plan이 fail closed한다. `research-full-v1` active shard는 현재 UTC day가
범위에 포함될 때만 선택하지만 mutable partial evidence이므로 완료된 UTC-day coverage는
rollover된 dated shard만 만족한다. 일반 누적 simulation DB는 계속 포함한다. 각 shard도 SQLite online backup,
원격·로컬 `quick_check`, SHA-256 검증을 통과해야 catalog에 승격된다. raw/blob/log table은
DB 안의 evidence이므로 별도 raw directory를 동기화하지 않는다. `database_safety`의
기존 opt-in 의미는 그대로 유지한다.

Accountless shadow runtime의 canonical `shadow.db`도 `database_sim`으로 발견하고 같은
SQLite online backup, 원격·로컬 `quick_check`, SHA-256 검증 절차를 적용한다. 이는
`research-full-v1` daily shard가 아니므로 날짜별 archive coverage 규칙은 적용하지 않는다.

외장 volume이 SQLite read lock을 지원하지 않아 `mode=ro` open이
`SQLITE_CANTOPEN`으로 끝나는 경우, snapshot helper는 한 번 재시도한 뒤 WAL sidecar가
없는 완전 checkpoint main DB에만 `immutable=1` fallback을 허용한다. 이 fallback은
source main/WAL fingerprint가 backup 전후 완전히 같고 snapshot `quick_check`와 SHA-256이
성공할 때만 유효하다. source가 한 번이라도 바뀌거나 WAL이 존재하면 snapshot을 폐기하고
fail closed한다. 원격 workspace에는 journal·sidecar를 만들지 않는다.

`research-full-v1` online snapshot은 collector rotation과 같은 advisory lock을 공유한다.
strategy directory별 exact mapping은 `golden-pomegranate=.pomegranate.lock`,
`golden-coconut=.coconut-cycle.lock`이다. 다른 strategy/lock 조합, symlink, non-regular
lock은 snapshot을 시작하기 전에 fail closed한다.

## 실패 처리

- SSH 실패: local latest와 catalog 완료 상태를 변경하지 않는다.
- remote snapshot 공간 부족: 해당 DB만 실패하고 로그 작업은 재시도할 수 있다.
- rsync 중단: `data/incoming/*.partial`을 유지해 다음 실행에서 재사용한다.
- SHA 또는 `quick_check` 불일치: incoming을 격리하고 기존 latest를 보존한다.
- immutable shard 변경: `IMMUTABLE_CONFLICT`와 open conflict를 남기고 기존 shard를 보존한다.
- open provenance conflict: prior artifact row가 없어도 이후 plan과 verify를 차단한다.
- workspace root 이동/중복: 유일 marker를 확인하고 저장 plan을 폐기한 뒤 새로 scan한다.
- workspace/provenance preflight 실패: 전송 전 실패도 `FAILED` sync run으로 기록한다.
- scan/plan 실패: plan 파일이 만들어지기 전이라도 deterministic `no-plan-*` plan ID의
  `FAILED` sync attempt를 남긴다. 과거 `SUCCESS`가 최신 시도로 보이게 두지 않는다.
- UI progress callback 실패: 전송 transaction과 sync run 종결을 방해하지 않는다.
- source 삭제: `source_missing`으로 표시하고 local 파일은 보존한다.
- local free space 50GB 미만: 새 sync와 bundle 생성을 차단한다.

앱은 원격 Jenkins workspace, build, DB, 로그를 삭제하지 않는다. 정리 대상은
`~/.cache/daily-rsync`의 자기 staging뿐이다.

## 보존

로그는 source timestamp가 365일을 넘으면 정리한다. catalog row는 남는다. bundle에
포함된 로그와 pinned DB, bundle 자체는 자동 정리하지 않는다.

```bash
# 항상 먼저 dry-run으로 파일 수와 회수 용량 확인
uv run daily-rsync prune --dry-run

# 확인 후 실제 적용
uv run daily-rsync prune --apply
```

## 회고 evidence 인계

AI 회고나 포스트모템을 시작할 때 경로를 손으로 추측하지 않는다.

```bash
uv run daily-rsync locate --job <jenkins-job>
uv run daily-rsync locate --strategy <golden-strategy>
uv run daily-rsync locate --strategy <golden-strategy> \
  --from-date <YYYY-MM-DD> --to-date <YYYY-MM-DD>
uv run daily-rsync verify --job <jenkins-job> --strategy <golden-strategy>
```

전략명만 조회하면 여러 Jenkins job deployment가 모두 반환될 수 있다. 잡명만 조회하면
그 잡의 과거 전략 epoch가 함께 나올 수 있다. 따라서 분석 cohort는 locate 결과의
`Jenkins job × strategy × runtime job`별로 나누고, `latest_sync_attempt`와
`latest_successful_sync`가 모두 `SUCCESS`이며 `verify`가 성공한 DB만 사용한다.
DB가 `SOURCE_MISSING`이면 보존된 과거 epoch로
취급하고 `source_completed_at`을 분석 cutoff로 명시한다. plan JSON이나 디렉터리
이름만 보고 성공 여부를 판단하지 않는다.

기간을 준 `locate`는 `research_archives`에 filename UTC day가 범위 안인 shard만
반환한다. `current_databases`는 active canonical DB만, `safety_databases`는 기존 안전
사본만 담는다. Archive의 `archive_date`, `remote_path`, `source_mtime_at`, local SHA와
sync cutoff를 보고서에 함께 남긴다.
또한 같은 runtime에서 요청 UTC day 전부가 `archive_coverage.covered_dates`에 있어야 한다.
missing/unavailable/conflicted 날짜, full-day cutoff가 증명되지 않은 `SOURCE_MISSING`, open
artifact conflict 중 하나라도 있으면 `verify`와 `analysis_ready`는 실패다. 범위 scan은
의도적으로 범위 밖에 둔 canonical DB를 `SOURCE_MISSING`으로 바꾸지 않는다.

## 실제 자료 확인

```bash
sqlite3 data/catalog.sqlite3 \
  "select kind,status,local_path,remote_size_bytes from artifacts order by synced_at desc;"

sqlite3 data/sources/macmini-m5/jobs/polybot-king/strategies/golden-queen/runtime/queen-live-12h/databases/latest/trades.db \
  "pragma quick_check;"
```

### Black RAW 공용 저장소 derivative

Golden Black의 검토된 세 RAW parent table은 원래 TEXT PK·rowid·FK·개인 관측 문맥을
남긴 skeleton과 공용 공개 projection으로 전환할 수 있다. 원격의
`<database>.raw-migration.json` (`black-raw-parent-derivative-v1`)은 전환 제안이며
그 자체로 verified evidence가 되지 않는다. 기존 `.storage-migration.json`과 동시에
존재하면 모호한 전환으로 거부한다.

동기화는 공개 projection closure v3와 남아 있는 public/mixed body를 먼저 검증한 뒤,
기존 로컬 원본과 incoming의 모든 논리 셀·원래 rowid·private 물리 값·FK·허용된
schema 변경을 다시 비교한다. SQLite backup의 header 차이가 있는 경우 원본 file SHA와
기존 verified snapshot SHA를 기존 manifest/fingerprint로 연결한다. 검사에 실패하면
이전 latest와 pin을 보존한다. 통과한 원본은 review/storage-migrations로 보존하며
새 pin에는 전환 lineage와 공용 dependency closure가 포함된다.

공용 record나 receipt, mixed body 중 하나라도 빠지면 verify와 pin을 차단한다.
mutable runtime을 다시 시작하기 전에는 이 고정 SHA sidecar의 검증·pin을 완료하고
승인된 배포 절차로 sidecar를 보존 위치로 옮겨야 한다. 이후의 새 write는 보통의
공용 DB dependency sync를 사용하며, stale sidecar의 SHA 불일치를 무시하지 않는다.

Watermelon historical main의 `watermelon-research-v401`은 별도의 명시적 schema
profile이다. `shared-raw-parent-derivative-v2`와 projection closure v4는 profile ID·version·
logical schema SHA를 함께 검증하며, Black v1의 closure v3와 혼합하지 않는다. 전환
범위는 해당 profile에 선언된 event/market/outcome/book 네 테이블이다. 다른 epoch의
DB를 이름이 비슷하다는 이유로 이 profile로 해석하지 않는다.

반복되는 mixed envelope는 strategy DB 내부의 PRIVATE packet dictionary에 보관할 수
있다. 이 dictionary와 owner·column binding은 private DB 및 pin에 그대로 남으며
공용 payload export 대상이 아니다. 대사는 로컬 marker를 원래 PMMIX bytes로 펼친 뒤
기존 mixed ownership·논리 셀을 검증한다. dictionary 누락, 다른 namespace, 손상된
packet, 다른 논리 열로의 marker 이동은 sync/verify/pin을 차단한다. 새 dictionary의
random owner 값이나 내부 ID 차이를 실제 시장 값 변경으로 해석하지 않는다.

Coconut historical `coconut-historical-v6`도 closure v4의 명시적 profile로 처리한다.
application ID `1195593521`, user version `6`, 원래 schema SHA와 dated shard의
`research-full-v1` 날짜를 검증한다. 15개 선언 표의 공개 source 필드를 연결하며,
working state·실험 판단·실행 가능액 계산 및 개인 packet은 private DB에 남는다.
현재 White recorder(`0x43535231`, user version `1`)는 같은 `golden-coconut` 이름을
사용해도 이 profile 대상이 아니다. historical archive의 source/job/runtime 출처를
현재 White 출처로 바꾸지 않는다.

Pomegranate의 `pomegranate-research-full-v4`는 `application_id/user_version=0/0`이며,
`collection_contracts`의 `research-full-v1`, schema version `4` 행이 버전 권위다.
12개 공개 source 표와 `orderbook_levels`의 원래 행을 함께 대사한다. 공개 trade tape는
account fill이 아니다. 파싱 판단·수집 시각·선별 이유는 private에 남고, 공개 raw preview와
이전 정산 원본 복사값만 공용화한다.

Pomegranate ladder의 원래 rowid와 nullable TEXT PK는 private binding에 보존한다.
`externalized_level_rows`, `declared-primary-key+rowid-v1`, `implicit_rowid_preserved=true`
증거가 없는 derivative는 승인하지 않는다. 과거 level link에 원래 rowid가 없으면 ordinal을
대신 만들어 검증하지 않고 원본이 필요하다고 실패한다. Native shared archive 및 inline/shared
원본의 재이관 모두 scan→sync→verify→pin에서 body·projection·ladder 의존성과 기존 pin 보존을
검사한다. 이 로컬 통합 경로의 검증은 실제 Pomegranate 원격 배포 완료를 뜻하지 않는다.

Raspberry의 `raspberry-queue-echo-v3`는 세 shard의 원래 metadata·frozen 계약·실험 기간을
검증한다. 공개 source 복사값을 가진 7개 표와 보존한 sparse level을 처리하며,
near-touch/entry flag와 첫 follow-up 요청·lease·terminal 이력은 private로 남는다.
기존 public 배열 본문은 scalar group에 중복 넣지 않는다. Native·inline 원본·이미 공용화한
원본의 재이관은 실제 SyncService의 verify/pin 경로에서 검증했다.

Strawberry는 `strawberry-last-mile-v1`과 `strawberry-followup-v2a-v4`를 구분한다.
v1의 mutable `latest_outcome_state`는 현재 private 문맥과 공개 record 포인터만 보관한다.
같은 공개 상태의 재사용은 기존 exact receipt tuple로 증명하며 별도 private 가격 이력을
추가하지 않는다. 가격 이력은 원래 immutable outcome 관측이 보존한다.

Frozen v1이 v2a의 source anchor인 경우 `<DB>.source-storage-transition.json`이 원래
anchor와 RAW 전환을 연결한다. 단순 `VERIFIED` 문구 또는 device-only 승인으로 대체하지
않는다. 동기화는 private receipt의 exact bytes SHA를 운반하고 기존 로컬 원본과 신규 DB의
전체 RAW·본문 proof를 다시 계산한 후 lineage/pin에 포함한다. Native reader는 그 뒤
anchor·seed·device-chain 의미를 별도로 검증한다. Receipt는 공용 CAS로 내보내지 않는다.
Snapshot linkage가 필요하면 RAW manifest에 먼저 추가한 뒤 source attestation을 발행하고,
원본을 검토 위치로 옮긴 다음 staged derivative와 두 sidecar를 canonical 경로로 이동한다.
Attestation 이후 RAW manifest를 바꾸면 checksum 불일치로 실패해야 한다.
