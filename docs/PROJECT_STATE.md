# polylab 프로젝트 상태 (세션 인계 문서)

새 세션·다른 AI 가 이 프로젝트를 이어받을 때 **가장 먼저 읽는 문서**다. 대화 문맥이 압축되거나 끊겨도 이 파일과
`reports/decisions.md`(연구자 결정 원본), `reports/attention.md`(열린 확인·결정 항목)만 읽으면 현재 상태를 복원할 수 있게 유지한다.
비밀값(키·지갑 주소)은 여기에 쓰지 않는다. 마지막 갱신: 2026-10-06.

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
| 부록 | LLM(Claude·ChatGPT) 교차검증 0:0 예측 vs 시장 | `research/llm_forecasts.db`, `docs/research/llm-forecast-study.md` |

## 운영 구성 (Mac mini 192.168.50.23, Jenkins view `polylab`)

- 런타임: `/Volumes/t7/polylab/{repo,data,state,logs,archive}` — 외장하드 미마운트 시 fail closed. 비밀은 `~/.polylab/`.
- 모든 Jenkins 잡은 macOS TCC 때문에 `ssh polylab-local` 경유로 실행(`docs/ops/macmini-runbook.md`). `brew services restart jenkins` 금지.
- 주요 잡: tick(1분, 수집+전략), stream(WS), discover(10분), backfill(매시), publish(5분, git pull 포함), ou05(1분)/ou05-discover(매시),
  general(1분, cherry 용 일반 마켓)/general-discover, manual-sync(15분, 수동 베팅 기록), llm-forecast(10:00), retro daily(03:30·08:00·19:30)/weekly(월 08:30)/monthly(1일 09:00), storage-compact(매월).
- 저장 예산: 월 50GB 이내(최대 100GB). `polylab health` 가 영역별 30일 증가를 예측해 경고.
- AI 회고: Claude(`claude -p`) → 실패 시 Codex → 결정론. 제안은 validator·테스트 게이트를 통과해야 적용, 커밋 전 비밀값 정확값 검사.
- 연구자 결정은 `reports/decisions.md` 가 원본. AI 는 이를 전제로 판단하고, attention 항목에 대한 답도 여기 기록된다.

## 전략 현황 (자세한 값은 `strategies/*.yaml`, 근거는 `docs/research/backtests/`)

| 변형 | 계좌 | 요지 | 상태 |
|---|---|---|---|
| watermelon-cat / dog | cat / dog | 경기 중 고확률 favourite, 조기 익절 | 축구 live, 그 외 종목 paper (NBA 는 두 시즌 백테스트로 우위 없음 → 10-21 live 취소) |
| apricot-eco / fruit | eco / fruit | 경기 후반 선두 | 전 종목 paper. NFL(시작 ~190분 뒤 선두 0.80–0.99, 0.96 익절)만 백테스트 통과(+2.76%, 103건) |
| plum-king / queen | king / queen | 중간대 선두 추세, +0.03 조기 익절 | paper |
| cherry-blue / tiger | blue / tiger | 초기 개념: 종료 ~3일 전 0.9 매수→0.95 매도(전 카테고리) | paper (8만 조합 중 우위 없음) |
| goal-over-all | lion | 주요 리그 축구 Over 0.5 를 킥오프 3일 전~5분 전 지정가 매수, +0.02 익절(AI 조정 가능)·−10% 손절·0.99 보유 | live 5 USDC, maker 주문 |
| llm-nil-consensus / llm-nil-draw | – | Claude·ChatGPT 0:0 예측 합의 top-3 Over 0.5 | paper |
| 수동 트랙 2 | red(메인)·wolf·eagle | 연구자 직접 베팅(AI 스킬), 공개 주소로 기록만 | O/U 0.5 만 집계 |

- 모든 변형은 5 USDC 에서 시작, 종목별 단위 ladder 로 증감(최대 100). 종목별 mode·단위·파라미터가 따로 있다.
- 2026-10-06: NBA·NHL·NFL 과거 데이터를 2024 시즌까지 확장(NBA 3,030·NHL 2,600·NFL 808경기), 3 전략 종목별 재최적화, paper→live 자동 전환 게이트(결정론, `risk/promotion.py`) 도입.

## 이어서 할 일 / 열린 문제

- NFL 은 시즌당 진입 경기가 적어 자동 전환 게이트의 '최근 120일 재생 40건' 조건을 못 넘는다 → 연구자 결정 대기(`manual:nfl-promotion-window`).

- O/U 0.5 생애 곡선: 2~3주 실시간 축적 후 판정(예비: 합 평균 7일+ 1.50 → 6~24시간 1.01).
- watermelon NBA 첫 20건의 실제 손절 체결가로 합성 호가 백테스트 낙관 여부 확인.
- 지정가(maker) 주문의 체결률·역선택 관찰 후 cherry 등 다른 전략 적용 검토.
- apricot: 손절 없는 구조의 꼬리 손실 → 새 가설 필요.

## 세션 운영 규칙 (AI 용)

- 모든 사용자 응답은 **한국어**. 매 요청이 끝나면 요청과 최종 응답 전문을 `task-summaries/YYYY/MM/` 에 날짜별 md 로 남긴다(로컬 전용).
- 공개 저장소다. 키·funder 주소를 커밋하지 않는다(커밋 전 정확값 검사).
- 큰 변경 뒤에는 이 문서의 표와 "이어서 할 일"을 갱신한다.
