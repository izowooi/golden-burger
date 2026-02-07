# Jenkins 설정 가이드

Polymarket 일일 리포트 봇을 Jenkins에서 실행하기 위한 설정 가이드입니다.

## 1. Jenkins Credentials 설정

Jenkins에서 `Manage Jenkins` → `Manage Credentials` → `(global)` → `Add Credentials`로 이동하여 다음 credential들을 추가하세요.

### 필수 Credentials

| ID | Type | Description | 예시 값 |
|----|------|-------------|---------|
| `polymarket-golden-apple-address` | Secret text | golden-apple 계좌의 funder address | `0x1234...abcd` |
| `polymarket-golden-banana-address` | Secret text | golden-banana 계좌의 funder address | `0x5678...efgh` |
| `polymarket-golden-cherry-address` | Secret text | golden-cherry 계좌의 funder address | `0x9abc...ijkl` |
| `polymarket-slack-webhook` | Secret text | Slack Webhook URL | `https://hooks.slack.com/services/...` |

### Credential 추가 방법

1. **Kind**: Secret text 선택
2. **Scope**: Global (Jenkins, nodes, items, all child items, etc) 선택
3. **Secret**: 실제 값 입력 (예: Wallet Address)
4. **ID**: 위 표의 ID 값 정확히 입력
5. **Description**: 설명 입력 (선택사항)

## 2. Jenkins Job 생성

### Pipeline Job 생성

1. Jenkins 메인 화면에서 `New Item` 클릭
2. Job 이름: `polymarket-daily-report` (원하는 이름)
3. Type: `Pipeline` 선택
4. `OK` 클릭

### Pipeline 설정

**General 탭:**
- ✅ `Discard old builds`
  - Strategy: Log Rotation
  - Max # of builds to keep: 30

**Build Triggers 탭:**
- ✅ `Build periodically`
  - Schedule: `0 9 * * *` (매일 오전 9시 실행)
  - 또는 원하는 cron 표현식 입력

**Pipeline 탭:**
- Definition: `Pipeline script from SCM` 또는 `Pipeline script` 선택

**Option 1: Pipeline script from SCM (권장)**
- SCM: Git
- Repository URL: 프로젝트 Git 저장소 URL
- Branch: `*/main` (또는 사용 중인 브랜치)
- Script Path: `Jenkinsfile`

**Option 2: Pipeline script (직접 입력)**
- Script: Jenkinsfile 내용을 복사하여 붙여넣기

## 3. Slack Webhook URL 생성

### Slack App 생성

