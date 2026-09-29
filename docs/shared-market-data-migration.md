# 공용 시장 데이터 저장소 전환

## 완료 조건

사용자 목표는 모든 전략과 시뮬레이션 수집기가 공용 시장 원본을 **한 저장소에서 쓰고 읽는 것**이다.
전략별 판단·실험·주문·체결·수수료·포지션·수동 계좌 소유권은 전략별로 남긴다.
아래는 작업 중인 계약이며 현재 배포 완료를 뜻하지 않는다.

| 요구 | 완료 증거 | 현재 상태 |
|---|---|---|
| Astra에 맞는 지침 | 활성 모델/config, 잘못된 권한·모델 가정 정정, 변경 검증 | 지침 수정 및 commit 완료 |
| 전 전략 공용 쓰기·읽기 | 실제 활성 job 전체의 writer/reader 배치와 개인 원장 분리 | payload/ORM/collector adapter 구현 중, 운영 미전환 |
| 동시성·내구성 | 단일 writer, 부하/재시도/중단/재개 테스트, commit 이후 ACK | store/service 단위 검증 통과, 원격 서비스 미배포 |
| 충분한 simulation 원본 | exact bytes/hash·관측 시각·token·depth·fee source·clock·정산, 기존 replay 대사 | White 과거 shard 전체 출력 대사 통과, 다른 소비자 검증 중 |
| 중복 원본 정리 | 기존→공용 참조 대사, 원본/신규 hash, 복원 검사, 검토 폴더/삭제 manifest | 미실행 |
| 작업 후 재개 | 시작 시 enabled였던 관련 job 복구, 자연 build·원본 적재·reader 검증 | 운영 job 미중지 |

의도적으로 비활성인 옛 전략을 새로 켜지 않는다. 매매 금액·진입·TP·SL 및 수동 포지션은 이 저장소 전환의 변경 대상이 아니다.

## 저장 경계

- 공개 Gamma event/market 응답, CLOB full book, 공개 스포츠 clock, exact market settlement 원본을 공용 저장한다.
- `GET`이라고 모두 공개자료는 아니다. 특정 wallet의 positions/activity/trades, 인증된 order/fill/balance 응답은 전략/계좌 자료다.
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
