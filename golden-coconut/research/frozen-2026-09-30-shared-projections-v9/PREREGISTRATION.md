# 공용 저장소 source epoch v9

2026-09-30 배포한 v8 source manifest는 보존한다. v9는 공용 reader의 불변 projection 크기·hash 재계산과 level append의 반복 전체 스캔을 줄인 소스 변경만 반영한다. 저장 형식·runtime·관측 범위·수집 주기·storage gate·resolved config는 동일하다.

# 공용 저장소 projection reader 의존성 갱신

White v7의 종목·registry·경기 범위·cadence·storage gate·논리 자료 계약과 수집 파라미터는 유지한다.
공용 SQLite reader가 Apple frame과 명시적 public/private 혼합 envelope도 해석하도록 확장되어
그 실제 shared dependency를 source manifest에 포함한다. White 자체 본문·clock·book의
공개 컬럼 분류와 exact bytes·SHA는 바꾸지 않는다.

기존 배포된 v7 preregistration/manifest는 수정하지 않는다. schema/runtime/data contract는
동일하고 새로운 source digest만 forward provenance에 기록한다. 공개 blob ACK 후 로컬 참조
commit, 누락 closure 실패, 원본 row/정산 출력의 정확한 복원 계약을 계속 따른다.

현행 recorder의 추가 source 복사본은 `coconut-recorder-v1` 저장 profile로 분리한다.
이는 user_version=1인 원래 recorder 물리 schema를 가리키며 historical Coconut v6와
다른 계약이다. 공개 팀명·condition/token·outcome·정규화된 경기 시작 시각은 공용 typed
projection에, slots/terminal/carryover의 공개 값은 정확한 lexical JSON 조각에 저장한다.
수집기 자체의 role 판정·상태·next_due·claim·성공/실패·config는 private에 남는다.

이미 채워진 inline DB는 자동 변환하지 않는다. 원본이 보존된 offline derivative 검증 후
canonical을 전환하며, 새 RAW shard와 UTC rollover는 같은 논리 행·working set을 유지한다.
tracking cache는 현재 공개 참조만 갱신하고 원래 관측 표가 과거 증거를 보존한다.
별도의 synthetic 가격/clock 관측이나 추적 상태 history를 만들지 않는다. 이 draft는
실제 job에 추가 배포되었다는 기록이 아니며, live 매매 기준을 바꾸지 않는다.
