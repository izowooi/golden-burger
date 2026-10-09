# 부속 연구: AI 교차검증 vs 시장 — "이 경기는 0-0 으로 끝나지 않는다"

가제: *Do two independent LLMs agree better than a prediction market on "no 0-0" soccer forecasts? A pre-registered study*

코드: `src/polylab/research/llm_forecast.py`(두 엔진 예측·합의·저장·결과), `src/polylab/research/llm_eval.py`(채점),
`src/polylab/strategies/llm_nil.py`(`llm-nil-draw` Claude 단독 대조군 paper, `llm-nil-consensus` 합의 arm),
`src/polylab/strategies/goal_over.py`(`goal-over-all` 기준선), 프롬프트 `prompts/llm_forecast.md`,
Jenkins `polylab-llm-forecast`(매일 10:00 KST).

**2026-10-03 설계 변경(track 1, thesis owner)**: 단일 엔진(claude → codex fallback)을 **두 엔진 독립 예측 + 사전 등록 합의**로
바꾸고, 시장 기준선 `goal-over-all` 과 live 허용 arm `llm-nil-consensus` 를 추가했다. 세 변형 모두 계좌 지정 전까지 `mode: paper`,
`account: null` 이다. live 전환은 사람만 하는 한 줄 변경(`mode: live` + `account: <alias>`)이며 `llm-nil-draw` 는 계속 paper 전용이다.
프롬프트를 top-3 → top-5 순위로 바꿨으므로 `prompt_sha` 가 달라졌다. 2026-10-03 이전 run 은 별도 프롬프트 버전으로 나눠 분석한다.

**2026-10-10 변경(`ai-ou05:red-live`)**: 같은 실행이 O/U 0.5 픽(유럽 5대 리그+MLS, 두 엔진 모두 자기 P(0:0) 하위·상위 8위)을
`ou05_picks` 에 고정 기록하고 `ai-ou05-red`(red, 실거래, 지정가)가 그 픽을 산다(`docs/research/ai-ou05-study.md`). 이에 따라
경기 상한 30 → **40**, 6개 리그 경기를 목록 앞에 둔다(상한에 잘리지 않게), 엔진 시간 제한 1500 → 3000초, Jenkins 시간 제한 60 → 110분.
프롬프트는 바꾸지 않았다(`prompt_sha` 동일). 2026-10-10 이후 run 은 경기 구성이 달라졌으므로 분석 시 기간을 나눈다.

**2026-10-10 프로토콜 v2 + 흡수(`ai-ou05:protocol-v2`, `ai-ou05:absorb-llm-nil`)**: 모델 고정(Claude Opus 5.5 effort high,
GPT-6.1-Sol reasoning high — 그전에는 고정이 없어 Claude 는 계정 기본 Sonnet 4.6, codex 는 `--ignore-user-config` 기본값 GPT-6.1-Sol
추론 없음으로 돌았다), ChatGPT 웹 검색 on(비밀 차단 sandbox-exec 안), **AI 에게 시장 가격 비공개**, 조사 6항목 구조화 프롬프트.
`llm-nil-draw`·`llm-nil-consensus` 는 off 로 두고 이 연구는 `docs/research/ai-ou05-study.md` 로 흡수했다. 예측·합의·채점 기록은 계속 쌓이며,
v1(2026-10-02~10-09) 과 v2 는 따로 분석한다.

## 1. 배경과 가설

저자는 매일 아침 AI 에게 "0-0 이 나지 않을 주요 리그 경기 1–3개"를 물어 왔다(전력 차·폼·감독 성향·경기 중요도·상대 전적 0-0 이력).
이 습관을 측정 가능한 실험으로 바꾼다.

- **H1 (정확도)**: LLM 의 `P(not 0-0)` 는 같은 시각 Polymarket Total 0.5 goals **Over mid** 보다 Brier score 가 낮지 않다.
  (귀무: 차이 없음. 효율적 시장 가정에서는 LLM 이 시장을 이기지 못할 것으로 예상한다.)
