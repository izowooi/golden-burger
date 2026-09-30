# 공용 시장 데이터 저장소 전환

## 현재 완료 범위 — 2026-09-30 사용자 축소 지시

과거 전체 전략·모든 역사 형식을 한 번에 이전하지 않는다. 현재 우선 대상은 다음 10개
Jenkins 잡의 신규 공용 시장 데이터 쓰기·읽기와 정상 실행이다.

- 스포츠: Cat, Dog, Eco, Fruit, King, Queen 및 각 잡의 모든 기존 child runtime.
- 추가 live: Yellow, Blue(기존 Cherry에서 개명), Orange, Fox.

공용 endpoint는 Mac mini의 `/Volumes/t7/jenkins/shared-market-data/public.db`와
`market-data.sock`이다. 주문 intent·confirmed fill·fee·포지션·계좌 원장은 기존 runtime에
남기고, 공용 API 원본과 새 public snapshot/catalog 저장은 공용 writer를 이용한다.
기존 inline history는 reader 호환 경로로 유지한다. 과거 전체 DB 변환과 종료 후 API 백필은
별도 후속 범위이며 이번 완료 조건이 아니다. 비활성 job을 활성화하지 않는다.

이미 중단하거나 변경한 주변 수집기는 정상 재개 또는 데이터 무손실 원복까지만 정리한다.
Apple 4개는 native runtime과 timer로 원복했고, Re에 원복 전 추가된 4행도 보존했다.
준비된 과거 adapter나 draft 문서를 운영 이전 완료로 세지 않는다.

## 실행 방향 재정렬 — 사용자의 단순화 요청

2026-09-30 사용자는 작업이 과도하게 커졌다고 지적했다. 목표는 모든 전략이 공개 시장
데이터를 하나의 DB에서 쓰고 읽으며 전략별 원장만 구분하는 것이다. 과거 모든 저장 형식의
범용 호환 엔진을 완성하는 일을 운영 전환의 선행 조건으로 삼지 않는다.

- 새 저장 형식·추가 압축 체계의 확장을 우선하지 않는다. 이미 검증한 공용 DB·writer를 재사용한다.
- 현재 실행 중인 job의 공용 쓰기/읽기, 전략별 판단·주문·체결 분리와 재개 확인을 먼저 완료한다.
- 기존 자료는 시뮬레이션에 필요한 값·시간·호가·정산과 원장 참조를 대사해 이전하고,
  불필요한 원본은 검토 폴더로 정리한다. 과거·비활성 자료도 최종 범위에 남긴다.
- 동기화·대시보드·재생 경로는 실제 전환 대상의 검증에 필요한 범위로 연결한다.
- 진척은 테스트 수보다 실제 전환한 job, 남은 job, 실제 회수한 공간, 운영 장애로 보고한다.
  로컬 코드 완성을 운영 배포 완료로 표현하지 않는다.

현재 Git/외장 쓰기/네트워크 제한은 운영 전환의 별도 장애다. 이를 다른 로컬 기능 확장으로
대체하지 않는다. 기존 변경을 일괄 삭제하거나 사용자 변경을 되돌리는 작업은 하지 않았다.

## 운영 연결 재확인 — 2026-09-30T11:02:55.895556+00:00

단순화 방향으로 실제 운영 전환을 시작하려고 Jenkins 조회 스킬과 TCP 연결을 재확인했다.
`192.168.50.23:8080`과 `:22` 모두 현재 실행 프로세스에서 errno 1
`Operation not permitted`로 거부됐다. 인증 전 연결 단계의 거부이므로 현재 Jenkins
잡 상태·서버 정상 여부·원격 여유 공간을 확인했다고 주장하지 않는다.

사용자의 배포 승인은 이미 있다. 현재 세션의 네트워크 및 Git/외장 쓰기 제한이 별도
실행 장애이며, 접근 가능한 환경에서 현재 배치를 다시 확인한 뒤 공용 DB 연결을 적용해야 한다.
이 재확인에서는 잡 정지·설정 변경·배포·원본 이동/삭제를 수행하지 않았다.

