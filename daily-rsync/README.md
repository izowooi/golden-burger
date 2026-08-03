# daily-rsync

Jenkins Mac mini의 전략 SQLite와 로그를 MacBook으로 안전하게 pull하는 로컬 전용
동기화 앱입니다. AI가 Jenkins에 접속하는 구조가 아니라, 사용자가 이 앱의 버튼 또는
CLI를 실행할 때만 `ssh`와 `rsync`가 동작합니다.

## 가장 쉬운 실행 방법

저장소 안의 `daily-rsync-toggle.sh`를 실행하면 현재 상태에 따라 웹 UI를 켜거나 끕니다.

```bash
cd /Users/izowooi/git/t1/daily-rsync
./daily-rsync-toggle.sh
```

같은 명령을 다시 실행하면 종료됩니다. Finder에서는
`Daily Rsync 켜고 끄기.command`를 더블클릭해 같은 동작을 할 수 있습니다. 상태를
명확히 지정하려면 다음 명령을 사용합니다.

```bash
./daily-rsync-toggle.sh start
./daily-rsync-toggle.sh stop
./daily-rsync-toggle.sh status
./daily-rsync-toggle.sh restart
./daily-rsync-toggle.sh open
```

스크립트는 `data/ui-server.pid`에 기록된 Daily Rsync 프로세스만 종료합니다. 같은
port에 다른 서버가 있거나 PID 소유권을 확인할 수 없으면 임의로 `kill`하지 않습니다.

Finder의 `응용 프로그램`에서 **Daily Rsync**를 더블클릭하면 로컬 웹 UI가 열립니다.
앱은 `127.0.0.1:8765`에서만 실행되며 외부 네트워크에는 공개되지 않습니다.

최초 한 번 앱을 설치하는 명령은 다음과 같습니다. 저장소에 포함된 `.app`을
`~/Applications/Daily Rsync.app`으로 복사하고 현재 프로젝트 경로를 연결합니다.

```bash
cd daily-rsync
uv sync --frozen
uv run daily-rsync install-app
open "$HOME/Applications/Daily Rsync.app"
```

이 설치는 현재 작업 과정에서 이미 수행하므로, 이후에는 터미널 명령을 기억할 필요가
없습니다. 서버가 이미 실행 중이면 앱은 기존 화면만 다시 열고 중복 서버를 만들지
않습니다.

## 웹 UI에서 할 수 있는 일

- Mac mini SSH와 디스크 상태 확인
- 전체 `polybot-*` Job 검색 및 무작위 선택
- 현재/과거 전략과 로그 기간, 안전 사본 DB 옵션 선택
- 증분 전송 계획의 파일 수·건너뜀 수·최대 용량 확인
- 동기화 진행률과 기술 로그 확인
- 동기화된 catalog 탐색과 Finder 열기
- SQLite DB 무결성 검사와 snapshot pin
- 계좌 deployment epoch 기록
- 365일 retention 미리보기와 확인 후 적용
- DB가 포함된 AI evidence bundle 생성
- 최근 동기화 결과 확인

오래 걸리는 동기화·무결성 검사·bundle 작업은 화면을 멈추지 않고 백그라운드에서
순서대로 실행합니다.

## 개발 또는 CLI 실행

```bash
cd daily-rsync
cp config.example.toml config.local.toml
uv sync --frozen --extra dev
uv run daily-rsync doctor
uv run daily-rsync serve --open
```

기본 설정은 이미 등록된 SSH alias `macmini-m5`, Jenkins home
`/Users/jongwoopark/.jenkins`, local `daily-rsync/data`를 사용합니다. SSH private key나
Polymarket credential을 이 프로젝트 설정에 넣지 않습니다.

## CLI 사용법

CLI는 웹 UI와 같은 Python 엔진과 catalog를 사용합니다. AI 자동화, cron, scheduled
작업에서는 계속 아래 명령을 사용할 수 있습니다.

