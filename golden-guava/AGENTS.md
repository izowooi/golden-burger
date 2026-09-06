# L3 — Golden Guava

상위 모노레포 AGENTS.md와 `docs/new-strategy-playbook.md`, `docs/retro/EVIDENCE_CONTRACT.md`를 따른다.
이 문서는 새 프로젝트의 명시적 구현·검증 계약이며 전역 개발 정책을 변경하지 않는다.

## 상태와 경계

- 신규 프로젝트 구현 중. 현재 research 경로와 실행 어댑터가 있으며 live 정책/상태관리 통합은 미완료다.
- 연구 4개 job은 `polybot-sim-guava-a`~`d`, live 이름은 `polybot-lion`/`polybot-wolf`를 유지한다.
- 모든 실제 workspace는 `/Volumes/t7/jenkins/<Jenkins job>`다. `/Volums`라는 오타 경로를 만들지 않는다.
- 종목·가설·기간·비용·규모·source/config를 구분한다. 심리적 인과나 수익성을 관측상관으로 단정하지 않는다.
- 연구는 credential 존재 자체를 거부하고 SDK/Signer/order 경로에 들어가지 않는다. `POST /books`는 공개 읽기다.
- 실거래는 명시적 live spec·권한·별도 DB·실제 서명 금액의 POST 전 내구성·주문 단위 위험 한도가 필요하다.
- 원래의 Kiwi/NHL 데이터나 수동 포지션을 새 전략에 합치거나 청산하지 않는다.

## 코드

- `config.py`: 엄격한 설정/job/mode 결합, source/config hash. 잘못된 타입·NaN·중복/모순을 거부한다.
- `public_clients.py`, `identity.py`, `official_news.py`: 허용된 공개 자료·정확한 경기/결과 식별과 시각.
- `book.py`, `hypotheses.py`: 직접 호가 계산과 관측 특징. 없는 가격·수수료·NO를 합성하지 않는다.
- `evidence.py`: 원자료/실패 영수증 보존, cycle+성공의 원자적 게시, 동일 cohort cache.
- `runtime.py`, `workspace.py`, `budget.py`: 실제 mount/UUID/marker, 단일 writer와 슬롯, 예산과 정리.
- `execution.py`: live-only broker. 정책·포지션·실거래 승인 자체가 아니다.

연구에서 `max_positions`나 가상 손익으로 원자료를 차단하지 않는다. live에서는 실제/불확정
노출을 계속 예약한다. 180분 경과를 성공 체결·0보유·손익 확정으로 바꾸지 않는다.
정기 경로에 전체 COUNT/quick_check/VACUUM을 추가하지 않는다. 전체 검사는 검증된 사본에서 수행한다.
부족한 budget은 새 요청을 제한하지만, POST 중 프로세스를 강제종료해 정상시간으로 위장하지 않는다.

## 검증·운영

`uv sync --frozen --extra dev`, `uv run pytest`, `uv build`와 저장소 공통 계약 검사를 사용한다.
실제 public cycle은 Jenkins 외장 workspace에서 수동 실행→DB/log 검사→예약 복원→자연 실행으로 검증한다.
fixture 통과는 실제 배포·1분 이내 실행·실제 체결의 증명이 아니다.
Jenkins 변경 때 key/funder 값·수동 보유분은 보존하고, source/runtime 전환 이력을 남긴다.
수집본은 daily-rsync scan/plan/sync/verify와 절대 경로/SHA/cohort를 고정한 뒤 읽기 전용 분석한다.
상세 요청·결과는 local-only 094 작업 기록에 연속해서 누적하며, 별도 중복 summary를 만들지 않는다.
