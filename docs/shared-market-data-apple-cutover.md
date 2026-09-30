# Apple collection-v2 월 DB 전환 순서

이 문서는 실행 절차이며 원격 전환 완료 기록이 아니다. 대상은
`/Volumes/t7/jenkins/golden-apple/polybot-{do,re,mi,shadow-one}`의 native Apple runtime이다.
모노레포 `golden-apple/` legacy bot, 유일한 cold `.xz`, 진행 중인 retirement는 대상이 아니다.
원격 변경은 통합 cutover 담당자가 수행한다.

## 순서와 준비 조건

1. 네 job의 초기 enabled 상태·실행 중 build·effective root/config·원본 source SHA를 기록하고
   관련 writer를 정지한 뒤 진행 중인 실행이 끝났음을 확인한다. 각 runtime의
   `data/collection-v2/.collector.lock`을 원래 collector와 같은 exclusive flock으로 유지해
   새 collector가 migration 중 시작하지 못하게 한다. 실제 public service는 단일
   writer로 유지한다. 외장 volume identity와 staging 여유 공간을 확인하며 내부 fallback은 없다.
2. job별 `data/collection-v2/YYYY-MM.sqlite`를 목록화한다. application ID `0x47415032`,
   user_version `1`, `meta.format=apple-filtered-frames-v1`, `meta.job=해당 job`,
   `meta.month=원본 파일 stem`을 확인한다. canonical strategy는 `golden-apple`, runtime은
   `meta.job`, mode는 simulation이다. `golden-apple-research`는 package 이름이다.
3. 해당 source의 열린 writer가 없고 `-wal`/`-journal`이 없음을 확인한 다음 파일 SHA를 고정한다.
   journal 파일을 임의 삭제해 조건을 맞추지 않는다. 원본은 그대로 보존하고 **같은 외장 root의
   새 경로**로 migration한다. 현재 월뿐 아니라 다시 읽을 기존 월마다 별도 증거를 만든다.
4. 정지한 원본을 daily-rsync의 공식 sync→verify→pin으로 먼저 보존한다. 그 원본 pin과
   source-file→snapshot lineage가 있어야 최종화한 artifact를 같은 경제적 원장의 storage
   migration으로 연결할 수 있다. 이어 아래 generic migration을 실행한다. 성공 manifest는 source/derived schema·PK/rowid·private
   값과 decoded 원본 bytes를 검증한 **seed 전 중간 artifact** 증거다.
5. staged 월 DB에 budget seed를 먼저 수행하고 historical public observation을 backfill한다.
   public observation은 private DB commit과 원자적인 묶음이 아니다. 실패하면 job을 계속 정지하고
   같은 source identity·원래 finished 시각으로 재시도한다. 이미 성공한 예약·관측은 재사용한다.
6. finalization 증거를 완성한 뒤에만 원본 파일을 검토/rollback 경로에 보존한다. finalized stage와
   두 manifest는 immutable checkpoint로 남기고, **별도 inode인 검증된 복사본**을 canonical 경로에
   원자적으로 설치한다. stage를 live 파일과 hardlink하지 않는다. 최종 canonical SHA·closure·native
   decoder를 다시 확인한다. 원본 삭제는 이 절차에 포함하지 않는다.
7. source SHA guard를 확인해 [runtime patch](shared-market-data-apple-runtime.patch)를 적용하고,
   같은 공통 package revision을 native runtime·public daemon·daily-rsync reader에 설치한다.
   patch compile/import와 source hash inclusion을 확인한다. `PUBLIC_MARKET_DATA_SOCKET`,
   `PUBLIC_MARKET_DATA_REQUIRED=1`, 안정적인 source/job별 `PUBLIC_MARKET_DATA_OBSERVER`를 설정한다.
   historical backfill에도 정확히 같은 observer 문자열을 쓴다.
