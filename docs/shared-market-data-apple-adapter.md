# 원격 Apple collection-v2 공용 원본 adapter

2026-09-29에 `macmini-m5`의 `polybot-do`, `polybot-re`, `polybot-mi`,
`polybot-shadow-one`을 read-only로 확인했다. 이 문서는 그 시점의 조사와 로컬
rehearsal 결과이며 배포 완료 기록이 아니다. 원격 코드·DB·Jenkins·retirement
상태는 변경하지 않았다. 모노레포의 기존 `golden-apple/`은 다른 legacy bot이므로
해당 디렉터리에 원격 운영 코드를 덮어쓰지 않는다.

구현은 `polybot-observability/src/polybot_observability/market_data_apple.py`,
운영 코드의 적용 후보는 [runtime patch](shared-market-data-apple-runtime.patch)다.
실제 적용 전 대상 코드 SHA와 설치한 공통 package revision을 확인한다.
네 job의 월 DB migration·budget seed·관측 backfill·최종 SHA 순서는
[cutover 절차](shared-market-data-apple-cutover.md)를 따른다.

## 확인한 형식과 공개 경계

네 job의 조사 대상 네 Python 파일은 각각 동일한 SHA였다. collection-v2 DB는
`application_id=0x47415032`, `user_version=1`, `meta.format=apple-filtered-frames-v1`이다.
`runs.frame`은 sorted-key canonical JSON을 zlib level 6으로 압축하며,
`frame_sha256`과 `frame_raw_bytes`는 압축 전 JSON의 SHA와 길이다.

`runs.frame` 전체를 일반 public column allowlist에 넣으면 안 된다. 공개 객체와
private 상태가 중첩된 구조이므로 다음 경로만 명시적으로 추출한다.

| 공유하는 public 객체 | 로컬에 남기는 주변 상태 |
|---|---|
| `markets[*].market` | 선택된 목록·순서·`event_id`·`tokens`, selection/audit |
| `events[*].event` | 해당 관측의 request receipt wrapper |
| `books[*].book`, `duplicate_books[*]` | status, capacity, 계산된 fee, request receipt |
| `followups[*].event.event`, `followups[*].markets[*]` | receipt, watched/unresolved market IDs |

그 밖의 키, `audit`, `fee`, `capacity`, `selection`, `features`, failures와
집계는 그대로 로컬 frame에 남긴다. 공개 market 객체 안의 venue가 제공한
fee metadata와 전략이 계산한 fee/capacity는 서로 다르다.
원본 수집기는 `raw_http_bodies_retained=False`를 기록한다. 이 adapter의 공개
객체는 저장되어 있던 JSON projection이며, 사라진 HTTP response bytes를
복원했다고 주장하지 않는다.

## 최종 storage contract: v2

외부 API는 다음과 같다. `references`는 명시적으로 전달하며 adapter 내부에서
환경 설정을 다시 찾거나 private frame 전체를 공용 writer에 보내지 않는다.

```python
is_shared_frame(blob)
externalize_frame(blob, references, *, raw_sha256=None, raw_size=None)
resolve_frame(blob, references)
frame_reference_hashes(blob)
```

`frame_provenance(blob)`은 codec·길이·hash만, `inspect_public_objects(blob, references)`는
공개 객체만 반환한다. 원본 legacy zlib frame은 계속 읽을 수 있다.
폐기한 object별 CAS prototype의 v1 envelope는 최종 decoder가 명시적으로 거절한다.

1. 기존 frame의 canonical JSON과 zlib6 압축 bytes가 모두 재현되는지 **쓰기 전에**
   확인한다. 재현할 수 없는 자료는 별도 migration 계약 없이 변환하지 않는다.
2. 공개 객체를 정확한 canonical bytes로 frame 내부에서 중복 제거하고 array 하나로
   묶는다. identity, zlib9, XZ preset 6 중 가장 작은 표현을 공용 payload로 저장한다.
   기존 central store는 이 bytes를 SHA256으로 식별하고 zlib/identity를 그대로 적용한다.
3. 로컬 private tree의 공개 leaf만 array index로 치환한다. private tree는 zlib9와
   공개 aggregate 마지막 32KiB를 dictionary로 사용하는 zlib9 중 작은 쪽을 사용한다.
   dictionary에 private 값을 섞지 않는다.
