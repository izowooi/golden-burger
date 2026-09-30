# polylab 운영 지침 (L2)

`/Users/izowooi/git/t1` 에 적용한다. 상위 `/Users/izowooi/git/AGENTS.md`(L1)의 Git·보안 규칙을 따른다.
원격 `github.com/izowooi/golden-burger`(**공개 저장소**), branch `main`.

## 무엇인가

Polymarket 스포츠(soccer·MLB·NBA·NFL·NHL) 가격이 경기 시간대별로 실제 확률과 얼마나 어긋나는지(과대/과소 평가),
같은 이벤트에 대한 가격 민감도가 경기 시간에 따라 어떻게 변하는지를 연구하고, 그 편향을 이용한 전략을
5→10→25→50→100 USDC 단위로 실거래 검증하는 **완전 자동화 연구 시스템**이다. 설계는 `docs/ARCHITECTURE.md`.

## 어디를 볼지

| 작업 | 자료 |
|---|---|
| 전체 구조·잡·데이터 흐름 | `docs/ARCHITECTURE.md` |
| 전략 변형(파라미터·단위·계좌·모드) | `strategies/*.yaml` (유일한 진실), 로직 `src/polylab/strategies/` |
| 전략 원 명세(레거시 포팅) | `docs/strategies/*.md` |
| API 출처·필드 | `docs/research/api-sources.md` |
| 대시보드 데이터 계약 | `docs/contracts/dashboard-json.md`, 앱 `dashboard/` |
| 회고 결과 | `reports/` (autopilot 커밋), 논문 요약 `docs/research/monthly/` |
| 논문 배경 | `docs/thesis/` |

`cloud_run_proj/` 는 별개 프로젝트(Jenkins `trend-follower-nasdaq`)이며 polylab 과 무관하다. 수정하지 않는다.

## 실행 환경

- Mac mini `jongwoopark@192.168.50.23`(VPN 24시간)에서만 Polymarket API 가 된다. MacBook 에서는 `scripts/tunnel.sh` SOCKS 터널을 쓴다.
- 런타임 checkout·DB: `/Volumes/t7/polylab/{repo,data,logs,state,archive}`. 외장하드 미마운트 시 fail closed.
- 비밀: `~/.polylab/accounts.env`(16개 계좌 키), `services.env`(Slack·Supabase), `claude_oauth_token` — chmod 600, **절대 커밋 금지**.
- Jenkins `http://192.168.50.23:8080` 의 `polylab-*` 잡만 이 시스템이다. 정의는 `jenkins/`.
- Python: `uv sync --extra dev && uv run pytest -q`, CLI `uv run polylab <command>`.

## 규칙

- 실손익은 CONFIRMED fill·fee·확인된 resolution 만. 미확정·unknown fee 는 0 으로 채우지 않는다.
- 모든 변형은 5 USDC 에서 시작, `src/polylab/risk` ladder 게이트로만 증액(최대 100). 킬스위치 `/Volumes/t7/polylab/state/KILL`.
- AI 회고(`polylab retro`)는 validator 를 통과한 yaml 변경만 커밋한다. 사람이 직접 바꿀 때도 yaml 과 bounds 를 함께 갱신한다.
- 공용 Polymarket 데이터는 `core.db`/`books/` 한 곳에만 쓴다. 전략은 자기 `strategies/<id>.db` 에만 쓴다.
- 최종 응답 직전 L1 의 local-only `task-summaries/YYYY/MM/` 기록을 남긴다.
