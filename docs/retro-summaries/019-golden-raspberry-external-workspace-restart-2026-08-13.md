# 019 — Golden Raspberry 외장 workspace 재시작 — 2026-08-13

작성일: 2026-08-13

대상: Jenkins `polybot-do` / `polybot-re` / `polybot-mi`, strategy
`golden-raspberry`, runtime `raspberry-do-shard-0` / `raspberry-re-shard-1` /
`raspberry-mi-shard-2`

## 0. 결론

세 Jenkins Job은 각각 다음 외장 APFS workspace에서 5분 cadence로 정상 실행 중이다.

| Jenkins job | Exact custom workspace | Timer | Official first build |
|---|---|---|---|
| `polybot-do` | `/Volumes/t7/jenkins/polybot-do` | `0-59/5 * * * *` | `#137 SUCCESS`, 3.655초 |
| `polybot-re` | `/Volumes/t7/jenkins/polybot-re` | `1-59/5 * * * *` | `#139 SUCCESS`, 3.429초 |
| `polybot-mi` | `/Volumes/t7/jenkins/polybot-mi` | `2-59/5 * * * *` | `#139 SUCCESS`, 2.994초 |

새 confirmatory window는 `[2026-08-13T12:00:00Z, 2026-09-12T12:00:00Z)`, 즉
KST 기준 `[2026-08-13 21:00, 2026-09-12 21:00)`이다. 공식 첫 세 slot과 그 이전 수동
검증 및 자연 timer build가 모두 성공했다. 수익성이나 threshold는 평가·변경하지 않았다.

과거 9시간 내부 DB를 새 외장 DB에 복사하지 않았다. 운영자가 새로 시작해도 된다고 한
범위에서 clean evidence epoch를 선택했기 때문이다. 다만 과거 자료는 삭제하지 않았다.
Mac Mini의 기존 내부 workspace와 MacBook의 기존 동기화 DB/log는 역사 evidence로
그대로 남고, 새 외장 자료와 로컬 경로가 섞이지 않는다.

## 1. 코드와 experiment identity

### Golden Raspberry

commit `45d34cb` (`Golden Raspberry 외장 실험 재시작 보호 추가`)을 배포했다.

- `/Volumes/t7` exact mount, APFS, `Internal=False`, volume UUID를 확인한다.
- volume sentinel과 Mac Mini 내부의 UUID pin을 교차 확인한다.
- `$WORKSPACE`, expected custom workspace와 mount의 `st_dev`가 일치해야 한다.
- 검사가 끝나기 전에는 `uv`, SQLite 또는 network collection을 시작하지 않는다.
- 성공 시 exact 3-key `.daily-rsync-workspace.json` marker를 원자적으로 기록한다.
- 새 frozen preregistration은 `research/frozen-2026-08-13-external-v2/`다.
- environment의 experiment start/end가 frozen 값과 다르면 config 단계에서 중단한다.

세 runtime의 source digest는 모두
`ec543461e939095a390486e34c9264e14fd2c2eae1975e9ff957c9a2ab4e9939`다. config hash는
DO `d423e02770ec…`, RE `32198668bb71…`, MI `aa5749392b8f…`로 shard별로 분리된다.

### daily-rsync

commit `ea86360` (`daily-rsync 외장 워크스페이스 epoch 분리`)으로 explicit
`workspace_epochs`를 추가했다. local-only `config.local.toml`에는 세 exact workspace를
`external-v2`로 등록했다.

이 기능은 충돌 무시 옵션이 아니다. 기존 파일 checksum과 DB `quick_check`, 현재 remote
workspace identity, 새 destination의 비충돌을 모두 확인한 경우에만 새 source를
`workspace-epochs/external-v2/`에 저장한다. 이미 발생했던 DO의 source-path conflict 2건은
감사 row를 삭제하지 않고 `RESOLVED`로 남겼다. 이전 내부 artifact는 파일을 유지한 채
`SOURCE_MISSING`으로 역사화했다. RE/MI도 기존 내부 자료를 보존하고 외장 자료를 별도
destination에 저장한다.

검증 결과:

- `uv run ruff check src tests`: PASS
- `uv run pytest`: 107 passed
- `uv build`: PASS
- Golden Raspberry test: 28 passed
- 20-project strategy contract verifier: PASS

## 2. Jenkins 최종 구성

`inspect-jenkins-job`의 redacted read와 targeted XML field 조회로 확인했다.

| Job | Config SHA-256 | Runtime / shard | Latest official-start build |
|---|---|---|---|
| `polybot-do` | `dae5715a97943520326d146cb8e9d5fa0b45529dd05f710719b9fde79223bd94` | `raspberry-do-shard-0`, 0/3 | `#137 SUCCESS`, start `12:00:11Z` |
| `polybot-re` | `654c448afaee3faf70afcf7dc6f659d630de5d44b439fe1ada92956f1ee9c647` | `raspberry-re-shard-1`, 1/3 | `#139 SUCCESS`, start `12:01:11Z` |
| `polybot-mi` | `80336e0aef25b7d4cdf104888be106c86ba03d25ce8ea4ed26430b4a7e196318` | `raspberry-mi-shard-2`, 2/3 | `#139 SUCCESS`, start `12:02:15Z` |

공통 확인 항목:

