# 부속 연구: LLM vs 시장 — "이 경기는 0-0 으로 끝나지 않는다" (paper 전용)

가제: *Can a web-searching LLM beat a prediction market on "no 0-0" soccer forecasts? A pre-registered paper-trading study*

실거래 없음. 모든 손익은 paper(가상 5 USDC)다. 코드: `src/polylab/research/llm_forecast.py`(예측·저장·결과),
`src/polylab/research/llm_eval.py`(채점), `src/polylab/strategies/llm_nil.py` + `strategies/llm-nil-draw.yaml`(paper 전략),
프롬프트 `prompts/llm_forecast.md`, Jenkins `polylab-llm-forecast`(매일 10:00 KST).

## 1. 배경과 가설

저자는 매일 아침 AI 에게 "0-0 이 나지 않을 주요 리그 경기 1–3개"를 물어 왔다(전력 차·폼·감독 성향·경기 중요도·상대 전적 0-0 이력).
이 습관을 측정 가능한 실험으로 바꾼다.

- **H1 (정확도)**: LLM 의 `P(not 0-0)` 는 같은 시각 Polymarket Total 0.5 goals **Over mid** 보다 Brier score 가 낮지 않다.
  (귀무: 차이 없음. 효율적 시장 가정에서는 LLM 이 시장을 이기지 못할 것으로 예상한다.)
- **H2 (기준선)**: LLM 은 리그 평균 Poisson 기준선 `P(not 0-0) = 1 − exp(−λ_league)` 보다 Brier 가 낮다.
- **H3 (순위)**: LLM top-3 경기의 실제 "0-0 아님" 비율은 전체 예측 경기의 비율보다 높다.
- **H4 (거래)**: `AI 확률 − ask ≥ edge` 조건의 Over 0.5 paper 매수는 수수료·체결 조건을 반영해도 양의 기대값이 아니다(효율적 시장).

## 2. 절차 (매일 10:00 KST)

1. `polylab forecast daily` 가 먼저 지난 예측의 결과를 채운다(resolve).
2. 대상: `MAJOR_SOCCER_LEAGUES` 경기 중 킥오프가 (지금+20분, 지금+30시간] 인 경기, 최대 12개.
   core.db 에 Total 0.5 Over 토큰이 있는 경기를 먼저, 나머지는 결과 마켓 거래량 순. Over 시장이 없는 경기도 예측한다
   (무·승 가격은 맥락으로만 제공하며 "시장가"로 채점하지 않는다).
3. 시장가: CLOB `POST /books`(공개·읽기 전용) 실시간 호가 → 없으면 저장된 호가(1시간 이내) → 1분 가격(3시간 이내).
4. AI: claude(`-p`, Read/Write/Edit + WebSearch/WebFetch 만. Bash·Grep·Glob 없음, 홈 디렉터리 읽기 금지, 쓰기는 context 폴더만)
   → 실패 시 codex(`workspace-write` 샌드박스, shell 네트워크 off). codex 의 web search 는 `POLYLAB_FORECAST_CODEX_WEB=1` 일 때만 켠다
   (codex 샌드박스는 `~/.polylab` 을 읽을 수 있어 웹 입력 + 검색 도구가 유출 경로가 될 수 있음). 산출물 `forecasts.json` 만 받는다.
   Grep 은 `Read(~/**)` 거부를 절대경로로 우회했다(2026-10-01 Mac mini canary). 그래서 도구 목록에서 뺐다.
5. 검증 후 **킥오프 이전** 행만 `research/llm_forecasts.db` 에 append. 그 뒤 Slack 한 줄 요약(top-3, AI vs 시장, 면책 문구).

## 3. 사전 등록 (pre-registration) — 분석 전에 고정

| 항목 | 정의 |
|---|---|
| 분석 단위 | 경기 1개. 한 경기의 **정식 예측(canonical)** = 킥오프 이전에 생성된 가장 최근의 성공 run 의 예측 (`canonical_forecasts`) |
| 목표 변수 | `y = 1` if 정규 시간 종료 시 0-0 아님. 1순위 Total 0.5 시장 정산(Over 승 = 1), 없으면 종료 3시간 이상 지난 최종 스코어 |
| 예측자 | AI `p_not_0_0`; 시장 = 예측 시각 Over 0.5 mid(양쪽 호가 있을 때만); Poisson = `1 − exp(−λ)`, λ = 예측 시각 이전 해당 리그 종료 경기 평균 득점(≥30경기), 아니면 상수 2.75 |
| 1차 지표 | Brier score. 시장 비교는 시장가가 있는 경기 부분집합에서 **짝지은(paired)** 차이 `Brier(AI) − Brier(시장)`, 경기 단위 bootstrap 95% CI(2000회, seed 11) |
| 2차 지표 | log-loss(확률 [1e-4, 1−1e-4] clip), calibration 구간 [0,.80,.85,.90,.93,.96,1], top-3 적중률, top-3 가상 손익(예측 시각 ask 로 $5, 수수료 제외), `llm-nil-draw` paper 원장 손익 |
| 표본 | 결론은 결과 확인 경기 n ≥ 100(시장 부분집합 n ≥ 50) 이후에만. 그 전에는 기술 통계만 보고 |
| 보고 | 주간·월간 리포트 "LLM vs 시장" 절과 retro context pack `llm_forecast_eval.json`(기간 + 누적) |