- **H2 (기준선)**: LLM 은 리그 평균 Poisson 기준선 `P(not 0-0) = 1 − exp(−λ_league)` 보다 Brier 가 낮다.
- **H3 (순위)**: LLM top-3 경기의 실제 "0-0 아님" 비율은 전체 예측 경기의 비율보다 높다.
- **H4 (거래)**: `AI 확률 − ask ≥ edge` 조건의 Over 0.5 paper 매수는 수수료·체결 조건을 반영해도 양의 기대값이 아니다(효율적 시장).
- **H5 (교차검증)**: Claude·ChatGPT 합의 P(0-0) 는 각 단일 엔진보다 Brier 가 낮고, 합의 top-3 의 0-0 비율은 단일 엔진 top-3 보다 낮다.
- **H6 (선별 가치)**: 합의 arm(`llm-nil-consensus`)의 거래당 손익은 같은 기간 기계적 전량 매수 기준선(`goal-over-all`)보다 높다.

## 2. 절차 (매일 10:00 KST)

1. `polylab forecast daily` 가 먼저 지난 예측의 결과를 채운다(resolve).
2. 대상: `MAJOR_SOCCER_LEAGUES` 경기 중 킥오프가 (지금+20분, 지금+30시간] 인 경기 전부(상한 30, 2026-10-03 이전 12).
   core.db 에 Total 0.5 Over 토큰이 있는 경기를 먼저, 나머지는 결과 마켓 거래량 순. Over 시장이 없는 경기도 예측한다
   (무·승 가격은 맥락으로만 제공하며 "시장가"로 채점하지 않는다).
3. 시장가: CLOB `POST /books`(공개·읽기 전용) 실시간 호가 → 없으면 저장된 호가(1시간 이내) → 1분 가격(3시간 이내).
4. AI(한 batch = 두 엔진, **fallback 없음**): 같은 context 를 엔진별 폴더(`state/forecast/<stamp>/{claude,codex}`)에 복사해
   **같은 프롬프트로 병렬·독립** 실행한다(서로의 산출물을 보지 않는다). 각 엔진은 모든 경기의 `p_not_0_0`·요인·출처와 자기 top-5 순위를 낸다.
   - Claude: `claude -p`, Read/Write/Edit + WebSearch/WebFetch 만. Bash·Grep·Glob 없음, 홈 디렉터리 읽기 금지, 쓰기는 context 폴더만.
     Grep 은 `Read(~/**)` 거부를 절대경로로 우회했다(2026-10-01 Mac mini canary). 그래서 도구 목록에서 뺐다.
   - ChatGPT: `codex exec`(`workspace-write` 샌드박스, shell 네트워크 off). web search 는 기본 **off**, `POLYLAB_FORECAST_CODEX_WEB=1`
     일 때만 켠다(codex 샌드박스는 `~/.polylab` 을 읽을 수 있어 웹 입력 + 검색 도구가 유출 경로가 될 수 있음).
   - 엔진별로 검증 후 **킥오프 이전** 행만 run `<batch>-claude` / `<batch>-codex` 로 `research/llm_forecasts.db` 에 append.
     한 엔진이 실패하면 그 엔진의 run 만 `failed` 로 남는다.
5. 합의(사전 등록, 아래 3절)를 `consensus_batches`·`consensus` 에 append(두 엔진 중 하나라도 실패하면 그 batch 는 `empty`).
6. Slack(한국어): 합의 top-3 의 Claude·ChatGPT P(0:0), 합의 P(0:0), 시장 내재 P(0:0)=1−Over 0.5 ask, `llm-nil-consensus` paper/live 상태.

## 3. 사전 등록 (pre-registration) — 분석 전에 고정

