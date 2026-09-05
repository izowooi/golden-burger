# v2a 원본 행 검증의 순차 읽기와 단계별 시간 기록

일반 복구 실행2406에서 원본/seed·경로 처리 시간이 앞선 예산을 대부분 사용하여,
Gamma 요청에 남은 read timeout이1ms 수준으로 줄었다. API 장애로 오인하지 않도록
DB 준비·source/seed 검증·각 수집/계산·원자적 저장 단계의 시작과 종료 시간을 로그에 남긴다.

- 읽기 전용 연결은64MiB 페이지 캐시를 사용하며 연결 종료 시 해제한다.
- imported episode/condition/threshold를 물리적 table 순으로 읽고, 기존 SQL ORDER BY와
  동일한 키/순서로 메모리에서 정렬해 기존 행별·전체 해시를 대조한다.
- 전체 seed 검증 이후 실제 수집에는 필요한 episode/condition/token ID와 fixed shares만 읽어
  큰 원본 JSON을 다시 읽지 않는다. 분석용 전체 행 조회는 유지하고 실제 모집단/순서는 같다.
- 최신 가격 조회의 입력 ID를 정렬해 같은 인덱스를 순서대로 읽으며 반환하는 값은 그대로다.
- 검사하는 모든 seed 행/칼럼/해시/개수와 원래 anchor는 그대로다. 실패를 성공으로 처리하거나
  원본/seed를 변경하지 않는다. 정상 운영에서는 추가 source 전체 스캔/재해시를 하지 않는다.
- 기존 entry·follow-up·cadence·threshold·450초 deadline·480초 기준은 바꾸지 않는다.
- 임시 사본 벤치마크와 실제 Jenkins 실행시간을 구분한다. 실제 수동/자연 실행에서 다시 확인한다.
