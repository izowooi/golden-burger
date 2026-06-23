# Slack Data Collector

`daily-report`가 Slack 채널로 전송한 Polymarket 리포트 이력을 수집하고,
정규화한 뒤 데이터베이스에 적재하기 위한 독립 워크스페이스입니다.

## 현재 상태

- 기간별 Slack 메시지와 스레드를 수집하는 Python CLI가 구현되어 있습니다.
- 원본 JSONL과 정규화 JSONL을 로컬 `data/`에 저장합니다.
- 실제 워크스페이스의 Bot Token, 채널 접근 및 메시지 조회를 검증했습니다.
- Supabase 스키마와 DB 적재는 아직 구현하지 않았습니다.
- 기존 `daily-report` 프로젝트의 전송 책임과 이 프로젝트의 수집 책임을 분리합니다.

## 수집 범위

1. `conversations.history`를 커서 기반으로 끝까지 순회해 채널 이력을 백필합니다.
2. 스레드가 있으면 `conversations.replies`로 답글까지 수집합니다.
3. Slack 원본 JSON을 보존하고, 분석용 필드를 별도로 정규화합니다.
4. Slack의 `ts`를 고유 기준으로 사용해 재실행 시 중복 적재를 방지합니다.
5. 마지막 수집 시점을 저장해 초기 전체 수집 이후에는 증분 수집합니다.

Slack API가 반환할 수 있는 이력만 수집할 수 있습니다. 워크스페이스의 메시지 보존
정책에 따라 이미 삭제되었거나 접근 기간이 지난 메시지는 API로 복구할 수 없습니다.

## 준비할 값

| 환경변수 | 예시 | 용도 |
|---|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-your-token` | Slack Web API 인증 |
| `SLACK_CHANNEL_ID` | `C0123456789` | 수집할 채널 지정 |

두 값은 로컬 `.env`에 저장합니다. 실제 토큰은
README, Slack App Manifest, 소스 코드 또는 Git에 넣지 않습니다.

## 빠른 실행

### 1. 개발 환경 구성

```bash
cd slack-data-collector
uv sync
```

### 2. 로컬 환경변수 구성

```bash
cp .env.example .env
```

복사한 `.env`의 대체 값을 실제 Bot Token과 Channel ID로 변경합니다. `.env`는 Git
ignore 대상입니다.

### 3. 연결 확인

```bash
uv run slack-data-collector check
```

토큰은 출력되지 않습니다. 대상 워크스페이스, 채널, Bot User의 채널 참여 여부만
확인합니다.

### 4. 기간별 메시지 수집

```bash
uv run slack-data-collector collect \
  --start 2026-06-01 \
  --end 2026-06-23
```

`--start`와 `--end`는 모두 포함되는 날짜이며 기본 날짜 경계는 `Asia/Seoul`입니다.
다른 타임존이 필요하면 `--timezone UTC`처럼 지정합니다.

기본 모드는 기간보다 오래된 부모 메시지까지 스캔합니다. 그래야 과거 부모 글에 지정
기간 중 새로 작성된 스레드 답글도 누락되지 않습니다. 채널 전체 이력이 크고 이
완전성보다 속도가 중요할 때만 다음 옵션을 사용합니다.

```bash
# 기간 안에 작성된 부모 메시지의 스레드만 조회
uv run slack-data-collector collect \
  --start 2026-06-01 \
  --end 2026-06-23 \
  --bounded-thread-scan

# 스레드를 제외하고 채널 본문만 조회
uv run slack-data-collector collect \
  --start 2026-06-01 \
  --end 2026-06-23 \
  --no-threads
```

### 5. 결과 확인

실행마다 다음 구조의 디렉터리가 생성됩니다.

```text
data/
└── 2026-06-01_2026-06-23_20260623T120000Z/
    ├── manifest.json
    ├── raw/
    │   └── messages.jsonl
    └── normalized/
        └── messages.jsonl
```

- `manifest.json`: 기간, 채널, 수집 통계와 파일 정보
- `raw/messages.jsonl`: Slack 메시지 원본 객체와 수집 메타데이터
- `normalized/messages.jsonl`: DB 적재를 위한 공통 필드, Block, Attachment, File,
  Reaction 정보

`data/`에는 실제 Slack 내용이 들어가므로 디렉터리 전체가 Git ignore 대상입니다.

### 6. 테스트 실행

```bash
uv run python -m unittest discover -s tests -v
```

## 1. Slack App 만들기

Slack App을 생성하고 설치할 권한이 있는 계정으로 진행합니다. 워크스페이스 정책에
따라 관리자 승인이 필요할 수 있습니다.

1. [Slack Your Apps](https://api.slack.com/apps)에 접속합니다.
2. `Create New App`을 누릅니다.
3. `From a manifest`를 선택합니다.
4. 리포트 채널이 있는 워크스페이스를 선택하고 `Next`를 누릅니다.
5. 형식을 `YAML`로 선택합니다.
6. 아래 Manifest 전체를 붙여 넣고 `Next`를 누릅니다.
7. 앱 이름과 요청 권한을 검토한 뒤 `Create`를 누릅니다.

### 권장 Manifest

```yaml
display_information:
  name: Slack Data Collector
  description: Collects Polymarket report messages for database ingestion
  background_color: "#2F6B5F"