| 항목 | 정의 |
|---|---|
| 분석 단위 | 경기 1개. 엔진별 **정식 예측(canonical)** = 그 엔진의 킥오프 이전 가장 최근 성공 run 의 예측 (`canonical_forecasts(engine=)`). 2026-10-03 이전 codex fallback run 은 codex 로 집계된다 |
| 합의 규칙 | 같은 batch 에서 두 엔진이 모두 예측한(각 엔진 완료 시각과 합의 시각 모두 킥오프 이전) 경기마다 P(0-0)=1−p_not_0_0. 합의 P(0-0) = 두 값의 **평균**. 두 엔진이 **모두 자기 top-5** 에 넣은 경기만 자격. 순위 = 자격 경기를 (합의 P(0-0) 오름차순, 두 순위 중 나쁜 쪽, 킥오프, game_key) 로 정렬, 상위 3 = 합의 픽. 엔진이 순위 목록을 주지 않으면 자기 확률 순으로 top-5 를 만든다(`top_derived` 기록). 한 엔진 실패 → batch `empty`(픽 없음, 사유 기록). 규칙은 `CONSENSUS_RULE` 로 batch 마다 저장 |
| 정식 합의 | 경기마다 그 경기를 포함한 가장 최근 `ok` batch 의 행(킥오프 이전 생성, `canonical_consensus`). `empty` batch 는 이전 batch 의 행을 취소하지 않는다(30시간 창이라 한 경기가 두 batch 에 들어갈 수 있음) |
| 목표 변수 | `y = 1` if 정규 시간 종료 시 0-0 아님. 1순위 Total 0.5 시장 정산(Over 승 = 1), 없으면 종료 3시간 이상 지난 최종 스코어 |
| 예측자 | AI `p_not_0_0`; 시장 = 예측 시각 Over 0.5 mid(양쪽 호가 있을 때만); Poisson = `1 − exp(−λ)`, λ = 예측 시각 이전 해당 리그 종료 경기 평균 득점(≥30경기), 아니면 상수 2.75 |
| 1차 지표 | Brier score. 시장 비교는 시장가가 있는 경기 부분집합에서 **짝지은(paired)** 차이 `Brier(AI) − Brier(시장)`, 경기 단위 bootstrap 95% CI(2000회, seed 11) |
| 2차 지표 | log-loss(확률 [1e-4, 1−1e-4] clip), calibration 구간 [0,.80,.85,.90,.93,.96,1], Claude/ChatGPT/합의 top-3 의 0-0 비율과 가상 손익(예측 시각 ask 로 $5, 수수료 제외), 공동 예측 경기에서 Claude·ChatGPT·합의·시장·Poisson Brier 와 짝지은 차이(합의−시장, Claude−ChatGPT), 원장 손익 `llm-nil-draw`·`llm-nil-consensus`·`goal-over-all` 을 **paper 와 live 분리**(live 는 CONFIRMED fill·확인된 정산만) |
| 표본 | 결론은 결과 확인 경기 n ≥ 100(시장 부분집합 n ≥ 50) 이후에만. 그 전에는 기술 통계만 보고 |
| 보고 | 주간·월간 리포트 "AI 교차검증 0:0 연구" 절과 retro context pack `llm_forecast_eval.json`(기간 + 누적) |

## 4. 편향 통제

- **Look-ahead 금지**: AI 실행이 끝난 시각(`created_at`)이 킥오프 이후인 예측은 저장하지 않는다. 채점은 킥오프 후 확정 결과만.
- **불변 기록**: `runs`·`forecasts`·`outcomes`·`consensus_batches`·`consensus` 는 SQLite trigger 로 UPDATE/DELETE 를 거부한다
  (append-only). 실패한 run 과 `empty` 합의 batch 도 기록한다.
- **엔진 독립성**: 두 엔진은 같은 context(같은 `context_sha`)·같은 프롬프트를 각자 폴더에서 동시에 받는다. 서로의 답을 보지 않는다.
- **엔진 비대칭(교란)**: 기본 설정에서 Claude 는 웹 검색을 쓰고 ChatGPT 는 쓰지 않는다. 따라서 Claude−ChatGPT 차이는 "모델 차이"가
  아니라 "웹 검색 + 모델" 차이다. `POLYLAB_FORECAST_CODEX_WEB=1` 로 바꾸면 그날부터 별도 조건으로 나눠 분석한다.
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

## 5. 전략 arm

| 변형 | family | 선택 | 진입 | 청산 | 모드 |
|---|---|---|---|---|---|
| `llm-nil-draw` (대조군) | llm_nil, `source: claude` | Claude 정식 예측 top-k(3) | `Claude P(not 0-0) − ask VWAP ≥ edge`(0.02), ask 0.50–0.96 | 정산까지 보유 | **paper 전용**(코드가 live 거부) |
| `llm-nil-consensus` | llm_nil, `source: consensus` | 정식 합의 순위 top-k(3) | `시장 내재 P(0-0) − 합의 P(0-0) ≥ edge`(기본 0.0), 내재 P(0-0)=1−ask VWAP, ask 0.50–0.985 | 보유(TP/SL 파라미터 있음, 기본 off) | paper(계좌 지정 시 live 허용) |
| `goal-over-all` (기준선) | goal_over | 범위 리그 **전 경기**, 경기당 1회 | 킥오프 60→5분 전, $5 전체 호가 walk ask 0.50–0.985 | 보유(TP/SL 기본 off) | paper(계좌 지정 시 live 가능) |

