# daily-rsync

Jenkins Mac mini의 전략 SQLite와 로그를 MacBook으로 안전하게 pull하는 로컬 전용
동기화 앱입니다. AI가 Jenkins에 접속하는 구조가 아니라, 사용자가 이 앱의 버튼 또는
CLI를 실행할 때만 `ssh`와 `rsync`가 동작합니다.

## 빠른 시작

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
