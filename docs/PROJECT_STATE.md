# polylab 프로젝트 상태 (세션 인계 문서)

새 세션·다른 AI 가 이 프로젝트를 이어받을 때 **가장 먼저 읽는 문서**다. 대화 문맥이 압축되거나 끊겨도 이 파일과
`reports/decisions.md`(연구자 결정 원본), `reports/attention.md`(열린 확인·결정 항목)만 읽으면 현재 상태를 복원할 수 있게 유지한다.
비밀값(키·지갑 주소)은 여기에 쓰지 않는다. 마지막 갱신: 2026-10-10.

## 최종 목적 (연구자)

- **1년 이상** Polymarket 스포츠 시장을 **1분 단위로 기록**해, 경기 시간대별 **과대·과소 평가**에 관한 논문을 쓴다.
- 그 편향을 이용한 **수익 전략**으로 논문 주장을 시스템적으로 증명한다(작은 수익이라도 실제 증명이 목표).
- 사람은 대시보드(https://poly.zowoo.uk)·Slack(#polymarket-report)·`reports/attention.md` 로 확인만 하고, 나머지는 자동화한다.

## 논문 구성 (현재)

| 장 | 질문 | 데이터·모듈 |
|---|---|---|
| RQ1 | 종목×경기 단계별 가격 vs 실현 확률(calibration gap) | core.db 1분 가격(과거 백필 포함), `analysis/calibration.py`, `/explore` |
| RQ2 | 같은 이벤트(득점) 민감도의 경기 시간별 변화 → "막판 변동성 때문에 손절 불가, 조기 익절" 의 근거 | game_states(실시간, 2026-09-30~), `analysis/events.py` |
| RQ3 | 편향을 이용한 전략의 비용 차감 후 수익 | 전략 원장, `analysis/backtest.py` |
| RQ4 | 거래 단위(5→10→25→50→100 USDC)별 안정성 | 단위 ladder, `analysis/performance.py` |
| 생애주기 장 | 축구 O/U 0.5 마켓의 Yes·No 매도 호가 합(=1+스프레드)과 Over 과대평가가 상장→킥오프→종료까지 어떻게 변하는가 | `data/ou05/`(O/U 0.5 만, 1분), `analysis/ou05.py`, `/ou05`, `docs/research/ou05-overround-study.md` |
| 가설(10-06 저녁) | **막판 선두 수렴**: 무승부 없는 승패 마켓에서 경기 전 ≤ X 경기의 선두가 T 이후 Y 를 넘으면 Z 까지 오르는가(역전 확률 vs 가격). 결과: 대부분 구간은 calibration, 원안(언제든 0.80→0.96)은 전 종목 −2.4~−3.4%, 양수는 종목 정규 길이 끝 무렵(NFL·NHL)만 | core.db 1분 가격(2024~), `docs/research/hypothesis-late-leader-convergence.md`, `late-leader-paper` |
| 0:0 과소평가(10-10, **논문 핵심**) | 프론티어 AI(Claude Opus 5.5·GPT-6.1-Sol, 시장 가격 비공개·웹 조사)가 Polymarket 보다 0:0 을 더 정확히 맞추는가(못 맞추거나 같으면 실패). 두 AI 가 모두 0:0 안 날/날 경기로 고른 5대 리그·MLS·UCL·UEL·네이션스리그 경기의 Over/Under 0.5 정산 적중률 vs 평균 매수가(내재 확률). 지정가(수수료 0)·손절 없음·5 USDC 실거래(red) | `research/llm_forecasts.db` `ou05_picks`(사전 등록 픽, ITT 분모), `strategies/ai-ou05-red.db`, `docs/research/ai-ou05-study.md` |
| (흡수됨) | 기존 LLM 0:0 예측 연구(v1, 10-02~10-09: Sonnet 4.6·추론 없는 GPT, AI 가 시장가를 봄)는 위 연구로 흡수 | `research/llm_forecasts.db`, `docs/research/llm-forecast-study.md` |

## 운영 구성 (Mac mini 192.168.50.23, Jenkins view `polylab`)

- 런타임: `/Volumes/t7/polylab/{repo,data,state,logs,archive}` — 외장하드 미마운트 시 fail closed. 비밀은 `~/.polylab/`.
- 모든 Jenkins 잡은 macOS TCC 때문에 `ssh polylab-local` 경유로 실행(`docs/ops/macmini-runbook.md`). `brew services restart jenkins` 금지.
- 주요 잡: tick(1분, 수집+전략), stream(WS), discover(10분), backfill(매시), publish(5분, git pull 포함), ou05(1분)/ou05-discover(매시),
  general(1분, cherry 용 일반 마켓)/general-discover, manual-sync(15분, 수동 베팅 기록), llm-forecast(10:00), retro daily(03:30·08:00·19:30)/weekly(월 08:30)/monthly(1일 09:00), storage-compact(매월).
- 저장 예산: 월 50GB 이내(최대 100GB). `polylab health` 가 영역별 정상 상태 증가(최근 7일 일별 중앙값, 백필 날·수집 설정 변경 전 날 제외)로 30일을 예측해 경고하고, 일회성 백필은 따로 보고한다. 2026-10-06 실측 정상 상태 약 5.7GB/월(일회성 백필 1.7GB 별도).
- AI 회고: Claude(`claude -p`) → 실패 시 Codex → 결정론. 제안은 validator·테스트 게이트를 통과해야 적용, 커밋 전 비밀값 정확값 검사.
- 연구자 결정은 `reports/decisions.md` 가 원본. AI 는 이를 전제로 판단하고, attention 항목에 대한 답도 여기 기록된다.

## 전략 현황 (자세한 값은 `strategies/*.yaml`, 근거는 `docs/research/backtests/`)

| 변형 | 계좌 | 요지 | 상태 |
|---|---|---|---|
| watermelon-cat / dog | cat / dog | 경기 중 고확률 favourite, 조기 익절 | 전 종목 paper(축구는 10-06 08:00 ladder 가 누적 손실로 live→paper 강등; NBA 는 두 시즌 백테스트로 우위 없음 → 10-21 live 취소) |
| apricot-eco / fruit | eco / fruit | 경기 후반 선두 | **eco NFL live 5 USDC**(10-06 연구자 결정, 시작 ~190분 뒤 선두 0.80–0.99, 0.96 익절, 백테스트 +2.76%·103건). fruit NFL(손절 arm)과 나머지 종목 paper |
| plum-king / queen | king / queen | 중간대 선두 추세, +0.03 조기 익절 | paper (paper ROI −3.0%·−0.9%, 연구자: live 금지, AI 실거래 경로 제외). 10-06 양수 탐색 88,680셀: NBA·NHL·MLB 없음. NFL 0.65–0.68·시작 60–120분·0.95 익절 셀(R1–R7 통과, 349건 +4.82%)을 paper 시험 중(queen 손절 없음, king 0.12). 축구 정산 보유 후보는 조기 익절 결정과 충돌해 보류 |
| late-leader-paper | 없음 | 막판 선두 수렴(10-06 저녁 신설): 경기 전 정배 ≤ X, 시작 T분 뒤 선두가 Y 교차 → Z 익절. NFL 170분·0.88→0.98·≤0.70, NHL 160분·0.80→0.98·≤0.60, NBA·MLB 약한 후보 | 전 종목 paper(자금 있는 여유 계좌 없음 → attention `account:late-leader-paper`). NFL 은 apricot-eco 와 진입 경기 37% 겹침 |
| cherry-blue / tiger | blue / tiger | 초기 개념: 종료 ~3일 전 0.9 매수→0.95 매도(전 카테고리) | paper (8만 조합 중 우위 없음) |
| goal-over-all | lion | 주요 리그 축구 Over 0.5 를 킥오프 3일 전~5분 전 지정가 매수, +0.02 익절(AI 조정 가능)·−10% 손절·0.99 보유 | live 5 USDC, maker 주문 |
| llm-nil-consensus / llm-nil-draw | – | Claude·ChatGPT 0:0 예측 합의 top-3 Over 0.5 | off(10-10 ai-ou05 로 흡수) |
| ai-ou05-red | red | 매일 10:00 두 AI 픽(9개 대회, 각자 P(0:0) 하위·상위 10위 공통) → Over(0.80–0.99)/Under(0.01–0.20) 지정가를 매수호가 한 틱 아래에 걸고 대기, 호가 합 ≤ 1.04 이고 낮은 순 우선, 정산 보유 | **paper**(10-10 오전 `ai-ou05:paper-first`: 자동 보고서가 연구자 보고서와 70% 이상 일치하면 Over 만 live 검토), 연구자 고정(`OWNER_LOCKED`: AI·ladder 변경 불가). red 는 수동 계좌와 공유 → 트랙 2 는 봇 체결을 거래 해시로 제외, 봇은 연구자 보유 조건 회피 |
| 수동 트랙 2 | red(메인)·wolf·eagle | 연구자 직접 베팅(AI 스킬), 공개 주소로 기록만 | O/U 0.5 만 집계 |

- **모든 변형·종목 5 USDC 고정**(10-06 저녁 `stake:freeze-5`: ladder 증액 동결·validator 5 초과 거부, 감액·paper 강등은 유지; 단위 확대는 나중에 별도 paper 연구). 종목별 mode·파라미터가 따로 있다.
- paper→live: 결정론 승격 게이트(`risk/promotion.py`) 또는 AI 제안 + retro 직접 재생 근거(2026-10-06 `promotion:ai-direct`). 종목 규칙
  `sample_rule`: 기본 재생 120일·n ≥ 40·paper ≥ 30, NFL 등 경기 수가 적은 종목 365일·n ≥ 20·paper ≥ 15, 비시즌은 365일 창.
  retro 재생은 모두 현재 수수료 0.05 강제. **2026-10-06 이전 retro 재생은 CLI 버그로 모두 실패했었다**(재조정·승격 증거가 한 번도 실제로 만들어지지 않음).
- 2026-10-06: NBA·NHL·NFL 과거 데이터를 2024 시즌까지 확장(NBA 3,030·NHL 2,600·NFL 808경기), 3 전략 종목별 재최적화, paper→live 자동 전환 게이트(결정론, `risk/promotion.py`) 도입.

## 이어서 할 일 / 열린 문제

- (해결 10-06) NFL 자동 전환 창: NFL 은 직전 365일·n ≥ 20·paper ≥ 15 로 낮췄다(`manual:nfl-promotion-window`).

- plum: NFL 후보 셀 paper out-of-sample 시험(다중 비교에 약함, 시즌당 약 120건). 축구 정산 보유 후보는 10-02 조기 익절 결정과 충돌 → 연구자 판단 대기(문서 5절).
- 저장 예측 정상 상태 표본이 1일뿐이다(raw lean 은 10-06 부분 일). **2026-10-13 부터** attention `reminder:storage-7d-steady-state` 와 회고 Slack "알림"(3일)이 7일 정상 상태를 자동 보고한다 → 연구자가 decisions.md 로 닫는다.
- late-leader: 계좌 배정 대기(attention `account:late-leader-paper`; 별칭을 받으면 yaml 에 연결). live 후보는 NFL 하나(R1–R7 통과). NHL 은 엔진 39건으로 n ≥ 40 에 1건 모자라고 120일 창 최대 16건이라 지금 규칙으로는 전환 불가(연구자 창 결정 필요). NBA·MLB 는 약한 후보. 2026-09-30 이후 `game_states` 점수로 "사건(득점) 뒤 교차" 정의와 비교할 표본을 쌓는 중.
- ai-ou05-red(10-10~): 첫 리그 주말에 예측 실행 시간(40경기, Opus 5.5 high + GPT-6.1-Sol high 웹 조사, 엔진 3000초 제한)·픽 수·체결률 확인. 한 엔진이 시간 초과하면 그날 픽이 없다 → 경기 묶음 분할 필요 여부 판단. 필요 표본 대략 Over 650건·Under 150건(`docs/research/ai-ou05-study.md` 5절). AI vs 시장 Brier 분석(v2 만)과 결과별 체결률은 표본이 쌓이면 회고에 자동화.
- Mac mini Claude Code 는 2.1.295(10-10 업데이트). Opus 5.5 는 2.1.280 이상 필요.
- O/U 0.5 생애 곡선: 2~3주 실시간 축적 후 판정(예비: 합 평균 7일+ 1.50 → 6~24시간 1.01).
- watermelon NBA 첫 20건의 실제 손절 체결가로 합성 호가 백테스트 낙관 여부 확인.
- 지정가(maker) 주문의 체결률·역선택 관찰 후 cherry 등 다른 전략 적용 검토.
- apricot: 손절 없는 구조의 꼬리 손실 → 새 가설 필요. 10-06 진입 0건 재조정 후보 28개(MLB·NFL) 통과 0건 — 진입 0건은 경기 수 문제.

## 세션 운영 규칙 (AI 용)

- 모든 사용자 응답은 **한국어**. 매 요청이 끝나면 요청과 최종 응답 전문을 `task-summaries/YYYY/MM/` 에 날짜별 md 로 남긴다(로컬 전용).
- 공개 저장소다. 키·funder 주소를 커밋하지 않는다(커밋 전 정확값 검사).
- 큰 변경 뒤에는 이 문서의 표와 "이어서 할 일"을 갱신한다.
