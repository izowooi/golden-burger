# v2a 관측 저장 캐시와 저장 중 제한시간 검사

동일한 entry/follow-up·가상정책·원본 anchor·seed·데이터 계약을 유지하는 운영 보완이다.
2026-09-05의 복구 빌드에서 초기 인덱스 준비 뒤 대량 저장도 작은 SQLite 페이지 캐시로
외장하드 읽기/쓰기를 반복하는 현상을 확인했다. 저장이 끝난 뒤에만 제한시간을 확인하면
이미 너무 오래 실행된 뒤이므로, SQLite 쓰기 도중에도 같은 cooperative deadline을 검사한다.

- 성공 cycle을 한 transaction에 저장하는 연결에만 최대256MiB(262144KiB) 페이지 캐시 적용.
  필요할 때만 할당되고 연결 종료 시 해제된다. 다른 API receipt 연결의 기본값은 유지한다.
- cache_spill·journal=DELETE·synchronous=FULL·foreign_keys·single-writer 계약은 완화하지 않는다.
- deadline은 읽기뿐 아니라 INSERT/executemany 도중에도 확인하며, 만료 시 성공 기록 전에
  중단한다. rollback 중에는 만료된 progress handler를 해제해 정상 원상복구를 방해하지 않는다.
- 기존450초 검사와480초 정기 실행 기준은 유지한다. 저장소 자체의 정지나 긴 fsync까지
  물리적으로 보장할 수는 없으므로 Jenkins 실제 duration도 별도로 확인한다.
- 중단된 유지보수 빌드2405는 성공으로 보지 않는다. SQLite의 정상 journal recovery로
  미완료 transaction을 취소하고 해당 run에 운영자 중단 사실을 FAILED로 남긴다.
  이미 commit된 기존 관측·seed·anchor와 완성된 인덱스는 보존한다.
- 테스트는 저장 도중 deadline을 넘긴 대량 INSERT 전체가 rollback되어 cycle/SUCCEEDED가
  남지 않는지, 메모리 상한과 FULL durability가 유지되는지 확인한다.

이 변경은 수익성 개선이나 새 가상 체결을 주장하지 않는다. 복구 뒤 실제 일반 cycle과
자연 timer 실행, 재동기화한 DB의 원자성·무결성으로 운영 결과를 검증한다.