## 접근 경로 구분 재확인 — 2026-09-30T11:08:50.930831+00:00

사용자가 Jenkins 웹과 SSH의 수동 접속 성공을 확인했다. 명령 실행 도구에서 Jenkins
조회와 `ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=yes
jongwoopark@192.168.50.23 hostname`을 재시도했으나 SSH는 여전히
`Operation not permitted`로 거부됐다. 한편 Computer Use로 이미 열린 Edge의 Jenkins
대시보드와 최근 build 목록을 읽을 수 있었다. 브라우저 경로와 명령 실행 도구의 제한은
구별해야 하며, Jenkins 서버 전체가 접근 불가능하다고 표현하지 않는다. 이 확인은
SSH 전송·Git 쓰기·배포 가능성을 증명하지 않으며 job 상태를 변경하지 않았다.

## 권한 복구 및 첫 운영 복구 — 2026-09-30

전체 권한 세션에서 명시적 SSH 사용자 `jongwoopark@192.168.50.23`로 연결을 확인했다.
앞선 명령 실행 도구의 네트워크 거부는 현재 해소됐다. Jenkins 33개 상태를 기록했고,
원래 disabled 5개와 timer 없는 Lion/Wolf는 그대로 보존한다.

White 실패는 공용 서비스의 `stats()`가 전체 body 합계를 읽느라 1.467초가 걸리는 데
감독기 timeout이 1초였기 때문이다. 빈 공개 읽기 왕복은 0.022초였다. commit `9eddcd8`은
통계 전체 계산 대신 빈 읽기로 응답성을 확인한다. 원격 DB의 online backup
SHA `fab8ec10b65b11c4446590fbfebc6b24426e7f24f5a69b1605eabf4d1b10e7f8`를 보존하고,
기존 writer를 정상 종료한 뒤 additive schema 초기화와 새 writer 시작을 완료했다.
이전 client 호환성을 별도로 검증했고 White 첫 자연 build #53743이 SUCCESS였다.
전체 fleet 전환 완료를 뜻하지 않는다.

White 20260927 body-only generation의 누락 pin도 완료했다. 전환본 SHA는
`1c89d30ec5aab9241b5e4c26b98ee822b94b5a997fd6c401fee7601576d979cc`이고
32,447개 payload closure, 날짜범위 verify, cold reader 복원과 FK 검사가 통과했다.
실제 마지막 receipt는 `2026-09-27T23:59:34.297158Z`다. 새 RAW profile 전환과 구분한다.

## 완료 조건

사용자 목표는 모든 전략과 시뮬레이션 수집기가 공용 시장 원본을 **한 저장소에서 쓰고 읽는 것**이다.
전략별 판단·실험·주문·체결·수수료·포지션·수동 계좌 소유권은 전략별로 남긴다.
아래는 작업 중인 계약이며 현재 배포 완료를 뜻하지 않는다.

| 요구 | 완료 증거 | 현재 상태 |
|---|---|---|
| Astra에 맞는 지침 | 활성 모델/config, 잘못된 권한·모델 가정 정정, 변경 검증 | 지침 수정 및 commit 완료 |
| 전 전략 공용 쓰기·읽기 | 실제 활성 job 전체의 writer/reader 배치와 개인 원장 분리 | White forward 전환 완료, 나머지 활성 job 전환 미완료 |
| 동시성·내구성 | 단일 writer, 부하/재시도/중단/재개 테스트, commit 이후 ACK | 원격 서비스 배포 및 Jenkins 자연 실행을 통한 서비스 복구 확인, 전체 동시 부하 미검증 |
| 충분한 simulation 원본 | exact bytes/hash·관측 시각·token·depth·fee source·clock·정산, 기존 replay 대사 | White 과거 shard 전체 출력 대사 통과, 다른 소비자 검증 중 |
| 중복 원본 정리 | 기존→공용 참조 대사, 원본/신규 hash, 복원 검사, 검토 폴더/삭제 manifest | White 2026-09-27 원본을 양쪽 검토 폴더로 이동, 다른 자료 미완료 |
| 작업 후 재개 | 시작 시 enabled였던 관련 job 복구, 자연 build·원본 적재·reader 검증 | White 재개 및 자연 빌드 성공 확인, 전체 전환 후 검증은 미완료 |

