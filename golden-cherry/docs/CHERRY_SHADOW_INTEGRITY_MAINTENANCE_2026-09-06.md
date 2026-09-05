# Cherry Shadow 주기 무결성 검사와 전체 유지보수 검사

## 변경 이유와 관측 한계

2026-09-05 UTC의 Jenkins #307은 22:53:10.272에 시작하여 23:28:29.076에
SUCCESS로 종료했다. 실행시간은 2,118.804초(35분 18.804초)이고,
별도의 큐 대기시간 1,837.180초는 실행시간에 포함하지 않는다.
수집 결과의 `elapsed_seconds=86.136`은 수집기 내부 측정값이지 Jenkins 전체 시간이 아니다.
종전 코드는 수집 이후 `run`의 전체 `PRAGMA quick_check`, 성공 기록,
별도 `status`의 COUNT와 두 번째 전체 `quick_check`를 실행했다.
이 작업들은 수집 후 240초 예산을 재검사하지 않았다.

따라서 **예산 밖 DB 작업과 전체 검사 중복은 확인됐지만, 35분 전체의 원인이
quick_check 하나라고 확정한 것은 아니다.** #308의 B-tree 순회/pread 스택만으로도
실행 중인 SQL을 특정할 수 없다. #308은 수집 결과 `elapsed_seconds=128.722`와
`quick_check=ok` 출력 후 status 단계에서 운영 담당자가 중단했다.
이 중단은 이미 남겨진 수집 SUCCEEDED를 FAILED로 소급 변경하지 않는다.
새 단계 로그와 Jenkins 전체 duration을 함께 확인해야 지연 위치를 구분할 수 있다.

배포309에서 기존 console formatter가 로컬 KST에 `Z`를 붙이는 별도 표기 오류도 확인했다.
Shadow 전용 formatter를 UTC로 고정한다. 과거 로그·DB 시각은 소급 변경하지 않으며,
309까지의 console asctime은 Mac mini의 KST로 해석한다. DB의 UTC 시각과 혼합하지 않는다.

## 주기 실행의 제한된 무결성 검사

사전등록 원문의 “SQLite integrity probe” 계약을 다음 범위로 구현한다.

- `shadow_schema_metadata`, `shadow_config_versions`, `shadow_run_events`,
  `shadow_market_sweeps`, `shadow_episodes`, `shadow_episode_policies`에만
  테이블별 `PRAGMA main.quick_check('<table>')`를 수행한다.
- SQLite 3.33 이상, 정확한 테이블 존재와 연결의 `foreign_keys=ON`을 확인한다.
- 큰 raw payload·시장 상세·membership·book/path 테이블은 주기 검사에서 순회하지 않는다.
- 프로브는 읽기 전용 연결이며 다른 writer를 기다리지 않는다. SQL 실행 전후의 남은 시간
  검사와 SQLite VM 진행 콜백을 통해 240초 협력적 예산을 적용한다.
- 예산은 저장소 초기화 전부터 측정하며 수집 후, 프로브 후, SUCCEEDED INSERT의
  commit 직전에도 검사한다. 손상, 불완전한 검사 응답, 시간 초과는 FAILED이고
  성공으로 표시하지 않는다. 이미 보존된 원시 실패 증거는 삭제하거나 성공으로 바꾸지 않는다.
- 디스크 시스템 호출이나 commit 자체를 도중에 강제 중단하는 하드 실시간 보장은 아니다.
  단계 로그·Jenkins wall time·저장장치 지연을 계속 함께 확인한다.

결과 필드는 `integrity_probe.status`, `integrity_probe.scope`, `integrity_probe.tables`,
`foreign_keys_enabled`와 프로브 소요시간으로 범위를 명시한다.
전체 검사를 하지 않은 실행은 **`full_quick_check=not_run_periodic_use_maintenance`**이며
종전의 전체 검사로 오인될 `quick_check=ok`를 출력하지 않는다.
`elapsed_seconds`는 기존 수집기 측정값을 보존하고 `run_elapsed_seconds`를 별도로 기록한다.

부분 검사는 전체 파일의 freelist, 테이블 간 페이지 중복, 큰 raw 데이터 손상이나 과거 모든
외래키 위반을 증명하지 않는다. `foreign_keys_enabled=true`도 과거 전체 FK 검증을 뜻하지
않으며 `historical_foreign_key_check=not_run`을 함께 출력한다.
범위와 한계는 [SQLite 부분 검사 문서](https://www.sqlite.org/pragma.html#pragma_integrity_check),
협력적 취소 방식은 [SQLite 진행 콜백 문서](https://www.sqlite.org/c3ref/progress_handler.html)를 따른다.

## 전체 검사 유지보수

`ShadowRepository.quick_check()`, 기존 `status --shadow`, `analyze --shadow`의 전체
DB 검사는 보존한다. 주기 Jenkins shell에서 status를 매번 호출하면 COUNT와 전체 검사
비용이 그대로 남는다. 운영 담당자가 주기 shell과 별도 유지보수 실행을 분리해야 한다.
이 패치는 Jenkins 구성이나 예약을 변경하지 않는다.

전체 검사는 보존·검증된 로컬 동기화본에서 분석할 때 또는 운영 담당자가 확보한
별도 유지보수 시간에 명시적으로 수행한다. 동시에 원격 대용량 검사를 중복 실행하지 않는다.
status는 기존대로 저장소 초기화 후 요약하므로 운영 DB를 전혀 쓰지 않는 명령으로
소개하지 않는다. 분석기의 read-only 전체 검사는 다음과 같이 명시한다.

```bash
uv run python main.py analyze --shadow --db /absolute/path/to/verified/trades_sim.db \
  --start 2026-09-04T16:00:00Z --end 2026-10-04T16:00:00Z
```

기간은 검사 대상에 맞춰 지정하며 위 예시는 명령 형태일 뿐 실제로 해당 기간을
수집 완료했다는 의미가 아니다. 전체 검사를 실패했다면 프로브 통과로 덮어쓰지 않는다.

## 변경하지 않는 실험 계약

사전등록 원문·고정 SHA-256, entry/exit grid, universe, 실험 기간, shadow_config.yaml,
DB schema와 과거 행은 변경하지 않는다. 본 문서는 source manifest에 포함한다.
소스 변경으로 strategy_source_digest가 달라지고 현재 config_hash는 이 digest를 포함하므로
파생 config_hash도 바뀐다. 과거 cohort를 재작성하거나 같은 cohort로 합치지 않는다.
이것은 수집 무결성·운영 개선이며 수익성 판정이나 파라미터 변경이 아니다.