- `disabled=false`, `concurrentBuild=false`, queue 없음
- SCM `main`, remote `izowooi/golden-burger`, inline sensitive variable 없음
- SCM cleanup extension 0개 — build마다 DB를 지우지 않음
- Jenkins build retention `daysToKeep=14`, `numToKeep=-1`
- credential-like environment를 모두 unset하고 simulation + `archive_only`로 실행
- preregistration manifest를 매 build 검증
- `config → run → status → health`가 모두 성공해야 build SUCCESS

수동 build는 DO `#131`, RE `#132`, MI `#132`가 성공했다. 그 뒤 timer를 켜고 각 Job에서
공식 시작 전 자연 build를 두 번 이상 확인했으며, 공식 window 첫 build까지 성공했다.

## 3. 외장 저장공간

`/Volumes/t7`는 APFS external volume으로 확인됐다. 최종 확인 시 free는
961,174,688KiB, 약 916.6GiB였다. 세 Job workspace는 각각 약 32.3~32.4MiB이며 여기에는
Git checkout 전체가 포함된다. 첫 공식 cycle 뒤 DB 합계는 약 9.69MiB다.

Jenkins console log는 custom workspace가 아니라 Mac Mini 내부 `$JENKINS_HOME/jobs/...`
아래에 남는다. 이 세 Job에는 14일 build retention을 적용했으므로 무제한 증가하지 않는다.

## 4. 최종 daily-rsync evidence

공식 첫 slot까지 다시 `scan → plan → sync → verify`했다. 최신 attempt와 최신 successful
sync가 같고 failure, retention skip, open conflict는 모두 0이다.

| Jenkins | Source cutoff UTC | Sync finished UTC | DB SHA-256 | Verify |
|---|---|---|---|---:|
| `polybot-do` | `2026-08-13T12:00:14Z` | `2026-08-13T12:02:42Z` | `303fa4bddb4973762716d8c4fe5edf72d6352716d673e0f4b9245ec0483f4bbc` | SUCCESS · 141 |
| `polybot-re` | `2026-08-13T12:01:14Z` | `2026-08-13T12:02:54Z` | `07faf143f4bea47649b07ebc2aa727681497f54f11792ce6f4c07870493e160e` | SUCCESS · 143 |
| `polybot-mi` | `2026-08-13T12:02:18Z` | `2026-08-13T12:03:05Z` | `3b9ce80066e0ca3ac3bdd3d50ce279fcc3d18162a0e8d2548683326ace786ea5` | SUCCESS · 143 |

Verified DB 절대 경로:

- `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-do/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-do-shard-0/databases/latest/trades_sim.db`
- `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-re/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-re-shard-1/databases/latest/trades_sim.db`
- `/Users/izowooi/git/t1/daily-rsync/data/sources/macmini-m5/jobs/polybot-mi/workspace-epochs/external-v2/strategies/golden-raspberry/runtime/raspberry-mi-shard-2/databases/latest/trades_sim.db`

## 5. 공식 첫 cycle health

| 지표 | DO | RE | MI |
|---|---:|---:|---:|
| Cycle number | 7 | 8 | 8 |
| Market sweep | 7 | 8 | 8 |
| Market observation | 201 | 226 | 229 |
| Orderbook snapshot | 110 | 38 | 132 |
| Research case | 12 | 0 | 12 |
| Signal decision | 165 | 57 | 198 |
| DB bytes | 3,407,872 | 2,813,952 | 3,936,256 |

세 공식 cycle 모두 다음을 만족했다.

- Gamma terminal sweep 22 pages, source envelope 2,181 markets
- eligible market 29, book coverage 100%
- `healthy=true`, `cadence_fresh=true`, `quick_check=ok`, WAL 0
- data quality issue 0, storage guard `OK`
- source digest와 config cohort 단일·일치

DO와 MI에서 각각 12개 case가 생성됐고 RE는 해당 slot에서 qualified signal이 없었다.
이는 오류가 아니다. follow-up은 entry 후 60~75분에 시도하므로 공식 첫 cycle 직후 0건인
것이 정상이다. 이 문서는 collection health만 확인하며 수익성 또는 파라미터를 판단하지 않는다.

## 6. 남은 운영상 주의

- Jenkins가 anonymous config read를 허용하고 HTTP를 사용하므로 inspector 기준
  `ANONYMOUS_CONFIG_READ` HIGH, `PLAINTEXT_HTTP` MEDIUM은 그대로다. 이번 shell에는 inline
  secret이 없다.
- old internal workspace는 더 이상 build 대상이 아니지만 자동 삭제하지 않았다. 복구와
  과거 evidence를 위해 보존한 것이다.
- 공식 window의 수집 판정은 반드시 `external-v2` DB만 사용한다. 이전 내부 DB와 합쳐
  cadence, cohort 또는 수익성을 계산하면 안 된다.
- 몇 시간 뒤에는 수익성을 보지 말고 official-start 이후 cadence, pair/raw coverage,
  due follow-up, control lineage, cohort, DB integrity와 증가량만 우선 확인한다.

다음 요청 예시:

> polybot-do, polybot-re, polybot-mi를 daily-rsync로 다시 동기화하고,
> golden-raspberry external-v2의 `[2026-08-13T12:00:00Z, 현재 sync cutoff)` collection
> health를 점검해줘. 과거 내부 epoch는 섞지 말고 cadence, YES/NO pair, follow-up,
> neutral/opposite control, cohort, DB 무결성, 저장공간 증가량과 Jenkins 실패 재발 여부만
> 확인해줘. 아직 기간이 짧으면 수익성이나 파라미터는 평가하지 마.