의도적으로 비활성인 옛 전략을 새로 켜지 않는다. 매매 금액·진입·TP·SL 및 수동 포지션은 이 저장소 전환의 변경 대상이 아니다.

## 저장 경계

- 공개 Gamma event/market 응답, CLOB full book, 공개 스포츠 clock, exact market settlement 원본을 공용 저장한다.
- `GET`이라고 모두 공개자료는 아니다. 특정 wallet의 positions/activity/trades, 인증된 order/fill/balance 응답은 전략/계좌 자료다.
- 공개 book·market을 조회하는 SDK 객체에 인증 정보가 있다는 이유만으로 해당 공개 응답을
  계좌 자료로 분류하지 않는다. 알려진 공개 route와 요청 조건을 확인하며 header·cookie·SDK의
  인증 상태는 공용 payload에 직렬화하지 않는다. 반대로 `auth`, `passphrase`, `api_passphrase`
  같은 인증용 query가 붙은 요청은 공개 수집에서 제외한다. 공개 trade tape와 계좌별 fill도 구별한다.
- `strategy_configs`, run identity, entry decisions, intent, confirmed ledger, capacity, leases와 실험별 derived features는 로컬 소유를 유지한다.
- 동일 body는 SHA-256으로 공유하지만 별도 시각·request attempt·source/runtime의 관측을 독립적으로 보존한다. 관측 중복 제거로 가상의 완전 경로나 더 많은 경기 수를 만들지 않는다.
- 공용 body columns는 strategy × table × column allowlist로 분류한다. `_json`/`_gzip` 이름만으로 자동 판단하지 않는다.
- 공개 원시 scalar ladder 테이블도 전환 대상이다. body만 외부화한 상태를 전체 전환 완료로 보고하지 않는다. derived notional ladder나 public/private 혼합 row는 원본과 판단을 분리한다.
- Apple `collection-v2`의 압축 `runs.frame`은 공개 book/event와 계산·선별 판단이 섞여 있으므로 통째로 공용자료로 옮기지 않는다. nested projection adapter가 필요하다.

## 공용 DB와 전략 참조

`polybot-observability`가 공통 구현 경계다. 공용 SQLite는 공개 body의 exact bytes를 SHA-256 key로 저장하고 효과가 있을 때만 lossless compression을 적용한다. owner-only UNIX socket 서비스의 bounded queue를 통해 한 writer가 transaction을 commit한다. 이미 durable한 body는 동일 key로 재사용한다. ACK 이후에만 전략 DB가 작은 TEXT/BLOB 참조를 기록한다.

로컬 transaction이 취소되면 아직 사용되지 않은 공개 payload가 남을 수 있지만, 성공한 로컬 참조에 대응하는 원본이 없는 상태는 허용하지 않는다. 공용 자료 삭제는 live/과거 pin의 reachable reference를 확인한 후에만 가능하다. 중앙 장애나 원본 누락 시 내부 디스크 또는 inline 중복 DB로 조용히 돌아가지 않는다.

reader는 참조를 확인해 원본 bytes/hash를 검증하고 기존 TEXT/BLOB 타입으로 반환한다. SQLite row factory, 별칭, raw SQL reader와 ORM 모두 같은 복원 경로를 사용한다. 거래 ledger JSON은 참조로 바꾸지 않는다. DB 파일을 물리적으로 재구성한 뒤 이전 checksum이 그대로 유효하다고 주장하지 않는다.

원시 숫자 level은 동일 public array를 공용 저장하고, source-specific level ID/parent snapshot과
실험 flag만 로컬 link에 둔다. 연결별 view가 기존 JOIN·SUM·정렬을 복원한다. 기존 실제 table은
미전환 legacy row와 원래 CHECK/UNIQUE/FK 검증의 기준으로 보존하며, 새 데이터는 공용 본문과
로컬 링크로 기록한다. 앞으로 이전기를 확장해 남은 legacy row도 검증 후 제거해야 한다.