1. [Slack API](https://api.slack.com/apps) 접속
2. `Create New App` 클릭
3. `From scratch` 선택
4. App Name: `Polymarket Reporter` (원하는 이름)
5. Workspace 선택

### Incoming Webhook 활성화

1. `Incoming Webhooks` 메뉴 선택
2. `Activate Incoming Webhooks` 토글을 ON으로 변경
3. 하단의 `Add New Webhook to Workspace` 클릭
4. 메시지를 받을 채널 선택 (예: `#polymarket-reports`)
5. `Allow` 클릭

### Webhook URL 복사

1. 생성된 Webhook URL 복사 (형식: `https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX`)
2. Jenkins Credentials에 `polymarket-slack-webhook`으로 저장

## 4. 디렉토리 구조 확인

Jenkins 워크스페이스에 다음 구조가 필요합니다:

```
workspace/
├── daily_report.py          # 메인 실행 스크립트
├── Jenkinsfile              # Jenkins 파이프라인 설정
├── golden-apple/
│   └── src/
│       └── polybot/
│           ├── api/
│           │   └── data_api_client.py
│           └── notifications/
│               └── slack_notifier.py
├── golden-banana/           # (동일 구조)
└── golden-cherry/           # (동일 구조)
```

## 5. 로컬 테스트

Jenkins에 배포하기 전에 로컬에서 테스트하세요:

### 환경 변수 설정

`.env` 파일 생성:

```bash
# Account addresses
ACCOUNT_1_NAME=golden-apple
ACCOUNT_1_ADDRESS=0x1234567890abcdef1234567890abcdef12345678

ACCOUNT_2_NAME=golden-banana
ACCOUNT_2_ADDRESS=0xabcdefabcdefabcdefabcdefabcdefabcdefabcd

ACCOUNT_3_NAME=golden-cherry
ACCOUNT_3_ADDRESS=0x9876543210fedcba9876543210fedcba98765432

# Slack webhook
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### 로컬 실행

```bash
# 환경 변수 로드
source .env  # 또는 export 명령어로 각각 설정

# 스크립트 실행
python3 daily_report.py
```

## 6. Cron 스케줄 예시

| 스케줄 | Cron 표현식 | 설명 |
|--------|------------|------|
| 매일 오전 9시 | `0 9 * * *` | 권장 |
| 매일 자정 | `0 0 * * *` | |
| 매일 오전 9시, 오후 6시 | `0 9,18 * * *` | 하루 2회 |
| 평일 오전 9시 | `0 9 * * 1-5` | 주말 제외 |
| 매시간 | `0 * * * *` | 테스트용 |

## 7. 트러블슈팅

### 문제: "Permission denied" 오류

**해결책:**
```bash
chmod +x daily_report.py
```

### 문제: Python 모듈을 찾을 수 없음

**해결책:**
- Jenkins에서 Python 가상환경을 사용하거나
- 필요한 패키지를 시스템 전역에 설치:
```bash
pip3 install --user py-clob-client requests pyyaml python-dotenv
```

### 문제: Slack 메시지가 전송되지 않음

**확인 사항:**
1. Webhook URL이 정확한지 확인
2. Slack App이 채널에 추가되었는지 확인
3. 네트워크 방화벽에서 `hooks.slack.com` 접근이 허용되는지 확인

### 문제: API 호출 실패 (Rate Limit)

**해결책:**
- 스크립트에 이미 retry 로직이 포함되어 있음
- 필요시 API 호출 간격을 늘리기 위해 `time.sleep()` 추가

## 8. 모니터링

### Jenkins Console Output 확인

1. Job 실행 후 `Console Output` 클릭
2. 로그에서 다음 정보 확인:
   - 각 계좌의 포지션 수
   - 총 포트폴리오 가치
   - 7일/30일 P&L

### Slack 채널 확인

- 매일 정해진 시간에 리포트 메시지가 도착하는지 확인
- 메시지에 모든 계좌 정보가 포함되어 있는지 확인

### 로그 아카이브

- Jenkins는 자동으로 `daily_report_*.log` 파일을 아카이브합니다
- Build 페이지에서 `Artifacts` 탭에서 다운로드 가능

## 9. 추가 계좌 설정

4번째 이상의 계좌를 추가하려면:

### Jenkins Credentials 추가

```
ID: polymarket-account-4-address
Secret: 0x... (wallet address)
```

### Jenkinsfile 수정

`environment` 섹션에 추가:
```groovy
ACCOUNT_4_NAME = 'golden-dragonfruit'
ACCOUNT_4_ADDRESS = credentials('polymarket-account-4-address')
```

스크립트는 자동으로 `ACCOUNT_*` 환경변수를 감지하여 처리합니다.

## 10. 보안 고려사항

### ⚠️ 중요: Private Key는 절대 저장하지 마세요

- 이 리포트 시스템은 **읽기 전용**입니다
- Data API는 public API이므로 **Private Key가 필요하지 않습니다**
- Wallet Address (funder address)만 필요합니다
- Private Key가 필요한 경우 별도의 보안 저장소(Vault, AWS Secrets Manager 등) 사용

### Credential 관리

- Jenkins Credentials는 암호화되어 저장됩니다
- 프로젝트 구성에서만 ID로 참조되며, 값은 노출되지 않습니다
- Console Output에도 마스킹되어 표시됩니다

---

**문제가 발생하면 로그를 확인하고, 필요시 Jenkins 관리자에게 문의하세요.**
