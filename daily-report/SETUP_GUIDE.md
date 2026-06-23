# PyCharm + uv 프로젝트 설정 가이드

> Supabase 일일 적재와 최신 Jenkins 설정은 [README.md](README.md)를 기준으로 합니다.

이 문서는 `daily-report` 프로젝트를 PyCharm에서 uv를 사용하여 설정하는 방법을 설명합니다.

## 📋 생성된 파일 설명

### 1. `pyproject.toml` ⭐ 가장 중요
**역할**: 프로젝트의 메타데이터와 의존성을 정의하는 Python 프로젝트 설정 파일

**주요 섹션**:
- `[project]`: 프로젝트 이름, 버전, 설명, 작성자 정보
- `dependencies`: 실행에 필요한 패키지 목록
  - `py-clob-client`: Polymarket API 클라이언트
  - `requests`: HTTP 요청
  - `python-dotenv`: 환경변수 관리
  - `pyyaml`: YAML 파일 파싱
- `[project.optional-dependencies]`: 개발용 패키지 (테스트, 린팅 등)
- `[tool.black]`, `[tool.ruff]`, `[tool.pytest]`: 코드 품질 도구 설정

**왜 필요한가**:
- uv가 이 파일을 읽어 자동으로 의존성을 설치합니다
- PyCharm이 프로젝트 구조를 인식합니다
- 다른 개발자가 동일한 환경을 재현할 수 있습니다

### 2. `.python-version`
**역할**: 프로젝트에서 사용할 Python 버전 지정

**내용**: `3.11`

**왜 필요한가**:
- uv와 pyenv가 자동으로 올바른 Python 버전을 사용합니다
- 팀원 간 Python 버전 불일치 방지

### 3. `.env.example`
**역할**: 환경변수 템플릿 파일

**사용법**:
```bash
cp .env.example .env
nano .env  # 실제 값 입력
```

**왜 필요한가**:
- 민감한 정보(Wallet Address, Webhook URL)를 코드에서 분리
- 새로운 팀원이 필요한 환경변수를 쉽게 파악
- `.env`는 `.gitignore`에 포함되어 Git에 커밋되지 않음

### 4. `.gitignore`
**역할**: Git에서 추적하지 않을 파일/폴더 지정

**포함 항목**:
- `__pycache__/`, `*.pyc`: Python 캐시 파일
- `.venv/`, `.env`: 가상환경과 환경변수 파일
- `.idea/`: PyCharm 설정 폴더
- `*.log`: 로그 파일

**왜 필요한가**:
- 불필요한 파일이 Git에 커밋되는 것 방지
- 팀원 간 충돌 방지
- 민감한 정보 보호

### 5. `README.md`
**역할**: 프로젝트 문서화

**포함 내용**:
- 프로젝트 설명
- 설치 방법
- 사용법
- 개발 가이드

**왜 필요한가**:
- 프로젝트 이해도 향상
- 새로운 기여자를 위한 진입 장벽 낮춤

### 6. `src/polybot_reporter/` 구조
**역할**: 실제 소스 코드가 위치하는 패키지

```
src/polybot_reporter/
├── __init__.py           # 패키지 초기화, 버전 정보
├── api/
│   ├── __init__.py
│   └── data_api_client.py   # Polymarket Data API 클라이언트
├── notifications/
│   ├── __init__.py
│   └── slack_notifier.py    # Slack 알림 모듈
└── retry.py              # 재시도 로직
```

**왜 src/ 구조를 사용하는가**:
- 테스트 격리: `import polybot_reporter`가 설치된 패키지를 참조하도록 강제
- 명확한 구조: 소스 코드와 설정 파일 분리
- Best Practice: Python 커뮤니티 표준

## 🚀 PyCharm 설정 단계

### 1단계: 프로젝트 열기

```bash
# PyCharm에서
File → Open → daily-report 폴더 선택
```

### 2단계: uv 가상환경 생성

**터미널에서**:
```bash
cd daily-report

# uv 설치 (설치 안 된 경우)
# Mac/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows:
# powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 가상환경 생성
uv venv

# 활성화
source .venv/bin/activate  # Mac/Linux
# .venv\Scripts\activate    # Windows

# 의존성 설치
uv pip install -e .
```

### 3단계: PyCharm Interpreter 설정

1. **Settings 열기**:
   - Mac: `PyCharm` → `Settings` (또는 `Cmd + ,`)
   - Windows: `File` → `Settings` (또는 `Ctrl + Alt + S`)

2. **Python Interpreter 설정**:
   ```
   Project: daily-report → Python Interpreter
   → Add Interpreter → Add Local Interpreter
   → Existing environment
   → Interpreter: daily-report/.venv/bin/python 선택
   → OK
   ```

3. **확인**:
   - Interpreter 리스트에 설치된 패키지가 보여야 함
   - `py-clob-client`, `requests` 등 확인

