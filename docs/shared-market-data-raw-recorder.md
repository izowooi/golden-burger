# 현행 White recorder의 source 중복 분리

`coconut-recorder-v1`은 기존 recorder의 SQLite user_version=1 schema를 뜻한다.
과거 Coconut `coconut-historical-v6`와 다른 profile이며, White/Silver의 기존
runtime·자료 계약·경기 범위·cadence·저장소 하한을 바꾸지 않는다.

| 위치 | 공용 값 | 수집기별로 보존할 값 |
|---|---|---|
| tracked_events / event_observations | event ID, source에서 정규화한 scheduled_start | run, family 판정, state, end anchor의 근거, next_due, missing count |
| book_observations | event/condition/token, outcome, team name | 관측 시각·request·status·role 판정·유효성·fee 판단 |
| slots_json | source 식별자·이름·question·원래 token/outcome 배열 | slot, result_kind, verified_role, unknown 필드 |
| terminal_json | token-aligned source payout 값 | terminal proof의 해석 marker |
| registry_carryovers.state_json | 위 source 복사값 | 원래 추적 상태와 rollover 소유권 |

원래 body/clock/full-depth 저장과 별개로 남아 있던 복사값을 분리했다. mixed profile
30~32는 원본 JSON의 공백·escape·Unicode까지 복원하며, carryover 안의 JSON 문자열도
전체를 공개 자료로 취급하지 않는다. 기존 profile의 의미는 바꾸지 않는다.

공통 RAW helper는 원래 10개 표·3개 index·27개 guard, key·rowid·SQLite 타입을 검증한다.
`tracked_events`만 명시적으로 mutable이다. 현재 공개 record 참조와 private 상태를 같은
로컬 transaction에서 갱신하고, 같은 공개 값은 기존 record/receipt를 재사용한다.
새 tracking history나 가상의 관측 시각은 추가하지 않는다. 조회는 기존 due index를 쓴다.

운영 writer에는 원래 INSERT와 UPDATE 의미를 보존하는 별도 경로를 연결했다. 이미 데이터가
있는 inline DB는 자동 변환하지 않는다. 빈 같은 날짜 DB의 명시적 RAW 활성화는 실제 layout을
만든 뒤에만 시작한다. 날짜 전환은 전일 파일을 보존하고 임시 새 파일에서 contract·공용 참조·
carry 상태를 완성한 후 create-only로 게시한다. 중간 ACK/게시 실패 시 불완전한 canonical을
남기지 않아 다음 실행이 원래 archive로부터 재개할 수 있다.

로컬 검증은 원본/RAW의 실제 Recorder cycle·cold exporter·pending cache·UTC rollover,
초기화 실패 후 재시도·carry 실패 후 재시도·archive bytes 보존, 그리고 실제 SyncService의
scan/plan/sync/verify/pin을 포함한다. 새로운 8개 통합 테스트와 공통 source/mixed의 25개
테스트가 통과했다. 원래 Strawberry mutable 경로 14개 및 mixed/private 회귀 227개도
통과했다. 이후 전체 프로젝트 검사는 별도 실행 결과로 기록한다.

후속 전체 검사는 Coconut **194 passed**, daily-rsync **561 passed**였으며 Guava의
공용 원본·실제 pin·reader revision 관련 **16 passed**, 도구 revision/재생 **22 passed**와
30개 전략 구조 검사도 통과했다. 첫 전체 Coconut 검사에서 불완전하게 생성한 테스트 객체의
의존성 누락을 발견해 fixture를 보완했다. 또한 source body가 없는 fee-only 행은 관측 색인을
만들지 않도록 분리하고 전체 검사를 다시 통과시켰다. 이전 실패 결과도 별도 XML로 보존했다.

이 구현은 아직 원격에 배포하지 않았다. 현행 White 전체 역사 shard를 이 추가 RAW profile로
이전한 절감률·실제 1분 처리 시간·전체 fleet 동시 IPC를 검증한 결과가 아니다. 이전 body-only
White 이전 시험의 33.49%를 새 추가 분리의 절감률로 인용하지 않는다. 원본 정리와 운영 재개는
실제 배포 및 reader 검증 이후의 남은 작업이다.
