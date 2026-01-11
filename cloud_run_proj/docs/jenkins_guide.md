# 🚀 Jenkins CI/CD 배포 가이드

Jenkins 환경에서 StockBot을 자동화하여 실행하는 방법을 안내합니다.

## 📋 Jenkins 환경 준비

### 1. 파이썬 3.12 설치
```bash
# Homebrew로 파이썬 3.12 설치 (macOS Jenkins에서)
brew install python@3.12

# 또는 pyenv로 설치
brew install pyenv
pyenv install 3.12.0
pyenv global 3.12.0
```

### 2. uv 설치
```bash
# uv 설치 (Jenkins agent에서)
curl -LsSf https://astral.sh/uv/install.sh | sh

# PATH에 추가 (.zshrc 또는 .bashrc에)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### 3. Jenkins Agent 설정
Jenkins 설정 → 노드 관리 → 해당 노드 설정에서:
```
환경변수:
PATH=/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH
```

## 🔧 Jenkins 파이프라인 스크립트

### 방법 1: Jenkinsfile (선호)
```groovy
pipeline {
    agent { label 'macos' }

    environment {
        DOTENV_PATH = "${WORKSPACE}/.env"
        TICKER_LIST = 'AAPL,GOOGL,MSFT'
        TEST_BUY_SIGNAL = 'false'
        IS_YAHOO = 'true'
    }

    stages {
        stage('Setup Environment') {
            steps {
                script {
                    // uv 설치 확인 및 설치
                    sh '''
                        if ! command -v uv &> /dev/null; then
                            echo "📦 Installing uv..."
                            curl -LsSf https://astral.sh/uv/install.sh | sh
                            export PATH="$HOME/.local/bin:$PATH"
                        fi
                    '''
                }
            }
        }

        stage('Install Dependencies') {
            steps {
                sh '''
                    echo "📦 Installing Python dependencies..."
                    uv sync
                '''
            }
        }

        stage('Setup .env') {
            steps {
                script {
                    // .env 파일 생성
                    writeFile file: '.env', text: '''
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-key
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
EMAIL_FROM=your-email@gmail.com
EMAIL_TO=your-email@gmail.com
TICKERS=AAPL,GOOGL,MSFT,AMZN
'''
                }
            }
        }

        stage('Data Ingestion') {
            steps {
                sh '''
                    echo "📊 Starting data ingestion..."
                    uv run python main.py ingest --tickers ${TICKER_LIST}
                '''
            }
        }

        stage('Signal Detection') {
            steps {
                script {
                    if (env.TEST_BUY_SIGNAL == 'true') {
                        sh '''
                            echo "🔍 Running signal detection (dry-run)..."
                            uv run python main.py signals --dry-run
                        '''
                    } else {
                        sh '''
                            echo "🚨 Running signal detection..."
                            uv run python main.py signals
                        '''
                    }
                }
            }
        }
    }

    post {
        always {
            script {
                // 로그 정리
                sh '''
                    echo "🧹 Cleaning up..."
                    # 필요한 정리 작업
                '''
            }
        }
        success {
            echo '✅ StockBot execution completed successfully!'
        }
        failure {
            echo '❌ StockBot execution failed!'
            // 알림 발송 로직 추가 가능
        }
    }
}
```

### 방법 2: 쉘 스크립트 (Jenkins 빌드 단계에서 실행)
```bash
#!/bin/bash

# 환경변수 설정
export DOTENV_PATH="${WORKSPACE}/.env"
export TICKER_LIST="${TICKER_LIST:-AAPL,GOOGL,MSFT}"
export TEST_BUY_SIGNAL="${TEST_BUY_SIGNAL:-false}"
export IS_YAHOO="${IS_YAHOO:-true}"

# 🔍 디버깅: 현재 Python 환경 확인
echo "🐍 Current Python: $(which python3)"
echo "🐍 Python version: $(python3 --version)"
echo "🐍 Python path: $(python3 -c 'import sys; print(sys.executable)')"

# uv 설치 확인
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 의존성 설치
echo "📦 Installing dependencies..."
uv sync

# 🔍 디버깅: uv Python 환경 확인
echo "🔧 uv Python: $(uv run which python)"
echo "🔧 uv Python version: $(uv run python --version)"

# .env 파일 생성 (Jenkins Credentials 사용 권장)
echo "📝 Creating .env file..."
cat > .env << EOF
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USER=${SMTP_USER}
SMTP_PASS=${SMTP_PASS}
EMAIL_FROM=${EMAIL_FROM}
EMAIL_TO=${EMAIL_TO}
TICKERS=${TICKER_LIST}
EOF

# ✅ 중요: uv run을 사용하여 올바른 Python 환경에서 실행
echo "📊 Starting data ingestion..."
uv run python main.py ingest --tickers ${TICKER_LIST}

# 신호 감지
if [ "${TEST_BUY_SIGNAL}" = "true" ]; then
    echo "🔍 Running signal detection (dry-run)..."
    uv run python main.py signals --dry-run
else
    echo "🚨 Running signal detection..."
    uv run python main.py signals
fi

