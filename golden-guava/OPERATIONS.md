# 배포와 점검

현재 새 연구 수집기를 외장 T7에 배포해 검증 중이다. 네 잡의 수동 실행은 확인했지만,
정기 실행·관측 가능한 실제 경기·호가·종료 추적은 각각 증거로 확인해야 한다.
실거래 준비 완료를 뜻하지 않는다. 현재 상태는 local Jenkins inventory를 우선한다.

## 외장 workspace

각 job은 `/Volumes/t7/jenkins/<job>`를 독점한다. 기존 shared T7 sentinel과 내부 디스크의
UUID pin을 읽으며 새 UUID를 자동 승인하지 않는다. mount 누락·symlink·잘못된 marker·
100GiB 미만 또는85% 사용 이상이면 DB/네트워크 전에 거부한다.
bootstrap은 표준 Python만 쓰며 package 설치보다 먼저 실행한다.

## 배포 순서

1. 대상 예약 중단과 실행 종료 확인. 과거 data/지갑 소유권·잔여 주문 확인.
2. 기존 mono-repo의 검증한 code artifact만 외장 workspace에 배포. data/.env/키/venv 포함 금지.
3. `python3 golden-guava/scripts/verify_workspace_bootstrap.py --job <job> --workspace <path> --write-marker`.
4. 해당 프로젝트에서 `uv sync --frozen`을 한 번 실행. live 준비 때만 승인된 extra `live`를 사용.
5. 수동 research build의 실제 자료·오류/수집누락·전체 시간을 검증.
6. 1분 예약과 concurrentBuild=false를 설정하고 자연 실행을 확인.
7. daily-rsync로 새 job×strategy의 DB/로그를 동기화·검증. 이전 Kiwi/NHL epoch를 섞지 않음.

정기 shell은 bootstrap 후 `uv run --no-sync polybot run --simulate --job <runtime>`이다.
매분 sync/config/status/전체 DB 검사를 반복하지 않는다. 실패·skip과 성공을 분리해서 기록한다.

## 운영 판단

첫24시간은 cadence, event shard 중복/누락, 직접6/2token, request/book/fee/clock,
공식결과 매핑, 종료 후 추적, SQLite 및 용량만 확인한다. 일주일은 가설별 독립 경기 수와
비용/미관측 분포를 평가한다. 부족한 자료를0손익/체결로 보충하지 않는다.

실거래 A/B는 한 가설·한 처치축·$5로 고정한 뒤 활성화한다. `run --live` 미완료 경로를
예약하거나 모의 수익으로 승격하지 않는다. 라이브 정책/포지션 통합과 검증은 아직 남아 있다.
