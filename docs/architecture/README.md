# polylab 아키텍처 (다이어그램)

논문 부록·발표용 시스템 구조도. 모든 그림은 GitHub에서 바로 렌더링되는 mermaid 로 작성했다.
텍스트 설계 명세는 [`../ARCHITECTURE.md`](../ARCHITECTURE.md), AI 자동화 상세는 [`ai-automation.md`](ai-automation.md).

## 1. 시스템 구성 (System context)

```mermaid
flowchart TB
    subgraph L1["① 데이터 원천 — Polymarket 공개 API"]
        direction LR
        GAMMA["Gamma API<br/>경기·마켓"] ~~~ CLOB["CLOB API<br/>호가·가격이력·주문"] ~~~ DATA["Data API v2<br/>체결·포지션·정산"] ~~~ WS["Sports / Market<br/>WebSocket"]
    end
    subgraph L2["② 수집 — Mac mini · Jenkins (VPN 24h)"]
        direction LR
        COL["collector<br/>discover · poll(1분) · stream · backfill"]
    end
    subgraph L3["③ 저장 — 외장하드 /Volumes/t7"]
        direction LR
        CORE[("core.db<br/>공용 시장 데이터")] ~~~ BOOKS[("books/*.db<br/>호가 스냅샷")] ~~~ RAW[("raw/*.jsonl.gz<br/>WS 원본")] ~~~ STRAT[("strategies/*.db<br/>전략별 원장")]
    end
    subgraph L4["④ 판단 — 전략 실행 · 분석 · AI 회고"]
        direction LR
        ENG["engine + strategies<br/>매 1분 · FOK 주문"] ~~~ ANA["analysis<br/>calibration · event study"] ~~~ AUTO["autopilot<br/>claude → codex · validator"]
    end
    subgraph L5["⑤ 출력 — 공개·알림"]
        direction LR
        GH["GitHub 공개 저장소<br/>전략 yaml · 리포트"] ~~~ DB["poly.zowoo.uk<br/>Supabase Storage + Cloudflare"] ~~~ SL["Slack<br/>회고 요약 · 이상 알림"]
    end
    L1 --> L2 --> L3 --> L4 --> L5
```

되먹임 경로(그림에서 생략): ④ 전략 엔진은 ① CLOB 에 FOK 주문을 보내고, ⑤ GitHub 에 커밋된 새 파라미터(`strategies/*.yaml`)는
5분마다 Mac mini checkout 으로 `git pull` 되어 다음 1분 tick 부터 ④에 적용된다(4번 그림).

## 2. 데이터 모델 (공용 DB + 전략별 원장)

```mermaid
erDiagram
    GAMES ||--o{ MARKETS : "game_key"
    MARKETS ||--|{ TOKENS : "condition_id"
    TOKENS ||--o{ PRICE_BARS : "1분 가격 (poll_mid / ws_last / history)"
    TOKENS ||--o{ BOOK_SNAPSHOTS : "호가 top-10 (books/YYYY-MM.db)"
    GAMES ||--o{ GAME_STATES : "점수·피리어드·경기 분"
    MARKETS ||--o{ PUBLIC_TRADES : "공개 체결"
    MARKETS ||--o{ MARKET_METRICS : "거래량·OI"

    PARAM_VERSIONS ||--o{ POSITIONS : "param_version"
    POSITIONS ||--o{ ORDERS : "position_id"
    ORDERS ||--o{ FILLS : "intent_id (CONFIRMED만 실손익)"
    POSITIONS }o--|| TOKENS : "token_id (논리 참조)"

    GAMES {
        text game_key PK
        text sport
        text league
        int start_time
        text status
        int home_score
        int away_score
    }
    MARKETS {
        text condition_id PK
        text market_type "moneyline/draw/total/spread..."
        real line
        int resolved_outcome_index
    }
    PRICE_BARS {
        text token_id
        int ts "분 단위 UTC"
        text source
        real price
    }
    POSITIONS {
        text position_id PK
        real stake_usdc "5→100 ladder"
        real entry_price
        text status
        real realized_pnl
    }
```

## 3. 1분 tick (수집 → 전략 → 주문 → 대사)

```mermaid
sequenceDiagram
    autonumber
    participant J as Jenkins (매 1분)
    participant P as collector.poll
    participant C as core.db / books
    participant S as 전략 변형 ×8
    participant R as risk (caps·kill switch)
    participant X as execution (CLOB)
    participant L as strategies/<id>.db

    J->>P: polylab tick
    P->>C: 라이브·임박 경기 호가, 1분 가격, 게임 상태
    loop 변형마다 (실패해도 다음 변형 계속)
        S->>L: 미확정 주문 대사 (CONFIRMED fill · fee)
        S->>C: 정산 확인 → resolution 손익
        S->>C: 청산 신호 (TP · 시간 청산 · 비상 손절)
        S->>C: 진입 신호 (가격대 · 경기 시간 · 리그 · 거래량)
        S->>R: 한도 · 일일 손실 · 킬스위치 확인
        R-->>S: 허용 / 거부
        S->>X: 주문 직전 호가 재확인 후 FOK
        X->>L: intent 선기록 → 체결 결과 기록
    end
    S->>X: (시간당 1회) 원장 소유 정산 포지션 redeem
```

## 4. AI 회고 루프 (재귀 개선)