### 4단계: 환경변수 설정

**방법 1: .env 파일 (권장)**

```bash
cp .env.example .env
nano .env  # 실제 값 입력
```

**방법 2: PyCharm Run Configuration**

1. `Run` → `Edit Configurations`
2. `+` → `Python`
3. 설정:
   - Name: `Daily Report`
   - Script path: `daily_report.py` 선택
   - Environment variables:
     ```
     ACCOUNT_1_NAME=golden-apple;ACCOUNT_1_ADDRESS=0x...;SLACK_WEBHOOK_URL=https://...
     ```
     (Windows는 `;` 대신 개행 또는 `,` 사용)

### 5단계: 실행

**터미널에서**:
```bash
# 테스트
python test_report.py

# 실제 리포트
python daily_report.py
```

**PyCharm에서**:
- `Run` → `Run 'Daily Report'`
- 또는 `Shift + F10`

## 🔧 uv 주요 명령어

### 패키지 관리

```bash
# 패키지 설치
uv pip install requests

# 개발 의존성 포함 설치
uv pip install -e ".[dev]"

# 패키지 제거
uv pip uninstall requests

# 설치된 패키지 목록
uv pip list

# 의존성 업데이트
uv pip install --upgrade requests
```

### 프로젝트 관리

```bash
# 현재 프로젝트를 editable 모드로 설치
uv pip install -e .

# 의존성 동기화 (pyproject.toml 기준)
uv pip sync

# 가상환경 재생성
rm -rf .venv
uv venv
uv pip install -e .
```

## 📦 패키지 추가 방법

### 1. pyproject.toml 수정

```toml
dependencies = [
    "py-clob-client>=0.22.3",
    "requests>=2.31.0",
    "python-dotenv>=1.0.0",
    "pyyaml>=6.0.1",
    "pandas>=2.0.0",  # 새로 추가
]
```

### 2. 설치

```bash
uv pip install -e .
```

### 3. PyCharm에서 인식

- PyCharm이 자동으로 새 패키지를 인식
- 안 되면 `File` → `Invalidate Caches` → `Invalidate and Restart`

## 🧪 테스트 실행

```bash
# pytest 설치 (dev 의존성)
uv pip install -e ".[dev]"

# 테스트 실행
pytest

# 커버리지와 함께
pytest --cov=polybot_reporter --cov-report=html

# 특정 테스트만
pytest tests/test_data_api.py::test_get_positions
```

## 🎨 코드 품질 도구

### Black (코드 포매터)

```bash
# 포매팅 실행
black src/ tests/

# 확인만 (변경 안 함)
black --check src/
```

**PyCharm 통합**:
```
Settings → Tools → Black
→ Enable Black: 체크
→ On save: 체크 (선택사항)
```

### Ruff (린터)

```bash
# 린팅 실행
ruff check src/ tests/

# 자동 수정
ruff check --fix src/
```

### mypy (타입 체커)

```bash
# 타입 체킹
mypy src/
```

## 🐛 트러블슈팅

### 문제 1: "uv: command not found"

**해결**:
```bash
# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh

# PATH 추가
export PATH="$HOME/.cargo/bin:$PATH"
```

### 문제 2: "ModuleNotFoundError: No module named 'polybot_reporter'"

**해결**:
```bash
# editable 모드로 재설치
uv pip install -e .

# PyCharm Interpreter 재설정
Settings → Python Interpreter → Show All → Remove → Add
```

### 문제 3: PyCharm에서 import가 빨간 줄

**해결**:
1. `File` → `Invalidate Caches` → `Invalidate and Restart`
2. `src` 폴더 우클릭 → `Mark Directory as` → `Sources Root`

### 문제 4: 가상환경 활성화 안 됨

**확인**:
```bash
which python
# 출력: /path/to/daily-report/.venv/bin/python 이어야 함
```

**해결**:
```bash
# 수동 활성화
source .venv/bin/activate
```

## 📚 추가 리소스

- [uv 공식 문서](https://github.com/astral-sh/uv)
- [PyCharm 가이드](https://www.jetbrains.com/help/pycharm/)
- [Python 패키징 가이드](https://packaging.python.org/)
- [pyproject.toml 명세](https://peps.python.org/pep-0621/)

## 💡 팁

1. **자주 사용하는 명령어를 alias로**:
   ```bash
   alias uvinstall='uv pip install -e .'
   alias uvtest='pytest'
   alias uvfmt='black src/ tests/ && ruff check --fix src/ tests/'
   ```

2. **pre-commit 훅 사용**:
   ```bash
   uv pip install pre-commit
   pre-commit install
   ```

3. **PyCharm Run Configuration 저장**:
   - 설정한 Run Configuration을 `.idea/runConfigurations`에 저장하여 팀과 공유 가능

---

**설정 완료 시간**: 약 10분
**난이도**: ⭐⭐☆☆☆ (초급~중급)
