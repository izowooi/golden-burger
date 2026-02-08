# 빠른 시작 가이드

Polymarket 일일 리포트 시스템을 빠르게 설정하고 실행하는 방법입니다.

## ⚡ 5분 안에 시작하기

### 1단계: Slack Webhook URL 생성 (2분)

1. [Slack API](https://api.slack.com/apps) 접속
2. "Create New App" → "From scratch"
3. App 이름: `Polymarket Reporter`
4. Workspace 선택
5. "Incoming Webhooks" → "Activate" 토글 ON
6. "Add New Webhook to Workspace" 클릭
7. 채널 선택 (예: `#polymarket-reports`)
8. Webhook URL 복사 (`https://hooks.slack.com/services/...`)
```json
{
    "display_information": {
        "name": "Polymarket Reporter",
        "description": "Daily portfolio reports for Polymarket trading accounts",
        "background_color": "#1a1d29"
    },
    "features": {
        "bot_user": {
            "display_name": "Polymarket Reporter",
            "always_online": true
        }
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "incoming-webhook",
                "chat:write",
                "chat:write.public"
            ]
        }
    },
    "settings": {
        "org_deploy_enabled": false,
        "socket_mode_enabled": false,
        "token_rotation_enabled": false
    }
}
```


### 2단계: 로컬 테스트 (2분)

```bash
# 환경 변수 설정
export ACCOUNT_1_NAME=golden-apple
export ACCOUNT_1_ADDRESS=<YOUR_WALLET_ADDRESS>
export SLACK_WEBHOOK_URL=<YOUR_WEBHOOK_URL>

# 테스트 실행
python3 test_report.py
```

✅ Slack 채널에 테스트 메시지가 도착하면 성공!

### 3단계: 실제 리포트 실행 (1분)

```bash
# 3개 계좌 모두 설정
export ACCOUNT_2_NAME=golden-banana
export ACCOUNT_2_ADDRESS=<WALLET_ADDRESS_2>
export ACCOUNT_3_NAME=golden-cherry
export ACCOUNT_3_ADDRESS=<WALLET_ADDRESS_3>

# 리포트 생성
python3 daily_report.py
```

## 🚀 Jenkins 자동화 설정

### 필수 사전 준비

- Jenkins 서버 접근 권한
- Wallet Address 3개 (각 계좌의 funder address)
- Slack Webhook URL

### Jenkins 설정 (5분)

1. **Credentials 등록** (Jenkins 관리 → Credentials)

   ```
   ID: polymarket-golden-apple-address
   Secret: 0x1234567890abcdef1234567890abcdef12345678

   ID: polymarket-golden-banana-address
   Secret: 0xabcdefabcdefabcdefabcdefabcdefabcdefabcd

   ID: polymarket-golden-cherry-address
   Secret: 0x9876543210fedcba9876543210fedcba98765432

   ID: polymarket-slack-webhook
   Secret: https://hooks.slack.com/services/...
   ```

2. **Jenkins Job 생성**

   - New Item → Pipeline
   - Name: `polymarket-daily-report`
   - Pipeline script from SCM
   - Repository URL 입력
   - Script Path: `Jenkinsfile`
   - Build Triggers: `Build periodically` → `0 9 * * *`

3. **첫 실행 테스트**

   - "Build Now" 클릭
   - Console Output 확인
   - Slack 채널 확인

✅ 이제 매일 자동으로 리포트가 생성됩니다!

## 📋 체크리스트

설정이 올바른지 확인하세요:

- [ ] Slack Webhook URL을 생성했고, 테스트 메시지가 전송됨
- [ ] 3개 계좌의 Wallet Address를 확보함
- [ ] `test_report.py` 실행이 성공함
- [ ] Jenkins Credentials를 모두 등록함
- [ ] Jenkins Job이 생성되고 첫 빌드가 성공함
- [ ] Slack 채널에 일일 리포트가 도착함

## 🔧 간단한 커스터마이징

### 리포트 시간 변경

`Jenkinsfile`에서:

```groovy
cron('0 9 * * *')  // 매일 오전 9시
```

다음으로 변경:

```groovy
cron('0 18 * * *')  // 매일 오후 6시
cron('0 9,18 * * *')  // 매일 오전 9시, 오후 6시 (2회)
```

### 계좌 추가

환경 변수 또는 Jenkins Credentials에 추가:

```bash
ACCOUNT_4_NAME=golden-dragonfruit
ACCOUNT_4_ADDRESS=0x...
```

스크립트가 자동으로 감지합니다 (최대 9개 계좌).

### P&L 기간 변경

`golden-apple/src/polybot/api/data_api_client.py`에서:

```python
pnl_7d = self.calculate_pnl_for_period(address, days_ago=7)
pnl_30d = self.calculate_pnl_for_period(address, days_ago=30)
```

다음으로 변경 (예: 14일, 90일):

```python
pnl_14d = self.calculate_pnl_for_period(address, days_ago=14)
pnl_90d = self.calculate_pnl_for_period(address, days_ago=90)
```

## 🐛 문제 해결

### "Module not found" 오류

```bash
pip install py-clob-client requests pyyaml python-dotenv
```

### Slack 메시지가 오지 않음

1. Webhook URL이 정확한지 확인
2. 터미널에서 테스트:
   ```bash
   curl -X POST -H 'Content-type: application/json' \
     --data '{"text":"Test"}' $SLACK_WEBHOOK_URL
   ```

### Jenkins 빌드 실패

1. Console Output 확인
2. Python 버전 확인 (`python3 --version`)
3. 환경 변수 확인 (Credentials ID 일치 여부)

## 📚 더 자세한 문서

- **전체 문서**: [DAILY_REPORT_README.md](DAILY_REPORT_README.md)
- **Jenkins 설정**: [JENKINS_SETUP.md](JENKINS_SETUP.md)
- **API 문서**: [Polymarket Docs](https://docs.polymarket.com/)

## 💬 도움이 필요하신가요?

1. 로그 파일 확인: `daily_report_YYYYMMDD.log`
2. Jenkins Console Output 확인
3. Slack 알림 확인

---

**설정 완료 시간**: 약 10분
**유지보수**: 거의 불필요 (자동 실행)
