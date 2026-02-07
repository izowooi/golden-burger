# Slack App 생성 가이드 (Manifest 사용)

Manifest 파일을 사용하여 Slack App을 빠르게 생성하는 방법입니다.

## 🚀 빠른 생성 (2분)

### 1단계: Slack API 페이지 접속

[https://api.slack.com/apps](https://api.slack.com/apps)

### 2단계: "Create New App" 클릭

화면 오른쪽 상단의 녹색 버튼 클릭

### 3단계: "From an app manifest" 선택

두 가지 옵션이 나타납니다:
- ❌ From scratch
- ✅ **From an app manifest** ← 이것 선택!

### 4단계: Workspace 선택

앱을 설치할 Slack Workspace를 선택합니다.

### 5단계: Manifest 입력

#### 방법 A: YAML 사용 (권장)

1. **YAML** 탭 선택
2. `slack-app-manifest.yaml` 파일 내용 복사
3. 붙여넣기
4. **Next** 클릭

#### 방법 B: JSON 사용

1. **JSON** 탭 선택
2. `slack-app-manifest.json` 파일 내용 복사
3. 붙여넣기
4. **Next** 클릭

### 6단계: 앱 정보 확인

자동으로 채워진 정보 확인:
- App Name: Polymarket Reporter
- Description: Daily portfolio reports...
- Features: Incoming Webhooks ✅

**Create** 버튼 클릭

## 🔗 Webhook URL 생성

### 1. Incoming Webhooks 활성화

앱 생성 후:

```
Settings 메뉴 → Features → Incoming Webhooks
→ "Activate Incoming Webhooks" 토글 ON (이미 ON 되어있음)
```

### 2. Webhook URL 추가

```
페이지 하단 → "Add New Webhook to Workspace" 클릭
→ 메시지를 받을 채널 선택 (예: #polymarket-reports)
→ "Allow" 클릭
```

### 3. Webhook URL 복사

생성된 URL이 표시됩니다:

```
https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX
```

**이 URL을 복사하세요!** 이것이 `SLACK_WEBHOOK_URL` 환경변수 값입니다.

## 📝 환경변수 설정

### 로컬 개발

`.env` 파일에 추가:

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX
```

### Jenkins

Jenkins Credentials로 등록:

```
Manage Jenkins → Credentials → Add Credentials

Kind: Secret text
Secret: https://hooks.slack.com/services/...
ID: polymarket-slack-webhook
Description: Polymarket Slack Webhook URL
```

## ✅ 테스트

### 터미널에서 테스트

```bash
curl -X POST \
  -H 'Content-type: application/json' \
  --data '{"text":"✅ Polymarket Reporter 테스트 메시지"}' \
  https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### Python 스크립트로 테스트

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
python test_report.py
```

Slack 채널에 테스트 메시지가 도착하면 성공! 🎉

## 🎨 앱 커스터마이징 (선택사항)

### 앱 아이콘 변경

```
Settings → Basic Information → Display Information
→ App Icon: 1024x1024 PNG 이미지 업로드
→ Background Color: #1a1d29 (또는 원하는 색상)
```

### 앱 이름 변경

```
Settings → Basic Information → Display Information
→ App Name: 원하는 이름 입력
→ Short Description: 간단한 설명
```

### 앱 설명 변경

manifest 파일의 `description`과 `long_description` 수정 후 재업로드

## 📋 Manifest 파일 설명

### YAML 버전 (`slack-app-manifest.yaml`)

```yaml
display_information:
  name: Polymarket Reporter        # 앱 이름
  description: Daily portfolio...   # 짧은 설명
  background_color: "#1a1d29"      # 배경색

features:
  incoming_webhooks:
    enabled: true                   # Webhook 기능 활성화

oauth_config:
  scopes:
    bot:
      - incoming-webhook            # 필수 권한
      - chat:write                  # 메시지 전송
```

### JSON 버전 (`slack-app-manifest.json`)

동일한 내용을 JSON 형식으로 표현

## 🔒 보안 주의사항

### ⚠️ Webhook URL 보호

1. **절대 공개 저장소에 커밋하지 마세요**
   - `.env` 파일은 `.gitignore`에 포함됨
   - Jenkins Credentials 사용

2. **URL 노출 시 대응**
   ```
   앱 설정 → Incoming Webhooks
   → 기존 Webhook 삭제
   → 새 Webhook 생성
   ```

3. **접근 제한**
   - Webhook URL은 채널별로 생성
   - 민감한 정보가 있는 채널은 Private 채널 사용

## 🔄 Manifest 업데이트

앱 설정 변경이 필요한 경우:

```
Settings → App Manifest
→ YAML/JSON 탭에서 manifest 수정
→ Save Changes
```

## 🐛 트러블슈팅

### 문제: "Invalid manifest" 오류

**원인**: YAML/JSON 문법 오류

**해결**:
- YAML 검증: [YAML Lint](https://www.yamllint.com/)
- JSON 검증: [JSON Lint](https://jsonlint.com/)

### 문제: Webhook URL이 작동하지 않음

**확인 사항**:
1. Incoming Webhooks가 "ON" 상태인지
2. 채널이 삭제되지 않았는지
3. URL이 올바르게 복사되었는지

**테스트**:
```bash
curl -X POST \
  -H 'Content-type: application/json' \
  --data '{"text":"Test"}' \
  YOUR_WEBHOOK_URL

# 출력: ok
```

### 문제: 메시지가 원하는 채널에 안 감

**원인**: Webhook은 생성 시 지정한 채널로만 전송됨

**해결**:
- 다른 채널에 보내려면 해당 채널용 Webhook 새로 생성
- 또는 `chat:write.public` 권한으로 Bot Token 사용 (고급)

## 📚 추가 리소스

- [Slack API 문서](https://api.slack.com/messaging/webhooks)
- [App Manifest 레퍼런스](https://api.slack.com/reference/manifests)
- [Incoming Webhooks 가이드](https://api.slack.com/messaging/webhooks)

## 💡 팁

### 여러 채널에 보내기

채널별로 Webhook URL을 만들고 환경변수로 관리:

```bash
# .env
SLACK_WEBHOOK_MAIN=https://hooks.slack.com/services/.../main
SLACK_WEBHOOK_ALERTS=https://hooks.slack.com/services/.../alerts
SLACK_WEBHOOK_DEV=https://hooks.slack.com/services/.../dev
```

### Webhook 관리

앱 설정에서 여러 Webhook를 만들고 관리할 수 있습니다:

```
Settings → Incoming Webhooks
→ Add New Webhook to Workspace (여러 개 추가 가능)
```

---

**설정 시간**: 약 2분
**난이도**: ⭐☆☆☆☆ (매우 쉬움)

Manifest를 사용하면 클릭 몇 번으로 Slack App 완성! 🚀