## 2026-09-29 로컬 실자료 시험

이미 검증된 White v1 `20260927` UTC shard의 원본 hash
`149f7bc89219c0765ac0e0bef22de92b0f63e13877e849e7256849e8f4f6991a`를 고정했다.
원본을 수정하지 않고 별도 공용 DB와 참조 DB를 만들었다.

| 측정 | 결과 |
|---|---:|
| 기존 DB | 2,579,701,760 bytes |
| 전략/수집 provenance 참조 DB | 71,512,064 bytes |
| 공개 payload + 관측 검색 index DB | 1,644,306,432 bytes |
| 새 두 파일 합계 | 1,715,818,496 bytes |
| 이 자료의 구조상 절감 | 33.49% |
| 공용 exact payload | 32,447개 |
| 출처별 public receipt 관측 | 38,405개 |
| 전체 가격 경로 출력 | 19,402행, 이전/이후 동일 SHA |
| exact 정산 출력 | 158행, 이전/이후 동일 SHA |

전체 테이블의 decoded logical hash, 원래 schema, PK/rowid, SQLite integrity, source 불변성과
32,447개 reachable payload hash를 확인했다. 기존 exporter에서 유효하던 19,388개 가격 행과
품질 미충족 행도 이전 전후 동일하다. 관측 공백을 채우거나 유효 판정을 높이지 않았다.
공용 DB만 이용한 token별 조회에서 예시 token의 994개 시점과 원본 book identity도 확인했다.

이 수치는 한 shard의 이전 시험 결과다. 전체 저장소 절감률로 외삽하지 않는다. 아직 원본과
시험 사본을 함께 보존하므로 **현재 디스크에서 이만큼 공간을 회수했다는 뜻은 아니다**.

## 이전·동기화·검증

1. job별 초기 enabled 상태와 실제 strategy/runtime·source digest·DB 경로를 기록한다. 외부에서 진행 중인 다른 데이터 retirement 작업을 덮지 않는다.
2. 완료된 verified shard에서 별도 derivative DB를 만든다. source 파일은 수정하지 않는다. 원본 schema·원장 row·PK/rowid·trigger·application ID를 보존하고 decoded 논리 checksum을 대사한다.
3. 공용 payload closure를 별도 검증한다. 개인 DB만 checksum 확인하고 pin 완료로 처리하지 않는다. `daily-rsync`는 새 payload만 중앙 로컬 사본으로 증분 반입하고 pin의 closure manifest를 보존해야 한다.
4. 실제 recorder·live reader·시각화·simulation이 공용 원본에서 같은 결과를 읽는지 확인한다. raw 공백·누락을 새 구조에서 숨기지 않는다.
5. 원본과 신규 DB의 경로/hash/크기/검증을 기록한 후 원본을 검토 폴더로 이동하거나 이미 허가된 중복 삭제를 수행한다. 같은 filesystem 내 이동은 공간 확보가 아니다.
6. 처음 활성 상태였던 관련 job을 재개하고 자연 build, shared write acknowledgement, 모든 child 실행, 기존 청산·대사·수집 경로를 확인한다.

가장 큰 archive를 한꺼번에 복사하지 않는다. 완료 shard 단위로 이전해 필요한 임시 공간을 제한한다. Apple의 이미 유일한 사본이 된 압축 cold archive와 진행 중 retirement source는 중복 cache가 아니다. 현재 source/runtime 경로를 먼저 증명한다.

## 남은 주요 전환 경로

- raw scalar levels의 중앙화와 기존 SQL joins에 대한 adapter
- SQLAlchemy 거래 전략, raw sqlite collector, 특수 Apple v2 mixed frames
- 공용 observations/price path index와 collector ingestion
- daily-rsync closure export/import/pin, 시각화와 simulation reader
- 전체 active runtime cutover, 역사 자료 증분 migration·정리, 운영 복구 검증

이 항목이 남아 있는 동안 전체 목표는 완료가 아니다.

## 2026-09-30 동기화·조회 checkpoint