8. job을 계속 정지한 상태에서 canonical의 최종 sidecar
   `<canonical>.storage-migration.json`를 사용해 공식 sync→verify→pin을 수행한다. 이 이름은 generic
   intermediate의 `<stage>.migration.json`과 다르다.
   source SHA·final SHA·preserved checkpoint·pin receipt의 연결을 확인한 뒤 고정 sidecar를
   검토/receipt 보존 경로로 이동한다. canonical 옆에 SHA가 곧 오래될 sidecar를 남기지 않는다.
   sidecar를 무조건 무시하는 방식으로 이후 sync를 통과시키지 않는다. finalizer 자체는 설치,
   sync/pin, sidecar 이동을 수행하지 않으며 통합 담당자가 증거를 확인한 후 수행한다.
9. 원래 활성 상태였던 job만 재개한다. 각 job의 자연 build가 원래 source/config cohort로
   새 frame 한 개 이상을 기록하고, 원본 counter·공용 payload·관측 한 행·월 예약·watch 상태와
   공용 DB만의 조회를 확인한다. 의도적으로 비활성인 job은 켜지 않는다.

재개된 현재 월의 정상 append는 finalized SHA와 달라진다. 이후 sync는 catalog에 보존된
storage lineage를 가진 mutable snapshot으로 진행하며, 최초 finalization을 반복 적용하지 않는다.
새 reader나 새 catalog는 preserved 원본과 finalized checkpoint의 검증된 lineage를 먼저 가져와야
한다. 고정 checkpoint 없이 이미 계속 쓰이는 canonical 파일만으로 원래 cutover 증거를 추정하지 않는다.

예를 들어 observer는 `macmini-m5:golden-apple:polybot-do:collection-v2`처럼 source와 job을
함께 명시한다. hostname fallback에 기대다가 historical/live observer가 달라지지 않게 한다.
기존 설정의 `storage.max_month_db_bytes_per_job`를 읽어 seed에 전달하며 임의 증액하지 않는다.
확인 당시 값은 job당 10,000,000,000 bytes였지만 실행 시 effective config가 기준이다.

## 실제 지원 migration 명령

변수는 cutover inventory에서 확인한 절대 경로·hash를 사용한다. `APPLE_PYTHON`은 해당 공통
package revision을 import하는 interpreter이며 `APPLE_STAGE`는 아직 존재하지 않는 파일이다.
소스/공용 DB/소켓 경로는 root의 실제 배포값을 전달한다. 이 CLI는 SSH를 실행하지 않으므로
세 경로에 직접 접근할 수 있는 대상 호스트에서 실행한다. 로컬 rehearsal DB만 원격으로
복사하면 public payload closure가 따라가지 않는다. 이 CLI는 runtime 이름 인자를 받지
않으므로 위의 Apple identity 검증을 생략하지 않는다.

```sh
"$APPLE_PYTHON" -m polybot_observability.market_data_migrate \
  --source "$APPLE_SOURCE" \
  --source-sha256 "$APPLE_SOURCE_SHA256" \
  --strategy golden-apple \
  --output "$APPLE_STAGE" \
  --storage-root "$APPLE_STORAGE_ROOT" \
  --socket "$APPLE_PUBLIC_SOCKET" \
  --public-db "$APPLE_PUBLIC_DB" \
  --body-only
```

`--body-only`에서도 explicit Apple `runs.frame` adapter를 사용한다. Apple monthly DB에는
별도 scalar level table 변환이 필요하지 않다. CLI는 원본에 덮어쓰지 않고 새 파일과
`<stage>.migration.json`을 만든다. `status=VERIFIED`, source SHA 일치, payload closure 검증을
확인한 뒤 다음 단계로 진행한다. source path의 전 구성요소도 cutover 담당자가 symlink와
volume identity를 검증한다. CLI의 source 경로 resolve만을 운영 source identity 검증으로
대체하지 않는다.

## 지원 finalization 명령