echo "✅ StockBot execution completed!"
```

## 🔐 Jenkins Credentials 설정

Jenkins에서 민감한 정보를 안전하게 관리하려면:

1. **Credentials 생성:**
   - Jenkins 대시보드 → Credentials → System → Global credentials
   - 각 API 키들을 Secret text로 저장

2. **환경변수로 사용:**
```groovy
environment {
    SUPABASE_URL = credentials('supabase-url')
    SUPABASE_SERVICE_ROLE_KEY = credentials('supabase-service-key')
    TELEGRAM_BOT_TOKEN = credentials('telegram-bot-token')
    TELEGRAM_CHAT_ID = credentials('telegram-chat-id')
    SMTP_PASS = credentials('smtp-password')
}
```

## ⏰ 스케줄링 설정

### 크론 표현식 예시:
```groovy
triggers {
    // 매일 평일 오전 9시 데이터 수집
    cron('0 9 * * 1-5')

    // 매일 평일 오후 4시 신호 감지
    cron('0 16 * * 1-5')
}
```

## 📊 모니터링 및 알림

### Slack 알림 추가:
```groovy
post {
    success {
        slackSend channel: '#trading-alerts',
                   color: 'good',
                   message: "✅ StockBot 실행 완료 - ${env.BUILD_URL}"
    }
    failure {
        slackSend channel: '#trading-alerts',
                   color: 'danger',
                   message: "❌ StockBot 실행 실패 - ${env.BUILD_URL}"
    }
}
```

## 🚀 고급 설정

### Docker 컨테이너 사용:
```dockerfile
FROM python:3.12-slim

# uv 설치
COPY --from=ghcr.io/astral-sh/uv/latest /uv /bin/uv

# 작업 디렉토리 설정
WORKDIR /app

# 프로젝트 파일 복사
COPY pyproject.toml ./
COPY stockbot/ ./stockbot/
COPY main.py ./

# 의존성 설치
RUN uv sync --frozen --no-install-project

# 기본 명령어
CMD ["uv", "run", "python", "main.py"]
```

## 🔧 과거 conda 방식 vs 새로운 uv 방식 비교

| 항목 | 과거 (conda) | 현재 (uv) |
|------|-------------|-----------|
| 패키지 관리 | `conda activate trend_follower` | `uv sync` |
| 실행 | `python main.py` | `uv run python main.py` |
| 속도 | 느림 | 매우 빠름 |
| 재현성 | 환경 파일 필요 | pyproject.toml 기반 |
| 크기 | 무거움 | 가벼움 |

### 마이그레이션 예시:
```bash
# 과거 방식
conda activate trend_follower
python main.py ingest --tickers AAPL,GOOGL
conda deactivate

# 새로운 방식
uv sync
uv run python main.py ingest --tickers AAPL,GOOGL
```

## 💡 추가 팁

1. **DOTENV_PATH 처리**: Jenkins에서는 환경변수로 `.env` 경로를 지정하는 대신, 빌드 단계에서 직접 파일을 생성하는 것이 좋습니다.

2. **에러 처리**: 파이프라인에서 실패 시 Slack이나 이메일로 알림을 받도록 설정하세요.

3. **로그 관리**: Jenkins에서 로그를 확인하고, 필요한 경우 로그 파일을 별도로 저장하세요.

4. **성능 최적화**: uv는 매우 빠르므로, 의존성 설치 시간을 크게 단축할 수 있습니다.

## 🚨 ModuleNotFoundError 문제 해결

Jenkins에서 `ModuleNotFoundError: No module named 'dotenv'`가 발생한다면:

### ✅ 즉시 해결 방법

**스크립트에서 `uv run` 사용하기:**
```bash
# ❌ 잘못된 방법
python main.py ingest --tickers AAPL

# ✅ 올바른 방법
uv run python main.py ingest --tickers AAPL
```

### 🔍 문제 진단

Jenkins 콘솔 로그에서 다음 정보를 확인하세요:

```bash
# 현재 Jenkins가 사용하는 Python
echo "🐍 Current Python: $(which python3)"
echo "🐍 Python version: $(python3 --version)"
echo "🐍 Python path: $(python3 -c 'import sys; print(sys.executable)')"

# uv Python 환경
echo "🔧 uv Python: $(uv run which python)"
echo "🔧 uv Python version: $(uv run python --version)"
```

### 🛠️ 추가 해결 방법

#### 1. Jenkins Node 설정에서 PATH 수정
Jenkins → 노드 관리 → 해당 노드 → 설정
```
환경변수:
PATH=/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH
```

#### 2. Jenkinsfile에서 명시적 Python 경로 지정
```groovy
environment {
    PYTHONPATH = '/opt/homebrew/bin/python3.12'
    PATH = "/opt/homebrew/bin:$HOME/.local/bin:$PATH"
}
```

#### 3. uv 환경 강제 사용
```bash
# Jenkins 스크립트에서
export PATH="$HOME/.local/bin:$PATH"
uv run python main.py ingest --tickers AAPL
```

### 💡 핵심 원인

Jenkins는 기본적으로 시스템 Python을 사용합니다. uv로 설치한 패키지들은 uv 환경에만 존재하므로, **항상 `uv run`을 사용**해야 합니다.

이 가이드를 따라 Jenkins에서 StockBot을 성공적으로 배포해보세요! 🚀