## 4. 편향 통제

- **Look-ahead 금지**: AI 실행이 끝난 시각(`created_at`)이 킥오프 이후인 예측은 저장하지 않는다. 채점은 킥오프 후 확정 결과만.
- **불변 기록**: `runs`·`forecasts`·`outcomes` 는 SQLite trigger 로 UPDATE/DELETE 를 거부한다(append-only). 실패한 run 도 기록한다.
- **재실행 cherry-picking 방지**: 같은 KST 날짜에 성공 run 이 있으면 재실행을 거부한다. `--force` 재실행은 `runs.forced=1` 로 남고,
  정식 예측 규칙(가장 최근 run)에 따라 이전 예측을 대체하므로 보고 시 forced 건수를 함께 밝힌다.
- **프롬프트·입력 고정**: `runs.prompt_sha`(프롬프트 해시)와 `context_sha`(입력 경기·가격 해시), 엔진·모델명을 저장한다.
  프롬프트를 바꾸면 해시가 달라지므로 프롬프트 버전별로 나눠 분석한다.
- **시장가 노출(anchoring)**: AI 는 시장가를 참고 정보로 본다(저자의 실제 사용 방식). 따라서 H1 은 "시장 정보를 본 LLM 이
  시장에 정보를 더하는가"를 검정한다. AI 가 웹에서 배당을 찾을 수도 있으므로 완전한 맹검은 불가능하며, 이를 한계로 적는다.
- **환각 통제**: 프롬프트가 출처 URL 없는 수치를 금지한다. 저장 시 http(s) URL 만 남기고(경기당 ≤12), 출처 수를 함께 보고한다.
  출처 0 인 예측의 성능을 별도로 비교할 수 있다.
- **선택 편향**: 대상은 수집 중인 주요 대회뿐이며, Total 0.5 시장은 거래량 하한(`POLYLAB_GOAL_MIN_VOLUME`, 기본 1만 USDC)을
  넘은 경기만 core.db 에 있다. 시장 비교 부분집합은 "거래가 있는 경기"로 치우친다.
- **다중 비교**: 1차 지표는 Brier 하나로 고정한다. 리그·확신도별 분석은 탐색적이라고 명시한다.

## 5. paper 전략 `llm-nil-draw`

- 대상: 정식 예측의 top-k(`top_k`, 기본 3) 경기. 킥오프 전 `entry_window_minutes`(기본 15분, Over 호가는 킥오프 15분 전부터만
  1분 수집) 안에서 `AI 확률 − ask VWAP ≥ edge`(기본 0.02), `min_price ≤ ask ≤ max_price`(0.50–0.96) 이면 Over 0.5 를
  $5 paper 매수하고 정산까지 보유(청산 없음). Over 토큰은 진입 시점에 game_key 로 다시 찾는다.
- **실거래 금지**: `llm_nil` family 는 `mode: live` 를 코드에서 거부하고, autopilot validator 는 AI 가 live 로 올리는 것을 막는다.
  autopilot 은 bounds 안에서 `top_k`·`edge`·`max_price`·`min_price`·`entry_window_minutes` 만 조정할 수 있다.

## 6. 운영 명령

```
uv run polylab forecast daily            # resolve + run + Slack (Jenkins)
uv run polylab forecast run [--force] [--max-games N] [--timeout S]
uv run polylab forecast resolve
uv run polylab forecast eval [--since YYYY-MM-DD]
```

## 7. 알려진 한계 (2026-10-01)

- core.db 의 Total 0.5 시장이 아직 매우 적다(수집 시작 직후, 거래량 하한). 초기에는 시장 비교·paper 거래 n 이 거의 0 이고
  대부분 Poisson 기준선 비교만 가능하다. 필요하면 수집기 `POLYLAB_GOAL_MIN_VOLUME` 하향을 검토한다(이 연구의 범위 밖).
- 최종 스코어 fallback 은 Gamma 점수이므로 컵 대회 연장전 득점이 포함될 수 있다(시장 정산이 있으면 그것을 우선).
- **실행 간 분산**: 같은 경기·같은 입력으로 약 10분 간격 두 번 실행한 결과, top-3 가 1경기만 겹쳤고 개별 확률이 최대 ±0.05 움직였다
  (2026-10-01 scratch 시험). 이 폭은 `edge`(0.02)보다 크다. 분석 시 run 간 분산을 별도로 보고하고, H3·전략 결과를 해석할 때 고려한다.
- **모델 미고정**: 기본은 claude CLI 기본 모델이다(시험 시 sonnet-4-6 + 보조 haiku). `runs.model` 에 기록되지만 교란 변수다.
  고정하려면 `POLYLAB_RETRO_CLAUDE_MODEL` 을 쓰는데, 이 변수는 retro 와 공유된다.
