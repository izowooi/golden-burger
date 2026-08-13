# 운영과 복구

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