```mermaid
flowchart TD
    T(["Jenkins 스케줄<br/>일일 03:30·08:00·19:30 / 주간 월 08:30 / 월간 1일 09:00"]) --> A
    A["① 결정론 리포트<br/>24h 경기·확률 움직임·거래·손익"] --> B
    B["② 결정론 단위 ladder<br/>5→10→25→50→100 게이트"] --> C
    C["③ context pack 생성<br/>지표·거래·현재 yaml·탐색 경계·백테스트"] --> D{"④ AI 엔진 체인"}
    D -- "1차" --> E1["claude -p<br/>파일 도구만, 쓰기는 context 폴더로 제한"]
    D -- "실패 시" --> E2["codex exec<br/>workspace-write 샌드박스, 네트워크 off"]
    D -- "둘 다 실패" --> E3["AI 생략 (결정론만)"]
    E1 & E2 --> F["narrative.md + proposal.json"]
    F --> W{"주간이면<br/>다른 엔진이 second opinion<br/>(거부만 가능)"}
    W --> V
    E3 --> V
    V{"⑤ validator<br/>탐색 경계·변경 폭·최소 표본 20·cooldown 3일<br/>증액은 ladder 게이트 통과 시만"}
    V -- "통과" --> Y["⑥ strategies/*.yaml 적용"]
    V -- "거부" --> Z["거부 사유를 리포트에 기록"]
    Y --> TST{"pytest 게이트"}
    TST -- "실패" --> RV["yaml 원복"]
    TST -- "통과" --> G
    Z --> G
    RV --> G
    G["⑦ reports/ 커밋 · 비밀값 정확값 검사 · push"] --> H["publish → 대시보드"] --> I["Slack 요약"]
    Y -. "다음 1분 tick 부터 새 파라미터 적용" .-> N(["실거래 → 새 데이터"])
    N -. "다음 회고의 근거" .-> A
```

## 5. 하루 운영 일정 (KST)

```mermaid
timeline
    title polylab 하루 (KST)
    상시 : tick 매 1분 (수집·전략·주문) : stream WebSocket 상시 수집 : publish 5분 (대시보드·이상 알림) : discover 10분
    매시 : backfill (종료 경기 1분 가격·체결·정산, 과거 시즌)
    03 30 : 새벽 일일 회고 + 연구 분석 갱신
    08 00 : 아침 일일 회고 : 월요일 08 30 주간 회고
    09 00 : 매월 1일 월간 회고 (논문 요약)
    19 30 : 저녁 일일 회고
```

## 6. 주문 단위 ladder (논문 RQ4)

```mermaid
stateDiagram-v2
    [*] --> S5: 모든 변형은 5 USDC 에서 시작
    S5 --> S10: 정산 20건↑ · 순손익>0 · ROI 80% 하한>0 · 낙폭<6×단위
    S10 --> S25: 동일 게이트 (단위 변경 후 3일 cooldown)
    S25 --> S50: 동일 게이트
    S50 --> S100: 동일 게이트 (상한 100)
    S100 --> S50: 최근 20건 손실 · ROI 하한<0
    S50 --> S25: 감액 게이트
    S25 --> S10: 감액 게이트
    S10 --> S5: 감액 게이트
    S5 --> Paper: 5 USDC 에서 40건↑ 누적 손실
    Paper --> [*]: AI 가 폐기 (retire)
    S5: 5 USDC
    S10: 10 USDC
    S25: 25 USDC
    S50: 50 USDC
    S100: 100 USDC
    Paper: paper (모의)
```

## 7. 저장소 구조

```mermaid
flowchart LR
    ROOT["golden-burger (public)"] --> SRC["src/polylab"]
    ROOT --> STY["strategies/*.yaml<br/>변형 레지스트리 (AI 가 수정)"]
    ROOT --> PR["prompts/<br/>daily·weekly·monthly·review"]
    ROOT --> REPS["reports/<br/>일일·주간·월간 회고 (자동 커밋)"]
    ROOT --> DASH["dashboard/<br/>Next.js on Cloudflare"]
    ROOT --> JK["jenkins/<br/>jobs.yaml · sync_jobs.py"]
    ROOT --> DOC["docs/<br/>architecture · research · strategies · ops"]
    SRC --> S1["api · collector"]
    SRC --> S2["strategies · engine · risk · execution"]
    SRC --> S3["analysis · reports · publish"]
    SRC --> S4["autopilot · ops"]
```

## 도구 스택

| 영역 | 도구 |
|---|---|
| 스케줄러 | Jenkins 2.461 (Mac mini, launchd), 잡 정의 코드화 `jenkins/jobs.yaml` |
| 언어·패키지 | Python 3.13, uv, pytest |
| 데이터 | SQLite(WAL) on 외장 SSD, parquet(연구 export), gzip JSONL(WebSocket 원본) |
| 시장 API | Polymarket Gamma · CLOB · Data API v2 · Sports/Market WebSocket, `py-clob-client-v2`, `polymarket-client` |
| AI | Claude Code CLI(`claude -p`, 1차), OpenAI Codex CLI(`codex exec`, 2차·검토) |
| 배포·가시화 | Supabase Storage(read model), Next.js + OpenNext on Cloudflare Workers, Slack incoming webhook |
| 형상 관리 | GitHub 공개 저장소(autopilot 은 deploy key 로 push) |