- 공통: Over 토큰은 진입 시점에 game_key 로 다시 찾는다. stake 5 USDC(ladder). 진입가 band 는 엔진의 fresh-book 재확인으로 다시 검사한다.
- **수집 창**: Over 0.5 호가는 킥오프 15분 전부터만 1분 수집된다(pre-game 저빈도 스냅샷은 moneyline/draw 만). `book_max_age_s` 120 이므로
  `goal-over-all` 의 60→5분 창은 실제로 약 15→5분이다. 창을 실제로 넓히려면 수집기 poll lead 를 바꿔야 한다(이 연구 범위 밖).
- **임계 청산**(`take_profit_price`, `stop_loss_price`; null=off, 진입 시 exit_rules 에 고정): TP 는 보유 전량 bid VWAP ≥ 값이고 매수·매도
  수수료 후 순이익일 때만(`base.net_positive_tp_check`, 수수료 스케줄 미상이면 불발). SL 은 best bid ≤ 값, 스프레드 ≤ `max_stop_spread`.
  Over 0.5 는 골이 나는 순간 사실상 정산(bid ~0.99+)되므로 0.99 근처 TP 는 추가 수익이 아니라 정산·redeem 전에 자본을 몇 시간 일찍
  회수하는 장치다. autopilot 은 bounds 안에서 null→값으로 켤 수 있으나 null 로 되돌리는 것은 사람만 한다.
- **경기 중 진입**(`allow_in_play`, 기본 false): 킥오프 후 `in_play_max_minutes` 이내, 300초 이내 game_state 가 0-0·진행 중일 때만.
- **live 가드**: `llm_nil` family 는 `id == llm-nil-consensus` 이고 `source == consensus` 일 때만 `mode: live` 를 허용한다(2026-10-03 thesis
  owner 결정). 그 외(`llm-nil-draw`, autopilot 이 복제한 변형 포함)는 코드에서 live 를 거부한다. autopilot validator 는 어떤 변형도 live 로
  올리지 못한다. live 전환은 사람이 yaml 의 `mode: live` 와 자금 있는 `account` alias 를 함께 바꾸는 한 줄 변경이다.

## 6. 운영 명령

```
uv run polylab forecast daily            # resolve + 두 엔진 run + 합의 + Slack (Jenkins)
uv run polylab forecast run [--force] [--max-games N] [--timeout S]
uv run polylab forecast resolve
uv run polylab forecast eval [--since YYYY-MM-DD]
```

## 7. 알려진 한계 (2026-10-01, 10-03 보강)

- **실행 시간**: 두 엔진을 병렬로 돌리며 엔진당 timeout 1500초. 경기 수 상한 30 은 Jenkins 60분 안에 들어가도록 잡았다.
  2026-10-03 scratch 시험(UNL 11경기): Claude 약 8분, ChatGPT(웹 off) 약 2분.
- **codex 프롬프트 호환**: codex 는 shell 로 파일을 읽는다. "명령 실행 없음" 문구를 그대로 두면 파일을 못 읽는다며 산출물 없이 끝났다
  (2026-10-03 시험). 프롬프트는 이 폴더 안의 읽기 명령과 `forecasts.json` 쓰기만 허용하도록 고쳤다.

- core.db 의 Total 0.5 시장이 아직 매우 적다(수집 시작 직후, 거래량 하한). 초기에는 시장 비교·paper 거래 n 이 거의 0 이고
  대부분 Poisson 기준선 비교만 가능하다. 필요하면 수집기 `POLYLAB_GOAL_MIN_VOLUME` 하향을 검토한다(이 연구의 범위 밖).
- 최종 스코어 fallback 은 Gamma 점수이므로 컵 대회 연장전 득점이 포함될 수 있다(시장 정산이 있으면 그것을 우선).
- **실행 간 분산**: 같은 경기·같은 입력으로 약 10분 간격 두 번 실행한 결과, top-3 가 1경기만 겹쳤고 개별 확률이 최대 ±0.05 움직였다
  (2026-10-01 scratch 시험). 이 폭은 `edge`(0.02)보다 크다. 분석 시 run 간 분산을 별도로 보고하고, H3·전략 결과를 해석할 때 고려한다.
- **모델 미고정**: 기본은 claude CLI 기본 모델이다(시험 시 sonnet-4-6 + 보조 haiku). `runs.model` 에 기록되지만 교란 변수다.
  고정하려면 `POLYLAB_RETRO_CLAUDE_MODEL` 을 쓰는데, 이 변수는 retro 와 공유된다.
