# Task summaries

Codex 작업이 끝날 때 사용자 요청과 최종 결과 요약을 로컬 Markdown으로 보관하는
폴더다. 과거 채팅을 다시 스크롤하지 않고 작업의 입력과 결과를 찾는 용도다.

## 저장 규칙

- 실제 기록: `YYYY/MM/YYYY-MM-DD_HHMMSS_<짧은-slug>.md`
- 기록 시점: 구현·검증이 끝나고 최종 응답을 보내기 직전
- 포함: 작업을 시작시킨 사용자 메시지의 텍스트, 최종 응답 요약
- 제외: 이미지·첨부 binary, system/developer/internal context, tool 원문 출력
- 보안: private key, token, webhook, password, 개인정보는 `[REDACTED]`로 치환
- 연속된 하나의 작업은 파일 하나로 합치고, 독립된 새 요청은 새 파일로 작성

## Git 정책

요청 원문에는 credential이나 개인정보가 들어올 수 있으므로 실제 summary 파일은 이
폴더의 `.gitignore`에 의해 **local-only**로 유지한다. 이 `README.md`와 `.gitignore`만
저장소에서 추적한다. summary를 Git에 강제로 추가하거나 remote로 push하지 않는다.

기록이 실패하면 최종 응답에서 저장하지 못한 경로와 사유를 알린다.