generic migration의 성공 뒤 아래 CLI로 seed·historical observation·최종 검증을 수행한다.
`APPLE_FINAL_MANIFEST`는 intermediate sidecar와 다른 새 경로다. `APPLE_EFFECTIVE_CONFIG_HASH`는
실행 담당자가 확인한 effective config의 canonical SHA이며 원본 `configs`에 같은 문서가
있어야 한다. 원본 문서의 `storage.max_month_db_bytes_per_job`와 명시한 한도가 다르면 중단한다.
설정을 변경한 뒤 이전 월을 최종화하려면 그 월의 실제 config provenance를 선택한다.

```sh
"$APPLE_PYTHON" -m polybot_observability.market_data_apple_finalize \
  --source "$APPLE_SOURCE" \
  --stage "$APPLE_STAGE" \
  --intermediate-manifest "$APPLE_STAGE.migration.json" \
  --final-manifest "$APPLE_FINAL_MANIFEST" \
  --collector-lock "$APPLE_COLLECTION_ROOT/.collector.lock" \
  --storage-root "$APPLE_STORAGE_ROOT" \
  --socket "$APPLE_PUBLIC_SOCKET" \
  --public-db "$APPLE_PUBLIC_DB" \
  --observer "$APPLE_OBSERVER" \
  --job "$APPLE_JOB" \
  --month "$APPLE_MONTH" \
  --effective-config-hash "$APPLE_EFFECTIVE_CONFIG_HASH" \
  --maximum-bytes "$APPLE_ORIGINAL_MONTH_LIMIT"
```

CLI는 collector와 같은 exclusive flock 및 stage 전용 flock을 직접 획득한다. 호출자가
collector lock을 별도 프로세스에서 이미 잡고 있다면 먼저 종료된 writer 상태를 유지한 채
lock 소유권을 finalizer에 넘긴다. 원본 옆 native `.collector.lock` 이외의 경로는 거절한다.
stage에는 SQLite exclusive lock도 적용한다. 모든 파일은 명시한 같은 storage root에 있어야
하며 symlink·WAL·journal·SHM가 있으면 중단한다. 기본 여유 공간 gate는 50 GiB/사용률 90%이고
seed 전과 observation마다 재확인한다. job 정지·재개나 canonical 파일 교체는 CLI가 수행하지 않는다.

실패하면 별도 최종 sidecar는 `FAILED`이고 이미 성공한 seed·observation은 보존한다. 같은
인자로 재실행하면 source/intermediate/config/observer identity와 실제 stage를 다시 확인한 뒤
이어간다. writer가 ACK 없이 timeout한 경우나 ACK를 반환했지만 독립 reader에서 observation이
없으면 `VERIFIED`가 되지 않는다. 원래 `finished`를 사용하는 PK 기반 readback은 같은 시각의
다수 frame도 검증하며, PARTIAL/FAILED 행도 상태를 바꾸지 않는다.

최종 sidecar는 `shared-public-bodies-migration-v1` 계약과 새로운 destination SHA를 유지하며,
`apple_monthly_finalization.contract=apple-monthly-finalization-v1`에 원본→중간→최종 SHA,
중간 sidecar 파일 SHA, 안전한 중간 manifest projection과 canonical SHA를 담는다. budget 추가
행은 실제 frame에서 계산한 count/digest로 증명하며 전체 reservation 목록을 중복하지 않는다.
최종 JSON은 8 MiB를 넘지 않는다. 검증 완료 후 canonical 경로와 함께 전달할
`<canonical>.storage-migration.json`는 이 **최종 manifest**의 복사본이며 intermediate sidecar도
별도 보존한다.