```bash
# 접속과 디스크 확인
uv run daily-rsync doctor

# 특정 Jenkins job의 현재 자료 조사
uv run daily-rsync scan --job polybot-king

# 전송 계획 생성
uv run daily-rsync plan --job polybot-king --strategy golden-queen

# 출력된 plan ID 실행
uv run daily-rsync sync --plan <plan-id>

# local 자료 검증
uv run daily-rsync verify --job polybot-king

# 회고용 로컬 DB·로그 자동 탐색 (잡명 또는 전략명 하나만 있어도 됨)
uv run daily-rsync locate --job polybot-king
uv run daily-rsync locate --strategy golden-queen

# 계좌가 바뀐 deployment epoch 기록
uv run daily-rsync account-epoch \
  --job polybot-king --strategy golden-queen \
  --account-alias golden-king --first-build 1

# 365일 보존 대상 미리보기 (삭제는 --apply를 붙인 경우만)
uv run daily-rsync prune --dry-run

# DB를 포함한 독립 AI bundle 생성
uv run daily-rsync bundle \
  --job polybot-king \
  --strategy golden-queen \
  --from-date 2026-07-24 \
  --to-date 2026-07-29
```

`sync-job`은 plan 생성과 실행을 한 번에 수행하는 운영자 편의 명령입니다.

```bash
uv run daily-rsync sync-job --job polybot-king --strategy golden-queen
```

## AI 회고용 evidence 찾기

`locate`는 SSH나 원격 scan을 실행하지 않고 로컬 catalog만 읽습니다. 같은 전략을 여러
Jenkins job이 실행하거나, 한 job이 시간에 따라 여러 전략을 실행한 경우에도
`source → Jenkins job → strategy → runtime job`을 분리해 모두 반환합니다.

출력에는 최근 sync 시도와 최근 성공 sync run, runtime별 DB 원본/로컬
경로·원본 시각·SHA-256·artifact 상태, bot log 및 Jenkins console log의
개수·범위·로컬 루트, 그리고 실행할 `verify` 명령이 포함됩니다. 회고 전에는 반드시
다음 순서를 지킵니다.

```bash
cd daily-rsync
uv run daily-rsync locate --job polybot-bear --strategy golden-honeydew
uv run daily-rsync verify --job polybot-bear --strategy golden-honeydew
```

`analysis_ready=true`는 최신 sync 시도까지 성공했고 `SYNCED` 또는 `SOURCE_MISSING`
상태의 로컬 DB가 존재한다는 뜻입니다. 예전 성공 뒤 최신 시도가 실패했다면
`latest_successful_sync`가 있어도 false입니다. `SOURCE_MISSING`은 원격에서 사라진 과거 epoch의 보존본이므로
`source_completed_at` 이후의 현재 상태를 주장하면 안 됩니다. 현재 파일의 checksum과
SQLite `quick_check`까지 다시 확인했다는 뜻은 아니므로 `verify`를 생략하면 안 됩니다.
보존 정책으로 이미 삭제된 로그는 `verify`가 `skipped_retention_deleted`로 별도
보고하며 결손으로 오인하지 않습니다. plan 파일의 존재만으로 동기화 성공을 추정하지
않습니다.

## 기본 정책

- 최초 로그 범위: 최근 60일
- 로그 보존: 365일
- 로컬 디스크 안전선: 50GB
- DB: canonical `trades.db`, `trades_sim.db` 기본 선택
- DB 이력: latest 1개, 수동 pin
- 로그: 원문 gzip, redaction 없음
- 원격 쓰기: `~/.cache/daily-rsync`의 일회성 SQLite snapshot만 허용
- 원격 삭제: 없음

중단된 단일 파일 전송은 `data/incoming/*.partial`을 남기고 다음 실행의 rsync가
이어받습니다. 검증을 마치지 못한 partial은 catalog의 완료 자료나 DB `latest`로
승격되지 않습니다.

과거 전략은 scan 결과에 표시되지만 현재 전략만 기본 선택합니다. 원격에서 과거 전략을
직접 삭제해도 local 자료는 자동으로 삭제되지 않습니다.

## 데이터와 비밀정보

Jenkins console log에는 과거 shell trace로 private key나 webhook이 들어 있을 수 있습니다.
사용자 결정에 따라 원문을 보관하므로 `data/`와 `config.local.toml`은 절대 Git에
추가하지 않습니다. 앱은 local data directory를 `0700`, 파일을 `0600`으로 만들고
웹 UI를 `127.0.0.1`에만 노출합니다.

세부 구조와 장애 복구는 [DATA_LAYOUT.md](DATA_LAYOUT.md),
[OPERATIONS.md](OPERATIONS.md), 구현 결정은 [PLAN.md](PLAN.md)를 참고합니다.
