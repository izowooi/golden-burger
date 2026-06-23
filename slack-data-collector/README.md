# Slack Data Collector

`daily-report`가 Slack 채널로 전송한 Polymarket 리포트 이력을 수집하고,
정규화한 뒤 데이터베이스에 적재하기 위한 독립 워크스페이스입니다.

## 현재 상태

- 워크스페이스 폴더만 생성된 상태입니다.
- Python 수집기와 Supabase 스키마는 다음 단계에서 구현합니다.
- 기존 `daily-report` 프로젝트의 전송 책임과 이 프로젝트의 수집 책임을 분리합니다.

## 수집 범위

1. `conversations.history`를 커서 기반으로 끝까지 순회해 채널 이력을 백필합니다.
2. 스레드가 있으면 `conversations.replies`로 답글까지 수집합니다.
3. Slack 원본 JSON을 보존하고, 분석용 필드를 별도로 정규화합니다.
4. Slack의 `ts`를 고유 기준으로 사용해 재실행 시 중복 적재를 방지합니다.
5. 마지막 수집 시점을 저장해 초기 전체 수집 이후에는 증분 수집합니다.

## Slack 준비 사항

- Slack 채널 ID
- Slack 앱의 Bot User OAuth Token (`xoxb-...`)
- 공개 채널이면 `channels:history`, 비공개 채널이면 `groups:history` 권한
- 대상 채널에 초대된 봇

Signing Secret은 Events API 등 Slack이 이 애플리케이션으로 보내는 요청을 검증할 때
사용합니다. 이 프로젝트처럼 Web API를 호출해 이력을 가져오는 배치 수집에는 필수가
아닙니다.

토큰과 시크릿은 로컬 환경변수로만 관리하며 Git에 커밋하지 않습니다.

## 다음 구현 단계

- Python 프로젝트와 환경변수 템플릿 구성
- Slack API 페이지네이션, 재시도 및 rate limit 처리
- 원본 JSONL 또는 JSON 파일 저장
- 메시지 형식 분석 후 정규화 규칙 정의
- Supabase 테이블, 인덱스, RLS 및 upsert 구현

## 참고 문서

- [Slack conversations.history](https://docs.slack.dev/reference/methods/conversations.history/)
- [Slack conversations.replies](https://docs.slack.dev/reference/methods/conversations.replies/)
- [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