Mac mini의 공용 서비스와 White 전환은 commit `1ecf87a`로 운영 중임을 확인했다.
White 설정 hash `504330793b80997c25b27858fc0ccb455fefca2da749563502078d0d55cd6953`는
전환 전후 동일하며 실제 source digest만 전환 이력으로 남는다. 이전 GUI LaunchAgent의
외장 실행 권한 문제는 Jenkins/SSH 문맥의 서비스 감독기로 해결했다. 개별 job 실패나
잠깐의 응답 지연 때문에 이미 실행 중인 writer를 강제로 다시 띄우지 않는다.

종료된 White `20260927` shard의 원격 파일 원본 SHA는
`30370bf5fe1f093f2a6e55048f3744557825ebdc686da4380f06fbf87bbee4b0`,
기존 MacBook SQLite snapshot SHA는
`149f7bc89219c0765ac0e0bef22de92b0f63e13877e849e7256849e8f4f6991a`다.
SQLite online backup의 header 차이를 실제 source fingerprint와 원본 manifest로 연결하고,
독립 decoded 행 대사 후 새 SHA
`1c89d30ec5aab9241b5e4c26b98ee822b94b5a997fd6c401fee7601576d979cc`를 별도 storage generation으로 등록했다.
새 파일 크기는 71,512,064 bytes다.

- 해당 day의 scan→plan→sync→verify 성공: checked 1, failed 0, archive coverage complete.
- 공용 본문을 사용하는 변환본에서 가격 경로 19,402행과 terminal 출력 158행을 재생했다.
  원본 출력과 SHA가 각각 `3814508f…a2682d`, `6ad0ea44…74815`로 일치한다.
  terminal 행 수는 실제 체결이나 수익 건수가 아니다.
- 양쪽 원본은 검토 폴더에 보존되어 있다. 같은 filesystem 내 이동이므로 공간 회수로 계산하지 않는다.
- 과거 shard pin은 아직 완료하지 않았다. 실패했던 명령은 source_key 마지막 `1`을 누락한
  잘못된 ID였으며, catalog의 정확한 key는
  `79389c35dd038c625f10eeec0b16750b21bb0a8bc2894432e2593deb8ea744c1`이다.
  이후 세션이 외장 쓰기·네트워크 제한으로 전환되어 pin·원격 후속 배포를 보류했다.

공용 index 후속 코드에서는 batch response를 토큰마다 복제하지 않고 하나의 관측과
재사용 가능한 토큰/event 집합으로 검색한다. 개별 토큰 검색도 해당 batch를 찾을 수 있다.
source clock이 없는 역사 행은 migration 시각으로 대체하지 않고 gap으로 집계한다.
이 후속 변경은 아직 원격에 배포하지 않았다. 새 Observation 필드를 사용하는 client보다
service를 먼저 업그레이드하고, 이후 각 job의 실제 설정 불변성과 자연 실행을 확인해야 한다.

후속 bundle v2는 이미 로컬에 있는 본문을 다시 보내지 않으면서 새 관측 index를 갱신한다.
하나의 동일 본문에 관측이 많이 연결되어도 cursor 페이지로 나눠 처리하며, 각 페이지의
본문 closure·관측 hash·토큰/event 집합을 검증한다. 페이지별 read snapshot이므로 페이지
사이에 이미 지난 cursor 위치로 추가된 기록은 다음 refresh에서 반입된다. 모든 페이지가
한 시각의 전역 snapshot이라고 주장하지 않는다. v1 본문-only bundle은 읽을 수 있지만
새 관측까지 동기화됐다는 증거로 쓰지 않는다.

기존 shared level을 다시 압축 이전할 때는 auxiliary DDL뿐 아니라 모든 group/binding의
실제 table·ordinal 대응을 먼저 검증한다. 해석하지 못한 링크를 누락시킨 채 VERIFIED로
종료하지 않는다. Apple legacy pool은 모든 reference role을 조사하고, 요청 URL/method가
없는 response reference는 public event 참조를 함께 갖더라도 private/unverified로 보존한다.

