# Polymarket Daily Portfolio Reporter

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/managed%20by-uv-green.svg)](https://github.com/astral-sh/uv)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

3개의 Polymarket 매매봇 계좌(golden-apple, golden-banana, golden-cherry)에 대한 일일 포트폴리오 리포트를 자동으로 생성하고 Slack으로 전송하는 시스템입니다.

## 🚀 빠른 시작

### 1. 프로젝트 클론 및 설정

```bash
# 프로젝트 디렉토리로 이동
cd daily-report

# uv로 가상환경 생성 및 의존성 설치
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e .
```

### 2. 환경 변수 설정

```bash
# .env 파일 생성
cp .env.example .env

# .env 파일 수정하여 실제 값 입력
nano .env  # 또는 원하는 에디터 사용
```

### 3. 실행

```bash
# 테스트 실행
python test_report.py

# 실제 리포트 생성
python daily_report.py
```

## 📦 프로젝트 구조

```
daily-report/
├── pyproject.toml              # 프로젝트 메타데이터 및 의존성
├── .python-version             # Python 버전 지정
├── .env.example                # 환경변수 템플릿
├── .gitignore                  # Git 제외 파일
├── README.md                   # 프로젝트 문서 (이 파일)
├── QUICK_START.md              # 빠른 시작 가이드
├── JENKINS_SETUP.md            # Jenkins 설정 가이드
├── daily_report.py             # 메인 실행 스크립트
├── test_report.py              # 테스트 스크립트
├── Jenkinsfile                 # Jenkins 파이프라인
└── src/
    └── polybot_reporter/
        ├── __init__.py
        ├── api/
        │   ├── __init__.py
        │   ├── data_api_client.py    # Data API 클라이언트
        │   └── gamma_client.py        # Gamma API 클라이언트
        ├── notifications/
        │   ├── __init__.py
        │   └── slack_notifier.py      # Slack 알림
        └── utils/
            ├── __init__.py
            └── logger.py              # 로깅 유틸리티
```

## 🔧 개발 환경 설정

### PyCharm 설정

1. **프로젝트 열기**: `daily-report` 폴더를 PyCharm으로 열기
2. **Python Interpreter 설정**:
   - `Settings` → `Project: daily-report` → `Python Interpreter`
   - `Add Interpreter` → `Add Local Interpreter`
   - Existing environment: `.venv/bin/python` 선택
3. **실행 구성**:
   - `Run` → `Edit Configurations`
   - `+` 클릭 → `Python`
   - Script path: `daily_report.py` 선택
   - Environment variables에 `.env` 파일 내용 입력

### uv 명령어

```bash
# 의존성 설치
uv pip install -e .

# 개발 의존성 포함 설치
uv pip install -e ".[dev]"

# 특정 패키지 추가
uv pip install requests

# 의존성 업데이트
uv pip install --upgrade -e .

# 가상환경 활성화
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows
```

## 🧪 테스트

```bash
# 모든 테스트 실행
pytest

# 커버리지와 함께 실행
pytest --cov=polybot_reporter --cov-report=html

# 특정 테스트만 실행
pytest tests/test_data_api.py
```

## 🎨 코드 품질

```bash
# 코드 포맷팅 (Black)
black src/ tests/

# 린팅 (Ruff)
ruff check src/ tests/

# 타입 체킹 (mypy)
mypy src/
```

## 📋 주요 기능

- ✅ 다중 계좌 지원 (최대 9개)
- ✅ 7일/30일 P&L 자동 계산
- ✅ Slack 자동 알림
- ✅ Jenkins 자동화
- ✅ 에러 핸들링 및 재시도
- ✅ 상세 로깅

## 🔐 보안

⚠️ **중요**: Private Key는 필요하지 않습니다!

- 이 시스템은 읽기 전용입니다
- Wallet Address (funder address)만 필요
- `.env` 파일은 절대 Git에 커밋하지 마세요

## 📚 문서

- [빠른 시작 가이드](QUICK_START.md)
- [Jenkins 설정 가이드](JENKINS_SETUP.md)
- [전체 문서](DAILY_REPORT_README.md)
- [Polymarket API 문서](https://docs.polymarket.com/)

## 🐛 트러블슈팅

### "Module not found" 오류

```bash
# 가상환경 활성화 확인
which python  # .venv/bin/python 이어야 함

# 재설치
uv pip install -e .
```

### Slack 메시지가 오지 않음

```bash
# Webhook 테스트
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Test"}' $SLACK_WEBHOOK_URL
```

## 🤝 기여

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 라이선스

MIT License - 자세한 내용은 [LICENSE](LICENSE) 파일을 참고하세요.

## 👤 작성자

**jongwoo**
- Email: izowooi@hotmail.com

## 🙏 감사의 말

- [Polymarket](https://polymarket.com/) - API 제공
- [uv](https://github.com/astral-sh/uv) - 빠른 Python 패키지 관리

---

**Last Updated**: 2026-02-07