4. public writer의 durable ACK 이후에만 로컬 envelope를 반환한다. 로컬 commit 실패 시
   미사용 immutable public payload가 남을 수 있지만 dangling ACK reference는 만들지 않는다.

최종 marker는 `b'\x1ePMAPPLEFRAME2:'`, contract 이름은
`apple-public-frame-envelope-v2`다. marker 다음 binary header는 network byte order
`!BBHIIII32s32s`이며 순서는 아래와 같다.

| 필드 | 의미 |
|---|---|
| private codec | `9`: zlib9, `137`: public-tail dictionary zlib9 |
| public codec | `0`: identity JSON, `1`: zlib9 JSON, `2`: XZ JSON |
| public count | 중복 제거한 공개 객체 수 |
| 원본 raw/압축 길이 | 기존 frame의 두 길이 |
| public/private raw 길이 | 새 두 구성요소의 검증 길이 |
| 원본 압축 SHA256 | 기존 `runs.frame` bytes의 SHA |
| public payload SHA256 | CAS에 ACK된 aggregate bytes의 SHA |

header 뒤에는 압축된 private tree와 전체 앞부분의 SHA256 trailer가 있다.
고정 overhead는 131 bytes다. 빈 public frame은 payload를 쓰지 않는다.
dependency closure는 public store를 열지 않고 header에서 0개 또는 1개의 SHA로 얻는다.
원본 raw 한도 16MiB, 원본 압축 한도 200,000 bytes, public leaf 한도 4,096개를 유지한다.
새 private envelope는 별도로 1MiB 압축 한도를 검사한다. XZ decode는 64MiB memory
limit와 출력 한도를 적용하고 trailing/concatenated stream을 거절한다.
index 확장 예상 길이를 serialization 전에 검사해 반복 index를 이용한 과다 할당을 막는다.

복원 결과는 **기존 zlib6 BLOB의 길이와 SHA가 일치해야 한다**. 원본 raw SHA/길이
컬럼은 유지되므로 기존 Apple `decode_frame()`도 그대로 검증할 수 있다.
canonical JSON 재직렬화나 논리 equality만으로 기존 BLOB 검증을 약화하지 않는다.

## 용량 비교와 선택 근거

외장 검증 workspace의 로컬 표본만 사용했다. 첫 집합은 네 job에서 두 frame씩,
두 번째는 `polybot-do`의 UTC `[2026-09-29T11:53:00Z, 2026-09-29T12:53:00Z)`
연속 60개 frame이며 source hash가 하나인 구간이다. 월 전체나 다른 cohort로
수치를 외삽하지 않는다.

| 구조 | 8개 frame / 기존 압축 크기 | 60개 frame / 기존 압축 크기 |
|---|---:|---:|
| object별 CAS, private zlib6 prototype | 2.21배 | 미채택 |
| aggregate JSON, store 기본 zlib3, private zlib6 | 1.193배 | 1.189배 |
| aggregate zlib9, private zlib9·public dictionary 선택 | 1.006배 | 1.009배 |
| aggregate zlib9 + binary header/private 선택 | 0.996배 | 1.000배 |
| depth 별도 CAS, aggregate metadata | 1.143배 | 1.122배 |
| bids/asks 별도 CAS, aggregate metadata | 1.208배 | 1.178배 |
| **최종 aggregate XZ6 + binary header/private zlib9 선택** | **0.752배** | **0.747배** |

최종 구현의 실제 측정값은 다음과 같다.

| 항목 | 네 source 8개 | do 60개 |
|---|---:|---:|
| 기존 압축 frame bytes 합 | 281,379 | 2,585,321 |
| 새 local envelope bytes 합 | 75,087 | 678,724 |
| shared store `stored_bytes` | 136,532 | 1,253,688 |
| 두 구성요소 합 | 211,619 | 1,932,412 |
| 새 공용 SQLite 파일 크기까지 포함한 합 | 243,023 | 1,989,444 |
| encode 평균 | 61.7ms | 76.8ms |
| 원본 bytes 복원 평균 | 7.9ms | 9.9ms |

마지막 합은 새 공용 SQLite 파일과 local envelope bytes를 더한 값이다. native 월별 DB의
페이지 재배치나 전체 운영 DB 크기를 실측한 값은 아니다. 그래도 새 SQLite 초기 schema·page
overhead를 포함한 비교에서 각각 13.6%, 23.0% 작았다. 공용 데이터는 frame마다 새 관측을
보존하며 가격이 같다는 이유로 관측을 합치지 않는다.

