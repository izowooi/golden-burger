# 파일 구조 및 설명

`daily-report` 프로젝트의 완전한 파일 구조와 각 파일의 역할을 설명합니다.

## 📂 전체 디렉토리 구조

```
daily-report/
├── 📄 pyproject.toml              ⭐ 프로젝트 설정 및 의존성 정의
├── 📄 .python-version             Python 버전 지정 (3.11)
├── 📄 .env.example                환경변수 템플릿
├── 📄 .gitignore                  Git 제외 파일 목록
├── 📄 README.md                   프로젝트 메인 문서
├── 📄 SETUP_GUIDE.md              PyCharm + uv 설정 가이드
├── 📄 QUICK_START.md              5분 빠른 시작 가이드
├── 📄 JENKINS_SETUP.md            Jenkins 상세 설정 가이드
├── 📄 DAILY_REPORT_README.md      전체 시스템 문서
├── 📄 Jenkinsfile                 Jenkins 파이프라인 설정
├── 🐍 daily_report.py             메인 실행 스크립트
├── 🐍 test_report.py              테스트 스크립트
├── 📁 src/
│   └── 📁 polybot_reporter/       메인 패키지
│       ├── 📄 __init__.py         패키지 초기화
│       ├── 🐍 retry.py            재시도 및 rate limit 핸들링
│       ├── 📁 api/                API 클라이언트 모듈
│       │   ├── 📄 __init__.py
│       │   └── 🐍 data_api_client.py  Polymarket Data API 클라이언트
│       └── 📁 notifications/      알림 모듈
│           ├── 📄 __init__.py
│           └── 🐍 slack_notifier.py   Slack 메시지 전송
└── 📁 tests/                      테스트 코드 (추후 추가)
    └── 📄 __init__.py
```

## 📋 파일별 상세 설명

### 핵심 설정 파일

#### 1. `pyproject.toml` ⭐⭐⭐⭐⭐
**가장 중요한 파일!**

**역할**:
- Python 프로젝트의 메타데이터 정의
- 의존성 패키지 목록
- 빌드 시스템 설정
- 개발 도구(black, ruff, pytest) 설정

**주요 섹션**:
```toml
[project]
name = "polymarket-daily-reporter"
version = "0.1.0"
dependencies = [
    "py-clob-client>=0.22.3",  # Polymarket API
    "requests>=2.31.0",         # HTTP 요청
    "python-dotenv>=1.0.0",     # 환경변수
    "pyyaml>=6.0.1",            # YAML 파싱
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",   # 테스트
    "black>=23.7.0",   # 포매터
    "ruff>=0.0.285",   # 린터
]
```

**언제 수정하나**:
- 새 패키지를 추가할 때
- 프로젝트 버전을 업데이트할 때
- 개발 도구 설정을 변경할 때

#### 2. `.python-version`
**역할**: Python 버전 고정

**내용**:
```
3.11
```

**효과**:
- uv가 자동으로 Python 3.11 사용
- pyenv와 연동되어 버전 관리
- 팀원 간 Python 버전 통일

#### 3. `.env.example`
**역할**: 환경변수 템플릿

**사용법**:
```bash
cp .env.example .env
nano .env  # 실제 값 입력
```

**포함 내용**:
```bash
ACCOUNT_1_NAME=golden-apple
ACCOUNT_1_ADDRESS=0x...
ACCOUNT_2_NAME=golden-banana
ACCOUNT_2_ADDRESS=0x...
ACCOUNT_3_NAME=golden-cherry
ACCOUNT_3_ADDRESS=0x...
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

**주의**: `.env`는 **절대 Git에 커밋하지 마세요!**

#### 4. `.gitignore`
**역할**: Git에서 제외할 파일 지정

**주요 항목**:
- `__pycache__/`: Python 캐시
- `.venv/`: 가상환경
- `.env`: 환경변수 (민감한 정보)
- `.idea/`: PyCharm 설정
- `*.log`: 로그 파일

### 실행 스크립트

#### 5. `daily_report.py` 🚀
**역할**: 메인 실행 스크립트 (Jenkins에서 실행)

**기능**:
1. 환경변수에서 계좌 설정 로드
2. 각 계좌의 포트폴리오 데이터 조회
3. P&L 계산 (7일, 30일)
4. Slack으로 통합 리포트 전송

**실행**:
```bash
python daily_report.py
```

**필수 환경변수**:
- `ACCOUNT_1_NAME`, `ACCOUNT_1_ADDRESS`
- `ACCOUNT_2_NAME`, `ACCOUNT_2_ADDRESS`
- `ACCOUNT_3_NAME`, `ACCOUNT_3_ADDRESS`
- `SLACK_WEBHOOK_URL`

#### 6. `test_report.py` 🧪
**역할**: 로컬 테스트 스크립트

**기능**:
- Data API 클라이언트 테스트
- Slack 알림 테스트
- Mock 데이터로 동작 확인

**실행**:
```bash
python test_report.py
```

### 소스 코드

#### 7. `src/polybot_reporter/__init__.py`
**역할**: 패키지 초기화 및 public API 정의

**내용**:
```python
__version__ = "0.1.0"
from .api.data_api_client import DataAPIClient
from .notifications.slack_notifier import SlackNotifier
```

**효과**:
```python
# 다음과 같이 import 가능
from polybot_reporter import DataAPIClient, SlackNotifier
```

#### 8. `src/polybot_reporter/api/data_api_client.py`
**역할**: Polymarket Data API 클라이언트

**주요 메서드**:
- `get_positions(address)`: 현재 포지션 조회
- `get_trades_by_address(address)`: 거래 내역 조회
- `calculate_pnl_for_period(address, days_ago)`: P&L 계산
- `get_portfolio_summary(address)`: 전체 포트폴리오 요약

**사용 예**:
```python
from polybot_reporter import DataAPIClient