features:
  bot_user:
    display_name: slack-data-collector
    always_online: false

oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - groups:history
      - groups:read

settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

이 Manifest에는 실제 토큰, 채널 ID, Client Secret 또는 Signing Secret을 입력하지
않습니다. Manifest는 앱의 이름, Bot User와 요청 권한만 정의합니다.

### Manifest 필드 설명

| 필드 | 이유 |
|---|---|
| `features.bot_user` | 워크스페이스에 설치할 수집용 Bot User를 만듭니다. |
| `channels:history` | 봇이 참여한 공개 채널의 메시지와 스레드를 읽습니다. |
| `groups:history` | 봇이 참여한 비공개 채널의 메시지와 스레드를 읽습니다. |
| `channels:read` | 공개 채널 ID와 기본 정보를 확인합니다. |
| `groups:read` | 봇이 참여한 비공개 채널 ID와 기본 정보를 확인합니다. |
| `socket_mode_enabled: false` | 이 프로젝트는 실시간 Socket Mode가 아닌 배치 조회를 사용합니다. |
| `token_rotation_enabled: false` | 초기 버전은 장기 실행 가능한 Bot Token을 환경변수로 관리합니다. |

`daily-report` 채널이 공개 채널로 확정되어 있고 최소 권한만 허용하려면
`groups:history`와 `groups:read`를 제거할 수 있습니다. 반대로 비공개 채널이면 두
`groups:*` 권한이 필요합니다.

메시지 작성 권한인 `chat:write`, Events API 설정, Request URL, Redirect URL,
Incoming Webhook은 이 수집기에 필요하지 않습니다. 사용자 이름까지 조회해야 할 때만
추후 `users:read`를 추가하고 앱을 다시 설치합니다.

## 2. 앱을 워크스페이스에 설치하기

Manifest로 앱을 만들었더라도 설치 전에는 Bot Token이 발급되지 않습니다.

1. 생성된 앱의 관리 화면에서 왼쪽 `OAuth & Permissions`를 엽니다.
2. `Scopes`의 `Bot Token Scopes`에 Manifest의 권한이 등록되었는지 확인합니다.
3. 화면 위쪽의 `Install to Workspace`를 누릅니다.
4. 권한 승인 화면에서 대상 워크스페이스가 맞는지 확인하고 `Allow`를 누릅니다.
5. 관리자 승인 화면이 나오면 워크스페이스 관리자에게 앱 승인을 요청합니다.

Manifest에서 OAuth scope를 추가하거나 변경한 경우 기존 토큰에 즉시 반영되지 않을 수
있습니다. `OAuth & Permissions`에서 `Reinstall to Workspace`를 실행하고 다시
승인합니다.

## 3. `SLACK_BOT_TOKEN` 얻기

앱 설치가 끝나면 Slack App 관리 화면의 다음 위치에서 토큰을 확인합니다.

1. 앱 관리 화면에서 `OAuth & Permissions`를 엽니다.
2. `OAuth Tokens for Your Workspace` 영역을 찾습니다.
3. `Bot User OAuth Token`의 `Copy`를 누릅니다.
4. 값이 `xoxb-`로 시작하는지 확인합니다.

이 값이 `SLACK_BOT_TOKEN`입니다. `xoxp-` User Token, `xapp-` App-Level Token,
Signing Secret, Client Secret 또는 기존 `daily-report`의 Incoming Webhook URL과
혼동하지 마십시오.

토큰이 노출되면 즉시 앱 관리 화면에서 토큰을 폐기하거나 앱을 재설치해 교체해야
합니다. 채팅, 이슈, 로그 또는 터미널 출력에 실제 토큰을 붙여 넣지 않습니다.

## 4. 봇을 수집 대상 채널에 추가하기

권한과 토큰이 있어도 Bot User가 채널에 참여하지 않았다면 해당 채널 이력을 읽을 수
없습니다.

1. Slack에서 리포트가 쌓이는 채널을 엽니다.
2. 메시지 입력창에 `/invite @slack-data-collector`를 입력합니다.
3. Slack이 표시하는 `Slack Data Collector` 앱을 선택해 초대합니다.

또는 채널 이름을 눌러 채널 상세 화면을 연 뒤 `Integrations` 또는 `Apps`에서
`Add an app`을 선택할 수 있습니다. 비공개 채널에는 해당 채널 멤버가 직접 앱을
추가해야 합니다.

## 5. `SLACK_CHANNEL_ID` 확인하기

채널 이름은 변경될 수 있으므로 API에는 이름이 아닌 Channel ID를 사용합니다.

### Slack 화면에서 확인

1. 대상 채널을 엽니다.
2. 상단의 채널 이름을 눌러 채널 상세 화면을 엽니다.
3. `About` 영역 아래쪽의 Channel ID를 찾아 복사합니다.

### 채널 URL에서 확인

Slack 웹 또는 데스크톱 앱에서 채널 링크를 복사하면 다음과 같은 형태가 됩니다.

