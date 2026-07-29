# `daily-rsync` 구현 계획

## 목표

MacBook에서 버튼이나 CLI를 실행하면 `macmini-m5`의 Jenkins workspace와 build log를
SSH/rsync로 **한 방향 pull**한다. 가져온 SQLite와 로그는 전략 회고, 장애 분석,
백테스트와 AI 분석에 바로 사용할 수 있어야 한다.

실측 기준 원격에는 Jenkins console log 약 24.7만 파일·26GB, 봇 일자별 로그
약 19.3GB, 운영 SQLite 약 16.8GB가 있다. 따라서 전체 디렉터리 복사 대신
Jenkins job과 전략을 선택하고, 이미 받은 자료를 다시 받지 않는 증분 catalog가
필수다.

## 확정된 제품 결정

- 프로젝트 폴더는 `daily-rsync/`다.
- Python 엔진과 FastAPI 로컬 웹 UI를 함께 제공한다.
- 웹 서버는 `127.0.0.1`에만 bind하고 수동 버튼과 CLI로 실행한다.
- 최초 기본 범위는 선택한 job/전략의 운영 DB 전체와 최근 60일 로그다.
- 선택 계층은 `Jenkins job → strategy → runtime job → artifact`다.
- 동일 전략의 복수 Jenkins job은 job별로 분리한다.
- 동일 Jenkins job의 전략 변경은 deployment epoch로 구분한다.
- runtime job `default`는 지우거나 이름을 바꾸지 않고 Jenkins job 아래에 둔다.
- 과거 전략은 발견하되 기본 선택하지 않는다.
- 계좌는 사용자가 별칭과 적용 시작 build를 입력하는 epoch metadata로 관리한다.
- 기본 DB는 `trades.db`, `trades_sim.db`이며 `pre-*` 안전 사본은 선택 사항이다.
- DB는 latest 한 개만 자동 유지하고 필요한 시점만 수동 pin한다.
- 로그는 원문 그대로 gzip 보관하고 source timestamp 기준 365일 후 정리한다.
- AI bundle은 선택 DB를 항상 포함하며 선택 로그를 일반 `.log`로 풀어 독립 폴더로 만든다.
- 로컬 여유 공간이 계획 후 50GB 미만이면 sync와 bundle 생성을 막는다.
- 원격 삭제 기능과 `rsync --delete`는 제공하지 않는다.

## 동기화 안전 계약

실행 중인 SQLite 파일을 raw rsync하지 않는다. 로컬 앱이 표준 Python helper를
SSH stdin으로 일회 실행하여 원격 cache에 SQLite online backup을 하나씩 만든다.
원격 `quick_check`와 SHA-256을 기록하고 rsync한 뒤 로컬에서도 SHA-256과
`quick_check`가 모두 일치한 경우에만 `latest`를 원자적으로 교체한다. 원격 helper는
설치하거나 daemon으로 남기지 않으며 staging은 완료 후 정리한다.

Jenkins console log는 완료 build만 `(source, job, build number)`로 식별한다.
봇 로그는 remote path·size·mtime fingerprint로 식별한다. 완료 로그는 gzip으로
압축하되 압축을 풀었을 때 원본 SHA-256이 유지되어야 한다. source에서 사라진 파일은
local에서 지우지 않고 catalog 상태만 `source_missing`으로 바꾼다.

## 사용자 흐름

1. Doctor에서 SSH, known_hosts, 원격/로컬 디스크와 필수 도구를 확인한다.
2. Scan에서 job과 현재 전략, DB·로그 크기를 읽는다.
3. job과 전략을 선택해 plan을 만들고 신규/변경 파일과 최대 전송량을 승인한다.
4. Sync 화면에서 snapshot, rsync, 검증, 압축 진행률을 확인한다.
5. Catalog에서 local 자료와 config cohort를 찾는다.
6. 필요한 job·전략·기간을 골라 AI bundle을 만든다.

## 완료 기준

- 같은 전략/다른 job과 같은 job/다른 전략이 경로와 catalog에서 충돌하지 않는다.
- 동일 plan 재실행 시 변경되지 않은 로그 전송량은 0이다.
- 중단된 transfer가 재실행 가능하고 검증 전 DB는 latest가 되지 않는다.
- 50GB disk floor, 365일 retention, pinned DB와 bundle 예외가 동작한다.
- 실제 `polybot-king / golden-queen / queen-live-12h` DB와 로그를 동기화하고
  로컬 DB `quick_check`, manifest SHA, catalog artifact 상태를 확인한다.
