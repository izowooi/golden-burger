# 연구자 결정 기록

`reports/attention.md` 의 질문에 대한 연구자의 답. 회고는 이 파일을 읽어 해당 항목을 "사용자 결정"으로 닫고,
AI 는 이 결정을 전제로 판단한다(같은 질문을 다시 하지 않는다). GitHub 웹에서 직접 추가해도 된다. 형식:

    ## YYYY-MM-DD
    - `<attention 항목 id>` — 결정 내용

## 2026-10-02
- `ai:watermelon-soccer-late-collapse` — take-profit early 를 적용한다. 진입 조건 강화보다 조기 익절을 우선한다. 1분 주기로는 경기 막판 급락에서 손절이 체결되지 않으므로(아일랜드 0.92→0.17, 13분) 막판까지 보유하지 않는다.
- `ai:soccer-events-sample-too-small` — 현재 축적 속도로 축구의 경기 시간 구간별 득점 민감도 분석이 어렵다는 데 동의한다. 이 가설의 논문상 역할은 "막판 변동성이 커서 손절이 무력하므로 전략은 경기 막판까지 들고 가지 않고 조기 익절해야 한다"는 실거래 수익화 논리의 근거다. 과대/과소 평가를 증명해도 급락에는 손절로 대응할 수 없다는 점이 핵심이다.
- `ai:apricot-fruit-tick-inferior` — 백테스트로 더 좋은 파라미터가 확인되면 그 값으로 변경한다(실거래 20건 대기 없이 조기 조정 허용).

## 2026-10-03
- `ai:manual-nfl-scope` — NFL 실거래를 폐기한다. 데이터가 충분히 쌓여 수익이 나는 전략과 파라미터가 확보될 때까지 잠정 중단한다.
- `manual:nfl-scope` — NFL 을 watermelon·plum 실거래 범위에서 제외한다. NFL 전용 전략이나 기존 전략의 NFL 전용 분기 로직이 생기기 전까지 실거래하지 않는다.
- `policy:us-sports` — NBA·NHL(·NFL)은 수익을 내는 전략과 파라미터가 확인될 때까지 수집과 paper 시뮬레이션만 한다(`watermelon-us-paper`, `plum-us-paper`, `cherry-us-paper`). live 전환은 연구자만 결정한다.

## 2026-10-04
- `track1:goal-over-all-live` — goal-over-all 을 lion 계좌로 실거래(5 USDC) 시작한다. 진입은 킥오프 3일 전부터 5분 전까지만(한 달 전 같은 이른 매수는 하지 않는다).
- `track1:goal-over-exit-rules` — Over 0.5(goal-over-all) 청산 기준: 진입가 대비 +2% 이상이면 익절, −10% 이상 하락하면 손절하고, 익절·손절한 경기에는 다시 들어가지 않는다. 매도 호가가 0.99 이상이면 익절·손절 없이 정산까지 보유한다. 이 세 값은 연구자가 정한 기준이므로 AI 는 근거가 충분할 때 attention 으로 먼저 제안하고 임의로 바꾸지 않는다.
- `ai:ai-ai-cherry-tiger-calib-mismatch` — cherry-tiger·blue 는 paper 로 내리지 않고 live(5 USDC) 를 유지하되 전략을 재설계한다. 실거래 11건(손절 8·익절 3, tiger ROI −8.5%·blue −9.6%)과 8개월 백테스트 모두 옛 설정(0.76–0.82 밴드, TP +20%·SL −8%·trailing 15%)이 수수료·스프레드·1분 주기 손절 미끄러짐 때문에 구조적으로 손해임을 보였다. 새 설정: YES 0.90–0.95 진입, 진입 창은 경기 전 72시간 + 킥오프 후 60분, 손절·trailing·상대 TP 끔, 매도 호가 0.99 이상이면 정산까지 보유, 익절은 수수료 후 순이익일 때만 전량 — blue 는 진입가 +0.03, tiger 는 고정 0.98(청산 방식만 다른 A/B). min_liquidity 125000 유지. 백테스트도 edge 를 입증하지 못했으므로(95% 구간이 0 포함) 실거래 20건 후 재평가한다. 근거 `docs/research/backtests/2026-10-04-cherry-plum.md`.
- `ai:ai-cherry-tiger-calib-mismatch` — 위 `ai:ai-ai-cherry-tiger-calib-mismatch` 결정과 같다(paper 전환 대신 live 유지·재설계).
- `ai:cherry-tiger-calib-mismatch` — 0.76–0.78 밴드 가설은 폐기한다(실현 승률 ≈ 가격, calibration 근거 없음). cherry 는 0.90–0.95 favourite 수렴 가설로 바꿔 검증한다(위 결정).
- `ai:plum-paper-next-step` — B 로 결정: delta 기반 조기 익절을 paper 에서 테스트한다. plum-king·plum-queen 에 `take_profit_delta` 0.03(진입가 +0.03 이상이면 보유 전량을 수수료 후 순이익일 때만 매도)과 `hold_above_price` 0.99(매도 호가 0.99 이상이면 정산까지 보유)를 적용하고 mode 는 paper 그대로 둔다. 손절폭 A/B(king 0.12 vs queen 0.17)는 유지한다. NFL 과 plum-us-paper 는 백테스트에서 delta 가 오히려 손해라 기존 TP 가격을 유지한다(bounds 만 열어 둠). 백테스트상 plum 은 모든 설정이 약 −2% 로 delta 는 손실을 줄일 뿐이므로 paper 20건 후 재평가한다. 근거 `docs/research/backtests/2026-10-04-cherry-plum.md`.
- `track1:goal-over-tp-absolute` — goal-over-all 익절은 진입가 +0.02 절대값(2센트)이다. 예: 0.95 매수 → 0.97 이상에서 익절, 0.94 매수 → 0.96 이상에서 익절. 0.97 이상에 산 경우는 목표가가 0.99 보유 구간에 닿으므로 정산까지 보유한다. 손절은 진입가 대비 −10%.
- `track2:over-0.5-only` — 0:0 연구는 Over 0.5 만 거래한다. 자동 매매는 Total 0.5 Over 만 사고, 수동 기록(red)도 Total 0.5 만 트랙 2 로 집계한다(7월의 1.5·3.5·4.5·5.5 줄 거래는 '기타'로 분리).
- `ai:apricot-fruit-idle` — fruit 진입 0건은 버그가 아니라 포스트시즌 경기 수 부족과 진입 시점 0.90 이상 선두 부재 때문이다. 백테스트로 두 arm 공통 prob_min 0.89, take_profit_price 0.92 로 재조정하고, 앞으로 진입이 멈춘 변형은 AI 가 백테스트 근거로 스스로 재조정한다.