1시간의 book 2,160건은 token별 전체 JSON unique가 2,110개였다. timestamp만 제외해도
2,110개이고 timestamp와 venue hash를 함께 제외하면 1,841개였다. 일부 hash 변동이
동일 depth의 dedup을 막지만, 별도 depth CAS의 항목·reference overhead가 이 표본의
이득보다 컸다. 따라서 가격 배열 분리를 최종 형식에 넣지 않았다.

## native patch와 provenance

patch의 사전 SHA는 네 runtime에서 같은 값으로 확인했다.

| 파일 | 적용 전 SHA256 |
|---|---|
| `apple/compact_store.py` | `c354e7a4c653fa985bc982773cb64e88d480a9c091050c986ff4abb99c794eb3` |
| `apple/compact_collection.py` | `9bae4c4591b45c29b2a3dbc4127e768fe202a3f869da1504a70ca386166d28aa` |

patch는 `MonthStore.finish()`의 기존 `encode_frame()` 다음에 공용 분리를 넣고,
private DB의 기존 transaction 이전에 payload와 observation의 ACK를 받는다.
`decode_frame()`은 envelope를 기존 압축 bytes로 돌린 뒤 원래 검사를 실행한다.
`fit_frame`, 선택·fee·capacity 계산, source frame raw budget 및 원래 compression budget은
변경하지 않는다. 새 local envelope + public aggregate bytes 합도 기존
`max_frame_compressed_bytes`를 넘으면 fail closed한다. 이 실패를 이유로 추가 market을
탈락시키거나 설정값을 늘리지 않는다.

logical `meta.format`과 SQLite schema version은 그대로 두고 추가 meta key
`frame_storage_format=apple-public-frame-envelope-v2`로 storage 형식을 구분한다.
원래 `runs.frame_sha256`, `frame_raw_bytes`, private summary와 watch 상태를 보존한다.
새 관측의 `source_hash()`에는 adapter·refs·client·store의 실제 source bytes도 포함한다.
기존 데이터의 source hash를 새 코드 값으로 덮어쓰지 않는다.

writer 활성화는 설치한 공통 package와 명시적인 `PUBLIC_MARKET_DATA_SOCKET`,
`PUBLIC_MARKET_DATA_REQUIRED=1`에 따른다. readonly DB만 설정한 상태에서 native writer가
inline으로 돌아가도록 하지 않는다. config 값이나 환경변수를 이 문서 작업에서 바꾸지 않았다.
공통 SQLite reader와 closure/migration hook을 먼저 배치해야 한다. 단순 public column
allowlist로 `runs.frame` 전체를 처리하면 안 된다.

원격 runtime 운영 코드는 source-SHA guard가 있는 patch artifact로 관리하고, 향후 모노레포의
별도 runtime 경로로 수용하더라도 기존 `golden-apple/`과 별도의 코드 경계로 유지한다.
새 Git repository나 branch를 만들 필요는 없다.

## 공용 관측 인덱스와 조회

`publish_frame_observation(envelope, references, *, observer, frame_id, observed_at, job)`은
공개 aggregate가 있는 프레임마다 `kind=apple_public_frame_v2` 관측 **한 행**을 추가한다.
동일 observer/frame ID의 동일 내용 재시도는 idempotent이며 다른 내용은 거절한다.
public payload가 없는 프레임은 관측을 만들지 않는다. token별로 metadata를 반복하지 않는다.

- `payload_sha`는 aggregate의 CAS hash다. metadata는 `job`, `public_codec`,
  `public_raw_bytes`, `public_count`만 허용한다.
- observer는 명시적 `PUBLIC_MARKET_DATA_OBSERVER` 또는 hostname + golden-apple + job +
  collection-v2다. frame ID는 원본 month/slot/run_key다.
- `observed_at`은 `MonthStore.finish()`가 받은 **원래 finished epoch**를 UTC로 표현한다.
  ingestion 시각으로 덮어쓰지 않는다. 여러 HTTP 응답을 묶는 frame 완료 시각이며
  개별 book의 HTTP receipt 시각이라고 주장하지 않는다.
