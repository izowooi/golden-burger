# White prior VWAP indexed lookup v4b-r4

2026-09-07 수집 성능 보정. r3의 종목·진입·손절·규모·기간·스키마·42초 network / 50초 cycle 예산을 유지한다.

r3 운영 #19973에서 CLOB 성공 응답 뒤 sweep.completed_at까지 37.276초가 소요됐다.
기존 prior VWAP 조회는 signal_decisions 전체를 두 번 scan/group했다. 현재 수집한 token만
기존 outcome_token_time_idx로 찾아 동일 run/token의 decision unique index에 연결하고,
기존과 같은 decided_at 내림차순의 최신 non-null entry_vwap을 사용한다. 현재 token이 없으면
조회하지 않는다. 결과의 timestamp 기준을 receipt time으로 바꾸지 않으며 NULL·중복 threshold의
의미도 유지한다. 새 index 생성이나 schema migration은 없다.

과거 검증 사본의 모든 206 token에서 기존 결과와 정확히 동일함을 확인했다. read query plan은
전역 scan을 제거한다. prior VWAP과 collection persistence 단계의 경과시간·행/토큰 수를 로그에
남긴다. full book, raw payload, credential 값을 로그에 추가하지 않는다.

r3 source cohort와 r4 source cohort를 분리한다. 과거 FAILED run은 변경하지 않고, 수집과
snapshot의 동시 I/O 부하 및 이후 자연 실행도 별도 검증한다. 두 번의 저부하 성공을 주말
최대 부하의 성공으로 해석하지 않는다. 기존 DB row를 삭제·수정·병합하지 않는다.