Apple 월별 frame 전환은 public/private 분리와 월 예산 seed, 최종 검증 helper 및
daily-rsync lineage 검증이 구현됐으나 fleet cutover는 남아 있다. Seed가 추가한 관리용 meta를
원본 설정·frame에서 다시 계산하고, 원본→중간 artifact→최종 artifact의 SHA를 구분한다.
원래 `finished` 시각으로 생성한 공용 관측은 실제 저장소에서 exact ID로 다시 확인한다.
실제 네 runtime에서 확보한 8개 frame으로 native decoder 복원과 원래 시각 보존을
rehearsal했다. 설정 문서는 격리된 fixture이므로 현재 운영 설정을 검증한 결과가 아니다.

월 파일은 수집 재개 뒤 계속 바뀐다. 고정 SHA를 증명하는 `.storage-migration.json`을 live
canonical 옆에 그대로 두면 이후 sync가 실패한다. 원래 writer를 정지한 상태에서
최종 checkpoint 복사→sync→verify→pin→검토 폴더로 sidecar 이동을 완료한 후 재개한다.
실제 SyncService catalog/pin/lineage를 사용한 테스트에서 이 순서와 이후 새 frame 추가·일반
mutable sync, 이전 두 pin의 보존을 확인했다. stale sidecar가 남아 있으면 계속 실패해야 한다.
두 호스트의 실제 배포·이동은 아직 하지 않았다.

공용 신규 본문의 zlib 압축은 level 3에서 6으로 조정했다. 실제 verified White 응답 하나
(원본 6,841,606 bytes)에서 897,277→710,661 bytes로 줄고, 해당 장비의 단일 측정 압축
시간은 17.3→33.4ms였다. 원본 SHA와 복원 bytes는 동일하다. 이 20.8%는 해당 응답의
압축본 차이이며 전체 DB 절감률이 아니다. 기존 저장된 payload를 다시 쓰지는 않는다.

현재 제한 세션에서 UNIX socket `bind`가 `Operation not permitted`로 거절되어
service/supervisor 운영 통신 테스트는 재실행을 완료하지 못했다(17 failed, 22 errors,
10 passed; 원인은 socket 생성 권한). 소켓을 사용하지 않는 store/capture/migration
검증은 별도로 통과했다. 이전 권한 문맥의 성공과 현재 실패를 합쳐 최신 전체 검증 성공으로
표현하지 않는다. 배포 전 정상 권한 문맥에서 서비스 통신을 다시 확인해야 한다.

마지막 로컬 통합 검사에서는 public index/migration/bundle/capture/Apple/store/reader/source
303개 테스트와 30개 전략 구조 검사가 통과했다. White의 새 v8 source digest는
`368cc97fae5beeb32badac15500f456a931320444ac31c642dd51a37ff3ca8e1`이며 위의 resolved config hash를
유지한다. Watermelon은 새 shared-receipts-v8 manifest를 사용하며 이전 v7/r6 문서를 보존한다.
역사 Coconut의 WSS 원본에 개별 메시지 수신 시각이 없으면 `missing_per_message_receipt_time`
gap으로 남긴다. WSS batch 종료 시각으로 가격 관측 시각을 대신 만들지 않는다.

## 남은 scalar snapshot 전환 범위

본문 공용화만으로 legacy SQLAlchemy `market_snapshots`가 공용화되지는 않는다. 기존
verified local pin을 읽어 확인한 다음 값은 과거 사본의 수이며 현재 remote 현황이 아니다.

| Source | Pin 시점 | market_snapshots 행 |
|---|---|---:|
| Red / Date default | 2026-09-09 | 2,660,037 |
| Blue / Cherry narrow | 2026-09-13 | 0 |
| Yellow / Cherry narrow | 2026-09-13 | 0 |
| Yellow / Cherry default | 2026-09-13 | 0 |

Date의 condition/probability/liquidity/volume 값은 공개 Gamma 값의 기존 숫자 변환 결과다.
timestamp는 거래소 수신 시각이 아닌 당시 ORM 삽입 시각이다. NULL·0·timestamp·original ID를
수정하거나, 없는 원시 API 응답을 복원했다고 주장하지 않는다. Momentum·주문·fee·판단은
별도 private 상태이며 이 snapshot table에 포함돼 있지 않다.