## 2026-10-05
- `track1:goal-over-tp-tunable` — goal-over-all 익절 +0.02 는 연구자 고정값이 아니다. AI 회고가 근거(백테스트·표본 게이트)가 있으면 더 나은 값으로 바꿔도 된다. 손절 −10%·0.99 보유는 유지.
- `hypothesis:ou05-lifecycle` — 진입 시점 가설: O/U 0.5 마켓은 처음 열릴 때 혼돈이 크고 약 하루 뒤 안정화되며, 이후 킥오프·종료까지 Over 과대평가가 커진다. 과대평가가 작을 때 사서 클 때 파는 것이 유리하므로, ou05 생애 통계로 검증한 뒤 goal-over 진입 창(현재 3일 전)을 조정한다(AI 가 통계 근거로 제안·적용).
- `apricot:edge-negative` — apricot 은 백테스트상 모든 조합이 음수라 수익 우위가 없다. 문제로 본다.
- `sports3:per-sport` — watermelon·apricot·plum 3 전략 모두 NBA·NHL 을 포함하고, 종목마다 진입 기준·파라미터·거래 단위를 따로 둔다. 시작은 모두 5 USDC.
- `cherry:back-to-basics` — cherry 를 초기 개념으로 되돌린다: 일정 유동성·거래량 이상 마켓에서 종료 3일 전쯤 0.9 근처에 사서 0.95 근처에 판다. 수천~수만 조합 백테스트로 최적값을 정하고, 데이터가 부족하면 paper 로만.
- `storage:budget` — 데이터 증가는 한 달 50GB 이내(최대 100GB)로 유지한다. 수집은 중앙 DB 한 곳에서.
- `fees:maker-preferred` — 가능하면 수수료가 없는 지정가(maker) 주문을 선호한다.
- `sports3:per-sport-applied` — 적용(위 `sports3:per-sport` 결정의 실행, 구현자 기록). yaml `sports` 를 종목별 매핑(종목마다 mode·stake_usdc·선택 limits, 파라미터는 `sport_overrides.<종목>`)으로 바꾸고, 엔진·ladder·validator·리포트가 (변형, 종목) 단위로 동작한다. live 판정: 연구자 규칙(백테스트 n ≥ 40, 전체·두 반기 ROI ≥ 0)을 **현재 수수료(sports_fees_v3 0.05)** 로 적용하고, 구현자가 결과를 본 뒤 추가한 기준(이웃 셀 강건성, 1분 지연 손절 스트레스 ≥ 0, 반기 손익 ≥ 1단위, 엔진 재생 확인)도 통과해야 live. 결과(모두 5 USDC): watermelon-cat·dog soccer live 유지 + **NBA live**(cat 진입 0.87, dog 0.96, 손절 0.70, 조기 익절 없음), NHL paper. plum 전 종목 paper(저장 수수료로는 king NBA 가 통과했지만 현재 수수료로 H1 −1.15%). apricot 전 종목 paper. NFL 은 어디에서도 live 아님. NBA·NHL 은 watermelon-us-paper·plum-us-paper 에서 빼서 각 변형의 종목 설정으로 옮겼다(두 변형은 NFL paper 만). **연구자 규칙만으로는 live 였지만 추가 기준으로 paper 에 둔 조합**: apricot-eco·fruit NBA·NHL(통과 셀이 모두 정산 손실 0건, 손실 1건이면 반기 음수) — 연구자가 다르게 결정할 수 있다. 이 결정은 10-03 `policy:us-sports`(미국 종목 paper 만)를 NBA·NHL 에 대해 대체한다. 근거 `docs/research/backtests/2026-10-05-per-sport-nba-nhl.md`.
- `apricot:mlb-paper` — apricot MLB 를 live 에서 paper 로 내린다. 조기 익절(TP ≤ 0.96) 1,152 조합 중 두 반기 모두 0 이상인 조합이 없다(현재값 eco −0.94%·fruit −0.40%). 양수는 TP 0.98–0.99(사실상 정산 보유)뿐이고 H2 가 음수이며 이웃 조합이 무너진다. NBA·NHL 도 통과 조합은 정산 손실이 0건일 때뿐이라(손실 1건이면 반기 음수) paper 로 둔다. 계좌(eco·fruit)는 유지하고 남은 live 포지션은 계속 청산·대사한다. 손절 추가 등 새 가설은 다음 연구 과제.
- `fees:maker-preferred-applied` — 적용(구현자 기록). 변형·종목별 `order_style: taker|maker` 를 추가했다. maker 는 진입을 호가 안쪽 한 틱의 지정가(post-only, 수수료 0)로 걸어 두고 60분마다 또는 2틱 넘게 움직이면 다시 걸며 진입 창이 닫히면 취소, 익절도 체결 후 진입가+폭에 지정가로 건다(goal-over 는 킥오프 전까지만 — 경기 중 골 급등은 0.99 보유 규칙대로 정산까지). 손절은 걸린 익절을 먼저 취소한 뒤 즉시 매도. 수수료 0 은 거래소가 maker 로 보고한 체결만. **goal-over-all 만 maker 로 켰다**(나머지는 taker 유지, 연구자가 변형·종목별로 바꿀 수 있음). paper·백테스트는 maker 체결을 보수적으로만 인정한다(백테스트는 taker 체결로 대리 재생). 실계좌(lion)로 5 USDC 지정가 1건을 걸고 취소까지 확인했다. 근거 `docs/research/api-sources.md` "Maker orders".
- `cherry:back-to-basics-applied` — 적용(구현자 기록). cherry 를 전 카테고리 종료 임박 전략으로 다시 썼다: 종료(end_ref: 경기 마켓 킥오프+3h, 그 밖 endDate) 전 창 안에서 앞선 결과가 밴드 안이면 마켓당 1회 매수, 절대가/+delta 순이익 익절·0.99 보유·선택 시간 청산, 손절 없음. 데이터는 새 중앙 수집 `data/general`(전 카테고리, 1분 L1 호가, core.db 추적분은 core 에서 읽음, 2026-02~ 과거 가격 백필). 9.6만 마켓·8만 1,548 조합×4 시나리오 백테스트에서 **현재 taker 수수료·측정 스프레드로 두 반기 모두 0 이상인 조합 0개**, 연구자 원안(48–72h, 0.90–0.95, 0.95 매도) ROI −4.7%, 스포츠가 가장 나빴다(core −5.6%, 그 밖 −8.1%). 그래서 **cherry-blue(원안, 전 카테고리)·cherry-tiger(esports 36–60h 0.94–0.96 정산 보유, 카테고리별 선택에서 유일하게 문턱 통과했지만 다중 비교라 검증용) 모두 paper**, cherry-us-paper 은퇴. maker(수수료 0) 가정에서는 일부 양수라 지정가 주문 도입 후 재검토. 근거 `docs/research/backtests/2026-10-05-cherry-basics.md`.
- `storage:budget-applied` — 적용. `polylab health` 가 데이터 영역별(core·books·raw·ou05·general·strategies·manual·research) 최근 7일 증가로 30일을 예측해 50GB/월 초과 경고, 100GB 초과 장애로 알리고 일일 리포트·대시보드 JSON(`system.collector.storage`)에 표시한다. 실측상 raw WebSocket 보관이 월 40–48GB 로 대부분이어서 기본 보관을 `lean`(price_change 이벤트 제외, 바이트의 약 95%)으로 바꿨다(`POLYLAB_RAW_MARKET=full|off` 로 변경 가능). 그 밖의 손잡이: general 수집 5분 주기(`POLYLAB_GENERAL_POLL_EVERY=5`), 지난 달 shard VACUUM(`polylab storage compact --apply`, 삭제 없음). 변경 후 예상 월 8–10GB.