읽기 전용 `verify_monthly_finalization(original, target, references, manifest)`는 schema,
원래 모든 PK/rowid/private cell, exact compressed frame bytes, config 한도, 계산된 budget
metadata만의 추가, payload closure와 정확한 observation을 재검증한다. daily-rsync가 SQLite
backup으로 보존한 원본을 사용할 때만, 별도 source-file→snapshot lineage 검증 후
`source_snapshot_sha256`를 명시할 수 있다. 이 예외는 원본 logical 비교를 완화하지 않는다.
`page_cap`은 `(maximum_bytes-reserved_bytes)//4096`의 계산값이며 SQLite connection을 다시 열 때
native writer가 cap을 다시 적용한다. reader가 PRAGMA cap을 변경해 증거를 만드는 방식은 쓰지 않는다.

같은 호스트에서 canonical·원본·checkpoint·실제 pin·daily-rsync catalog를 모두 읽을 수 있다면
`retire_monthly_sidecar(...)` helper로 고정 sidecar를 검토 폴더로 이동할 수 있다. source key와
정확한 source/job/runtime/path identity를 명시하며, 실제 catalog의 pin row와 storage-generation
lineage, pin manifest, payload closure, 공용 observation, canonical/checkpoint/pin SHA를 검증한다.
checkpoint와 canonical·pin은 서로 다른 inode여야 한다. review 경로는 새 파일이며 source DB를
수정하거나 지우지 않는다. receipt는 이동 전 `PREPARED`, 이동 뒤 `RETIRED`로 기록한다. crash로
receipt 상태가 늦게 남으면 실제 sidecar 위치와 SHA가 복구 기준이며, 자동으로 재개했다고 간주하지 않는다.

이 helper는 다른 호스트가 주장하는 JSON을 인증하는 방식이 아니다. MacBook pin과 Macmini
canonical을 함께 직접 읽을 수 없는 실제 두 호스트 전환에서는 담당자가 MacBook의 실제 catalog·pin
검증을 끝내고, 정지된 Macmini에서 같은 final SHA·checkpoint·sidecar SHA를 재검증한 뒤
한정된 sidecar 이동을 별도로 수행해야 한다. 로컬 rehearsal 성공을 원격 pin/전환 완료로 기록하지 않는다.

## Budget seed와 historical observation 내부 API

다음 코드는 finalizer 내부의 핵심 API 순서를 설명한다. 운영 실행은 위 CLI를 사용한다.
`stage`, `public_db`, `socket_path`,
`job`, `month`, `observer`, `original_month_limit`는 위에서 검증한 명시적 값이다.
connection은 **raw sqlite3**이며 writer는 기존 공용 daemon의 `StoreClient`다. 별도 PayloadStore
writer를 열지 않는다. 스크립트는 credentials나 config document를 출력할 필요가 없다.

```python
from contextlib import closing
from datetime import datetime, timezone
import sqlite3

from polybot_observability.market_data_apple import (
    APPLICATION_ID, ORIGINAL_FORMAT, is_shared_frame, publish_frame_observation,
    seed_month_storage_budget,
)
from polybot_observability.market_data_client import StoreClient
from polybot_observability.market_data_refs import PayloadReferences
from polybot_observability.market_data_store import PayloadReader

with StoreClient(socket_path) as writer, PayloadReader(public_db) as reader:
    refs = PayloadReferences(reader=reader, writer=writer, cache_bytes=0)
    with closing(sqlite3.connect(stage, isolation_level=None)) as db:
        db.execute("PRAGMA synchronous=FULL")
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        meta = dict(db.execute("SELECT key,value FROM meta"))
        assert (meta["job"], meta["month"], meta["format"]) == (job, month, ORIGINAL_FORMAT)
        seed_result = seed_month_storage_budget(db, refs, original_month_limit)
        public_frames = 0
        for slot, run_key, finished, blob in db.execute(
            "SELECT slot,run_key,finished,frame FROM runs WHERE frame IS NOT NULL ORDER BY slot"
        ):
            if not is_shared_frame(blob) or finished is None:
                raise ValueError("incomplete Apple monthly conversion or missing original frame clock")
            public_frames += int(publish_frame_observation(
                blob, refs, observer=observer,
                frame_id=month + "/" + str(slot) + "/" + run_key,
                observed_at=datetime.fromtimestamp(finished, timezone.utc).isoformat(), job=job,
            ))
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
```