```text
https://app.slack.com/client/T0123456789/C0123456789
```

- `T0123456789`: Workspace ID
- `C0123456789`: Channel ID

두 번째 ID를 `SLACK_CHANNEL_ID`로 사용합니다. 채널 종류에 따라 ID 접두사가 다를 수
있으므로 임의로 수정하지 말고 복사한 값을 그대로 사용합니다.

## 6. 로컬 환경변수 준비

Python 프로젝트를 구성한 뒤 `slack-data-collector/.env`에 다음 값을 넣습니다.

```dotenv
SLACK_BOT_TOKEN=xoxb-replace-with-your-token
SLACK_CHANNEL_ID=C0123456789
```

위 값은 형식 예시입니다. 실제 `.env` 파일은 Git ignore 대상이며 절대 커밋하지
않습니다. 현재 배치 조회 방식에는 `SLACK_SIGNING_SECRET`이 필요하지 않습니다.

Signing Secret은 Events API, Slash Command 등 Slack이 애플리케이션으로 보내는
HTTP 요청의 서명을 검증할 때 사용합니다. 이 프로젝트는 반대로 Python에서 Slack Web
API를 호출하므로 Bot Token으로 인증합니다.

## 7. 토큰과 채널 접근 테스트

Python 수집기를 작성하기 전에도 `curl`로 설정을 확인할 수 있습니다. 먼저 현재 셸에
값을 입력합니다. 아래 명령의 예시 값을 실제 값으로 바꾸되 셸 히스토리 공유에
주의합니다.

```bash
export SLACK_BOT_TOKEN='xoxb-replace-with-your-token'
export SLACK_CHANNEL_ID='C0123456789'
```

### 토큰 확인

```bash
curl --silent --show-error \
  --header "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  https://slack.com/api/auth.test
```

응답의 `ok`가 `true`이고 `team`이 대상 워크스페이스인지 확인합니다.

### 채널 접근 확인

```bash
curl --silent --show-error --get \
  --header "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  --data-urlencode "channel=${SLACK_CHANNEL_ID}" \
  https://slack.com/api/conversations.info
```

### 메시지 한 건 조회

```bash
curl --silent --show-error --get \
  --header "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  --data-urlencode "channel=${SLACK_CHANNEL_ID}" \
  --data-urlencode "limit=1" \
  https://slack.com/api/conversations.history
```

마지막 응답에서 `ok: true`와 `messages` 배열이 확인되면 Python 수집기를 작성할 준비가
끝난 것입니다.

## 자주 발생하는 오류

| Slack 오류 | 확인할 내용 |
|---|---|
| `invalid_auth` | 토큰 오타, 폐기된 토큰 또는 다른 종류의 토큰인지 확인합니다. |
| `missing_scope` | Manifest에 필요한 scope를 추가하고 앱을 재설치합니다. |
| `not_in_channel` / `no_permission` | Bot User를 대상 채널에 초대합니다. |
| `channel_not_found` | Channel ID와 토큰의 워크스페이스가 서로 일치하는지 확인합니다. |
| `not_allowed_token_type` | `xoxb-`로 시작하는 Bot User OAuth Token을 사용합니다. |
| `ratelimited` | 응답의 `Retry-After` 헤더만큼 기다린 뒤 재시도합니다. |

## 구현 시 적용할 수집 원칙

- 응답의 `response_metadata.next_cursor`가 빌 때까지 페이지네이션합니다.
- `reply_count`가 있는 부모 메시지는 `conversations.replies`로 스레드를 조회합니다.
- HTTP 429 응답에서는 `Retry-After` 값을 준수합니다.
- Slack 원본 JSON을 먼저 보존한 후 분석용 구조로 변환합니다.
- `(channel_id, ts)`를 고유 키로 사용해 중복 적재를 방지합니다.
- 초기 전체 백필 이후에는 마지막 수집 시점을 기준으로 증분 수집합니다.
- 파일 자체를 내려받아야 할 경우에만 `files:read` 권한을 추가합니다.

내부 워크스페이스용 Custom App의 `conversations.history`와
`conversations.replies`는 현재 Slack Tier 3 rate limit이 적용됩니다. 구현에서는
현재 한도가 충분하더라도 429 재시도 처리를 반드시 포함합니다.

## 다음 구현 단계

- 실제 `daily-report` 메시지 형식 분석 후 정규화 규칙 정의
- Supabase 테이블, 인덱스, RLS 및 upsert 구현

## 참고 문서

- [Slack App Manifest 개요](https://docs.slack.dev/app-manifests/)
- [Manifest로 앱 구성하기](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/)
- [Slack App Manifest 필드](https://docs.slack.dev/reference/app-manifest/)
- [Slack 토큰 종류](https://docs.slack.dev/authentication/tokens/)
- [Slack conversations.history](https://docs.slack.dev/reference/methods/conversations.history/)
- [Slack conversations.replies](https://docs.slack.dev/reference/methods/conversations.replies/)
- [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
- [Slack URL과 ID 확인](https://slack.com/help/articles/221769328-Locate-your-Slack-URL-or-ID)