- private audit/capacity/fee/선택 정보, 전체 private envelope와 원본 private frame hash는
  observation으로 보내지 않는다. private transaction 실패 후에도 공개 receipt가 남을 수
  있으므로 성공 cycle·주문·체결의 증거로 사용하지 않는다.

다음 조회는 private DB나 envelope 없이 공용 index와 payload만 읽는다.

```python
from polybot_observability.market_data_apple import iter_public_frames
from polybot_observability.market_data_store import PayloadReader

with PayloadReader(public_store_path) as reader:
    for receipt in iter_public_frames(
        reader, observer=source_observer,
        start="2026-09-29T12:00:00Z", end="2026-09-29T13:00:00Z", limit=1000,
    ):
        public_objects = receipt["objects"]
```

기간은 UTC 반개구간이며 common reader의 SQL kind filter가 LIMIT보다 먼저 적용된다.
개별 관측을 이미 얻었다면 `read_public_frame_observation(observation, references)`를 쓴다.
객체 array에는 event/market/book projection이 함께 들어 있으므로 후속 소비자가 실제
객체 형식·token을 확인해야 한다. 가격 path를 얻는다고 event 객체를 book으로 해석하지 않는다.

## 원래 카운터와 월별 저장 예산

확인한 원본의 실제 gate는 `max_month_db_bytes_per_job / 4096` SQLite page limit이다.
현재 값은 job당 10,000,000,000 bytes이며 `monthly_total_limit_bytes=50,000,000,000`은
policy 검증 상수다. frame을 공용으로 옮기고 private page limit만 남기면 전체 비용이
누락된다. 적용 후보는 다음과 같이 원래 의미와 공용 비용을 분리한다.

- 반환값 `frame_compressed_bytes`는 계속 **원래 zlib6 bytes 길이**다. 기존 raw byte/hash와
  selection budget도 유지한다. `frame_local_envelope_bytes`, `frame_public_payload_bytes`,
  `frame_public_budget_bytes`, `frame_public_index_rows`, `month_shared_reserved_bytes`를
  별도 반환한다. `database_bytes`는 계속 private SQLite 파일의 실제 크기다.
- `prepare_frame()`은 쓰기 없는 계획을 만든다. `reserve_frame_storage()`는 공용 dependency
  길이를 4096 bytes 단위로 올리고 SQLite payload/observation/index 용도로 4 pages를 더한
  금액을 private meta에 먼저 durable commit한다. dedup credit 없이 frame마다 전체 비용을
  charge하고, 그 합을 원래 월 한도에서 차감해 private `max_page_count`를 낮춘다.
- 이 reservation 이후 public payload ACK, observation ACK, private frame commit 순서로
  진행한다. 전송 실패나 결과 불명확 상태의 예약은 지우지 않는다. 같은 slot·원본/public
  hash는 재사용하고 충돌은 거절한다. 나중의 회수는 별도 증거 검증이 필요하다.
- seed와 reserve는 `BEGIN IMMEDIATE`를 명시해 autocommit connection에서도 부분 예약을
  남기지 않는다. 예산 초과 시 metadata 변경을 rollback하고 이전 page cap을 복구한다.
- 새 빈 월만 자동으로 seed한다. **이미 존재하는 월은 운영 쓰기 재개 전에** cutover owner가
  `seed_month_storage_budget(connection, references, original_month_limit)`를 offline 실행해야
  한다. seed는 기존 shared frame을 원본 bytes까지 검증해 비용을 채우며 원래 runs 행을
  바꾸지 않는다. 기존 seed가 있으면 실패한 publication의 예약도 보존한다.

이는 월별 보수적 비용 할당이다. 실제 공용 SQLite의 전체 page 배치·WAL·다른 source 용량을
엄밀하게 귀속하는 물리 계량은 아니므로 공용 volume의 실제 free-space/총량 gate는 별도로
유지한다. dedup이나 이 문서의 압축비를 근거로 config 한도를 증액하지 않는다.

## daily-rsync source identity

월 DB의 canonical strategy는 **golden-apple**, runtime은 `meta.job`(예: polybot-do),
mode는 simulation이다. `golden-apple-research`는 원격 package 이름이므로 별도 전략으로
분할하지 않는다. 확인 시점의 기존 scan은 `golden-*/data/*/*.db` 및 trades/shadow 이름만
허용해 외부 runtime의 `data/collection-v2/YYYY-MM.sqlite`를 발견하지 못했다. 지원 시에는
application ID·format·meta.month·job을 검증하는 별도 monthly route와 snapshot/plan 정책이
필요하며 단순 확장자 allowlist만 늘리면 canonical 이름 검사에서 다시 누락된다.
이 adapter 작업에서는 remote_agent.py를 수정하지 않았다.