client = DataAPIClient()
summary = client.get_portfolio_summary("0x...")
print(f"Total Value: ${summary['total_value']:.2f}")
```

#### 9. `src/polybot_reporter/notifications/slack_notifier.py`
**역할**: Slack 메시지 전송

**주요 메서드**:
- `send_portfolio_report(account_name, summary)`: 단일 계좌 리포트
- `send_multi_account_report(reports)`: 통합 리포트
- `send_error_notification(account_name, error)`: 에러 알림

**사용 예**:
```python
from polybot_reporter import SlackNotifier

slack = SlackNotifier(webhook_url="https://...")
slack.send_portfolio_report("golden-apple", summary)
```

#### 10. `src/polybot_reporter/retry.py`
**역할**: API 호출 재시도 및 rate limit 핸들링

**기능**:
- HTTP 429 (Rate Limit) 처리
- HTTP 5xx (Server Error) 재시도
- Exponential backoff with jitter

**사용 예**:
```python
from polybot_reporter.retry import rate_limit_handler

@rate_limit_handler(max_retries=3)
def fetch_data():
    return requests.get("https://api.polymarket.com/...")
```

### Jenkins 설정

#### 11. `Jenkinsfile`
**역할**: Jenkins 파이프라인 정의

**주요 설정**:
- **Trigger**: `cron('0 9 * * *')` - 매일 오전 9시 실행
- **Environment**: Jenkins Credentials에서 환경변수 주입
- **Stages**:
  1. Setup: 의존성 설치
  2. Generate Report: 리포트 생성
  3. Archive Logs: 로그 아카이브

**Jenkins에서 사용**:
- Pipeline script from SCM
- Repository URL 설정
- Script Path: `Jenkinsfile`

### 문서

#### 12. `README.md`
**대상**: 프로젝트 사용자 전체

**내용**:
- 프로젝트 개요
- 빠른 시작
- 주요 기능
- 개발 환경 설정

#### 13. `SETUP_GUIDE.md`
**대상**: PyCharm + uv를 사용하는 개발자

**내용**:
- PyCharm 설정 단계별 가이드
- uv 명령어 모음
- 트러블슈팅
- 각 파일 상세 설명

#### 14. `QUICK_START.md`
**대상**: 빠르게 시작하고 싶은 사용자

**내용**:
- 5분 안에 시작하기
- 필수 단계만 간추림
- 체크리스트

#### 15. `JENKINS_SETUP.md`
**대상**: Jenkins 관리자

**내용**:
- Jenkins Credentials 설정
- Job 생성 방법
- Cron 스케줄 설정
- 모니터링 방법

#### 16. `DAILY_REPORT_README.md`
**대상**: 고급 사용자 / 관리자

**내용**:
- 시스템 아키텍처
- 모듈별 상세 설명
- 커스터마이징 방법
- API 사용법

## 🔄 일반적인 작업 흐름

### 1. 새 프로젝트 시작

```bash
# 프로젝트 클론
cd daily-report

# 환경 설정
cp .env.example .env
nano .env

# 의존성 설치
uv venv
source .venv/bin/activate
uv pip install -e .

# 테스트
python test_report.py
```

### 2. 개발 작업

```bash
# 가상환경 활성화
source .venv/bin/activate

# 코드 수정
nano src/polybot_reporter/api/data_api_client.py

# 포맷팅
black src/

# 린팅
ruff check src/

# 테스트
pytest
```

### 3. 새 패키지 추가

```bash
# 1. pyproject.toml 수정
nano pyproject.toml

# dependencies 섹션에 추가:
# "pandas>=2.0.0",

# 2. 설치
uv pip install -e .

# 3. 확인
uv pip list | grep pandas
```

### 4. Git 커밋

```bash
# .env는 자동으로 제외됨 (.gitignore)
git add .
git commit -m "Add new feature"
git push
```

## 📊 파일 중요도

| 파일 | 중요도 | 수정 빈도 | 설명 |
|------|--------|----------|------|
| `pyproject.toml` | ⭐⭐⭐⭐⭐ | 중간 | 의존성 추가 시 |
| `.env` | ⭐⭐⭐⭐⭐ | 낮음 | 계좌 변경 시 |
| `daily_report.py` | ⭐⭐⭐⭐ | 낮음 | 로직 변경 시 |
| `data_api_client.py` | ⭐⭐⭐⭐ | 중간 | API 변경 시 |
| `slack_notifier.py` | ⭐⭐⭐ | 낮음 | 알림 포맷 변경 시 |
| `.gitignore` | ⭐⭐⭐ | 낮음 | 제외 파일 추가 시 |
| `README.md` | ⭐⭐⭐ | 중간 | 문서 업데이트 시 |
| `Jenkinsfile` | ⭐⭐⭐ | 낮음 | 스케줄 변경 시 |

## 💡 Best Practices

1. **절대 커밋하지 말 것**:
   - `.env` (환경변수)
   - `*.log` (로그 파일)
   - `.idea/` (PyCharm 설정)

2. **항상 가상환경 사용**:
   ```bash
   source .venv/bin/activate
   ```

3. **의존성 추가 시**:
   - `pyproject.toml`에 버전 범위 지정
   - `uv pip install -e .`로 설치

4. **코드 푸시 전**:
   ```bash
   black src/
   ruff check src/
   pytest
   ```

---

**작성일**: 2026-02-07
**버전**: 1.0.0
