# AI 자동화 메커니즘 (autopilot)

polylab 의 "재귀 개선"은 **결정론적 뼈대 위에 AI 판단을 한 단계로 끼워 넣은 구조**다. 사람이 프롬프트를 치지 않는다.
Jenkins 가 정해진 시각에 `polylab retro <kind>` 를 실행하면, 코드가 AI 에게 줄 자료를 만들고, AI 는 제안서만 쓰며,
그 제안을 결정론 validator 와 테스트가 통과시켜야만 실거래 설정이 바뀐다. AI 가 멈춰도 시스템은 멈추지 않는다.

코드: `src/polylab/autopilot/` (retro · context · runner · validator · gitops), 프롬프트: `prompts/*.md`.

## 1. 누가 무엇을 하나

| 주체 | 하는 일 | 할 수 없는 일 |
|---|---|---|
| Jenkins (시계) | 03:30·08:00·19:30 일일, 월 08:30 주간, 1일 09:00 월간에 retro 실행. 놓친 회차는 다음 회차가 마지막 성공 시점부터 묶어서 처리 | 판단 |
| 결정론 코드 | 리포트 작성, 단위 ladder 판정·적용, context pack 생성, 검증, 테스트, 커밋, 게시, Slack | 새 가설 세우기 |
| AI (claude → codex) | context pack 을 읽고 `narrative.md`(서술 회고)와 `proposal.json`(변경 제안) 작성 | 명령 실행·네트워크·git·저장소 쓰기·실거래 설정 직접 변경 |
| validator | 제안을 규칙으로 걸러 통과분만 yaml 로 | 규칙 밖 변경 허용 |
| 사람 | 대시보드·Slack·`reports/` 확인, 필요 시 킬스위치나 yaml 직접 수정 | (선택) |

## 2. 한 번의 회고가 도는 순서

```mermaid
sequenceDiagram
    autonumber
    participant J as Jenkins
    participant R as retro (결정론)
    participant L as risk.ladder
    participant P as context pack
    participant C as claude -p
    participant X as codex exec
    participant V as validator
    participant T as pytest
    participant G as GitHub
    participant O as 대시보드·Slack

    J->>R: polylab retro daily
    R->>R: 리포트 (지난 24h 경기·확률 움직임·거래·손익)
    R->>L: 변형별 단위 게이트 평가
    L-->>R: 증액/감액/유지 (AI 없이도 적용)
    R->>P: 지표·최근 거래·현재 yaml·bounds·백테스트 결과 (비밀값 없음)
    R->>C: prompts/daily.md + context 폴더 (쓰기는 이 폴더만)
    alt claude 성공
        C-->>R: narrative.md + proposal.json
    else 실패·시간초과·잘못된 JSON
        R->>X: 같은 프롬프트 (샌드박스, 네트워크 차단)
        X-->>R: narrative.md + proposal.json
    end
    opt 주간 회고
        R->>X: 다른 엔진이 1차 제안을 검토 (거부·경고만 가능)
    end
    R->>V: 제안 검증
    V-->>R: 통과 / 거부(사유)
    R->>T: 통과분을 yaml 에 반영 후 전체 테스트
    alt 테스트 실패
        R->>R: yaml 원복
    end
    R->>G: reports/·strategies/ 커밋 (계좌 비밀값 정확값 검사 후 push)
    R->>O: 대시보드 JSON 갱신 + Slack 요약
    Note over G,J: 5분 뒤 publish 잡이 git pull → 다음 1분 tick 부터 새 파라미터
```

## 3. AI 에게 주는 것과 받는 것

- **입력 (context pack, `state/retro/<ts>/`)**: `MANIFEST.md`, 리포트 JSON, 변형별 성과(`metrics/*.json`), 최근 CONFIRMED 거래,
  calibration·이벤트 분석 요약, 현재 `strategies/*.yaml` 과 탐색 경계(bounds), (주간) 파라미터 grid 백테스트 결과.
  계좌는 alias 로만 나오고 키·지갑 주소는 없다. 공개용 사본은 `reports/context/latest/`.
- **지시 (prompts)**: 근거는 CONFIRMED 체결과 확인된 정산만, 표본 20건 미만이면 결론 금지, 논문 3개 질문과 연결,
  사용자 운영 철학(1분 주기라 후반 손절은 믿을 수 없음 → 진입을 엄격히, 작은 이익에서 일찍 청산).
- **출력**: `proposal.json` (`polylab.proposal/v1`)

```json
{"schema": "polylab.proposal/v1", "summary": "…",
 "changes": [{"variant_id": "watermelon-cat", "change": "params",
              "values": {"prob_min": 0.93}, "rationale": "…", "evidence": {"n": 34, "roi": 0.012}}]}
```

변경 종류: `params`(파라미터) · `stake`(단위 한 단계) · `mode`(live→paper/off 만) · `new_variant`(주간·월간, paper 로만) · `retire`.

## 4. 안전장치 (validator + 게이트)

| 규칙 | 값 |
|---|---|
| 파라미터 | yaml `bounds` 의 [min, max] 안, 한 번에 `max_step` 이하 |
| 최소 표본 | 현재 파라미터로 정산 20건 이상 |
| cooldown | 같은 변형의 파라미터·단위 변경 후 3일 |
| 단위 | ladder(5·10·25·50·100) 한 단계씩, **증액은 결정론 게이트 통과 시에만**, 감액은 항상 허용, 상한 100 |
| 모드 | AI 는 live 로 올릴 수 없음(live→paper/off, paper→off 만) |
| 신규 변형 | paper·5 USDC 로만, 기반 변형의 bounds·limits 상속 |
| 회당 변경 수 | 일일 2 · 주간 5 · 월간 4, 변형당 1건 |
| 테스트 게이트 | 적용 후 전체 pytest 실패 시 원복 |
| 공개 검사 | 커밋·대시보드 업로드 전 계좌 개인키·funder 주소 정확값 대조, 발견 시 중단 |
| AI 권한 | claude: 파일 도구만(`--permission-mode dontAsk`), 홈 디렉터리 읽기 금지 · codex: `workspace-write` 샌드박스, 네트워크 off |

## 5. 주기별 역할

| 회고 | AI 가 보는 것 | 할 수 있는 제안 | 추가 산출물 |
|---|---|---|---|
| 일일 (3회) | 지난 창의 경기·거래·손익, ladder 상태 | 작은 파라미터 조정, 감액·중지 | `reports/daily/*.md` |
| 주간 | 7일 성과 + 파라미터 grid 백테스트 | 파라미터·단위·신규 paper 변형·폐기, 다른 엔진의 교차 검토 | `reports/weekly/*.md` |
| 월간 | 누적 성과, 종목×경기 단계 calibration, 이벤트 민감도, 단위별 안정성 | 구조적 변경 제안 | `docs/research/monthly/YYYY-MM.md` (논문용) |

## 6. 사람이 개입하는 방법

- 전면 중지: Mac mini 에서 `touch /Volumes/t7/polylab/state/KILL` (신규 진입만 중단, 청산·대사는 계속).
- 특정 변형 중지: `strategies/<id>.yaml` 의 `mode: off` 로 커밋·push → 5분 안에 반영.
- AI 제안 외부 주입(선택): `autopilot/inbox/*.json` 에 같은 스키마로 커밋 → 다음 회고가 같은 validator 로 처리.
- 확인: https://poly.zowoo.uk (개요·24h 거래·시각화·연구·리포트), Slack `#polymarket-report`, 저장소 `reports/`·`reports/changes.md`.
