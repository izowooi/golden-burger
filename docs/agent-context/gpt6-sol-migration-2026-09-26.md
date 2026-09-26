# GPT-6 Sol 작업 지침 마이그레이션 — 2026-09-26

## 결과

- Codex user config는 이미 `model = "gpt-6-sol"`, `model_reasoning_effort = "max"`였다. [GPT-6 Sol 공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-sol)는 `max` effort와 MCP·skills를 지원한다. 설정값을 추측으로 변경하지 않았다.
- 별도 터미널 CLI는 `0.155.1`이었다. [Codex 공식 변경 이력](https://learn.chatgpt.com/docs/changelog)에 따라 GPT-6 Sol 지원이 포함된 `0.157.0`으로 설치했고 `codex --version`과 `codex mcp list`를 다시 확인했다.
- user MCP 연결은 모델 독립이다. `openaiDeveloperDocs` 검색이 실제 응답했고 Supabase·Playwright·node_repl 설정은 유지했다. Better Stack은 `Not logged in`으로 표시되어 인증이 필요한 작업에는 사용 전 연결 확인이 필요하다. 비활성 서버·다른 인증 설정은 임의로 바꾸지 않았다.

## 지침과 스킬

- 루트 `AGENTS.md`는 약 40KB의 변경되기 쉬운 전략별 수치를 항상 로딩하던 구조에서, 적용 경로·증거 계약·작업별 문서 경로를 담은 약 5.6KB의 지침으로 줄였다. 기존 전문은 [보존본](legacy-root-2026-09-26.md)에 보관하고 현재 운영값의 권위가 아님을 표시했다. 금융 실거래 confirmed fill, cohort, verified DB, local-only summary, Git·secret 규칙은 남겼다.
- 상위 `/Users/izowooi/git/AGENTS.md`의 문서 읽기와 테스트를 작업 위험에 맞췄다. 현재 대화에서 이미 승인된 범위는 반복 질문하지 않고, 환경 장애는 안전한 자체 진단 뒤 사용자만 해결할 수 있는 부분을 묻도록 명확히 했다. 미승인 운영 배포·비가역 변경·secret 경계는 유지한다. 원본과 동일한 SHA-256을 확인한 복구본은 `~/.Codex/_workspace/agents-md/2026-09-26_gpt6-sol-migration/L1_AGENTS_before.md`에 있다.
- `agents-md` 스킬의 다른 모델(`opus`), 존재하지 않는 agent 파일·팀 도구, 매 단계 필수 인터뷰·승인 흐름을 제거했다. 활성 GPT-6 모델을 상속하고 실제 미정인 정책만 묻는다. 이전 본문과 상세 질문 파일은 `~/.Codex/_workspace/agents-md/2026-09-26_gpt6-sol-migration/` 및 `~/.codex/archive/skills/agents-md-references-2026-09-26/`에 보존했다.
- `harness` 스킬은 약 28KB의 Claude 전용 팀 절차와 `opus` 강제를 약 3KB의 현재 Codex 역할·스킬·MCP 경계로 교체했다. 이전 전체 스킬은 `~/.codex/archive/skills/harness-legacy-2026-09-26/`에 있다.
- 저장소의 `sports-trade-report` 스킬은 약 7.6KB에서 약 3.8KB로 줄였지만 CONFIRMED fill, fee, exact resolution, 경기·팔·전체 합계와 unknown 노출을 보존했다. 별도 전역 사본은 내용이 중복되어 `~/.codex/archive/skills/sports-trade-report-2026-09-26/`으로 옮겼다. 상세 evidence reference는 저장소 스킬에 그대로 있다.

[OpenAI의 GPT-6 지침 글](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)은 짧고 구분되는 스킬 설명, 조건부 reference 로딩, 과도한 사전 문서 읽기와 중복 승인 제거를 권한다. 이는 GPT-6 Sol에 대한 별도의 성능 수치 주장이 아니라 이 워크스페이스 지침을 정리한 근거다.

## 검증·범위

세 활성 개인/저장소 스킬은 `quick_validate.py`를 통과했고, 활성 지침의 `opus`·`gpt-5`·`AskUserQuestion` 강제 참조는 0개다. `uv run tools/verify_strategy_contracts.py`는 30개 전략 계약을 통과했다. 저장소의 기존 사용자 미추적·수정 파일은 건드리지 않았다. 실거래 봇 코드, Jenkins job, 거래 금액·TP·SL은 이번 마이그레이션에서 변경하지 않았다.

## 짧은 후속 회고 지시 예시

> sports-trade-report로 최근 **24시간(또는 7일)** 스포츠 전략을 회고해줘. 실제 설정과 모든 child runtime을 확인하고, 필요한 job은 daily-rsync로 동기화·검증·pin해. 표 맨 위에 전략×종목×팔 확정 손익과 전체 합계, 미확정 노출을 보여줘. 경기별 실제 체결·수수료·정산을 simulation과 분리하고, 장애·호가 공백·Jenkins 실패를 고쳐줘. 과거 전체 DB의 같은 경기·동일 금액 walk-forward로 TP/SL/진입과 전략×종목별 금액의 증액·감액을 비교해줘. 안정적인 변경만 A/B 한 변수 원칙으로 테스트·commit·push·live 배포하고 자연 빌드 두 번까지 검증해. 근거가 약하면 현행 유지와 필요한 표본을 말해줘.