## 2026-10-06
- `sports3:us-all` — watermelon·apricot·plum 3 전략이 NBA·NHL·NFL 도 함께 다룬다(전략 family 는 하나, 종목마다 진입 기준·진입 시각·TP/SL·거래량 하한·단위를 따로). 모든 종목 5 USDC 에서 시작한다. NFL 은 다시 포함하되 **paper 부터** 시작한다. 이 결정은 10-03 `policy:us-sports`·`manual:nfl-scope`·`ai:manual-nfl-scope` 를 대체한다.
- `sports3:auto-promotion` — paper→live 전환을 사람 대신 **결정론 게이트**가 자동으로 한다(10-03 "live 전환은 연구자만"과 "AI 는 절대 live 로 올릴 수 없다"를 이 범위에서 대체). 조건(모두): (변형, 종목)의 현재 파라미터 paper 정산 ≥ 30건, paper ROI 80% bootstrap 하한 > 0, paper 표본의 두 반기 모두 ≥ 0, retro 가 직접 돌린 최근 120일 백테스트 n ≥ 40·ROI ≥ 0, 변형에 계좌가 있음, 프리시즌 기간이 아님(`live_from` 존중), 마지막 변경 후 3일 cooldown. 전환 시 5 USDC, stake_events·reports/changes.md·attention(참고) "자동 실거래 전환" 기록. live→paper 강등은 기존 ladder 규칙대로 자동. AI 자신은 여전히 mode=live 를 직접 정할 수 없다(이 게이트만).
- `sports3:us-all-applied` — 적용(구현자 기록). (1) 과거 데이터: NBA·NHL·NFL moneyline 1분 가격을 2024-01 부터 운영 core.db 에 백필했다(NBA +2,386경기·NHL +1,938·NFL +692, 실패 0, +641 MB). Polymarket 에 그 이전 경기 마켓은 없다(NBA 2024-04, NFL 2024-08, NHL 2024-10 부터; NHL 실사용은 2024-12 부터). 해상도는 1분 그대로이고, 저하된 구간은 NBA 2024 봄(거래량 2만 미만)과 종료 시각이 빠진 일부 달뿐이다. (2) 두 시즌 grid(현재 수수료 0.05 강제, 사전 등록 규칙 R1–R7, 근거 `docs/research/backtests/2026-10-06-us-sports.md`): watermelon·plum 은 NBA·NHL·NFL·MLB 어디에도 n ≥ 40·두 반기 ≥ 0 셀이 없다. apricot NFL(경기 시작 약 190분 뒤 4쿼터 막판 선두 0.80–0.99 매수, 0.96 조기 익절)만 R1–R7 을 통과했다(엔진 재생 103건 +2.76%, 두 반기·세 시즌 모두 양수) — 연구자 지시대로 paper 로 시작하고 승격 게이트의 첫 후보로 둔다. (3) **10-05 의 watermelon NBA live(10-21 시작 예정)를 취소하고 paper 로 되돌렸다**: 10-05 판정은 2026-02~06 반 시즌만 본 것이고, 두 시즌으로 넓히면 통과 셀이 0개, 10-05 값(0.87/0.70)은 2025-11~12 엔진 재생에서 −1.73%(479건)였다. NBA live 포지션은 없었다. (4) 결과: 미국 종목·MLB 는 6개 변형 모두 paper(soccer 는 그대로: watermelon live, plum paper). watermelon-us-paper·plum-us-paper 는 NFL 을 cat/dog·king/queen 으로 옮기고 off. 새 파라미터: watermelon·plum 진입 시각 창(`min_wall_minute`/`max_wall_minute`), apricot 선택 손절(`stop_loss_delta`). (5) 승격 게이트: `src/polylab/risk/promotion.py` + retro(paper 판정은 매 회고, 120일 재생은 주간 회고 회당 1건, 프리시즌 창 거래는 paper 표본에서 제외) + validator(출처 `promotion`·게이트 근거만 live 허용). **연구자 확인 필요**: NFL apricot 은 진입 경기가 시즌당 약 40–50건이라 최근 120일 재생이 2025-26 시즌 어느 주에도 n ≥ 40 에 닿지 않는다(23–39건). 지금 규칙으로는 NFL 이 자동 전환되기 어렵다 — NFL 만 창을 늘릴지 등은 연구자가 정한다.