원본 세 REAL 값에 각각 75-byte 참조를 붙이면 커지므로 그대로 body adapter를 적용하지
않는다. 공용 typed scalar table과 조건 식별자 dictionary, 전략별 membership을 결합하고,
조건/시간 index 조회·single-writer insert ACK·원래 ID/commit/삭제 rowcount·retention을
함께 검증해야 한다. ORM repository와 공통 maintenance의 DELETE도 포함한다. Date/Cherry의
이 경로는 아래 개발 checkpoint까지 구현·검증했지만 실제 운영 이전은 남아 있다.

### Scalar 저장 구조 시험 — 운영 미적용

초기 typed scalar prototype과 Date/Cherry reader·retention adapter를 비교한 뒤
durable HOT→1,024행 압축 block 구조를 선택했다. 아래 처음 두 방식은 채택하지 않았다.
각 숫자에 SHA 참조를 붙이거나 작은 행마다 큰 index를 추가하면 중앙화해도 원래보다
커질 수 있기 때문이다. 전체 runtime 연결·배포 전이므로 운영 전환 완료를 뜻하지 않는다.

| 시험 | 비교 자료 | 공용+전략별 참조 / inline 크기 |
|---|---|---:|
| 행별 SHA 참조 | 합성 100,000행·1,000 condition | 1.447배 |
| 정수 ID 참조 | 실제 Date 앞 100,000행 | 1.187배 |
| 1,024행 numeric-value block | 같은 실제 Date 100,000행 | 0.920배 |

모든 표에는 필요한 index와 private membership 비용을 포함한다. 실자료 10만 행의
condition 36,972개와 전체 266만 행의 condition 55,595개는 분포가 다르므로 앞부분만으로
전체 절감률을 단정하지 않는다. 비교 원본 SHA는
`84d2bc974cda16753fbbf19023fb596bc75572ff0bf4002594eaf4aa96b1bc44`다.
원본은 read-only이며 시제품은 임시 영역에만 있다. 운영 공간 회수 실적이 아니다.

숫자형/NULL 시각이 있는 경우 SQLite DATETIME의 NUMERIC affinity도 유지해야 한다.
새 view가 BLOB affinity를 노출하면 값은 같아도 비교 결과가 달라질 수 있어,
정확한 cell 복원뿐 아니라 기존 predicate/query plan을 함께 검증한다.
초기 `commit=False` batch writer는 attached public WAL snapshot의 read-your-writes 문제도
해결해야 한다. Date/Cherry 외 전략까지 호환된다고 아직 주장하지 않는다.

전체 2,660,037행의 후속 RAM 비교에서는 기준 691,941,376 bytes 대비 정수 방식
676,528,128 bytes(−2.23%), 압축 block 방식 497,848,320 bytes(−28.05%)였다.
전체 typed hash·행 수·선택 조회를 대사했고, 마지막 압축 방식을 선택했다.
실시간 한 행 쓰기도 durable HOT 이후 압축되도록 구현하고 commit 전후 종료와 재시도를 시험했다.
최종 인터페이스와 검증 범위는 [scalar 전환 계약](shared-market-data-scalar-contract.md)에 있다.

후속 실제 구현을 Date 10만 행에 적용한 임시 파일 검증은 25,784,320→24,252,416 bytes
(공용+private, 5.94% 감소)였고 원본 값·ID·조회·HOT 압축 경계·재시작을 확인했다.
Date/Cherry의 snapshot 저장/조회/retention과 daily-rsync의 scalar 의존성·UUID·source namespace
대사 및 pin 경로를 연결했다. 다른 scalar schema의 연결과 실제 fleet 배포는 여전히 남아 있다.

이후 daily-rsync 전체 419개 테스트가 통과했다. Snapshot sidecar 탐지에서 불필요한 사전
SQLite open을 제거하여 기존 CANTOPEN 재시도 횟수와 immutable fallback 계약을 유지했다.
source/job/runtime 연결은 실제 sync·verify·pin 경로에서 확인하며, 의존성만 확인한 helper의
결과를 source 경로 검증 완료로 표시하지 않는다.

