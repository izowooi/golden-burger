# Astra 지침 점검과 공용 데이터 전환

사용자 요청에 따라 DB 개편 전에 활성 작업 지침을 확인했다. Codex user config는 이미
`gpt-6-astra`, reasoning `medium`이고 별도 agent 모델 override는 없었다. 모델·effort는 유지했다.
공식 [Astra 모델 정보](https://developers.openai.com/api/docs/models/gpt-6-astra)에서 지원 범위를
확인했으며 모델 전환만으로 저장소 구조나 거래 성과가 개선된다고 가정하지 않는다.

[공식 지침 정리 가이드](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)에
맞춰 적용되는 근거만 읽기, 기존 사용자 허가의 지속, 실제 완료 조건까지 진행하기를 명확히 했다.
루트 AGENTS는 승인된 공용 데이터 마이그레이션을 독립 폴더 규칙으로 막지 않도록 바꿨다.
공용 raw 데이터와 전략별 주문·체결·수수료·포지션의 논리적 경계는 유지한다.
공통 코드를 바꿀 때 영향받는 소비자의 테스트와 운영 경로를 검증하되, 매 수정마다 무관한
프로젝트 의존성 설치·테스트를 반복하는 문구를 제거했다.

개인 `agents-md` agent/template의 이전 자동 push 금지, 무조건 재승인, 검토 2회 후 강제 통과,
고정 depth 제한을 현재 정책과 맞췄다. 원본 6개 파일은 local-only
`~/.codex/archive/astra-guidance-2026-09-29/`에 보존했다. TOML 3개 파싱과 skill 검증을 통과했다.
기존 skill은 전반적으로 이미 짧아 대대적인 재작성을 하지 않았다. 필요한 데이터 동기화는
요청된 회고·검증·마이그레이션의 실행 범위로 정리했다.

MCP 연결은 모델 독립이며, 이번 요청과 관련된 공식 문서 조회·filesystem·Jenkins 접근이
작동했다. 모델 변경을 이유로 서버·인증 설정을 일괄 변경하지 않았다. credential 비노출,
CONFIRMED fill과 exact resolution의 증거 계약, 수동 포지션 소유권은 완화하지 않는다.