기존 seed가 없으면 전체 shared frame을 exact 원본 bytes까지 복원 검증하고 meta 예약을 한
transaction으로 만든다. 이미 seed가 있으면 기존 실패/결과 불명확 예약까지 보존하며 cap을
재검증한다. caller transaction이 열려 있으면 거절해 caller의 pending 작업을 commit하지 않는다.
inline frame을 먼저 seed하고 나중에 migration하는 순서는 사용하지 않는다. 이미 만들어진
0 seed가 후속 migration의 공용 비용을 자동 재계산하지 않기 때문이다.

Native patch는 **새로 만드는 빈 월**만 자동 seed한다. 기존 현재 월이나 이전 월을 seed 없이
열어 writer를 재개하면 `SHARED_MONTH_BUDGET_SEED_REQUIRED`로 실패한다. 모든 월을 이관할 수
없는 작업 범위라면 현재 live 월의 seed·reader 지원을 우선 완료하고 남은 월을 명시적으로
미완료로 기록한다. 이를 전체 공용화 완료로 세지 않는다.

backfill은 원래 `runs.finished`를 사용한다. status가 PARTIAL/FAILED여도 실제 저장된 public
receipt는 보존하며 성공 cycle로 바꾸지 않는다. frame 없는 RUNNING/INTERRUPTED 행은 그대로
두고 observation을 만들지 않는다. 원본 status·summary·source hash·config·watch를 수정하지 않는다.

## 최종 manifest가 증명해야 하는 것

**Budget seed는 private meta와 파일 SHA를 바꾼다.** seed 전 `<stage>.migration.json`의
`destination_sha256`와 `tables.meta.logical_sha256`를 seed 후 파일의 값이라고 재사용하면 안 된다.
generic CLI는 seed 전 artifact를 만들며, 별도 finalizer CLI가 seed/backfill/finalization을 수행한다.

cutover receipt는 다음 증거를 추가해 중간 migration manifest와 최종 artifact를 연결한다.

- 원본 경로·원본 SHA·month/job/app ID 및 보존 경로; 원본이 migration 후에도 같은 SHA인지.
- seed 전 VERIFIED manifest 파일의 SHA와 그 `destination_sha256`.
- seed 전후 meta 대사. 원래 모든 meta 값은 같아야 하며 허용되는 추가 key는
  `shared_frame_budget_contract`, `shared_frame_reserved_bytes`, `shared_frame_reservation:<slot>`뿐이다.
  native 첫 새 frame부터 쓰는 `frame_storage_format`은 이 seed/backfill 절차가 추가하지 않는다.
- schema/application ID/user_version, runs/configs/watch와 다른 기존 table의 decoded logical hash가
  VERIFIED 중간 artifact와 같은지. private meta 추가를 private row 전체 변경 허가로 해석하지 않는다.
- seed 후 최종 private file SHA·크기·integrity, reachable public payload SHA 목록/closure hash,
  모든 shared frame의 exact original compressed SHA 검증, 관측 observer/frame ID/원래 시각과 수.
- 원래 월 한도, reserved sum과 최종 private page cap; 공용 실제 volume free-space 확인.
- 해당 receipt와 최종 checksum을 반영한 daily-rsync 공식 storage lineage·verify·pin 결과.

이 파일의 경로/hash 증거 없이 intermediate manifest의 상태 문자열만 `VERIFIED`로 남겨
cutover하지 않는다. migration·budget reservation·observation·canonical 파일 교체는 서로 다른
commit 경계다. source 보존과 단계별 hash가 복구 근거이며 공용 CAS/관측을 rollback 삭제하지 않는다.