새 service/client는 `capabilities`로 공용 본문·관측 subject·scalar block 계약 지원 여부를
확인한다. Migration CLI는 출력 이전에 필요한 계약을 요구하고, supervisor는 반복 가능한
`--require-capability` 옵션으로 같은 사전 검사를 할 수 있다. 구 서비스가 `stats`에 응답해도
신규 계약 지원으로 간주하지 않는다. 불일치·timeout은 실행 중인 writer를 종료하거나 자동
교체하지 않는다. 이 경로는 소켓을 사용하지 않는 dispatcher/supervisor 검사까지 통과했으며,
현재 원격 서비스 `1ecf87a`에는 아직 배포되지 않았다.

## 2026-09-30 snapshot 연결 후속 checkpoint

10개 여섯 컬럼 전략과 13개 확장 schema 전략의 snapshot writer/reader를 로컬에서 연결했다.
8개 추가 legacy 프로젝트 전체 687개, Gamma/Kiwi 7개 전체 1,837개, 스포츠 5개 전체
1,426개 테스트가 통과했다. Blueberry 전체는 새 shared 경로를 포함해 384개가 통과했다.
이는 실제 운영 이관이나 IPC 부하 검증을 대신하지 않는다.

확장 snapshot의 첫 구현은 문자열·index 반복 때문에 실제 Blueberry 자료에서 커져
배포 대상으로 채택하지 않았다. 정수 사전으로 고친 최종 검정은 78,484행 전체에서 원래
11열·index 34,652,160 bytes 대비 public+private 25,440,256 bytes로 **26.58% 감소**했다.
전체 값 hash·기존 조건/시간 조회가 일치했다. 상세 범위와 원본 hash는
[projection 계약](shared-market-data-projection-contract.md)에 있다.

Honeydew/Nectarine는 원래 지연 flush를 유지하는 한정된 Session hook을 사용한다. 여러
pending snapshot 중 뒤쪽 public ACK가 실패하면 같은 flush의 context와 객체 변환을
savepoint로 되돌린다. caller private transaction·nested rollback·재시도도 검사했다.
스포츠는 원래 scanner 기준시각을 저장 시 전달하고, Plum health 갱신을 private context로
연결했다. 금액·entry·TP·SL·trader/classifier 계산을 변경하지 않았다.

13개 schema의 격리 migration과 일반 source/migration의 daily-rsync sync→verify→pin을
연결했다. public dictionary의 모든 ID가 있더라도 private condition/token/outcome 결합이
맞지 않으면 거부한다. 비등록 private PMDATA/PMMIX는 public transfer 이전의 정책 검사와
migration의 private physical 대사에서 거부한다. 과거 pin을 덮어쓰지 않는다.

최종 daily-rsync 전체 474개가 통과했다. 공용 package 전체 실행은 1,033개 통과와 기존
소켓 생성 제한으로 17 failed/22 errors였다. 이후 바뀐 projection/소유권/이관/source-digest
범위 146개와 White recorder 39개, Watermelon source 3개도 통과했다. IPC 전체 성공으로
표현하지 않는다. 원격·외장·Git 쓰기 권한 제한도 그대로다.

개발 중인 White v8 source digest는
`c016064d3af836328f82a5f73d6d327497b70e8d09579c12892aba3770a32efe`이며 config hash
`504330793b80997c25b27858fc0ccb455fefca2da749563502078d0d55cd6953`는 유지한다.
이전 v7/r6 manifest는 바꾸지 않았다. 새 v8도 원격 미배포 상태다.

필수 잔여 범위에는 [market_catalog의 공개 필드](shared-market-data-catalog-scope.md),
raw collector의 미전환 public scalar 감사, 전체 fleet·역사자료 이관과 원본 정리,
실제 동시 IPC·자연 Jenkins 실행·UI/재생 검증이 있다. 현재 목표는 완료가 아니다.