## legacy payload pool

legacy `payloads.compressed`는 raw public response와 계산 결과가 섞여 있다.
`legacy_payload_roles(connection, hashes)`는 요청한 hash의 명시적인 참조 관계만 분류한다.

| 역할 | 처리 |
|---|---|
| 검증된 public HTTPS endpoint의 `requests.payload_hash` | public response 후보 |
| event/market의 `payload_hash` | public 문서 후보 |
| legacy `depth_hash`, compact `depth_key` | **bids/asks projection** 후보; full HTTP book으로 승격하지 않음 |
| `metrics_hash`/`metrics_key`, `requests.request_hash` | private, local 유지 |
| unknown endpoint·참조·미분류 FK·orphan | local 유지 |

public 후보와 private/unknown 역할이 같은 hash를 공유하면 local에 남긴다.
확인된 public-only 행만 `externalize_legacy_payload()`에 전달할 수 있으며, 원래 zlib
BLOB을 `PMDATA1:B`로 공유한다. generic BLOB resolver가 원래 압축 bytes를 복구하므로
기존 raw hash/길이/JSON 검사와 `encoding='zlib'` 계약이 유지된다.

이 분류기와 adapter는 cold `.xz` 파일을 열거나 복구·이동·삭제하지 않는다.
retirement/migration marker와 진행 중인 정리는 변경하지 않았다. legacy 변환은 이번
collection-v2 native patch에 자동 연결하지 않았다.

## 검증 근거 위치

원본 코드·실제 frame·DB·private envelope는 Git에 넣지 않았다. 검증 사본은 외장
`/Volumes/daily-rsync-data/data/reports/apple-public-adapter-20260929T123338Z/`에 있다.

- `source-manifest.json`: 네 source의 네 파일 SHA, 총 170,220 bytes.
- `frame-samples.json`, `hour-frame-samples.json`: 8개 및 60개 표본의 source identity.
- `compression-candidate-report.json`, `aggregate-xz-candidate-report.json`: 후보 비교.
- `v2-frame-roundtrip-report.json`: 68개 표본의 원본 압축 SHA와 native decoder 결과 SHA 검증.
- `native-month-store-rehearsal.json`: 네 source 실제 frame을 사용한 isolated MonthStore 확인.
  최소 fixture policy를 사용했으며 실제 운영 policy validator 실행을 대체하지 않는다.
- `local-socket-roundtrip-report.json`: 네 source의 8개 frame을 로컬 Unix service와
  `StoreClient`로 저장·조회해 원본 압축 bytes를 복원한 결과. DB는 같은 외장 root에 두었다.
- `native-index-budget-rehearsal.json`: 원본 8개 frame·실제 원래 finished 시각을 사용해
  observation 8행, private DB 없는 조회, 원본 compressed counter, reserve 차감 page cap,
  native decode 결과를 확인했다. public SQLite(새 observation index 포함) 172,032 bytes +
  local envelope 75,087 bytes = 원래 281,379 bytes의 87.82%다. 원격 mutation은 없었다.
- `native-unknown-ack-rehearsal.json`: observation commit 후 ACK 유실을 주입해 private frame은
  아직 미게시, 40,960-byte 예약은 보존됨을 확인했다. 같은 입력 재시도 후 관측 한 행과
  동일 예약을 유지한 채 private commit이 완료됐다.
- `frame-receipt-times.json`: 같은 8개 원본 slot의 read-only started/finished metadata.
- `runtime-patch-manifest.json`: 적용 후보의 before/after SHA. 원본 코드에 대한 patch dry-run 통과.

synthetic 119개 Apple 테스트는 공개/private 경계, 모든 codec, 원본 bytes, re-ACK,
누락·변조·과대 XZ, index 확장 한도, legacy mixed-role 보존, 공용 관측의 idempotency·기간조회,
다른 kind의 LIMIT 간섭, 예산 seed·durable reservation·autocommit rollback을 검증한다. 손익·체결 판단이나 전략 parameter는
이 검증의 대상이 아니다.
